-- Payment lifecycle hardening.
--
-- The payment tables already modelled attempts (payments.attempt_number) and a
-- transaction log (payment_transactions). What was missing was the vocabulary to
-- say "we do not know yet" and the machinery to make provider callbacks safe to
-- replay. This migration adds both:
--
--   * wider status vocabularies, so a payment can be pending / awaiting
--     verification / cancelled / expired instead of being forced into a
--     premature 'failed'
--   * commerce.webhook_events, so a redelivered Razorpay event is rejected by a
--     UNIQUE constraint rather than by an application-level "did we already do
--     this?" check that two concurrent workers can both pass
--   * commerce.notification_log, same idea for "order confirmed" emails
--   * reconciliation bookkeeping on payments, so a background worker can find
--     unresolved attempts and back off between attempts


-- =============================================================================
-- commerce.payments — wider status vocabulary + reconciliation bookkeeping
-- =============================================================================

ALTER TABLE commerce.payments
  DROP CONSTRAINT IF EXISTS payments_status_check;

ALTER TABLE commerce.payments
  ADD CONSTRAINT payments_status_check CHECK (
    status IN (
      -- Razorpay order created, customer has not acted yet.
      'created',
      -- Customer acted; provider has not reached a terminal answer.
      'pending',
      -- We hold a signal we could not confirm (timeout, signature mismatch,
      -- gateway unreachable). Never show this to a customer as failure.
      'verification_required',
      'authorized',
      'captured',
      'failed',
      -- Customer closed checkout and no payment exists at the provider.
      'cancelled',
      -- Razorpay order aged out without a payment.
      'expired',
      'refund_pending',
      'partially_refunded',
      'refunded'
    )
  );

ALTER TABLE commerce.payments
  -- When server-side verification (signature or provider fetch) last succeeded.
  ADD COLUMN IF NOT EXISTS verified_at         TIMESTAMPTZ,
  -- Razorpay's machine-readable reason, e.g. 'payment_risk_check_failed'.
  ADD COLUMN IF NOT EXISTS failure_code        TEXT,
  ADD COLUMN IF NOT EXISTS captured_at         TIMESTAMPTZ,
  -- Reconciliation bookkeeping. next_reconcile_at is the backoff gate.
  ADD COLUMN IF NOT EXISTS reconcile_attempts  INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_reconciled_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS next_reconcile_at   TIMESTAMPTZ,
  -- Set when automated reconciliation gives up, or when we detect a duplicate
  -- capture / capture-without-order. Drives the admin reconciliation queue.
  ADD COLUMN IF NOT EXISTS needs_admin_review  BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS admin_review_reason TEXT;

COMMENT ON COLUMN commerce.payments.verified_at IS
  'When this attempt was last confirmed server-side (signature or provider fetch).';
COMMENT ON COLUMN commerce.payments.failure_code IS
  'Provider machine-readable failure code, e.g. payment_risk_check_failed.';
COMMENT ON COLUMN commerce.payments.reconcile_attempts IS
  'How many times the reconciler has queried the provider for this attempt.';
COMMENT ON COLUMN commerce.payments.next_reconcile_at IS
  'Backoff gate; the reconciler ignores rows until this passes.';
COMMENT ON COLUMN commerce.payments.needs_admin_review IS
  'True when automation cannot resolve this attempt and a human must look.';

-- One Razorpay order maps to exactly one attempt. This is what makes webhook
-- and callback lookups unambiguous, and blocks a second attempt accidentally
-- reusing a provider order id.
CREATE UNIQUE INDEX IF NOT EXISTS payments_provider_order_id_unique
  ON commerce.payments (provider_order_id)
  WHERE provider_order_id IS NOT NULL;

COMMENT ON INDEX commerce.payments_provider_order_id_unique IS
  'Provider order IDs are unique when set.';

-- The reconciler's work queue: unresolved attempts whose backoff has elapsed.
CREATE INDEX IF NOT EXISTS payments_reconcile_queue_idx
  ON commerce.payments (next_reconcile_at)
  WHERE status IN ('created', 'pending', 'verification_required', 'authorized');

COMMENT ON INDEX commerce.payments_reconcile_queue_idx IS
  'Drives the reconciliation worker; only unresolved attempts are indexed.';

CREATE INDEX IF NOT EXISTS payments_admin_review_idx
  ON commerce.payments (created_at DESC)
  WHERE needs_admin_review;

COMMENT ON INDEX commerce.payments_admin_review_idx IS
  'Admin reconciliation queue.';


-- =============================================================================
-- commerce.orders — allow "we are still verifying" as a payment state
-- =============================================================================

ALTER TABLE commerce.orders
  DROP CONSTRAINT IF EXISTS orders_payment_status_check;

ALTER TABLE commerce.orders
  ADD CONSTRAINT orders_payment_status_check CHECK (
    payment_status IN (
      'pending',
      -- Customer may have been debited; provider has not confirmed. This is the
      -- state that must never be rendered to a customer as a failure.
      'verification_pending',
      'authorized',
      'paid',
      'partially_refunded',
      'refunded',
      'failed'
    )
  );


-- =============================================================================
-- commerce.webhook_events — provider callbacks, deduplicated by the database
-- =============================================================================

CREATE TABLE IF NOT EXISTS commerce.webhook_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  provider            TEXT NOT NULL,

  -- Razorpay's x-razorpay-event-id header. Nullable because a malformed or
  -- unsigned delivery is still worth recording for forensics.
  provider_event_id   TEXT,

  event_type          TEXT NOT NULL,

  -- False rows are retained deliberately: repeated signature failures against a
  -- payments endpoint is a security signal, not noise to discard.
  signature_valid     BOOLEAN NOT NULL DEFAULT FALSE,

  -- received -> processed | ignored | failed | duplicate
  processing_status   TEXT NOT NULL DEFAULT 'received',

  payment_id          UUID REFERENCES commerce.payments (id) ON DELETE SET NULL,
  order_id            UUID REFERENCES commerce.orders (id) ON DELETE SET NULL,

  provider_order_id   TEXT,
  provider_payment_id TEXT,

  payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
  error               TEXT,

  processed_at        TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT webhook_events_provider_check CHECK (
    provider IN ('razorpay', 'stripe', 'payu', 'cashfree')
  ),

  CONSTRAINT webhook_events_processing_status_check CHECK (
    processing_status IN ('received', 'processed', 'ignored', 'failed', 'duplicate')
  )
);

COMMENT ON TABLE commerce.webhook_events IS
  'Every provider webhook delivery. The unique index below is what makes '
  'webhook processing idempotent — a redelivery loses the insert race and is '
  'skipped, rather than relying on an application-level check two workers can '
  'both pass.';

CREATE UNIQUE INDEX IF NOT EXISTS webhook_events_provider_event_unique
  ON commerce.webhook_events (provider, provider_event_id)
  WHERE provider_event_id IS NOT NULL;

COMMENT ON INDEX commerce.webhook_events_provider_event_unique IS
  'One row per provider event id — the idempotency guarantee.';

CREATE INDEX IF NOT EXISTS webhook_events_payment_id_idx
  ON commerce.webhook_events (payment_id, created_at DESC);

CREATE INDEX IF NOT EXISTS webhook_events_order_id_idx
  ON commerce.webhook_events (order_id, created_at DESC);

CREATE INDEX IF NOT EXISTS webhook_events_unprocessed_idx
  ON commerce.webhook_events (created_at DESC)
  WHERE processing_status IN ('received', 'failed');

COMMENT ON INDEX commerce.webhook_events_unprocessed_idx IS
  'Deliveries that were accepted but not successfully applied.';


-- =============================================================================
-- commerce.notification_log — send-exactly-once for order/payment messaging
-- =============================================================================

CREATE TABLE IF NOT EXISTS commerce.notification_log (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  order_id      UUID NOT NULL REFERENCES commerce.orders (id) ON DELETE CASCADE,

  -- e.g. 'order_confirmed', 'payment_failed', 'refund_processed'
  kind          TEXT NOT NULL,
  channel       TEXT NOT NULL DEFAULT 'email',

  recipient     TEXT,
  status        TEXT NOT NULL DEFAULT 'sent',
  error         TEXT,

  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT notification_log_status_check CHECK (
    status IN ('sent', 'failed')
  )
);

COMMENT ON TABLE commerce.notification_log IS
  'One row per order notification actually dispatched. The unique index is the '
  'idempotency key: a duplicate webhook cannot send a second confirmation.';

-- Only successful sends claim the slot, so a failed send can be retried.
CREATE UNIQUE INDEX IF NOT EXISTS notification_log_once_idx
  ON commerce.notification_log (order_id, kind, channel)
  WHERE status = 'sent';

COMMENT ON INDEX commerce.notification_log_once_idx IS
  'Send-exactly-once guard for order notifications.';


-- =============================================================================
-- commerce.refunds — track the provider's own view of the refund
-- =============================================================================

ALTER TABLE commerce.refunds
  DROP CONSTRAINT IF EXISTS refunds_status_check;

ALTER TABLE commerce.refunds
  ADD CONSTRAINT refunds_status_check CHECK (
    status IN ('requested', 'pending', 'processed', 'failed', 'cancelled')
  );

ALTER TABLE commerce.refunds
  -- Razorpay's raw status string, kept separate from ours.
  ADD COLUMN IF NOT EXISTS provider_status  TEXT,
  ADD COLUMN IF NOT EXISTS processed_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_synced_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS failure_reason   TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS refunds_provider_refund_id_unique
  ON commerce.refunds (provider_refund_id)
  WHERE provider_refund_id IS NOT NULL;

COMMENT ON INDEX commerce.refunds_provider_refund_id_unique IS
  'Provider refund IDs are unique when set — makes refund webhooks idempotent.';

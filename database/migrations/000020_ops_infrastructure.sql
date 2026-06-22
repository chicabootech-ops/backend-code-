-- =============================================================================
-- Migration: 000020_ops_infrastructure
-- Chic A Boo — operational infrastructure (Architecture v3.0)
-- Depends on: 000011, 000016 (orders), 000017 (payments)
-- Blocks:     000025
--
-- Creates:
--   ops.idempotency_keys      — write deduplication / replay protection
--   ops.webhook_events        — inbound Razorpay (+ future) webhook inbox
--   ops.notification_logs     — Resend / provider delivery audit
--
-- RLS: disabled (service role only)
-- =============================================================================


-- =============================================================================
-- TABLE: ops.idempotency_keys
-- Prevents duplicate mutating API requests (checkout, payment, refund).
-- =============================================================================
CREATE TABLE ops.idempotency_keys (
  key                     TEXT PRIMARY KEY,

  scope                   TEXT NOT NULL,

  actor_id                UUID,

  request_hash            TEXT NOT NULL,

  response_status         INTEGER,

  response_body           JSONB,

  locked_until            TIMESTAMPTZ,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  completed_at            TIMESTAMPTZ,

  CONSTRAINT idempotency_keys_response_status_valid CHECK (
    response_status IS NULL
    OR (response_status >= 100 AND response_status < 600)
  )
);

COMMENT ON TABLE ops.idempotency_keys IS
  'Idempotency store for mutating API calls; purge completed keys after 30 days.';

COMMENT ON COLUMN ops.idempotency_keys.key IS
  'Client-supplied Idempotency-Key header value (PK).';
COMMENT ON COLUMN ops.idempotency_keys.scope IS
  'Logical scope e.g. checkout.create, payment.capture.';
COMMENT ON COLUMN ops.idempotency_keys.actor_id IS
  'User or admin UUID when applicable.';
COMMENT ON COLUMN ops.idempotency_keys.request_hash IS
  'SHA-256 of normalised request body for conflict detection.';
COMMENT ON COLUMN ops.idempotency_keys.response_status IS
  'Cached HTTP status when request completed.';
COMMENT ON COLUMN ops.idempotency_keys.response_body IS
  'Cached JSON response body for replay.';
COMMENT ON COLUMN ops.idempotency_keys.locked_until IS
  'Pessimistic lock expiry while handler is in flight.';
COMMENT ON COLUMN ops.idempotency_keys.created_at IS 'UTC first seen.';
COMMENT ON COLUMN ops.idempotency_keys.completed_at IS
  'UTC when response was stored; NULL = in progress.';

CREATE INDEX idempotency_keys_created_at_idx
  ON ops.idempotency_keys (created_at);

COMMENT ON INDEX ops.idempotency_keys_created_at_idx IS
  'TTL purge job for completed keys (> 30 days).';

CREATE INDEX idempotency_keys_scope_actor_idx
  ON ops.idempotency_keys (scope, actor_id)
  WHERE actor_id IS NOT NULL;

COMMENT ON INDEX ops.idempotency_keys_scope_actor_idx IS
  'Debug idempotency collisions per actor.';


-- =============================================================================
-- TABLE: ops.webhook_events
-- Inbound webhook inbox with deduplication and retry / dead-letter support.
-- =============================================================================
CREATE TABLE ops.webhook_events (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  provider                TEXT NOT NULL,

  provider_event_id       TEXT NOT NULL,

  event_type              TEXT NOT NULL,

  payload                 JSONB NOT NULL DEFAULT '{}'::jsonb,

  status                  TEXT NOT NULL DEFAULT 'pending',

  payment_id              UUID
    REFERENCES commerce.payments (id) ON DELETE SET NULL,

  order_id                UUID
    REFERENCES commerce.orders (id) ON DELETE SET NULL,

  error_message           TEXT,

  retry_count             INTEGER NOT NULL DEFAULT 0,

  next_retry_at           TIMESTAMPTZ,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  processed_at            TIMESTAMPTZ,

  CONSTRAINT webhook_events_provider_event_unique UNIQUE (provider, provider_event_id),

  CONSTRAINT webhook_events_provider_check CHECK (
    provider IN ('razorpay', 'stripe', 'payu', 'cashfree', 'shiprocket', 'delhivery')
  ),

  CONSTRAINT webhook_events_status_check CHECK (
    status IN (
      'pending',
      'processing',
      'processed',
      'failed',
      'dead_letter'
    )
  ),

  CONSTRAINT webhook_events_retry_count_nonneg CHECK (retry_count >= 0),

  CONSTRAINT webhook_events_payload_object_check CHECK (
    jsonb_typeof(payload) = 'object'
  )
);

COMMENT ON TABLE ops.webhook_events IS
  'Inbound webhook event store; dedupe by (provider, provider_event_id).';

COMMENT ON COLUMN ops.webhook_events.id IS 'UUID primary key.';
COMMENT ON COLUMN ops.webhook_events.provider IS 'Webhook source e.g. razorpay.';
COMMENT ON COLUMN ops.webhook_events.provider_event_id IS
  'Provider-assigned event ID for deduplication.';
COMMENT ON COLUMN ops.webhook_events.event_type IS
  'Provider event type e.g. payment.captured.';
COMMENT ON COLUMN ops.webhook_events.payload IS 'Raw webhook JSON body.';
COMMENT ON COLUMN ops.webhook_events.status IS
  'pending|processing|processed|failed|dead_letter.';
COMMENT ON COLUMN ops.webhook_events.payment_id IS
  'Resolved commerce.payments row when applicable.';
COMMENT ON COLUMN ops.webhook_events.order_id IS
  'Resolved commerce.orders row when applicable.';
COMMENT ON COLUMN ops.webhook_events.error_message IS
  'Last processing error for retries.';
COMMENT ON COLUMN ops.webhook_events.retry_count IS
  'Number of processing attempts.';
COMMENT ON COLUMN ops.webhook_events.next_retry_at IS
  'Scheduled time for next retry attempt.';
COMMENT ON COLUMN ops.webhook_events.created_at IS 'UTC webhook received.';
COMMENT ON COLUMN ops.webhook_events.processed_at IS
  'UTC successfully processed or moved to dead_letter.';

CREATE INDEX webhook_events_status_failed_idx
  ON ops.webhook_events (status, next_retry_at)
  WHERE status IN ('failed', 'pending');

COMMENT ON INDEX ops.webhook_events_status_failed_idx IS
  'Retry worker queue for failed/pending webhooks.';

CREATE INDEX webhook_events_created_at_idx
  ON ops.webhook_events (created_at DESC);

COMMENT ON INDEX ops.webhook_events_created_at_idx IS
  'Recent webhook audit.';

CREATE INDEX webhook_events_payment_id_idx
  ON ops.webhook_events (payment_id)
  WHERE payment_id IS NOT NULL;

COMMENT ON INDEX ops.webhook_events_payment_id_idx IS
  'Webhooks linked to a payment.';

CREATE INDEX webhook_events_order_id_idx
  ON ops.webhook_events (order_id)
  WHERE order_id IS NOT NULL;

COMMENT ON INDEX ops.webhook_events_order_id_idx IS
  'Webhooks linked to an order.';

CREATE INDEX webhook_events_dead_letter_idx
  ON ops.webhook_events (created_at DESC)
  WHERE status = 'dead_letter';

COMMENT ON INDEX ops.webhook_events_dead_letter_idx IS
  'Dead-letter queue for manual replay.';


-- =============================================================================
-- TABLE: ops.notification_logs
-- Outbound notification delivery audit (Resend email at launch).
-- =============================================================================
CREATE TABLE ops.notification_logs (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  user_id                 UUID
    REFERENCES identity.users (id) ON DELETE SET NULL,

  channel                 TEXT NOT NULL,

  template                TEXT NOT NULL,

  provider                TEXT NOT NULL,

  provider_message_id     TEXT,

  recipient               TEXT NOT NULL,

  status                  TEXT NOT NULL DEFAULT 'pending',

  reference_type          TEXT,

  reference_id            UUID,

  retry_count             INTEGER NOT NULL DEFAULT 0,

  error_message           TEXT,

  raw_payload             JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  sent_at                 TIMESTAMPTZ,

  CONSTRAINT notification_logs_channel_check CHECK (
    channel IN ('email', 'sms', 'push')
  ),

  CONSTRAINT notification_logs_provider_check CHECK (
    provider IN ('resend', 'twilio', 'fcm', 'msg91')
  ),

  CONSTRAINT notification_logs_status_check CHECK (
    status IN (
      'pending',
      'sent',
      'delivered',
      'failed',
      'bounced',
      'dead_letter'
    )
  ),

  CONSTRAINT notification_logs_retry_count_nonneg CHECK (retry_count >= 0),

  CONSTRAINT notification_logs_reference_type_check CHECK (
    reference_type IS NULL
    OR reference_type IN ('order', 'user', 'return', 'review', 'payment')
  )
);

COMMENT ON TABLE ops.notification_logs IS
  'Append-only outbound notification delivery log; purge > 180 days.';

COMMENT ON COLUMN ops.notification_logs.id IS 'UUID primary key.';
COMMENT ON COLUMN ops.notification_logs.user_id IS
  'Recipient user when registered.';
COMMENT ON COLUMN ops.notification_logs.channel IS 'email|sms|push.';
COMMENT ON COLUMN ops.notification_logs.template IS
  'Template code e.g. order-confirmed, verify-email.';
COMMENT ON COLUMN ops.notification_logs.provider IS 'Delivery provider e.g. resend.';
COMMENT ON COLUMN ops.notification_logs.provider_message_id IS
  'Provider-assigned message ID.';
COMMENT ON COLUMN ops.notification_logs.recipient IS 'Email address or phone.';
COMMENT ON COLUMN ops.notification_logs.status IS
  'pending|sent|delivered|failed|bounced|dead_letter.';
COMMENT ON COLUMN ops.notification_logs.reference_type IS
  'Polymorphic link type.';
COMMENT ON COLUMN ops.notification_logs.reference_id IS
  'Polymorphic link UUID.';
COMMENT ON COLUMN ops.notification_logs.retry_count IS
  'Delivery retry attempts.';
COMMENT ON COLUMN ops.notification_logs.error_message IS
  'Last delivery error.';
COMMENT ON COLUMN ops.notification_logs.raw_payload IS
  'Provider request/response snapshot.';
COMMENT ON COLUMN ops.notification_logs.created_at IS 'UTC log created.';
COMMENT ON COLUMN ops.notification_logs.sent_at IS 'UTC provider accepted send.';

CREATE INDEX notification_logs_reference_idx
  ON ops.notification_logs (reference_type, reference_id)
  WHERE reference_type IS NOT NULL AND reference_id IS NOT NULL;

COMMENT ON INDEX ops.notification_logs_reference_idx IS
  'Lookup notifications for an order/user/etc.';

CREATE INDEX notification_logs_user_created_idx
  ON ops.notification_logs (user_id, created_at DESC)
  WHERE user_id IS NOT NULL;

COMMENT ON INDEX ops.notification_logs_user_created_idx IS
  'Notification history per user.';

CREATE INDEX notification_logs_status_pending_idx
  ON ops.notification_logs (status, created_at)
  WHERE status IN ('pending', 'failed');

COMMENT ON INDEX ops.notification_logs_status_pending_idx IS
  'Retry worker for pending/failed deliveries.';

CREATE INDEX notification_logs_template_idx
  ON ops.notification_logs (template);

COMMENT ON INDEX ops.notification_logs_template_idx IS
  'Filter logs by notification template.';


-- =============================================================================
-- VERIFICATION QUERIES (manual)
-- =============================================================================
--
-- 1. Tables exist in ops schema:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'ops' ORDER BY table_name;
--
-- 2. Webhook deduplication unique:
--    SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'ops' AND indexname = 'webhook_events_provider_event_unique';
--
-- 3. dead_letter status allowed:
--    SELECT pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'ops.webhook_events'::regclass
--      AND conname = 'webhook_events_status_check';
--
-- 4. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000020_ops_infrastructure';


-- =============================================================================
-- ROLLBACK SQL (manual)
-- =============================================================================
--
-- DROP TABLE IF EXISTS ops.notification_logs;
-- DROP TABLE IF EXISTS ops.webhook_events;
-- DROP TABLE IF EXISTS ops.idempotency_keys;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000020_ops_infrastructure';

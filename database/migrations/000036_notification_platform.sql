-- Notification platform: WhatsApp primary, Message Central SMS fallback.
--
-- Two dead tables already existed for this (ops.notification_logs from 000020 and
-- admin.notification_types from 000026) — created, never referenced by a single
-- line of application code, and with CHECK constraints that predate both
-- WhatsApp and Message Central. Rather than add a fourth notification system,
-- this migration adopts them: widens their vocabularies, adds the attempt-level
-- table their design implied but never had, and renames them into the shape the
-- service layer actually uses.
--
-- The other half is identity.otp_challenges. Phone OTP currently delegates code
-- generation to Message Central VerifyNow, so the backend never sees the code —
-- which makes "the same OTP on WhatsApp and on the SMS fallback" impossible, and
-- also means the only record of an in-flight OTP lives in Redis (so phone
-- verification 503s whenever Redis blips). Owning the OTP fixes both.


-- =============================================================================
-- identity.otp_challenges — we generate, hash and verify our own OTPs
-- =============================================================================

CREATE TABLE IF NOT EXISTS identity.otp_challenges (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  user_id             UUID REFERENCES identity.users (id) ON DELETE CASCADE,

  -- phone_verify | login | registration | password_reset | change_phone | ...
  purpose             TEXT NOT NULL,

  -- 'phone' | 'email'. Which kind of destination the code was sent to.
  destination_type    TEXT NOT NULL,
  -- E.164 phone or normalised email. Indexed for per-destination rate limiting.
  destination         TEXT NOT NULL,

  -- Argon2 hash. The raw code is never stored, never logged, never returned.
  otp_hash            TEXT NOT NULL,

  expires_at          TIMESTAMPTZ NOT NULL,
  attempts            INTEGER NOT NULL DEFAULT 0,
  max_attempts        INTEGER NOT NULL DEFAULT 5,

  -- Set the moment a code is accepted, so it cannot be replayed.
  consumed_at         TIMESTAMPTZ,
  -- Set when superseded by a resend, so old codes stop working immediately.
  superseded_at       TIMESTAMPTZ,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT otp_challenges_destination_type_check CHECK (
    destination_type IN ('phone', 'email')
  ),
  CONSTRAINT otp_challenges_attempts_nonneg CHECK (attempts >= 0),
  CONSTRAINT otp_challenges_max_attempts_positive CHECK (max_attempts > 0)
);

COMMENT ON TABLE identity.otp_challenges IS
  'Self-issued OTP challenges. One challenge can be delivered over several '
  'channels (WhatsApp then SMS) carrying the SAME code — which is only possible '
  'because we generate it rather than the SMS provider.';

COMMENT ON COLUMN identity.otp_challenges.otp_hash IS
  'Argon2 hash of the code. Raw OTP is never persisted or logged.';
COMMENT ON COLUMN identity.otp_challenges.superseded_at IS
  'Set when a resend replaces this challenge; old codes stop verifying at once.';

-- The hot path: "is there a live challenge for this destination and purpose?"
CREATE INDEX IF NOT EXISTS otp_challenges_live_idx
  ON identity.otp_challenges (destination, purpose, created_at DESC)
  WHERE consumed_at IS NULL AND superseded_at IS NULL;

COMMENT ON INDEX identity.otp_challenges_live_idx IS
  'Finds the active challenge for a destination; also drives resend cooldown.';

CREATE INDEX IF NOT EXISTS otp_challenges_user_idx
  ON identity.otp_challenges (user_id, created_at DESC);

-- Rate-limit window scans ("how many codes has this destination asked for?").
CREATE INDEX IF NOT EXISTS otp_challenges_rate_idx
  ON identity.otp_challenges (destination, created_at DESC);

CREATE TRIGGER otp_challenges_set_updated_at
  BEFORE UPDATE ON identity.otp_challenges
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();


-- =============================================================================
-- ops.notifications — one row per logical notification (adopted from
-- ops.notification_logs, which no code ever used)
-- =============================================================================

ALTER TABLE ops.notification_logs RENAME TO notifications;

ALTER TABLE ops.notifications
  DROP CONSTRAINT IF EXISTS notification_logs_channel_check,
  DROP CONSTRAINT IF EXISTS notification_logs_provider_check,
  DROP CONSTRAINT IF EXISTS notification_logs_status_check,
  DROP CONSTRAINT IF EXISTS notification_logs_reference_type_check;

ALTER TABLE ops.notifications
  -- The notification type, e.g. ORDER_CONFIRMED / OTP_PHONE_VERIFY.
  ADD COLUMN IF NOT EXISTS notification_type  TEXT,
  -- transactional | otp | marketing. Drives the fallback and consent policy.
  ADD COLUMN IF NOT EXISTS category           TEXT NOT NULL DEFAULT 'transactional',
  -- Which channel we intend to try first, and which we are allowed to fall to.
  ADD COLUMN IF NOT EXISTS channel_preference TEXT NOT NULL DEFAULT 'whatsapp',
  ADD COLUMN IF NOT EXISTS fallback_allowed   BOOLEAN NOT NULL DEFAULT TRUE,
  -- The duplicate guard. See the unique index below.
  ADD COLUMN IF NOT EXISTS idempotency_key    TEXT,
  ADD COLUMN IF NOT EXISTS otp_challenge_id   UUID
      REFERENCES identity.otp_challenges (id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS campaign_id        UUID,
  -- Template variables. Must never contain an OTP or any secret.
  ADD COLUMN IF NOT EXISTS variables          JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS delivered_at       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS failed_at          TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS completed_at       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- `provider` and `provider_message_id` now live on the attempt, not the
-- notification: one notification can be tried on WhatsApp and then on SMS.
ALTER TABLE ops.notifications
  ALTER COLUMN provider DROP NOT NULL,
  ALTER COLUMN template DROP NOT NULL,
  ALTER COLUMN channel  DROP NOT NULL;

ALTER TABLE ops.notifications
  ADD CONSTRAINT notifications_category_check CHECK (
    category IN ('transactional', 'otp', 'marketing')
  ),
  ADD CONSTRAINT notifications_status_check CHECK (
    status IN (
      'pending',    -- created, not yet picked up
      'sending',    -- a provider attempt is in flight
      'sent',       -- a provider accepted it (NOT the same as delivered)
      'delivered',  -- provider confirmed delivery to the handset
      'read',       -- WhatsApp only
      'failed',     -- every permitted channel failed
      'unknown',    -- accepted somewhere but no delivery signal arrived
      'cancelled'   -- suppressed by consent, opt-out or admin
    )
  ),
  ADD CONSTRAINT notifications_channel_preference_check CHECK (
    channel_preference IN ('whatsapp', 'sms', 'email')
  ),
  ADD CONSTRAINT notifications_reference_type_check CHECK (
    reference_type IS NULL
    OR reference_type IN ('order', 'user', 'return', 'review', 'payment', 'campaign', 'otp')
  );

COMMENT ON TABLE ops.notifications IS
  'One row per logical notification. Provider attempts hang off '
  'ops.notification_attempts — a single notification may be tried on WhatsApp '
  'and then on SMS, and both attempts belong to this one row.';

-- The idempotency guarantee. Same pattern as commerce.webhook_events: a repeat
-- caller loses the insert race rather than passing an application-level check.
CREATE UNIQUE INDEX IF NOT EXISTS notifications_idempotency_key_unique
  ON ops.notifications (idempotency_key)
  WHERE idempotency_key IS NOT NULL;

COMMENT ON INDEX ops.notifications_idempotency_key_unique IS
  'Duplicate order events, webhook retries and worker retries all collapse here.';

CREATE INDEX IF NOT EXISTS notifications_queue_idx
  ON ops.notifications (created_at)
  WHERE status IN ('pending', 'sending');

CREATE INDEX IF NOT EXISTS notifications_user_idx
  ON ops.notifications (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS notifications_type_idx
  ON ops.notifications (notification_type, created_at DESC);

CREATE INDEX IF NOT EXISTS notifications_reference_idx
  ON ops.notifications (reference_type, reference_id);

CREATE TRIGGER notifications_set_updated_at
  BEFORE UPDATE ON ops.notifications
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();


-- =============================================================================
-- ops.notification_attempts — one row per provider try
-- =============================================================================

CREATE TABLE IF NOT EXISTS ops.notification_attempts (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  notification_id     UUID NOT NULL
    REFERENCES ops.notifications (id) ON DELETE CASCADE,

  attempt_number      INTEGER NOT NULL DEFAULT 1,

  provider            TEXT NOT NULL,   -- whatsapp | message_central | resend | smtp
  channel             TEXT NOT NULL,   -- whatsapp | sms | email

  -- Distinct from delivery: 'accepted' only means the provider took the request.
  status              TEXT NOT NULL DEFAULT 'requested',

  provider_message_id TEXT,
  template_name       TEXT,

  failure_code        TEXT,
  failure_reason      TEXT,
  -- transient | permanent | unknown. Only 'permanent' may trigger fallback.
  error_class         TEXT,

  requested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  accepted_at         TIMESTAMPTZ,
  delivered_at        TIMESTAMPTZ,
  read_at             TIMESTAMPTZ,
  failed_at           TIMESTAMPTZ,

  raw_response        JSONB NOT NULL DEFAULT '{}'::jsonb,

  CONSTRAINT notification_attempts_provider_check CHECK (
    provider IN ('whatsapp', 'message_central', 'resend', 'smtp')
  ),
  CONSTRAINT notification_attempts_channel_check CHECK (
    channel IN ('whatsapp', 'sms', 'email')
  ),
  CONSTRAINT notification_attempts_status_check CHECK (
    status IN ('requested', 'accepted', 'sent', 'delivered', 'read', 'failed', 'unknown')
  ),
  CONSTRAINT notification_attempts_error_class_check CHECK (
    error_class IS NULL OR error_class IN ('transient', 'permanent', 'unknown')
  ),
  CONSTRAINT notification_attempts_number_positive CHECK (attempt_number > 0)
);

COMMENT ON TABLE ops.notification_attempts IS
  'Provider-level attempts. status tracks the delivery ladder explicitly — '
  'accepted (provider took it) is deliberately NOT the same as delivered, and '
  'only a definitive permanent failure is allowed to trigger channel fallback.';

COMMENT ON COLUMN ops.notification_attempts.error_class IS
  'transient = retry; permanent = fall back to the next channel; unknown = '
  'reconcile, never assume failure (the message may have arrived).';

-- One attempt per channel per notification: this is what stops a worker retry
-- from sending the same WhatsApp message twice.
CREATE UNIQUE INDEX IF NOT EXISTS notification_attempts_once_idx
  ON ops.notification_attempts (notification_id, channel, attempt_number);

COMMENT ON INDEX ops.notification_attempts_once_idx IS
  'Worker retries collapse onto the existing attempt instead of re-sending.';

-- Webhook lookups arrive keyed by provider message id.
CREATE UNIQUE INDEX IF NOT EXISTS notification_attempts_provider_msg_idx
  ON ops.notification_attempts (provider, provider_message_id)
  WHERE provider_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS notification_attempts_notification_idx
  ON ops.notification_attempts (notification_id, requested_at);

-- Attempts that were accepted but never confirmed — the reconciler's queue.
CREATE INDEX IF NOT EXISTS notification_attempts_unresolved_idx
  ON ops.notification_attempts (requested_at)
  WHERE status IN ('requested', 'accepted', 'sent', 'unknown');


-- =============================================================================
-- ops.notification_templates — provider template mapping, not hardcoded
-- =============================================================================

CREATE TABLE IF NOT EXISTS ops.notification_templates (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  notification_type     TEXT NOT NULL,
  channel               TEXT NOT NULL,
  provider              TEXT NOT NULL,

  -- Meta template name + language, e.g. 'chicaboo_order_confirmed' / 'en'.
  provider_template_name TEXT,
  provider_template_id   TEXT,
  language               TEXT NOT NULL DEFAULT 'en',

  -- Meta's own classification. Utility templates must never carry marketing.
  category              TEXT NOT NULL DEFAULT 'utility',

  -- Ordered variable names bound to template {{1}}, {{2}}, ... Used to render
  -- and to validate a caller supplied everything the template needs.
  variable_order        JSONB NOT NULL DEFAULT '[]'::jsonb,

  -- Plain-text body for SMS/email channels, with {placeholders}.
  body_text             TEXT,

  is_active             BOOLEAN NOT NULL DEFAULT TRUE,

  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT notification_templates_channel_check CHECK (
    channel IN ('whatsapp', 'sms', 'email')
  ),
  CONSTRAINT notification_templates_provider_check CHECK (
    provider IN ('whatsapp', 'message_central', 'resend', 'smtp')
  ),
  CONSTRAINT notification_templates_category_check CHECK (
    category IN ('authentication', 'utility', 'marketing')
  )
);

COMMENT ON TABLE ops.notification_templates IS
  'Maps a notification type to a provider template. Template names/ids are data, '
  'not code, so Meta-approved templates can be swapped without a deploy — and so '
  'the frontend can never choose an arbitrary template id.';

CREATE UNIQUE INDEX IF NOT EXISTS notification_templates_lookup_unique
  ON ops.notification_templates (notification_type, channel, provider, language)
  WHERE is_active;

CREATE TRIGGER notification_templates_set_updated_at
  BEFORE UPDATE ON ops.notification_templates
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();


-- =============================================================================
-- ops.whatsapp_webhook_events — Meta deliveries, deduplicated by the database
-- =============================================================================

CREATE TABLE IF NOT EXISTS ops.whatsapp_webhook_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Meta's per-status/message id. Unique per (id, status) pair.
  provider_message_id TEXT,
  event_type          TEXT NOT NULL,   -- sent | delivered | read | failed | message
  status_value        TEXT,

  signature_valid     BOOLEAN NOT NULL DEFAULT FALSE,
  processing_status   TEXT NOT NULL DEFAULT 'received',

  attempt_id          UUID REFERENCES ops.notification_attempts (id) ON DELETE SET NULL,

  payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
  error               TEXT,

  processed_at        TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT whatsapp_webhook_events_processing_status_check CHECK (
    processing_status IN ('received', 'processed', 'ignored', 'failed', 'duplicate')
  )
);

COMMENT ON TABLE ops.whatsapp_webhook_events IS
  'Every Meta webhook delivery, valid signature or not — repeated signature '
  'failures against this endpoint are a security signal worth keeping.';

-- Meta redelivers the same (message id, status) pair; this is the dedupe key.
CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_webhook_events_dedupe_idx
  ON ops.whatsapp_webhook_events (provider_message_id, event_type, status_value)
  WHERE provider_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS whatsapp_webhook_events_unprocessed_idx
  ON ops.whatsapp_webhook_events (created_at DESC)
  WHERE processing_status IN ('received', 'failed');


-- =============================================================================
-- ops.notification_campaigns — marketing, deliberately separate from
-- transactional so a campaign can never borrow a utility template
-- =============================================================================

CREATE TABLE IF NOT EXISTS ops.notification_campaigns (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  name                TEXT NOT NULL,
  notification_type   TEXT NOT NULL DEFAULT 'MARKETING_BROADCAST',
  template_id         UUID REFERENCES ops.notification_templates (id),

  -- Marketing defaults to WhatsApp-only. SMS fallback is opt-in per campaign,
  -- never implicit — a failed marketing WhatsApp must not become a paid SMS.
  sms_fallback_enabled BOOLEAN NOT NULL DEFAULT FALSE,

  status              TEXT NOT NULL DEFAULT 'draft',
  audience_filter     JSONB NOT NULL DEFAULT '{}'::jsonb,
  variables           JSONB NOT NULL DEFAULT '{}'::jsonb,

  scheduled_at        TIMESTAMPTZ,
  started_at          TIMESTAMPTZ,
  completed_at        TIMESTAMPTZ,

  total_recipients    INTEGER NOT NULL DEFAULT 0,
  sent_count          INTEGER NOT NULL DEFAULT 0,
  delivered_count     INTEGER NOT NULL DEFAULT 0,
  failed_count        INTEGER NOT NULL DEFAULT 0,

  created_by_admin_id UUID,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT notification_campaigns_status_check CHECK (
    status IN ('draft', 'scheduled', 'running', 'paused', 'completed', 'cancelled')
  )
);

COMMENT ON COLUMN ops.notification_campaigns.sms_fallback_enabled IS
  'Opt-in per campaign. Default FALSE so marketing never silently becomes SMS.';

CREATE INDEX IF NOT EXISTS notification_campaigns_status_idx
  ON ops.notification_campaigns (status, scheduled_at);

CREATE TRIGGER notification_campaigns_set_updated_at
  BEFORE UPDATE ON ops.notification_campaigns
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();


-- =============================================================================
-- admin.notification_types — widen the channel vocabulary (also unused today,
-- kept as the human-readable registry the templates table points at)
-- =============================================================================

ALTER TABLE admin.notification_types
  DROP CONSTRAINT IF EXISTS notification_types_channel_check;

ALTER TABLE admin.notification_types
  ADD CONSTRAINT notification_types_channel_check CHECK (
    channel IN ('email', 'sms', 'push', 'whatsapp')
  );


-- =============================================================================
-- Consent — WhatsApp needs its own columns; reusing sms_marketing would opt
-- customers into a channel they never agreed to
-- =============================================================================

ALTER TABLE public.user_preferences
  ADD COLUMN IF NOT EXISTS whatsapp_marketing     BOOLEAN NOT NULL DEFAULT FALSE,
  -- Transactional defaults on: order updates are a service message, and the
  -- customer asked for them by placing an order.
  ADD COLUMN IF NOT EXISTS whatsapp_transactional BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN public.user_preferences.whatsapp_marketing IS
  'Explicit opt-in. Marketing WhatsApp requires this; transactional does not.';
COMMENT ON COLUMN public.user_preferences.whatsapp_transactional IS
  'Order/payment updates over WhatsApp. On by default as a service message.';


-- =============================================================================
-- Template seeds
--
-- WhatsApp rows carry the Meta template NAME, which must match a template
-- approved in the WhatsApp Manager before it will send. They are seeded inactive
-- for WhatsApp until you confirm the approved names, so a half-configured
-- deployment falls through to SMS rather than failing every send.
--
-- SMS rows carry the literal body and are active immediately, which is what
-- preserves today's behaviour during the migration.
-- =============================================================================

INSERT INTO ops.notification_templates
  (notification_type, channel, provider, provider_template_name, language,
   category, variable_order, body_text, is_active)
VALUES
  -- OTP. `otp` is the variable name the WhatsApp provider also copies into the
  -- authentication template's copy-code button.
  ('OTP_PHONE_VERIFY', 'whatsapp', 'whatsapp', 'chicaboo_otp_verify', 'en',
   'authentication', '["otp"]'::jsonb, NULL, FALSE),
  ('OTP_PHONE_VERIFY', 'sms', 'message_central', NULL, 'en',
   'authentication', '[]'::jsonb,
   'Your Chic A Boo verification code is {otp}. It expires in 10 minutes. Do not share it with anyone.', TRUE),

  ('OTP_LOGIN', 'whatsapp', 'whatsapp', 'chicaboo_otp_login', 'en',
   'authentication', '["otp"]'::jsonb, NULL, FALSE),
  ('OTP_LOGIN', 'sms', 'message_central', NULL, 'en',
   'authentication', '[]'::jsonb,
   'Your Chic A Boo login code is {otp}. It expires in 10 minutes. Do not share it with anyone.', TRUE),

  ('OTP_PASSWORD_RESET', 'whatsapp', 'whatsapp', 'chicaboo_otp_reset', 'en',
   'authentication', '["otp"]'::jsonb, NULL, FALSE),
  ('OTP_PASSWORD_RESET', 'sms', 'message_central', NULL, 'en',
   'authentication', '[]'::jsonb,
   'Your Chic A Boo password reset code is {otp}. It expires in 10 minutes.', TRUE),

  ('OTP_CHANGE_PHONE', 'whatsapp', 'whatsapp', 'chicaboo_otp_verify', 'en',
   'authentication', '["otp"]'::jsonb, NULL, FALSE),
  ('OTP_CHANGE_PHONE', 'sms', 'message_central', NULL, 'en',
   'authentication', '[]'::jsonb,
   'Your Chic A Boo verification code is {otp}. It expires in 10 minutes.', TRUE),

  -- Order lifecycle (utility category — never marketing content in these).
  ('ORDER_CONFIRMED', 'whatsapp', 'whatsapp', 'chicaboo_order_confirmed', 'en',
   'utility', '["customer_name","order_number","total"]'::jsonb, NULL, FALSE),
  ('ORDER_CONFIRMED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Order #{order_number} confirmed. Total {total}. Track it at chicaboo.co/track-order', TRUE),

  ('PAYMENT_CONFIRMED', 'whatsapp', 'whatsapp', 'chicaboo_payment_confirmed', 'en',
   'utility', '["order_number","total"]'::jsonb, NULL, FALSE),
  ('PAYMENT_CONFIRMED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Payment of {total} received for order #{order_number}. Thank you!', TRUE),

  ('PAYMENT_FAILED', 'whatsapp', 'whatsapp', 'chicaboo_payment_failed', 'en',
   'utility', '["order_number"]'::jsonb, NULL, FALSE),
  ('PAYMENT_FAILED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Payment for order #{order_number} could not be completed. Your bag is saved - try again at chicaboo.co', TRUE),

  ('ORDER_SHIPPED', 'whatsapp', 'whatsapp', 'chicaboo_order_shipped', 'en',
   'utility', '["order_number","tracking_url"]'::jsonb, NULL, FALSE),
  ('ORDER_SHIPPED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Order #{order_number} has shipped. Track: {tracking_url}', TRUE),

  ('OUT_FOR_DELIVERY', 'whatsapp', 'whatsapp', 'chicaboo_out_for_delivery', 'en',
   'utility', '["order_number"]'::jsonb, NULL, FALSE),
  ('OUT_FOR_DELIVERY', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Order #{order_number} is out for delivery today.', TRUE),

  ('ORDER_DELIVERED', 'whatsapp', 'whatsapp', 'chicaboo_order_delivered', 'en',
   'utility', '["order_number"]'::jsonb, NULL, FALSE),
  ('ORDER_DELIVERED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Order #{order_number} has been delivered. We hope you love it!', TRUE),

  ('ORDER_CANCELLED', 'whatsapp', 'whatsapp', 'chicaboo_order_cancelled', 'en',
   'utility', '["order_number"]'::jsonb, NULL, FALSE),
  ('ORDER_CANCELLED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Order #{order_number} has been cancelled.', TRUE),

  ('REFUND_INITIATED', 'whatsapp', 'whatsapp', 'chicaboo_refund_initiated', 'en',
   'utility', '["order_number","amount"]'::jsonb, NULL, FALSE),
  ('REFUND_INITIATED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Refund of {amount} initiated for order #{order_number}. It reaches your account in 5-7 working days.', TRUE),

  ('REFUND_COMPLETED', 'whatsapp', 'whatsapp', 'chicaboo_refund_completed', 'en',
   'utility', '["order_number","amount"]'::jsonb, NULL, FALSE),
  ('REFUND_COMPLETED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Refund of {amount} for order #{order_number} is complete.', TRUE),

  ('RETURN_CREATED', 'whatsapp', 'whatsapp', 'chicaboo_return_created', 'en',
   'utility', '["order_number"]'::jsonb, NULL, FALSE),
  ('RETURN_CREATED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Return request received for order #{order_number}.', TRUE),

  ('RETURN_APPROVED', 'whatsapp', 'whatsapp', 'chicaboo_return_approved', 'en',
   'utility', '["order_number"]'::jsonb, NULL, FALSE),
  ('RETURN_APPROVED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Your return for order #{order_number} has been approved.', TRUE),

  ('RETURN_REJECTED', 'whatsapp', 'whatsapp', 'chicaboo_return_rejected', 'en',
   'utility', '["order_number"]'::jsonb, NULL, FALSE),
  ('RETURN_REJECTED', 'sms', 'message_central', NULL, 'en',
   'utility', '[]'::jsonb,
   'Chic A Boo: Your return for order #{order_number} could not be approved. Contact us for details.', TRUE),

  ('EXCHANGE_CREATED', 'whatsapp', 'whatsapp', 'chicaboo_exchange_created', 'en',
   'utility', '["order_number"]'::jsonb, NULL, FALSE),
  ('EXCHANGE_COMPLETED', 'whatsapp', 'whatsapp', 'chicaboo_exchange_completed', 'en',
   'utility', '["order_number"]'::jsonb, NULL, FALSE)
ON CONFLICT DO NOTHING;

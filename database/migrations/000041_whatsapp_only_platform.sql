-- WhatsApp becomes the only delivery channel, and the platform grows the pieces
-- that were specified but never built: campaign recipients, a retry ladder,
-- abandoned-cart state and daily analytics.
--
-- Two things this migration deliberately does NOT do:
--
--   * It does not drop the SMS template rows or widen away the `msg91` /
--     `message_central` provider values. Those rows are history — 13 historical
--     attempts name a vendor that no longer exists, and rewriting them would
--     assert that a message went out over a transport it never used. Templates
--     are config, so they are deactivated; attempts are facts, so they are left
--     exactly as they are.
--
--   * It does not create parallel `whatsapp_*` tables. `ops.notifications`,
--     `ops.notification_templates` and `ops.notification_campaigns` already hold
--     precisely this data and are live in production. A second set would mean two
--     answers to "what is this customer's current OTP".
--
-- Order matters in section 2: the CHECK is widened BEFORE the new rows land, or
-- every INSERT violates the constraint that does not yet list the new types.


-- =============================================================================
-- 1. Consent — abandoned cart gets its own opt-in
-- =============================================================================
-- Separate from whatsapp_marketing on purpose. A customer who wants order
-- updates and no promotional blasts may still want to be told they left
-- something in the basket, and folding the two together makes that unexpressible.

ALTER TABLE public.user_preferences
  ADD COLUMN IF NOT EXISTS whatsapp_abandoned_cart BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.user_preferences.whatsapp_abandoned_cart IS
  'Explicit opt-in for cart reminders. Meta classifies these as MARKETING '
  'templates, so they are consent-gated — but on their own flag, independent '
  'of whatsapp_marketing.';


-- =============================================================================
-- 2. Notification type vocabulary
-- =============================================================================
-- `notification_type` is free TEXT on ops.notifications (no CHECK), but
-- admin.notification_types is the reference list the admin UI reads. Widen it
-- first so the template seeds below have something to point at.

-- `template` here is the Meta template name, matching provider_template_name in
-- ops.notification_templates. `is_transactional` is FALSE for anything Meta
-- classifies as marketing, which is what the admin UI reads to decide whether a
-- type is consent-gated.
INSERT INTO admin.notification_types
  (code, channel, template, description, is_transactional, is_active)
VALUES
  ('OTP_REGISTRATION',      'whatsapp', 'chicaboo_otp_signup',
   'Signup verification code',         TRUE,  TRUE),
  ('ORDER_PROCESSING',      'whatsapp', 'chicaboo_order_processing',
   'Order moved into processing',      TRUE,  TRUE),
  ('ORDER_PACKED',          'whatsapp', 'chicaboo_order_packed',
   'Order packed and awaiting pickup', TRUE,  TRUE),
  ('CART_REMINDER_FIRST',   'whatsapp', 'chicaboo_cart_reminder_1',
   'Cart abandoned — first nudge',     FALSE, TRUE),
  ('CART_REMINDER_SECOND',  'whatsapp', 'chicaboo_cart_reminder_2',
   'Cart abandoned — second nudge',    FALSE, TRUE),
  ('CART_REMINDER_COUPON',  'whatsapp', 'chicaboo_cart_coupon',
   'Cart abandoned — coupon offer',    FALSE, TRUE),
  ('WELCOME_OFFER',         'whatsapp', 'chicaboo_welcome_offer',
   'Welcome offer for new signups',    FALSE, TRUE),
  ('FIRST_PURCHASE_COUPON', 'whatsapp', 'chicaboo_first_purchase',
   'Coupon after first purchase',      FALSE, TRUE),
  ('FESTIVAL_SALE',         'whatsapp', 'chicaboo_festival_sale',
   'Festival sale announcement',       FALSE, TRUE),
  ('FLASH_SALE',            'whatsapp', 'chicaboo_flash_sale',
   'Flash sale announcement',          FALSE, TRUE),
  ('LIMITED_OFFER',         'whatsapp', 'chicaboo_limited_offer',
   'Limited-time offer',               FALSE, TRUE),
  ('NEW_COLLECTION',        'whatsapp', 'chicaboo_new_collection',
   'New collection launch',            FALSE, TRUE),
  ('COUPON_EXPIRING',       'whatsapp', 'chicaboo_coupon_expiring',
   'Coupon about to expire',           FALSE, TRUE),
  ('PRICE_DROP',            'whatsapp', 'chicaboo_price_drop',
   'Watched item dropped in price',    FALSE, TRUE),
  ('RESTOCKED_ITEM',        'whatsapp', 'chicaboo_restocked',
   'Watched item back in stock',       FALSE, TRUE)
ON CONFLICT (code) DO NOTHING;


-- =============================================================================
-- 3. Retire the SMS channel
-- =============================================================================
-- Deactivated rather than deleted. `notification_templates_lookup_unique` is a
-- partial index over `WHERE is_active`, so an inactive row cannot collide with
-- the WhatsApp row for the same type — and keeping them documents what the SMS
-- copy said when those historical attempts were made.

UPDATE ops.notification_templates
   SET is_active = FALSE
 WHERE channel = 'sms';

COMMENT ON TABLE ops.notification_templates IS
  'Maps a notification type to a provider template. WhatsApp is the only active '
  'channel; SMS rows are retained inactive as a record of retired copy. Template '
  'names are data, not code, so a Meta-approved template can be swapped without '
  'a deploy — and the frontend can never choose an arbitrary template id.';


-- =============================================================================
-- 4. Activate the WhatsApp templates
-- =============================================================================
-- These were seeded is_active=FALSE in 000036 because the business was not yet
-- Meta-verified and unapproved templates fail with error 132001. Verification
-- has landed, so they go live here.
--
-- If a template has NOT actually been approved in WhatsApp Manager, its send
-- fails with a PERMANENT 132001 and the notification is marked failed — it does
-- not hang. Deactivate the individual row to stop trying.

UPDATE ops.notification_templates
   SET is_active = TRUE
 WHERE channel = 'whatsapp'
   AND provider = 'whatsapp';


-- =============================================================================
-- 5. Templates that did not exist yet
-- =============================================================================
-- `variable_order` is the contract with Meta: position N in this array binds to
-- {{N}} in the approved template body. Getting the order wrong is error 132012,
-- not a silent mis-render, so it is stored next to the name rather than inferred.

INSERT INTO ops.notification_templates
  (notification_type, channel, provider, provider_template_name, language,
   category, variable_order, body_text, is_active)
VALUES
  -- Signup OTP. OTP_REGISTRATION existed in the enum with no template behind it,
  -- so a signup code had nothing to render and failed 'template_missing'.
  ('OTP_REGISTRATION', 'whatsapp', 'whatsapp', 'chicaboo_otp_signup', 'en',
   'authentication', '["otp"]'::jsonb, NULL, TRUE),

  -- Order lifecycle gaps. PENDING has no message on purpose: it is the state an
  -- order is in before payment, and messaging it would tell customers about
  -- carts they abandoned mid-checkout.
  ('ORDER_PROCESSING', 'whatsapp', 'whatsapp', 'chicaboo_order_processing', 'en',
   'utility', '["customer_name","order_number"]'::jsonb, NULL, TRUE),
  ('ORDER_PACKED', 'whatsapp', 'whatsapp', 'chicaboo_order_packed', 'en',
   'utility', '["customer_name","order_number"]'::jsonb, NULL, TRUE),

  -- Abandoned cart. Marketing category — Meta will reject these as utility.
  ('CART_REMINDER_FIRST', 'whatsapp', 'whatsapp', 'chicaboo_cart_reminder_1', 'en',
   'marketing', '["customer_name","item_name","cart_url"]'::jsonb, NULL, TRUE),
  ('CART_REMINDER_SECOND', 'whatsapp', 'whatsapp', 'chicaboo_cart_reminder_2', 'en',
   'marketing', '["customer_name","item_name","cart_url"]'::jsonb, NULL, TRUE),
  ('CART_REMINDER_COUPON', 'whatsapp', 'whatsapp', 'chicaboo_cart_coupon', 'en',
   'marketing', '["customer_name","coupon_code","discount","cart_url"]'::jsonb, NULL, TRUE),

  -- Marketing set.
  ('WELCOME_OFFER', 'whatsapp', 'whatsapp', 'chicaboo_welcome_offer', 'en',
   'marketing', '["customer_name","coupon_code","discount"]'::jsonb, NULL, TRUE),
  ('FIRST_PURCHASE_COUPON', 'whatsapp', 'whatsapp', 'chicaboo_first_purchase', 'en',
   'marketing', '["customer_name","coupon_code","discount"]'::jsonb, NULL, TRUE),
  ('FESTIVAL_SALE', 'whatsapp', 'whatsapp', 'chicaboo_festival_sale', 'en',
   'marketing', '["customer_name","festival_name","discount","shop_url"]'::jsonb, NULL, TRUE),
  ('FLASH_SALE', 'whatsapp', 'whatsapp', 'chicaboo_flash_sale', 'en',
   'marketing', '["customer_name","discount","hours_left","shop_url"]'::jsonb, NULL, TRUE),
  ('LIMITED_OFFER', 'whatsapp', 'whatsapp', 'chicaboo_limited_offer', 'en',
   'marketing', '["customer_name","offer_text","expires_on","shop_url"]'::jsonb, NULL, TRUE),
  ('NEW_COLLECTION', 'whatsapp', 'whatsapp', 'chicaboo_new_collection', 'en',
   'marketing', '["customer_name","collection_name","shop_url"]'::jsonb, NULL, TRUE),
  ('COUPON_EXPIRING', 'whatsapp', 'whatsapp', 'chicaboo_coupon_expiring', 'en',
   'marketing', '["customer_name","coupon_code","expires_on"]'::jsonb, NULL, TRUE),
  ('PRICE_DROP', 'whatsapp', 'whatsapp', 'chicaboo_price_drop', 'en',
   'marketing', '["customer_name","item_name","new_price","product_url"]'::jsonb, NULL, TRUE),
  ('RESTOCKED_ITEM', 'whatsapp', 'whatsapp', 'chicaboo_restocked', 'en',
   'marketing', '["customer_name","item_name","product_url"]'::jsonb, NULL, TRUE),

  ('MARKETING_BROADCAST', 'whatsapp', 'whatsapp', 'chicaboo_broadcast', 'en',
   'marketing', '["customer_name","message"]'::jsonb, NULL, TRUE)
ON CONFLICT DO NOTHING;


-- =============================================================================
-- 6. Retry ladder on ops.notifications
-- =============================================================================
-- With no second channel to fall back to, a transient failure has to be retried
-- on WhatsApp itself. `next_retry_at` is what the worker polls; NULL means "not
-- waiting on a retry", which is why the index is partial.

ALTER TABLE ops.notifications
  ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_error    TEXT;

COMMENT ON COLUMN ops.notifications.attempt_count IS
  'Provider attempts made so far. Compared against NOTIFICATION_MAX_ATTEMPTS to '
  'decide between another retry and a terminal failure.';

COMMENT ON COLUMN ops.notifications.next_retry_at IS
  'When the retry worker may pick this up. NULL = not awaiting retry. Set only '
  'for TRANSIENT failures — an UNKNOWN waits for the webhook instead, because '
  'retrying a message that may already have arrived double-charges the customer '
  'in attention and Meta in conversation fees.';

CREATE INDEX IF NOT EXISTS notifications_retry_idx
  ON ops.notifications (next_retry_at)
  WHERE next_retry_at IS NOT NULL AND status = 'pending';

-- The reconciler's queue: accepted by Meta, but no delivery signal ever arrived.
CREATE INDEX IF NOT EXISTS notifications_unknown_idx
  ON ops.notifications (updated_at)
  WHERE status = 'unknown';


-- =============================================================================
-- 7. ops.campaign_recipients
-- =============================================================================
-- The one table on the spec that genuinely did not exist. It is the campaign's
-- unit of work: a row per (campaign, user) claimed exactly once, so two workers
-- running the same campaign cannot both message the same customer.

CREATE TABLE IF NOT EXISTS ops.campaign_recipients (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  campaign_id     UUID NOT NULL
    REFERENCES ops.notification_campaigns (id) ON DELETE CASCADE,

  user_id         UUID
    REFERENCES identity.users (id) ON DELETE CASCADE,

  -- Snapshotted at segment-resolution time. A user who changes their number
  -- mid-campaign should not silently receive the blast twice on two numbers.
  recipient       TEXT NOT NULL,

  -- Per-recipient template variables, merged over the campaign defaults.
  variables       JSONB NOT NULL DEFAULT '{}'::jsonb,

  status          TEXT NOT NULL DEFAULT 'pending',

  -- Set when the send is claimed, so the notification row can be found again
  -- and the delivery webhook can roll status back up to the campaign.
  notification_id UUID REFERENCES ops.notifications (id) ON DELETE SET NULL,

  failure_reason  TEXT,

  queued_at       TIMESTAMPTZ,
  sent_at         TIMESTAMPTZ,
  delivered_at    TIMESTAMPTZ,
  read_at         TIMESTAMPTZ,
  failed_at       TIMESTAMPTZ,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT campaign_recipients_status_check CHECK (
    status IN (
      'pending',    -- resolved into the audience, not yet sent
      'queued',     -- a notification row exists
      'sent',       -- provider accepted
      'delivered',
      'read',
      'failed',
      'skipped'     -- consent withdrawn between resolution and send
    )
  )
);

COMMENT ON TABLE ops.campaign_recipients IS
  'One row per campaign member. The unique index below is what makes campaign '
  'sending safe to re-run: a resumed or double-started campaign collapses onto '
  'existing rows rather than messaging anyone twice.';

-- The whole safety property of campaign sending. Without this a paused-then-
-- resumed campaign re-resolves its audience and messages everyone again.
CREATE UNIQUE INDEX IF NOT EXISTS campaign_recipients_once_idx
  ON ops.campaign_recipients (campaign_id, user_id)
  WHERE user_id IS NOT NULL;

-- Guest/phone-only recipients have no user_id, so they need their own guard.
CREATE UNIQUE INDEX IF NOT EXISTS campaign_recipients_once_phone_idx
  ON ops.campaign_recipients (campaign_id, recipient)
  WHERE user_id IS NULL;

-- The worker's claim query: oldest pending members of one campaign.
CREATE INDEX IF NOT EXISTS campaign_recipients_pending_idx
  ON ops.campaign_recipients (campaign_id, created_at)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS campaign_recipients_notification_idx
  ON ops.campaign_recipients (notification_id)
  WHERE notification_id IS NOT NULL;

-- Dropped first because the table above is created IF NOT EXISTS: on a re-run
-- against a database where it already exists, a bare CREATE TRIGGER would abort
-- the whole migration.
DROP TRIGGER IF EXISTS campaign_recipients_set_updated_at ON ops.campaign_recipients;

CREATE TRIGGER campaign_recipients_set_updated_at
  BEFORE UPDATE ON ops.campaign_recipients
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();


-- =============================================================================
-- 8. Campaign columns for WhatsApp sending
-- =============================================================================

ALTER TABLE ops.notification_campaigns
  -- MARKETING | PROMOTIONAL | BROADCAST | ABANDONED_CART | FLASH_SALE
  ADD COLUMN IF NOT EXISTS campaign_type TEXT NOT NULL DEFAULT 'MARKETING',
  -- Counters the analytics endpoint reads without scanning the recipient table.
  ADD COLUMN IF NOT EXISTS read_count    INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS skipped_count INTEGER NOT NULL DEFAULT 0,
  -- Set when an admin pauses a running campaign, so resume knows where it was.
  ADD COLUMN IF NOT EXISTS paused_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS created_by    UUID;

ALTER TABLE ops.notification_campaigns
  DROP CONSTRAINT IF EXISTS notification_campaigns_type_check;

ALTER TABLE ops.notification_campaigns
  ADD CONSTRAINT notification_campaigns_type_check CHECK (
    campaign_type IN (
      'MARKETING', 'PROMOTIONAL', 'BROADCAST', 'ABANDONED_CART', 'FLASH_SALE'
    )
  );

COMMENT ON COLUMN ops.notification_campaigns.audience_filter IS
  'Segment definition as JSON, interpreted by app/notifications/segmentation.py. '
  'Stored rather than a resolved user list so a scheduled campaign targets who '
  'matches at send time, not who matched when it was drafted.';

-- Scheduled campaigns the worker must pick up.
CREATE INDEX IF NOT EXISTS notification_campaigns_due_idx
  ON ops.notification_campaigns (scheduled_at)
  WHERE status = 'scheduled';


-- =============================================================================
-- 9. Abandoned-cart ladder state
-- =============================================================================
-- Which rung a cart has already received. Keyed by cart so the ladder survives
-- the cart being edited: adding an item does not restart the sequence from the
-- first reminder, it just moves the clock.

CREATE TABLE IF NOT EXISTS ops.cart_reminders (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  cart_id         UUID NOT NULL
    REFERENCES commerce.carts (id) ON DELETE CASCADE,

  user_id         UUID
    REFERENCES identity.users (id) ON DELETE CASCADE,

  -- 1 = first nudge, 2 = second, 3 = coupon.
  stage           SMALLINT NOT NULL,

  notification_id UUID REFERENCES ops.notifications (id) ON DELETE SET NULL,

  sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT cart_reminders_stage_check CHECK (stage BETWEEN 1 AND 3)
);

COMMENT ON TABLE ops.cart_reminders IS
  'One row per reminder actually sent. The unique index is the ladder: a cart '
  'cannot receive stage 2 twice, and a worker that crashes mid-batch resumes '
  'without re-nudging anyone.';

CREATE UNIQUE INDEX IF NOT EXISTS cart_reminders_once_idx
  ON ops.cart_reminders (cart_id, stage);

CREATE INDEX IF NOT EXISTS cart_reminders_cart_idx
  ON ops.cart_reminders (cart_id, stage DESC);


-- =============================================================================
-- 10. Daily analytics rollup
-- =============================================================================
-- Precomputed because the dashboard queries span months of ops.notifications,
-- and that table is on the OTP hot path — an analytics scan must not compete
-- with a login.

CREATE TABLE IF NOT EXISTS ops.notification_analytics_daily (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  day               DATE NOT NULL,
  notification_type TEXT NOT NULL,
  category          TEXT NOT NULL,
  channel           TEXT NOT NULL DEFAULT 'whatsapp',

  requested         INTEGER NOT NULL DEFAULT 0,
  sent              INTEGER NOT NULL DEFAULT 0,
  delivered         INTEGER NOT NULL DEFAULT 0,
  read              INTEGER NOT NULL DEFAULT 0,
  failed            INTEGER NOT NULL DEFAULT 0,
  unknown           INTEGER NOT NULL DEFAULT 0,

  computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT notification_analytics_daily_counts_nonneg CHECK (
    requested >= 0 AND sent >= 0 AND delivered >= 0
    AND read >= 0 AND failed >= 0 AND unknown >= 0
  )
);

-- Recomputing a day must overwrite it, not append a second version.
CREATE UNIQUE INDEX IF NOT EXISTS notification_analytics_daily_key
  ON ops.notification_analytics_daily (day, notification_type, channel);

CREATE INDEX IF NOT EXISTS notification_analytics_daily_day_idx
  ON ops.notification_analytics_daily (day DESC);


-- =============================================================================
-- 11. Channel policy defaults
-- =============================================================================
-- Existing rows still say the OTP channel is SMS. Nothing serves SMS any more,
-- so leaving them would route every pending notification at a provider that is
-- not registered — they would sit unsendable rather than fail loudly.

UPDATE ops.notifications
   SET channel_preference = 'whatsapp',
       fallback_allowed   = FALSE
 WHERE status IN ('pending', 'sending')
   AND channel_preference <> 'whatsapp';

COMMENT ON COLUMN ops.notifications.fallback_allowed IS
  'Retained for schema compatibility; always FALSE now that WhatsApp is the only '
  'channel. A future second transport would set it per notification again.';

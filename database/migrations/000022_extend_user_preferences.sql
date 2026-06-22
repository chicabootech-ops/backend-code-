-- =============================================================================
-- Migration: 000022_extend_user_preferences
-- Chic A Boo — communication preference columns (Architecture v3.0)
-- Depends on: 000003_public_domain
-- Blocks:     none
--
-- Extends public.user_preferences with communication / locale columns.
--
-- Note: email_marketing, sms_marketing, preferred_language, and currency
-- (preferred_currency) already exist from 000003. This migration adds
-- missing v3 columns and documents existing ones.
--
-- Backfill: DEFAULT values auto-apply to existing rows on ADD COLUMN.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Columns already present in 000003 (document only — no DDL):
--   email_marketing     BOOLEAN DEFAULT FALSE
--   sms_marketing       BOOLEAN DEFAULT FALSE
--   preferred_language  TEXT DEFAULT 'en'
--   currency            TEXT DEFAULT 'INR'  (serves as preferred_currency)
-- -----------------------------------------------------------------------------

COMMENT ON COLUMN public.user_preferences.email_marketing IS
  'Opt-in for promotional email (DPDP consent mirrored in consent_records).';
COMMENT ON COLUMN public.user_preferences.sms_marketing IS
  'Opt-in for promotional SMS.';
COMMENT ON COLUMN public.user_preferences.preferred_language IS
  'ISO 639-1 language code e.g. en, hi.';
COMMENT ON COLUMN public.user_preferences.currency IS
  'Preferred ISO 4217 currency code (preferred_currency); launch: INR.';


-- -----------------------------------------------------------------------------
-- New columns (Architecture v3.0 MODULE 2)
-- -----------------------------------------------------------------------------
ALTER TABLE public.user_preferences
  ADD COLUMN push_notifications   BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN analytics_tracking   BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN order_updates_email  BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN order_updates_sms      BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.user_preferences.push_notifications IS
  'Transactional + marketing push when mobile app launches.';
COMMENT ON COLUMN public.user_preferences.analytics_tracking IS
  'Allow product analytics (PostHog) for personalised experience.';
COMMENT ON COLUMN public.user_preferences.order_updates_email IS
  'Order status emails (shipped, delivered).';
COMMENT ON COLUMN public.user_preferences.order_updates_sms IS
  'Order status SMS notifications.';


-- =============================================================================
-- VERIFICATION QUERIES (manual)
-- =============================================================================
--
-- 1. New columns exist:
--    SELECT column_name, column_default
--    FROM information_schema.columns
--    WHERE table_schema = 'public' AND table_name = 'user_preferences'
--      AND column_name IN (
--        'push_notifications', 'analytics_tracking',
--        'order_updates_email', 'order_updates_sms',
--        'email_marketing', 'sms_marketing',
--        'preferred_language', 'currency'
--      )
--    ORDER BY column_name;
--
-- 2. Existing rows received defaults:
--    SELECT push_notifications, analytics_tracking
--    FROM public.user_preferences LIMIT 5;
--
-- 3. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000022_extend_user_preferences';


-- =============================================================================
-- ROLLBACK SQL (manual)
-- =============================================================================
--
-- ALTER TABLE public.user_preferences
--   DROP COLUMN IF EXISTS order_updates_sms,
--   DROP COLUMN IF EXISTS order_updates_email,
--   DROP COLUMN IF EXISTS analytics_tracking,
--   DROP COLUMN IF EXISTS push_notifications;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000022_extend_user_preferences';

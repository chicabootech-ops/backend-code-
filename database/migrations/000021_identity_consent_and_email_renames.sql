-- =============================================================================
-- Migration: 000021_identity_consent_and_email_renames
-- Chic A Boo — consent audit + email verification rename (Architecture v3.0)
-- Depends on: 000002_auth_domain
-- Blocks:     none
--
-- Creates:
--   identity.consent_records
--
-- Renames:
--   identity.email_otps → identity.email_verifications
--
-- Adds to email_verifications:
--   email_normalized, purpose, max_attempts, verified_at
--
-- Backfill: email_normalized, verified_at, purpose defaults
-- =============================================================================


-- =============================================================================
-- TABLE: identity.consent_records
-- Append-only DPDP / marketing consent audit trail.
-- =============================================================================
CREATE TABLE identity.consent_records (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  user_id                 UUID NOT NULL
    REFERENCES identity.users (id) ON DELETE CASCADE,

  consent_type            TEXT NOT NULL,

  granted                 BOOLEAN NOT NULL,

  ip_address              TEXT,

  user_agent              TEXT,

  source                  TEXT NOT NULL,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT consent_records_consent_type_check CHECK (
    consent_type IN (
      'marketing_email',
      'marketing_sms',
      'marketing_push',
      'terms',
      'privacy',
      'analytics'
    )
  ),

  CONSTRAINT consent_records_source_check CHECK (
    source IN ('registration', 'preferences', 'checkout', 'admin')
  )
);

COMMENT ON TABLE identity.consent_records IS
  'Append-only consent history; current prefs on public.user_preferences.';

COMMENT ON COLUMN identity.consent_records.id IS 'UUID primary key.';
COMMENT ON COLUMN identity.consent_records.user_id IS 'FK to identity.users.';
COMMENT ON COLUMN identity.consent_records.consent_type IS
  'marketing_email|marketing_sms|marketing_push|terms|privacy|analytics.';
COMMENT ON COLUMN identity.consent_records.granted IS
  'True = consent granted; false = withdrawn.';
COMMENT ON COLUMN identity.consent_records.ip_address IS 'Client IP at consent time.';
COMMENT ON COLUMN identity.consent_records.user_agent IS 'Client user agent.';
COMMENT ON COLUMN identity.consent_records.source IS
  'registration|preferences|checkout|admin.';
COMMENT ON COLUMN identity.consent_records.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN identity.consent_records.created_at IS 'UTC consent event time.';

CREATE INDEX consent_records_user_created_idx
  ON identity.consent_records (user_id, created_at DESC);

COMMENT ON INDEX identity.consent_records_user_created_idx IS
  'Consent history per user newest first.';

CREATE INDEX consent_records_consent_type_idx
  ON identity.consent_records (consent_type, created_at DESC);

COMMENT ON INDEX identity.consent_records_consent_type_idx IS
  'Audit queries by consent type.';


-- =============================================================================
-- RENAME: identity.email_otps → identity.email_verifications
-- =============================================================================
ALTER TABLE identity.email_otps RENAME TO email_verifications;


-- =============================================================================
-- Extend email_verifications
-- =============================================================================
ALTER TABLE identity.email_verifications
  ADD COLUMN email_normalized TEXT,
  ADD COLUMN purpose          TEXT NOT NULL DEFAULT 'registration',
  ADD COLUMN max_attempts     INTEGER NOT NULL DEFAULT 3,
  ADD COLUMN verified_at      TIMESTAMPTZ;

-- Backfill email_normalized from legacy email column.
UPDATE identity.email_verifications
SET email_normalized = lower(trim(email))
WHERE email_normalized IS NULL;

ALTER TABLE identity.email_verifications
  ALTER COLUMN email_normalized SET NOT NULL;

-- Backfill verified_at from legacy verified boolean.
UPDATE identity.email_verifications
SET verified_at = created_at
WHERE verified = TRUE
  AND verified_at IS NULL;

ALTER TABLE identity.email_verifications
  ADD CONSTRAINT email_verifications_purpose_check CHECK (
    purpose IN ('registration', 'login', 'password_reset', 'email_change')
  ),

  ADD CONSTRAINT email_verifications_max_attempts_positive CHECK (
    max_attempts > 0
  ),

  ADD CONSTRAINT email_verifications_attempts_lte_max CHECK (
    attempts <= max_attempts
  );

COMMENT ON TABLE identity.email_verifications IS
  'Email OTP / verification tokens (renamed from email_otps).';

COMMENT ON COLUMN identity.email_verifications.email_normalized IS
  'Lowercase trimmed email for lookups.';
COMMENT ON COLUMN identity.email_verifications.purpose IS
  'registration|login|password_reset|email_change.';
COMMENT ON COLUMN identity.email_verifications.max_attempts IS
  'Maximum verification attempts before lockout.';
COMMENT ON COLUMN identity.email_verifications.verified_at IS
  'UTC when OTP was successfully verified; NULL if pending.';
COMMENT ON COLUMN identity.email_verifications.verified IS
  'Legacy boolean; prefer verified_at IS NOT NULL in new code.';

-- Rename legacy indexes.
ALTER INDEX IF EXISTS email_otps_email_idx
  RENAME TO email_verifications_email_legacy_idx;

ALTER INDEX IF EXISTS email_otps_expires_at_idx
  RENAME TO email_verifications_expires_at_idx;

CREATE INDEX email_verifications_email_normalized_purpose_idx
  ON identity.email_verifications (email_normalized, purpose)
  WHERE verified_at IS NULL;

COMMENT ON INDEX identity.email_verifications_email_normalized_purpose_idx IS
  'Active (unverified) tokens per email and purpose.';


-- =============================================================================
-- VERIFICATION QUERIES (manual)
-- =============================================================================
--
-- 1. email_otps renamed:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'identity' AND table_name = 'email_verifications';
--
-- 2. email_normalized backfilled:
--    SELECT COUNT(*) FROM identity.email_verifications
--    WHERE email_normalized IS NULL;
--    -- Expect: 0
--
-- 3. verified_at backfill:
--    SELECT COUNT(*) FROM identity.email_verifications
--    WHERE verified = TRUE AND verified_at IS NULL;
--    -- Expect: 0
--
-- 4. consent_records exists:
--    SELECT COUNT(*) FROM information_schema.tables
--    WHERE table_schema = 'identity' AND table_name = 'consent_records';
--
-- 5. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000021_identity_consent_and_email_renames';


-- =============================================================================
-- ROLLBACK SQL (manual)
-- =============================================================================
--
-- DROP TABLE IF EXISTS identity.consent_records;
--
-- DROP INDEX IF EXISTS identity.email_verifications_email_normalized_purpose_idx;
-- ALTER INDEX IF EXISTS identity.email_verifications_expires_at_idx
--   RENAME TO email_otps_expires_at_idx;
-- ALTER INDEX IF EXISTS identity.email_verifications_email_legacy_idx
--   RENAME TO email_otps_email_idx;
--
-- ALTER TABLE identity.email_verifications
--   DROP CONSTRAINT IF EXISTS email_verifications_attempts_lte_max,
--   DROP CONSTRAINT IF EXISTS email_verifications_max_attempts_positive,
--   DROP CONSTRAINT IF EXISTS email_verifications_purpose_check,
--   DROP COLUMN IF EXISTS verified_at,
--   DROP COLUMN IF EXISTS max_attempts,
--   DROP COLUMN IF EXISTS purpose,
--   DROP COLUMN IF EXISTS email_normalized;
--
-- ALTER TABLE identity.email_verifications RENAME TO email_otps;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000021_identity_consent_and_email_renames';

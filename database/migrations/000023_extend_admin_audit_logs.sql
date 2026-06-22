-- =============================================================================
-- Migration: 000023_extend_admin_audit_logs
-- Chic A Boo — audit log domain extensions (Architecture v3.0)
-- Depends on: 000004_admin_domain, 000008_production_enhancements
-- Blocks:     none
--
-- Extends admin.audit_logs with domain / actor context.
--
-- Already present from 000008:
--   request_id, target_user_id, user_agent
--
-- Backfill: actor_type = 'admin', service_name = 'admin' on historical rows.
-- =============================================================================


ALTER TABLE admin.audit_logs
  ADD COLUMN domain          TEXT,
  ADD COLUMN actor_type      TEXT,
  ADD COLUMN service_name    TEXT,
  ADD COLUMN correlation_id  TEXT;

COMMENT ON COLUMN admin.audit_logs.domain IS
  'Bounded context: catalog, inventory, order, payment, user, admin.';
COMMENT ON COLUMN admin.audit_logs.actor_type IS
  'admin, user, or system.';
COMMENT ON COLUMN admin.audit_logs.service_name IS
  'Originating service: admin, backend, userservice, gateway.';
COMMENT ON COLUMN admin.audit_logs.correlation_id IS
  'Distributed trace / correlation ID (may equal request_id).';
COMMENT ON COLUMN admin.audit_logs.request_id IS
  'HTTP request ID from 000008.';
COMMENT ON COLUMN admin.audit_logs.target_user_id IS
  'Affected customer user ID from 000008.';
COMMENT ON COLUMN admin.audit_logs.user_agent IS
  'Client user agent from 000008.';

ALTER TABLE admin.audit_logs
  ADD CONSTRAINT audit_logs_domain_check CHECK (
    domain IS NULL
    OR domain IN ('catalog', 'inventory', 'order', 'payment', 'user', 'admin')
  ),

  ADD CONSTRAINT audit_logs_actor_type_check CHECK (
    actor_type IS NULL
    OR actor_type IN ('admin', 'user', 'system')
  );

-- Backfill historical rows written before domain columns existed.
UPDATE admin.audit_logs
SET
  actor_type   = COALESCE(actor_type, 'admin'),
  service_name = COALESCE(service_name, 'admin')
WHERE actor_type IS NULL
   OR service_name IS NULL;

CREATE INDEX audit_logs_domain_created_at_idx
  ON admin.audit_logs (domain, created_at DESC)
  WHERE domain IS NOT NULL;

COMMENT ON INDEX admin.audit_logs_domain_created_at_idx IS
  'Filter audit trail by bounded context.';

CREATE INDEX audit_logs_actor_type_idx
  ON admin.audit_logs (actor_type, created_at DESC)
  WHERE actor_type IS NOT NULL;

COMMENT ON INDEX admin.audit_logs_actor_type_idx IS
  'Filter audit by actor type.';

CREATE INDEX audit_logs_service_name_idx
  ON admin.audit_logs (service_name, created_at DESC)
  WHERE service_name IS NOT NULL;

COMMENT ON INDEX admin.audit_logs_service_name_idx IS
  'Filter audit by originating service.';

CREATE INDEX audit_logs_correlation_id_idx
  ON admin.audit_logs (correlation_id)
  WHERE correlation_id IS NOT NULL;

COMMENT ON INDEX admin.audit_logs_correlation_id_idx IS
  'Trace lookup by correlation ID.';


-- =============================================================================
-- VERIFICATION QUERIES (manual)
-- =============================================================================
--
-- 1. New columns exist:
--    SELECT column_name FROM information_schema.columns
--    WHERE table_schema = 'admin' AND table_name = 'audit_logs'
--      AND column_name IN (
--        'domain', 'actor_type', 'service_name', 'correlation_id',
--        'request_id', 'target_user_id', 'user_agent'
--      )
--    ORDER BY column_name;
--
-- 2. Backfill applied:
--    SELECT COUNT(*) FROM admin.audit_logs
--    WHERE actor_type IS NULL OR service_name IS NULL;
--    -- Expect: 0
--
-- 3. Domain index exists:
--    SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'admin' AND indexname = 'audit_logs_domain_created_at_idx';
--
-- 4. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000023_extend_admin_audit_logs';


-- =============================================================================
-- ROLLBACK SQL (manual)
-- =============================================================================
--
-- DROP INDEX IF EXISTS admin.audit_logs_correlation_id_idx;
-- DROP INDEX IF EXISTS admin.audit_logs_service_name_idx;
-- DROP INDEX IF EXISTS admin.audit_logs_actor_type_idx;
-- DROP INDEX IF EXISTS admin.audit_logs_domain_created_at_idx;
--
-- ALTER TABLE admin.audit_logs
--   DROP CONSTRAINT IF EXISTS audit_logs_actor_type_check,
--   DROP CONSTRAINT IF EXISTS audit_logs_domain_check,
--   DROP COLUMN IF EXISTS correlation_id,
--   DROP COLUMN IF EXISTS service_name,
--   DROP COLUMN IF EXISTS actor_type,
--   DROP COLUMN IF EXISTS domain;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000023_extend_admin_audit_logs';

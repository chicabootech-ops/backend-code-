-- =============================================================================
-- Migration: 000017_commerce_payments
-- Chic A Boo — payments, ledger, refunds (Architecture v3.0)
-- Depends on: 000016_commerce_orders
-- Blocks:     000019, 000020, 000025
--
-- Creates:
--   commerce.payment_customers  (relocated from public.payment_customers)
--   commerce.payments
--   commerce.payment_transactions
--   commerce.refunds
--
-- Relocation: public.payment_customers → commerce.payment_customers
--   (backfill + drop public table and RLS policy from 000009)
--
-- Deferred to 000019:
--   refunds.return_id → commerce.returns
-- =============================================================================


-- =============================================================================
-- TABLE: commerce.payment_customers
-- Provider customer mapping (Razorpay + future gateways).
-- =============================================================================
CREATE TABLE commerce.payment_customers (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  user_id                 UUID NOT NULL
    REFERENCES identity.users (id) ON DELETE CASCADE,

  provider                TEXT NOT NULL,

  provider_customer_id    TEXT NOT NULL,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at              TIMESTAMPTZ,

  CONSTRAINT payment_customers_provider_check CHECK (
    provider IN ('razorpay', 'stripe', 'payu', 'cashfree')
  )
);

COMMENT ON TABLE commerce.payment_customers IS
  'Maps identity.users to payment provider customer IDs. Relocated from public schema.';

COMMENT ON COLUMN commerce.payment_customers.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.payment_customers.user_id IS 'FK to identity.users.';
COMMENT ON COLUMN commerce.payment_customers.provider IS 'Payment gateway identifier.';
COMMENT ON COLUMN commerce.payment_customers.provider_customer_id IS
  'Customer ID at the payment provider.';
COMMENT ON COLUMN commerce.payment_customers.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.payment_customers.created_at IS 'UTC row creation time.';
COMMENT ON COLUMN commerce.payment_customers.updated_at IS 'UTC last update; trigger-maintained.';
COMMENT ON COLUMN commerce.payment_customers.deleted_at IS 'Soft delete timestamp.';

CREATE UNIQUE INDEX payment_customers_user_provider_unique
  ON commerce.payment_customers (user_id, provider)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.payment_customers_user_provider_unique IS
  'One active provider mapping per user.';

CREATE UNIQUE INDEX payment_customers_provider_customer_unique
  ON commerce.payment_customers (provider, provider_customer_id)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.payment_customers_provider_customer_unique IS
  'Provider customer IDs are globally unique among active rows.';

CREATE INDEX payment_customers_user_id_idx
  ON commerce.payment_customers (user_id);

COMMENT ON INDEX commerce.payment_customers_user_id_idx IS
  'Lookup all payment customers for a user.';

CREATE TRIGGER payment_customers_set_updated_at
  BEFORE UPDATE ON commerce.payment_customers
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER payment_customers_set_updated_at ON commerce.payment_customers IS
  'Sets updated_at = NOW() on every UPDATE.';


-- Backfill from public.payment_customers (empty in fresh deploys).
INSERT INTO commerce.payment_customers (
  id, user_id, provider, provider_customer_id,
  metadata, created_at, updated_at, deleted_at
)
SELECT
  id, user_id, provider, provider_customer_id,
  metadata, created_at, updated_at, deleted_at
FROM public.payment_customers;


-- Drop legacy public table (RLS policy and trigger from 000008/000009).
DROP POLICY IF EXISTS payment_customers_select_own ON public.payment_customers;

DROP TRIGGER IF EXISTS payment_customers_set_updated_at ON public.payment_customers;

DROP TABLE public.payment_customers;


-- =============================================================================
-- TABLE: commerce.payments
-- Multiple payment attempts per order allowed (UNIQUE order_id, attempt_number).
-- =============================================================================
CREATE TABLE commerce.payments (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  order_id                UUID NOT NULL
    REFERENCES commerce.orders (id) ON DELETE RESTRICT,

  user_id                 UUID
    REFERENCES identity.users (id) ON DELETE SET NULL,

  attempt_number          INTEGER NOT NULL DEFAULT 1,

  provider                TEXT NOT NULL,

  provider_order_id       TEXT,

  provider_payment_id     TEXT,

  amount_paise            BIGINT NOT NULL,

  currency                TEXT NOT NULL DEFAULT 'INR',

  status                  TEXT NOT NULL DEFAULT 'created',

  method                  TEXT,

  failure_reason          TEXT,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT payments_order_attempt_unique UNIQUE (order_id, attempt_number),

  CONSTRAINT payments_provider_check CHECK (
    provider IN ('razorpay', 'stripe', 'payu', 'cashfree')
  ),

  CONSTRAINT payments_status_check CHECK (
    status IN ('created', 'authorized', 'captured', 'failed', 'refunded')
  ),

  CONSTRAINT payments_method_check CHECK (
    method IS NULL
    OR method IN ('upi', 'card', 'netbanking', 'wallet', 'emi')
  ),

  CONSTRAINT payments_currency_check CHECK (currency = 'INR'),

  CONSTRAINT payments_amount_paise_positive CHECK (amount_paise > 0)
);

COMMENT ON TABLE commerce.payments IS
  'Payment attempts per order; multiple retries via attempt_number.';

COMMENT ON COLUMN commerce.payments.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.payments.order_id IS
  'FK to commerce.orders; NOT unique — multiple attempts allowed.';
COMMENT ON COLUMN commerce.payments.user_id IS
  'Paying user when registered; NULL for guest checkout.';
COMMENT ON COLUMN commerce.payments.attempt_number IS
  'Retry counter per order (1 = first attempt).';
COMMENT ON COLUMN commerce.payments.provider IS 'Payment gateway.';
COMMENT ON COLUMN commerce.payments.provider_order_id IS
  'Provider-side order ID e.g. Razorpay order_id.';
COMMENT ON COLUMN commerce.payments.provider_payment_id IS
  'Provider payment ID after authorization/capture.';
COMMENT ON COLUMN commerce.payments.amount_paise IS 'Payment amount in INR paise.';
COMMENT ON COLUMN commerce.payments.currency IS 'ISO currency; launch supports INR only.';
COMMENT ON COLUMN commerce.payments.status IS
  'created|authorized|captured|failed|refunded.';
COMMENT ON COLUMN commerce.payments.method IS 'Payment instrument when known.';
COMMENT ON COLUMN commerce.payments.failure_reason IS 'Provider failure message.';
COMMENT ON COLUMN commerce.payments.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.payments.created_at IS 'UTC payment record created.';
COMMENT ON COLUMN commerce.payments.updated_at IS 'UTC last status update.';

CREATE UNIQUE INDEX payments_provider_payment_id_unique
  ON commerce.payments (provider_payment_id)
  WHERE provider_payment_id IS NOT NULL;

COMMENT ON INDEX commerce.payments_provider_payment_id_unique IS
  'Provider payment IDs are unique when set.';

CREATE INDEX payments_order_id_idx
  ON commerce.payments (order_id);

COMMENT ON INDEX commerce.payments_order_id_idx IS
  'All payment attempts for an order.';

CREATE INDEX payments_status_idx
  ON commerce.payments (status);

COMMENT ON INDEX commerce.payments_status_idx IS
  'Reconciliation by payment status.';

CREATE INDEX payments_created_at_idx
  ON commerce.payments (created_at DESC);

COMMENT ON INDEX commerce.payments_created_at_idx IS
  'Recent payments reporting.';

CREATE TRIGGER payments_set_updated_at
  BEFORE UPDATE ON commerce.payments
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER payments_set_updated_at ON commerce.payments IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.payment_transactions
-- Append-only payment event ledger (webhook snapshots).
-- =============================================================================
CREATE TABLE commerce.payment_transactions (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  payment_id                  UUID NOT NULL
    REFERENCES commerce.payments (id) ON DELETE RESTRICT,

  transaction_type            TEXT NOT NULL,

  provider_transaction_id     TEXT,

  amount_paise                BIGINT NOT NULL,

  status                      TEXT NOT NULL,

  raw_payload                 JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT payment_transactions_type_check CHECK (
    transaction_type IN ('authorization', 'capture', 'refund', 'void')
  ),

  CONSTRAINT payment_transactions_status_check CHECK (
    status IN ('pending', 'success', 'failed')
  ),

  CONSTRAINT payment_transactions_amount_paise_positive CHECK (amount_paise > 0)
);

COMMENT ON TABLE commerce.payment_transactions IS
  'Append-only payment transaction ledger. Never UPDATE or DELETE.';

COMMENT ON COLUMN commerce.payment_transactions.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.payment_transactions.payment_id IS 'FK to commerce.payments.';
COMMENT ON COLUMN commerce.payment_transactions.transaction_type IS
  'authorization|capture|refund|void.';
COMMENT ON COLUMN commerce.payment_transactions.provider_transaction_id IS
  'Provider transaction/reference ID.';
COMMENT ON COLUMN commerce.payment_transactions.amount_paise IS 'Transaction amount in paise.';
COMMENT ON COLUMN commerce.payment_transactions.status IS 'pending|success|failed.';
COMMENT ON COLUMN commerce.payment_transactions.raw_payload IS
  'Webhook or API response snapshot.';
COMMENT ON COLUMN commerce.payment_transactions.created_at IS 'UTC event time.';

CREATE UNIQUE INDEX payment_transactions_provider_txn_unique
  ON commerce.payment_transactions (provider_transaction_id)
  WHERE provider_transaction_id IS NOT NULL;

COMMENT ON INDEX commerce.payment_transactions_provider_txn_unique IS
  'Provider transaction IDs unique when set.';

CREATE INDEX payment_transactions_payment_id_idx
  ON commerce.payment_transactions (payment_id);

COMMENT ON INDEX commerce.payment_transactions_payment_id_idx IS
  'Ledger entries for a payment.';

CREATE INDEX payment_transactions_created_at_idx
  ON commerce.payment_transactions (created_at DESC);

COMMENT ON INDEX commerce.payment_transactions_created_at_idx IS
  'Recent transaction events.';

CREATE OR REPLACE FUNCTION commerce.payment_transactions_prevent_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'commerce.payment_transactions is append-only; UPDATE and DELETE are forbidden';
END;
$$;

COMMENT ON FUNCTION commerce.payment_transactions_prevent_mutation() IS
  'Blocks UPDATE/DELETE on payment_transactions.';

CREATE TRIGGER payment_transactions_prevent_update
  BEFORE UPDATE ON commerce.payment_transactions
  FOR EACH ROW
  EXECUTE FUNCTION commerce.payment_transactions_prevent_mutation();

CREATE TRIGGER payment_transactions_prevent_delete
  BEFORE DELETE ON commerce.payment_transactions
  FOR EACH ROW
  EXECUTE FUNCTION commerce.payment_transactions_prevent_mutation();


-- =============================================================================
-- TABLE: commerce.refunds
-- Refund records linked to payments and orders.
-- return_id FK added in 000019 after commerce.returns exists.
-- =============================================================================
CREATE TABLE commerce.refunds (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  payment_id                  UUID NOT NULL
    REFERENCES commerce.payments (id) ON DELETE RESTRICT,

  order_id                    UUID NOT NULL
    REFERENCES commerce.orders (id) ON DELETE RESTRICT,

  return_id                   UUID,

  provider_refund_id          TEXT,

  amount_paise                BIGINT NOT NULL,

  status                      TEXT NOT NULL DEFAULT 'pending',

  reason                      TEXT,

  initiated_by_admin_id       UUID
    REFERENCES admin.admin_users (id) ON DELETE SET NULL,

  metadata                    JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT refunds_status_check CHECK (
    status IN ('pending', 'processed', 'failed')
  ),

  CONSTRAINT refunds_amount_paise_positive CHECK (amount_paise > 0)
);

COMMENT ON TABLE commerce.refunds IS
  'Payment refunds; optionally linked to a return (FK in 000019).';

COMMENT ON COLUMN commerce.refunds.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.refunds.payment_id IS 'FK to commerce.payments.';
COMMENT ON COLUMN commerce.refunds.order_id IS 'FK to commerce.orders.';
COMMENT ON COLUMN commerce.refunds.return_id IS
  'Optional return authorization; FK added in 000019.';
COMMENT ON COLUMN commerce.refunds.provider_refund_id IS 'Provider refund ID.';
COMMENT ON COLUMN commerce.refunds.amount_paise IS 'Refund amount in paise.';
COMMENT ON COLUMN commerce.refunds.status IS 'pending|processed|failed.';
COMMENT ON COLUMN commerce.refunds.reason IS 'Refund reason.';
COMMENT ON COLUMN commerce.refunds.initiated_by_admin_id IS
  'Admin who initiated manual refund.';
COMMENT ON COLUMN commerce.refunds.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.refunds.created_at IS 'UTC refund initiated.';
COMMENT ON COLUMN commerce.refunds.updated_at IS 'UTC last status update.';

CREATE UNIQUE INDEX refunds_provider_refund_id_unique
  ON commerce.refunds (provider_refund_id)
  WHERE provider_refund_id IS NOT NULL;

COMMENT ON INDEX commerce.refunds_provider_refund_id_unique IS
  'Provider refund IDs unique when set.';

CREATE INDEX refunds_order_id_idx
  ON commerce.refunds (order_id);

COMMENT ON INDEX commerce.refunds_order_id_idx IS
  'Refunds for an order.';

CREATE INDEX refunds_payment_id_idx
  ON commerce.refunds (payment_id);

COMMENT ON INDEX commerce.refunds_payment_id_idx IS
  'Refunds against a payment.';

CREATE INDEX refunds_return_id_idx
  ON commerce.refunds (return_id)
  WHERE return_id IS NOT NULL;

COMMENT ON INDEX commerce.refunds_return_id_idx IS
  'Refunds linked to a return (FK in 000019).';

CREATE TRIGGER refunds_set_updated_at
  BEFORE UPDATE ON commerce.refunds
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER refunds_set_updated_at ON commerce.refunds IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- VERIFICATION QUERIES (run manually after migrate.py — do not execute here)
-- =============================================================================
--
-- 1. payment_customers relocated:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'commerce' AND table_name = 'payment_customers';
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'public' AND table_name = 'payment_customers';
--    -- Expect: commerce yes, public no
--
-- 2. Row count match (if public had data before migrate):
--    SELECT COUNT(*) FROM commerce.payment_customers;
--
-- 3. Multiple payments per order allowed:
--    SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'commerce' AND indexname = 'payments_order_attempt_unique';
--
-- 4. provider_payment_id partial unique:
--    SELECT indexdef FROM pg_indexes
--    WHERE schemaname = 'commerce' AND indexname = 'payments_provider_payment_id_unique';
--
-- 5. refunds.return_id has no FK yet:
--    SELECT confrelid::regclass FROM pg_constraint
--    WHERE conrelid = 'commerce.refunds'::regclass AND contype = 'f'
--      AND conname LIKE '%return%';
--    -- Expect: 0 rows
--
-- 6. Append-only payment_transactions:
--    SELECT tgname FROM pg_trigger t
--    JOIN pg_class c ON c.oid = t.tgrelid
--    WHERE c.relname = 'payment_transactions' AND NOT t.tgisinternal;
--
-- 7. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000017_commerce_payments';


-- =============================================================================
-- ROLLBACK SQL (manual only — reverse order of creation)
-- =============================================================================
--
-- WARNING: Recreating public.payment_customers requires backfill from commerce.
--
-- DROP TRIGGER IF EXISTS refunds_set_updated_at ON commerce.refunds;
-- DROP TABLE IF EXISTS commerce.refunds;
--
-- DROP TRIGGER IF EXISTS payment_transactions_prevent_delete ON commerce.payment_transactions;
-- DROP TRIGGER IF EXISTS payment_transactions_prevent_update ON commerce.payment_transactions;
-- DROP FUNCTION IF EXISTS commerce.payment_transactions_prevent_mutation();
-- DROP TABLE IF EXISTS commerce.payment_transactions;
--
-- DROP TRIGGER IF EXISTS payments_set_updated_at ON commerce.payments;
-- DROP TABLE IF EXISTS commerce.payments;
--
-- CREATE TABLE public.payment_customers ( ... ); -- restore from 000008
-- INSERT INTO public.payment_customers SELECT * FROM commerce.payment_customers;
-- DROP TRIGGER IF EXISTS payment_customers_set_updated_at ON commerce.payment_customers;
-- DROP TABLE commerce.payment_customers;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000017_commerce_payments';

-- =============================================================================
-- Migration: 000011_create_commerce_and_ops_schemas
-- Chic A Boo — Phase 1 foundation (Architecture v3.0)
-- Depends on: 000010_rename_auth_schema_to_identity
-- Blocks:     000012–000026
--
-- Purpose:
--   Introduces the two bounded-context schemas for transactional e-commerce
--   (commerce) and cross-cutting operational logs (ops). Also creates human-
--   readable BIGINT sequences used by orders, invoices, returns, and shipments
--   in later migrations. No tables, indexes, RLS, or seed data in this step.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Schema: commerce
-- Catalog, inventory, cart, orders, payments, coupons, shipping, returns,
-- and reviews all live in this schema (per ARCHITECTURE_v3.md ADR-001).
-- -----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS commerce;

COMMENT ON SCHEMA commerce IS
    'Transactional e-commerce: catalog, inventory, cart, orders, payments, '
    'promotions, fulfillment, returns, and reviews. Service-owned; minimal RLS.';

-- -----------------------------------------------------------------------------
-- Schema: ops
-- Append-only infrastructure: idempotency keys, inbound webhook events,
-- and notification delivery logs (per ARCHITECTURE_v3.md ADR-001).
-- -----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ops;

COMMENT ON SCHEMA ops IS
    'Operational infrastructure: idempotency, inbound webhooks, notification '
    'delivery audit. Service-owned; RLS disabled.';

-- -----------------------------------------------------------------------------
-- Sequence: commerce.order_number_seq
-- Human-facing order reference starting at 1000001 (display: Order #1000001).
-- Wired as DEFAULT on commerce.orders.order_number in migration 000016.
-- UUID remains the primary join key everywhere.
-- -----------------------------------------------------------------------------
CREATE SEQUENCE commerce.order_number_seq
    START WITH 1000001
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

COMMENT ON SEQUENCE commerce.order_number_seq IS
    'Generates unique order_number BIGINT values for commerce.orders (from 1000001).';

-- -----------------------------------------------------------------------------
-- Sequence: commerce.invoice_number_seq
-- GST invoice numbers for commerce.invoices (migration 000016).
-- -----------------------------------------------------------------------------
CREATE SEQUENCE commerce.invoice_number_seq
    START WITH 100001
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

COMMENT ON SEQUENCE commerce.invoice_number_seq IS
    'Generates unique invoice_number BIGINT values for commerce.invoices (from 100001).';

-- -----------------------------------------------------------------------------
-- Sequence: commerce.return_number_seq
-- Return authorization numbers for commerce.returns (migration 000019).
-- -----------------------------------------------------------------------------
CREATE SEQUENCE commerce.return_number_seq
    START WITH 500001
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

COMMENT ON SEQUENCE commerce.return_number_seq IS
    'Generates unique return_number BIGINT values for commerce.returns (from 500001).';

-- -----------------------------------------------------------------------------
-- Sequence: commerce.shipment_number_seq
-- Shipment reference numbers for commerce.shipments (migration 000018).
-- -----------------------------------------------------------------------------
CREATE SEQUENCE commerce.shipment_number_seq
    START WITH 700001
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

COMMENT ON SEQUENCE commerce.shipment_number_seq IS
    'Generates unique shipment_number BIGINT values for commerce.shipments (from 700001).';


-- =============================================================================
-- VERIFICATION QUERIES (run manually after migrate.py — do not execute here)
-- =============================================================================
--
-- 1. Schemas exist with correct descriptions:
--
--    SELECT n.nspname AS schema_name, d.description
--    FROM pg_namespace n
--    LEFT JOIN pg_description d
--        ON d.objoid = n.oid AND d.classoid = 'pg_namespace'::regclass
--    WHERE n.nspname IN ('commerce', 'ops')
--    ORDER BY n.nspname;
--    -- Expect: 2 rows (commerce, ops)
--
-- 2. Sequences exist in commerce schema with correct start values:
--
--    SELECT sequence_schema, sequence_name, start_value, increment
--    FROM information_schema.sequences
--    WHERE sequence_schema = 'commerce'
--    ORDER BY sequence_name;
--    -- Expect: 4 rows
--    --   invoice_number_seq  start 100001
--    --   order_number_seq    start 1000001
--    --   return_number_seq   start 500001
--    --   shipment_number_seq start 700001
--
-- 3. ops schema is empty (no tables or sequences yet):
--
--    SELECT table_schema, table_name
--    FROM information_schema.tables
--    WHERE table_schema = 'ops';
--    -- Expect: 0 rows
--
-- 4. commerce schema has sequences only (no tables until 000012):
--
--    SELECT c.relname AS object_name, c.relkind
--    FROM pg_class c
--    JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname = 'commerce'
--      AND c.relkind IN ('S', 'r')  -- S = sequence, r = table
--    ORDER BY c.relkind, c.relname;
--    -- Expect: 4 sequences (relkind S), 0 tables (relkind r)
--
-- 5. Migration recorded:
--
--    SELECT version, applied_at
--    FROM public.schema_migrations
--    WHERE version = '000011_create_commerce_and_ops_schemas';
--    -- Expect: 1 row


-- =============================================================================
-- ROLLBACK SQL (run manually only if reversing 000011 — not via migrate.py)
--
-- WARNING: Only safe when commerce and ops contain NO tables or dependent
-- objects from migrations 000012+. If later migrations were applied, roll
-- back those first in reverse order before executing this block.
-- =============================================================================
--
-- DROP SCHEMA IF EXISTS commerce CASCADE;
-- DROP SCHEMA IF EXISTS ops CASCADE;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000011_create_commerce_and_ops_schemas';
--
-- -- Post-rollback verification:
-- -- SELECT nspname FROM pg_namespace WHERE nspname IN ('commerce', 'ops');
-- -- Expect: 0 rows

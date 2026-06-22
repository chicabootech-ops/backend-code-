-- =============================================================================
-- Migration: 000016_commerce_orders
-- Chic A Boo — orders and invoices (Architecture v3.0)
-- Depends on: 000013, 000014, 000015
-- Blocks:     000017, 000018, 000019, 000020
--
-- Creates:
--   commerce.orders
--   commerce.order_items
--   commerce.order_tax_lines
--   commerce.order_status_history
--   commerce.invoices
--
-- Also wires deferred FKs from 000014 and 000015:
--   stock_reservations.order_id / cart_id
--   fulfillment_allocations.order_item_id
--   carts.converted_order_id
--
-- Deferred to 000018:
--   orders.coupon_id → commerce.coupons
-- =============================================================================


-- =============================================================================
-- TABLE: commerce.orders
-- Financial record — immutable (no deleted_at). Cancel via status only.
-- =============================================================================
CREATE TABLE commerce.orders (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  order_number            BIGINT NOT NULL DEFAULT nextval('commerce.order_number_seq'),

  user_id                 UUID
    REFERENCES identity.users (id) ON DELETE SET NULL,

  guest_email             TEXT,

  status                  TEXT NOT NULL DEFAULT 'pending',

  payment_status          TEXT NOT NULL DEFAULT 'pending',

  fulfillment_status      TEXT NOT NULL DEFAULT 'unfulfilled',

  currency                TEXT NOT NULL DEFAULT 'INR',

  subtotal_paise          BIGINT NOT NULL DEFAULT 0,
  discount_paise          BIGINT NOT NULL DEFAULT 0,
  tax_paise               BIGINT NOT NULL DEFAULT 0,
  shipping_paise          BIGINT NOT NULL DEFAULT 0,
  grand_total_paise       BIGINT NOT NULL DEFAULT 0,

  -- Snapshot + FK to coupons added in 000018.
  coupon_id               UUID,
  coupon_code             TEXT,

  shipping_address        JSONB NOT NULL DEFAULT '{}'::jsonb,
  billing_address         JSONB NOT NULL DEFAULT '{}'::jsonb,

  gstin                   TEXT,

  customer_note           TEXT,
  admin_note              TEXT,

  cancelled_at            TIMESTAMPTZ,
  cancellation_reason     TEXT,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT orders_order_number_unique UNIQUE (order_number),

  CONSTRAINT orders_contact_check CHECK (
    user_id IS NOT NULL OR guest_email IS NOT NULL
  ),

  CONSTRAINT orders_status_check CHECK (
    status IN (
      'pending',
      'confirmed',
      'processing',
      'shipped',
      'delivered',
      'completed',
      'cancelled',
      'returned',
      'refunded'
    )
  ),

  CONSTRAINT orders_payment_status_check CHECK (
    payment_status IN (
      'pending',
      'authorized',
      'paid',
      'partially_refunded',
      'refunded',
      'failed'
    )
  ),

  CONSTRAINT orders_fulfillment_status_check CHECK (
    fulfillment_status IN (
      'unfulfilled',
      'partial',
      'fulfilled',
      'cancelled'
    )
  ),

  CONSTRAINT orders_currency_check CHECK (currency = 'INR'),

  CONSTRAINT orders_subtotal_paise_nonneg CHECK (subtotal_paise >= 0),

  CONSTRAINT orders_discount_paise_nonneg CHECK (discount_paise >= 0),

  CONSTRAINT orders_tax_paise_nonneg CHECK (tax_paise >= 0),

  CONSTRAINT orders_shipping_paise_nonneg CHECK (shipping_paise >= 0),

  CONSTRAINT orders_grand_total_paise_nonneg CHECK (grand_total_paise >= 0),

  CONSTRAINT orders_shipping_address_object_check CHECK (
    jsonb_typeof(shipping_address) = 'object'
  ),

  CONSTRAINT orders_billing_address_object_check CHECK (
    jsonb_typeof(billing_address) = 'object'
  )
);

COMMENT ON TABLE commerce.orders IS
  'Order header — immutable financial record. No soft delete; cancel via status.';

COMMENT ON COLUMN commerce.orders.id IS 'UUID primary key (join key everywhere).';
COMMENT ON COLUMN commerce.orders.order_number IS
  'Human-facing BIGINT from commerce.order_number_seq (e.g. Order #1000001).';
COMMENT ON COLUMN commerce.orders.user_id IS 'Registered customer; NULL for guest checkout.';
COMMENT ON COLUMN commerce.orders.guest_email IS 'Guest contact email at checkout.';
COMMENT ON COLUMN commerce.orders.status IS
  'Order lifecycle: pending → confirmed → … → completed; or cancelled/returned/refunded.';
COMMENT ON COLUMN commerce.orders.payment_status IS 'Derived payment state for the order.';
COMMENT ON COLUMN commerce.orders.fulfillment_status IS 'Shipment/fulfillment aggregate state.';
COMMENT ON COLUMN commerce.orders.currency IS 'ISO currency; launch supports INR only.';
COMMENT ON COLUMN commerce.orders.subtotal_paise IS 'Line items subtotal before discount/tax/shipping.';
COMMENT ON COLUMN commerce.orders.discount_paise IS 'Total coupon discount in paise.';
COMMENT ON COLUMN commerce.orders.tax_paise IS 'Total GST in paise.';
COMMENT ON COLUMN commerce.orders.shipping_paise IS 'Shipping charge in paise.';
COMMENT ON COLUMN commerce.orders.grand_total_paise IS 'Amount charged to customer in paise.';
COMMENT ON COLUMN commerce.orders.coupon_id IS 'Coupon applied; FK added in 000018.';
COMMENT ON COLUMN commerce.orders.coupon_code IS 'Coupon code snapshot at order time.';
COMMENT ON COLUMN commerce.orders.shipping_address IS 'JSONB address snapshot at checkout.';
COMMENT ON COLUMN commerce.orders.billing_address IS 'JSONB billing address snapshot.';
COMMENT ON COLUMN commerce.orders.gstin IS 'Customer GSTIN for B2B invoices.';
COMMENT ON COLUMN commerce.orders.customer_note IS 'Note from customer at checkout.';
COMMENT ON COLUMN commerce.orders.admin_note IS 'Internal admin note.';
COMMENT ON COLUMN commerce.orders.cancelled_at IS 'UTC when order was cancelled.';
COMMENT ON COLUMN commerce.orders.cancellation_reason IS 'Reason for cancellation.';
COMMENT ON COLUMN commerce.orders.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.orders.created_at IS 'UTC order placement time.';
COMMENT ON COLUMN commerce.orders.updated_at IS 'UTC last update; trigger-maintained.';

CREATE INDEX orders_user_id_created_at_idx
  ON commerce.orders (user_id, created_at DESC)
  WHERE user_id IS NOT NULL;

COMMENT ON INDEX commerce.orders_user_id_created_at_idx IS
  'Customer order history.';

CREATE INDEX orders_status_idx
  ON commerce.orders (status);

COMMENT ON INDEX commerce.orders_status_idx IS
  'Admin order queue by lifecycle status.';

CREATE INDEX orders_payment_status_idx
  ON commerce.orders (payment_status);

COMMENT ON INDEX commerce.orders_payment_status_idx IS
  'Payment reconciliation queries.';

CREATE INDEX orders_guest_email_idx
  ON commerce.orders (guest_email)
  WHERE guest_email IS NOT NULL;

COMMENT ON INDEX commerce.orders_guest_email_idx IS
  'Guest order lookup by email.';

CREATE INDEX orders_created_at_idx
  ON commerce.orders (created_at DESC);

COMMENT ON INDEX commerce.orders_created_at_idx IS
  'Reporting and recent orders.';

CREATE TRIGGER orders_set_updated_at
  BEFORE UPDATE ON commerce.orders
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER orders_set_updated_at ON commerce.orders IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.order_items
-- Immutable line snapshots — no deleted_at.
-- =============================================================================
CREATE TABLE commerce.order_items (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  order_id                UUID NOT NULL
    REFERENCES commerce.orders (id) ON DELETE RESTRICT,

  product_variant_id      UUID NOT NULL
    REFERENCES commerce.product_variants (id) ON DELETE RESTRICT,

  product_id              UUID NOT NULL
    REFERENCES commerce.products (id) ON DELETE RESTRICT,

  sku                     TEXT NOT NULL,
  product_name            TEXT NOT NULL,
  variant_title           TEXT NOT NULL,

  quantity                INTEGER NOT NULL,

  unit_price_paise        BIGINT NOT NULL,
  discount_paise          BIGINT NOT NULL DEFAULT 0,
  tax_paise               BIGINT NOT NULL DEFAULT 0,
  line_total_paise        BIGINT NOT NULL,

  hsn_code                TEXT,
  tax_rate_bps            INTEGER,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT order_items_quantity_positive CHECK (quantity > 0),

  CONSTRAINT order_items_unit_price_paise_nonneg CHECK (unit_price_paise >= 0),

  CONSTRAINT order_items_discount_paise_nonneg CHECK (discount_paise >= 0),

  CONSTRAINT order_items_tax_paise_nonneg CHECK (tax_paise >= 0),

  CONSTRAINT order_items_line_total_paise_nonneg CHECK (line_total_paise >= 0),

  CONSTRAINT order_items_tax_rate_bps_valid CHECK (
    tax_rate_bps IS NULL OR (tax_rate_bps >= 0 AND tax_rate_bps <= 10000)
  )
);

COMMENT ON TABLE commerce.order_items IS
  'Immutable order line snapshots with prices in paise.';

COMMENT ON COLUMN commerce.order_items.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.order_items.order_id IS 'FK to commerce.orders.';
COMMENT ON COLUMN commerce.order_items.product_variant_id IS
  'FK to sellable SKU at time of order.';
COMMENT ON COLUMN commerce.order_items.product_id IS 'Denormalised product FK for reporting.';
COMMENT ON COLUMN commerce.order_items.sku IS 'SKU snapshot.';
COMMENT ON COLUMN commerce.order_items.product_name IS 'Product title snapshot.';
COMMENT ON COLUMN commerce.order_items.variant_title IS 'Variant label snapshot.';
COMMENT ON COLUMN commerce.order_items.quantity IS 'Units ordered (> 0).';
COMMENT ON COLUMN commerce.order_items.unit_price_paise IS 'Unit selling price in paise.';
COMMENT ON COLUMN commerce.order_items.discount_paise IS 'Line-level discount in paise.';
COMMENT ON COLUMN commerce.order_items.tax_paise IS 'Line-level tax in paise.';
COMMENT ON COLUMN commerce.order_items.line_total_paise IS 'Line total in paise after discount.';
COMMENT ON COLUMN commerce.order_items.hsn_code IS 'HSN code snapshot for GST.';
COMMENT ON COLUMN commerce.order_items.tax_rate_bps IS
  'GST rate in basis points e.g. 1800 = 18%.';
COMMENT ON COLUMN commerce.order_items.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.order_items.created_at IS 'UTC line creation time.';

CREATE INDEX order_items_order_id_idx
  ON commerce.order_items (order_id);

COMMENT ON INDEX commerce.order_items_order_id_idx IS
  'Load lines for an order.';

CREATE INDEX order_items_product_variant_id_idx
  ON commerce.order_items (product_variant_id);

COMMENT ON INDEX commerce.order_items_product_variant_id_idx IS
  'Variant sales history.';

CREATE INDEX order_items_product_id_idx
  ON commerce.order_items (product_id);

COMMENT ON INDEX commerce.order_items_product_id_idx IS
  'Product sales reporting.';


-- =============================================================================
-- TABLE: commerce.order_tax_lines
-- GST breakdown per order (CGST/SGST/IGST/cess).
-- =============================================================================
CREATE TABLE commerce.order_tax_lines (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  order_id                UUID NOT NULL
    REFERENCES commerce.orders (id) ON DELETE RESTRICT,

  tax_type                TEXT NOT NULL,

  tax_rate_bps            INTEGER NOT NULL,

  taxable_amount_paise    BIGINT NOT NULL,

  tax_amount_paise        BIGINT NOT NULL,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT order_tax_lines_tax_type_check CHECK (
    tax_type IN ('cgst', 'sgst', 'igst', 'cess')
  ),

  CONSTRAINT order_tax_lines_tax_rate_bps_valid CHECK (
    tax_rate_bps >= 0 AND tax_rate_bps <= 10000
  ),

  CONSTRAINT order_tax_lines_taxable_amount_paise_nonneg CHECK (
    taxable_amount_paise >= 0
  ),

  CONSTRAINT order_tax_lines_tax_amount_paise_nonneg CHECK (
    tax_amount_paise >= 0
  )
);

COMMENT ON TABLE commerce.order_tax_lines IS
  'GST tax line breakdown for an order; amounts in paise.';

COMMENT ON COLUMN commerce.order_tax_lines.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.order_tax_lines.order_id IS 'FK to commerce.orders.';
COMMENT ON COLUMN commerce.order_tax_lines.tax_type IS 'cgst, sgst, igst, or cess.';
COMMENT ON COLUMN commerce.order_tax_lines.tax_rate_bps IS 'Rate in basis points.';
COMMENT ON COLUMN commerce.order_tax_lines.taxable_amount_paise IS 'Taxable base in paise.';
COMMENT ON COLUMN commerce.order_tax_lines.tax_amount_paise IS 'Tax collected in paise.';
COMMENT ON COLUMN commerce.order_tax_lines.created_at IS 'UTC when tax line was recorded.';

CREATE INDEX order_tax_lines_order_id_idx
  ON commerce.order_tax_lines (order_id);

COMMENT ON INDEX commerce.order_tax_lines_order_id_idx IS
  'All tax lines for an order.';


-- =============================================================================
-- TABLE: commerce.order_status_history
-- Append-only order status audit trail.
-- =============================================================================
CREATE TABLE commerce.order_status_history (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  order_id                UUID NOT NULL
    REFERENCES commerce.orders (id) ON DELETE RESTRICT,

  from_status             TEXT,
  to_status               TEXT NOT NULL,

  changed_by_type         TEXT NOT NULL,

  changed_by_id           UUID,

  reason                  TEXT,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT order_status_history_changed_by_type_check CHECK (
    changed_by_type IN ('system', 'customer', 'admin')
  )
);

COMMENT ON TABLE commerce.order_status_history IS
  'Append-only order status transition log.';

COMMENT ON COLUMN commerce.order_status_history.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.order_status_history.order_id IS 'FK to commerce.orders.';
COMMENT ON COLUMN commerce.order_status_history.from_status IS 'Previous order status; NULL on first row.';
COMMENT ON COLUMN commerce.order_status_history.to_status IS 'New order status.';
COMMENT ON COLUMN commerce.order_status_history.changed_by_type IS 'system, customer, or admin.';
COMMENT ON COLUMN commerce.order_status_history.changed_by_id IS
  'Actor UUID (user_id or admin_id) when applicable.';
COMMENT ON COLUMN commerce.order_status_history.reason IS 'Optional transition reason.';
COMMENT ON COLUMN commerce.order_status_history.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.order_status_history.created_at IS 'UTC transition time.';

CREATE INDEX order_status_history_order_created_idx
  ON commerce.order_status_history (order_id, created_at DESC);

COMMENT ON INDEX commerce.order_status_history_order_created_idx IS
  'Order status timeline newest first.';

CREATE OR REPLACE FUNCTION commerce.order_status_history_prevent_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'commerce.order_status_history is append-only; UPDATE and DELETE are forbidden';
END;
$$;

COMMENT ON FUNCTION commerce.order_status_history_prevent_mutation() IS
  'Blocks UPDATE/DELETE on order_status_history.';

CREATE TRIGGER order_status_history_prevent_update
  BEFORE UPDATE ON commerce.order_status_history
  FOR EACH ROW
  EXECUTE FUNCTION commerce.order_status_history_prevent_mutation();

CREATE TRIGGER order_status_history_prevent_delete
  BEFORE DELETE ON commerce.order_status_history
  FOR EACH ROW
  EXECUTE FUNCTION commerce.order_status_history_prevent_mutation();


-- =============================================================================
-- TABLE: commerce.invoices
-- GST invoice document — one per order.
-- =============================================================================
CREATE TABLE commerce.invoices (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  order_id                UUID NOT NULL
    REFERENCES commerce.orders (id) ON DELETE RESTRICT,

  invoice_number          BIGINT NOT NULL DEFAULT nextval('commerce.invoice_number_seq'),

  pdf_r2_key              TEXT,

  issued_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT invoices_order_id_unique UNIQUE (order_id),

  CONSTRAINT invoices_invoice_number_unique UNIQUE (invoice_number)
);

COMMENT ON TABLE commerce.invoices IS
  'GST invoice record; PDF stored in R2 via pdf_r2_key.';

COMMENT ON COLUMN commerce.invoices.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.invoices.order_id IS 'FK to commerce.orders; one invoice per order.';
COMMENT ON COLUMN commerce.invoices.invoice_number IS
  'Human-facing BIGINT from commerce.invoice_number_seq.';
COMMENT ON COLUMN commerce.invoices.pdf_r2_key IS 'R2 object key for generated PDF.';
COMMENT ON COLUMN commerce.invoices.issued_at IS 'UTC invoice issue time.';
COMMENT ON COLUMN commerce.invoices.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.invoices.created_at IS 'UTC row creation time.';


-- =============================================================================
-- Deferred FKs from 000014 (inventory) and 000015 (carts)
-- =============================================================================
ALTER TABLE commerce.stock_reservations
  ADD CONSTRAINT stock_reservations_order_id_fkey
    FOREIGN KEY (order_id) REFERENCES commerce.orders (id) ON DELETE SET NULL;

COMMENT ON CONSTRAINT stock_reservations_order_id_fkey
  ON commerce.stock_reservations IS
  'Links reservation to order after checkout.';

ALTER TABLE commerce.stock_reservations
  ADD CONSTRAINT stock_reservations_cart_id_fkey
    FOREIGN KEY (cart_id) REFERENCES commerce.carts (id) ON DELETE SET NULL;

COMMENT ON CONSTRAINT stock_reservations_cart_id_fkey
  ON commerce.stock_reservations IS
  'Links reservation to persisted cart.';

ALTER TABLE commerce.fulfillment_allocations
  ADD CONSTRAINT fulfillment_allocations_order_item_id_fkey
    FOREIGN KEY (order_item_id) REFERENCES commerce.order_items (id) ON DELETE RESTRICT;

COMMENT ON CONSTRAINT fulfillment_allocations_order_item_id_fkey
  ON commerce.fulfillment_allocations IS
  'Links warehouse allocation to order line.';

ALTER TABLE commerce.carts
  ADD CONSTRAINT carts_converted_order_id_fkey
    FOREIGN KEY (converted_order_id) REFERENCES commerce.orders (id) ON DELETE SET NULL;

COMMENT ON CONSTRAINT carts_converted_order_id_fkey
  ON commerce.carts IS
  'Order created when cart was converted at checkout.';


-- =============================================================================
-- VERIFICATION QUERIES (run manually after migrate.py — do not execute here)
-- =============================================================================
--
-- 1. Tables exist:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'commerce'
--      AND table_name IN (
--        'orders', 'order_items', 'order_tax_lines',
--        'order_status_history', 'invoices'
--      )
--    ORDER BY table_name;
--
-- 2. orders has no deleted_at:
--    SELECT column_name FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'orders'
--      AND column_name = 'deleted_at';
--    -- Expect: 0 rows
--
-- 3. order_number sequence wired:
--    SELECT column_default FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'orders'
--      AND column_name = 'order_number';
--
-- 4. Deferred FKs from 000014 now present:
--    SELECT conname FROM pg_constraint
--    WHERE conrelid = 'commerce.stock_reservations'::regclass AND contype = 'f'
--      AND conname LIKE '%order_id%';
--
-- 5. orders.coupon_id still without FK (until 000018):
--    SELECT conname FROM pg_constraint
--    WHERE conrelid = 'commerce.orders'::regclass AND contype = 'f';
--    -- Expect: user_id only
--
-- 6. Append-only order_status_history:
--    SELECT tgname FROM pg_trigger t
--    JOIN pg_class c ON c.oid = t.tgrelid
--    WHERE c.relname = 'order_status_history' AND NOT t.tgisinternal;
--
-- 7. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000016_commerce_orders';


-- =============================================================================
-- ROLLBACK SQL (manual only — reverse order of creation)
-- =============================================================================
--
-- ALTER TABLE commerce.carts DROP CONSTRAINT IF EXISTS carts_converted_order_id_fkey;
-- ALTER TABLE commerce.fulfillment_allocations
--   DROP CONSTRAINT IF EXISTS fulfillment_allocations_order_item_id_fkey;
-- ALTER TABLE commerce.stock_reservations DROP CONSTRAINT IF EXISTS stock_reservations_cart_id_fkey;
-- ALTER TABLE commerce.stock_reservations DROP CONSTRAINT IF EXISTS stock_reservations_order_id_fkey;
--
-- DROP TRIGGER IF EXISTS order_status_history_prevent_delete ON commerce.order_status_history;
-- DROP TRIGGER IF EXISTS order_status_history_prevent_update ON commerce.order_status_history;
-- DROP FUNCTION IF EXISTS commerce.order_status_history_prevent_mutation();
-- DROP TABLE IF EXISTS commerce.invoices;
-- DROP TABLE IF EXISTS commerce.order_status_history;
-- DROP TABLE IF EXISTS commerce.order_tax_lines;
-- DROP TABLE IF EXISTS commerce.order_items;
--
-- DROP TRIGGER IF EXISTS orders_set_updated_at ON commerce.orders;
-- DROP TABLE IF EXISTS commerce.orders;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000016_commerce_orders';

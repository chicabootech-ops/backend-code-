-- =============================================================================
-- Migration: 000018_commerce_coupons_and_shipping
-- Chic A Boo — coupons, shipments, tracking (Architecture v3.0)
-- Depends on: 000016_commerce_orders, 000014_commerce_inventory
-- Blocks:     000019, 000025
--
-- Creates:
--   commerce.coupons
--   commerce.coupon_usages
--   commerce.shipments
--   commerce.shipment_items
--   commerce.shipment_tracking_events
--
-- Also wires deferred FKs from 000015 and 000016:
--   carts.coupon_id   → commerce.coupons
--   orders.coupon_id  → commerce.coupons
-- =============================================================================


-- =============================================================================
-- TABLE: commerce.coupons
-- Promotion codes; usage_count removed — coupon_usages is source of truth.
-- =============================================================================
CREATE TABLE commerce.coupons (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  code                        TEXT NOT NULL,
  code_normalized             TEXT NOT NULL,

  description                 TEXT,

  discount_type               TEXT NOT NULL,

  -- percentage: discount_percent (1–100); fixed_amount: discount_value_paise.
  discount_percent            INTEGER,
  discount_value_paise        BIGINT,

  max_discount_paise          BIGINT,
  min_order_amount_paise      BIGINT,

  usage_limit_total           INTEGER,
  usage_limit_per_user        INTEGER NOT NULL DEFAULT 1,

  starts_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at                  TIMESTAMPTZ,

  status                      TEXT NOT NULL DEFAULT 'active',

  applies_to                  TEXT NOT NULL DEFAULT 'all',

  -- Nullable target per applies_to (replaces UUID[] target_ids).
  target_id                   UUID,

  metadata                    JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at                  TIMESTAMPTZ,

  CONSTRAINT coupons_discount_type_check CHECK (
    discount_type IN ('percentage', 'fixed_amount', 'free_shipping')
  ),

  CONSTRAINT coupons_status_check CHECK (
    status IN ('active', 'inactive', 'expired')
  ),

  CONSTRAINT coupons_applies_to_check CHECK (
    applies_to IN ('all', 'category', 'product', 'customer')
  ),

  CONSTRAINT coupons_discount_percent_range CHECK (
    discount_percent IS NULL
    OR (discount_percent > 0 AND discount_percent <= 100)
  ),

  CONSTRAINT coupons_discount_value_paise_nonneg CHECK (
    discount_value_paise IS NULL OR discount_value_paise > 0
  ),

  CONSTRAINT coupons_max_discount_paise_nonneg CHECK (
    max_discount_paise IS NULL OR max_discount_paise > 0
  ),

  CONSTRAINT coupons_min_order_amount_paise_nonneg CHECK (
    min_order_amount_paise IS NULL OR min_order_amount_paise >= 0
  ),

  CONSTRAINT coupons_usage_limit_total_positive CHECK (
    usage_limit_total IS NULL OR usage_limit_total > 0
  ),

  CONSTRAINT coupons_usage_limit_per_user_positive CHECK (
    usage_limit_per_user > 0
  ),

  CONSTRAINT coupons_discount_fields_match_type CHECK (
    (discount_type = 'percentage' AND discount_percent IS NOT NULL)
    OR (discount_type = 'fixed_amount' AND discount_value_paise IS NOT NULL)
    OR (discount_type = 'free_shipping')
  )
);

COMMENT ON TABLE commerce.coupons IS
  'Promotion coupons; no usage_count column — use coupon_usages. Soft-deletable.';

COMMENT ON COLUMN commerce.coupons.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.coupons.code IS 'Display coupon code as entered by customer.';
COMMENT ON COLUMN commerce.coupons.code_normalized IS
  'Lowercase trimmed code for lookups; unique among active coupons.';
COMMENT ON COLUMN commerce.coupons.description IS 'Admin-facing description.';
COMMENT ON COLUMN commerce.coupons.discount_type IS
  'percentage|fixed_amount|free_shipping.';
COMMENT ON COLUMN commerce.coupons.discount_percent IS
  'Percent off for percentage type (1–100).';
COMMENT ON COLUMN commerce.coupons.discount_value_paise IS
  'Fixed discount in paise for fixed_amount type.';
COMMENT ON COLUMN commerce.coupons.max_discount_paise IS
  'Cap on percentage discount in paise.';
COMMENT ON COLUMN commerce.coupons.min_order_amount_paise IS
  'Minimum cart subtotal in paise to apply coupon.';
COMMENT ON COLUMN commerce.coupons.usage_limit_total IS 'Global redemption cap; NULL = unlimited.';
COMMENT ON COLUMN commerce.coupons.usage_limit_per_user IS 'Per-user redemption cap.';
COMMENT ON COLUMN commerce.coupons.starts_at IS 'UTC when coupon becomes valid.';
COMMENT ON COLUMN commerce.coupons.expires_at IS 'UTC expiry; NULL = no expiry.';
COMMENT ON COLUMN commerce.coupons.status IS 'active|inactive|expired.';
COMMENT ON COLUMN commerce.coupons.applies_to IS
  'all|category|product|customer — scopes target_id.';
COMMENT ON COLUMN commerce.coupons.target_id IS
  'Category, product, or user UUID when applies_to is not all.';
COMMENT ON COLUMN commerce.coupons.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.coupons.created_at IS 'UTC row creation time.';
COMMENT ON COLUMN commerce.coupons.updated_at IS 'UTC last update; trigger-maintained.';
COMMENT ON COLUMN commerce.coupons.deleted_at IS 'Soft delete timestamp.';

CREATE UNIQUE INDEX coupons_code_normalized_unique_active
  ON commerce.coupons (code_normalized)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.coupons_code_normalized_unique_active IS
  'Normalized coupon codes unique among non-deleted rows.';

CREATE INDEX coupons_status_idx
  ON commerce.coupons (status)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.coupons_status_idx IS
  'Active coupon admin listing.';

CREATE INDEX coupons_expires_at_idx
  ON commerce.coupons (expires_at)
  WHERE deleted_at IS NULL AND expires_at IS NOT NULL;

COMMENT ON INDEX commerce.coupons_expires_at_idx IS
  'Expire coupons job.';

CREATE INDEX coupons_target_id_idx
  ON commerce.coupons (target_id)
  WHERE target_id IS NOT NULL AND deleted_at IS NULL;

COMMENT ON INDEX commerce.coupons_target_id_idx IS
  'Scoped coupons by target entity.';

CREATE OR REPLACE FUNCTION commerce.coupons_normalise_code()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.code_normalized := lower(trim(NEW.code));
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.coupons_normalise_code() IS
  'BEFORE trigger: sets code_normalized from code.';

CREATE TRIGGER coupons_normalise_code
  BEFORE INSERT OR UPDATE OF code
  ON commerce.coupons
  FOR EACH ROW
  EXECUTE FUNCTION commerce.coupons_normalise_code();

COMMENT ON TRIGGER coupons_normalise_code ON commerce.coupons IS
  'Maintains code_normalized on write.';

CREATE TRIGGER coupons_set_updated_at
  BEFORE UPDATE ON commerce.coupons
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER coupons_set_updated_at ON commerce.coupons IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.coupon_usages
-- Redemption audit — source of truth for coupon usage counts.
-- =============================================================================
CREATE TABLE commerce.coupon_usages (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  coupon_id                   UUID NOT NULL
    REFERENCES commerce.coupons (id) ON DELETE RESTRICT,

  user_id                     UUID
    REFERENCES identity.users (id) ON DELETE SET NULL,

  order_id                    UUID NOT NULL
    REFERENCES commerce.orders (id) ON DELETE RESTRICT,

  discount_applied_paise      BIGINT NOT NULL,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT coupon_usages_coupon_order_unique UNIQUE (coupon_id, order_id),

  CONSTRAINT coupon_usages_discount_applied_paise_nonneg CHECK (
    discount_applied_paise >= 0
  )
);

COMMENT ON TABLE commerce.coupon_usages IS
  'Records each coupon redemption; replaces coupons.usage_count.';

COMMENT ON COLUMN commerce.coupon_usages.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.coupon_usages.coupon_id IS 'FK to commerce.coupons.';
COMMENT ON COLUMN commerce.coupon_usages.user_id IS
  'Redeeming user when registered; NULL for guest.';
COMMENT ON COLUMN commerce.coupon_usages.order_id IS 'FK to commerce.orders.';
COMMENT ON COLUMN commerce.coupon_usages.discount_applied_paise IS
  'Actual discount granted in paise.';
COMMENT ON COLUMN commerce.coupon_usages.created_at IS 'UTC redemption time.';

CREATE INDEX coupon_usages_user_coupon_idx
  ON commerce.coupon_usages (user_id, coupon_id)
  WHERE user_id IS NOT NULL;

COMMENT ON INDEX commerce.coupon_usages_user_coupon_idx IS
  'Enforce per-user usage limits.';

CREATE INDEX coupon_usages_coupon_id_idx
  ON commerce.coupon_usages (coupon_id);

COMMENT ON INDEX commerce.coupon_usages_coupon_id_idx IS
  'Count total redemptions per coupon.';


-- =============================================================================
-- TABLE: commerce.shipments
-- Outbound shipment header — supports split / multi-shipment fulfillment.
-- =============================================================================
CREATE TABLE commerce.shipments (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  shipment_number             BIGINT NOT NULL DEFAULT nextval('commerce.shipment_number_seq'),

  order_id                    UUID NOT NULL
    REFERENCES commerce.orders (id) ON DELETE RESTRICT,

  warehouse_id                UUID
    REFERENCES commerce.warehouses (id) ON DELETE SET NULL,

  status                      TEXT NOT NULL DEFAULT 'pending',

  courier_code                TEXT,
  courier_name                TEXT,

  tracking_number             TEXT,
  tracking_url                TEXT,

  shipped_at                  TIMESTAMPTZ,
  delivered_at                TIMESTAMPTZ,

  metadata                    JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at                  TIMESTAMPTZ,

  CONSTRAINT shipments_shipment_number_unique UNIQUE (shipment_number),

  CONSTRAINT shipments_status_check CHECK (
    status IN (
      'pending',
      'packed',
      'shipped',
      'in_transit',
      'delivered',
      'failed',
      'returned'
    )
  )
);

COMMENT ON TABLE commerce.shipments IS
  'Shipment document for an order; line detail in shipment_items. Soft-deletable.';

COMMENT ON COLUMN commerce.shipments.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.shipments.shipment_number IS
  'Human-facing BIGINT from commerce.shipment_number_seq.';
COMMENT ON COLUMN commerce.shipments.order_id IS 'FK to commerce.orders.';
COMMENT ON COLUMN commerce.shipments.warehouse_id IS
  'Origin warehouse; NULL if not yet allocated.';
COMMENT ON COLUMN commerce.shipments.status IS
  'pending|packed|shipped|in_transit|delivered|failed|returned.';
COMMENT ON COLUMN commerce.shipments.courier_code IS 'Courier partner code.';
COMMENT ON COLUMN commerce.shipments.courier_name IS 'Courier display name.';
COMMENT ON COLUMN commerce.shipments.tracking_number IS 'AWB / tracking number.';
COMMENT ON COLUMN commerce.shipments.tracking_url IS 'Customer tracking URL.';
COMMENT ON COLUMN commerce.shipments.shipped_at IS 'UTC handoff to courier.';
COMMENT ON COLUMN commerce.shipments.delivered_at IS 'UTC delivery confirmation.';
COMMENT ON COLUMN commerce.shipments.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.shipments.created_at IS 'UTC shipment created.';
COMMENT ON COLUMN commerce.shipments.updated_at IS 'UTC last update.';
COMMENT ON COLUMN commerce.shipments.deleted_at IS 'Soft delete timestamp.';

CREATE INDEX shipments_order_id_idx
  ON commerce.shipments (order_id);

COMMENT ON INDEX commerce.shipments_order_id_idx IS
  'All shipments for an order.';

CREATE INDEX shipments_tracking_number_idx
  ON commerce.shipments (tracking_number)
  WHERE tracking_number IS NOT NULL AND deleted_at IS NULL;

COMMENT ON INDEX commerce.shipments_tracking_number_idx IS
  'Lookup shipment by tracking number.';

CREATE INDEX shipments_status_idx
  ON commerce.shipments (status)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.shipments_status_idx IS
  'Fulfillment queue by status.';

CREATE TRIGGER shipments_set_updated_at
  BEFORE UPDATE ON commerce.shipments
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER shipments_set_updated_at ON commerce.shipments IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.shipment_items
-- Line items included in a shipment (split fulfillment).
-- =============================================================================
CREATE TABLE commerce.shipment_items (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  shipment_id                 UUID NOT NULL
    REFERENCES commerce.shipments (id) ON DELETE CASCADE,

  order_item_id               UUID NOT NULL
    REFERENCES commerce.order_items (id) ON DELETE RESTRICT,

  quantity                    INTEGER NOT NULL,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT shipment_items_quantity_positive CHECK (quantity > 0)
);

COMMENT ON TABLE commerce.shipment_items IS
  'Maps order lines to shipments for partial / split fulfillment.';

COMMENT ON COLUMN commerce.shipment_items.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.shipment_items.shipment_id IS 'FK to commerce.shipments.';
COMMENT ON COLUMN commerce.shipment_items.order_item_id IS 'FK to commerce.order_items.';
COMMENT ON COLUMN commerce.shipment_items.quantity IS
  'Units in this shipment (may be < order line quantity).';
COMMENT ON COLUMN commerce.shipment_items.created_at IS 'UTC row creation time.';

CREATE INDEX shipment_items_shipment_id_idx
  ON commerce.shipment_items (shipment_id);

COMMENT ON INDEX commerce.shipment_items_shipment_id_idx IS
  'Items in a shipment.';

CREATE INDEX shipment_items_order_item_id_idx
  ON commerce.shipment_items (order_item_id);

COMMENT ON INDEX commerce.shipment_items_order_item_id_idx IS
  'Which shipments include an order line.';


-- =============================================================================
-- TABLE: commerce.shipment_tracking_events
-- Append-only courier tracking timeline.
-- =============================================================================
CREATE TABLE commerce.shipment_tracking_events (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  shipment_id                 UUID NOT NULL
    REFERENCES commerce.shipments (id) ON DELETE RESTRICT,

  status                      TEXT NOT NULL,

  description                 TEXT,

  location                    TEXT,

  event_at                    TIMESTAMPTZ NOT NULL,

  raw_payload                 JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE commerce.shipment_tracking_events IS
  'Append-only shipment tracking events from courier webhooks.';

COMMENT ON COLUMN commerce.shipment_tracking_events.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.shipment_tracking_events.shipment_id IS
  'FK to commerce.shipments.';
COMMENT ON COLUMN commerce.shipment_tracking_events.status IS 'Courier status code.';
COMMENT ON COLUMN commerce.shipment_tracking_events.description IS 'Human-readable event text.';
COMMENT ON COLUMN commerce.shipment_tracking_events.location IS 'Event location if known.';
COMMENT ON COLUMN commerce.shipment_tracking_events.event_at IS
  'UTC time reported by courier.';
COMMENT ON COLUMN commerce.shipment_tracking_events.raw_payload IS
  'Webhook payload snapshot.';
COMMENT ON COLUMN commerce.shipment_tracking_events.created_at IS
  'UTC when event was ingested.';

CREATE INDEX shipment_tracking_events_shipment_event_idx
  ON commerce.shipment_tracking_events (shipment_id, event_at DESC);

COMMENT ON INDEX commerce.shipment_tracking_events_shipment_event_idx IS
  'Tracking timeline newest first.';

CREATE OR REPLACE FUNCTION commerce.shipment_tracking_events_prevent_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'commerce.shipment_tracking_events is append-only; UPDATE and DELETE are forbidden';
END;
$$;

COMMENT ON FUNCTION commerce.shipment_tracking_events_prevent_mutation() IS
  'Blocks UPDATE/DELETE on shipment_tracking_events.';

CREATE TRIGGER shipment_tracking_events_prevent_update
  BEFORE UPDATE ON commerce.shipment_tracking_events
  FOR EACH ROW
  EXECUTE FUNCTION commerce.shipment_tracking_events_prevent_mutation();

CREATE TRIGGER shipment_tracking_events_prevent_delete
  BEFORE DELETE ON commerce.shipment_tracking_events
  FOR EACH ROW
  EXECUTE FUNCTION commerce.shipment_tracking_events_prevent_mutation();


-- =============================================================================
-- Deferred FKs from 000015 and 000016
-- =============================================================================
ALTER TABLE commerce.carts
  ADD CONSTRAINT carts_coupon_id_fkey
    FOREIGN KEY (coupon_id) REFERENCES commerce.coupons (id) ON DELETE SET NULL;

COMMENT ON CONSTRAINT carts_coupon_id_fkey ON commerce.carts IS
  'Applied coupon on persisted cart.';

ALTER TABLE commerce.orders
  ADD CONSTRAINT orders_coupon_id_fkey
    FOREIGN KEY (coupon_id) REFERENCES commerce.coupons (id) ON DELETE SET NULL;

COMMENT ON CONSTRAINT orders_coupon_id_fkey ON commerce.orders IS
  'Coupon snapshot reference on order.';


-- =============================================================================
-- VERIFICATION QUERIES (run manually after migrate.py — do not execute here)
-- =============================================================================
--
-- 1. Tables exist:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'commerce'
--      AND table_name IN (
--        'coupons', 'coupon_usages', 'shipments',
--        'shipment_items', 'shipment_tracking_events'
--      )
--    ORDER BY table_name;
--
-- 2. coupons has no usage_count column:
--    SELECT column_name FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'coupons'
--      AND column_name = 'usage_count';
--    -- Expect: 0 rows
--
-- 3. code_normalized partial unique:
--    SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'commerce' AND indexname = 'coupons_code_normalized_unique_active';
--
-- 4. Deferred coupon FKs wired:
--    SELECT conname FROM pg_constraint
--    WHERE conrelid = 'commerce.carts'::regclass AND conname = 'carts_coupon_id_fkey';
--
-- 5. shipment_items supports split fulfillment:
--    SELECT column_name FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'shipment_items';
--
-- 6. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000018_commerce_coupons_and_shipping';


-- =============================================================================
-- ROLLBACK SQL (manual only — reverse order of creation)
-- =============================================================================
--
-- ALTER TABLE commerce.orders DROP CONSTRAINT IF EXISTS orders_coupon_id_fkey;
-- ALTER TABLE commerce.carts DROP CONSTRAINT IF EXISTS carts_coupon_id_fkey;
--
-- DROP TRIGGER IF EXISTS shipment_tracking_events_prevent_delete
--   ON commerce.shipment_tracking_events;
-- DROP TRIGGER IF EXISTS shipment_tracking_events_prevent_update
--   ON commerce.shipment_tracking_events;
-- DROP FUNCTION IF EXISTS commerce.shipment_tracking_events_prevent_mutation();
-- DROP TABLE IF EXISTS commerce.shipment_tracking_events;
-- DROP TABLE IF EXISTS commerce.shipment_items;
--
-- DROP TRIGGER IF EXISTS shipments_set_updated_at ON commerce.shipments;
-- DROP TABLE IF EXISTS commerce.shipments;
--
-- DROP TABLE IF EXISTS commerce.coupon_usages;
--
-- DROP TRIGGER IF EXISTS coupons_normalise_code ON commerce.coupons;
-- DROP TRIGGER IF EXISTS coupons_set_updated_at ON commerce.coupons;
-- DROP FUNCTION IF EXISTS commerce.coupons_normalise_code();
-- DROP TABLE IF EXISTS commerce.coupons;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000018_commerce_coupons_and_shipping';

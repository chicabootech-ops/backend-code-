-- =============================================================================
-- Migration: 000015_commerce_cart_and_wishlist
-- Chic A Boo — cart persistence + wishlist (Architecture v3.0)
-- Depends on: 000013_commerce_products, 000013a_product_catalog_constraints
-- Blocks:     000016, 000018, 000025
--
-- Creates:
--   commerce.carts
--   commerce.cart_items
--   commerce.wishlist_items
--
-- Redis-primary cart pattern: DB rows created at checkout or when Redis TTL expires.
--
-- Deferred FKs:
--   carts.coupon_id           → commerce.coupons (000018)
--   carts.converted_order_id    → commerce.orders (000016)
-- =============================================================================


-- =============================================================================
-- TABLE: commerce.carts
-- Persisted cart header for authenticated users and guests.
-- =============================================================================
CREATE TABLE commerce.carts (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  user_id                 UUID
    REFERENCES identity.users (id) ON DELETE SET NULL,

  guest_token             TEXT,

  status                  TEXT NOT NULL DEFAULT 'active',

  currency                TEXT NOT NULL DEFAULT 'INR',

  -- FK added in 000018 after commerce.coupons exists.
  coupon_id               UUID,

  subtotal_paise          BIGINT NOT NULL DEFAULT 0,
  discount_paise          BIGINT NOT NULL DEFAULT 0,

  expires_at              TIMESTAMPTZ,

  -- FK added in 000016 after commerce.orders exists.
  converted_order_id      UUID,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at              TIMESTAMPTZ,

  CONSTRAINT carts_owner_check CHECK (
    user_id IS NOT NULL OR guest_token IS NOT NULL
  ),

  CONSTRAINT carts_status_check CHECK (
    status IN ('active', 'converted', 'abandoned', 'expired')
  ),

  CONSTRAINT carts_currency_check CHECK (currency = 'INR'),

  CONSTRAINT carts_subtotal_paise_nonneg CHECK (subtotal_paise >= 0),

  CONSTRAINT carts_discount_paise_nonneg CHECK (discount_paise >= 0)
);

COMMENT ON TABLE commerce.carts IS
  'Persisted shopping cart; Redis is primary until checkout/TTL flush. Soft-deletable while active.';

COMMENT ON COLUMN commerce.carts.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.carts.user_id IS 'Authenticated owner; NULL for guest carts.';
COMMENT ON COLUMN commerce.carts.guest_token IS 'Hashed guest session identifier.';
COMMENT ON COLUMN commerce.carts.status IS
  'active|converted|abandoned|expired; converted carts are not soft-deleted.';
COMMENT ON COLUMN commerce.carts.currency IS 'ISO currency; launch supports INR only.';
COMMENT ON COLUMN commerce.carts.coupon_id IS 'Applied coupon; FK added in 000018.';
COMMENT ON COLUMN commerce.carts.subtotal_paise IS 'Cart subtotal in INR paise before discount.';
COMMENT ON COLUMN commerce.carts.discount_paise IS 'Coupon discount total in paise.';
COMMENT ON COLUMN commerce.carts.expires_at IS 'Guest cart TTL; NULL = no expiry.';
COMMENT ON COLUMN commerce.carts.converted_order_id IS
  'Order created from this cart; FK added in 000016.';
COMMENT ON COLUMN commerce.carts.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.carts.created_at IS 'UTC row creation time.';
COMMENT ON COLUMN commerce.carts.updated_at IS 'UTC last update; trigger-maintained.';
COMMENT ON COLUMN commerce.carts.deleted_at IS
  'Soft delete for active carts only; NULL on converted carts.';

CREATE INDEX carts_user_id_active_idx
  ON commerce.carts (user_id)
  WHERE user_id IS NOT NULL
    AND status = 'active'
    AND deleted_at IS NULL;

COMMENT ON INDEX commerce.carts_user_id_active_idx IS
  'Lookup active cart for authenticated user.';

CREATE UNIQUE INDEX carts_guest_token_active_unique
  ON commerce.carts (guest_token)
  WHERE guest_token IS NOT NULL
    AND status = 'active'
    AND deleted_at IS NULL;

COMMENT ON INDEX commerce.carts_guest_token_active_unique IS
  'One active guest cart per guest_token.';

CREATE INDEX carts_expires_at_idx
  ON commerce.carts (expires_at)
  WHERE status = 'active'
    AND expires_at IS NOT NULL
    AND deleted_at IS NULL;

COMMENT ON INDEX commerce.carts_expires_at_idx IS
  'Expire abandoned guest carts.';

CREATE INDEX carts_status_idx
  ON commerce.carts (status)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.carts_status_idx IS
  'Filter carts by lifecycle status.';

CREATE TRIGGER carts_set_updated_at
  BEFORE UPDATE ON commerce.carts
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER carts_set_updated_at ON commerce.carts IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.cart_items
-- Line items keyed by product_variant (sellable SKU).
-- =============================================================================
CREATE TABLE commerce.cart_items (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  cart_id                 UUID NOT NULL
    REFERENCES commerce.carts (id) ON DELETE CASCADE,

  product_variant_id      UUID NOT NULL
    REFERENCES commerce.product_variants (id) ON DELETE RESTRICT,

  quantity                INTEGER NOT NULL,

  unit_price_paise        BIGINT NOT NULL,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at              TIMESTAMPTZ,

  CONSTRAINT cart_items_quantity_positive CHECK (quantity > 0),

  CONSTRAINT cart_items_unit_price_paise_nonneg CHECK (unit_price_paise >= 0)
);

COMMENT ON TABLE commerce.cart_items IS
  'Cart line items with price snapshot in paise. Soft-deletable.';

COMMENT ON COLUMN commerce.cart_items.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.cart_items.cart_id IS 'FK to commerce.carts.';
COMMENT ON COLUMN commerce.cart_items.product_variant_id IS
  'FK to sellable SKU (commerce.product_variants).';
COMMENT ON COLUMN commerce.cart_items.quantity IS 'Units in cart (> 0).';
COMMENT ON COLUMN commerce.cart_items.unit_price_paise IS
  'Price snapshot at add-to-cart time in paise.';
COMMENT ON COLUMN commerce.cart_items.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.cart_items.created_at IS 'UTC row creation time.';
COMMENT ON COLUMN commerce.cart_items.updated_at IS 'UTC last update; trigger-maintained.';
COMMENT ON COLUMN commerce.cart_items.deleted_at IS 'Soft delete timestamp.';

CREATE UNIQUE INDEX cart_items_cart_variant_unique_active
  ON commerce.cart_items (cart_id, product_variant_id)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.cart_items_cart_variant_unique_active IS
  'One active line per variant per cart.';

CREATE INDEX cart_items_cart_id_idx
  ON commerce.cart_items (cart_id);

COMMENT ON INDEX commerce.cart_items_cart_id_idx IS
  'Load all items for a cart.';

CREATE INDEX cart_items_product_variant_id_idx
  ON commerce.cart_items (product_variant_id);

COMMENT ON INDEX commerce.cart_items_product_variant_id_idx IS
  'FK index for variant lookups.';

CREATE TRIGGER cart_items_set_updated_at
  BEFORE UPDATE ON commerce.cart_items
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER cart_items_set_updated_at ON commerce.cart_items IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.wishlist_items
-- Simplified wishlist (no multi-list / share tokens at launch).
-- =============================================================================
CREATE TABLE commerce.wishlist_items (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  user_id                 UUID NOT NULL
    REFERENCES identity.users (id) ON DELETE CASCADE,

  product_id              UUID NOT NULL
    REFERENCES commerce.products (id) ON DELETE CASCADE,

  product_variant_id      UUID
    REFERENCES commerce.product_variants (id) ON DELETE SET NULL,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE commerce.wishlist_items IS
  'Per-user saved products; optional variant for size/colour preference.';

COMMENT ON COLUMN commerce.wishlist_items.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.wishlist_items.user_id IS 'FK to identity.users.';
COMMENT ON COLUMN commerce.wishlist_items.product_id IS 'FK to commerce.products.';
COMMENT ON COLUMN commerce.wishlist_items.product_variant_id IS
  'Optional preferred variant; NULL = any variant of product.';
COMMENT ON COLUMN commerce.wishlist_items.created_at IS 'UTC when item was saved.';

CREATE UNIQUE INDEX wishlist_items_user_product_variant_unique
  ON commerce.wishlist_items (user_id, product_id, product_variant_id)
  NULLS NOT DISTINCT;

COMMENT ON INDEX commerce.wishlist_items_user_product_variant_unique IS
  'One wishlist row per user × product × variant (PG15 NULLS NOT DISTINCT).';

CREATE INDEX wishlist_items_user_id_idx
  ON commerce.wishlist_items (user_id);

COMMENT ON INDEX commerce.wishlist_items_user_id_idx IS
  'List wishlist for a user.';

CREATE INDEX wishlist_items_product_id_idx
  ON commerce.wishlist_items (product_id);

COMMENT ON INDEX commerce.wishlist_items_product_id_idx IS
  'Count wishlists per product.';


-- =============================================================================
-- VERIFICATION QUERIES (run manually after migrate.py — do not execute here)
-- =============================================================================
--
-- 1. Tables exist:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'commerce'
--      AND table_name IN ('carts', 'cart_items', 'wishlist_items')
--    ORDER BY table_name;
--
-- 2. carts owner CHECK:
--    SELECT pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'commerce.carts'::regclass AND conname = 'carts_owner_check';
--
-- 3. Deferred FKs (coupon_id, converted_order_id have no FK yet):
--    SELECT conname FROM pg_constraint
--    WHERE conrelid = 'commerce.carts'::regclass AND contype = 'f';
--    -- Expect: user_id only
--
-- 4. cart_items unique partial index:
--    SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'commerce' AND indexname = 'cart_items_cart_variant_unique_active';
--
-- 5. Money in paise on carts:
--    SELECT column_name FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'carts'
--      AND column_name LIKE '%paise%';
--
-- 6. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000015_commerce_cart_and_wishlist';


-- =============================================================================
-- ROLLBACK SQL (manual only — reverse order of creation)
-- =============================================================================
--
-- DROP TABLE IF EXISTS commerce.wishlist_items;
--
-- DROP TRIGGER IF EXISTS cart_items_set_updated_at ON commerce.cart_items;
-- DROP TABLE IF EXISTS commerce.cart_items;
--
-- DROP TRIGGER IF EXISTS carts_set_updated_at ON commerce.carts;
-- DROP TABLE IF EXISTS commerce.carts;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000015_commerce_cart_and_wishlist';

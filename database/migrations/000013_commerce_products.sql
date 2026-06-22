-- =============================================================================
-- Migration: 000013_commerce_products
-- Chic A Boo — product catalog (Architecture v3.0)
-- Depends on: 000012_commerce_media_and_categories, 000012a_category_tree_constraints
-- Blocks:     000014, 000015, 000016, 000018, 000019, 000024
--
-- Creates:
--   commerce.products
--   commerce.product_categories
--   commerce.product_variants
--   commerce.product_images
--   commerce.product_tags
--   commerce.product_tag_mappings
--
-- Deferred to 000024:
--   search_vector GIN, pg_trgm on name/sku, search refresh triggers
-- =============================================================================


-- =============================================================================
-- TABLE: commerce.products
-- Core product record. Price lives on product_variants (BIGINT paise).
-- vendor_id is nullable for future marketplace (no FK until vendors table).
-- =============================================================================
CREATE TABLE commerce.products (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Primary navigation category (denormalised; full list in product_categories).
  primary_category_id     UUID NOT NULL
    REFERENCES commerce.categories (id) ON DELETE RESTRICT,

  -- Future marketplace seller; NULL = first-party Chic A Boo catalog.
  vendor_id               UUID,

  name                    TEXT NOT NULL,
  slug                    TEXT NOT NULL,

  description             TEXT,
  short_description       TEXT,
  brand                   TEXT,

  status                  TEXT NOT NULL DEFAULT 'draft',
  is_featured             BOOLEAN NOT NULL DEFAULT FALSE,
  featured_sort_order     INTEGER,

  -- India GST classification for tax line calculation at checkout.
  tax_class               TEXT NOT NULL DEFAULT 'gst_18',
  hsn_code                TEXT,

  -- Full-text search vector; column only — GIN index deferred to 000024.
  search_vector           TSVECTOR,

  seo_title               TEXT,
  seo_description         TEXT,

  published_at            TIMESTAMPTZ,

  -- ERP / external system sync (products only — per ADR-010).
  external_id             TEXT,
  sync_status             TEXT,
  last_synced_at          TIMESTAMPTZ,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at              TIMESTAMPTZ,

  CONSTRAINT products_status_check CHECK (
    status IN ('draft', 'active', 'inactive')
  ),

  CONSTRAINT products_tax_class_check CHECK (
    tax_class IN ('gst_5', 'gst_12', 'gst_18', 'gst_28', 'exempt')
  ),

  CONSTRAINT products_sync_status_check CHECK (
    sync_status IS NULL
    OR sync_status IN ('pending', 'synced', 'failed')
  ),

  CONSTRAINT products_featured_sort_order_nonneg CHECK (
    featured_sort_order IS NULL OR featured_sort_order >= 0
  )
);

COMMENT ON TABLE commerce.products IS
  'Product catalog parent row. Sellable SKU units are product_variants. Soft-deletable.';

COMMENT ON COLUMN commerce.products.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.products.primary_category_id IS
  'Main category for breadcrumbs/PLP; must also appear in product_categories.';
COMMENT ON COLUMN commerce.products.vendor_id IS
  'Nullable marketplace vendor UUID; FK added when catalog.vendors exists.';
COMMENT ON COLUMN commerce.products.name IS 'Display product title.';
COMMENT ON COLUMN commerce.products.slug IS
  'URL slug; unique among non-deleted products.';
COMMENT ON COLUMN commerce.products.description IS 'Long HTML/markdown product description.';
COMMENT ON COLUMN commerce.products.short_description IS 'Short teaser for cards and SEO.';
COMMENT ON COLUMN commerce.products.brand IS 'Brand name for filters and search.';
COMMENT ON COLUMN commerce.products.status IS
  'draft = admin only; active = storefront; inactive = hidden.';
COMMENT ON COLUMN commerce.products.is_featured IS
  'When true, product may appear in featured modules.';
COMMENT ON COLUMN commerce.products.featured_sort_order IS
  'Optional ordering among featured products (lower first).';
COMMENT ON COLUMN commerce.products.tax_class IS 'GST rate bucket for checkout tax lines.';
COMMENT ON COLUMN commerce.products.hsn_code IS 'Harmonised System Nomenclature code (India GST).';
COMMENT ON COLUMN commerce.products.search_vector IS
  'tsvector for FTS; populated by trigger in migration 000024.';
COMMENT ON COLUMN commerce.products.seo_title IS 'HTML meta title override.';
COMMENT ON COLUMN commerce.products.seo_description IS 'HTML meta description override.';
COMMENT ON COLUMN commerce.products.published_at IS
  'UTC moment the product went active on storefront.';
COMMENT ON COLUMN commerce.products.external_id IS 'ERP/PIM external identifier.';
COMMENT ON COLUMN commerce.products.sync_status IS 'ERP sync state: pending, synced, failed.';
COMMENT ON COLUMN commerce.products.last_synced_at IS 'Last successful ERP sync timestamp.';
COMMENT ON COLUMN commerce.products.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.products.created_at IS 'UTC row creation time.';
COMMENT ON COLUMN commerce.products.updated_at IS 'UTC last update; trigger-maintained.';
COMMENT ON COLUMN commerce.products.deleted_at IS 'Soft delete timestamp; NULL = active.';

COMMENT ON CONSTRAINT products_status_check ON commerce.products IS
  'Allowed product lifecycle states (no PG ENUM).';
COMMENT ON CONSTRAINT products_tax_class_check ON commerce.products IS
  'India GST tax buckets.';
COMMENT ON CONSTRAINT products_sync_status_check ON commerce.products IS
  'ERP sync status values when not NULL.';
COMMENT ON CONSTRAINT products_featured_sort_order_nonneg ON commerce.products IS
  'featured_sort_order cannot be negative.';

CREATE UNIQUE INDEX products_slug_unique_active
  ON commerce.products (slug)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.products_slug_unique_active IS
  'Partial unique: slug unique among non-deleted products.';

CREATE INDEX products_primary_category_id_idx
  ON commerce.products (primary_category_id);

COMMENT ON INDEX commerce.products_primary_category_id_idx IS
  'FK index for category-scoped product listings.';

CREATE INDEX products_status_active_idx
  ON commerce.products (status)
  WHERE status = 'active' AND deleted_at IS NULL;

COMMENT ON INDEX commerce.products_status_active_idx IS
  'Partial index for storefront active product queries.';

CREATE INDEX products_is_featured_idx
  ON commerce.products (featured_sort_order NULLS LAST)
  WHERE is_featured = TRUE AND deleted_at IS NULL AND status = 'active';

COMMENT ON INDEX commerce.products_is_featured_idx IS
  'Supports featured product carousel ordering.';

CREATE INDEX products_vendor_id_idx
  ON commerce.products (vendor_id)
  WHERE vendor_id IS NOT NULL AND deleted_at IS NULL;

COMMENT ON INDEX commerce.products_vendor_id_idx IS
  'Marketplace vendor scoping when vendor_id is set.';

CREATE INDEX products_brand_idx
  ON commerce.products (brand)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.products_brand_idx IS
  'Brand filter on catalog browse.';

CREATE TRIGGER products_set_updated_at
  BEFORE UPDATE ON commerce.products
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER products_set_updated_at ON commerce.products IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.product_categories
-- Many-to-many product ↔ category. is_primary marks the canonical category row.
-- =============================================================================
CREATE TABLE commerce.product_categories (
  product_id      UUID NOT NULL
    REFERENCES commerce.products (id) ON DELETE CASCADE,
  category_id     UUID NOT NULL
    REFERENCES commerce.categories (id) ON DELETE RESTRICT,
  is_primary      BOOLEAN NOT NULL DEFAULT FALSE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  PRIMARY KEY (product_id, category_id)
);

COMMENT ON TABLE commerce.product_categories IS
  'Assigns products to one or more categories; supports multiple category membership.';

COMMENT ON COLUMN commerce.product_categories.product_id IS 'FK to commerce.products.';
COMMENT ON COLUMN commerce.product_categories.category_id IS 'FK to commerce.categories.';
COMMENT ON COLUMN commerce.product_categories.is_primary IS
  'True for the primary category row (should match products.primary_category_id).';
COMMENT ON COLUMN commerce.product_categories.created_at IS 'UTC when mapping was created.';

CREATE INDEX product_categories_category_id_idx
  ON commerce.product_categories (category_id);

COMMENT ON INDEX commerce.product_categories_category_id_idx IS
  'List products in a category.';

CREATE INDEX product_categories_product_id_idx
  ON commerce.product_categories (product_id);

COMMENT ON INDEX commerce.product_categories_product_id_idx IS
  'List categories for a product.';

CREATE UNIQUE INDEX product_categories_one_primary_per_product
  ON commerce.product_categories (product_id)
  WHERE is_primary = TRUE;

COMMENT ON INDEX commerce.product_categories_one_primary_per_product IS
  'At most one primary category mapping per product.';


-- =============================================================================
-- TABLE: commerce.product_variants
-- Sellable SKU unit with price in BIGINT paise (ADR-002).
-- =============================================================================
CREATE TABLE commerce.product_variants (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  product_id              UUID NOT NULL
    REFERENCES commerce.products (id) ON DELETE CASCADE,

  sku                     TEXT NOT NULL,
  barcode                 TEXT,

  title                   TEXT NOT NULL,

  -- Variant options e.g. {"color":"Red","size":"M"} — replaces EAV attribute tables.
  option_values           JSONB NOT NULL DEFAULT '{}'::jsonb,

  price_paise             BIGINT NOT NULL,
  compare_at_price_paise  BIGINT,
  cost_price_paise        BIGINT,

  weight_grams            INTEGER,

  status                  TEXT NOT NULL DEFAULT 'active',

  external_id             TEXT,
  sync_status             TEXT,
  last_synced_at          TIMESTAMPTZ,

  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at              TIMESTAMPTZ,

  CONSTRAINT product_variants_status_check CHECK (
    status IN ('active', 'inactive')
  ),

  CONSTRAINT product_variants_sync_status_check CHECK (
    sync_status IS NULL
    OR sync_status IN ('pending', 'synced', 'failed')
  ),

  CONSTRAINT product_variants_price_paise_nonneg CHECK (price_paise >= 0),

  CONSTRAINT product_variants_compare_at_price_paise_nonneg CHECK (
    compare_at_price_paise IS NULL OR compare_at_price_paise >= 0
  ),

  CONSTRAINT product_variants_cost_price_paise_nonneg CHECK (
    cost_price_paise IS NULL OR cost_price_paise >= 0
  ),

  CONSTRAINT product_variants_weight_grams_nonneg CHECK (
    weight_grams IS NULL OR weight_grams >= 0
  )
);

COMMENT ON TABLE commerce.product_variants IS
  'Purchasable SKU with price in paise. Inventory attaches here in 000014.';

COMMENT ON COLUMN commerce.product_variants.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.product_variants.product_id IS 'Parent product FK.';
COMMENT ON COLUMN commerce.product_variants.sku IS
  'Stock keeping unit; globally unique among active variants.';
COMMENT ON COLUMN commerce.product_variants.barcode IS
  'Optional EAN/UPC; unique when set among active variants.';
COMMENT ON COLUMN commerce.product_variants.title IS
  'Display variant label e.g. Red / M.';
COMMENT ON COLUMN commerce.product_variants.option_values IS
  'JSONB map of option name to value.';
COMMENT ON COLUMN commerce.product_variants.price_paise IS
  'Selling price in INR paise (integer).';
COMMENT ON COLUMN commerce.product_variants.compare_at_price_paise IS
  'Strike-through MRP in paise; optional.';
COMMENT ON COLUMN commerce.product_variants.cost_price_paise IS
  'COGS in paise for margin reporting; optional.';
COMMENT ON COLUMN commerce.product_variants.weight_grams IS
  'Shipping weight; optional.';
COMMENT ON COLUMN commerce.product_variants.status IS 'active or inactive.';
COMMENT ON COLUMN commerce.product_variants.external_id IS 'ERP SKU identifier.';
COMMENT ON COLUMN commerce.product_variants.sync_status IS 'ERP sync state.';
COMMENT ON COLUMN commerce.product_variants.last_synced_at IS 'Last ERP sync time.';
COMMENT ON COLUMN commerce.product_variants.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.product_variants.created_at IS 'UTC creation time.';
COMMENT ON COLUMN commerce.product_variants.updated_at IS 'UTC last update; trigger-maintained.';
COMMENT ON COLUMN commerce.product_variants.deleted_at IS 'Soft delete timestamp.';

COMMENT ON CONSTRAINT product_variants_price_paise_nonneg ON commerce.product_variants IS
  'Price must be non-negative paise.';
COMMENT ON CONSTRAINT product_variants_status_check ON commerce.product_variants IS
  'Variant visibility state.';

CREATE UNIQUE INDEX product_variants_sku_unique_active
  ON commerce.product_variants (sku)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.product_variants_sku_unique_active IS
  'Global SKU uniqueness among non-deleted variants.';

CREATE UNIQUE INDEX product_variants_barcode_unique_active
  ON commerce.product_variants (barcode)
  WHERE deleted_at IS NULL AND barcode IS NOT NULL;

COMMENT ON INDEX commerce.product_variants_barcode_unique_active IS
  'Barcode uniqueness when barcode is set.';

CREATE INDEX product_variants_product_id_idx
  ON commerce.product_variants (product_id);

COMMENT ON INDEX commerce.product_variants_product_id_idx IS
  'List variants for a product.';

CREATE INDEX product_variants_status_active_idx
  ON commerce.product_variants (status)
  WHERE status = 'active' AND deleted_at IS NULL;

COMMENT ON INDEX commerce.product_variants_status_active_idx IS
  'Partial index for sellable variants.';

CREATE TRIGGER product_variants_set_updated_at
  BEFORE UPDATE ON commerce.product_variants
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER product_variants_set_updated_at ON commerce.product_variants IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.product_images
-- Links products (and optionally variants) to commerce.media_assets (R2 keys).
-- =============================================================================
CREATE TABLE commerce.product_images (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  product_id      UUID NOT NULL
    REFERENCES commerce.products (id) ON DELETE CASCADE,

  variant_id      UUID
    REFERENCES commerce.product_variants (id) ON DELETE SET NULL,

  asset_id        UUID NOT NULL
    REFERENCES commerce.media_assets (id) ON DELETE RESTRICT,

  alt_text        TEXT,
  sort_order      INTEGER NOT NULL DEFAULT 0,
  is_primary      BOOLEAN NOT NULL DEFAULT FALSE,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at      TIMESTAMPTZ,

  CONSTRAINT product_images_sort_order_nonneg CHECK (sort_order >= 0)
);

COMMENT ON TABLE commerce.product_images IS
  'Product gallery images referencing media_assets; soft-deletable.';

COMMENT ON COLUMN commerce.product_images.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.product_images.product_id IS 'Parent product FK.';
COMMENT ON COLUMN commerce.product_images.variant_id IS
  'Optional variant-specific image; NULL = applies to whole product.';
COMMENT ON COLUMN commerce.product_images.asset_id IS
  'FK to commerce.media_assets (R2 object registry).';
COMMENT ON COLUMN commerce.product_images.alt_text IS 'Image alt text for accessibility/SEO.';
COMMENT ON COLUMN commerce.product_images.sort_order IS 'Gallery order (lower first).';
COMMENT ON COLUMN commerce.product_images.is_primary IS
  'True for the main product image (at most one per product).';
COMMENT ON COLUMN commerce.product_images.created_at IS 'UTC creation time.';
COMMENT ON COLUMN commerce.product_images.deleted_at IS 'Soft delete timestamp.';

CREATE INDEX product_images_product_id_idx
  ON commerce.product_images (product_id);

COMMENT ON INDEX commerce.product_images_product_id_idx IS
  'Load gallery for a product.';

CREATE INDEX product_images_product_id_sort_idx
  ON commerce.product_images (product_id, sort_order)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.product_images_product_id_sort_idx IS
  'Ordered gallery retrieval.';

CREATE INDEX product_images_variant_id_idx
  ON commerce.product_images (variant_id)
  WHERE variant_id IS NOT NULL;

COMMENT ON INDEX commerce.product_images_variant_id_idx IS
  'Variant-specific images.';

CREATE INDEX product_images_asset_id_idx
  ON commerce.product_images (asset_id);

COMMENT ON INDEX commerce.product_images_asset_id_idx IS
  'FK index for media_assets lookups.';

CREATE UNIQUE INDEX product_images_one_primary_per_product
  ON commerce.product_images (product_id)
  WHERE is_primary = TRUE AND deleted_at IS NULL;

COMMENT ON INDEX commerce.product_images_one_primary_per_product IS
  'At most one primary image per non-deleted product gallery.';


-- =============================================================================
-- TABLE: commerce.product_tags
-- Normalised tag registry for search/filter (Hindi tags etc.).
-- =============================================================================
CREATE TABLE commerce.product_tags (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  name            TEXT NOT NULL,
  slug            TEXT NOT NULL,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE commerce.product_tags IS
  'Reusable product tags; mapped via product_tag_mappings.';

COMMENT ON COLUMN commerce.product_tags.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.product_tags.name IS 'Human-readable tag label.';
COMMENT ON COLUMN commerce.product_tags.slug IS 'URL-safe unique slug.';
COMMENT ON COLUMN commerce.product_tags.created_at IS 'UTC creation time.';

CREATE UNIQUE INDEX product_tags_name_unique
  ON commerce.product_tags (name);

COMMENT ON INDEX commerce.product_tags_name_unique IS
  'Tag names are globally unique.';

CREATE UNIQUE INDEX product_tags_slug_unique
  ON commerce.product_tags (slug);

COMMENT ON INDEX commerce.product_tags_slug_unique IS
  'Tag slugs are globally unique.';


-- =============================================================================
-- TABLE: commerce.product_tag_mappings
-- Many-to-many product ↔ tag.
-- =============================================================================
CREATE TABLE commerce.product_tag_mappings (
  product_id      UUID NOT NULL
    REFERENCES commerce.products (id) ON DELETE CASCADE,
  tag_id          UUID NOT NULL
    REFERENCES commerce.product_tags (id) ON DELETE RESTRICT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  PRIMARY KEY (product_id, tag_id)
);

COMMENT ON TABLE commerce.product_tag_mappings IS
  'Associates tags with products for search and merchandising.';

COMMENT ON COLUMN commerce.product_tag_mappings.product_id IS 'FK to commerce.products.';
COMMENT ON COLUMN commerce.product_tag_mappings.tag_id IS 'FK to commerce.product_tags.';
COMMENT ON COLUMN commerce.product_tag_mappings.created_at IS 'UTC when tag was applied.';

CREATE INDEX product_tag_mappings_tag_id_idx
  ON commerce.product_tag_mappings (tag_id);

COMMENT ON INDEX commerce.product_tag_mappings_tag_id_idx IS
  'Find all products with a given tag.';

CREATE INDEX product_tag_mappings_product_id_idx
  ON commerce.product_tag_mappings (product_id);

COMMENT ON INDEX commerce.product_tag_mappings_product_id_idx IS
  'List tags for a product.';


-- =============================================================================
-- VERIFICATION QUERIES (run manually after migrate.py — do not execute here)
-- =============================================================================
--
-- 1. All six tables exist:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'commerce'
--      AND table_name IN (
--        'products', 'product_categories', 'product_variants',
--        'product_images', 'product_tags', 'product_tag_mappings'
--      )
--    ORDER BY table_name;
--    -- Expect: 6 rows
--
-- 2. products.search_vector column exists without GIN index:
--    SELECT column_name, udt_name FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'products'
--      AND column_name = 'search_vector';
--    SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'commerce' AND tablename = 'products'
--      AND indexdef ILIKE '%search_vector%';
--    -- Expect: column tsvector; 0 GIN indexes
--
-- 3. SKU partial unique index:
--    SELECT indexname, indexdef FROM pg_indexes
--    WHERE schemaname = 'commerce' AND indexname = 'product_variants_sku_unique_active';
--
-- 4. vendor_id nullable, no FK:
--    SELECT column_name, is_nullable FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'products'
--      AND column_name = 'vendor_id';
--    -- Expect: YES
--
-- 5. Price in paise (BIGINT) on variants:
--    SELECT column_name, data_type FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'product_variants'
--      AND column_name LIKE '%paise%';
--
-- 6. Smoke test (requires category + media_asset rows):
--    BEGIN;
--    INSERT INTO commerce.media_assets (r2_key, content_type)
--      VALUES ('products/test/main.webp', 'image/webp') RETURNING id;
--    -- use category id from commerce.categories
--    INSERT INTO commerce.products (primary_category_id, name, slug, status)
--      VALUES ('<category_uuid>', 'Test Saree', 'test-saree', 'draft') RETURNING id;
--    INSERT INTO commerce.product_variants (product_id, sku, title, price_paise)
--      VALUES ('<product_uuid>', 'SKU-TEST-001', 'Default', 199900);
--    ROLLBACK;
--
-- 7. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000013_commerce_products';


-- =============================================================================
-- ROLLBACK SQL (manual only — reverse order of creation)
-- =============================================================================
--
-- DROP TABLE IF EXISTS commerce.product_tag_mappings;
-- DROP TABLE IF EXISTS commerce.product_tags;
-- DROP TABLE IF EXISTS commerce.product_images;
-- DROP TRIGGER IF EXISTS product_variants_set_updated_at ON commerce.product_variants;
-- DROP TABLE IF EXISTS commerce.product_variants;
-- DROP TABLE IF EXISTS commerce.product_categories;
-- DROP TRIGGER IF EXISTS products_set_updated_at ON commerce.products;
-- DROP TABLE IF EXISTS commerce.products;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000013_commerce_products';
--
-- -- Verify:
-- -- SELECT table_name FROM information_schema.tables
-- -- WHERE table_schema = 'commerce' AND table_name LIKE 'product%';

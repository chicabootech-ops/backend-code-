-- =============================================================================
-- Patch: 000013a_product_catalog_constraints (000013A)
-- Chic A Boo — supplements 000013_commerce_products
-- Depends on: 000013_commerce_products
--
-- Fixes / additions from 000013 review:
--   3. Sync products.primary_category_id ↔ product_categories.is_primary
--   6. Add product statuses: archived, discontinued
--   5. Add product_variants.dimensions JSONB (shipping volumetrics)
--   * Normalise product slug on write (consistent with category slugs)
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 6. Expand products.status CHECK: archived, discontinued
-- Keeps existing inactive; maps semantic intent:
--   inactive     = temporarily hidden
--   archived     = delisted, admin-only catalogue record
--   discontinued = no new sales; may remain visible for order history links
-- -----------------------------------------------------------------------------
ALTER TABLE commerce.products
  DROP CONSTRAINT products_status_check;

ALTER TABLE commerce.products
  ADD CONSTRAINT products_status_check CHECK (
    status IN ('draft', 'active', 'inactive', 'archived', 'discontinued')
  );

COMMENT ON CONSTRAINT products_status_check ON commerce.products IS
  'Product lifecycle: draft, active, inactive, archived, discontinued.';

COMMENT ON COLUMN commerce.products.status IS
  'draft=admin; active=storefront; inactive=hidden; archived=delisted; discontinued=no new sales.';

CREATE INDEX products_status_discontinued_idx
  ON commerce.products (status)
  WHERE status = 'discontinued' AND deleted_at IS NULL;

COMMENT ON INDEX commerce.products_status_discontinued_idx IS
  'Admin/catalog filter for discontinued products.';


-- -----------------------------------------------------------------------------
-- 5. Variant dimensions JSONB (length/width/height in mm for volumetric shipping)
-- compare_at_price_paise, cost_price_paise, weight_grams already exist in 000013.
-- -----------------------------------------------------------------------------
ALTER TABLE commerce.product_variants
  ADD COLUMN dimensions JSONB;

COMMENT ON COLUMN commerce.product_variants.dimensions IS
  'Optional package dimensions e.g. {"length_mm":300,"width_mm":200,"height_mm":50}.';

ALTER TABLE commerce.product_variants
  ADD CONSTRAINT product_variants_dimensions_object_check CHECK (
    dimensions IS NULL OR jsonb_typeof(dimensions) = 'object'
  );

COMMENT ON CONSTRAINT product_variants_dimensions_object_check ON commerce.product_variants IS
  'dimensions must be a JSON object when set.';


-- -----------------------------------------------------------------------------
-- Product slug normalisation (align with commerce.categories_validate_tree)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION commerce.products_normalise_slug()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  normalised TEXT;
BEGIN
  normalised := lower(trim(NEW.slug));

  IF normalised !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' THEN
    RAISE EXCEPTION
      'products.slug must be lowercase alphanumeric with hyphens (got %)', NEW.slug;
  END IF;

  NEW.slug := normalised;
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.products_normalise_slug() IS
  'BEFORE trigger: lowercases and validates product slug format.';

CREATE TRIGGER products_normalise_slug
  BEFORE INSERT OR UPDATE OF slug
  ON commerce.products
  FOR EACH ROW
  EXECUTE FUNCTION commerce.products_normalise_slug();

COMMENT ON TRIGGER products_normalise_slug ON commerce.products IS
  'Canonicalises slug before uniqueness check.';


-- -----------------------------------------------------------------------------
-- 3. Primary category consistency: products.primary_category_id ↔ junction
-- Ensures exactly one primary mapping stays aligned with denormalised FK.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION commerce.products_after_insert_primary_mapping()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO commerce.product_categories (product_id, category_id, is_primary)
  VALUES (NEW.id, NEW.primary_category_id, TRUE)
  ON CONFLICT (product_id, category_id)
  DO UPDATE SET is_primary = TRUE;

  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.products_after_insert_primary_mapping() IS
  'AFTER INSERT on products: creates primary product_categories row.';

CREATE TRIGGER products_after_insert_primary_mapping
  AFTER INSERT ON commerce.products
  FOR EACH ROW
  EXECUTE FUNCTION commerce.products_after_insert_primary_mapping();

COMMENT ON TRIGGER products_after_insert_primary_mapping ON commerce.products IS
  'Auto-creates primary category junction row on product insert.';

CREATE OR REPLACE FUNCTION commerce.products_sync_primary_category_from_products()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.primary_category_id IS NOT DISTINCT FROM OLD.primary_category_id THEN
    RETURN NEW;
  END IF;

  -- Demote any existing primary junction rows.
  UPDATE commerce.product_categories
  SET is_primary = FALSE
  WHERE product_id = NEW.id
    AND is_primary = TRUE;

  -- Promote or create the mapping for the new primary category.
  INSERT INTO commerce.product_categories (product_id, category_id, is_primary)
  VALUES (NEW.id, NEW.primary_category_id, TRUE)
  ON CONFLICT (product_id, category_id)
  DO UPDATE SET is_primary = TRUE;

  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.products_sync_primary_category_from_products() IS
  'AFTER UPDATE on products.primary_category_id: syncs junction is_primary flags.';

CREATE TRIGGER products_sync_primary_category_from_products
  AFTER UPDATE OF primary_category_id
  ON commerce.products
  FOR EACH ROW
  EXECUTE FUNCTION commerce.products_sync_primary_category_from_products();

COMMENT ON TRIGGER products_sync_primary_category_from_products ON commerce.products IS
  'Keeps product_categories in sync when primary_category_id changes.';

CREATE OR REPLACE FUNCTION commerce.product_categories_sync_primary_category()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.is_primary THEN
      RAISE EXCEPTION
        'cannot delete primary category mapping for product %; reassign primary_category_id first',
        OLD.product_id;
    END IF;
    RETURN OLD;
  END IF;

  IF NEW.is_primary THEN
    -- Demote sibling primary rows (in case unique index race).
    UPDATE commerce.product_categories
    SET is_primary = FALSE
    WHERE product_id = NEW.product_id
      AND category_id IS DISTINCT FROM NEW.category_id
      AND is_primary = TRUE;

    UPDATE commerce.products
    SET
      primary_category_id = NEW.category_id,
      updated_at = NOW()
    WHERE id = NEW.product_id
      AND primary_category_id IS DISTINCT FROM NEW.category_id;
  END IF;

  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.product_categories_sync_primary_category() IS
  'Syncs products.primary_category_id when junction is_primary is set; blocks primary row delete.';

CREATE TRIGGER product_categories_sync_primary_category
  AFTER INSERT OR UPDATE OF is_primary, category_id
  ON commerce.product_categories
  FOR EACH ROW
  EXECUTE FUNCTION commerce.product_categories_sync_primary_category();

COMMENT ON TRIGGER product_categories_sync_primary_category ON commerce.product_categories IS
  'Propagates is_primary=true to products.primary_category_id.';

CREATE TRIGGER product_categories_protect_primary_delete
  BEFORE DELETE ON commerce.product_categories
  FOR EACH ROW
  EXECUTE FUNCTION commerce.product_categories_sync_primary_category();

COMMENT ON TRIGGER product_categories_protect_primary_delete ON commerce.product_categories IS
  'Prevents deleting the primary category mapping without reassignment.';


-- =============================================================================
-- VERIFICATION QUERIES (manual)
-- =============================================================================
--
-- 1. Status CHECK includes archived, discontinued:
--    SELECT pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'commerce.products'::regclass AND conname = 'products_status_check';
--
-- 2. dimensions column on variants:
--    SELECT column_name FROM information_schema.columns
--    WHERE table_schema='commerce' AND table_name='product_variants'
--      AND column_name='dimensions';
--
-- 3. Primary category sync (BEGIN; ROLLBACK):
--    INSERT INTO commerce.products (primary_category_id, name, slug, status)
--      SELECT id, 'Sync Test', 'sync-test', 'draft' FROM commerce.categories LIMIT 1
--      RETURNING id, primary_category_id;
--    SELECT * FROM commerce.product_categories WHERE product_id = '<id>';
--    -- Expect one row, is_primary = true, category_id = primary_category_id
--
-- 4. Slug normalisation:
--    INSERT INTO commerce.products (primary_category_id, name, slug, status)
--      SELECT id, 'Case Test', 'Upper-Slug', 'draft' FROM commerce.categories LIMIT 1
--      RETURNING slug;
--    -- Expect: upper-slug
--
-- 5. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000013a_product_catalog_constraints';


-- =============================================================================
-- ROLLBACK SQL (manual)
-- =============================================================================
--
-- DROP TRIGGER IF EXISTS product_categories_protect_primary_delete ON commerce.product_categories;
-- DROP TRIGGER IF EXISTS product_categories_sync_primary_category ON commerce.product_categories;
-- DROP TRIGGER IF EXISTS products_sync_primary_category_from_products ON commerce.products;
-- DROP TRIGGER IF EXISTS products_after_insert_primary_mapping ON commerce.products;
-- DROP TRIGGER IF EXISTS products_normalise_slug ON commerce.products;
-- DROP FUNCTION IF EXISTS commerce.product_categories_sync_primary_category();
-- DROP FUNCTION IF EXISTS commerce.products_sync_primary_category_from_products();
-- DROP FUNCTION IF EXISTS commerce.products_after_insert_primary_mapping();
-- DROP FUNCTION IF EXISTS commerce.products_normalise_slug();
-- DROP INDEX IF EXISTS commerce.products_status_discontinued_idx;
-- ALTER TABLE commerce.product_variants DROP CONSTRAINT IF EXISTS product_variants_dimensions_object_check;
-- ALTER TABLE commerce.product_variants DROP COLUMN IF EXISTS dimensions;
-- ALTER TABLE commerce.products DROP CONSTRAINT products_status_check;
-- ALTER TABLE commerce.products ADD CONSTRAINT products_status_check
--   CHECK (status IN ('draft', 'active', 'inactive'));
-- DELETE FROM public.schema_migrations WHERE version = '000013a_product_catalog_constraints';

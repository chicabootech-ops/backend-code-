-- =============================================================================
-- Migration: 000024_search_triggers_and_gin_indexes
-- Chic A Boo — full-text + fuzzy search layer (Architecture v3.0)
-- Depends on: 000012, 000012a, 000013, 000014
-- Blocks:     none (run before 000026 seed)
--
-- Implements:
--   commerce.refresh_product_search_vector()
--   commerce.products_refresh_search_vector() trigger
--   commerce.categories_refresh_search_vector() trigger
--   commerce.product_tag_mappings_refresh_product_search() trigger
--   GIN tsvector indexes on products, categories
--   GIN pg_trgm indexes on products.name, categories.name, product_variants.sku
--   P3 low-stock partial index on commerce.inventory
--
-- Note: category path/depth triggers live in 000012a (not duplicated here).
-- =============================================================================


CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- =============================================================================
-- FUNCTION: commerce.refresh_product_search_vector
-- Rebuilds search_vector for one product (name, brand, descriptions, tags).
-- =============================================================================
CREATE OR REPLACE FUNCTION commerce.refresh_product_search_vector(p_product_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
  v_name              TEXT;
  v_brand             TEXT;
  v_short_description TEXT;
  v_description       TEXT;
  v_tag_text          TEXT;
BEGIN
  SELECT p.name, p.brand, p.short_description, p.description
  INTO v_name, v_brand, v_short_description, v_description
  FROM commerce.products AS p
  WHERE p.id = p_product_id;

  IF NOT FOUND THEN
    RETURN;
  END IF;

  SELECT string_agg(pt.name, ' ')
  INTO v_tag_text
  FROM commerce.product_tag_mappings AS ptm
  JOIN commerce.product_tags AS pt ON pt.id = ptm.tag_id
  WHERE ptm.product_id = p_product_id;

  UPDATE commerce.products AS p
  SET search_vector =
    setweight(to_tsvector('english', COALESCE(v_name, '')), 'A')
    || setweight(to_tsvector('english', COALESCE(v_brand, '')), 'B')
    || setweight(to_tsvector('english', COALESCE(v_short_description, '')), 'C')
    || setweight(to_tsvector('english', COALESCE(v_description, '')), 'D')
    || setweight(to_tsvector('simple', COALESCE(v_tag_text, '')), 'B')
  WHERE p.id = p_product_id;
END;
$$;

COMMENT ON FUNCTION commerce.refresh_product_search_vector(UUID) IS
  'Rebuilds products.search_vector including tag names (Hindi tags use simple config).';


-- =============================================================================
-- TRIGGER: products search_vector on write
-- =============================================================================
CREATE OR REPLACE FUNCTION commerce.products_refresh_search_vector()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  PERFORM commerce.refresh_product_search_vector(NEW.id);
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.products_refresh_search_vector() IS
  'AFTER trigger: refreshes search_vector when product text fields change.';

CREATE TRIGGER products_refresh_search_vector
  AFTER INSERT OR UPDATE OF name, brand, description, short_description
  ON commerce.products
  FOR EACH ROW
  EXECUTE FUNCTION commerce.products_refresh_search_vector();

COMMENT ON TRIGGER products_refresh_search_vector ON commerce.products IS
  'Maintains FTS vector on product catalog writes.';


-- =============================================================================
-- TRIGGER: categories search_vector on write
-- =============================================================================
CREATE OR REPLACE FUNCTION commerce.categories_refresh_search_vector()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.search_vector :=
    setweight(to_tsvector('english', COALESCE(NEW.name, '')), 'A')
    || setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'B');
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.categories_refresh_search_vector() IS
  'BEFORE trigger: sets categories.search_vector from name and description.';

CREATE TRIGGER categories_refresh_search_vector
  BEFORE INSERT OR UPDATE OF name, description
  ON commerce.categories
  FOR EACH ROW
  EXECUTE FUNCTION commerce.categories_refresh_search_vector();

COMMENT ON TRIGGER categories_refresh_search_vector ON commerce.categories IS
  'Maintains FTS vector on category writes.';


-- =============================================================================
-- TRIGGER: re-index parent product when tags change
-- =============================================================================
CREATE OR REPLACE FUNCTION commerce.product_tag_mappings_refresh_product_search()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM commerce.refresh_product_search_vector(OLD.product_id);
    RETURN OLD;
  END IF;

  PERFORM commerce.refresh_product_search_vector(NEW.product_id);
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.product_tag_mappings_refresh_product_search() IS
  'Re-indexes parent product search_vector when tag mappings change.';

CREATE TRIGGER product_tag_mappings_refresh_product_search
  AFTER INSERT OR UPDATE OR DELETE
  ON commerce.product_tag_mappings
  FOR EACH ROW
  EXECUTE FUNCTION commerce.product_tag_mappings_refresh_product_search();

COMMENT ON TRIGGER product_tag_mappings_refresh_product_search
  ON commerce.product_tag_mappings IS
  'Propagates tag changes to parent product FTS vector.';


-- =============================================================================
-- GIN indexes — full-text search
-- =============================================================================
CREATE INDEX products_search_vector_gin_idx
  ON commerce.products
  USING GIN (search_vector);

COMMENT ON INDEX commerce.products_search_vector_gin_idx IS
  'GIN FTS index for product catalog search.';

CREATE INDEX categories_search_vector_gin_idx
  ON commerce.categories
  USING GIN (search_vector);

COMMENT ON INDEX commerce.categories_search_vector_gin_idx IS
  'GIN FTS index for category search.';


-- =============================================================================
-- GIN pg_trgm indexes — fuzzy / autocomplete
-- =============================================================================
CREATE INDEX products_name_trgm_gin_idx
  ON commerce.products
  USING GIN (name gin_trgm_ops)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.products_name_trgm_gin_idx IS
  'Trigram fuzzy match on product names.';

CREATE INDEX categories_name_trgm_gin_idx
  ON commerce.categories
  USING GIN (name gin_trgm_ops)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.categories_name_trgm_gin_idx IS
  'Trigram fuzzy match on category names.';

CREATE INDEX product_variants_sku_trgm_gin_idx
  ON commerce.product_variants
  USING GIN (sku gin_trgm_ops)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.product_variants_sku_trgm_gin_idx IS
  'Trigram fuzzy match on variant SKUs.';


-- =============================================================================
-- P3: low-stock partial index on commerce.inventory
-- =============================================================================
CREATE INDEX inventory_low_stock_idx
  ON commerce.inventory (warehouse_id, product_variant_id)
  WHERE low_stock_threshold IS NOT NULL
    AND (quantity_on_hand - quantity_reserved) <= low_stock_threshold;

COMMENT ON INDEX commerce.inventory_low_stock_idx IS
  'Admin alerts when available stock falls at or below threshold.';


-- =============================================================================
-- Backfill existing rows (fires triggers on categories; explicit on products)
-- =============================================================================
UPDATE commerce.categories
SET name = name
WHERE deleted_at IS NULL;

UPDATE commerce.products
SET name = name
WHERE deleted_at IS NULL;


-- =============================================================================
-- VERIFICATION QUERIES (manual)
-- =============================================================================
--
-- 1. pg_trgm extension:
--    SELECT extname FROM pg_extension WHERE extname = 'pg_trgm';
--
-- 2. GIN indexes exist:
--    SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'commerce'
--      AND indexname IN (
--        'products_search_vector_gin_idx',
--        'categories_search_vector_gin_idx',
--        'products_name_trgm_gin_idx',
--        'categories_name_trgm_gin_idx',
--        'product_variants_sku_trgm_gin_idx',
--        'inventory_low_stock_idx'
--      )
--    ORDER BY indexname;
--
-- 3. Triggers installed:
--    SELECT c.relname, t.tgname
--    FROM pg_trigger t
--    JOIN pg_class c ON c.oid = t.tgrelid
--    JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname = 'commerce'
--      AND t.tgname IN (
--        'products_refresh_search_vector',
--        'categories_refresh_search_vector',
--        'product_tag_mappings_refresh_product_search'
--      )
--      AND NOT t.tgisinternal;
--
-- 4. Category search_vector populated after backfill:
--    SELECT id, name, search_vector IS NOT NULL AS has_vector
--    FROM commerce.categories
--    WHERE deleted_at IS NULL
--    LIMIT 5;
--
-- 5. FTS smoke test (after products exist):
--    SELECT id, name FROM commerce.products
--    WHERE search_vector @@ plainto_tsquery('english', 'saree')
--    LIMIT 5;
--
-- 6. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000024_search_triggers_and_gin_indexes';


-- =============================================================================
-- ROLLBACK SQL (manual)
-- =============================================================================
--
-- DROP INDEX IF EXISTS commerce.inventory_low_stock_idx;
-- DROP INDEX IF EXISTS commerce.product_variants_sku_trgm_gin_idx;
-- DROP INDEX IF EXISTS commerce.categories_name_trgm_gin_idx;
-- DROP INDEX IF EXISTS commerce.products_name_trgm_gin_idx;
-- DROP INDEX IF EXISTS commerce.categories_search_vector_gin_idx;
-- DROP INDEX IF EXISTS commerce.products_search_vector_gin_idx;
--
-- DROP TRIGGER IF EXISTS product_tag_mappings_refresh_product_search
--   ON commerce.product_tag_mappings;
-- DROP TRIGGER IF EXISTS categories_refresh_search_vector ON commerce.categories;
-- DROP TRIGGER IF EXISTS products_refresh_search_vector ON commerce.products;
--
-- DROP FUNCTION IF EXISTS commerce.product_tag_mappings_refresh_product_search();
-- DROP FUNCTION IF EXISTS commerce.categories_refresh_search_vector();
-- DROP FUNCTION IF EXISTS commerce.products_refresh_search_vector();
-- DROP FUNCTION IF EXISTS commerce.refresh_product_search_vector(UUID);
--
-- -- Extension left installed (shared); drop only if no other objects depend:
-- -- DROP EXTENSION IF EXISTS pg_trgm;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000024_search_triggers_and_gin_indexes';

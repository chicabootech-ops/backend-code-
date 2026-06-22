-- =============================================================================
-- Patch: 000012a_category_tree_constraints (000012A)
-- Chic A Boo — category tree integrity (supplements 000012)
-- Depends on: 000012_commerce_media_and_categories
--
-- Adds missing guarantees identified in 000012 review:
--   1. path / depth consistency (trigger-maintained + CHECK invariants)
--   2. circular parent_id prevention (trigger)
--   3. duplicate sibling name prevention (partial unique index)
--   4. sibling slug: already enforced globally by categories_slug_unique_active
--      (000012); no per-parent slug index added (stricter than sibling-only).
--
-- Note: filename uses lowercase "012a" so lexical sort runs AFTER 000012_*.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- FUNCTION: commerce.categories_validate_tree
-- BEFORE INSERT OR UPDATE on commerce.categories.
--   - Rejects self-parent and ancestor cycles
--   - Rejects deleted / missing parent
--   - Computes depth and materialised path from parent + slug
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION commerce.categories_validate_tree()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  ancestor_id   UUID;
  hops          INTEGER := 0;
  max_hops      CONSTANT INTEGER := 64;
  parent_row    commerce.categories%ROWTYPE;
  normalised_slug TEXT;
BEGIN
  -- Normalise slug for path segments (lowercase, trimmed).
  normalised_slug := lower(trim(NEW.slug));

  -- Slug must be URL-safe lowercase segments (matches path segment rules).
  IF normalised_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' THEN
    RAISE EXCEPTION
      'categories.slug must be lowercase alphanumeric with single hyphens (got %)',
      NEW.slug;
  END IF;

  -- Keep slug canonical in the row.
  NEW.slug := normalised_slug;

  -- Cannot be your own parent.
  IF NEW.parent_id IS NOT NULL AND NEW.parent_id = NEW.id THEN
    RAISE EXCEPTION 'category cannot be its own parent (id=%)', NEW.id;
  END IF;

  IF NEW.parent_id IS NULL THEN
    -- Root node: depth 0, path /{slug}
    NEW.depth := 0;
    NEW.path := '/' || normalised_slug;
    RETURN NEW;
  END IF;

  -- Parent must exist and not be soft-deleted.
  SELECT *
  INTO parent_row
  FROM commerce.categories
  WHERE id = NEW.parent_id
    AND deleted_at IS NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION
      'parent category % not found or is soft-deleted', NEW.parent_id;
  END IF;

  -- Walk ancestors to detect cycles (NEW.id must not appear in parent chain).
  ancestor_id := parent_row.parent_id;
  WHILE ancestor_id IS NOT NULL AND hops < max_hops LOOP
    IF ancestor_id = NEW.id THEN
      RAISE EXCEPTION
        'circular category reference: id % is an ancestor of parent %',
        NEW.id, NEW.parent_id;
    END IF;

    SELECT parent_id
    INTO ancestor_id
    FROM commerce.categories
    WHERE id = ancestor_id
      AND deleted_at IS NULL;

    hops := hops + 1;
  END LOOP;

  IF hops >= max_hops THEN
    RAISE EXCEPTION 'category tree exceeds maximum depth of %', max_hops;
  END IF;

  -- Parent path must be set (maintained by this same trigger on parent row).
  IF parent_row.path IS NULL OR parent_row.path = '' THEN
    RAISE EXCEPTION
      'parent category % has invalid path; rebuild tree before adding children',
      NEW.parent_id;
  END IF;

  -- Child depth is exactly parent depth + 1.
  NEW.depth := parent_row.depth + 1;

  -- Materialised path = parent.path + '/' + slug (no duplicate slashes).
  NEW.path := parent_row.path || '/' || normalised_slug;

  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.categories_validate_tree() IS
  'BEFORE trigger: prevents category cycles, validates parent, sets depth and path.';


-- -----------------------------------------------------------------------------
-- FUNCTION: commerce.categories_cascade_path_to_descendants
-- AFTER UPDATE when slug or parent_id changes — recomputes path/depth for all
-- descendants in breadth-first order so path remains consistent down the tree.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION commerce.categories_cascade_path_to_descendants()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  iterations    INTEGER := 0;
  max_iter      CONSTANT INTEGER := 64;
  rows_updated  INTEGER;
BEGIN
  IF TG_OP <> 'UPDATE' THEN
    RETURN NEW;
  END IF;

  IF OLD.slug IS NOT DISTINCT FROM NEW.slug
     AND OLD.parent_id IS NOT DISTINCT FROM NEW.parent_id
     AND OLD.deleted_at IS NOT DISTINCT FROM NEW.deleted_at THEN
    RETURN NEW;
  END IF;

  -- Layer-by-layer: each pass fixes children whose parent already has correct path.
  LOOP
    iterations := iterations + 1;
    IF iterations > max_iter THEN
      RAISE EXCEPTION 'descendant path cascade exceeded % iterations', max_iter;
    END IF;

    UPDATE commerce.categories AS c
    SET
      depth = p.depth + 1,
      path  = p.path || '/' || lower(trim(c.slug)),
      updated_at = NOW()
    FROM commerce.categories AS p
    WHERE c.parent_id = p.id
      AND c.deleted_at IS NULL
      AND p.deleted_at IS NULL
      AND p.path IS NOT NULL
      AND (
        c.path IS DISTINCT FROM (p.path || '/' || lower(trim(c.slug)))
        OR c.depth IS DISTINCT FROM (p.depth + 1)
      );

    GET DIAGNOSTICS rows_updated = ROW_COUNT;
    EXIT WHEN rows_updated = 0;
  END LOOP;

  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION commerce.categories_cascade_path_to_descendants() IS
  'AFTER trigger: propagates path/depth recalculation to all descendants.';


-- -----------------------------------------------------------------------------
-- TRIGGERS on commerce.categories
-- -----------------------------------------------------------------------------
CREATE TRIGGER categories_validate_tree
  BEFORE INSERT OR UPDATE OF parent_id, slug, deleted_at
  ON commerce.categories
  FOR EACH ROW
  EXECUTE FUNCTION commerce.categories_validate_tree();

COMMENT ON TRIGGER categories_validate_tree ON commerce.categories IS
  'Enforces acyclic tree, path/depth consistency, and slug format on write.';

CREATE TRIGGER categories_cascade_path_to_descendants
  AFTER UPDATE OF parent_id, slug, deleted_at
  ON commerce.categories
  FOR EACH ROW
  EXECUTE FUNCTION commerce.categories_cascade_path_to_descendants();

COMMENT ON TRIGGER categories_cascade_path_to_descendants ON commerce.categories IS
  'Rewrites descendant path/depth when a node slug or parent changes.';


-- -----------------------------------------------------------------------------
-- CHECK constraints (static invariants complementing trigger)
-- -----------------------------------------------------------------------------

-- Roots must be depth 0; children must be depth >= 1.
ALTER TABLE commerce.categories
  ADD CONSTRAINT categories_root_depth_check
  CHECK (parent_id IS NOT NULL OR depth = 0);

COMMENT ON CONSTRAINT categories_root_depth_check ON commerce.categories IS
  'Root categories (parent_id NULL) must have depth = 0.';

ALTER TABLE commerce.categories
  ADD CONSTRAINT categories_child_depth_check
  CHECK (parent_id IS NULL OR depth >= 1);

COMMENT ON CONSTRAINT categories_child_depth_check ON commerce.categories IS
  'Non-root categories must have depth >= 1.';

-- When path is set, it must start with / and use slug-safe segments.
ALTER TABLE commerce.categories
  ADD CONSTRAINT categories_path_format_check
  CHECK (
    path IS NULL
    OR path ~ '^(/[a-z0-9]+(?:-[a-z0-9]+)*)+$'
  );

COMMENT ON CONSTRAINT categories_path_format_check ON commerce.categories IS
  'Materialised path uses lowercase hyphenated segments prefixed by /';

-- Root path must equal /{slug} when both are present.
ALTER TABLE commerce.categories
  ADD CONSTRAINT categories_root_path_check
  CHECK (
    parent_id IS NOT NULL
    OR path IS NULL
    OR path = '/' || slug
  );

COMMENT ON CONSTRAINT categories_root_path_check ON commerce.categories IS
  'Root category path must be exactly /{slug}.';


-- -----------------------------------------------------------------------------
-- INDEX: sibling name uniqueness (active rows only)
-- PostgreSQL 15 NULLS NOT DISTINCT — only one root may share a given name.
-- -----------------------------------------------------------------------------
CREATE UNIQUE INDEX categories_sibling_name_unique_active
  ON commerce.categories (
    parent_id,
    lower(trim(name))
  )
  NULLS NOT DISTINCT
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.categories_sibling_name_unique_active IS
  'Prevents duplicate display names among siblings (same parent_id); one name per root.';


-- -----------------------------------------------------------------------------
-- BACKFILL: recompute path/depth for any existing rows (no-op on empty table)
-- Fires categories_validate_tree by touching parent_id/slug via UPDATE.
-- -----------------------------------------------------------------------------
UPDATE commerce.categories AS c
SET
  slug = lower(trim(c.slug)),
  updated_at = NOW()
WHERE c.deleted_at IS NULL;


-- =============================================================================
-- VERIFICATION QUERIES (run manually — do not execute here)
-- =============================================================================
--
-- 1. Triggers installed:
--    SELECT tgname FROM pg_trigger
--    WHERE tgrelid = 'commerce.categories'::regclass AND NOT tgisinternal
--    ORDER BY tgname;
--    -- Expect: categories_cascade_path_to_descendants, categories_set_updated_at,
--    --         categories_validate_tree
--
-- 2. New constraints:
--    SELECT conname FROM pg_constraint
--    WHERE conrelid = 'commerce.categories'::regclass AND contype = 'c'
--    ORDER BY conname;
--    -- Expect: categories_child_depth_check, categories_depth_nonneg,
--    --         categories_path_format_check, categories_root_depth_check,
--    --         categories_root_path_check, categories_sort_order_nonneg,
--    --         categories_status_check
--
-- 3. Sibling name index:
--    SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'commerce' AND indexname = 'categories_sibling_name_unique_active';
--
-- 4. Cycle prevention (should ERROR):
--    BEGIN;
--    INSERT INTO commerce.categories (name, slug) VALUES ('A', 'a-test') RETURNING id; -- save id_a
--    INSERT INTO commerce.categories (name, slug, parent_id) VALUES ('B', 'b-test', id_a) RETURNING id; -- id_b
--    UPDATE commerce.categories SET parent_id = id_b WHERE id = id_a;
--    ROLLBACK;
--
-- 5. Sibling duplicate name (should ERROR on second insert):
--    BEGIN;
--    INSERT INTO commerce.categories (name, slug) VALUES ('Women', 'women-1');
--    INSERT INTO commerce.categories (name, slug) VALUES ('Women', 'women-2');
--    ROLLBACK;
--
-- 6. Path consistency after insert tree:
--    BEGIN;
--    INSERT INTO commerce.categories (name, slug) VALUES ('Women', 'women') RETURNING path, depth; -- /women, 0
--    INSERT INTO commerce.categories (name, slug, parent_id)
--      SELECT 'Ethnic', 'ethnic', id FROM commerce.categories WHERE slug = 'women'
--      RETURNING path, depth; -- /women/ethnic, 1
--    ROLLBACK;
--
-- 7. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000012a_category_tree_constraints';


-- =============================================================================
-- ROLLBACK SQL (manual only)
-- =============================================================================
--
-- DROP TRIGGER IF EXISTS categories_cascade_path_to_descendants ON commerce.categories;
-- DROP TRIGGER IF EXISTS categories_validate_tree ON commerce.categories;
-- DROP FUNCTION IF EXISTS commerce.categories_cascade_path_to_descendants();
-- DROP FUNCTION IF EXISTS commerce.categories_validate_tree();
-- DROP INDEX IF EXISTS commerce.categories_sibling_name_unique_active;
-- ALTER TABLE commerce.categories DROP CONSTRAINT IF EXISTS categories_root_path_check;
-- ALTER TABLE commerce.categories DROP CONSTRAINT IF EXISTS categories_path_format_check;
-- ALTER TABLE commerce.categories DROP CONSTRAINT IF EXISTS categories_child_depth_check;
-- ALTER TABLE commerce.categories DROP CONSTRAINT IF EXISTS categories_root_depth_check;
-- DELETE FROM public.schema_migrations WHERE version = '000012a_category_tree_constraints';

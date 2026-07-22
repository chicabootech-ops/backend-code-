-- =============================================================================
-- Migration: 000031_catalog_section_kind
-- Sections (homepage rails) vs Categories (covers under a section).
-- Products attach only to kind=category rows.
-- Depends on: 000012, 000012a
-- =============================================================================

ALTER TABLE commerce.categories
  ADD COLUMN IF NOT EXISTS kind TEXT;

UPDATE commerce.categories
SET kind = CASE
  WHEN parent_id IS NULL THEN 'section'
  ELSE 'category'
END
WHERE kind IS NULL;

ALTER TABLE commerce.categories
  ALTER COLUMN kind SET DEFAULT 'category';

ALTER TABLE commerce.categories
  ALTER COLUMN kind SET NOT NULL;

ALTER TABLE commerce.categories
  DROP CONSTRAINT IF EXISTS categories_kind_check;

ALTER TABLE commerce.categories
  ADD CONSTRAINT categories_kind_check CHECK (kind IN ('section', 'category'));

COMMENT ON COLUMN commerce.categories.kind IS
  'section = homepage rail (root); category = cover under a section (depth 1).';

-- Extend tree trigger: enforce section/category flat tree for v1
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
  normalised_slug := lower(trim(NEW.slug));

  IF normalised_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' THEN
    RAISE EXCEPTION
      'categories.slug must be lowercase alphanumeric with single hyphens (got %)',
      NEW.slug;
  END IF;

  NEW.slug := normalised_slug;

  IF NEW.parent_id IS NOT NULL AND NEW.parent_id = NEW.id THEN
    RAISE EXCEPTION 'category cannot be its own parent (id=%)', NEW.id;
  END IF;

  IF NEW.parent_id IS NULL THEN
    NEW.depth := 0;
    NEW.path := '/' || normalised_slug;
    NEW.kind := 'section';
    RETURN NEW;
  END IF;

  SELECT *
  INTO parent_row
  FROM commerce.categories
  WHERE id = NEW.parent_id
    AND deleted_at IS NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION
      'parent category % not found or is soft-deleted', NEW.parent_id;
  END IF;

  -- v1: only sections may have children; categories are leaves
  IF parent_row.kind IS DISTINCT FROM 'section' OR parent_row.parent_id IS NOT NULL THEN
    RAISE EXCEPTION
      'categories may only be created under a section (parent % is not a section)',
      NEW.parent_id;
  END IF;

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

  IF parent_row.path IS NULL OR parent_row.path = '' THEN
    RAISE EXCEPTION
      'parent category % has invalid path; rebuild tree before adding children',
      NEW.parent_id;
  END IF;

  NEW.depth := parent_row.depth + 1;
  NEW.path := parent_row.path || '/' || normalised_slug;
  NEW.kind := 'category';

  RETURN NEW;
END;
$$;

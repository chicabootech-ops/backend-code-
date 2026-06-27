-- =============================================================================
-- Migration: 000028_purge_legacy_clothing_categories
-- Permanently removes inactive clothing placeholder rows from commerce.categories.
-- Safe: no products or product_categories reference these IDs.
-- Depends on: 000027_chicaboo_catalog_seed
-- =============================================================================

DELETE FROM commerce.categories
WHERE slug IN ('women', 'men', 'kids', 'accessories', 'ethnic')
  AND deleted_at IS NOT NULL;

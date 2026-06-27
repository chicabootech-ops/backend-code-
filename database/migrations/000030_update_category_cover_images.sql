-- =============================================================================
-- Migration: 000030_update_category_cover_images
-- Sets storefront cover paths on existing Chic A Boo categories (idempotent).
-- Depends on: 000027_chicaboo_catalog_seed
-- =============================================================================

UPDATE commerce.categories
SET image_r2_key = '/collections/tulips.jpeg', updated_at = NOW()
WHERE slug = 'magazines-customisation' AND deleted_at IS NULL;

UPDATE commerce.categories
SET image_r2_key = '/collections/hibiscus-flowers.jpeg', updated_at = NOW()
WHERE slug = 'song-book' AND deleted_at IS NULL;

UPDATE commerce.categories
SET image_r2_key = '/collections/tulip-crochet-bouque.jpeg', updated_at = NOW()
WHERE slug = 'crochet-flowers-bunch' AND deleted_at IS NULL;

UPDATE commerce.categories
SET image_r2_key = '/collections/tulip-crochet-bouque.jpeg', updated_at = NOW()
WHERE slug = 'single-bouquet' AND deleted_at IS NULL;

UPDATE commerce.categories
SET image_r2_key = '/collections/hibiscus-flowers.jpeg', updated_at = NOW()
WHERE slug = 'bunch-flower-bouquet' AND deleted_at IS NULL;

UPDATE commerce.categories
SET image_r2_key = '/collections/customised-travelling.jpeg', updated_at = NOW()
WHERE slug = 'photograph-bouquet' AND deleted_at IS NULL;

UPDATE commerce.categories
SET image_r2_key = '/collections/hibiscus-flowers.jpeg', updated_at = NOW()
WHERE slug = 'customised-premium-hampers' AND deleted_at IS NULL;

UPDATE commerce.categories
SET image_r2_key = '/collections/polaroid-picture-box.jpeg', updated_at = NOW()
WHERE slug = 'polaroid-pictures-box' AND deleted_at IS NULL;

UPDATE commerce.categories
SET image_r2_key = '/collections/crochet-flower-pot.jpeg', updated_at = NOW()
WHERE slug = 'crochet-frames' AND deleted_at IS NULL;

UPDATE commerce.categories
SET image_r2_key = '/collections/key-chains.jpeg', updated_at = NOW()
WHERE slug = 'key-chains-crochet' AND deleted_at IS NULL;

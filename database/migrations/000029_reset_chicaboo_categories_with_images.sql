-- =============================================================================
-- Migration: 000029_reset_chicaboo_categories_with_images
-- Wipes Chic A Boo category tree and re-seeds with storefront cover images.
-- image_r2_key stores public paths (e.g. /collections/tulips.jpeg) until R2 upload.
-- Safe when no products reference categories (dev / fresh catalog).
-- Depends on: 000027_chicaboo_catalog_seed
-- =============================================================================

DELETE FROM commerce.product_categories;
DELETE FROM commerce.products;

DELETE FROM commerce.categories WHERE parent_id IS NOT NULL;
DELETE FROM commerce.categories WHERE parent_id IS NULL;

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description, image_r2_key, metadata)
VALUES
  (
    'Magazines (Customisation)',
    'magazines-customisation',
    NULL,
    0,
    'Personalised magazines for bespoke gifting.',
    '/collections/tulips.jpeg',
    '{"sizes": ["A4", "A5"]}'::jsonb
  ),
  (
    'Song Book',
    'song-book',
    NULL,
    1,
    'Custom song books — a musical gift.',
    '/collections/hibiscus-flowers.jpeg',
    '{}'::jsonb
  ),
  (
    'Crochet Flowers Bunch',
    'crochet-flowers-bunch',
    NULL,
    2,
    'Handmade crochet flower bouquets.',
    '/collections/tulip-crochet-bouque.jpeg',
    '{}'::jsonb
  ),
  (
    'Photograph Bouquet',
    'photograph-bouquet',
    NULL,
    3,
    'Photo memories arranged as a bouquet.',
    '/collections/customised-travelling.jpeg',
    '{}'::jsonb
  ),
  (
    'Customised Premium Hampers',
    'customised-premium-hampers',
    NULL,
    4,
    'Curated premium gift hampers.',
    '/collections/hibiscus-flowers.jpeg',
    '{}'::jsonb
  ),
  (
    'Polaroid Pictures Box',
    'polaroid-pictures-box',
    NULL,
    5,
    'Polaroid photo gift boxes.',
    '/collections/polaroid-picture-box.jpeg',
    '{}'::jsonb
  ),
  (
    'Crochet Frames',
    'crochet-frames',
    NULL,
    6,
    'Crochet photo frames.',
    '/collections/crochet-flower-pot.jpeg',
    '{}'::jsonb
  ),
  (
    'Key Chains (Crochet)',
    'key-chains-crochet',
    NULL,
    7,
    'Handmade crochet key chains.',
    '/collections/key-chains.jpeg',
    '{}'::jsonb
  );

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description, image_r2_key)
SELECT
  'Single Bouquet',
  'single-bouquet',
  c.id,
  0,
  'Single crochet flower bouquet.',
  '/collections/tulip-crochet-bouque.jpeg'
FROM commerce.categories c
WHERE c.slug = 'crochet-flowers-bunch' AND c.deleted_at IS NULL;

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description, image_r2_key)
SELECT
  'Bunch Flower Bouquet',
  'bunch-flower-bouquet',
  c.id,
  1,
  'Full bunch crochet flower bouquet.',
  '/collections/hibiscus-flowers.jpeg'
FROM commerce.categories c
WHERE c.slug = 'crochet-flowers-bunch' AND c.deleted_at IS NULL;

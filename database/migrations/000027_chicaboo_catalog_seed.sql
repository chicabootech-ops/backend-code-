-- =============================================================================
-- Migration: 000027_chicaboo_catalog_seed
-- Chic A Boo — gifting catalog categories (replaces clothing placeholder seed)
--
-- WHERE THIS SHOWS UP:
--   Supabase → Table Editor → schema `commerce` → table `categories`
--   Storefront after GET /api/categories is wired (navbar, shop-by-collection)
--   Admin panel category CRUD
--
-- NOTE: Old Men/Women/Kids/Accessories came from 000026_seed_reference_data.sql
--       (generic e-commerce template). This migration retires that subtree and
--       seeds Chic A Boo bespoke gifting categories only.
--
-- Structure:
--   8 root product categories → sub-types only where needed (crochet flowers)
--   Products are added later under category / sub-category via admin.
-- Depends on: 000026_seed_reference_data
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Seed Chic A Boo gifting categories FIRST (before retiring legacy rows)
-- -----------------------------------------------------------------------------

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description, metadata)
SELECT
  'Magazines (Customisation)',
  'magazines-customisation',
  NULL,
  0,
  'Personalised magazines for bespoke gifting.',
  '{"sizes": ["A4", "A5"]}'::jsonb
WHERE NOT EXISTS (
  SELECT 1 FROM commerce.categories
  WHERE slug = 'magazines-customisation' AND deleted_at IS NULL
);

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description)
SELECT 'Song Book', 'song-book', NULL, 1, 'Custom song books — a musical gift.'
WHERE NOT EXISTS (
  SELECT 1 FROM commerce.categories WHERE slug = 'song-book' AND deleted_at IS NULL
);

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description)
SELECT
  'Crochet Flowers Bunch',
  'crochet-flowers-bunch',
  NULL,
  2,
  'Handmade crochet flower bouquets.'
WHERE NOT EXISTS (
  SELECT 1 FROM commerce.categories
  WHERE slug = 'crochet-flowers-bunch' AND deleted_at IS NULL
);

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description)
SELECT
  'Single Bouquet',
  'single-bouquet',
  c.id,
  0,
  'Single crochet flower bouquet.'
FROM commerce.categories c
WHERE c.slug = 'crochet-flowers-bunch' AND c.deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM commerce.categories
    WHERE slug = 'single-bouquet' AND deleted_at IS NULL
  );

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description)
SELECT
  'Bunch Flower Bouquet',
  'bunch-flower-bouquet',
  c.id,
  1,
  'Full bunch crochet flower bouquet.'
FROM commerce.categories c
WHERE c.slug = 'crochet-flowers-bunch' AND c.deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM commerce.categories
    WHERE slug = 'bunch-flower-bouquet' AND deleted_at IS NULL
  );

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description)
SELECT
  'Photograph Bouquet',
  'photograph-bouquet',
  NULL,
  3,
  'Photo memories arranged as a bouquet.'
WHERE NOT EXISTS (
  SELECT 1 FROM commerce.categories WHERE slug = 'photograph-bouquet' AND deleted_at IS NULL
);

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description)
SELECT
  'Customised Premium Hampers',
  'customised-premium-hampers',
  NULL,
  4,
  'Curated premium gift hampers.'
WHERE NOT EXISTS (
  SELECT 1 FROM commerce.categories
  WHERE slug = 'customised-premium-hampers' AND deleted_at IS NULL
);

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description)
SELECT
  'Polaroid Pictures Box',
  'polaroid-pictures-box',
  NULL,
  5,
  'Polaroid photo gift boxes.'
WHERE NOT EXISTS (
  SELECT 1 FROM commerce.categories WHERE slug = 'polaroid-pictures-box' AND deleted_at IS NULL
);

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description)
SELECT 'Crochet Frames', 'crochet-frames', NULL, 6, 'Crochet photo frames.'
WHERE NOT EXISTS (
  SELECT 1 FROM commerce.categories WHERE slug = 'crochet-frames' AND deleted_at IS NULL
);

INSERT INTO commerce.categories (name, slug, parent_id, sort_order, description)
SELECT
  'Key Chains (Crochet)',
  'key-chains-crochet',
  NULL,
  7,
  'Handmade crochet key chains.'
WHERE NOT EXISTS (
  SELECT 1 FROM commerce.categories WHERE slug = 'key-chains-crochet' AND deleted_at IS NULL
);

-- -----------------------------------------------------------------------------
-- 2. Retire legacy clothing placeholder — children FIRST, then roots
--    (Single UPDATE would fail tree trigger if parent soft-deletes before child)
-- -----------------------------------------------------------------------------

-- 2a. Nested rows under legacy roots (e.g. Ethnic under Women)
UPDATE commerce.categories
SET
  status = 'inactive',
  deleted_at = COALESCE(deleted_at, NOW()),
  updated_at = NOW()
WHERE deleted_at IS NULL
  AND depth >= 1
  AND (
    path LIKE '/women/%'
    OR path LIKE '/men/%'
    OR path LIKE '/kids/%'
    OR path LIKE '/accessories/%'
  );

-- 2b. Legacy root categories
UPDATE commerce.categories
SET
  status = 'inactive',
  deleted_at = COALESCE(deleted_at, NOW()),
  updated_at = NOW()
WHERE deleted_at IS NULL
  AND slug IN ('women', 'men', 'kids', 'accessories');

-- -----------------------------------------------------------------------------
-- 3. Bootstrap super admin (dev/local — password: Chicaboo@Admin2026)
-- -----------------------------------------------------------------------------
INSERT INTO admin.admin_users (email, password_hash, full_name, role_id, status)
SELECT
  'admin@chicaboo.com',
  '$argon2id$v=19$m=65536,t=3,p=4$61zoaZSpyuQqEyc9Jwzs5g$d9HR/LGCREqqDgq7673B6N2eGM8IarP6n5nwMesJbes',
  'Chic A Boo Admin',
  r.id,
  'active'
FROM admin.roles r
WHERE r.name = 'super_admin'
  AND NOT EXISTS (
    SELECT 1 FROM admin.admin_users
    WHERE email = 'admin@chicaboo.com' AND deleted_at IS NULL
  );

-- =============================================================================
-- Migration: 000019_commerce_returns_and_reviews
-- Chic A Boo — returns, reviews (Architecture v3.0)
-- Depends on: 000016_commerce_orders, 000013_commerce_products, 000017_commerce_payments
-- Blocks:     000025
--
-- Creates:
--   commerce.returns
--   commerce.return_items
--   commerce.reviews
--   commerce.review_images
--   commerce.review_votes
--
-- Also wires deferred FK from 000017:
--   refunds.return_id → commerce.returns
-- =============================================================================


-- =============================================================================
-- TABLE: commerce.returns
-- Return authorization (RMA) header.
-- =============================================================================
CREATE TABLE commerce.returns (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  return_number               BIGINT NOT NULL DEFAULT nextval('commerce.return_number_seq'),

  order_id                    UUID NOT NULL
    REFERENCES commerce.orders (id) ON DELETE RESTRICT,

  user_id                     UUID NOT NULL
    REFERENCES identity.users (id) ON DELETE RESTRICT,

  status                      TEXT NOT NULL DEFAULT 'requested',

  reason                      TEXT NOT NULL,

  customer_note               TEXT,
  admin_note                  TEXT,

  refund_amount_paise         BIGINT,

  metadata                    JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at                  TIMESTAMPTZ,

  CONSTRAINT returns_return_number_unique UNIQUE (return_number),

  CONSTRAINT returns_status_check CHECK (
    status IN (
      'requested',
      'approved',
      'rejected',
      'received',
      'refunded',
      'closed'
    )
  ),

  CONSTRAINT returns_refund_amount_paise_nonneg CHECK (
    refund_amount_paise IS NULL OR refund_amount_paise >= 0
  )
);

COMMENT ON TABLE commerce.returns IS
  'Return merchandise authorization (RMA). Soft-deletable.';

COMMENT ON COLUMN commerce.returns.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.returns.return_number IS
  'Human-facing BIGINT from commerce.return_number_seq.';
COMMENT ON COLUMN commerce.returns.order_id IS 'FK to commerce.orders.';
COMMENT ON COLUMN commerce.returns.user_id IS 'Customer requesting return.';
COMMENT ON COLUMN commerce.returns.status IS
  'requested|approved|rejected|received|refunded|closed.';
COMMENT ON COLUMN commerce.returns.reason IS 'Primary return reason code or text.';
COMMENT ON COLUMN commerce.returns.customer_note IS 'Note from customer.';
COMMENT ON COLUMN commerce.returns.admin_note IS 'Internal admin note.';
COMMENT ON COLUMN commerce.returns.refund_amount_paise IS
  'Total refund approved in paise.';
COMMENT ON COLUMN commerce.returns.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.returns.created_at IS 'UTC return requested.';
COMMENT ON COLUMN commerce.returns.updated_at IS 'UTC last status update.';
COMMENT ON COLUMN commerce.returns.deleted_at IS 'Soft delete timestamp.';

CREATE INDEX returns_order_id_idx
  ON commerce.returns (order_id);

COMMENT ON INDEX commerce.returns_order_id_idx IS
  'Returns for an order.';

CREATE INDEX returns_user_id_idx
  ON commerce.returns (user_id);

COMMENT ON INDEX commerce.returns_user_id_idx IS
  'Customer return history.';

CREATE INDEX returns_status_idx
  ON commerce.returns (status)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.returns_status_idx IS
  'Admin return queue by status.';

CREATE TRIGGER returns_set_updated_at
  BEFORE UPDATE ON commerce.returns
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER returns_set_updated_at ON commerce.returns IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.return_items
-- Partial return line items (quantity may be < order line).
-- =============================================================================
CREATE TABLE commerce.return_items (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  return_id                   UUID NOT NULL
    REFERENCES commerce.returns (id) ON DELETE CASCADE,

  order_item_id               UUID NOT NULL
    REFERENCES commerce.order_items (id) ON DELETE RESTRICT,

  quantity                    INTEGER NOT NULL,

  condition                   TEXT NOT NULL DEFAULT 'unopened',

  refund_amount_paise         BIGINT NOT NULL DEFAULT 0,

  restock                     BOOLEAN NOT NULL DEFAULT TRUE,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT return_items_quantity_positive CHECK (quantity > 0),

  CONSTRAINT return_items_condition_check CHECK (
    condition IN ('unopened', 'opened', 'damaged')
  ),

  CONSTRAINT return_items_refund_amount_paise_nonneg CHECK (
    refund_amount_paise >= 0
  )
);

COMMENT ON TABLE commerce.return_items IS
  'Line-level return detail; supports partial quantity returns.';

COMMENT ON COLUMN commerce.return_items.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.return_items.return_id IS 'FK to commerce.returns.';
COMMENT ON COLUMN commerce.return_items.order_item_id IS 'FK to commerce.order_items.';
COMMENT ON COLUMN commerce.return_items.quantity IS 'Units being returned (> 0).';
COMMENT ON COLUMN commerce.return_items.condition IS
  'unopened|opened|damaged — affects restocking.';
COMMENT ON COLUMN commerce.return_items.refund_amount_paise IS
  'Refund for this line in paise.';
COMMENT ON COLUMN commerce.return_items.restock IS
  'When true, inventory restocked on receipt.';
COMMENT ON COLUMN commerce.return_items.created_at IS 'UTC row creation time.';

CREATE INDEX return_items_return_id_idx
  ON commerce.return_items (return_id);

COMMENT ON INDEX commerce.return_items_return_id_idx IS
  'Items in a return.';

CREATE INDEX return_items_order_item_id_idx
  ON commerce.return_items (order_item_id);

COMMENT ON INDEX commerce.return_items_order_item_id_idx IS
  'Returns against an order line.';


-- =============================================================================
-- TABLE: commerce.reviews
-- Product reviews with moderation workflow.
-- =============================================================================
CREATE TABLE commerce.reviews (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  product_id                  UUID NOT NULL
    REFERENCES commerce.products (id) ON DELETE CASCADE,

  product_variant_id          UUID
    REFERENCES commerce.product_variants (id) ON DELETE SET NULL,

  user_id                     UUID NOT NULL
    REFERENCES identity.users (id) ON DELETE CASCADE,

  order_id                    UUID
    REFERENCES commerce.orders (id) ON DELETE SET NULL,

  rating                      INTEGER NOT NULL,

  title                       TEXT,
  body                        TEXT,

  is_verified_purchase        BOOLEAN NOT NULL DEFAULT FALSE,

  status                      TEXT NOT NULL DEFAULT 'pending',

  moderated_by_admin_id       UUID
    REFERENCES admin.admin_users (id) ON DELETE SET NULL,

  moderated_at                TIMESTAMPTZ,

  helpful_count               INTEGER NOT NULL DEFAULT 0,

  metadata                    JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at                  TIMESTAMPTZ,

  CONSTRAINT reviews_rating_range CHECK (rating >= 1 AND rating <= 5),

  CONSTRAINT reviews_status_check CHECK (
    status IN ('pending', 'approved', 'rejected', 'hidden')
  ),

  CONSTRAINT reviews_helpful_count_nonneg CHECK (helpful_count >= 0)
);

COMMENT ON TABLE commerce.reviews IS
  'Customer product reviews with moderation. Soft-deletable.';

COMMENT ON COLUMN commerce.reviews.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.reviews.product_id IS 'FK to commerce.products.';
COMMENT ON COLUMN commerce.reviews.product_variant_id IS
  'Optional variant reviewed; NULL = product-level review.';
COMMENT ON COLUMN commerce.reviews.user_id IS 'Review author.';
COMMENT ON COLUMN commerce.reviews.order_id IS
  'Order proving purchase when is_verified_purchase.';
COMMENT ON COLUMN commerce.reviews.rating IS 'Star rating 1–5.';
COMMENT ON COLUMN commerce.reviews.title IS 'Review headline.';
COMMENT ON COLUMN commerce.reviews.body IS 'Review text.';
COMMENT ON COLUMN commerce.reviews.is_verified_purchase IS
  'True when tied to a completed order.';
COMMENT ON COLUMN commerce.reviews.status IS
  'pending|approved|rejected|hidden.';
COMMENT ON COLUMN commerce.reviews.moderated_by_admin_id IS 'Moderating admin.';
COMMENT ON COLUMN commerce.reviews.moderated_at IS 'UTC moderation decision.';
COMMENT ON COLUMN commerce.reviews.helpful_count IS
  'Denormalised helpful vote count; updated in application transaction.';
COMMENT ON COLUMN commerce.reviews.metadata IS 'JSONB extension point.';
COMMENT ON COLUMN commerce.reviews.created_at IS 'UTC review submitted.';
COMMENT ON COLUMN commerce.reviews.updated_at IS 'UTC last update.';
COMMENT ON COLUMN commerce.reviews.deleted_at IS 'Soft delete timestamp.';

CREATE UNIQUE INDEX reviews_user_product_order_unique_active
  ON commerce.reviews (user_id, product_id, order_id)
  NULLS NOT DISTINCT
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.reviews_user_product_order_unique_active IS
  'One review per user × product × order (PG15 NULLS NOT DISTINCT).';

CREATE INDEX reviews_product_id_approved_idx
  ON commerce.reviews (product_id, created_at DESC)
  WHERE status = 'approved' AND deleted_at IS NULL;

COMMENT ON INDEX commerce.reviews_product_id_approved_idx IS
  'Storefront product reviews.';

CREATE INDEX reviews_rating_idx
  ON commerce.reviews (rating)
  WHERE status = 'approved' AND deleted_at IS NULL;

COMMENT ON INDEX commerce.reviews_rating_idx IS
  'Filter/sort reviews by rating.';

CREATE INDEX reviews_user_id_idx
  ON commerce.reviews (user_id);

COMMENT ON INDEX commerce.reviews_user_id_idx IS
  'Reviews written by a user.';

CREATE TRIGGER reviews_set_updated_at
  BEFORE UPDATE ON commerce.reviews
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

COMMENT ON TRIGGER reviews_set_updated_at ON commerce.reviews IS
  'Sets updated_at = NOW() on every UPDATE.';


-- =============================================================================
-- TABLE: commerce.review_images
-- Review photos referencing commerce.media_assets (R2).
-- =============================================================================
CREATE TABLE commerce.review_images (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  review_id                   UUID NOT NULL
    REFERENCES commerce.reviews (id) ON DELETE CASCADE,

  asset_id                    UUID NOT NULL
    REFERENCES commerce.media_assets (id) ON DELETE RESTRICT,

  sort_order                  INTEGER NOT NULL DEFAULT 0,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at                  TIMESTAMPTZ,

  CONSTRAINT review_images_sort_order_nonneg CHECK (sort_order >= 0)
);

COMMENT ON TABLE commerce.review_images IS
  'Images attached to reviews via media_assets registry.';

COMMENT ON COLUMN commerce.review_images.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.review_images.review_id IS 'FK to commerce.reviews.';
COMMENT ON COLUMN commerce.review_images.asset_id IS
  'FK to commerce.media_assets (R2 object key).';
COMMENT ON COLUMN commerce.review_images.sort_order IS 'Gallery order (lower first).';
COMMENT ON COLUMN commerce.review_images.created_at IS 'UTC upload time.';
COMMENT ON COLUMN commerce.review_images.deleted_at IS 'Soft delete timestamp.';

CREATE INDEX review_images_review_id_idx
  ON commerce.review_images (review_id)
  WHERE deleted_at IS NULL;

COMMENT ON INDEX commerce.review_images_review_id_idx IS
  'Images for a review in sort order.';

CREATE INDEX review_images_asset_id_idx
  ON commerce.review_images (asset_id);

COMMENT ON INDEX commerce.review_images_asset_id_idx IS
  'FK index for media_assets.';


-- =============================================================================
-- TABLE: commerce.review_votes
-- Helpful / not helpful votes per review.
-- =============================================================================
CREATE TABLE commerce.review_votes (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  review_id                   UUID NOT NULL
    REFERENCES commerce.reviews (id) ON DELETE CASCADE,

  user_id                     UUID NOT NULL
    REFERENCES identity.users (id) ON DELETE CASCADE,

  is_helpful                  BOOLEAN NOT NULL,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT review_votes_review_user_unique UNIQUE (review_id, user_id)
);

COMMENT ON TABLE commerce.review_votes IS
  'Per-user helpful votes on reviews.';

COMMENT ON COLUMN commerce.review_votes.id IS 'UUID primary key.';
COMMENT ON COLUMN commerce.review_votes.review_id IS 'FK to commerce.reviews.';
COMMENT ON COLUMN commerce.review_votes.user_id IS 'Voting user.';
COMMENT ON COLUMN commerce.review_votes.is_helpful IS
  'True = helpful; false = not helpful.';
COMMENT ON COLUMN commerce.review_votes.created_at IS 'UTC vote time.';

CREATE INDEX review_votes_review_id_idx
  ON commerce.review_votes (review_id);

COMMENT ON INDEX commerce.review_votes_review_id_idx IS
  'All votes for a review.';


-- =============================================================================
-- Deferred FK from 000017
-- =============================================================================
ALTER TABLE commerce.refunds
  ADD CONSTRAINT refunds_return_id_fkey
    FOREIGN KEY (return_id) REFERENCES commerce.returns (id) ON DELETE SET NULL;

COMMENT ON CONSTRAINT refunds_return_id_fkey ON commerce.refunds IS
  'Links refund to return authorization when applicable.';


-- =============================================================================
-- VERIFICATION QUERIES (run manually after migrate.py — do not execute here)
-- =============================================================================
--
-- 1. Tables exist:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'commerce'
--      AND table_name IN (
--        'returns', 'return_items', 'reviews',
--        'review_images', 'review_votes'
--      )
--    ORDER BY table_name;
--
-- 2. reviews.rating CHECK 1–5:
--    SELECT pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'commerce.reviews'::regclass AND conname = 'reviews_rating_range';
--
-- 3. review_images uses media_assets not URL:
--    SELECT column_name FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'review_images';
--    -- Expect: asset_id, not url
--
-- 4. refunds.return_id FK now present:
--    SELECT conname FROM pg_constraint
--    WHERE conrelid = 'commerce.refunds'::regclass AND conname = 'refunds_return_id_fkey';
--
-- 5. Partial unique on reviews:
--    SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'commerce'
--      AND indexname = 'reviews_user_product_order_unique_active';
--
-- 6. return_number sequence:
--    SELECT column_default FROM information_schema.columns
--    WHERE table_schema = 'commerce' AND table_name = 'returns'
--      AND column_name = 'return_number';
--
-- 7. Migration recorded:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000019_commerce_returns_and_reviews';


-- =============================================================================
-- ROLLBACK SQL (manual only — reverse order of creation)
-- =============================================================================
--
-- ALTER TABLE commerce.refunds DROP CONSTRAINT IF EXISTS refunds_return_id_fkey;
--
-- DROP TABLE IF EXISTS commerce.review_votes;
-- DROP TABLE IF EXISTS commerce.review_images;
--
-- DROP TRIGGER IF EXISTS reviews_set_updated_at ON commerce.reviews;
-- DROP TABLE IF EXISTS commerce.reviews;
--
-- DROP TABLE IF EXISTS commerce.return_items;
--
-- DROP TRIGGER IF EXISTS returns_set_updated_at ON commerce.returns;
-- DROP TABLE IF EXISTS commerce.returns;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000019_commerce_returns_and_reviews';

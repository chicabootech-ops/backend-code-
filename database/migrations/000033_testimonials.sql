-- Chic A Boo customer testimonials shown on the storefront (home + /testimonials).
-- Curated by admins rather than derived from product reviews, so the copy on the
-- marketing surfaces stays under editorial control.
CREATE TABLE commerce.testimonials (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Display name of the customer, e.g. "Ananya S."
  author_name   TEXT NOT NULL,

  -- Optional context line under the name, e.g. "Delhi" or "Rakhi hamper".
  author_role   TEXT,

  -- R2 object key (or /public path) for the customer's photo.
  avatar_r2_key TEXT,

  -- The testimonial body.
  quote         TEXT NOT NULL,

  -- 1..5 stars; NULL hides the rating row.
  rating        SMALLINT,

  -- Optional product this testimonial is about; nulled if the product goes away.
  product_id    UUID REFERENCES commerce.products (id) ON DELETE SET NULL,

  -- Pinned testimonials lead the homepage rail.
  is_featured   BOOLEAN NOT NULL DEFAULT FALSE,

  status        TEXT NOT NULL DEFAULT 'published',
  sort_order    INTEGER NOT NULL DEFAULT 0,

  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at    TIMESTAMPTZ,

  CONSTRAINT testimonials_author_name_not_blank CHECK (btrim(author_name) <> ''),
  CONSTRAINT testimonials_quote_not_blank CHECK (btrim(quote) <> ''),
  CONSTRAINT testimonials_rating_range CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
  CONSTRAINT testimonials_status_check CHECK (status IN ('published', 'hidden')),
  CONSTRAINT testimonials_sort_order_nonneg CHECK (sort_order >= 0)
);

-- Storefront reads: published rows in curator order.
CREATE INDEX testimonials_published_idx
  ON commerce.testimonials (sort_order, created_at DESC)
  WHERE status = 'published' AND deleted_at IS NULL;

CREATE INDEX testimonials_product_idx
  ON commerce.testimonials (product_id)
  WHERE deleted_at IS NULL;

CREATE TRIGGER testimonials_set_updated_at
  BEFORE UPDATE ON commerce.testimonials
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

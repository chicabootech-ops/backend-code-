-- Custom bouquet builder.
--
-- Admins publish the flower types, colours and wraps they can actually make;
-- customers combine them into a bouquet. The chosen combination is priced
-- server-side and snapshotted onto the order item, so a past order still reads
-- correctly after an option is renamed, repriced or retired.
CREATE TABLE commerce.bouquet_options (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Which dropdown this option belongs to.
  kind              TEXT NOT NULL,

  name              TEXT NOT NULL,
  slug              TEXT NOT NULL,
  description       TEXT,

  -- Colour swatch shown in the picker, e.g. '#e3c6c6'. Colours only.
  hex_code          TEXT,

  -- Optional photo (R2 key or /public path).
  image_r2_key      TEXT,

  -- Added to the per-stem price when this option is chosen. May be 0.
  price_delta_paise BIGINT NOT NULL DEFAULT 0,

  status            TEXT NOT NULL DEFAULT 'active',
  sort_order        INTEGER NOT NULL DEFAULT 0,

  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at        TIMESTAMPTZ,

  CONSTRAINT bouquet_options_kind_check
    CHECK (kind IN ('flower', 'color', 'wrap')),
  CONSTRAINT bouquet_options_status_check
    CHECK (status IN ('active', 'inactive')),
  CONSTRAINT bouquet_options_name_not_blank CHECK (btrim(name) <> ''),
  CONSTRAINT bouquet_options_price_delta_nonneg CHECK (price_delta_paise >= 0),
  CONSTRAINT bouquet_options_sort_order_nonneg CHECK (sort_order >= 0),
  -- Colours must carry a swatch; other kinds must not pretend to.
  CONSTRAINT bouquet_options_hex_format
    CHECK (hex_code IS NULL OR hex_code ~* '^#[0-9a-f]{6}$')
);

-- Slugs are unique per kind, ignoring soft-deleted rows.
CREATE UNIQUE INDEX bouquet_options_kind_slug_key
  ON commerce.bouquet_options (kind, slug)
  WHERE deleted_at IS NULL;

CREATE INDEX bouquet_options_published_idx
  ON commerce.bouquet_options (kind, sort_order, name)
  WHERE status = 'active' AND deleted_at IS NULL;

CREATE TRIGGER bouquet_options_set_updated_at
  BEFORE UPDATE ON commerce.bouquet_options
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();


-- Starter options so the builder is usable the moment it ships. Admins edit or
-- delete these from the panel like any other content.
INSERT INTO commerce.bouquet_options (kind, name, slug, hex_code, price_delta_paise, sort_order)
VALUES
  ('flower', 'Tulip',     'tulip',     NULL,  0,     0),
  ('flower', 'Rose',      'rose',      NULL,  2000,  1),
  ('flower', 'Hibiscus',  'hibiscus',  NULL,  1500,  2),
  ('flower', 'Daisy',     'daisy',     NULL,  1000,  3),
  ('flower', 'Sunflower', 'sunflower', NULL,  2500,  4),
  ('color',  'Blush Pink',  'blush-pink',  '#e3c6c6', 0, 0),
  ('color',  'Ivory',       'ivory',       '#f5efeb', 0, 1),
  ('color',  'Butter Yellow','butter-yellow','#f2dfa0', 0, 2),
  ('color',  'Scarlet Red', 'scarlet-red', '#b23a3a', 0, 3),
  ('color',  'Lavender',    'lavender',    '#c9b6dd', 0, 4),
  ('color',  'Sky Blue',    'sky-blue',    '#a8c6de', 0, 5),
  ('wrap',   'Kraft Paper',   'kraft-paper',   '#c8a97e', 0,    0),
  ('wrap',   'Ivory Tissue',  'ivory-tissue',  '#f5efeb', 5000, 1),
  ('wrap',   'Satin Ribbon Box', 'satin-ribbon-box', '#d8b4c4', 15000, 2);

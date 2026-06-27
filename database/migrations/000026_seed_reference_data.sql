-- =============================================================================
-- Migration: 000026_seed_reference_data
-- Chic A Boo — Phase 1 reference seed (Architecture v3.0)
-- Depends on: 000007, 000012, 000014, 000018, 000024, 000025
-- Blocks:     none — Phase 1 schema complete
--
-- Creates:
--   admin.system_settings       — key/value system configuration
--   admin.notification_types    — notification template registry
--
-- Seeds (idempotent):
--   default warehouse
--   root categories
--   RBAC permissions + role mappings
--   notification types
--   system configuration
--
-- Does NOT seed: users, products, orders, payments, demo coupons
-- Optional dev coupon WELCOME10: see SEED_DEV_COUPON note at end
-- =============================================================================


-- =============================================================================
-- TABLE: admin.system_settings
-- Central system configuration store (settings.read / settings.write).
-- =============================================================================
CREATE TABLE admin.system_settings (
  key                     TEXT PRIMARY KEY,

  value                   JSONB NOT NULL DEFAULT '{}'::jsonb,

  description             TEXT,

  is_public               BOOLEAN NOT NULL DEFAULT FALSE,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT system_settings_value_object_check CHECK (
    jsonb_typeof(value) = 'object'
  )
);

COMMENT ON TABLE admin.system_settings IS
  'Key-value system configuration; is_public=true exposed to storefront API.';

COMMENT ON COLUMN admin.system_settings.key IS 'Dot-namespaced config key.';
COMMENT ON COLUMN admin.system_settings.value IS 'JSON configuration payload.';
COMMENT ON COLUMN admin.system_settings.description IS 'Admin UI description.';
COMMENT ON COLUMN admin.system_settings.is_public IS
  'When true, readable by unauthenticated storefront bootstrap.';
COMMENT ON COLUMN admin.system_settings.created_at IS 'UTC row creation time.';
COMMENT ON COLUMN admin.system_settings.updated_at IS 'UTC last update.';

CREATE TRIGGER system_settings_set_updated_at
  BEFORE UPDATE ON admin.system_settings
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();


-- =============================================================================
-- TABLE: admin.notification_types
-- Registry of outbound notification templates (ops.notification_logs.template).
-- =============================================================================
CREATE TABLE admin.notification_types (
  code                    TEXT PRIMARY KEY,

  channel                 TEXT NOT NULL,

  template                TEXT NOT NULL,

  description             TEXT NOT NULL,

  is_transactional        BOOLEAN NOT NULL DEFAULT TRUE,

  is_active               BOOLEAN NOT NULL DEFAULT TRUE,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT notification_types_channel_check CHECK (
    channel IN ('email', 'sms', 'push')
  )
);

COMMENT ON TABLE admin.notification_types IS
  'Notification template registry for Resend/SMS/push at launch.';

COMMENT ON COLUMN admin.notification_types.code IS
  'Stable code e.g. order-confirmed.';
COMMENT ON COLUMN admin.notification_types.channel IS 'email|sms|push.';
COMMENT ON COLUMN admin.notification_types.template IS
  'Provider template identifier / Resend template slug.';
COMMENT ON COLUMN admin.notification_types.description IS 'Human-readable purpose.';
COMMENT ON COLUMN admin.notification_types.is_transactional IS
  'True = transactional (not marketing).';
COMMENT ON COLUMN admin.notification_types.is_active IS
  'When false, notification is disabled.';


-- =============================================================================
-- SEED: default warehouse
-- =============================================================================
INSERT INTO commerce.warehouses (
  code, name, address, is_active, is_default, priority
)
SELECT
  'WH-DEFAULT-01',
  'Chic A Boo Default Warehouse',
  '{"city": "Mumbai", "state": "Maharashtra", "country": "IN"}'::jsonb,
  TRUE,
  TRUE,
  0
WHERE NOT EXISTS (
  SELECT 1 FROM commerce.warehouses
  WHERE code = 'WH-DEFAULT-01' AND deleted_at IS NULL
);


-- =============================================================================
-- SEED: root categories
-- Gifting catalog categories are seeded in 000027_chicaboo_catalog_seed.sql
-- (Do NOT add clothing/fashion placeholders here.)
-- =============================================================================


-- =============================================================================
-- SEED: RBAC permissions (additive — skip duplicates)
-- =============================================================================
INSERT INTO admin.permissions (code, description) VALUES
  ('fulfillment.read',   'View shipments and fulfillment allocations'),
  ('fulfillment.write',  'Create and update shipments'),
  ('returns.read',       'View return requests'),
  ('returns.write',      'Approve and process returns'),
  ('reviews.moderate',   'Moderate customer reviews'),
  ('commerce.read',      'View commerce operational data')
ON CONFLICT (code) DO NOTHING;

-- super_admin — all new permissions
INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles AS r
CROSS JOIN admin.permissions AS p
WHERE r.name = 'super_admin'
  AND p.code IN (
    'fulfillment.read', 'fulfillment.write',
    'returns.read', 'returns.write',
    'reviews.moderate', 'commerce.read'
  )
ON CONFLICT DO NOTHING;

INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles AS r
JOIN admin.permissions AS p ON p.code IN (
  'fulfillment.read', 'returns.read', 'returns.write', 'orders.refund'
)
WHERE r.name = 'admin'
ON CONFLICT DO NOTHING;

INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles AS r
JOIN admin.permissions AS p ON p.code IN (
  'fulfillment.read', 'fulfillment.write',
  'inventory.read', 'inventory.write'
)
WHERE r.name = 'inventory_manager'
ON CONFLICT DO NOTHING;

INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles AS r
JOIN admin.permissions AS p ON p.code IN (
  'returns.read', 'orders.refund', 'orders.read', 'orders.write'
)
WHERE r.name = 'customer_support'
ON CONFLICT DO NOTHING;

INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles AS r
JOIN admin.permissions AS p ON p.code IN (
  'coupons.read', 'coupons.write', 'reviews.moderate'
)
WHERE r.name = 'marketing_manager'
ON CONFLICT DO NOTHING;


-- =============================================================================
-- SEED: notification types
-- =============================================================================
INSERT INTO admin.notification_types (code, channel, template, description, is_transactional)
VALUES
  ('verify-email',      'email', 'verify-email',      'Email address verification OTP',        TRUE),
  ('welcome',           'email', 'welcome',           'Welcome email after registration',      TRUE),
  ('order-confirmed',   'email', 'order-confirmed',   'Order placement confirmation',          TRUE),
  ('order-shipped',     'email', 'order-shipped',     'Shipment dispatched with tracking',     TRUE),
  ('order-delivered',   'email', 'order-delivered',   'Delivery confirmation',                 TRUE),
  ('password-reset',    'email', 'password-reset',    'Password reset link',                   TRUE),
  ('return-approved',   'email', 'return-approved',   'Return request approved',               TRUE),
  ('return-rejected',   'email', 'return-rejected',   'Return request rejected',               TRUE),
  ('refund-processed',  'email', 'refund-processed',  'Refund credited notification',          TRUE),
  ('payment-failed',    'email', 'payment-failed',    'Payment failure at checkout',           TRUE),
  ('marketing-promo',   'email', 'marketing-promo',   'Promotional campaign email',            FALSE)
ON CONFLICT (code) DO NOTHING;


-- =============================================================================
-- SEED: system configuration
-- =============================================================================
INSERT INTO admin.system_settings (key, value, description, is_public)
VALUES
  (
    'store.name',
    '{"value": "Chic A Boo"}'::jsonb,
    'Store display name',
    TRUE
  ),
  (
    'store.currency',
    '{"value": "INR", "symbol": "₹"}'::jsonb,
    'Default storefront currency',
    TRUE
  ),
  (
    'store.country',
    '{"value": "IN", "name": "India"}'::jsonb,
    'Primary operating country',
    TRUE
  ),
  (
    'shipping.default_warehouse_code',
    '{"value": "WH-DEFAULT-01"}'::jsonb,
    'Default fulfillment warehouse code',
    FALSE
  ),
  (
    'gst.default_tax_class',
    '{"value": "gst_18", "rate_bps": 1800}'::jsonb,
    'Default GST tax class for new products',
    FALSE
  ),
  (
    'checkout.guest_cart_ttl_hours',
    '{"value": 72}'::jsonb,
    'Guest cart Redis/DB TTL in hours',
    FALSE
  ),
  (
    'checkout.currency',
    '{"value": "INR"}'::jsonb,
    'Checkout currency (paise storage)',
    TRUE
  ),
  (
    'notifications.default_provider',
    '{"email": "resend"}'::jsonb,
    'Default notification delivery providers',
    FALSE
  ),
  (
    'payments.default_provider',
    '{"value": "razorpay"}'::jsonb,
    'Default payment gateway',
    FALSE
  ),
  (
    'search.locale_configs',
    '{"product": "english", "tags": "simple"}'::jsonb,
    'FTS dictionary config per field group',
    FALSE
  )
ON CONFLICT (key) DO NOTHING;


-- =============================================================================
-- OPTIONAL dev-only coupon (uncomment for local/docker with SEED_DEV_COUPON=true)
-- Not executed by default — production must not seed promotional coupons.
-- =============================================================================
--
-- INSERT INTO commerce.coupons (
--   code, discount_type, discount_percent,
--   min_order_amount_paise, usage_limit_per_user, status, starts_at
-- )
-- SELECT
--   'WELCOME10', 'percentage', 10,
--   50000, 1, 'active', NOW()
-- WHERE NOT EXISTS (
--   SELECT 1 FROM commerce.coupons
--   WHERE code_normalized = 'welcome10' AND deleted_at IS NULL
-- );


-- =============================================================================
-- VERIFICATION QUERIES (manual)
-- =============================================================================
--
-- 1. Default warehouse:
--    SELECT code, name, is_default FROM commerce.warehouses
--    WHERE code = 'WH-DEFAULT-01' AND deleted_at IS NULL;
--
-- 2. Root categories (4):
--    SELECT name, slug, path, search_vector IS NOT NULL AS indexed
--    FROM commerce.categories
--    WHERE parent_id IS NULL AND deleted_at IS NULL
--    ORDER BY sort_order;
--
-- 3. Notification types:
--    SELECT code, channel, is_transactional FROM admin.notification_types
--    ORDER BY code;
--
-- 4. System settings:
--    SELECT key, is_public FROM admin.system_settings ORDER BY key;
--
-- 5. New permissions mapped:
--    SELECT r.name, p.code
--    FROM admin.role_permissions rp
--    JOIN admin.roles r ON r.id = rp.role_id
--    JOIN admin.permissions p ON p.id = rp.permission_id
--    WHERE p.code IN ('fulfillment.read', 'returns.read', 'reviews.moderate')
--    ORDER BY r.name, p.code;
--
-- 6. No demo products/orders:
--    SELECT
--      (SELECT COUNT(*) FROM commerce.products) AS products,
--      (SELECT COUNT(*) FROM commerce.orders) AS orders,
--      (SELECT COUNT(*) FROM identity.users) AS users;
--
-- 7. Phase 1 complete:
--    SELECT version FROM public.schema_migrations
--    WHERE version = '000026_seed_reference_data';


-- =============================================================================
-- ROLLBACK SQL (manual)
-- =============================================================================
--
-- DELETE FROM admin.system_settings;
-- DELETE FROM admin.notification_types;
--
-- DELETE FROM commerce.categories
-- WHERE slug IN ('women', 'men', 'kids', 'accessories')
--   AND parent_id IS NULL;
--
-- DELETE FROM commerce.warehouses WHERE code = 'WH-DEFAULT-01';
--
-- DELETE FROM admin.role_permissions rp
-- USING admin.permissions p
-- WHERE rp.permission_id = p.id
--   AND p.code IN (
--     'fulfillment.read', 'fulfillment.write',
--     'returns.read', 'returns.write',
--     'reviews.moderate', 'commerce.read'
--   );
--
-- DELETE FROM admin.permissions
-- WHERE code IN (
--   'fulfillment.read', 'fulfillment.write',
--   'returns.read', 'returns.write',
--   'reviews.moderate', 'commerce.read'
-- );
--
-- DROP TRIGGER IF EXISTS system_settings_set_updated_at ON admin.system_settings;
-- DROP TABLE IF EXISTS admin.notification_types;
-- DROP TABLE IF EXISTS admin.system_settings;
--
-- DELETE FROM public.schema_migrations
-- WHERE version = '000026_seed_reference_data';

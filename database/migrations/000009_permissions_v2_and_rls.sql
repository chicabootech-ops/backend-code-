-- Chic A Boo — permission vocabulary v2 + RLS for new customer tables
-- Migrates products.update → products.write, adds granular permissions

-- ---------------------------------------------------------------------------
-- 7. Permission strategy v2 (additive — old codes retained for compatibility)
-- ---------------------------------------------------------------------------
INSERT INTO admin.permissions (code, description) VALUES
    ('users.write',        'Create and update customer accounts'),
    ('users.delete',       'Soft-delete or block customer accounts'),
    ('products.read',      'View products and catalog'),
    ('products.write',     'Create and update products'),
    ('orders.write',       'Create and modify orders'),
    ('orders.refund',      'Issue payment refunds'),
    ('inventory.read',     'View stock levels'),
    ('inventory.write',    'Adjust inventory counts'),
    ('coupons.read',       'View coupons'),
    ('coupons.write',      'Create and update coupons'),
    ('analytics.write',    'Export analytics data'),
    ('settings.read',      'View system settings'),
    ('settings.write',     'Modify system settings')
ON CONFLICT DO NOTHING;

-- super_admin gets all new permissions
INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
CROSS JOIN admin.permissions p
WHERE r.name = 'super_admin'
  AND p.code IN (
    'users.write', 'users.delete', 'products.read', 'products.write',
    'orders.write', 'orders.refund', 'inventory.read', 'inventory.write',
    'coupons.read', 'coupons.write', 'analytics.write',
    'settings.read', 'settings.write'
  )
ON CONFLICT DO NOTHING;

-- admin role mappings
INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
JOIN admin.permissions p ON p.code IN (
    'users.write', 'products.read', 'products.write', 'orders.write',
    'inventory.read', 'coupons.read', 'coupons.write', 'analytics.write',
    'settings.read'
)
WHERE r.name = 'admin'
ON CONFLICT DO NOTHING;

INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
JOIN admin.permissions p ON p.code IN (
    'products.read', 'products.write', 'inventory.read', 'inventory.write', 'orders.read'
)
WHERE r.name = 'inventory_manager'
ON CONFLICT DO NOTHING;

INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
JOIN admin.permissions p ON p.code IN (
    'users.read', 'users.write', 'orders.read', 'orders.write', 'orders.refund'
)
WHERE r.name = 'customer_support'
ON CONFLICT DO NOTHING;

INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
JOIN admin.permissions p ON p.code IN (
    'analytics.read', 'analytics.write', 'users.read', 'coupons.read', 'coupons.write'
)
WHERE r.name = 'marketing_manager'
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- RLS — new customer-facing tables
-- ---------------------------------------------------------------------------
ALTER TABLE auth.user_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.login_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_devices_select_own ON auth.user_devices
    FOR SELECT
    USING (
        user_id = public.current_user_id()
        AND revoked_at IS NULL
    );

CREATE POLICY user_devices_insert_own ON auth.user_devices
    FOR INSERT
    WITH CHECK (user_id = public.current_user_id());

CREATE POLICY user_devices_update_own ON auth.user_devices
    FOR UPDATE
    USING (user_id = public.current_user_id())
    WITH CHECK (user_id = public.current_user_id());

CREATE POLICY payment_customers_select_own ON public.payment_customers
    FOR SELECT
    USING (
        user_id = public.current_user_id()
        AND deleted_at IS NULL
    );

CREATE POLICY login_history_select_own ON auth.login_history
    FOR SELECT
    USING (user_id = public.current_user_id());

-- payment_customers and login_history writes are service-only (no INSERT/UPDATE policies)

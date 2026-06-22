-- Chic A Boo — seed RBAC roles and permissions

INSERT INTO admin.roles (name, description) VALUES
    ('super_admin',        'Full system access'),
    ('admin',              'General administration'),
    ('inventory_manager',  'Product and stock management'),
    ('customer_support',   'Customer and order support'),
    ('marketing_manager',  'Marketing and analytics')
ON CONFLICT DO NOTHING;

INSERT INTO admin.permissions (code, description) VALUES
    ('users.read',       'View customer accounts'),
    ('users.update',     'Update customer accounts'),
    ('orders.read',      'View orders'),
    ('orders.update',    'Update order status'),
    ('products.create',  'Create products'),
    ('products.update',  'Update products'),
    ('products.delete',  'Soft-delete products'),
    ('analytics.read',   'View analytics dashboards')
ON CONFLICT DO NOTHING;

-- super_admin — all permissions
INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
CROSS JOIN admin.permissions p
WHERE r.name = 'super_admin'
ON CONFLICT DO NOTHING;

-- admin — all except products.delete
INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
JOIN admin.permissions p ON p.code != 'products.delete'
WHERE r.name = 'admin'
ON CONFLICT DO NOTHING;

-- inventory_manager
INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
JOIN admin.permissions p ON p.code IN (
    'products.create', 'products.update', 'products.delete', 'orders.read'
)
WHERE r.name = 'inventory_manager'
ON CONFLICT DO NOTHING;

-- customer_support
INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
JOIN admin.permissions p ON p.code IN (
    'users.read', 'users.update', 'orders.read', 'orders.update'
)
WHERE r.name = 'customer_support'
ON CONFLICT DO NOTHING;

-- marketing_manager
INSERT INTO admin.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM admin.roles r
JOIN admin.permissions p ON p.code IN ('analytics.read', 'users.read')
WHERE r.name = 'marketing_manager'
ON CONFLICT DO NOTHING;

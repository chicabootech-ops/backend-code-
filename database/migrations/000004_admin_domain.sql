-- Chic A Boo — admin schema

-- ---------------------------------------------------------------------------
-- admin.roles
-- ---------------------------------------------------------------------------
CREATE TABLE admin.roles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX roles_name_unique ON admin.roles (name);

-- ---------------------------------------------------------------------------
-- admin.permissions
-- ---------------------------------------------------------------------------
CREATE TABLE admin.permissions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX permissions_code_unique ON admin.permissions (code);

-- ---------------------------------------------------------------------------
-- admin.role_permissions
-- ---------------------------------------------------------------------------
CREATE TABLE admin.role_permissions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id         UUID NOT NULL REFERENCES admin.roles (id) ON DELETE CASCADE,
    permission_id   UUID NOT NULL REFERENCES admin.permissions (id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT role_permissions_role_permission_unique UNIQUE (role_id, permission_id)
);

CREATE INDEX role_permissions_role_id_idx ON admin.role_permissions (role_id);
CREATE INDEX role_permissions_permission_id_idx ON admin.role_permissions (permission_id);

-- ---------------------------------------------------------------------------
-- admin.admin_users
-- ---------------------------------------------------------------------------
CREATE TABLE admin.admin_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    role_id         UUID NOT NULL REFERENCES admin.roles (id) ON DELETE RESTRICT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT admin_users_status_check CHECK (
        status IN ('active', 'inactive', 'suspended', 'super_admin')
    )
);

CREATE UNIQUE INDEX admin_users_email_unique_active
    ON admin.admin_users (email)
    WHERE deleted_at IS NULL;

CREATE INDEX admin_users_role_id_idx ON admin.admin_users (role_id);

-- ---------------------------------------------------------------------------
-- admin.audit_logs
-- ---------------------------------------------------------------------------
CREATE TABLE admin.audit_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id    UUID NOT NULL REFERENCES admin.admin_users (id) ON DELETE RESTRICT,
    entity_type TEXT NOT NULL,
    entity_id   UUID NOT NULL,
    action      TEXT NOT NULL,
    old_data    JSONB,
    new_data    JSONB,
    ip_address  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX audit_logs_admin_id_idx ON admin.audit_logs (admin_id);
CREATE INDEX audit_logs_entity_type_idx ON admin.audit_logs (entity_type);
CREATE INDEX audit_logs_created_at_idx ON admin.audit_logs (created_at);

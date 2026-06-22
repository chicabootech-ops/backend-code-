-- Chic A Boo — production enhancements (v1.1)
-- Depends on: 000001–000007

-- ===========================================================================
-- 1. Customer number (human-friendly reference, UUID remains PK)
-- ===========================================================================
CREATE SEQUENCE identity.customer_number_seq
    START WITH 100001
    INCREMENT BY 1
    NO MAXVALUE
    CACHE 1;

ALTER TABLE identity.users
    ADD COLUMN customer_number BIGINT;

UPDATE identity.users
SET customer_number = nextval('identity.customer_number_seq')
WHERE customer_number IS NULL;

ALTER TABLE identity.users
    ALTER COLUMN customer_number SET NOT NULL,
    ALTER COLUMN customer_number SET DEFAULT nextval('identity.customer_number_seq');

CREATE UNIQUE INDEX users_customer_number_unique ON identity.users (customer_number);

-- ===========================================================================
-- 2. Account security fields
-- ===========================================================================
ALTER TABLE identity.users
    ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN locked_until TIMESTAMPTZ,
    ADD CONSTRAINT users_failed_login_attempts_nonneg CHECK (failed_login_attempts >= 0);

CREATE INDEX users_locked_until_idx ON identity.users (locked_until)
    WHERE locked_until IS NOT NULL;

-- ===========================================================================
-- 3. Email normalization (case-insensitive uniqueness)
-- ===========================================================================
ALTER TABLE identity.users
    ADD COLUMN email_normalized TEXT;

UPDATE identity.users
SET email_normalized = lower(trim(email))
WHERE email_normalized IS NULL;

ALTER TABLE identity.users
    ALTER COLUMN email_normalized SET NOT NULL;

DROP INDEX IF EXISTS identity.users_email_unique_active;

CREATE UNIQUE INDEX users_email_normalized_unique_active
    ON identity.users (email_normalized)
    WHERE deleted_at IS NULL;

CREATE INDEX users_email_idx ON identity.users (email);

-- ===========================================================================
-- 5. User status tracking
-- ===========================================================================
ALTER TABLE identity.users
    ADD COLUMN status_reason TEXT;

CREATE INDEX users_status_reason_idx ON identity.users (status)
    WHERE status IN ('suspended', 'blocked');

-- ===========================================================================
-- 11. Phone strategy — Option B: non-unique, indexed for lookup
-- ===========================================================================
DROP INDEX IF EXISTS identity.users_phone_unique_active;

CREATE INDEX users_phone_idx ON identity.users (phone)
    WHERE deleted_at IS NULL AND phone IS NOT NULL;

-- Optional: one verified phone per active account (recommended middle ground)
CREATE UNIQUE INDEX users_phone_verified_unique_active
    ON identity.users (phone)
    WHERE deleted_at IS NULL
      AND phone IS NOT NULL
      AND phone_verified = TRUE;

-- ===========================================================================
-- 4. Address improvements
-- ===========================================================================
ALTER TABLE public.user_addresses
    ADD COLUMN address_type TEXT NOT NULL DEFAULT 'shipping',
    ADD COLUMN custom_label TEXT,
    ADD CONSTRAINT user_addresses_address_type_check CHECK (
        address_type IN ('shipping', 'billing', 'home', 'office', 'other')
    );

CREATE INDEX user_addresses_address_type_idx ON public.user_addresses (address_type);
CREATE INDEX user_addresses_user_id_type_idx ON public.user_addresses (user_id, address_type)
    WHERE deleted_at IS NULL;

-- ===========================================================================
-- 6. Enhanced admin audit logs
-- ===========================================================================
ALTER TABLE admin.audit_logs
    ADD COLUMN request_id TEXT,
    ADD COLUMN user_agent TEXT,
    ADD COLUMN target_user_id UUID REFERENCES identity.users (id) ON DELETE SET NULL;

CREATE INDEX audit_logs_request_id_idx ON admin.audit_logs (request_id)
    WHERE request_id IS NOT NULL;

CREATE INDEX audit_logs_target_user_id_idx ON admin.audit_logs (target_user_id)
    WHERE target_user_id IS NOT NULL;

CREATE INDEX audit_logs_entity_lookup_idx ON admin.audit_logs (entity_type, entity_id);

-- ===========================================================================
-- 8. Admin MFA support
-- ===========================================================================
ALTER TABLE admin.admin_users
    ADD COLUMN mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN mfa_secret TEXT,
    ADD COLUMN last_mfa_at TIMESTAMPTZ;

CREATE INDEX admin_users_mfa_enabled_idx ON admin.admin_users (mfa_enabled)
    WHERE mfa_enabled = TRUE AND deleted_at IS NULL;

-- ===========================================================================
-- 9. User device tracking
-- ===========================================================================
CREATE TABLE identity.user_devices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
    device_name     TEXT,
    device_type     TEXT NOT NULL DEFAULT 'unknown',
    ip_address      TEXT,
    user_agent      TEXT,
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at      TIMESTAMPTZ,
    CONSTRAINT user_devices_device_type_check CHECK (
        device_type IN ('mobile', 'tablet', 'desktop', 'unknown')
    )
);

CREATE INDEX user_devices_user_id_idx ON identity.user_devices (user_id);
CREATE INDEX user_devices_last_seen_at_idx ON identity.user_devices (last_seen_at);
CREATE INDEX user_devices_user_active_idx ON identity.user_devices (user_id, last_seen_at DESC)
    WHERE revoked_at IS NULL;

-- ===========================================================================
-- 10. Payment customer mapping (Razorpay + future providers)
-- ===========================================================================
CREATE TABLE public.payment_customers (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
    provider                TEXT NOT NULL,
    provider_customer_id    TEXT NOT NULL,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at              TIMESTAMPTZ,
    CONSTRAINT payment_customers_provider_check CHECK (
        provider IN ('razorpay', 'stripe', 'payu', 'cashfree')
    )
);

CREATE UNIQUE INDEX payment_customers_user_provider_unique
    ON public.payment_customers (user_id, provider)
    WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX payment_customers_provider_customer_unique
    ON public.payment_customers (provider, provider_customer_id)
    WHERE deleted_at IS NULL;

CREATE INDEX payment_customers_user_id_idx ON public.payment_customers (user_id);

CREATE TRIGGER payment_customers_set_updated_at
    BEFORE UPDATE ON public.payment_customers
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- ===========================================================================
-- 12. Foundational: login history (structured auth audit)
-- ===========================================================================
CREATE TABLE identity.login_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES identity.users (id) ON DELETE SET NULL,
    email_attempted TEXT,
    success         BOOLEAN NOT NULL,
    failure_reason  TEXT,
    ip_address      TEXT,
    user_agent      TEXT,
    device_id       UUID REFERENCES identity.user_devices (id) ON DELETE SET NULL,
    request_id      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX login_history_user_id_idx ON identity.login_history (user_id);
CREATE INDEX login_history_created_at_idx ON identity.login_history (created_at);
CREATE INDEX login_history_ip_address_idx ON identity.login_history (ip_address)
    WHERE ip_address IS NOT NULL;

-- ===========================================================================
-- 12. Foundational: admin sessions (DB record; Redis holds active session)
-- ===========================================================================
CREATE TABLE admin.admin_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id        UUID NOT NULL REFERENCES admin.admin_users (id) ON DELETE CASCADE,
    session_token_hash TEXT NOT NULL,
    ip_address      TEXT,
    user_agent      TEXT,
    mfa_verified    BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX admin_sessions_token_hash_unique ON admin.admin_sessions (session_token_hash);
CREATE INDEX admin_sessions_admin_id_idx ON admin.admin_sessions (admin_id);
CREATE INDEX admin_sessions_expires_at_idx ON admin.admin_sessions (expires_at);

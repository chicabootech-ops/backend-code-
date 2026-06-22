-- Chic A Boo — auth schema

-- ---------------------------------------------------------------------------
-- identity.users
-- ---------------------------------------------------------------------------
CREATE TABLE identity.users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL,
    phone           TEXT,
    password_hash   TEXT NOT NULL,
    email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    phone_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'pending_verification',
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT users_status_check CHECK (
        status IN ('active', 'suspended', 'blocked', 'pending_verification')
    )
);

CREATE UNIQUE INDEX users_email_unique_active
    ON identity.users (email)
    WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX users_phone_unique_active
    ON identity.users (phone)
    WHERE deleted_at IS NULL AND phone IS NOT NULL;

CREATE INDEX users_status_idx ON identity.users (status);
CREATE INDEX users_created_at_idx ON identity.users (created_at);

-- ---------------------------------------------------------------------------
-- identity.refresh_tokens
-- ---------------------------------------------------------------------------
CREATE TABLE identity.refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
    token_jti   TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX refresh_tokens_token_jti_unique ON identity.refresh_tokens (token_jti);
CREATE INDEX refresh_tokens_user_id_idx ON identity.refresh_tokens (user_id);
CREATE INDEX refresh_tokens_expires_at_idx ON identity.refresh_tokens (expires_at);

-- ---------------------------------------------------------------------------
-- identity.email_otps
-- ---------------------------------------------------------------------------
CREATE TABLE identity.email_otps (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT NOT NULL,
    otp_hash    TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    expires_at  TIMESTAMPTZ NOT NULL,
    verified    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX email_otps_email_idx ON identity.email_otps (email);
CREATE INDEX email_otps_expires_at_idx ON identity.email_otps (expires_at);

-- ---------------------------------------------------------------------------
-- identity.password_resets
-- ---------------------------------------------------------------------------
CREATE TABLE identity.password_resets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX password_resets_token_hash_unique ON identity.password_resets (token_hash);
CREATE INDEX password_resets_user_id_idx ON identity.password_resets (user_id);

-- ---------------------------------------------------------------------------
-- identity.security_logs
-- ---------------------------------------------------------------------------
CREATE TABLE identity.security_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES identity.users (id) ON DELETE SET NULL,
    event_type  TEXT NOT NULL,
    ip_address  TEXT,
    user_agent  TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX security_logs_user_id_idx ON identity.security_logs (user_id);
CREATE INDEX security_logs_event_type_idx ON identity.security_logs (event_type);
CREATE INDEX security_logs_created_at_idx ON identity.security_logs (created_at);

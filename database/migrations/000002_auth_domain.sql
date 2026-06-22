-- Chic A Boo — auth schema

-- ---------------------------------------------------------------------------
-- auth.users
-- ---------------------------------------------------------------------------
CREATE TABLE auth.users (
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
    ON auth.users (email)
    WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX users_phone_unique_active
    ON auth.users (phone)
    WHERE deleted_at IS NULL AND phone IS NOT NULL;

CREATE INDEX users_status_idx ON auth.users (status);
CREATE INDEX users_created_at_idx ON auth.users (created_at);

-- ---------------------------------------------------------------------------
-- auth.refresh_tokens
-- ---------------------------------------------------------------------------
CREATE TABLE auth.refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    token_jti   TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX refresh_tokens_token_jti_unique ON auth.refresh_tokens (token_jti);
CREATE INDEX refresh_tokens_user_id_idx ON auth.refresh_tokens (user_id);
CREATE INDEX refresh_tokens_expires_at_idx ON auth.refresh_tokens (expires_at);

-- ---------------------------------------------------------------------------
-- auth.email_otps
-- ---------------------------------------------------------------------------
CREATE TABLE auth.email_otps (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT NOT NULL,
    otp_hash    TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    expires_at  TIMESTAMPTZ NOT NULL,
    verified    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX email_otps_email_idx ON auth.email_otps (email);
CREATE INDEX email_otps_expires_at_idx ON auth.email_otps (expires_at);

-- ---------------------------------------------------------------------------
-- auth.password_resets
-- ---------------------------------------------------------------------------
CREATE TABLE auth.password_resets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX password_resets_token_hash_unique ON auth.password_resets (token_hash);
CREATE INDEX password_resets_user_id_idx ON auth.password_resets (user_id);

-- ---------------------------------------------------------------------------
-- auth.security_logs
-- ---------------------------------------------------------------------------
CREATE TABLE auth.security_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES auth.users (id) ON DELETE SET NULL,
    event_type  TEXT NOT NULL,
    ip_address  TEXT,
    user_agent  TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX security_logs_user_id_idx ON auth.security_logs (user_id);
CREATE INDEX security_logs_event_type_idx ON auth.security_logs (event_type);
CREATE INDEX security_logs_created_at_idx ON auth.security_logs (created_at);

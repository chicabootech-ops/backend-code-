-- Chic A Boo — public (customer) schema

-- ---------------------------------------------------------------------------
-- public.user_profiles
-- ---------------------------------------------------------------------------
CREATE TABLE public.user_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
    first_name      TEXT,
    last_name       TEXT,
    gender          TEXT,
    date_of_birth   DATE,
    avatar_url      TEXT,
    loyalty_points  INTEGER NOT NULL DEFAULT 0,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT user_profiles_loyalty_points_nonneg CHECK (loyalty_points >= 0)
);

CREATE UNIQUE INDEX user_profiles_user_id_unique
    ON public.user_profiles (user_id)
    WHERE deleted_at IS NULL;

CREATE INDEX user_profiles_first_name_idx ON public.user_profiles (first_name);
CREATE INDEX user_profiles_last_name_idx ON public.user_profiles (last_name);

-- ---------------------------------------------------------------------------
-- public.user_addresses
-- ---------------------------------------------------------------------------
CREATE TABLE public.user_addresses (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
    label       TEXT,
    full_name   TEXT NOT NULL,
    phone       TEXT,
    line1       TEXT NOT NULL,
    line2       TEXT,
    landmark    TEXT,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    postal_code TEXT NOT NULL,
    country     TEXT NOT NULL DEFAULT 'IN',
    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX user_addresses_user_id_idx ON public.user_addresses (user_id);
CREATE INDEX user_addresses_postal_code_idx ON public.user_addresses (postal_code);

-- ---------------------------------------------------------------------------
-- public.user_preferences
-- ---------------------------------------------------------------------------
CREATE TABLE public.user_preferences (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
    email_marketing     BOOLEAN NOT NULL DEFAULT FALSE,
    sms_marketing       BOOLEAN NOT NULL DEFAULT FALSE,
    preferred_language  TEXT NOT NULL DEFAULT 'en',
    currency            TEXT NOT NULL DEFAULT 'INR',
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX user_preferences_user_id_unique ON public.user_preferences (user_id);

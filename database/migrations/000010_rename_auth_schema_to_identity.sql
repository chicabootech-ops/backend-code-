-- Rename legacy Chic A Boo `auth` schema on self-hosted Postgres.
-- Skipped on Supabase (auth schema is owned by supabase_admin).

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'auth'
          AND table_name = 'users'
          AND column_name = 'password_hash'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.schemata
        WHERE schema_name = 'identity'
    ) THEN
        ALTER SCHEMA auth RENAME TO identity;
    END IF;
END $$;

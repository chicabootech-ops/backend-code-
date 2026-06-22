-- Chic A Boo — Row Level Security (customer-facing tables)
--
-- Services bypass RLS using a privileged DB role (e.g. chicaboo_service).
-- Customer-scoped queries set: SET LOCAL app.current_user_id = '<uuid>';

ALTER TABLE identity.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_addresses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_preferences ENABLE ROW LEVEL SECURITY;

-- identity.users — users may only access their own non-deleted row
CREATE POLICY users_select_own ON identity.users
    FOR SELECT
    USING (
        id = public.current_user_id()
        AND deleted_at IS NULL
    );

CREATE POLICY users_update_own ON identity.users
    FOR UPDATE
    USING (id = public.current_user_id())
    WITH CHECK (id = public.current_user_id());

-- public.user_profiles
CREATE POLICY user_profiles_select_own ON public.user_profiles
    FOR SELECT
    USING (
        user_id = public.current_user_id()
        AND deleted_at IS NULL
    );

CREATE POLICY user_profiles_insert_own ON public.user_profiles
    FOR INSERT
    WITH CHECK (user_id = public.current_user_id());

CREATE POLICY user_profiles_update_own ON public.user_profiles
    FOR UPDATE
    USING (user_id = public.current_user_id())
    WITH CHECK (user_id = public.current_user_id());

-- public.user_addresses
CREATE POLICY user_addresses_select_own ON public.user_addresses
    FOR SELECT
    USING (
        user_id = public.current_user_id()
        AND deleted_at IS NULL
    );

CREATE POLICY user_addresses_insert_own ON public.user_addresses
    FOR INSERT
    WITH CHECK (user_id = public.current_user_id());

CREATE POLICY user_addresses_update_own ON public.user_addresses
    FOR UPDATE
    USING (user_id = public.current_user_id())
    WITH CHECK (user_id = public.current_user_id());

-- public.user_preferences
CREATE POLICY user_preferences_select_own ON public.user_preferences
    FOR SELECT
    USING (user_id = public.current_user_id());

CREATE POLICY user_preferences_insert_own ON public.user_preferences
    FOR INSERT
    WITH CHECK (user_id = public.current_user_id());

CREATE POLICY user_preferences_update_own ON public.user_preferences
    FOR UPDATE
    USING (user_id = public.current_user_id())
    WITH CHECK (user_id = public.current_user_id());

-- Internal auth tables and all admin tables intentionally have RLS disabled.
-- Access is restricted at the application layer via service credentials.

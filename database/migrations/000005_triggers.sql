-- Chic A Boo — shared triggers and helper functions

-- Session variable used by RLS policies (set per transaction by application services):
--   SET LOCAL app.current_user_id = '<uuid>';

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.current_user_id()
RETURNS UUID
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid;
$$;

-- auth.users
CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- public.user_profiles
CREATE TRIGGER user_profiles_set_updated_at
    BEFORE UPDATE ON public.user_profiles
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- public.user_addresses
CREATE TRIGGER user_addresses_set_updated_at
    BEFORE UPDATE ON public.user_addresses
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- public.user_preferences
CREATE TRIGGER user_preferences_set_updated_at
    BEFORE UPDATE ON public.user_preferences
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- admin.roles
CREATE TRIGGER roles_set_updated_at
    BEFORE UPDATE ON admin.roles
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- admin.admin_users
CREATE TRIGGER admin_users_set_updated_at
    BEFORE UPDATE ON admin.admin_users
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

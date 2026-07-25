-- Chic A Boo newsletter double opt-in subscribers.
CREATE TABLE commerce.newsletter_subscribers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  confirm_token TEXT,
  confirmed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT newsletter_subscribers_email_unique UNIQUE (email),
  CONSTRAINT newsletter_subscribers_confirm_token_unique UNIQUE (confirm_token),
  CONSTRAINT newsletter_subscribers_status_check
    CHECK (status IN ('pending', 'confirmed'))
);

CREATE INDEX newsletter_subscribers_status_idx
  ON commerce.newsletter_subscribers (status);

CREATE TRIGGER newsletter_subscribers_set_updated_at
  BEFORE UPDATE ON commerce.newsletter_subscribers
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

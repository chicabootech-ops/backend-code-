-- Unsubscribe support for the newsletter list.
--
-- Marketing email cannot legally go out without a working one-click
-- unsubscribe (India's DPDP Act; CAN-SPAM and GDPR for recipients abroad), and
-- the table had no way to express "this person opted out" — status allowed only
-- 'pending' and 'confirmed'. Sending to this list before this migration would
-- have meant every recipient was stuck on it.
--
-- The token is separate from confirm_token on purpose. confirm_token is cleared
-- once opt-in completes, so reusing it would leave confirmed subscribers with
-- nothing to unsubscribe with — which is precisely the group that receives mail.

ALTER TABLE commerce.newsletter_subscribers
  DROP CONSTRAINT IF EXISTS newsletter_subscribers_status_check;

ALTER TABLE commerce.newsletter_subscribers
  ADD CONSTRAINT newsletter_subscribers_status_check
    CHECK (status IN ('pending', 'confirmed', 'unsubscribed'));

ALTER TABLE commerce.newsletter_subscribers
  ADD COLUMN IF NOT EXISTS unsubscribe_token TEXT,
  ADD COLUMN IF NOT EXISTS unsubscribed_at   TIMESTAMPTZ;

-- Every existing row gets a token now rather than lazily at send time: a send
-- that has to mint tokens mid-flight is a send that can partially fail and
-- leave some recipients without a working link.
UPDATE commerce.newsletter_subscribers
   SET unsubscribe_token = replace(gen_random_uuid()::text, '-', '')
                        || replace(gen_random_uuid()::text, '-', '')
 WHERE unsubscribe_token IS NULL;

ALTER TABLE commerce.newsletter_subscribers
  ALTER COLUMN unsubscribe_token SET NOT NULL;

ALTER TABLE commerce.newsletter_subscribers
  ALTER COLUMN unsubscribe_token
    SET DEFAULT replace(gen_random_uuid()::text, '-', '')
             || replace(gen_random_uuid()::text, '-', '');

CREATE UNIQUE INDEX IF NOT EXISTS newsletter_subscribers_unsub_token_idx
  ON commerce.newsletter_subscribers (unsubscribe_token);

COMMENT ON COLUMN commerce.newsletter_subscribers.unsubscribe_token IS
  'Stable per-subscriber secret for the one-click unsubscribe link. Unlike '
  'confirm_token this is never cleared — it must keep working for as long as '
  'the address can receive mail.';

COMMENT ON COLUMN commerce.newsletter_subscribers.status IS
  'pending = opted in, not confirmed. confirmed = mailable. '
  'unsubscribed = opted out; never include in a marketing send.';

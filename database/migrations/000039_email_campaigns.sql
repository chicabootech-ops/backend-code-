-- Email newsletter campaigns.
--
-- Reuses ops.notification_campaigns rather than adding a parallel table: it was
-- built for exactly this and has status, counters and timestamps already. What
-- it lacked was anywhere to put an email — it was shaped around WhatsApp/SMS,
-- where the body lives in a provider template rather than on the campaign.
--
-- `channel` is added so a future WhatsApp campaign can share the table without
-- a newsletter blast ever being mistaken for one.

ALTER TABLE ops.notification_campaigns
  ADD COLUMN IF NOT EXISTS channel   TEXT NOT NULL DEFAULT 'email',
  ADD COLUMN IF NOT EXISTS subject   TEXT,
  ADD COLUMN IF NOT EXISTS body_html TEXT;

ALTER TABLE ops.notification_campaigns
  DROP CONSTRAINT IF EXISTS notification_campaigns_channel_check;

ALTER TABLE ops.notification_campaigns
  ADD CONSTRAINT notification_campaigns_channel_check
    CHECK (channel IN ('email', 'sms', 'whatsapp'));

-- An email campaign is unsendable without both. Enforced here rather than only
-- in the service so a half-written row can never be picked up and sent blank.
ALTER TABLE ops.notification_campaigns
  DROP CONSTRAINT IF EXISTS notification_campaigns_email_body_check;

ALTER TABLE ops.notification_campaigns
  ADD CONSTRAINT notification_campaigns_email_body_check CHECK (
    channel <> 'email'
    OR status = 'draft'
    OR (subject IS NOT NULL AND btrim(subject) <> ''
        AND body_html IS NOT NULL AND btrim(body_html) <> '')
  );

COMMENT ON COLUMN ops.notification_campaigns.body_html IS
  'Author-supplied HTML body. The unsubscribe footer is appended per recipient '
  'at send time, never stored here — each recipient needs their own token.';

CREATE INDEX IF NOT EXISTS notification_campaigns_channel_idx
  ON ops.notification_campaigns (channel, created_at DESC);

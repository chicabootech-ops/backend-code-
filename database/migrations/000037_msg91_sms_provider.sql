-- MSG91 replaces the previous SMS vendor.
--
-- `provider` is TEXT guarded by a CHECK on two tables, so writing 'msg91'
-- fails at the database until those constraints are widened. Without this
-- migration the new provider authenticates, sends, and then dies recording the
-- attempt — the send would actually go out and the ledger would not know.
--
-- The two tables are treated differently on purpose:
--
--   ops.notification_templates is *configuration*. Those rows describe how we
--   send today, so they are repointed at msg91 and the old value is dropped
--   from the constraint.
--
--   ops.notification_attempts is *history*. Each row records what a provider
--   actually did at a point in time. Rewriting them to 'msg91' would assert
--   that MSG91 returned errors it never saw, and deleting them would destroy
--   the delivery audit trail. The old value therefore stays permitted here,
--   for existing rows only.

-- No BEGIN/COMMIT here: migrate.py runs each file inside its own transaction
-- and records the version in the same one, matching every migration before this.

-- --------------------------------------------------------------------------
-- Attempts: allow msg91 going forward, keep the old value so history validates
-- --------------------------------------------------------------------------
ALTER TABLE ops.notification_attempts
  DROP CONSTRAINT IF EXISTS notification_attempts_provider_check;

ALTER TABLE ops.notification_attempts
  ADD CONSTRAINT notification_attempts_provider_check CHECK (
    provider IN ('whatsapp', 'msg91', 'resend', 'smtp', 'message_central')
  );

COMMENT ON COLUMN ops.notification_attempts.provider IS
  'Which provider served this attempt. The pre-migration SMS vendor value is '
  'retained only so historical rows remain valid. No new row should use it.';

-- --------------------------------------------------------------------------
-- Templates: repoint configuration, then narrow the constraint
--
-- Order matters and is not the obvious one. The constraint must come off
-- BEFORE the rows are repointed: the old CHECK does not list 'msg91', so an
-- UPDATE that runs first fails on its own constraint. Drop, update, re-add.
-- --------------------------------------------------------------------------
ALTER TABLE ops.notification_templates
  DROP CONSTRAINT IF EXISTS notification_templates_provider_check;

UPDATE ops.notification_templates
   SET provider = 'msg91'
 WHERE provider = 'message_central';

ALTER TABLE ops.notification_templates
  ADD CONSTRAINT notification_templates_provider_check CHECK (
    provider IN ('whatsapp', 'msg91', 'resend', 'smtp')
  );

-- MSG91 renders its own DLT-registered template from named variables rather
-- than accepting a body we assembled, so each row needs the MSG91 template id
-- in provider_template_id. They are NULL right now; until they are filled in,
-- the provider falls back to the MSG91_TEMPLATE_ID setting, which is correct
-- for OTP and wrong for everything else.
COMMENT ON COLUMN ops.notification_templates.provider_template_id IS
  'For msg91 rows this is the DLT-registered MSG91 template id. Required for '
  'anything other than the single default OTP template.';

-- Allow 'marketing' as an audit domain, and rescue campaigns stranded by its
-- absence.
--
-- audit_logs_domain_check permitted catalog/inventory/order/payment/user/admin.
-- Newsletter campaigns and subscriber opt-outs are none of those, so every send
-- raised a CheckViolation *after* the mail loop had already run — the operator
-- saw a 500, and the campaign row was left in 'running' with counters that had
-- rolled back. Marketing is a genuinely distinct domain, so it gets named
-- rather than folded into 'admin'.

ALTER TABLE admin.audit_logs
  DROP CONSTRAINT IF EXISTS audit_logs_domain_check;

ALTER TABLE admin.audit_logs
  ADD CONSTRAINT audit_logs_domain_check CHECK (
    domain IS NULL
    OR domain IN ('catalog', 'inventory', 'order', 'payment', 'user', 'admin', 'marketing')
  );

-- Close out anything the old constraint stranded. These are marked 'cancelled'
-- rather than 'completed' on purpose: the counters never persisted, so how many
-- recipients were actually reached is unknown, and recording an unknown as a
-- success would misreport it forever.
UPDATE ops.notification_campaigns
   SET status = 'cancelled',
       completed_at = COALESCE(completed_at, now())
 WHERE status = 'running'
   AND started_at < now() - interval '1 minute';

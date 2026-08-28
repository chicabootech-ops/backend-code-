-- Codify the OTP template mapping that production is already running.
--
-- 000036 gave each OTP purpose its own template name — `chicaboo_otp_login`,
-- `_signup`, `_verify`, `_reset` — and 000041 section 4 activated all of them.
-- None of the four was ever created in WhatsApp Manager. The live database has
-- since been corrected out of band: all five OTP types already read
-- `chicaboo_otp`, verified against the Neon database on 2026-08-28. This
-- migration is therefore a no-op against production and exists so that a
-- database provisioned from 000001..000042 lands in the same state instead of
-- reproducing names that return 132001.
--
-- `chicaboo_otp` is the only template in the WhatsApp Business Account
-- (confirmed by enumerating message_templates with paging: exactly one row,
-- APPROVED, AUTHENTICATION, en). Its body is `*{{1}}* is your verification
-- code` plus a COPY_CODE button, which matches the `["otp"]` variable_order
-- these rows already carry — the provider repeats that value into the button
-- for any template whose category is `authentication`.
--
-- The remaining 29 active WhatsApp rows still name templates that do not exist,
-- so the entire order lifecycle, cart reminders and marketing set fail 132001.
-- That is a Meta submission job, not a schema change, and is deliberately left
-- alone here rather than deactivated: an inactive row is indistinguishable from
-- a type nobody wired up, and these are all wired up and waiting on approval.

UPDATE ops.notification_templates
   SET provider_template_name = 'chicaboo_otp'
 WHERE channel = 'whatsapp'
   AND provider = 'whatsapp'
   AND notification_type IN (
     'OTP_LOGIN',
     'OTP_REGISTRATION',
     'OTP_PHONE_VERIFY',
     'OTP_PASSWORD_RESET',
     'OTP_CHANGE_PHONE'
   );

UPDATE admin.notification_types
   SET template = 'chicaboo_otp'
 WHERE channel = 'whatsapp'
   AND code IN (
     'OTP_LOGIN',
     'OTP_REGISTRATION',
     'OTP_PHONE_VERIFY',
     'OTP_PASSWORD_RESET',
     'OTP_CHANGE_PHONE'
   );

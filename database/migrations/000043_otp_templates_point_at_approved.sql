-- Point every OTP notification type at the one WhatsApp template Meta has
-- actually approved.
--
-- 000036 gave each OTP purpose its own template name — `chicaboo_otp_login`,
-- `_signup`, `_verify`, `_reset` — on the reasoning that Meta rejects a single
-- generic "here is a code". None of those four were ever submitted, and 000041
-- section 4 then activated the rows anyway. The WhatsApp Business Account holds
-- exactly one approved template, `chicaboo_otp` (AUTHENTICATION, en, one body
-- variable plus a COPY_CODE button), so every send has resolved to a name that
-- does not exist and come back 132001. That code is in `_PERMANENT_CODES`, so
-- the notification fails outright, `send_otp` supersedes the challenge, and the
-- customer gets a 503 — no OTP has been deliverable, on any purpose, since 41.
--
-- 000041 anticipated this exactly: "If a template has NOT actually been
-- approved in WhatsApp Manager, its send fails with a PERMANENT 132001."
--
-- `variable_order` is left alone: `["otp"]` already matches {{1}} in the
-- approved body, and the provider repeats that value into the copy-code button
-- for every template whose category is `authentication`, which all five rows
-- already are.
--
-- The per-purpose names remain the right end state — Meta reads a login code
-- and a password-reset code as different messages. Restoring them is this
-- statement in reverse, once each name exists and is APPROVED.

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

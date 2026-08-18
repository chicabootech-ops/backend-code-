-- Meta has one authentication template, named chicaboo_otp. The seeded rows in
-- 000036/000041 guessed per-purpose names that were never created, so every OTP
-- send failed with (#132001) Template name does not exist in the translation.

UPDATE ops.notification_templates
   SET provider_template_name = 'chicaboo_otp'
 WHERE channel = 'whatsapp'
   AND provider = 'whatsapp'
   AND notification_type LIKE 'OTP%';

SELECT notification_type, provider_template_name, language, category, is_active
  FROM ops.notification_templates
 WHERE channel = 'whatsapp' AND provider = 'whatsapp' AND notification_type LIKE 'OTP%'
 ORDER BY notification_type;

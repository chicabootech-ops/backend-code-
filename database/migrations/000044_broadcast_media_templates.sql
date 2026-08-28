-- Media-carrying broadcast templates.
--
-- A WhatsApp template's header format is fixed when Meta approves it, so
-- `chicaboo_broadcast` — body-only — can never carry an image or a video. These
-- two rows back the media variants, created in the WABA on 2026-08-28:
-- `chicaboo_broadcast_image` (IMAGE header) and `chicaboo_broadcast_video`
-- (VIDEO header). Same body and the same `["customer_name","message"]` binding
-- as the text variant, so the admin composer only chooses a header, not a
-- different message.
--
-- The header media itself does NOT live in `variable_order`. It travels as the
-- reserved `_header_media_id` / `_header_media_type` keys inside `variables`,
-- which is why a campaign's stored JSONB reaches the provider unchanged and no
-- column has to be added here. `{{1}}` and `{{2}}` remain the body's own.
--
-- Meta requires the header on every send once the template declares one: a
-- media template with no media id fails, which is why the sending path picks the
-- text variant when the admin attaches nothing.

INSERT INTO admin.notification_types
  (code, channel, template, description, is_transactional, is_active)
VALUES
  ('MARKETING_BROADCAST_IMAGE', 'whatsapp', 'chicaboo_broadcast_image',
   'Admin broadcast with an image header', FALSE, TRUE),
  ('MARKETING_BROADCAST_VIDEO', 'whatsapp', 'chicaboo_broadcast_video',
   'Admin broadcast with a video header', FALSE, TRUE)
ON CONFLICT DO NOTHING;

INSERT INTO ops.notification_templates
  (notification_type, channel, provider, provider_template_name, language,
   category, variable_order, body_text, is_active)
VALUES
  ('MARKETING_BROADCAST_IMAGE', 'whatsapp', 'whatsapp', 'chicaboo_broadcast_image',
   'en', 'marketing', '["customer_name","message"]'::jsonb, NULL, TRUE),
  ('MARKETING_BROADCAST_VIDEO', 'whatsapp', 'whatsapp', 'chicaboo_broadcast_video',
   'en', 'marketing', '["customer_name","message"]'::jsonb, NULL, TRUE)
ON CONFLICT DO NOTHING;

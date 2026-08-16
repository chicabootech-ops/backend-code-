"""Email newsletter campaigns.

Three rules shape this file, and each exists because breaking it causes real
damage rather than a bug report:

1.  **Only confirmed subscribers.** Pending addresses never opted in and
    unsubscribed ones opted out. The recipient query is the single place that
    decides, so there is no path that sends to anyone else.

2.  **Every email carries its own unsubscribe link.** The footer is appended per
    recipient at send time using that subscriber's token — it is never stored on
    the campaign, because a shared link cannot identify who clicked it.

3.  **Sends are paced.** Blasting a list as fast as the API allows is what gets a
    domain rate-limited or spam-filed, and this same domain carries order
    confirmations and invoices. A newsletter must never cost you those.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import NotFoundError, ValidationError
from app.admin_api.repositories.audit_repository import AuditRepository
from app.admin_api.schemas.campaign import CampaignOut, CampaignSendResult
from app.config import settings
from app.identity.services.email_service import EmailService

logger = logging.getLogger(__name__)

#: Recipients per batch, and the pause between batches. Deliberately gentle —
#: this list is small and the cost of going slowly is nothing next to the cost
#: of a damaged sending reputation.
_BATCH_SIZE = 20
_BATCH_PAUSE_SECONDS = 1.0


_URL_RE = re.compile(r"(https?://[^\s<]+)")


def render_body(text_body: str) -> str:
    """Turn what an admin actually typed into email-safe HTML.

    Admins write a message, not markup. Asking for HTML meant a stray `<` broke
    the layout and a plain-text paragraph arrived as one unbroken run of words.

    Everything is escaped first, so nothing a user types can inject markup; then
    blank lines become paragraphs, single newlines become line breaks, and bare
    URLs become links. Inline styles rather than a stylesheet, because email
    clients strip `<style>` blocks.
    """
    escaped = html.escape(text_body.strip())
    escaped = _URL_RE.sub(
        lambda m: f'<a href="{m.group(1)}" style="color:#946a2b">{m.group(1)}</a>', escaped
    )
    paragraphs = [block.strip() for block in re.split(r"\n\s*\n", escaped) if block.strip()]
    body = "".join(
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.7;color:#2b2724">'
        f'{block.replace(chr(10), "<br>")}</p>'
        for block in paragraphs
    )
    return (
        '<div style="font-family:Georgia,serif;max-width:560px;margin:0 auto;'
        'padding:32px 24px;background:#faf7f4;color:#2b2724">'
        f"{body}"
        "</div>"
    )


def _unsubscribe_footer(token: str) -> str:
    site = settings.site_url.rstrip("/")
    url = f"{site}/newsletter/unsubscribe?token={token}"
    return (
        '<hr style="margin:32px 0 16px;border:none;border-top:1px solid #e6ded7">'
        '<p style="font-size:12px;color:#7a6f66;line-height:1.6">'
        "You are receiving this because you subscribed to the Chic A Boo newsletter.<br>"
        f'<a href="{url}" style="color:#946a2b">Unsubscribe</a>'
        "</p>"
    )


class CampaignAdminService:
    def __init__(self, session: AsyncSession, email_service: EmailService | None = None) -> None:
        self._session = session
        self._audit = AuditRepository(session)
        self._email = email_service or EmailService(settings)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    async def list_campaigns(self, *, limit: int = 50) -> list[CampaignOut]:
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT id, name, subject, body_html, status, channel,
                           total_recipients, sent_count, failed_count,
                           started_at, completed_at, created_at
                      FROM ops.notification_campaigns
                     WHERE channel = 'email'
                     ORDER BY created_at DESC
                     LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        ).mappings().all()
        return [CampaignOut(**dict(r)) for r in rows]

    async def audience_size(self) -> int:
        return (
            await self._session.execute(
                text(
                    "SELECT count(*) FROM commerce.newsletter_subscribers "
                    "WHERE status = 'confirmed'"
                )
            )
        ).scalar_one()

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    async def send_test(self, *, subject: str, body: str, to_email: str) -> None:
        """Send one copy to an address of the admin's choosing.

        The footer is rendered with a dummy token so the layout matches a real
        send without handing out a working unsubscribe link for someone else.
        """
        self._validate(subject, body)
        await self._email._send(  # noqa: SLF001
            to_email=to_email.strip(),
            subject=f"[TEST] {subject}",
            html=render_body(body) + _unsubscribe_footer("test-token-not-valid"),
            required=True,
        )

    async def create_and_send(
        self,
        *,
        name: str,
        subject: str,
        body: str,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> CampaignSendResult:
        self._validate(subject, body)
        # Rendered once, not per recipient — the output is identical for
        # everyone and only the footer varies.
        body_html = render_body(body)

        recipients = (
            await self._session.execute(
                text(
                    """
                    SELECT email, unsubscribe_token
                      FROM commerce.newsletter_subscribers
                     WHERE status = 'confirmed'
                     ORDER BY created_at
                    """
                )
            )
        ).mappings().all()

        if not recipients:
            raise ValidationError("There are no confirmed subscribers to send to.")

        campaign_id = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO ops.notification_campaigns
                        (name, channel, subject, body_html, status,
                         total_recipients, started_at, created_by_admin_id)
                    VALUES (:name, 'email', :subject, :body, 'running',
                            :total, now(), :admin)
                 RETURNING id
                    """
                ),
                {
                    "name": name.strip() or subject.strip(),
                    "subject": subject.strip(),
                    "body": body_html,
                    "total": len(recipients),
                    "admin": str(admin_id),
                },
            )
        ).scalar_one()
        # Commit before sending: if the process dies mid-send, a 'running' row
        # with counters is a far better trace than no record that it happened.
        await self._session.commit()

        sent = 0
        failed = 0
        for start in range(0, len(recipients), _BATCH_SIZE):
            batch = recipients[start : start + _BATCH_SIZE]
            for row in batch:
                try:
                    await self._email._send(  # noqa: SLF001
                        to_email=row["email"],
                        subject=subject.strip(),
                        html=body_html + _unsubscribe_footer(row["unsubscribe_token"]),
                        required=True,
                    )
                    sent += 1
                except Exception:  # noqa: BLE001
                    # One bad address must not abort the campaign. The address is
                    # deliberately not logged at error level — a bounce list is
                    # not something to scatter through application logs.
                    failed += 1
                    logger.warning("campaign_send_failed campaign=%s", campaign_id)
            if start + _BATCH_SIZE < len(recipients):
                await asyncio.sleep(_BATCH_PAUSE_SECONDS)

        await self._session.execute(
            text(
                """
                UPDATE ops.notification_campaigns
                   SET status = 'completed', completed_at = now(),
                       sent_count = :sent, failed_count = :failed
                 WHERE id = :id
                """
            ),
            {"id": campaign_id, "sent": sent, "failed": failed},
        )
        # Committed before the audit entry, deliberately. Mail has already left;
        # the record of how many and to whom must survive even if a later write
        # fails. Getting this order wrong is what left campaigns stuck in
        # 'running' with their counters rolled back.
        await self._session.commit()

        await self._audit.log(
            admin_id=admin_id,
            entity_type="notification_campaign",
            entity_id=campaign_id,
            action="send",
            old_data=None,
            new_data={"subject": subject.strip(), "sent": sent, "failed": failed},
            domain="marketing",
            ip_address=ip_address,
        )
        logger.info(
            "campaign_completed id=%s sent=%s failed=%s", campaign_id, sent, failed
        )
        return CampaignSendResult(
            campaign_id=campaign_id,
            total_recipients=len(recipients),
            sent=sent,
            failed=failed,
        )

    @staticmethod
    def _validate(subject: str, body: str) -> None:
        if not subject.strip():
            raise ValidationError("Give the email a subject.")
        if not body.strip():
            raise ValidationError("The message is empty.")

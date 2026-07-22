"""Transactional email via Resend (primary) or SMTP (fallback)."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from app.config import Settings
from app.identity.core.exceptions import AppError
from app.identity.services import email_templates as templates

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def site_url(self) -> str:
        return (self._settings.site_url or "https://www.chicaboo.co").rstrip("/")

    async def send_verification_otp(self, *, to_email: str, otp: str) -> None:
        subject, html = templates.verification_otp_email(
            otp=otp,
            expires_minutes=max(1, self._settings.otp_ttl_seconds // 60),
            site_url=self.site_url,
        )
        await self._send(to_email=to_email, subject=subject, html=html, required=True)

    async def send_password_reset(self, *, to_email: str, reset_token: str) -> None:
        reset_url = f"{self.site_url}/reset-password?token={reset_token}"
        subject, html = templates.password_reset_email(
            reset_url=reset_url,
            expires_minutes=max(1, self._settings.password_reset_ttl_seconds // 60),
            site_url=self.site_url,
        )
        await self._send(to_email=to_email, subject=subject, html=html, required=True)

    async def send_welcome(self, *, to_email: str, first_name: str | None = None) -> None:
        subject, html = templates.welcome_email(first_name=first_name, site_url=self.site_url)
        await self._send(to_email=to_email, subject=subject, html=html, required=False)

    async def send_order_confirmation(
        self,
        *,
        to_email: str,
        order_number: str,
        total_label: str,
        track_url: str | None = None,
    ) -> None:
        subject, html = templates.order_confirmation_email(
            order_number=order_number,
            total_label=total_label,
            site_url=self.site_url,
            track_url=track_url,
        )
        await self._send(to_email=to_email, subject=subject, html=html, required=False)

    async def send_order_shipped(
        self,
        *,
        to_email: str,
        order_number: str,
        track_url: str | None = None,
    ) -> None:
        subject, html = templates.order_shipped_email(
            order_number=order_number,
            site_url=self.site_url,
            track_url=track_url,
        )
        await self._send(to_email=to_email, subject=subject, html=html, required=False)

    async def send_admin_alert(self, *, title: str, detail: str) -> None:
        to_email = self._settings.email_admin or self._settings.email_from
        if not to_email:
            logger.warning("Admin alert skipped — EMAIL_ADMIN not set")
            return
        subject, html = templates.admin_alert_email(
            title=title, detail=detail, site_url=self.site_url
        )
        await self._send(to_email=to_email, subject=subject, html=html, required=False)

    def _has_resend(self) -> bool:
        return bool(self._settings.resend_api_key)

    def _has_smtp(self) -> bool:
        return bool(
            self._settings.smtp_host
            and self._settings.smtp_user
            and self._settings.smtp_pass
        )

    def _from_header(self, *, for_smtp: bool = False) -> str:
        name = (self._settings.email_from_name or "Chic A Boo").strip()
        if for_smtp and self._settings.smtp_user:
            # Gmail/SMTP usually requires From to match the authenticated mailbox
            return f"{name} <{self._settings.smtp_user.strip()}>"
        raw = (self._settings.email_from or "noreply@chicaboo.co").strip()
        if "<" in raw:
            return raw
        return f"{name} <{raw}>"

    async def _send(self, *, to_email: str, subject: str, html: str, required: bool = False) -> None:
        if self._has_resend():
            try:
                await self._send_resend(to_email=to_email, subject=subject, html=html)
                return
            except AppError:
                if self._has_smtp():
                    logger.warning("Resend failed — falling back to SMTP for %s", to_email)
                    await self._send_smtp(to_email=to_email, subject=subject, html=html)
                    return
                if required:
                    raise
                return

        if self._has_smtp():
            await self._send_smtp(to_email=to_email, subject=subject, html=html)
            return

        if required:
            raise AppError(
                "Email could not be sent — configure RESEND_API_KEY or SMTP credentials",
                code="email_not_configured",
                status_code=503,
            )

        logger.error("Email not sent — no provider configured (to=%s)", to_email)

    async def _send_resend(self, *, to_email: str, subject: str, html: str) -> None:
        payload: dict = {
            "from": self._from_header(),
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        if self._settings.email_reply_to:
            payload["reply_to"] = self._settings.email_reply_to
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {self._settings.resend_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if response.status_code >= 400:
                    logger.error(
                        "Resend error status=%s body=%s",
                        response.status_code,
                        response.text[:500],
                    )
                response.raise_for_status()
            logger.info("Resend email sent to %s subject=%s", to_email, subject)
        except httpx.HTTPError as exc:
            logger.exception("Resend delivery failed for %s", to_email)
            raise AppError(
                "Failed to send email. Please try again.",
                code="email_delivery_failed",
                status_code=503,
            ) from exc

    async def _send_smtp(self, *, to_email: str, subject: str, html: str) -> None:
        try:
            await asyncio.to_thread(self._send_smtp_sync, to_email, subject, html)
        except smtplib.SMTPException as exc:
            logger.exception("SMTP delivery failed for %s", to_email)
            raise AppError(
                "Failed to send email. Please try again.",
                code="email_delivery_failed",
                status_code=503,
            ) from exc

    def _send_smtp_sync(self, to_email: str, subject: str, html: str) -> None:
        from_addr = self._from_header(for_smtp=True)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_email
        if self._settings.email_reply_to:
            msg["Reply-To"] = self._settings.email_reply_to
        msg.attach(MIMEText(html, "html", "utf-8"))

        envelope_from = self._settings.smtp_user.strip()
        port = self._settings.smtp_port
        if self._settings.smtp_secure:
            with smtplib.SMTP_SSL(self._settings.smtp_host, port, timeout=30) as server:
                server.login(self._settings.smtp_user, self._settings.smtp_pass)
                server.sendmail(envelope_from, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(self._settings.smtp_host, port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._settings.smtp_user, self._settings.smtp_pass)
                server.sendmail(envelope_from, [to_email], msg.as_string())

        logger.info("SMTP email sent to %s subject=%s", to_email, subject)

"""Transactional email via Resend or SMTP."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from app.config import Settings
from app.identity.core.exceptions import AppError

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send_verification_otp(self, *, to_email: str, otp: str) -> None:
        subject = "Verify your Chic A Boo account"
        html = (
            f"<p>Your verification code is:</p>"
            f"<h2 style='letter-spacing:4px'>{otp}</h2>"
            f"<p>This code expires in {self._settings.otp_ttl_seconds // 60} minutes.</p>"
            "<p>If you did not create an account, you can ignore this email.</p>"
        )
        await self._send(to_email=to_email, subject=subject, html=html, required=True)

    async def send_password_reset(self, *, to_email: str, reset_token: str) -> None:
        subject = "Reset your Chic A Boo password"
        html = (
            "<p>Use the token below to reset your password:</p>"
            f"<p><code>{reset_token}</code></p>"
            f"<p>This link expires in {self._settings.password_reset_ttl_seconds // 60} minutes.</p>"
        )
        await self._send(to_email=to_email, subject=subject, html=html, required=True)

    def _has_resend(self) -> bool:
        return bool(self._settings.resend_api_key)

    def _has_smtp(self) -> bool:
        return bool(
            self._settings.smtp_host
            and self._settings.smtp_user
            and self._settings.smtp_pass
        )

    async def _send(self, *, to_email: str, subject: str, html: str, required: bool = False) -> None:
        if self._has_resend():
            await self._send_resend(to_email=to_email, subject=subject, html=html)
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
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {self._settings.resend_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": self._settings.email_from,
                        "to": [to_email],
                        "subject": subject,
                        "html": html,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.exception("Resend delivery failed for %s", to_email)
            raise AppError(
                "Failed to send verification email. Please try again.",
                code="email_delivery_failed",
                status_code=503,
            ) from exc

    async def _send_smtp(self, *, to_email: str, subject: str, html: str) -> None:
        try:
            await asyncio.to_thread(self._send_smtp_sync, to_email, subject, html)
        except smtplib.SMTPException as exc:
            logger.exception("SMTP delivery failed for %s", to_email)
            raise AppError(
                "Failed to send verification email. Please try again.",
                code="email_delivery_failed",
                status_code=503,
            ) from exc

    def _send_smtp_sync(self, to_email: str, subject: str, html: str) -> None:
        from_addr = self._settings.email_from or self._settings.smtp_user
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_email
        msg.attach(MIMEText(html, "html", "utf-8"))

        port = self._settings.smtp_port
        if self._settings.smtp_secure:
            with smtplib.SMTP_SSL(self._settings.smtp_host, port, timeout=30) as server:
                server.login(self._settings.smtp_user, self._settings.smtp_pass)
                server.sendmail(from_addr, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(self._settings.smtp_host, port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._settings.smtp_user, self._settings.smtp_pass)
                server.sendmail(from_addr, [to_email], msg.as_string())

        logger.info("SMTP email sent to %s subject=%s", to_email, subject)

"""Transactional email via Resend."""

from __future__ import annotations

import logging

import httpx

from app.config import Settings

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
        )
        await self._send(to_email=to_email, subject=subject, html=html)

    async def send_password_reset(self, *, to_email: str, reset_token: str) -> None:
        subject = "Reset your Chic A Boo password"
        html = (
            "<p>Use the token below to reset your password:</p>"
            f"<p><code>{reset_token}</code></p>"
            f"<p>This link expires in {self._settings.password_reset_ttl_seconds // 60} minutes.</p>"
        )
        await self._send(to_email=to_email, subject=subject, html=html)

    async def _send(self, *, to_email: str, subject: str, html: str) -> None:
        if self._settings.resend_api_key:
            await self._send_resend(to_email=to_email, subject=subject, html=html)
            return

        if self._settings.app_env == "development":
            logger.warning(
                "DEV EMAIL (no RESEND_API_KEY): to=%s subject=%s body=%s",
                to_email,
                subject,
                html,
            )
            return

        logger.error("Email not sent — RESEND_API_KEY not configured")

    async def _send_resend(self, *, to_email: str, subject: str, html: str) -> None:
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

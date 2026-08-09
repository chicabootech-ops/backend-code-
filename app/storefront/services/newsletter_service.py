"""Newsletter double opt-in workflow."""

from __future__ import annotations

import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import get_event_bus
from app.events.types import EventType
from app.identity.services.email_service import EmailService


class NewsletterService:
    def __init__(self, session: AsyncSession, email_service: EmailService | None = None) -> None:
        self._session = session
        self._email_service = email_service

    async def subscribe(self, email: str) -> dict[str, str]:
        normalized = email.strip().lower()
        token = secrets.token_urlsafe(32)
        result = await self._session.execute(
            text(
                """
                INSERT INTO commerce.newsletter_subscribers (email, status, confirm_token)
                VALUES (:email, 'pending', :token)
                ON CONFLICT (email) DO UPDATE
                SET confirm_token = CASE
                        WHEN commerce.newsletter_subscribers.status = 'confirmed'
                        THEN commerce.newsletter_subscribers.confirm_token
                        ELSE EXCLUDED.confirm_token
                    END,
                    updated_at = NOW()
                RETURNING status, confirm_token
                """
            ),
            {"email": normalized, "token": token},
        )
        row = result.mappings().one()
        if row["status"] == "pending" and self._email_service:
            await self._email_service.send_newsletter_confirmation(
                to_email=normalized, confirm_token=str(row["confirm_token"])
            )
        return {
            "status": str(row["status"]),
            "message": (
                "You're already subscribed."
                if row["status"] == "confirmed"
                else "Check your email to confirm your subscription."
            ),
        }

    async def confirm(self, token: str) -> dict[str, str]:
        result = await self._session.execute(
            text(
                """
                UPDATE commerce.newsletter_subscribers
                SET status = 'confirmed', confirmed_at = COALESCE(confirmed_at, NOW()),
                    confirm_token = NULL
                WHERE confirm_token = :token
                RETURNING email
                """
            ),
            {"token": token},
        )
        if not result.first():
            return {"status": "invalid", "message": "This confirmation link is invalid or expired."}
        await get_event_bus().publish(EventType.NEWSLETTER_SUBSCRIBED, {})
        return {"status": "confirmed", "message": "Your newsletter subscription is confirmed."}

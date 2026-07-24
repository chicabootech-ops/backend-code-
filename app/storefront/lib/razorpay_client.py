"""Minimal async Razorpay client.

Talks to the Razorpay REST API directly with httpx (Basic auth = key_id:key_secret)
and verifies signatures with stdlib HMAC-SHA256 — no third-party SDK required, which
keeps the dependency surface small and consistent with the rest of the backend
(email delivery already uses httpx the same way).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_API_BASE = "https://api.razorpay.com/v1"


class PaymentGatewayError(Exception):
    """Raised when the payment gateway is unreachable or returns an error."""

    def __init__(self, message: str, *, code: str = "payment_gateway_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class RazorpayClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.razorpay_configured

    @property
    def key_id(self) -> str:
        return self._settings.razorpay_key_id

    def _auth(self) -> tuple[str, str]:
        return (self._settings.razorpay_key_id, self._settings.razorpay_key_secret)

    def _require_configured(self) -> None:
        if not self.configured:
            raise PaymentGatewayError(
                "Online payments are not configured. Please try again later.",
                code="payment_not_configured",
            )

    async def create_order(
        self,
        *,
        amount_paise: int,
        receipt: str,
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a Razorpay order. `amount_paise` is charged in INR paise."""
        self._require_configured()
        payload: dict[str, Any] = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
        }
        if notes:
            payload["notes"] = notes
        return await self._post("/orders", payload)

    async def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        self._require_configured()
        return await self._get(f"/payments/{payment_id}")

    async def create_refund(self, payment_id: str, *, amount_paise: int | None = None) -> dict[str, Any]:
        self._require_configured()
        payload: dict[str, Any] = {}
        if amount_paise is not None:
            payload["amount"] = amount_paise
        return await self._post(f"/payments/{payment_id}/refund", payload)

    def verify_checkout_signature(
        self, *, order_id: str, payment_id: str, signature: str
    ) -> bool:
        """Verify the signature returned to the browser by Razorpay Checkout."""
        expected = self._hmac(f"{order_id}|{payment_id}", self._settings.razorpay_key_secret)
        return hmac.compare_digest(expected, signature or "")

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str) -> bool:
        """Verify the X-Razorpay-Signature header on a webhook payload."""
        secret = self._settings.razorpay_webhook_secret
        if not secret:
            logger.warning("Razorpay webhook secret not configured — rejecting webhook")
            return False
        expected = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    @staticmethod
    def _hmac(message: str, secret: str) -> str:
        return hmac.new(
            secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.post(
                    f"{_API_BASE}{path}", json=payload, auth=self._auth()
                )
                return self._handle(response)
        except httpx.HTTPError as exc:
            logger.exception("Razorpay POST %s failed", path)
            raise PaymentGatewayError("Could not reach payment gateway.") from exc

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.get(f"{_API_BASE}{path}", auth=self._auth())
                return self._handle(response)
        except httpx.HTTPError as exc:
            logger.exception("Razorpay GET %s failed", path)
            raise PaymentGatewayError("Could not reach payment gateway.") from exc

    @staticmethod
    def _handle(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = body.get("error", {}).get("description", "")
            except Exception:  # noqa: BLE001
                detail = response.text[:300]
            logger.error("Razorpay error status=%s detail=%s", response.status_code, detail)
            raise PaymentGatewayError(detail or "Payment gateway request failed.")
        return response.json()

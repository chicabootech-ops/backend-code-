"""Minimal async Razorpay client.

Talks to the Razorpay REST API directly with httpx (Basic auth = key_id:key_secret)
and verifies signatures with stdlib HMAC-SHA256 — no third-party SDK required, which
keeps the dependency surface small and consistent with the rest of the backend
(email delivery already uses httpx the same way).

Two things here matter for correctness rather than tidiness:

*   **Timeouts are a distinct exception.** ``PaymentGatewayTimeout`` means "we do
    not know what happened"; ``PaymentGatewayError`` means the gateway gave us a
    definite answer. Callers must not collapse the first into a failed payment —
    that is how a debited customer gets told their payment failed.

*   **One shared connection pool.** A per-call ``AsyncClient`` paid a fresh TLS
    handshake on every request, which both slowed checkout and widened the window
    for the timeouts above.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_API_BASE = "https://api.razorpay.com/v1"

#: Connect fast, but give the gateway room to answer — Razorpay's own guidance is
#: that reads can be slow under load, and a premature client-side timeout creates
#: exactly the ambiguity this module exists to avoid.
_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)
_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)


class PaymentGatewayError(Exception):
    """The gateway gave a definite error response."""

    def __init__(self, message: str, *, code: str = "payment_gateway_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class PaymentGatewayTimeout(PaymentGatewayError):
    """We could not reach the gateway, or it did not answer in time.

    Distinct from :class:`PaymentGatewayError` because the outcome is *unknown*.
    A caller must route this to ``verification_required``, never to ``failed``.
    """

    def __init__(self, message: str = "The payment provider did not respond in time.") -> None:
        super().__init__(message, code="payment_gateway_timeout")


class RazorpayClient:
    #: Shared across instances; the client is per-process, not per-request.
    _client: httpx.AsyncClient | None = None
    _client_lock = asyncio.Lock()

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

    @classmethod
    async def _get_client(cls) -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed:
            async with cls._client_lock:
                if cls._client is None or cls._client.is_closed:
                    cls._client = httpx.AsyncClient(
                        base_url=_API_BASE, timeout=_TIMEOUT, limits=_LIMITS
                    )
        return cls._client

    @classmethod
    async def aclose(cls) -> None:
        """Close the shared pool. Called from the app lifespan."""
        if cls._client is not None and not cls._client.is_closed:
            await cls._client.aclose()
        cls._client = None

    # ------------------------------------------------------------------ #
    # Orders & payments
    # ------------------------------------------------------------------ #
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

    async def fetch_order(self, order_id: str) -> dict[str, Any]:
        self._require_configured()
        return await self._get(f"/orders/{order_id}")

    async def fetch_order_payments(self, order_id: str) -> list[dict[str, Any]]:
        """Every payment Razorpay has recorded against one of our orders.

        This is the backbone of reconciliation: when a callback never arrived we
        have an order id but no payment id, and this is the only way to learn
        whether the customer's money actually moved.
        """
        self._require_configured()
        body = await self._get(f"/orders/{order_id}/payments")
        items = body.get("items")
        return items if isinstance(items, list) else []

    async def capture_payment(self, payment_id: str, *, amount_paise: int) -> dict[str, Any]:
        """Explicitly capture an authorized payment."""
        self._require_configured()
        return await self._post(
            f"/payments/{payment_id}/capture",
            {"amount": amount_paise, "currency": "INR"},
        )

    # ------------------------------------------------------------------ #
    # Refunds
    # ------------------------------------------------------------------ #
    async def create_refund(
        self,
        payment_id: str,
        *,
        amount_paise: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self._require_configured()
        payload: dict[str, Any] = {}
        if amount_paise is not None:
            payload["amount"] = amount_paise
        # Razorpay honours this header on refund creation, so a retried request
        # returns the original refund instead of issuing a second one.
        headers = {"X-Razorpay-Idempotency": idempotency_key} if idempotency_key else None
        return await self._post(f"/payments/{payment_id}/refund", payload, headers=headers)

    async def fetch_refund(self, refund_id: str) -> dict[str, Any]:
        self._require_configured()
        return await self._get(f"/refunds/{refund_id}")

    # ------------------------------------------------------------------ #
    # Signatures
    # ------------------------------------------------------------------ #
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
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    @staticmethod
    def _hmac(message: str, secret: str) -> str:
        return hmac.new(
            secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #
    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.post(
                path, json=payload, auth=self._auth(), headers=headers
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Deliberately not logged as an error with a stack trace: this is an
            # expected, recoverable condition that reconciliation resolves.
            logger.warning("Razorpay POST %s unreachable: %s", path, type(exc).__name__)
            raise PaymentGatewayTimeout() from exc
        except httpx.HTTPError as exc:
            logger.exception("Razorpay POST %s failed", path)
            raise PaymentGatewayError("Could not reach payment gateway.") from exc
        return self._handle(response)

    async def _get(self, path: str) -> dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.get(path, auth=self._auth())
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("Razorpay GET %s unreachable: %s", path, type(exc).__name__)
            raise PaymentGatewayTimeout() from exc
        except httpx.HTTPError as exc:
            logger.exception("Razorpay GET %s failed", path)
            raise PaymentGatewayError("Could not reach payment gateway.") from exc
        return self._handle(response)

    @staticmethod
    def _handle(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            detail = ""
            code = "payment_gateway_error"
            try:
                body = response.json()
                error = body.get("error", {}) or {}
                detail = error.get("description", "")
                code = error.get("reason") or error.get("code") or code
            except Exception:  # noqa: BLE001
                detail = response.text[:300]
            logger.error(
                "Razorpay error status=%s code=%s detail=%s",
                response.status_code,
                code,
                detail,
            )
            # 5xx and 429 are "ask again later", not "this payment is dead".
            if response.status_code >= 500 or response.status_code == 429:
                raise PaymentGatewayTimeout(
                    detail or "The payment provider is temporarily unavailable."
                )
            raise PaymentGatewayError(detail or "Payment gateway request failed.", code=code)
        return response.json()

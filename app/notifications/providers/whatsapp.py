"""WhatsApp Business Platform provider (Meta Cloud API).

Sends template messages via `POST /{version}/{phone_number_id}/messages` and
classifies Meta's error codes into the transient/permanent/unknown taxonomy the
fallback engine depends on.

Two things worth knowing about this API:

*   A 200 response only means Meta queued the message. It returns a message id
    and `status: accepted`. Real delivery arrives later on the webhook. This
    provider therefore never returns DELIVERED from `send()`.
*   Authentication-category templates (OTP) place the code in the body variable
    **and** in the button payload for one-tap copy. The code is passed through
    as a template variable and is never logged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings
from app.notifications.providers.base import NotificationProvider
from app.notifications.types import (
    Channel,
    DeliveryStatus,
    ErrorClass,
    OutboundMessage,
    Provider,
    ProviderResult,
)

logger = logging.getLogger(__name__)

#: Meta's template category for OTP templates. Distinct from our Category enum.
TEMPLATE_CATEGORY_AUTHENTICATION = "authentication"

_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)

#: Meta error codes that will never succeed on retry for this recipient/message.
#: Source: Cloud API error reference. Anything unlisted is treated as transient
#: rather than permanent, because wrongly declaring PERMANENT sends a duplicate
#: SMS to someone who already got the WhatsApp.
_PERMANENT_CODES = {
    131026,  # message undeliverable — recipient not on WhatsApp
    131047,  # re-engagement required outside the 24h window
    131051,  # unsupported message type
    132000,  # template param count mismatch
    132001,  # template does not exist / not approved in this language
    132005,  # template text too long
    132007,  # template format character policy violation
    132012,  # template parameter format mismatch
    132015,  # template is paused
    132016,  # template is disabled
    133010,  # phone number not registered
    190,     # access token expired/invalid
    100,     # invalid parameter
    10,      # permission denied
    200,     # permission error
}

#: Explicitly transient: rate limits and Meta-side capacity.
_TRANSIENT_CODES = {
    130429,  # rate limit hit
    131048,  # spam rate limit hit
    131056,  # pair rate limit hit
    133016,  # temporarily blocked for restrictions
    1,       # unknown API error (Meta's own catch-all — retryable)
    2,       # temporary service outage
    4,       # application request limit reached
    80007,   # rate limit
}


class WhatsAppProvider(NotificationProvider):
    provider = Provider.WHATSAPP
    channel = Channel.WHATSAPP

    _client: httpx.AsyncClient | None = None
    _client_lock = asyncio.Lock()

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.whatsapp_configured

    @classmethod
    async def _get_client(cls) -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed:
            async with cls._client_lock:
                if cls._client is None or cls._client.is_closed:
                    cls._client = httpx.AsyncClient(timeout=_TIMEOUT, limits=_LIMITS)
        return cls._client

    @classmethod
    async def aclose(cls) -> None:
        if cls._client is not None and not cls._client.is_closed:
            await cls._client.aclose()
        cls._client = None

    # ------------------------------------------------------------------ #
    # Send
    # ------------------------------------------------------------------ #
    async def send(self, message: OutboundMessage) -> ProviderResult:
        if not self.configured:
            return self._fail(
                "whatsapp_not_configured",
                "WhatsApp is not configured",
                ErrorClass.PERMANENT,
            )
        if message.template is None:
            # Meta only permits free-form text inside a 24h customer service
            # window; everything we send is template-based by design.
            return self._fail(
                "template_missing",
                f"No WhatsApp template mapped for {message.notification_type}",
                ErrorClass.PERMANENT,
            )

        payload = self._build_payload(message)
        url = (
            f"https://graph.facebook.com/{self._settings.whatsapp_api_version}"
            f"/{self._settings.whatsapp_phone_number_id}/messages"
        )
        client = await self._get_client()

        try:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._settings.whatsapp_access_token}"},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # The request may well have been delivered. This is precisely the
            # case that must NOT become an immediate SMS.
            logger.warning(
                "whatsapp_unknown type=%s reason=%s",
                message.notification_type,
                type(exc).__name__,
            )
            return ProviderResult(
                status=DeliveryStatus.UNKNOWN,
                provider=self.provider,
                channel=self.channel,
                error_class=ErrorClass.UNKNOWN,
                failure_code="network_timeout",
                failure_reason="No response from WhatsApp; delivery state unknown",
            )
        except httpx.HTTPError as exc:
            return ProviderResult(
                status=DeliveryStatus.UNKNOWN,
                provider=self.provider,
                channel=self.channel,
                error_class=ErrorClass.UNKNOWN,
                failure_code="transport_error",
                failure_reason=type(exc).__name__,
            )

        return self._interpret(response, message)

    def _build_payload(self, message: OutboundMessage) -> dict[str, Any]:
        template = message.template
        assert template is not None
        components: list[dict[str, Any]] = []

        body_params = [
            {"type": "text", "text": str(message.variables.get(name, ""))}
            for name in template.variable_order
        ]
        if body_params:
            components.append({"type": "body", "parameters": body_params})

        # Authentication templates repeat the code in a copy-code button so the
        # customer can one-tap it. Meta requires this component whenever the
        # approved template declares that button.
        #
        # `template.category` is Meta's vocabulary (authentication/utility/
        # marketing), which is deliberately distinct from our own Category enum
        # (transactional/otp/marketing) — they answer different questions.
        if template.category == TEMPLATE_CATEGORY_AUTHENTICATION:
            code = str(message.variables.get("otp", ""))
            if code:
                components.append(
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [{"type": "text", "text": code}],
                    }
                )

        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": _digits(message.recipient),
            "type": "template",
            "template": {
                "name": template.provider_template_name,
                "language": {"code": template.language or self._settings.whatsapp_default_language},
                "components": components,
            },
        }

    def _interpret(self, response: httpx.Response, message: OutboundMessage) -> ProviderResult:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            body = {}

        if response.status_code < 400:
            messages = body.get("messages") or []
            provider_message_id = (messages[0] or {}).get("id") if messages else None
            logger.info(
                "whatsapp_accepted type=%s message_id=%s",
                message.notification_type,
                provider_message_id,
            )
            # Accepted, NOT delivered. The webhook upgrades this later.
            return ProviderResult(
                status=DeliveryStatus.ACCEPTED,
                provider=self.provider,
                channel=self.channel,
                provider_message_id=provider_message_id,
                raw={"messages": messages},
            )

        error = (body.get("error") or {}) if isinstance(body, dict) else {}
        code = error.get("code")
        subcode = (error.get("error_data") or {}).get("messaging_product")
        detail = error.get("message") or response.text[:200]
        error_class = self._classify(code, response.status_code)

        logger.warning(
            "whatsapp_failed type=%s http=%s code=%s class=%s",
            message.notification_type,
            response.status_code,
            code,
            error_class,
        )
        return ProviderResult(
            # An UNKNOWN classification must not present as FAILED, or the
            # fallback engine would treat it as a definitive non-delivery.
            status=DeliveryStatus.UNKNOWN
            if error_class is ErrorClass.UNKNOWN
            else DeliveryStatus.FAILED,
            provider=self.provider,
            channel=self.channel,
            error_class=error_class,
            failure_code=str(code) if code is not None else f"http_{response.status_code}",
            failure_reason=str(detail)[:400],
            raw={"code": code, "subcode": subcode, "http_status": response.status_code},
        )

    @staticmethod
    def _classify(code: Any, http_status: int) -> ErrorClass:
        try:
            numeric = int(code) if code is not None else None
        except (TypeError, ValueError):
            numeric = None

        if numeric in _PERMANENT_CODES:
            return ErrorClass.PERMANENT
        if numeric in _TRANSIENT_CODES:
            return ErrorClass.TRANSIENT
        if http_status == 429 or http_status >= 500:
            return ErrorClass.TRANSIENT
        if http_status in (401, 403):
            # Bad credentials will not fix themselves; failing over to SMS is
            # the right move while someone rotates the token.
            return ErrorClass.PERMANENT
        if 400 <= http_status < 500:
            return ErrorClass.PERMANENT
        return ErrorClass.UNKNOWN

    def _fail(self, code: str, reason: str, error_class: ErrorClass) -> ProviderResult:
        return ProviderResult(
            status=DeliveryStatus.FAILED,
            provider=self.provider,
            channel=self.channel,
            error_class=error_class,
            failure_code=code,
            failure_reason=reason,
        )


def _digits(recipient: str) -> str:
    """Meta wants the number without '+' or separators."""
    return "".join(ch for ch in recipient if ch.isdigit())

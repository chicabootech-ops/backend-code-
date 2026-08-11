"""Message Central provider — SMS delivery for the fallback channel.

This wraps the existing `MessageCentralClient` rather than replacing it. Two
Message Central products are involved and the distinction matters:

*   **VerifyNow** (`send_otp` / `validate_otp`) generates and validates the OTP
    on Message Central's side. We never see the code. That is fine for an
    SMS-only world, but it makes "the same OTP on WhatsApp and on SMS"
    impossible — we cannot put a code we have never seen into a WhatsApp
    template. Those methods are retained on the client (nothing else uses them,
    and deleting working code buys nothing) but are no longer on the OTP path.

*   **Message Now** — plain SMS send. We supply the full message body, so the
    OTP we generated ourselves is the one that goes out. This is what the
    provider below uses.

The send path is a `GET`/`POST` against a configurable path because the exact
endpoint depends on the account's product tier — see `message_central_sms_path`.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.identity.core.exceptions import AppError
from app.identity.integrations.message_central import BASE_URL, MessageCentralClient
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

_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)

#: Message Central response codes that will not succeed on retry.
_PERMANENT_FRAGMENTS = (
    "INVALID_MOBILE",
    "INVALID_NUMBER",
    "INVALID_COUNTRY",
    "INVALID_SENDER",
    "BLOCKED",
    "DND",
    "TEMPLATE_NOT_FOUND",
    "INVALID_TEMPLATE",
    "UNAUTHORIZED",
    "INSUFFICIENT_BALANCE",
)

_TRANSIENT_FRAGMENTS = (
    "RATE_LIMIT",
    "THROTTL",
    "TIMEOUT",
    "TEMPORARY",
    "TRY_AGAIN",
)


class MessageCentralProvider(NotificationProvider):
    provider = Provider.MESSAGE_CENTRAL
    channel = Channel.SMS

    def __init__(self, settings: Settings, client: MessageCentralClient | None = None) -> None:
        self._settings = settings
        #: Reuses the existing client purely for its cached auth token, so token
        #: handling and credential semantics stay in one place.
        self._client = client or MessageCentralClient(settings)

    @property
    def configured(self) -> bool:
        return bool(self._settings.message_central_enabled and self._client.configured)

    async def send(self, message: OutboundMessage) -> ProviderResult:
        if not self.configured:
            return ProviderResult(
                status=DeliveryStatus.FAILED,
                provider=self.provider,
                channel=self.channel,
                error_class=ErrorClass.PERMANENT,
                failure_code="message_central_not_configured",
                failure_reason="Message Central is not configured",
            )

        body = self._render(message)
        if not body:
            return ProviderResult(
                status=DeliveryStatus.FAILED,
                provider=self.provider,
                channel=self.channel,
                error_class=ErrorClass.PERMANENT,
                failure_code="template_missing",
                failure_reason=f"No SMS body mapped for {message.notification_type}",
            )

        try:
            token = await self._client._get_auth_token()  # noqa: SLF001
        except AppError as exc:
            # Auth failures are the provider's, not the recipient's — but they
            # will not resolve on retry either, so this is the end of the line.
            return ProviderResult(
                status=DeliveryStatus.FAILED,
                provider=self.provider,
                channel=self.channel,
                error_class=ErrorClass.PERMANENT,
                failure_code=getattr(exc, "code", "sms_auth_failed"),
                failure_reason="Message Central authentication failed",
            )

        national, country = _split_number(
            message.recipient, self._settings.message_central_country_code
        )
        params: dict[str, Any] = {
            "countryCode": country,
            "customerId": self._settings.message_central_customer_id,
            "flowType": "SMS",
            "mobileNumber": national,
            "message": body,
            "type": "SMS",
        }
        if self._settings.message_central_sender_id:
            params["senderId"] = self._settings.message_central_sender_id

        url = f"{BASE_URL}{self._settings.message_central_sms_path}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    url, params=params, headers={"authToken": token, "accept": "*/*"}
                )
        except (httpx.TimeoutException, httpx.TransportError):
            # Same principle as WhatsApp: a timeout is not a failure. SMS is the
            # last channel, so nothing falls back further — but marking it FAILED
            # would wrongly report a possibly-delivered message as undelivered.
            logger.warning("sms_unknown type=%s reason=timeout", message.notification_type)
            return ProviderResult(
                status=DeliveryStatus.UNKNOWN,
                provider=self.provider,
                channel=self.channel,
                error_class=ErrorClass.UNKNOWN,
                failure_code="network_timeout",
                failure_reason="No response from Message Central",
            )

        return self._interpret(response, message)

    def _interpret(self, response: httpx.Response, message: OutboundMessage) -> ProviderResult:
        try:
            data = response.json() if response.content else {}
        except Exception:  # noqa: BLE001
            data = {}
        nested = data.get("data") if isinstance(data.get("data"), dict) else {}

        provider_message_id = str(
            nested.get("transactionId")
            or nested.get("messageId")
            or nested.get("verificationId")
            or data.get("transactionId")
            or ""
        ) or None
        text = f"{data.get('message', '')} {nested.get('message', '')}".upper()

        if response.status_code < 400 and (provider_message_id or "SUCCESS" in text):
            logger.info(
                "sms_requested type=%s message_id=%s",
                message.notification_type,
                provider_message_id,
            )
            # Message Central acknowledges submission, not handset delivery.
            return ProviderResult(
                status=DeliveryStatus.ACCEPTED,
                provider=self.provider,
                channel=self.channel,
                provider_message_id=provider_message_id,
                raw={"responseCode": data.get("responseCode")},
            )

        error_class = self._classify(text, response.status_code)
        logger.warning(
            "sms_failed type=%s http=%s class=%s",
            message.notification_type,
            response.status_code,
            error_class,
        )
        return ProviderResult(
            status=DeliveryStatus.UNKNOWN
            if error_class is ErrorClass.UNKNOWN
            else DeliveryStatus.FAILED,
            provider=self.provider,
            channel=self.channel,
            error_class=error_class,
            failure_code=str(data.get("responseCode") or f"http_{response.status_code}"),
            failure_reason=(data.get("message") or response.text[:200])[:400],
            raw={"http_status": response.status_code},
        )

    @staticmethod
    def _classify(text: str, http_status: int) -> ErrorClass:
        if any(fragment in text for fragment in _PERMANENT_FRAGMENTS):
            return ErrorClass.PERMANENT
        if any(fragment in text for fragment in _TRANSIENT_FRAGMENTS):
            return ErrorClass.TRANSIENT
        if http_status == 429 or http_status >= 500:
            return ErrorClass.TRANSIENT
        if http_status in (401, 403):
            return ErrorClass.PERMANENT
        if 400 <= http_status < 500:
            return ErrorClass.PERMANENT
        return ErrorClass.UNKNOWN

    def _render(self, message: OutboundMessage) -> str | None:
        """Fill the SMS body template with the notification's variables."""
        template = message.template
        if template is None or not template.body_text:
            return None
        try:
            return template.body_text.format(**message.variables)
        except (KeyError, IndexError):
            # A template referencing a variable the caller did not supply is a
            # configuration error; better to send nothing than a broken message
            # with a literal {placeholder} in it.
            logger.error(
                "sms_template_render_failed type=%s", message.notification_type
            )
            return None


def _split_number(recipient: str, default_country: str) -> tuple[str, str]:
    """Split E.164 into (national, country) as Message Central expects them."""
    digits = "".join(ch for ch in recipient if ch.isdigit())
    country = default_country.lstrip("+")
    if digits.startswith(country) and len(digits) > len(country):
        return digits[len(country) :], country
    return digits, country

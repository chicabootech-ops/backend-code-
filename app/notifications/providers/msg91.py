"""MSG91 provider — SMS delivery.

Replaces the previous SMS provider. The contract with the notification service is
unchanged: classify honestly, never raise for an ordinary provider rejection,
and treat "we do not know" as its own outcome rather than collapsing it into
failure. That last rule is what stops a customer receiving the same OTP twice.

Template resolution differs from the old provider, which took a fully rendered
body. MSG91 renders its own DLT-registered template from named variables, so
`ops.notification_templates.provider_template_id` now carries the MSG91 template
id, and `msg91_template_id` in settings is the fallback for rows that have not
been given one yet.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.identity.integrations.msg91 import Msg91Client, Msg91Error
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

#: MSG91 error text that will not succeed on retry.
_PERMANENT_FRAGMENTS = (
    "INVALID",
    "BLOCKED",
    "DND",
    "UNAUTHORIZED",
    "AUTHKEY",
    "INSUFFICIENT",
    "BALANCE",
    "TEMPLATE",
    "NOT FOUND",
)

_TRANSIENT_FRAGMENTS = (
    "RATE LIMIT",
    "RATELIMIT",
    "THROTTL",
    "TIMEOUT",
    "TEMPORARY",
    "TRY AGAIN",
)


class Msg91Provider(NotificationProvider):
    provider = Provider.MSG91
    channel = Channel.SMS

    def __init__(self, settings: Settings, client: Msg91Client | None = None) -> None:
        self._settings = settings
        self._client = client or Msg91Client(settings)

    @property
    def configured(self) -> bool:
        return self._client.configured

    async def send(self, message: OutboundMessage) -> ProviderResult:
        if not self.configured:
            return self._failed(
                "msg91_not_configured",
                "MSG91 is not configured",
                ErrorClass.PERMANENT,
            )

        template_id = self._template_id(message)
        if not template_id:
            return self._failed(
                "template_missing",
                f"No MSG91 template id for {message.notification_type}",
                ErrorClass.PERMANENT,
            )

        mobile = _msisdn(message.recipient, self._settings.sms_country_code)
        if not mobile:
            return self._failed(
                "invalid_mobile",
                "Recipient is not a usable mobile number",
                ErrorClass.PERMANENT,
            )

        try:
            response = await self._client.send_flow(
                template_id=template_id,
                mobile=mobile,
                variables=_stringify(message.variables),
            )
        except Msg91Error as exc:
            # No response at all. Same principle as the old provider: unknown is
            # not failure, because the message may well have gone out.
            logger.warning(
                "sms_unknown type=%s reason=%s", message.notification_type, exc.code
            )
            return ProviderResult(
                status=DeliveryStatus.UNKNOWN,
                provider=self.provider,
                channel=self.channel,
                error_class=ErrorClass.UNKNOWN,
                failure_code=exc.code,
                failure_reason=exc.message,
            )

        return self._interpret(response, message)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _template_id(self, message: OutboundMessage) -> str | None:
        template = message.template
        if template is not None and template.provider_template_id:
            return template.provider_template_id
        # Falls back to the single configured template so an OTP still sends on
        # a deployment whose template rows have not been populated yet.
        return self._settings.msg91_template_id or None

    def _interpret(self, response: Any, message: OutboundMessage) -> ProviderResult:
        try:
            data = response.json() if response.content else {}
        except Exception:  # noqa: BLE001
            data = {}
        if not isinstance(data, dict):
            data = {}

        # MSG91 answers {"message": "<request id>", "type": "success"} on accept
        # and {"message": "<reason>", "type": "error"} on rejection. HTTP 200 is
        # returned for both, so the body is the only reliable signal.
        result_type = str(data.get("type", "")).lower()
        detail = str(data.get("message", ""))

        if response.status_code < 400 and result_type == "success":
            logger.info(
                "sms_requested type=%s request_id=%s",
                message.notification_type,
                detail,
            )
            # Accepted means MSG91 took the request, not that a handset saw it.
            return ProviderResult(
                status=DeliveryStatus.ACCEPTED,
                provider=self.provider,
                channel=self.channel,
                provider_message_id=detail or None,
                raw={"type": result_type},
            )

        error_class = self._classify(detail.upper(), response.status_code)
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
            failure_code=f"http_{response.status_code}"
            if response.status_code >= 400
            else "msg91_error",
            failure_reason=(detail or str(response.text)[:200])[:400],
            raw={"http_status": response.status_code},
        )

    @staticmethod
    def _classify(text: str, http_status: int) -> ErrorClass:
        if any(fragment in text for fragment in _TRANSIENT_FRAGMENTS):
            return ErrorClass.TRANSIENT
        if any(fragment in text for fragment in _PERMANENT_FRAGMENTS):
            return ErrorClass.PERMANENT
        if http_status == 429 or http_status >= 500:
            return ErrorClass.TRANSIENT
        if http_status in (401, 403):
            return ErrorClass.PERMANENT
        if 400 <= http_status < 500:
            return ErrorClass.PERMANENT
        # A 200 whose body says "error" without a phrase we recognise. Unknown
        # keeps it out of the failure path until reconciliation says otherwise.
        return ErrorClass.UNKNOWN

    def _failed(self, code: str, reason: str, error_class: ErrorClass) -> ProviderResult:
        return ProviderResult(
            status=DeliveryStatus.FAILED,
            provider=self.provider,
            channel=self.channel,
            error_class=error_class,
            failure_code=code,
            failure_reason=reason,
        )


def _stringify(variables: dict[str, Any]) -> dict[str, str]:
    """MSG91 rejects non-string variable values."""
    return {key: str(value) for key, value in variables.items() if value is not None}


def _msisdn(recipient: str, default_country: str) -> str | None:
    """Normalise to digits-with-country-code, which is MSG91's `mobiles` format."""
    digits = "".join(ch for ch in recipient if ch.isdigit())
    if not digits:
        return None
    country = default_country.lstrip("+")
    if not digits.startswith(country):
        digits = f"{country}{digits}"
    # country code + at least a plausible national number
    return digits if len(digits) > len(country) + 5 else None

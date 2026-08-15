"""MSG91 HTTP client — the SMS transport.

Replaces the previous SMS vendor's client. Two differences shape this file:

*   **No token exchange.** MSG91 authenticates every request with a static
    `authkey` header, so there is no auth call to cache, refresh or fail
    separately. What was a two-request send is now one.

*   **Templates live at MSG91, not here.** The Flow API takes a `template_id`
    plus named variables and renders the body on their side against the
    DLT-registered text. We therefore send *variables*, never a rendered body —
    which also means the OTP travels as one named field rather than embedded in
    a string we assembled.

The OTP itself is still generated and verified by us (`identity.otp_challenges`,
Argon2-hashed). MSG91 offers an OTP product that would generate and validate the
code on their side; it is deliberately unused, because handing code generation to
the provider is what made the previous integration impossible to extend and left
verification dependent on the SMS vendor being reachable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)

#: Retry only these. A network timeout is deliberately absent: we cannot know
#: whether MSG91 accepted the message, and re-sending an OTP the customer may
#: already have received is worse than reporting an unknown outcome.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.25


class Msg91Error(Exception):
    """Transport-level failure. Provider-level rejections are not exceptions."""

    def __init__(self, message: str, *, code: str = "msg91_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class Msg91Client:
    """Thin wrapper over the MSG91 Flow API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        """False when credentials are missing, so the router can skip us.

        `template_id` counts as a credential: the Flow API cannot send without
        one, and a deployment that has the key but no template would fail on
        every single send rather than degrade.
        """
        return bool(
            self._settings.msg91_enabled
            and self._settings.msg91_auth_key
            and self._settings.msg91_template_id
        )

    async def send_flow(
        self,
        *,
        template_id: str,
        mobile: str,
        variables: dict[str, Any],
    ) -> httpx.Response:
        """POST one message to the Flow API.

        `mobile` must be digits including country code and no `+`, which is what
        MSG91 expects. Returns the raw response so the provider can classify it;
        raises only when no response was obtained at all.
        """
        payload: dict[str, Any] = {
            "template_id": template_id,
            "short_url": "0",
            # Ask MSG91 to report submission errors in the response body instead
            # of accepting everything and failing silently downstream.
            "realTimeResponse": "1",
            "recipients": [{"mobiles": mobile, **variables}],
        }
        if self._settings.msg91_sender_id:
            payload["sender"] = self._settings.msg91_sender_id

        url = f"{self._settings.msg91_base_url.rstrip('/')}{self._settings.msg91_flow_path}"
        headers = {
            "authkey": self._settings.msg91_auth_key,
            "Content-Type": "application/json",
            "accept": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    response = await client.post(url, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Not retried — see _RETRYABLE_STATUSES. Surfaced so the provider
                # can report UNKNOWN rather than FAILED.
                last_error = exc
                break

            if response.status_code in _RETRYABLE_STATUSES and attempt < _MAX_ATTEMPTS:
                delay = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "msg91_retry attempt=%s/%s http=%s delay=%.2fs",
                    attempt,
                    _MAX_ATTEMPTS,
                    response.status_code,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            return response

        raise Msg91Error(
            "No response from MSG91",
            code="network_timeout",
        ) from last_error

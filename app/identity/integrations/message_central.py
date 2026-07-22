"""Message Central (VerifyNow) SMS OTP client.

Docs: https://www.messagecentral.com/en-in/product/verify-now/api
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.identity.core.exceptions import AppError

logger = logging.getLogger(__name__)

BASE_URL = "https://cpaas.messagecentral.com"


@dataclass
class SendOtpResult:
    verification_id: str
    timeout_seconds: int
    raw: dict[str, Any]


class MessageCentralClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached_token: str | None = None

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.message_central_customer_id
            and self._settings.message_central_email
            and self._settings.message_central_password
        )

    def _password_key(self) -> str:
        # Message Central expects base64-encoded password as `key`
        raw = self._settings.message_central_password.encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    async def _get_auth_token(self, *, force: bool = False) -> str:
        if self._cached_token and not force:
            return self._cached_token
        if not self.configured:
            raise AppError(
                "Phone verification is not configured",
                code="sms_not_configured",
                status_code=503,
            )

        params = {
            "customerId": self._settings.message_central_customer_id,
            "key": self._password_key(),
            "scope": "NEW",
            "country": self._settings.message_central_country_code,
            "email": self._settings.message_central_email,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{BASE_URL}/auth/v1/authentication/token",
                params=params,
                headers={"accept": "*/*"},
            )
            if response.status_code >= 400:
                logger.error("MessageCentral token error: %s", response.text[:400])
                raise AppError(
                    "Could not authenticate SMS provider",
                    code="sms_auth_failed",
                    status_code=503,
                )
            data = response.json()
        token = data.get("token") or (data.get("data") or {}).get("token")
        if not token:
            logger.error("MessageCentral token missing in response: %s", data)
            raise AppError(
                "Could not authenticate SMS provider",
                code="sms_auth_failed",
                status_code=503,
            )
        self._cached_token = str(token)
        return self._cached_token

    async def send_otp(self, *, mobile_number: str, country_code: str | None = None) -> SendOtpResult:
        """Send OTP via SMS. `mobile_number` should be digits without country code."""
        token = await self._get_auth_token()
        cc = country_code or self._settings.message_central_country_code
        params = {
            "countryCode": cc,
            "flowType": "SMS",
            "mobileNumber": mobile_number,
            "otpLength": str(self._settings.message_central_otp_length),
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{BASE_URL}/verification/v3/send",
                params=params,
                headers={"authToken": token, "accept": "*/*"},
            )
            # Retry once on auth failure
            if response.status_code in (401, 403):
                token = await self._get_auth_token(force=True)
                response = await client.post(
                    f"{BASE_URL}/verification/v3/send",
                    params=params,
                    headers={"authToken": token, "accept": "*/*"},
                )
            data = response.json() if response.content else {}
            nested = data.get("data") if isinstance(data.get("data"), dict) else (
                data if isinstance(data, dict) else {}
            )
            verification_id = str(
                nested.get("verificationId")
                or nested.get("verification_id")
                or data.get("verificationId")
                or ""
            )
            message = str(data.get("message") or nested.get("message") or "")
            # Active OTP already exists — reuse verificationId if present
            already = "ALREADY" in message.upper() or "REQUEST_ALREADY_EXISTS" in message.upper()
            if verification_id and (response.status_code < 400 or already):
                timeout_raw = (
                    nested.get("timeout")
                    or nested.get("timeoutInSec")
                    or self._settings.otp_ttl_seconds
                )
                try:
                    timeout = int(timeout_raw)
                except (TypeError, ValueError):
                    timeout = self._settings.otp_ttl_seconds
                return SendOtpResult(
                    verification_id=verification_id,
                    timeout_seconds=timeout,
                    raw=data if isinstance(data, dict) else {"raw": data},
                )

            logger.error(
                "MessageCentral send failed status=%s body=%s",
                response.status_code,
                data or response.text[:400],
            )
            raise AppError(
                message or "Failed to send SMS OTP. Please try again.",
                code="sms_send_failed",
                status_code=503,
            )


    async def validate_otp(self, *, verification_id: str, code: str) -> bool:
        token = await self._get_auth_token()
        params = {
            "verificationId": verification_id,
            "code": code,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{BASE_URL}/verification/v3/validateOtp",
                params=params,
                headers={"authToken": token, "accept": "*/*"},
            )
            if response.status_code in (401, 403):
                token = await self._get_auth_token(force=True)
                response = await client.get(
                    f"{BASE_URL}/verification/v3/validateOtp",
                    params=params,
                    headers={"authToken": token, "accept": "*/*"},
                )
            data = response.json() if response.content else {}

        # Accept common success shapes
        nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        status = str(
            nested.get("verificationStatus")
            or nested.get("status")
            or data.get("message")
            or data.get("responseCode")
            or ""
        ).upper()
        if response.status_code < 400 and status in (
            "SUCCESS",
            "VERIFIED",
            "VALIDATION_SUCCESS",
            "200",
            "TRUE",
        ):
            return True
        if response.status_code < 400 and str(data.get("responseCode")) in ("200", "200.0"):
            # Some responses only return responseCode 200 on success
            if "INVALID" in status or "FAIL" in status:
                return False
            return True
        logger.info("MessageCentral validate failed: %s", data)
        return False

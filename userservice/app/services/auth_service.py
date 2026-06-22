"""Authentication business logic."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    UnauthorizedError,
    ValidationError,
)
from app.core.security.jwt import JWTManager
from app.core.redis.client import RedisClient
from app.core.security.password import hash_otp, hash_password, verify_otp, verify_password
from app.core.security.tokens import fingerprint_token
from app.repositories.audit_repository import (
    ConsentRepository,
    DeviceRepository,
    LoginHistoryRepository,
    SecurityLogRepository,
)
from app.repositories.email_verification_repository import EmailVerificationRepository
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository, normalize_email
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.schemas.common import ClientContext, MessageResponse
from app.services.email_service import EmailService
from app.services.rate_limit_service import RateLimitService
from app.services.token_service import TokenService


class AuthService:
    def __init__(
        self,
        settings: Settings,
        token_service: TokenService,
        email_service: EmailService,
        rate_limit_service: RateLimitService,
        jwt_manager: JWTManager,
        redis: RedisClient,
    ) -> None:
        self._settings = settings
        self._tokens = token_service
        self._email = email_service
        self._rate_limit = rate_limit_service
        self._jwt = jwt_manager
        self._redis = redis

    async def register(
        self,
        session: AsyncSession,
        body: RegisterRequest,
        ctx: ClientContext,
    ) -> MessageResponse:
        await self._rate_limit.check(
            "register",
            ctx.ip_address or body.email,
            limit=self._settings.rate_limit_register,
            window_seconds=3600,
        )

        if not body.accept_terms:
            raise ValidationError("You must accept the terms and privacy policy")

        users = UserRepository(session)
        email_norm = normalize_email(body.email)
        if await users.get_by_email_normalized(email_norm):
            raise ConflictError("An account with this email already exists", code="email_exists")

        password_hash = hash_password(body.password)
        user = await users.create_user(
            email=body.email,
            password_hash=password_hash,
            first_name=body.first_name,
            last_name=body.last_name,
        )

        consent = ConsentRepository(session)
        for consent_type in ("terms", "privacy"):
            await consent.record(
                user_id=user.id,
                consent_type=consent_type,
                granted=True,
                source="registration",
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )

        # Registration rolls back if OTP email cannot be delivered.
        await self._issue_email_otp(session, email=body.email, purpose="registration")

        security = SecurityLogRepository(session)
        await security.record(
            user_id=user.id,
            event_type="registration",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"email": email_norm},
        )

        return MessageResponse(
            message="Registration successful. Check your email for the verification code."
        )

    async def verify_email(
        self,
        session: AsyncSession,
        body: VerifyEmailRequest,
        ctx: ClientContext,
    ) -> MessageResponse:
        await self._rate_limit.check(
            "verify_email",
            normalize_email(body.email),
            limit=self._settings.rate_limit_verify_email,
            window_seconds=900,
        )

        email_norm = normalize_email(body.email)
        users = UserRepository(session)
        user = await users.get_by_email_normalized(email_norm)
        if not user:
            raise ValidationError("Invalid verification request")

        await self._validate_otp(session, email=body.email, purpose="registration", otp=body.otp)

        await users.mark_email_verified(user)

        security = SecurityLogRepository(session)
        await security.record(
            user_id=user.id,
            event_type="email_verified",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

        return MessageResponse(message="Email verified successfully.")

    async def login(
        self,
        session: AsyncSession,
        body: LoginRequest,
        ctx: ClientContext,
    ) -> TokenResponse:
        await self._rate_limit.check(
            "login",
            ctx.ip_address or "unknown",
            limit=self._settings.rate_limit_login,
            window_seconds=60,
        )

        email_norm = normalize_email(body.email)
        users = UserRepository(session)
        user = await users.get_by_email_normalized(email_norm)

        login_history = LoginHistoryRepository(session)
        security = SecurityLogRepository(session)

        if not user:
            await login_history.record(
                user_id=None,
                email_attempted=email_norm,
                success=False,
                failure_reason="user_not_found",
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                device_id=None,
                request_id=ctx.request_id,
            )
            raise UnauthorizedError("Invalid email or password", code="invalid_credentials")

        now = datetime.now(UTC)
        if user.locked_until:
            locked = user.locked_until
            if locked.tzinfo is None:
                locked = locked.replace(tzinfo=UTC)
            if locked > now:
                raise ForbiddenError(
                    "Account temporarily locked. Try again later.",
                    code="account_locked",
                )

        if user.status == "blocked":
            raise ForbiddenError("Account is blocked", code="account_blocked")
        if user.status == "suspended":
            raise ForbiddenError("Account is suspended", code="account_suspended")
        if not user.email_verified or user.status == "pending_verification":
            raise ForbiddenError(
                "Email not verified. Please verify your email before logging in.",
                code="email_not_verified",
            )

        if not verify_password(body.password, user.password_hash):
            lock_until = None
            if user.failed_login_attempts + 1 >= self._settings.max_failed_login_attempts:
                lock_until = now + timedelta(minutes=self._settings.account_lockout_minutes)
            await users.increment_failed_login(user, lock_until=lock_until)

            if lock_until:
                await security.record(
                    user_id=user.id,
                    event_type="account_locked",
                    ip_address=ctx.ip_address,
                    user_agent=ctx.user_agent,
                    metadata={"failed_attempts": user.failed_login_attempts},
                )

            await login_history.record(
                user_id=user.id,
                email_attempted=email_norm,
                success=False,
                failure_reason="invalid_password",
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                device_id=None,
                request_id=ctx.request_id,
            )
            raise UnauthorizedError("Invalid email or password", code="invalid_credentials")

        device_repo = DeviceRepository(session)
        device = await device_repo.upsert_device(
            user_id=user.id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            device_name=ctx.device_name,
            device_type=ctx.device_type,
        )

        await users.update_last_login(user)

        tokens, _, refresh_row = self._tokens.issue_tokens(user.id)
        await self._tokens.persist_refresh_token(session, refresh_row)

        await login_history.record(
            user_id=user.id,
            email_attempted=email_norm,
            success=True,
            failure_reason=None,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            device_id=device.id,
            request_id=ctx.request_id,
        )
        await security.record(
            user_id=user.id,
            event_type="login_success",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"device_id": str(device.id)},
        )

        return tokens

    async def refresh(
        self,
        session: AsyncSession,
        refresh_token: str,
        ctx: ClientContext,
    ) -> TokenResponse:
        await self._rate_limit.check(
            "refresh",
            ctx.ip_address or "unknown",
            limit=self._settings.rate_limit_refresh,
            window_seconds=60,
        )
        tokens, _user = await self._tokens.rotate_refresh_token(session, refresh_token)
        return tokens

    async def logout(
        self,
        session: AsyncSession,
        *,
        access_token: str | None,
        refresh_token: str | None,
        ctx: ClientContext,
        user_id: uuid.UUID | None,
    ) -> MessageResponse:
        if access_token:
            try:
                await self._tokens.blacklist_access_token(access_token)
            except UnauthorizedError:
                pass

        await self._tokens.revoke_refresh_token(session, refresh_token)

        if user_id:
            security = SecurityLogRepository(session)
            await security.record(
                user_id=user_id,
                event_type="logout",
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )

        return MessageResponse(message="Logged out successfully.")

    async def forgot_password(
        self,
        session: AsyncSession,
        body: ForgotPasswordRequest,
        ctx: ClientContext,
    ) -> MessageResponse:
        await self._rate_limit.check(
            "forgot_password",
            normalize_email(body.email),
            limit=self._settings.rate_limit_forgot_password,
            window_seconds=3600,
        )

        email_norm = normalize_email(body.email)
        users = UserRepository(session)
        user = await users.get_by_email_normalized(email_norm)

        # Always return same message to prevent email enumeration.
        message = MessageResponse(
            message="If an account exists for this email, a reset link has been sent."
        )
        if not user or not user.email_verified:
            return message

        reset_plain = secrets.token_urlsafe(48)
        token_hash = fingerprint_token(reset_plain)
        expires_at = datetime.now(UTC) + timedelta(seconds=self._settings.password_reset_ttl_seconds)

        resets = PasswordResetRepository(session)
        await resets.create(user_id=user.id, token_hash=token_hash, expires_at=expires_at)

        await self._email.send_password_reset(to_email=user.email, reset_token=reset_plain)

        security = SecurityLogRepository(session)
        await security.record(
            user_id=user.id,
            event_type="password_reset_requested",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

        return message

    async def reset_password(
        self,
        session: AsyncSession,
        body: ResetPasswordRequest,
        ctx: ClientContext,
    ) -> MessageResponse:
        await self._rate_limit.check(
            "reset_password",
            ctx.ip_address or "unknown",
            limit=self._settings.rate_limit_reset_password,
            window_seconds=3600,
        )

        token_hash = fingerprint_token(body.token)
        resets = PasswordResetRepository(session)
        reset_row = await resets.get_valid_by_token_hash(token_hash)
        if not reset_row:
            raise ValidationError("Invalid or expired reset token")

        users = UserRepository(session)
        user = await users.get_by_id(reset_row.user_id)
        if not user:
            raise ValidationError("Invalid or expired reset token")

        await users.update_password(user, hash_password(body.new_password))
        await resets.mark_used(reset_row)

        refresh_repo = RefreshTokenRepository(session)
        await refresh_repo.revoke_all_for_user(user.id)

        security = SecurityLogRepository(session)
        await security.record(
            user_id=user.id,
            event_type="password_reset_completed",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

        return MessageResponse(message="Password reset successfully.")

    async def validate_access_token(self, token: str) -> dict:
        payload = self._jwt.decode_token(token, expected_type="access")
        if await self._redis.is_access_token_blacklisted(payload.jti):
            raise UnauthorizedError("Token has been revoked", code="token_revoked")
        return {
            "valid": True,
            "user_id": payload.sub,
            "expires_at": payload.exp,
            "jti": payload.jti,
        }

    async def _issue_email_otp(
        self,
        session: AsyncSession,
        *,
        email: str,
        purpose: str,
    ) -> None:
        otp = f"{secrets.randbelow(1_000_000):06d}"
        otp_hashed = hash_otp(otp)
        expires_at = datetime.now(UTC) + timedelta(seconds=self._settings.otp_ttl_seconds)

        verifications = EmailVerificationRepository(session)
        await verifications.create(
            email=email,
            otp_hash=otp_hashed,
            expires_at=expires_at,
            purpose=purpose,
        )

        email_norm = normalize_email(email)
        await self._redis.set_otp(purpose, email_norm, otp, self._settings.otp_ttl_seconds)

        await self._email.send_verification_otp(to_email=email, otp=otp)

    async def _validate_otp(
        self,
        session: AsyncSession,
        *,
        email: str,
        purpose: str,
        otp: str,
    ) -> None:
        email_norm = normalize_email(email)
        verifications = EmailVerificationRepository(session)
        row = await verifications.get_active(email_normalized=email_norm, purpose=purpose)
        if not row:
            raise ValidationError("Invalid or expired verification code")

        if row.attempts >= row.max_attempts:
            raise ForbiddenError("Too many verification attempts", code="otp_max_attempts")

        redis_otp = await self._redis.get_otp(purpose, email_norm)
        otp_valid = (redis_otp == otp) if redis_otp else verify_otp(otp, row.otp_hash)

        if not otp_valid:
            await verifications.increment_attempts(row)
            raise ValidationError("Invalid verification code")

        await verifications.mark_verified(row)
        await self._redis.delete_otp(purpose, email_norm)

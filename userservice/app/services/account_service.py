"""Account, profile, address, preferences, and security business logic."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, UnauthorizedError, ValidationError
from app.repositories.address_repository import AddressRepository
from app.repositories.audit_repository import (
    ConsentRepository,
    DeviceRepository,
    LoginHistoryRepository,
    SecurityLogRepository,
)
from app.repositories.preferences_repository import PreferencesRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.address import (
    AddressCreateRequest,
    AddressListResponse,
    AddressResponse,
    AddressUpdateRequest,
)
from app.schemas.common import ClientContext, MessageResponse
from app.schemas.onboarding import OnboardingResponse, OnboardingStep
from app.schemas.preferences import PreferencesResponse, PreferencesUpdateRequest
from app.schemas.security import (
    DeviceListResponse,
    DeviceResponse,
    LoginHistoryListResponse,
    LoginHistoryResponse,
)
from app.schemas.user import (
    CurrentUserResponse,
    OnboardingFlagsResponse,
    ProfileUpdateRequest,
    UserProfileResponse,
)
from app.services.onboarding import compute_onboarding_state, touch_onboarding_metadata


class AccountService:
    async def get_me(self, session: AsyncSession, user_id: uuid.UUID) -> CurrentUserResponse:
        return await self.build_current_user_with_onboarding(session, user_id)

    async def update_me(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        body: ProfileUpdateRequest,
    ) -> CurrentUserResponse:
        user = await self._require_user(session, user_id)
        users = UserRepository(session)

        profile_fields: dict[str, Any] = {}
        if body.first_name is not None:
            profile_fields["first_name"] = body.first_name.strip()
        if body.last_name is not None:
            profile_fields["last_name"] = body.last_name.strip()
        if body.gender is not None:
            profile_fields["gender"] = body.gender
        if body.date_of_birth is not None:
            profile_fields["date_of_birth"] = body.date_of_birth

        metadata_patch: dict[str, Any] | None = None
        if body.referral_code is not None:
            metadata_patch = {"referral_code": body.referral_code.strip() or None}

        if not profile_fields and body.phone is None and metadata_patch is None:
            raise ValidationError("No fields to update")

        await users.update_profile(
            user,
            phone=body.phone,
            profile_fields=profile_fields or None,
            metadata_patch=metadata_patch,
        )

        await self._sync_onboarding_metadata(session, user)
        return await self.build_current_user_with_onboarding(session, user_id)

    async def get_onboarding(self, session: AsyncSession, user_id: uuid.UUID) -> OnboardingResponse:
        user = await self._require_user(session, user_id)
        addresses = AddressRepository(session)
        address_count = await addresses.count_for_user(user_id)
        state = compute_onboarding_state(user, address_count=address_count)

        steps = [
            OnboardingStep(key="email_verified", label="Verify email", completed=state["email_verified"]),
            OnboardingStep(key="profile_complete", label="Complete profile", completed=state["profile_complete"]),
            OnboardingStep(key="has_address", label="Add delivery address", completed=state["has_address"]),
            OnboardingStep(
                key="preferences_reviewed",
                label="Review preferences",
                completed=state["preferences_reviewed"],
                required=False,
            ),
        ]
        required_steps = [s for s in steps if s.required]
        completed_required = sum(1 for s in required_steps if s.completed)
        completion_percent = int((completed_required / len(required_steps)) * 100) if required_steps else 100

        return OnboardingResponse(
            **{k: state[k] for k in (
                "email_verified",
                "profile_complete",
                "has_address",
                "preferences_reviewed",
                "shopping_ready",
                "profile_completed_at",
                "address_added_at",
                "preferences_reviewed_at",
                "shopping_ready_at",
            )},
            steps=steps,
            completion_percent=completion_percent,
        )

    async def list_addresses(self, session: AsyncSession, user_id: uuid.UUID) -> AddressListResponse:
        await self._require_user(session, user_id)
        repo = AddressRepository(session)
        rows = await repo.list_for_user(user_id)
        items = [AddressResponse.model_validate(r) for r in rows]
        return AddressListResponse(items=items, total=len(items))

    async def create_address(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        body: AddressCreateRequest,
    ) -> AddressResponse:
        await self._require_user(session, user_id)
        repo = AddressRepository(session)
        count = await repo.count_for_user(user_id)
        is_default = body.is_default or count == 0

        data = body.model_dump(exclude={"is_default"})
        address = await repo.create(user_id=user_id, data=data, is_default=is_default)

        user = await UserRepository(session).get_by_id(user_id)
        if user:
            await self._sync_onboarding_metadata(session, user)

        await session.refresh(address)
        return AddressResponse.model_validate(address)

    async def get_address(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        address_id: uuid.UUID,
    ) -> AddressResponse:
        await self._require_user(session, user_id)
        repo = AddressRepository(session)
        address = await repo.get_for_user(user_id, address_id)
        if not address:
            raise NotFoundError("Address not found", code="address_not_found")
        return AddressResponse.model_validate(address)

    async def update_address(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        address_id: uuid.UUID,
        body: AddressUpdateRequest,
    ) -> AddressResponse:
        await self._require_user(session, user_id)
        repo = AddressRepository(session)
        address = await repo.get_for_user(user_id, address_id)
        if not address:
            raise NotFoundError("Address not found", code="address_not_found")

        updates = body.model_dump(exclude_unset=True, exclude={"is_default"})
        if not updates and body.is_default is None:
            raise ValidationError("No fields to update")

        await repo.update(address, data=updates, is_default=body.is_default)
        await session.refresh(address)
        return AddressResponse.model_validate(address)

    async def delete_address(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        address_id: uuid.UUID,
    ) -> MessageResponse:
        await self._require_user(session, user_id)
        repo = AddressRepository(session)
        address = await repo.get_for_user(user_id, address_id)
        if not address:
            raise NotFoundError("Address not found", code="address_not_found")

        was_default = address.is_default
        await repo.soft_delete(address)

        if was_default:
            remaining = await repo.list_for_user(user_id)
            if remaining:
                await repo.set_default(user_id, remaining[0].id)

        user = await UserRepository(session).get_by_id(user_id)
        if user:
            await self._sync_onboarding_metadata(session, user)

        return MessageResponse(message="Address deleted")

    async def set_default_address(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        address_id: uuid.UUID,
    ) -> AddressResponse:
        await self._require_user(session, user_id)
        repo = AddressRepository(session)
        address = await repo.set_default(user_id, address_id)
        if not address:
            raise NotFoundError("Address not found", code="address_not_found")
        await session.refresh(address)
        return AddressResponse.model_validate(address)

    async def get_preferences(self, session: AsyncSession, user_id: uuid.UUID) -> PreferencesResponse:
        user = await self._require_user(session, user_id)
        if not user.preferences:
            raise NotFoundError("Preferences not found", code="preferences_not_found")
        return PreferencesResponse.model_validate(user.preferences)

    async def update_preferences(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        body: PreferencesUpdateRequest,
        ctx: ClientContext,
    ) -> PreferencesResponse:
        user = await self._require_user(session, user_id)
        if not user.preferences:
            raise NotFoundError("Preferences not found", code="preferences_not_found")

        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise ValidationError("No fields to update")

        prefs_repo = PreferencesRepository(session)
        consent_repo = ConsentRepository(session)

        marketing_fields = {
            "email_marketing": "email_marketing",
            "sms_marketing": "sms_marketing",
            "analytics_tracking": "analytics_tracking",
        }
        for field, consent_type in marketing_fields.items():
            if field in updates:
                await consent_repo.record(
                    user_id=user_id,
                    consent_type=consent_type,
                    granted=updates[field],
                    source="preferences_api",
                    ip_address=ctx.ip_address,
                    user_agent=ctx.user_agent,
                )

        prefs = await prefs_repo.update(user.preferences, updates)

        profile = user.profile
        if profile and "preferences_reviewed_at" not in (profile.metadata_.get("onboarding") or {}):
            await self._sync_onboarding_metadata(session, user, preferences_reviewed=True)

        await session.refresh(prefs)
        return PreferencesResponse.model_validate(prefs)

    async def list_devices(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        current_user_agent: str | None = None,
    ) -> DeviceListResponse:
        await self._require_user(session, user_id)
        repo = DeviceRepository(session)
        devices = await repo.list_for_user(user_id)
        items = []
        for d in devices:
            item = DeviceResponse.model_validate(d)
            item.is_current = bool(current_user_agent and d.user_agent == current_user_agent)
            items.append(item)
        return DeviceListResponse(items=items, total=len(items))

    async def list_login_history(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> LoginHistoryListResponse:
        await self._require_user(session, user_id)
        repo = LoginHistoryRepository(session)
        rows, total = await repo.list_for_user(user_id, limit=limit, offset=offset)
        items = [LoginHistoryResponse.model_validate(r) for r in rows]
        return LoginHistoryListResponse(items=items, total=total)

    async def revoke_device(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        ctx: ClientContext,
    ) -> MessageResponse:
        await self._require_user(session, user_id)
        devices = DeviceRepository(session)
        device = await devices.get_for_user(user_id, device_id)
        if not device:
            raise NotFoundError("Device not found", code="device_not_found")

        await devices.revoke(device)
        security = SecurityLogRepository(session)
        await security.record(
            user_id=user_id,
            event_type="device_revoked",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"device_id": str(device_id)},
        )
        return MessageResponse(message="Device revoked")

    async def logout_all(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        ctx: ClientContext,
    ) -> MessageResponse:
        await self._require_user(session, user_id)
        tokens = RefreshTokenRepository(session)
        await tokens.revoke_all_for_user(user_id)

        security = SecurityLogRepository(session)
        await security.record(
            user_id=user_id,
            event_type="logout_all",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return MessageResponse(message="Logged out from all devices")

    async def _require_user(self, session: AsyncSession, user_id: uuid.UUID):
        users = UserRepository(session)
        user = await users.get_by_id(user_id)
        if not user:
            raise UnauthorizedError("User not found", code="user_not_found")
        return user

    async def _sync_onboarding_metadata(
        self,
        session: AsyncSession,
        user,
        *,
        preferences_reviewed: bool = False,
    ) -> None:
        if not user.profile:
            return

        addresses = AddressRepository(session)
        address_count = await addresses.count_for_user(user.id)
        state = compute_onboarding_state(user, address_count=address_count)

        prefs_reviewed = state["preferences_reviewed"] or preferences_reviewed
        user.profile.metadata_ = touch_onboarding_metadata(
            user.profile.metadata_,
            profile_complete=state["profile_complete"],
            has_address=state["has_address"],
            preferences_reviewed=prefs_reviewed,
            shopping_ready=state["shopping_ready"],
        )
        await session.flush()

    def _build_current_user_response(self, user, session: AsyncSession) -> CurrentUserResponse:
        # Note: address_count requires async; caller should use get_me which has session
        return CurrentUserResponse(
            id=user.id,
            email=user.email,
            customer_number=user.customer_number,
            email_verified=user.email_verified,
            phone=user.phone,
            phone_verified=user.phone_verified,
            status=user.status,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            profile=self._map_profile(user),
            preferences=PreferencesResponse.model_validate(user.preferences) if user.preferences else None,
            onboarding=None,
        )

    async def build_current_user_with_onboarding(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> CurrentUserResponse:
        user = await self._require_user(session, user_id)
        response = self._build_current_user_response(user, session)
        addresses = AddressRepository(session)
        address_count = await addresses.count_for_user(user_id)
        state = compute_onboarding_state(user, address_count=address_count)
        response.onboarding = OnboardingFlagsResponse(
            email_verified=state["email_verified"],
            profile_complete=state["profile_complete"],
            has_address=state["has_address"],
            preferences_reviewed=state["preferences_reviewed"],
            shopping_ready=state["shopping_ready"],
        )
        return response

    def _map_profile(self, user) -> UserProfileResponse | None:
        if not user.profile:
            return None
        metadata = user.profile.metadata_ or {}
        return UserProfileResponse(
            first_name=user.profile.first_name,
            last_name=user.profile.last_name,
            gender=user.profile.gender,
            date_of_birth=user.profile.date_of_birth,
            avatar_url=user.profile.avatar_url,
            referral_code=metadata.get("referral_code"),
        )

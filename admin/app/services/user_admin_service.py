from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import NotFoundError
from app.integrations.r2_client import R2Client
from app.models.commerce import (
    CustomerUser,
    Order,
    UserAddress,
    UserPreferences,
    UserProfile,
)
from app.repositories.audit_repository import AuditRepository
from app.repositories.user_admin_repository import UserAdminRepository
from app.schemas.user import (
    AdminAddressOut,
    AdminPreferencesOut,
    AdminProfileOut,
    AdminUserDetailOut,
    AdminUserOrderOut,
    AdminUserOut,
    UserListResponse,
    UserStatusUpdate,
)

_r2_client: R2Client | None = None


def _get_r2_client() -> R2Client | None:
    global _r2_client
    if not settings.r2_configured:
        return None
    if _r2_client is None:
        _r2_client = R2Client(
            endpoint_url=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket_name=settings.r2_bucket_name,
            get_ttl_seconds=settings.avatar_get_url_ttl_seconds,
        )
    return _r2_client


def _avatar_url(key: str | None) -> str | None:
    if not key:
        return None
    client = _get_r2_client()
    if not client:
        return None
    return client.create_presigned_get(key)


def _user_out(user: CustomerUser, profile: UserProfile | None, order_count: int) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        phone=user.phone,
        first_name=profile.first_name if profile else None,
        last_name=profile.last_name if profile else None,
        status=user.status,
        status_reason=user.status_reason,
        email_verified=user.email_verified,
        customer_number=user.customer_number,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        order_count=order_count,
    )


def _detail_out(
    user: CustomerUser,
    profile: UserProfile | None,
    addresses: list[UserAddress],
    preferences: UserPreferences | None,
    order_count: int,
    orders: list[Order],
) -> AdminUserDetailOut:
    avatar_key = profile.avatar_url if profile else None
    onboarding = None
    if profile and profile.metadata_:
        onboarding = profile.metadata_.get("onboarding")

    return AdminUserDetailOut(
        id=user.id,
        email=user.email,
        phone=user.phone,
        status=user.status,
        status_reason=user.status_reason,
        email_verified=user.email_verified,
        phone_verified=user.phone_verified,
        customer_number=user.customer_number,
        failed_login_attempts=user.failed_login_attempts,
        locked_until=user.locked_until,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
        order_count=order_count,
        profile=AdminProfileOut(
            first_name=profile.first_name if profile else None,
            last_name=profile.last_name if profile else None,
            gender=profile.gender if profile else None,
            date_of_birth=profile.date_of_birth if profile else None,
            avatar_key=avatar_key,
            avatar_url=_avatar_url(avatar_key),
            loyalty_points=profile.loyalty_points if profile else 0,
            onboarding=onboarding,
            created_at=profile.created_at if profile else None,
            updated_at=profile.updated_at if profile else None,
        )
        if profile
        else None,
        preferences=AdminPreferencesOut(
            email_marketing=preferences.email_marketing,
            sms_marketing=preferences.sms_marketing,
            preferred_language=preferences.preferred_language,
            currency=preferences.currency,
            push_notifications=preferences.push_notifications,
            analytics_tracking=preferences.analytics_tracking,
            order_updates_email=preferences.order_updates_email,
            order_updates_sms=preferences.order_updates_sms,
            updated_at=preferences.updated_at,
        )
        if preferences
        else None,
        addresses=[AdminAddressOut.model_validate(a) for a in addresses],
        recent_orders=[
            AdminUserOrderOut(
                id=o.id,
                order_number=o.order_number,
                status=o.status,
                payment_status=o.payment_status,
                fulfillment_status=o.fulfillment_status,
                grand_total_paise=o.grand_total_paise,
                created_at=o.created_at,
            )
            for o in orders
        ],
    )


class UserAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = UserAdminRepository(session)
        self._audit = AuditRepository(session)

    async def list_users(self, **kwargs) -> UserListResponse:
        rows, total = await self._repo.list_users(**kwargs)
        page = kwargs.get("page", 1)
        page_size = kwargs.get("page_size", 20)
        return UserListResponse(
            items=[_user_out(u, p, c) for u, p, c in rows],
            meta={
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
        )

    async def get_user(self, user_id: uuid.UUID) -> AdminUserDetailOut:
        row = await self._repo.get_user_detail(user_id)
        if not row:
            raise NotFoundError("User not found")
        user, profile, addresses, preferences, order_count, orders = row
        return _detail_out(user, profile, addresses, preferences, order_count, orders)

    async def update_status(
        self,
        user_id: uuid.UUID,
        payload: UserStatusUpdate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> AdminUserOut:
        existing = await self._repo.get_user(user_id)
        if not existing:
            raise NotFoundError("User not found")
        user_before = existing[0]

        updated = await self._repo.update_status(user_id, payload.status, payload.status_reason)
        if not updated:
            raise NotFoundError("User not found")

        await self._audit.log(
            admin_id=admin_id,
            entity_type="user",
            entity_id=user_id,
            action=f"status_{payload.status}",
            old_data={"status": user_before.status},
            new_data={"status": payload.status, "status_reason": payload.status_reason},
            domain="user",
            target_user_id=user_id,
            ip_address=ip_address,
        )
        row = await self._repo.get_user(user_id)
        return _user_out(*row) if row else _user_out(updated, existing[1], existing[2])

    async def ban(self, user_id: uuid.UUID, **kwargs) -> AdminUserOut:
        reason = kwargs.pop("reason", None) or "Banned by admin"
        return await self.update_status(
            user_id,
            UserStatusUpdate(status="blocked", status_reason=reason),
            **kwargs,
        )

    async def suspend(self, user_id: uuid.UUID, **kwargs) -> AdminUserOut:
        reason = kwargs.pop("reason", None) or "Suspended by admin"
        return await self.update_status(
            user_id,
            UserStatusUpdate(status="suspended", status_reason=reason),
            **kwargs,
        )

    async def activate(self, user_id: uuid.UUID, **kwargs) -> AdminUserOut:
        return await self.update_status(
            user_id,
            UserStatusUpdate(status="active", status_reason=None),
            **kwargs,
        )

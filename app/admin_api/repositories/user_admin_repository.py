from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.models.commerce import CustomerUser, Order, UserAddress, UserPreferences, UserProfile


class UserAdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_users(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        status: str | None = None,
    ) -> tuple[list[tuple[CustomerUser, UserProfile | None, int]], int]:
        order_count = (
            select(func.count(Order.id))
            .where(Order.user_id == CustomerUser.id)
            .correlate(CustomerUser)
            .scalar_subquery()
        )
        stmt = (
            select(CustomerUser, UserProfile, order_count.label("order_count"))
            .outerjoin(UserProfile, UserProfile.user_id == CustomerUser.id)
            .where(CustomerUser.deleted_at.is_(None))
        )
        count_stmt = select(func.count()).select_from(CustomerUser).where(
            CustomerUser.deleted_at.is_(None)
        )

        if status:
            stmt = stmt.where(CustomerUser.status == status)
            count_stmt = count_stmt.where(CustomerUser.status == status)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    CustomerUser.email.ilike(pattern),
                    CustomerUser.phone.ilike(pattern),
                    UserProfile.first_name.ilike(pattern),
                    UserProfile.last_name.ilike(pattern),
                )
            )
            count_stmt = count_stmt.where(
                or_(
                    CustomerUser.email.ilike(pattern),
                    CustomerUser.phone.ilike(pattern),
                )
            )

        total = int((await self._session.execute(count_stmt)).scalar_one())
        offset = (page - 1) * page_size
        stmt = stmt.order_by(CustomerUser.created_at.desc()).offset(offset).limit(page_size)
        rows = (await self._session.execute(stmt)).all()
        items = [(row[0], row[1], int(row[2] or 0)) for row in rows]
        return items, total

    async def stats(self) -> dict[str, int]:
        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(CustomerUser).where(CustomerUser.deleted_at.is_(None))
                )
            ).scalar_one()
        )

        async def count_status(status: str) -> int:
            stmt = select(func.count()).select_from(CustomerUser).where(
                CustomerUser.deleted_at.is_(None),
                CustomerUser.status == status,
            )
            return int((await self._session.execute(stmt)).scalar_one())

        since = datetime.now(UTC) - timedelta(days=30)
        new_30d = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(CustomerUser).where(
                        CustomerUser.deleted_at.is_(None),
                        CustomerUser.created_at >= since,
                    )
                )
            ).scalar_one()
        )
        with_orders = int(
            (
                await self._session.execute(
                    select(func.count(func.distinct(Order.user_id))).where(Order.user_id.is_not(None))
                )
            ).scalar_one()
        )

        return {
            "total": total,
            "active": await count_status("active"),
            "suspended": await count_status("suspended"),
            "blocked": await count_status("blocked"),
            "pending_verification": await count_status("pending_verification"),
            "new_30d": new_30d,
            "with_orders": with_orders,
        }

    def _filtered_users_stmt(
        self,
        *,
        search: str | None = None,
        status: str | None = None,
    ):
        order_stats = (
            select(
                Order.user_id.label("user_id"),
                func.count(Order.id).label("order_count"),
                func.coalesce(func.sum(Order.grand_total_paise), 0).label("total_spent_paise"),
                func.max(Order.created_at).label("last_order_at"),
            )
            .where(Order.user_id.is_not(None))
            .group_by(Order.user_id)
            .subquery()
        )
        default_address = (
            select(
                UserAddress.user_id.label("user_id"),
                UserAddress.city.label("default_city"),
                UserAddress.state.label("default_state"),
                UserAddress.postal_code.label("default_postal_code"),
            )
            .where(UserAddress.deleted_at.is_(None))
            .order_by(
                UserAddress.user_id,
                UserAddress.is_default.desc(),
                UserAddress.created_at.desc(),
            )
            .distinct(UserAddress.user_id)
            .subquery()
        )

        stmt = (
            select(
                CustomerUser,
                UserProfile,
                UserPreferences,
                func.coalesce(order_stats.c.order_count, 0).label("order_count"),
                func.coalesce(order_stats.c.total_spent_paise, 0).label("total_spent_paise"),
                order_stats.c.last_order_at,
                default_address.c.default_city,
                default_address.c.default_state,
                default_address.c.default_postal_code,
            )
            .outerjoin(UserProfile, UserProfile.user_id == CustomerUser.id)
            .outerjoin(UserPreferences, UserPreferences.user_id == CustomerUser.id)
            .outerjoin(order_stats, order_stats.c.user_id == CustomerUser.id)
            .outerjoin(default_address, default_address.c.user_id == CustomerUser.id)
            .where(CustomerUser.deleted_at.is_(None))
        )

        if status:
            stmt = stmt.where(CustomerUser.status == status)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    CustomerUser.email.ilike(pattern),
                    CustomerUser.phone.ilike(pattern),
                    UserProfile.first_name.ilike(pattern),
                    UserProfile.last_name.ilike(pattern),
                )
            )
        return stmt.order_by(CustomerUser.created_at.desc())

    async def fetch_export_rows(
        self,
        *,
        search: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = (await self._session.execute(self._filtered_users_stmt(search=search, status=status))).all()
        out: list[dict[str, Any]] = []
        for row in rows:
            user: CustomerUser = row[0]
            profile: UserProfile | None = row[1]
            prefs: UserPreferences | None = row[2]
            full_name = " ".join(
                part
                for part in [
                    profile.first_name if profile else None,
                    profile.last_name if profile else None,
                ]
                if part
            ).strip()
            out.append(
                {
                    "user_id": user.id,
                    "customer_number": user.customer_number,
                    "email": user.email,
                    "phone": user.phone,
                    "status": user.status,
                    "status_reason": user.status_reason,
                    "email_verified": user.email_verified,
                    "phone_verified": user.phone_verified,
                    "last_login_at": user.last_login_at,
                    "created_at": user.created_at,
                    "full_name": full_name or None,
                    "gender": profile.gender if profile else None,
                    "date_of_birth": profile.date_of_birth if profile else None,
                    "loyalty_points": profile.loyalty_points if profile else 0,
                    "order_count": int(row[3] or 0),
                    "total_spent_paise": int(row[4] or 0),
                    "last_order_at": row[5],
                    "default_city": row[6],
                    "default_state": row[7],
                    "default_postal_code": row[8],
                    "email_marketing": prefs.email_marketing if prefs else False,
                    "sms_marketing": prefs.sms_marketing if prefs else False,
                    "order_updates_email": prefs.order_updates_email if prefs else True,
                    "order_updates_sms": prefs.order_updates_sms if prefs else False,
                    "push_notifications": prefs.push_notifications if prefs else True,
                    "preferred_language": prefs.preferred_language if prefs else "en",
                    "currency": prefs.currency if prefs else "INR",
                }
            )
        return out

    async def fetch_export_addresses(
        self,
        *,
        user_ids: list[uuid.UUID],
    ) -> list[dict[str, Any]]:
        if not user_ids:
            return []

        rows = (
            await self._session.execute(
                select(UserAddress, CustomerUser.email, CustomerUser.customer_number)
                .join(CustomerUser, CustomerUser.id == UserAddress.user_id)
                .where(
                    UserAddress.user_id.in_(user_ids),
                    UserAddress.deleted_at.is_(None),
                    CustomerUser.deleted_at.is_(None),
                )
                .order_by(CustomerUser.customer_number.asc(), UserAddress.is_default.desc())
            )
        ).all()

        return [
            {
                "customer_number": customer_number,
                "email": email,
                "label": address.label,
                "full_name": address.full_name,
                "phone": address.phone,
                "line1": address.line1,
                "line2": address.line2,
                "landmark": address.landmark,
                "city": address.city,
                "state": address.state,
                "postal_code": address.postal_code,
                "country": address.country,
                "is_default": address.is_default,
                "address_type": address.address_type,
            }
            for address, email, customer_number in rows
        ]

    async def get_user(self, user_id: uuid.UUID) -> tuple[CustomerUser, UserProfile | None, int] | None:
        order_count = (
            select(func.count(Order.id)).where(Order.user_id == user_id).scalar_subquery()
        )
        result = await self._session.execute(
            select(CustomerUser, UserProfile, order_count.label("order_count"))
            .outerjoin(UserProfile, UserProfile.user_id == CustomerUser.id)
            .where(CustomerUser.id == user_id, CustomerUser.deleted_at.is_(None))
        )
        row = result.one_or_none()
        if not row:
            return None
        return row[0], row[1], int(row[2] or 0)

    async def get_user_detail(
        self, user_id: uuid.UUID
    ) -> tuple[CustomerUser, UserProfile | None, list[UserAddress], UserPreferences | None, int, list[Order]] | None:
        base = await self.get_user(user_id)
        if not base:
            return None
        user, profile, order_count = base

        addresses_result = await self._session.execute(
            select(UserAddress)
            .where(UserAddress.user_id == user_id, UserAddress.deleted_at.is_(None))
            .order_by(UserAddress.is_default.desc(), UserAddress.created_at.desc())
        )
        addresses = list(addresses_result.scalars().all())

        prefs_result = await self._session.execute(
            select(UserPreferences).where(UserPreferences.user_id == user_id)
        )
        preferences = prefs_result.scalar_one_or_none()

        orders_result = await self._session.execute(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .limit(10)
        )
        orders = list(orders_result.scalars().all())

        return user, profile, addresses, preferences, order_count, orders

    async def update_status(
        self,
        user_id: uuid.UUID,
        status: str,
        status_reason: str | None,
    ) -> CustomerUser | None:
        result = await self._session.execute(
            update(CustomerUser)
            .where(CustomerUser.id == user_id, CustomerUser.deleted_at.is_(None))
            .values(status=status, status_reason=status_reason)
            .returning(CustomerUser)
        )
        user = result.scalar_one_or_none()
        if user:
            await self._session.refresh(user)
        return user

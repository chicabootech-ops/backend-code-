from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import CustomerUser, Order, UserAddress, UserPreferences, UserProfile


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

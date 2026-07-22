"""User address persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import UserAddress


class AddressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _active_filter(self, user_id: uuid.UUID):
        return (UserAddress.user_id == user_id, UserAddress.deleted_at.is_(None))

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(UserAddress).where(*self._active_filter(user_id))
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserAddress]:
        stmt = (
            select(UserAddress)
            .where(*self._active_filter(user_id))
            .order_by(UserAddress.is_default.desc(), UserAddress.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_user(self, user_id: uuid.UUID, address_id: uuid.UUID) -> UserAddress | None:
        stmt = select(UserAddress).where(
            UserAddress.id == address_id,
            *self._active_filter(user_id),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        data: dict[str, Any],
        is_default: bool,
    ) -> UserAddress:
        if is_default:
            await self._clear_defaults(user_id)

        address = UserAddress(
            id=uuid.uuid4(),
            user_id=user_id,
            is_default=is_default,
            **data,
        )
        self._session.add(address)
        await self._session.flush()
        return address

    async def update(
        self,
        address: UserAddress,
        *,
        data: dict[str, Any],
        is_default: bool | None = None,
    ) -> UserAddress:
        if is_default is True:
            await self._clear_defaults(address.user_id, exclude_id=address.id)
            address.is_default = True
        elif is_default is False:
            address.is_default = False

        for key, value in data.items():
            setattr(address, key, value)

        address.updated_at = datetime.now(UTC)
        await self._session.flush()
        return address

    async def soft_delete(self, address: UserAddress) -> None:
        address.deleted_at = datetime.now(UTC)
        address.is_default = False
        address.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def set_default(self, user_id: uuid.UUID, address_id: uuid.UUID) -> UserAddress | None:
        address = await self.get_for_user(user_id, address_id)
        if not address:
            return None
        await self._clear_defaults(user_id, exclude_id=address_id)
        address.is_default = True
        address.updated_at = datetime.now(UTC)
        await self._session.flush()
        return address

    async def _clear_defaults(self, user_id: uuid.UUID, *, exclude_id: uuid.UUID | None = None) -> None:
        conditions = [UserAddress.user_id == user_id, UserAddress.deleted_at.is_(None), UserAddress.is_default.is_(True)]
        if exclude_id:
            conditions.append(UserAddress.id != exclude_id)
        stmt = update(UserAddress).where(*conditions).values(is_default=False)
        await self._session.execute(stmt)

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_root_active(self) -> list[Category]:
        result = await self._session.execute(
            select(Category)
            .where(
                Category.deleted_at.is_(None),
                Category.status == "active",
                Category.parent_id.is_(None),
                Category.kind == "section",
            )
            .order_by(Category.sort_order, Category.name)
        )
        return list(result.scalars().all())

    async def get_by_slug(self, slug: str) -> Category | None:
        result = await self._session.execute(
            select(Category).where(
                Category.slug == slug,
                Category.deleted_at.is_(None),
                Category.status == "active",
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, category_id: uuid.UUID) -> Category | None:
        result = await self._session.execute(
            select(Category).where(
                Category.id == category_id,
                Category.deleted_at.is_(None),
                Category.status == "active",
            )
        )
        return result.scalar_one_or_none()

    async def list_children(self, parent_id: uuid.UUID) -> list[Category]:
        result = await self._session.execute(
            select(Category)
            .where(
                Category.parent_id == parent_id,
                Category.deleted_at.is_(None),
                Category.status == "active",
            )
            .order_by(Category.sort_order, Category.name)
        )
        return list(result.scalars().all())

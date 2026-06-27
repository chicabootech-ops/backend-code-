from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import Category


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self, *, include_inactive: bool = True) -> list[Category]:
        stmt = select(Category).where(Category.deleted_at.is_(None))
        if not include_inactive:
            stmt = stmt.where(Category.status == "active")
        stmt = stmt.order_by(Category.depth, Category.sort_order, Category.name)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, category_id: uuid.UUID) -> Category | None:
        result = await self._session.execute(
            select(Category).where(
                Category.id == category_id,
                Category.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> Category:
        category = Category(**data)
        self._session.add(category)
        await self._session.flush()
        await self._session.refresh(category)
        return category

    async def update(self, category_id: uuid.UUID, data: dict[str, Any]) -> Category | None:
        category = await self.get_by_id(category_id)
        if not category:
            return None
        for key, value in data.items():
            if key == "metadata":
                setattr(category, "metadata_", value)
            elif value is not None or key in {"parent_id", "description", "image_r2_key"}:
                setattr(category, key if key != "metadata" else "metadata_", value)
        await self._session.flush()
        await self._session.refresh(category)
        return category

    async def soft_delete(self, category_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            update(Category)
            .where(Category.id == category_id, Category.deleted_at.is_(None))
            .values(deleted_at=func.now(), status="inactive")
            .returning(Category.id)
        )
        return result.scalar_one_or_none() is not None

    async def slug_exists(self, slug: str, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(Category.id).where(Category.slug == slug, Category.deleted_at.is_(None))
        if exclude_id:
            stmt = stmt.where(Category.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

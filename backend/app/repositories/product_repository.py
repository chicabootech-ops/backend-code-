from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product, ProductVariant


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active_by_category_ids(
        self,
        category_ids: list[uuid.UUID],
        *,
        limit: int = 12,
    ) -> list[Product]:
        if not category_ids:
            return []
        result = await self._session.execute(
            select(Product)
            .where(
                Product.deleted_at.is_(None),
                Product.status == "active",
                Product.primary_category_id.in_(category_ids),
            )
            .order_by(Product.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_active_by_category(
        self,
        category_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 24,
    ) -> tuple[list[Product], int]:
        base = select(Product).where(
            Product.deleted_at.is_(None),
            Product.status == "active",
            Product.primary_category_id == category_id,
        )
        count_result = await self._session.execute(base)
        total = len(list(count_result.scalars().all()))
        result = await self._session.execute(
            base.order_by(Product.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def get_by_slug(self, slug: str) -> Product | None:
        result = await self._session.execute(
            select(Product).where(
                Product.slug == slug,
                Product.deleted_at.is_(None),
                Product.status == "active",
            )
        )
        return result.scalar_one_or_none()

    async def get_variants(self, product_id: uuid.UUID) -> list[ProductVariant]:
        result = await self._session.execute(
            select(ProductVariant).where(
                ProductVariant.product_id == product_id,
                ProductVariant.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

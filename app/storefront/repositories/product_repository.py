from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.models.product import Product, ProductVariant


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(
        self,
        *,
        page: int = 1,
        page_size: int = 24,
        featured_only: bool = False,
    ) -> tuple[list[Product], int]:
        """Catalog-wide listing — powers shop-all, new arrivals and featured rails."""
        filters = [Product.deleted_at.is_(None), Product.status == "active"]
        if featured_only:
            filters.append(Product.is_featured.is_(True))

        total = await self._session.scalar(
            select(func.count()).select_from(Product).where(*filters)
        )
        result = await self._session.execute(
            select(Product)
            .where(*filters)
            .order_by(Product.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def list_by_slugs(self, slugs: list[str]) -> list[Product]:
        """Unordered fetch; callers re-sort to match their own ranking."""
        if not slugs:
            return []
        result = await self._session.execute(
            select(Product).where(
                Product.slug.in_(slugs),
                Product.deleted_at.is_(None),
                Product.status == "active",
            )
        )
        return list(result.scalars().all())

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

    async def get_variant_by_id(self, variant_id: uuid.UUID) -> ProductVariant | None:
        result = await self._session.execute(
            select(ProductVariant).where(
                ProductVariant.id == variant_id,
                ProductVariant.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, product_id: uuid.UUID) -> Product | None:
        result = await self._session.execute(
            select(Product).where(
                Product.id == product_id,
                Product.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_default_variant(self, product_id: uuid.UUID) -> ProductVariant | None:
        variants = await self.get_variants(product_id)
        active = [v for v in variants if v.status == "active"] or variants
        return active[0] if active else None

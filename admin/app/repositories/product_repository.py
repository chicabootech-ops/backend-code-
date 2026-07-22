from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import Product, ProductVariant


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_products(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        category_id: uuid.UUID | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> tuple[list[Product], int]:
        stmt = select(Product).where(Product.deleted_at.is_(None))
        count_stmt = select(func.count()).select_from(Product).where(Product.deleted_at.is_(None))

        if category_id:
            stmt = stmt.where(Product.primary_category_id == category_id)
            count_stmt = count_stmt.where(Product.primary_category_id == category_id)
        if status:
            stmt = stmt.where(Product.status == status)
            count_stmt = count_stmt.where(Product.status == status)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(Product.name.ilike(pattern))
            count_stmt = count_stmt.where(Product.name.ilike(pattern))

        total = int((await self._session.execute(count_stmt)).scalar_one())
        offset = (page - 1) * page_size
        stmt = stmt.order_by(Product.created_at.desc()).offset(offset).limit(page_size)
        products = list((await self._session.execute(stmt)).scalars().all())
        return products, total

    async def get_by_id(self, product_id: uuid.UUID) -> Product | None:
        result = await self._session.execute(
            select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
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

    async def create_product(self, data: dict[str, Any]) -> Product:
        product = Product(**data)
        self._session.add(product)
        await self._session.flush()
        await self._session.refresh(product)
        return product

    async def create_variant(self, data: dict[str, Any]) -> ProductVariant:
        variant = ProductVariant(**data)
        self._session.add(variant)
        await self._session.flush()
        await self._session.refresh(variant)
        return variant

    async def update_variant(self, variant_id: uuid.UUID, data: dict[str, Any]) -> ProductVariant | None:
        result = await self._session.execute(
            select(ProductVariant).where(
                ProductVariant.id == variant_id,
                ProductVariant.deleted_at.is_(None),
            )
        )
        variant = result.scalar_one_or_none()
        if not variant:
            return None
        for key, value in data.items():
            if value is not None or key in {"compare_at_price_paise", "option_values"}:
                setattr(variant, key, value)
        await self._session.flush()
        await self._session.refresh(variant)
        return variant

    async def update_product(self, product_id: uuid.UUID, data: dict[str, Any]) -> Product | None:
        product = await self.get_by_id(product_id)
        if not product:
            return None
        for key, value in data.items():
            if value is not None:
                attr = "metadata_" if key == "metadata" else key
                setattr(product, attr, value)
        await self._session.flush()
        await self._session.refresh(product)
        return product

    async def soft_delete(self, product_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            update(Product)
            .where(Product.id == product_id, Product.deleted_at.is_(None))
            .values(deleted_at=func.now(), status="inactive")
            .returning(Product.id)
        )
        return result.scalar_one_or_none() is not None

    async def slug_exists(self, slug: str, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(Product.id).where(Product.slug == slug, Product.deleted_at.is_(None))
        if exclude_id:
            stmt = stmt.where(Product.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

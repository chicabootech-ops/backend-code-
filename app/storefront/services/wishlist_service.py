"""Wishlist service for authenticated users."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.lib.media import product_image_url
from app.storefront.models.cart import WishlistItem
from app.storefront.models.product import Product, ProductVariant
from app.storefront.repositories.product_repository import ProductRepository
from app.storefront.schemas.cart import WishlistAdd, WishlistItemOut, WishlistOut
from app.storefront.services.cart_service import CartError


class WishlistService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._products = ProductRepository(session)

    async def list_items(self, user_id: uuid.UUID) -> WishlistOut:
        result = await self._session.execute(
            select(WishlistItem)
            .where(WishlistItem.user_id == user_id)
            .order_by(WishlistItem.created_at.desc())
        )
        rows = list(result.scalars().all())
        items: list[WishlistItemOut] = []
        for row in rows:
            product = await self._products.get_by_id(row.product_id)
            if not product or product.deleted_at is not None:
                continue
            price = 0
            if row.product_variant_id:
                variant = await self._products.get_variant_by_id(row.product_variant_id)
                price = int(variant.price_paise) if variant else 0
            else:
                variant = await self._products.get_default_variant(product.id)
                price = int(variant.price_paise) if variant else 0
            items.append(
                WishlistItemOut(
                    id=row.id,
                    product_id=product.id,
                    variant_id=row.product_variant_id,
                    product_name=product.name,
                    slug=product.slug,
                    price_paise=price,
                    image_url=product_image_url(product.metadata_),
                    created_at=row.created_at,
                )
            )
        return WishlistOut(items=items, total=len(items))

    async def add(self, user_id: uuid.UUID, payload: WishlistAdd) -> WishlistOut:
        product: Product | None = None
        if payload.product_id:
            product = await self._products.get_by_id(payload.product_id)
        elif payload.slug:
            product = await self._products.get_by_slug(payload.slug)
        if not product or product.status != "active":
            raise CartError("Product not found", status_code=404, code="product_not_found")

        variant_id = payload.variant_id
        if variant_id:
            variant = await self._products.get_variant_by_id(variant_id)
            if not variant or variant.product_id != product.id:
                raise CartError("Invalid variant", code="variant_invalid")
        else:
            default = await self._products.get_default_variant(product.id)
            variant_id = default.id if default else None

        # Upsert with NULLS NOT DISTINCT unique index
        await self._session.execute(
            text(
                """
                INSERT INTO commerce.wishlist_items (user_id, product_id, product_variant_id)
                VALUES (:u, :p, :v)
                ON CONFLICT (user_id, product_id, product_variant_id)
                DO NOTHING
                """
            ),
            {"u": str(user_id), "p": str(product.id), "v": str(variant_id) if variant_id else None},
        )
        await self._session.flush()
        return await self.list_items(user_id)

    async def remove(self, user_id: uuid.UUID, item_id: uuid.UUID) -> WishlistOut:
        result = await self._session.execute(
            select(WishlistItem).where(
                WishlistItem.id == item_id,
                WishlistItem.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            raise CartError("Wishlist item not found", status_code=404, code="item_not_found")
        await self._session.delete(row)
        await self._session.flush()
        return await self.list_items(user_id)

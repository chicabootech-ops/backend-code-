"""Persisted shopping cart for authenticated users."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.lib.media import product_image_url
from app.storefront.models.cart import Cart, CartItem
from app.storefront.models.product import Product, ProductVariant
from app.storefront.repositories.product_repository import ProductRepository
from app.storefront.schemas.cart import (
    CartApplyCoupon,
    CartItemAdd,
    CartItemOut,
    CartItemUpdate,
    CartOut,
)
from app.storefront.services.coupon_service import CouponError, CouponService


class CartError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "cart_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class CartService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._products = ProductRepository(session)
        self._coupons = CouponService(session)

    async def get_or_create_cart(self, user_id: uuid.UUID) -> Cart:
        result = await self._session.execute(
            select(Cart).where(
                Cart.user_id == user_id,
                Cart.status == "active",
                Cart.deleted_at.is_(None),
            )
        )
        cart = result.scalar_one_or_none()
        if cart:
            return cart
        cart = Cart(user_id=user_id, status="active", currency="INR")
        self._session.add(cart)
        await self._session.flush()
        return cart

    async def get_cart(self, user_id: uuid.UUID) -> CartOut:
        cart = await self.get_or_create_cart(user_id)
        return await self._serialize(cart)

    async def add_item(self, user_id: uuid.UUID, payload: CartItemAdd) -> CartOut:
        variant = await self._resolve_variant(payload)
        cart = await self.get_or_create_cart(user_id)
        result = await self._session.execute(
            select(CartItem).where(
                CartItem.cart_id == cart.id,
                CartItem.product_variant_id == variant.id,
                CartItem.deleted_at.is_(None),
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.quantity += payload.quantity
            existing.unit_price_paise = int(variant.price_paise)
            existing.updated_at = datetime.now(UTC)
        else:
            self._session.add(
                CartItem(
                    cart_id=cart.id,
                    product_variant_id=variant.id,
                    quantity=payload.quantity,
                    unit_price_paise=int(variant.price_paise),
                )
            )
        await self._session.flush()
        await self._recompute_totals(cart)
        return await self.get_cart(user_id)

    async def update_item(
        self, user_id: uuid.UUID, item_id: uuid.UUID, payload: CartItemUpdate
    ) -> CartOut:
        cart = await self.get_or_create_cart(user_id)
        item = await self._require_item(cart.id, item_id)
        item.quantity = payload.quantity
        item.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._recompute_totals(cart)
        return await self.get_cart(user_id)

    async def remove_item(self, user_id: uuid.UUID, item_id: uuid.UUID) -> CartOut:
        cart = await self.get_or_create_cart(user_id)
        item = await self._require_item(cart.id, item_id)
        item.deleted_at = datetime.now(UTC)
        await self._session.flush()
        await self._recompute_totals(cart)
        return await self.get_cart(user_id)

    async def apply_coupon(self, user_id: uuid.UUID, payload: CartApplyCoupon) -> CartOut:
        cart = await self.get_or_create_cart(user_id)
        await self._recompute_totals(cart)
        try:
            result = await self._coupons.validate(
                payload.code,
                user_id=user_id,
                subtotal_paise=cart.subtotal_paise,
                shipping_paise=0,
            )
        except CouponError as exc:
            raise CartError(exc.message, code=exc.code) from exc
        cart.coupon_id = result.coupon_id
        cart.discount_paise = result.discount_paise
        cart.metadata_ = {**(cart.metadata_ or {}), "coupon_code": result.code}
        await self._session.flush()
        return await self.get_cart(user_id)

    async def clear_coupon(self, user_id: uuid.UUID) -> CartOut:
        cart = await self.get_or_create_cart(user_id)
        cart.coupon_id = None
        cart.discount_paise = 0
        meta = dict(cart.metadata_ or {})
        meta.pop("coupon_code", None)
        cart.metadata_ = meta
        await self._session.flush()
        return await self.get_cart(user_id)

    async def merge_guest_items(
        self, user_id: uuid.UUID, items: list[CartItemAdd]
    ) -> CartOut:
        for item in items:
            try:
                await self.add_item(user_id, item)
            except CartError:
                continue
        return await self.get_cart(user_id)

    async def _resolve_variant(self, payload: CartItemAdd) -> ProductVariant:
        if payload.variant_id:
            variant = await self._products.get_variant_by_id(payload.variant_id)
            if not variant or variant.status != "active":
                raise CartError("Product variant not found", status_code=404, code="variant_not_found")
            return variant
        product: Product | None = None
        if payload.product_id:
            product = await self._products.get_by_id(payload.product_id)
        elif payload.slug:
            product = await self._products.get_by_slug(payload.slug)
        if not product or product.status != "active":
            raise CartError("Product not found", status_code=404, code="product_not_found")
        variant = await self._products.get_default_variant(product.id)
        if not variant:
            raise CartError("Product has no sellable variants", code="no_variants")
        return variant

    async def _require_item(self, cart_id: uuid.UUID, item_id: uuid.UUID) -> CartItem:
        result = await self._session.execute(
            select(CartItem).where(
                CartItem.id == item_id,
                CartItem.cart_id == cart_id,
                CartItem.deleted_at.is_(None),
            )
        )
        item = result.scalar_one_or_none()
        if not item:
            raise CartError("Cart item not found", status_code=404, code="item_not_found")
        return item

    async def _recompute_totals(self, cart: Cart) -> None:
        result = await self._session.execute(
            select(CartItem).where(CartItem.cart_id == cart.id, CartItem.deleted_at.is_(None))
        )
        items = list(result.scalars().all())
        cart.subtotal_paise = sum(i.quantity * i.unit_price_paise for i in items)
        if cart.coupon_id and (cart.metadata_ or {}).get("coupon_code"):
            try:
                coupon = await self._coupons.validate(
                    str(cart.metadata_["coupon_code"]),
                    user_id=cart.user_id,
                    subtotal_paise=cart.subtotal_paise,
                    shipping_paise=0,
                )
                cart.discount_paise = coupon.discount_paise
            except CouponError:
                cart.coupon_id = None
                cart.discount_paise = 0
                meta = dict(cart.metadata_ or {})
                meta.pop("coupon_code", None)
                cart.metadata_ = meta
        elif not cart.coupon_id:
            cart.discount_paise = 0
        cart.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def _serialize(self, cart: Cart) -> CartOut:
        result = await self._session.execute(
            select(CartItem).where(CartItem.cart_id == cart.id, CartItem.deleted_at.is_(None))
        )
        rows = list(result.scalars().all())
        out_items: list[CartItemOut] = []
        for row in rows:
            variant = await self._products.get_variant_by_id(row.product_variant_id)
            if not variant:
                continue
            product = await self._products.get_by_id(variant.product_id)
            if not product:
                continue
            out_items.append(
                CartItemOut(
                    id=row.id,
                    product_id=product.id,
                    variant_id=variant.id,
                    product_name=product.name,
                    variant_title=variant.title,
                    slug=product.slug,
                    sku=variant.sku,
                    quantity=row.quantity,
                    unit_price_paise=row.unit_price_paise,
                    line_total_paise=row.quantity * row.unit_price_paise,
                    image_url=product_image_url(product.metadata_),
                )
            )
        return CartOut(
            id=cart.id,
            status=cart.status,
            currency=cart.currency,
            subtotal_paise=cart.subtotal_paise,
            discount_paise=cart.discount_paise,
            coupon_code=(cart.metadata_ or {}).get("coupon_code"),
            item_count=sum(i.quantity for i in out_items),
            items=out_items,
            updated_at=cart.updated_at,
        )

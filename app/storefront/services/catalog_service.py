from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.lib.media import product_image_url, resolve_storage_url
from app.storefront.models.category import Category
from app.storefront.models.product import Product
from app.storefront.repositories.category_repository import CategoryRepository
from app.storefront.repositories.product_repository import ProductRepository
from app.storefront.schemas.category import StorefrontCategoryOut
from app.storefront.schemas.product import (
    StorefrontProductDetailOut,
    StorefrontProductListResponse,
    StorefrontProductOut,
)
from app.storefront.schemas.section import (
    StorefrontSectionDetailOut,
    StorefrontSectionListResponse,
    StorefrontSectionOut,
)


def category_to_out(category: Category) -> StorefrontCategoryOut:
    return StorefrontCategoryOut(
        id=category.id,
        name=category.name,
        slug=category.slug,
        description=category.description,
        image_url=resolve_storage_url(category.image_r2_key),
        sort_order=category.sort_order,
        kind=getattr(category, "kind", None) or ("section" if category.parent_id is None else "category"),
    )


def product_to_out(
    product: Product,
    *,
    price_paise: int = 0,
    compare_at_price_paise: int | None = None,
    category: Category | None = None,
) -> StorefrontProductOut:
    return StorefrontProductOut(
        id=product.id,
        name=product.name,
        slug=product.slug,
        short_description=product.short_description,
        description=product.description,
        image_url=product_image_url(product.metadata_),
        price_paise=price_paise,
        compare_at_price_paise=compare_at_price_paise,
        primary_category_id=product.primary_category_id,
        category_slug=category.slug if category else None,
        category_name=category.name if category else None,
    )


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._categories = CategoryRepository(session)
        self._products = ProductRepository(session)

    async def _price_map(self, products: list[Product]) -> dict[uuid.UUID, tuple[int, int | None]]:
        prices: dict[uuid.UUID, tuple[int, int | None]] = {}
        for product in products:
            variants = await self._products.get_variants(product.id)
            active = [v for v in variants if v.status == "active"] or variants
            if active:
                prices[product.id] = (active[0].price_paise, active[0].compare_at_price_paise)
            else:
                prices[product.id] = (0, None)
        return prices

    async def list_sections(self, *, preview_limit: int = 10) -> StorefrontSectionListResponse:
        sections = await self._categories.list_root_active()
        items: list[StorefrontSectionOut] = []
        for section in sections:
            children = await self._categories.list_children(section.id)
            child_ids = [c.id for c in children]
            products = await self._products.list_active_by_category_ids(
                child_ids, limit=preview_limit
            )
            prices = await self._price_map(products)
            cat_by_id = {c.id: c for c in children}
            items.append(
                StorefrontSectionOut(
                    **category_to_out(section).model_dump(exclude={"kind"}),
                    products=[
                        product_to_out(
                            p,
                            price_paise=prices[p.id][0],
                            compare_at_price_paise=prices[p.id][1],
                            category=cat_by_id.get(p.primary_category_id),
                        )
                        for p in products
                    ],
                    categories=[category_to_out(c) for c in children],
                )
            )
        return StorefrontSectionListResponse(items=items)

    async def get_section(self, slug: str) -> StorefrontSectionDetailOut | None:
        section = await self._categories.get_by_slug(slug)
        if not section or section.parent_id is not None:
            return None
        children = await self._categories.list_children(section.id)
        child_ids = [c.id for c in children]
        products = await self._products.list_active_by_category_ids(child_ids, limit=48)
        prices = await self._price_map(products)
        cat_by_id = {c.id: c for c in children}
        return StorefrontSectionDetailOut(
            **category_to_out(section).model_dump(exclude={"kind"}),
            products=[
                product_to_out(
                    p,
                    price_paise=prices[p.id][0],
                    compare_at_price_paise=prices[p.id][1],
                    category=cat_by_id.get(p.primary_category_id),
                )
                for p in products
            ],
            categories=[category_to_out(c) for c in children],
        )

    async def get_category_with_products(
        self, slug: str, *, page: int = 1, page_size: int = 24
    ):
        category = await self._categories.get_by_slug(slug)
        if not category:
            return None
        if category.parent_id is None:
            # Treat as section for convenience
            return None

        products, total = await self._products.list_active_by_category(
            category.id, page=page, page_size=page_size
        )
        prices = await self._price_map(products)
        parent = None
        if category.parent_id:
            parent = await self._categories.get_by_id(category.parent_id)

        from app.storefront.schemas.category import (
            StorefrontCategoryDetailOut,
            StorefrontCategoryParentOut,
        )

        return StorefrontCategoryDetailOut(
            **category_to_out(category).model_dump(),
            parent=(
                StorefrontCategoryParentOut(
                    name=parent.name,
                    slug=parent.slug,
                    kind=getattr(parent, "kind", None)
                    or ("section" if parent.parent_id is None else "category"),
                )
                if parent
                else None
            ),
            children=[],
            products=[
                product_to_out(
                    p,
                    price_paise=prices[p.id][0],
                    compare_at_price_paise=prices[p.id][1],
                    category=category,
                )
                for p in products
            ],
            products_total=total,
            products_page=page,
            products_page_size=page_size,
        )

    async def get_product(self, slug: str) -> StorefrontProductDetailOut | None:
        product = await self._products.get_by_slug(slug)
        if not product:
            return None
        variants = await self._products.get_variants(product.id)
        active = [v for v in variants if v.status == "active"] or variants
        price = active[0].price_paise if active else 0
        compare = active[0].compare_at_price_paise if active else None
        category = await self._categories.get_by_id(product.primary_category_id)
        base = product_to_out(
            product,
            price_paise=price,
            compare_at_price_paise=compare,
            category=category,
        )
        return StorefrontProductDetailOut(
            **base.model_dump(),
            brand=product.brand,
            is_featured=product.is_featured,
        )

    async def list_products(
        self,
        *,
        category_slug: str | None = None,
        page: int = 1,
        page_size: int = 24,
    ) -> StorefrontProductListResponse:
        if not category_slug:
            return StorefrontProductListResponse(items=[], total=0, page=page, page_size=page_size)

        category = await self._categories.get_by_slug(category_slug)
        if not category or category.parent_id is None:
            return StorefrontProductListResponse(items=[], total=0, page=page, page_size=page_size)

        products, total = await self._products.list_active_by_category(
            category.id, page=page, page_size=page_size
        )
        prices = await self._price_map(products)
        return StorefrontProductListResponse(
            items=[
                product_to_out(
                    p,
                    price_paise=prices[p.id][0],
                    compare_at_price_paise=prices[p.id][1],
                    category=category,
                )
                for p in products
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.admin_api.core.slug import slugify
from app.admin_api.models.commerce import Product
from app.admin_api.repositories.audit_repository import AuditRepository
from app.admin_api.repositories.category_repository import CategoryRepository
from app.admin_api.repositories.product_repository import ProductRepository
from app.admin_api.schemas.product import ProductCreate, ProductOut, ProductUpdate, ProductVariantOut


class ProductService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ProductRepository(session)
        self._categories = CategoryRepository(session)
        self._audit = AuditRepository(session)

    def _to_out(self, product: Product, variants=None) -> ProductOut:
        meta = product.metadata_ or {}
        image_url = meta.get("image_url") or meta.get("image_r2_key")
        if isinstance(image_url, str):
            image_url = image_url.strip() or None
        else:
            image_url = None
        return ProductOut(
            id=product.id,
            name=product.name,
            slug=product.slug,
            primary_category_id=product.primary_category_id,
            description=product.description,
            short_description=product.short_description,
            brand=product.brand,
            status=product.status,
            is_featured=product.is_featured,
            image_url=image_url,
            metadata=meta,
            variants=[
                ProductVariantOut(
                    id=v.id,
                    sku=v.sku,
                    title=v.title,
                    option_values=v.option_values or {},
                    price_paise=v.price_paise,
                    compare_at_price_paise=v.compare_at_price_paise,
                    status=v.status,
                )
                for v in (variants or [])
            ],
            created_at=product.created_at,
            updated_at=product.updated_at,
        )

    async def list_products(self, **kwargs) -> tuple[list[ProductOut], dict]:
        products, total = await self._repo.list_products(**kwargs)
        page = kwargs.get("page", 1)
        page_size = kwargs.get("page_size", 20)
        items: list[ProductOut] = []
        for product in products:
            variants = await self._repo.get_variants(product.id)
            items.append(self._to_out(product, variants))
        meta = {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }
        return items, meta

    async def get(self, product_id: uuid.UUID) -> ProductOut:
        product = await self._repo.get_by_id(product_id)
        if not product:
            raise NotFoundError("Product not found")
        variants = await self._repo.get_variants(product_id)
        return self._to_out(product, variants)

    async def create(
        self,
        payload: ProductCreate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> ProductOut:
        category = await self._categories.get_by_id(payload.primary_category_id)
        if not category:
            raise ValidationError("Category not found")
        cat_kind = getattr(category, "kind", None) or (
            "section" if category.parent_id is None else "category"
        )
        if cat_kind != "category" or category.parent_id is None:
            raise ValidationError("Products must belong to a category under a section")

        slug = slugify(payload.slug or payload.name)
        if await self._repo.slug_exists(slug):
            raise ConflictError(f"Slug '{slug}' already exists")

        metadata = dict(payload.metadata or {})
        if payload.image_url is not None:
            metadata["image_url"] = payload.image_url.strip() or None

        product = await self._repo.create_product(
            {
                "name": payload.name.strip(),
                "slug": slug,
                "primary_category_id": payload.primary_category_id,
                "description": payload.description,
                "short_description": payload.short_description,
                "brand": payload.brand,
                "status": payload.status,
                "is_featured": payload.is_featured,
                "metadata_": metadata,
            }
        )

        variant_in = payload.variant
        sku = variant_in.sku if variant_in and variant_in.sku else f"{slug}-default"
        price_paise = variant_in.price_paise if variant_in else 0
        variant = await self._repo.create_variant(
            {
                "product_id": product.id,
                "sku": sku,
                "title": variant_in.title if variant_in else "Default",
                "option_values": variant_in.option_values if variant_in else {},
                "price_paise": price_paise,
                "compare_at_price_paise": variant_in.compare_at_price_paise if variant_in else None,
                "status": variant_in.status if variant_in else "active",
            }
        )

        await self._session.execute(
            text(
                """
                INSERT INTO commerce.product_categories (product_id, category_id, is_primary)
                VALUES (:product_id, :category_id, TRUE)
                ON CONFLICT DO NOTHING
                """
            ),
            {"product_id": product.id, "category_id": payload.primary_category_id},
        )

        await self._audit.log(
            admin_id=admin_id,
            entity_type="product",
            entity_id=product.id,
            action="create",
            new_data={"name": product.name, "slug": product.slug},
            ip_address=ip_address,
        )
        return self._to_out(product, [variant])

    async def update(
        self,
        product_id: uuid.UUID,
        payload: ProductUpdate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> ProductOut:
        product = await self._repo.get_by_id(product_id)
        if not product:
            raise NotFoundError("Product not found")

        data: dict = {}
        if payload.name is not None:
            data["name"] = payload.name.strip()
        if payload.slug is not None:
            slug = slugify(payload.slug)
            if await self._repo.slug_exists(slug, exclude_id=product_id):
                raise ConflictError(f"Slug '{slug}' already exists")
            data["slug"] = slug
        if payload.primary_category_id is not None:
            category = await self._categories.get_by_id(payload.primary_category_id)
            if not category:
                raise ValidationError("Category not found")
            cat_kind = getattr(category, "kind", None) or (
                "section" if category.parent_id is None else "category"
            )
            if cat_kind != "category" or category.parent_id is None:
                raise ValidationError("Products must belong to a category under a section")
            data["primary_category_id"] = payload.primary_category_id
        if payload.description is not None:
            data["description"] = payload.description
        if payload.short_description is not None:
            data["short_description"] = payload.short_description
        if payload.brand is not None:
            data["brand"] = payload.brand
        if payload.status is not None:
            data["status"] = payload.status
        if payload.is_featured is not None:
            data["is_featured"] = payload.is_featured
        if payload.metadata is not None or payload.image_url is not None:
            meta = dict(payload.metadata if payload.metadata is not None else (product.metadata_ or {}))
            if payload.image_url is not None:
                meta["image_url"] = payload.image_url.strip() or None
            data["metadata"] = meta

        updated = await self._repo.update_product(product_id, data)
        if not updated:
            raise NotFoundError("Product not found")

        if payload.variant is not None:
            variants = await self._repo.get_variants(product_id)
            if variants:
                await self._repo.update_variant(
                    variants[0].id,
                    {
                        "title": payload.variant.title,
                        "price_paise": payload.variant.price_paise,
                        "compare_at_price_paise": payload.variant.compare_at_price_paise,
                        "status": payload.variant.status,
                        "option_values": payload.variant.option_values,
                    },
                )
            else:
                await self._repo.create_variant(
                    {
                        "product_id": product_id,
                        "sku": payload.variant.sku or f"{updated.slug}-default",
                        "title": payload.variant.title,
                        "option_values": payload.variant.option_values,
                        "price_paise": payload.variant.price_paise,
                        "compare_at_price_paise": payload.variant.compare_at_price_paise,
                        "status": payload.variant.status,
                    }
                )

        await self._audit.log(
            admin_id=admin_id,
            entity_type="product",
            entity_id=product_id,
            action="update",
            new_data=data,
            ip_address=ip_address,
        )
        variants = await self._repo.get_variants(product_id)
        return self._to_out(updated, variants)

    async def delete(
        self,
        product_id: uuid.UUID,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> None:
        product = await self._repo.get_by_id(product_id)
        if not product:
            raise NotFoundError("Product not found")
        await self._repo.soft_delete(product_id)
        await self._audit.log(
            admin_id=admin_id,
            entity_type="product",
            entity_id=product_id,
            action="delete",
            old_data={"name": product.name, "slug": product.slug},
            ip_address=ip_address,
        )

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.lib.media import resolve_storage_url
from app.storefront.models.category import Category
from app.storefront.repositories.category_repository import CategoryRepository
from app.storefront.schemas.category import (
    StorefrontCategoryDetailOut,
    StorefrontCategoryListResponse,
    StorefrontCategoryOut,
    StorefrontCategoryParentOut,
)


def _to_out(category: Category) -> StorefrontCategoryOut:
    resolved = resolve_storage_url(category.image_r2_key)
    return StorefrontCategoryOut(
        id=category.id,
        name=category.name,
        slug=category.slug,
        description=category.description,
        image_url=resolved,
        sort_order=category.sort_order,
    )


class CategoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = CategoryRepository(session)

    async def list_collections(self) -> StorefrontCategoryListResponse:
        categories = await self._repo.list_root_active()
        return StorefrontCategoryListResponse(items=[_to_out(category) for category in categories])

    async def get_by_slug(self, slug: str) -> StorefrontCategoryDetailOut | None:
        category = await self._repo.get_by_slug(slug)
        if not category:
            return None

        parent_out = None
        if category.parent_id:
            parent = await self._repo.get_by_id(category.parent_id)
            if parent:
                parent_out = StorefrontCategoryParentOut(name=parent.name, slug=parent.slug)

        children = await self._repo.list_children(category.id)
        return StorefrontCategoryDetailOut(
            **_to_out(category).model_dump(),
            parent=parent_out,
            children=[_to_out(child) for child in children],
        )

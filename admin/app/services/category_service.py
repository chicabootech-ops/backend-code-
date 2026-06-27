from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.slug import slugify
from app.models.commerce import Category
from app.repositories.audit_repository import AuditRepository
from app.repositories.category_repository import CategoryRepository
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate


def _to_out(category: Category, children: list[CategoryOut] | None = None) -> CategoryOut:
    return CategoryOut(
        id=category.id,
        parent_id=category.parent_id,
        name=category.name,
        slug=category.slug,
        description=category.description,
        image_r2_key=category.image_r2_key,
        sort_order=category.sort_order,
        status=category.status,
        path=category.path,
        depth=category.depth,
        metadata=category.metadata_ or {},
        children=children or [],
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


def _build_tree(categories: list[Category]) -> list[CategoryOut]:
    by_parent: dict[uuid.UUID | None, list[Category]] = {}
    for cat in categories:
        by_parent.setdefault(cat.parent_id, []).append(cat)

    def walk(parent_id: uuid.UUID | None) -> list[CategoryOut]:
        nodes = sorted(by_parent.get(parent_id, []), key=lambda c: (c.sort_order, c.name))
        return [_to_out(cat, walk(cat.id)) for cat in nodes]

    return walk(None)


class CategoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CategoryRepository(session)
        self._audit = AuditRepository(session)

    async def list_tree(self, *, include_inactive: bool = True) -> list[CategoryOut]:
        categories = await self._repo.list_all(include_inactive=include_inactive)
        return _build_tree(categories)

    async def get(self, category_id: uuid.UUID) -> CategoryOut:
        category = await self._repo.get_by_id(category_id)
        if not category:
            raise NotFoundError("Category not found")
        return _to_out(category)

    async def create(
        self,
        payload: CategoryCreate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> CategoryOut:
        slug = slugify(payload.slug or payload.name)
        if await self._repo.slug_exists(slug):
            raise ConflictError(f"Slug '{slug}' already exists")

        if payload.parent_id:
            parent = await self._repo.get_by_id(payload.parent_id)
            if not parent:
                raise ValidationError("Parent category not found")

        category = await self._repo.create(
            {
                "name": payload.name.strip(),
                "slug": slug,
                "parent_id": payload.parent_id,
                "description": payload.description,
                "image_r2_key": payload.image_r2_key,
                "sort_order": payload.sort_order,
                "status": payload.status,
                "metadata_": payload.metadata,
            }
        )
        await self._audit.log(
            admin_id=admin_id,
            entity_type="category",
            entity_id=category.id,
            action="create",
            new_data={"name": category.name, "slug": category.slug},
            ip_address=ip_address,
        )
        return _to_out(category)

    async def update(
        self,
        category_id: uuid.UUID,
        payload: CategoryUpdate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> CategoryOut:
        category = await self._repo.get_by_id(category_id)
        if not category:
            raise NotFoundError("Category not found")

        data: dict = {}
        if payload.name is not None:
            data["name"] = payload.name.strip()
        if payload.slug is not None:
            slug = slugify(payload.slug)
            if await self._repo.slug_exists(slug, exclude_id=category_id):
                raise ConflictError(f"Slug '{slug}' already exists")
            data["slug"] = slug
        if payload.parent_id is not None:
            if payload.parent_id == category_id:
                raise ValidationError("Category cannot be its own parent")
            if payload.parent_id:
                parent = await self._repo.get_by_id(payload.parent_id)
                if not parent:
                    raise ValidationError("Parent category not found")
            data["parent_id"] = payload.parent_id
        if payload.description is not None:
            data["description"] = payload.description
        if payload.image_r2_key is not None:
            data["image_r2_key"] = payload.image_r2_key
        if payload.sort_order is not None:
            data["sort_order"] = payload.sort_order
        if payload.status is not None:
            data["status"] = payload.status
        if payload.metadata is not None:
            data["metadata"] = payload.metadata

        updated = await self._repo.update(category_id, data)
        if not updated:
            raise NotFoundError("Category not found")

        await self._audit.log(
            admin_id=admin_id,
            entity_type="category",
            entity_id=category_id,
            action="update",
            old_data={"name": category.name, "slug": category.slug},
            new_data=data,
            ip_address=ip_address,
        )
        return _to_out(updated)

    async def delete(
        self,
        category_id: uuid.UUID,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> None:
        category = await self._repo.get_by_id(category_id)
        if not category:
            raise NotFoundError("Category not found")
        await self._repo.soft_delete(category_id)
        await self._audit.log(
            admin_id=admin_id,
            entity_type="category",
            entity_id=category_id,
            action="delete",
            old_data={"name": category.name, "slug": category.slug},
            ip_address=ip_address,
        )

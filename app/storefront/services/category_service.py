from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.storefront.models.category import Category
from app.storefront.repositories.category_repository import CategoryRepository
from app.storefront.schemas.category import (
    StorefrontCategoryDetailOut,
    StorefrontCategoryListResponse,
    StorefrontCategoryOut,
    StorefrontCategoryParentOut,
)

DEFAULT_COLLECTION_IMAGE = "/collections/tulips.jpeg"
LEGACY_PLACEHOLDER_IMAGE = "/collections/premium-blooms.jpg"


def _image_url(image_r2_key: str | None) -> str | None:
    if not image_r2_key:
        return None
    key = image_r2_key.strip()
    if not key:
        return None
    if key.startswith("/"):
        return key
    if settings.r2_public_base_url:
        return f"{settings.r2_public_base_url.rstrip('/')}/{key.lstrip('/')}"
    if not (
        settings.r2_endpoint_url
        and settings.r2_access_key
        and settings.r2_secret_key
        and settings.r2_bucket
    ):
        return None
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key,
            aws_secret_access_key=settings.r2_secret_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket, "Key": image_r2_key},
            ExpiresIn=3600,
        )
    except ClientError:
        return None


def _to_out(category: Category) -> StorefrontCategoryOut:
    resolved = _image_url(category.image_r2_key)
    if resolved == LEGACY_PLACEHOLDER_IMAGE:
        resolved = None
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

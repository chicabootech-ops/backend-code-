from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.db.session import get_session
from app.storefront.services.catalog_service import CatalogService
from app.storefront.services.category_service import CategoryService


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = request.app.state.session_factory
    async for session in get_session(session_factory):
        yield session


async def get_category_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CategoryService:
    return CategoryService(db)


async def get_catalog_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CatalogService:
    return CatalogService(db)


CategoryServiceDep = Annotated[CategoryService, Depends(get_category_service)]
CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]

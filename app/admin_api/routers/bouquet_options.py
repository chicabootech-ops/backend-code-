from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from app.admin_api.core.events import catalog_changed
from app.admin_api.core.security.permissions import CatalogWriter
from app.admin_api.dependencies import (
    BouquetAdminServiceDep,
    CatalogCacheDep,
    CurrentAdmin,
)
from app.admin_api.schemas.bouquet import (
    BouquetOptionCreate,
    BouquetOptionListResponse,
    BouquetOptionOut,
    BouquetOptionUpdate,
)

router = APIRouter(prefix="/admin/bouquet-options", tags=["admin-bouquet"])


def _ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )


@router.get("", response_model=BouquetOptionListResponse)
async def list_options(
    _admin: CurrentAdmin,
    service: BouquetAdminServiceDep,
    kind: str | None = Query(default=None, pattern="^(flower|color|wrap)$"),
):
    return await service.list(kind=kind)


@router.get("/{option_id}", response_model=BouquetOptionOut)
async def get_option(
    option_id: uuid.UUID, _admin: CurrentAdmin, service: BouquetAdminServiceDep
):
    return await service.get(option_id)


@router.post("", response_model=BouquetOptionOut, status_code=201)
async def create_option(
    payload: BouquetOptionCreate,
    admin: CatalogWriter,
    service: BouquetAdminServiceDep,
    cache: CatalogCacheDep,
    request: Request,
):
    result = await service.create(payload, admin_id=admin.sub, ip_address=_ip(request))
    await cache.bump()
    await catalog_changed("bouquet_option", "create", result.id)
    return result


@router.patch("/{option_id}", response_model=BouquetOptionOut)
async def update_option(
    option_id: uuid.UUID,
    payload: BouquetOptionUpdate,
    admin: CatalogWriter,
    service: BouquetAdminServiceDep,
    cache: CatalogCacheDep,
    request: Request,
):
    result = await service.update(option_id, payload, admin_id=admin.sub, ip_address=_ip(request))
    await cache.bump()
    await catalog_changed("bouquet_option", "update", option_id)
    return result


@router.delete("/{option_id}", status_code=204)
async def delete_option(
    option_id: uuid.UUID,
    admin: CatalogWriter,
    service: BouquetAdminServiceDep,
    cache: CatalogCacheDep,
    request: Request,
):
    await service.delete(option_id, admin_id=admin.sub, ip_address=_ip(request))
    await cache.bump()
    await catalog_changed("bouquet_option", "delete", option_id)

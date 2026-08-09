from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from app.admin_api.core.events import catalog_changed
from app.admin_api.core.security.permissions import CatalogWriter
from app.admin_api.dependencies import CatalogCacheDep, CurrentAdmin, ProductServiceDep
from app.admin_api.schemas.product import ProductCreate, ProductOut, ProductUpdate

router = APIRouter(prefix="/admin/products", tags=["admin-products"])


def _ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )


@router.get("")
async def list_products(
    _admin: CurrentAdmin,
    service: ProductServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category_id: uuid.UUID | None = None,
    status: str | None = None,
    search: str | None = None,
):
    items, meta = await service.list_products(
        page=page, page_size=page_size, category_id=category_id, status=status, search=search
    )
    return {"items": items, "meta": meta}


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: uuid.UUID, _admin: CurrentAdmin, service: ProductServiceDep):
    return await service.get(product_id)


@router.post("", response_model=ProductOut, status_code=201)
async def create_product(
    payload: ProductCreate,
    admin: CatalogWriter,
    service: ProductServiceDep,
    cache: CatalogCacheDep,
    request: Request,
):
    result = await service.create(payload, admin_id=admin.sub, ip_address=_ip(request))
    await cache.bump()
    await catalog_changed("product", "create", result.id)
    return result


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    admin: CatalogWriter,
    service: ProductServiceDep,
    cache: CatalogCacheDep,
    request: Request,
):
    result = await service.update(product_id, payload, admin_id=admin.sub, ip_address=_ip(request))
    await cache.bump()
    await catalog_changed("product", "update", product_id)
    return result


@router.delete("/{product_id}", status_code=204)
async def delete_product(
    product_id: uuid.UUID,
    admin: CatalogWriter,
    service: ProductServiceDep,
    cache: CatalogCacheDep,
    request: Request,
):
    await service.delete(product_id, admin_id=admin.sub, ip_address=_ip(request))
    await cache.bump()
    await catalog_changed("product", "delete", product_id)

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from app.admin_api.core.events import catalog_changed
from app.admin_api.dependencies import CatalogCacheDep, CurrentAdmin, TestimonialAdminServiceDep
from app.admin_api.schemas.testimonial import (
    TestimonialCreate,
    TestimonialListResponse,
    TestimonialOut,
    TestimonialUpdate,
)

router = APIRouter(prefix="/admin/testimonials", tags=["admin-testimonials"])


def _ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )


@router.get("", response_model=TestimonialListResponse)
async def list_testimonials(
    _admin: CurrentAdmin,
    service: TestimonialAdminServiceDep,
    include_hidden: bool = Query(default=True),
):
    return await service.list(include_hidden=include_hidden)


@router.get("/{testimonial_id}", response_model=TestimonialOut)
async def get_testimonial(
    testimonial_id: uuid.UUID,
    _admin: CurrentAdmin,
    service: TestimonialAdminServiceDep,
):
    return await service.get(testimonial_id)


@router.post("", response_model=TestimonialOut, status_code=201)
async def create_testimonial(
    payload: TestimonialCreate,
    admin: CurrentAdmin,
    service: TestimonialAdminServiceDep,
    cache: CatalogCacheDep,
    request: Request,
):
    result = await service.create(payload, admin_id=admin.sub, ip_address=_ip(request))
    await cache.bump()
    await catalog_changed("testimonial", "create", result.id)
    return result


@router.patch("/{testimonial_id}", response_model=TestimonialOut)
async def update_testimonial(
    testimonial_id: uuid.UUID,
    payload: TestimonialUpdate,
    admin: CurrentAdmin,
    service: TestimonialAdminServiceDep,
    cache: CatalogCacheDep,
    request: Request,
):
    result = await service.update(
        testimonial_id, payload, admin_id=admin.sub, ip_address=_ip(request)
    )
    await cache.bump()
    await catalog_changed("testimonial", "update", testimonial_id)
    return result


@router.delete("/{testimonial_id}", status_code=204)
async def delete_testimonial(
    testimonial_id: uuid.UUID,
    admin: CurrentAdmin,
    service: TestimonialAdminServiceDep,
    cache: CatalogCacheDep,
    request: Request,
):
    await service.delete(testimonial_id, admin_id=admin.sub, ip_address=_ip(request))
    await cache.bump()
    await catalog_changed("testimonial", "delete", testimonial_id)

from __future__ import annotations

from fastapi import APIRouter, Query

from app.storefront.dependencies import CatalogCacheDep, TestimonialServiceDep
from app.storefront.schemas.testimonial import StorefrontTestimonialListResponse

router = APIRouter(prefix="/api/testimonials", tags=["testimonials"])

CACHE_TTL_SECONDS = 300


@router.get("", response_model=StorefrontTestimonialListResponse)
async def list_testimonials(
    service: TestimonialServiceDep,
    cache: CatalogCacheDep,
    limit: int = Query(default=24, ge=1, le=100),
    featured_only: bool = Query(default=False),
) -> StorefrontTestimonialListResponse:
    return await cache.get_or_set(
        f"testimonials:{limit}:{int(featured_only)}",
        CACHE_TTL_SECONDS,
        lambda: service.list_published(limit=limit, featured_only=featured_only),
        model=StorefrontTestimonialListResponse,
    )

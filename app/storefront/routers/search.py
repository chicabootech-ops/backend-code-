from __future__ import annotations

from fastapi import APIRouter, Query

from app.storefront.dependencies import SearchServiceDep
from app.storefront.services.search_service import SearchResponse

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def search_products(
    service: SearchServiceDep,
    q: str = Query("", max_length=120),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    min_price_paise: int | None = Query(default=None, ge=0),
    max_price_paise: int | None = Query(default=None, ge=0),
    sort: str = Query(
        "relevance",
        pattern="^(relevance|price_asc|price_desc|newest|name)$",
    ),
) -> SearchResponse:
    return await service.search(
        q,
        page=page,
        page_size=page_size,
        min_price_paise=min_price_paise,
        max_price_paise=max_price_paise,
        sort=sort,
    )

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.storefront.dependencies import BouquetServiceDep, CatalogCacheDep
from app.storefront.schemas.bouquet import (
    BouquetConfigIn,
    BouquetOptionsResponse,
    BouquetQuoteOut,
)
from app.storefront.services.bouquet_service import BouquetError

router = APIRouter(prefix="/api/bouquet", tags=["bouquet"])

OPTIONS_TTL_SECONDS = 120


@router.get("/options", response_model=BouquetOptionsResponse)
async def list_options(
    service: BouquetServiceDep, cache: CatalogCacheDep
) -> BouquetOptionsResponse:
    return await cache.get_or_set(
        "bouquet:options",
        OPTIONS_TTL_SECONDS,
        service.get_options,
        model=BouquetOptionsResponse,
    )


@router.post("/quote", response_model=BouquetQuoteOut)
async def quote(payload: BouquetConfigIn, service: BouquetServiceDep) -> BouquetQuoteOut:
    """Live price for a configuration — never cached, never trusted from the client."""
    try:
        return await service.quote(payload)
    except BouquetError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"error": exc.message, "code": exc.code}
        ) from exc

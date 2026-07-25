from fastapi import APIRouter, HTTPException, Query

from app.storefront.dependencies import CatalogServiceDep
from app.storefront.schemas.product import StorefrontProductDetailOut, StorefrontProductListResponse

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=StorefrontProductListResponse)
async def list_products(
    service: CatalogServiceDep,
    category: str | None = Query(default=None, description="Category slug"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    sort: str = Query("newest", pattern="^(newest|price_asc|price_desc|name)$"),
    min_price_paise: int | None = Query(default=None, ge=0),
    max_price_paise: int | None = Query(default=None, ge=0),
) -> StorefrontProductListResponse:
    return await service.list_products(
        category_slug=category,
        page=page,
        page_size=page_size,
        sort=sort,
        min_price_paise=min_price_paise,
        max_price_paise=max_price_paise,
    )


@router.get("/{slug}", response_model=StorefrontProductDetailOut)
async def get_product(slug: str, service: CatalogServiceDep) -> StorefrontProductDetailOut:
    product = await service.get_product(slug)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

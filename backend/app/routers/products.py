from fastapi import APIRouter, HTTPException, Query

from app.dependencies import CatalogServiceDep
from app.schemas.product import StorefrontProductDetailOut, StorefrontProductListResponse

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=StorefrontProductListResponse)
async def list_products(
    service: CatalogServiceDep,
    category: str | None = Query(default=None, description="Category slug"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
) -> StorefrontProductListResponse:
    return await service.list_products(category_slug=category, page=page, page_size=page_size)


@router.get("/{slug}", response_model=StorefrontProductDetailOut)
async def get_product(slug: str, service: CatalogServiceDep) -> StorefrontProductDetailOut:
    product = await service.get_product(slug)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

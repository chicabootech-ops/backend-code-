from fastapi import APIRouter, HTTPException, Query

from app.events.bus import get_event_bus
from app.events.handlers import TRENDING_KEY
from app.events.types import EventType
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


@router.get("/featured", response_model=StorefrontProductListResponse)
async def featured_products(
    service: CatalogServiceDep,
    limit: int = Query(8, ge=1, le=24),
) -> StorefrontProductListResponse:
    featured = await service.list_catalog(page=1, page_size=limit, featured_only=True)
    if featured.items:
        return featured
    return await service.list_catalog(page=1, page_size=limit, sort="newest")


@router.get("/trending", response_model=StorefrontProductListResponse)
async def trending_products(
    service: CatalogServiceDep,
    limit: int = Query(8, ge=1, le=24),
) -> StorefrontProductListResponse:
    """Ranked by real product views recorded on the Redis event stream.

    Falls back to the newest products while the counters are still cold, so the
    homepage rail is never empty on a fresh deploy.
    """
    ranked = await get_event_bus().top_members(TRENDING_KEY, limit=limit)
    slugs = [slug for slug, _score in ranked]
    if slugs:
        products = await service.list_by_slugs(slugs)
        if products.items:
            return products
    return await service.list_catalog(page=1, page_size=limit, sort="newest")


@router.get("/{slug}", response_model=StorefrontProductDetailOut)
async def get_product(slug: str, service: CatalogServiceDep) -> StorefrontProductDetailOut:
    product = await service.get_product(slug)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await get_event_bus().publish(
        EventType.PRODUCT_VIEWED, {"slug": slug, "product_id": str(product.id)}
    )
    return product

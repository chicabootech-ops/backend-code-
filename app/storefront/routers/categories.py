from fastapi import APIRouter, HTTPException, Query

from app.storefront.dependencies import CatalogServiceDep
from app.storefront.schemas.category import StorefrontCategoryDetailOut, StorefrontCategoryListResponse, StorefrontCategoryOut

router = APIRouter(prefix="/api/categories", tags=["categories"])


@router.get("", response_model=StorefrontCategoryListResponse)
async def list_categories(service: CatalogServiceDep) -> StorefrontCategoryListResponse:
    """Legacy: returns section roots for nav compatibility."""
    sections = await service.list_sections()
    return StorefrontCategoryListResponse(
        items=[
            StorefrontCategoryOut(
                id=s.id,
                name=s.name,
                slug=s.slug,
                description=s.description,
                image_url=s.image_url,
                sort_order=s.sort_order,
                kind="section",
            )
            for s in sections.items
        ]
    )


@router.get("/{slug}", response_model=StorefrontCategoryDetailOut)
async def get_category(
    slug: str,
    service: CatalogServiceDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
) -> StorefrontCategoryDetailOut:
    # Prefer category (leaf); if slug is a section, return section-shaped category detail
    detail = await service.get_category_with_products(slug, page=page, page_size=page_size)
    if detail:
        return detail

    section = await service.get_section(slug)
    if not section:
        raise HTTPException(status_code=404, detail="Category not found")

    return StorefrontCategoryDetailOut(
        id=section.id,
        name=section.name,
        slug=section.slug,
        description=section.description,
        image_url=section.image_url,
        sort_order=section.sort_order,
        kind="section",
        parent=None,
        children=section.categories,
        products=section.products,
        products_total=len(section.products),
        products_page=1,
        products_page_size=len(section.products) or 24,
    )

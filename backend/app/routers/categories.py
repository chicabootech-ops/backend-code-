from fastapi import APIRouter, HTTPException

from app.dependencies import CategoryServiceDep
from app.schemas.category import StorefrontCategoryDetailOut, StorefrontCategoryListResponse

router = APIRouter(prefix="/api/categories", tags=["categories"])


@router.get("", response_model=StorefrontCategoryListResponse)
async def list_categories(service: CategoryServiceDep) -> StorefrontCategoryListResponse:
    return await service.list_collections()


@router.get("/{slug}", response_model=StorefrontCategoryDetailOut)
async def get_category(slug: str, service: CategoryServiceDep) -> StorefrontCategoryDetailOut:
    category = await service.get_by_slug(slug)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category

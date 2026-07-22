from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.storefront.dependencies import CatalogServiceDep
from app.storefront.schemas.section import StorefrontSectionDetailOut, StorefrontSectionListResponse

router = APIRouter(prefix="/api/sections", tags=["sections"])


@router.get("", response_model=StorefrontSectionListResponse)
async def list_sections(service: CatalogServiceDep) -> StorefrontSectionListResponse:
    return await service.list_sections()


@router.get("/{slug}", response_model=StorefrontSectionDetailOut)
async def get_section(slug: str, service: CatalogServiceDep) -> StorefrontSectionDetailOut:
    section = await service.get_section(slug)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    return section

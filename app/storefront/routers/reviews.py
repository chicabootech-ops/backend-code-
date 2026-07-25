from __future__ import annotations

from datetime import datetime
from typing import Annotated
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from app.storefront.dependencies import CurrentUserId, ReviewServiceDep
from app.storefront.services.review_service import ReviewError

router = APIRouter(prefix="/api/products/{slug}/reviews", tags=["reviews"])

OptionalText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    title: OptionalText | None = Field(default=None, max_length=160)
    body: OptionalText | None = Field(default=None, max_length=4000)


class ReviewOut(BaseModel):
    id: uuid.UUID
    rating: int
    title: str | None = None
    body: str | None = None
    status: str | None = None
    author_name: str | None = None
    is_verified_purchase: bool | None = None
    helpful_count: int | None = None
    created_at: datetime


@router.get("", response_model=list[ReviewOut])
async def list_reviews(slug: str, service: ReviewServiceDep) -> list[dict]:
    return await service.list_approved(slug)


@router.post("", response_model=ReviewOut, status_code=201)
async def create_review(
    slug: str,
    payload: ReviewCreate,
    user_id: CurrentUserId,
    service: ReviewServiceDep,
) -> dict:
    try:
        return await service.submit(
            slug,
            user_id,
            rating=payload.rating,
            title=payload.title,
            body=payload.body,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

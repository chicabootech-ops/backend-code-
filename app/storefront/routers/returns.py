from __future__ import annotations

from datetime import datetime
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.storefront.dependencies import CurrentUserId, ReturnServiceDep
from app.storefront.services.return_service import ReturnError

router = APIRouter(prefix="/api/returns", tags=["returns"])


class ReturnCreate(BaseModel):
    order_id: uuid.UUID
    reason: str = Field(min_length=2, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class ReturnOut(BaseModel):
    id: uuid.UUID
    return_number: int
    order_id: uuid.UUID
    order_number: int
    status: str
    reason: str
    customer_note: str | None = None
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=list[ReturnOut])
async def list_returns(user_id: CurrentUserId, service: ReturnServiceDep) -> list[dict]:
    return await service.list_for_user(user_id)


@router.post("", response_model=ReturnOut, status_code=201)
async def create_return(
    payload: ReturnCreate,
    user_id: CurrentUserId,
    service: ReturnServiceDep,
) -> dict:
    try:
        return await service.create(
            user_id,
            order_id=payload.order_id,
            reason=payload.reason.strip(),
            note=payload.note.strip() if payload.note else None,
        )
    except ReturnError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

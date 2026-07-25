from datetime import datetime
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.storefront.dependencies import CurrentUserId, NotificationServiceDep

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_number: int
    from_status: str | None
    to_status: str
    title: str
    reason: str | None
    created_at: datetime


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    user_id: CurrentUserId, service: NotificationServiceDep
) -> list[dict]:
    return await service.list_for_user(user_id)

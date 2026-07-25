from fastapi import APIRouter, Query
from pydantic import BaseModel, EmailStr

from app.storefront.dependencies import NewsletterServiceDep

router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])


class NewsletterSubscribe(BaseModel):
    email: EmailStr


class NewsletterResult(BaseModel):
    status: str
    message: str


@router.post("/subscribe", response_model=NewsletterResult)
async def subscribe(
    payload: NewsletterSubscribe, service: NewsletterServiceDep
) -> dict[str, str]:
    return await service.subscribe(str(payload.email))


@router.get("/confirm", response_model=NewsletterResult)
async def confirm(
    service: NewsletterServiceDep,
    token: str = Query(min_length=20, max_length=200),
) -> dict[str, str]:
    return await service.confirm(token)

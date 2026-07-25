from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.storefront.dependencies import CurrentUserId, WishlistServiceDep
from app.storefront.schemas.cart import WishlistAdd, WishlistOut
from app.storefront.services.cart_service import CartError

router = APIRouter(prefix="/api/wishlist", tags=["wishlist"])


@router.get("", response_model=WishlistOut)
async def list_wishlist(user_id: CurrentUserId, service: WishlistServiceDep) -> WishlistOut:
    return await service.list_items(user_id)


@router.post("", response_model=WishlistOut)
async def add_wishlist_item(
    payload: WishlistAdd, user_id: CurrentUserId, service: WishlistServiceDep
) -> WishlistOut:
    try:
        return await service.add(user_id, payload)
    except CartError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"message": exc.message, "code": exc.code}
        ) from exc


@router.delete("/{item_id}", response_model=WishlistOut)
async def remove_wishlist_item(
    item_id: uuid.UUID, user_id: CurrentUserId, service: WishlistServiceDep
) -> WishlistOut:
    try:
        return await service.remove(user_id, item_id)
    except CartError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"message": exc.message, "code": exc.code}
        ) from exc

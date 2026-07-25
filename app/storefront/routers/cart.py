from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.storefront.dependencies import CartServiceDep, CurrentUserId
from app.storefront.schemas.cart import CartApplyCoupon, CartItemAdd, CartItemUpdate, CartOut
from app.storefront.services.cart_service import CartError

router = APIRouter(prefix="/api/cart", tags=["cart"])


def _raise(exc: CartError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"message": exc.message, "code": exc.code})


@router.get("", response_model=CartOut)
async def get_cart(user_id: CurrentUserId, service: CartServiceDep) -> CartOut:
    try:
        return await service.get_cart(user_id)
    except CartError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.post("/items", response_model=CartOut)
async def add_cart_item(
    payload: CartItemAdd, user_id: CurrentUserId, service: CartServiceDep
) -> CartOut:
    try:
        return await service.add_item(user_id, payload)
    except CartError as exc:
        _raise(exc)
        raise


@router.patch("/items/{item_id}", response_model=CartOut)
async def update_cart_item(
    item_id: uuid.UUID,
    payload: CartItemUpdate,
    user_id: CurrentUserId,
    service: CartServiceDep,
) -> CartOut:
    try:
        return await service.update_item(user_id, item_id, payload)
    except CartError as exc:
        _raise(exc)
        raise


@router.delete("/items/{item_id}", response_model=CartOut)
async def remove_cart_item(
    item_id: uuid.UUID, user_id: CurrentUserId, service: CartServiceDep
) -> CartOut:
    try:
        return await service.remove_item(user_id, item_id)
    except CartError as exc:
        _raise(exc)
        raise


@router.post("/apply-coupon", response_model=CartOut)
async def apply_coupon(
    payload: CartApplyCoupon, user_id: CurrentUserId, service: CartServiceDep
) -> CartOut:
    try:
        return await service.apply_coupon(user_id, payload)
    except CartError as exc:
        _raise(exc)
        raise


@router.delete("/coupon", response_model=CartOut)
async def clear_coupon(user_id: CurrentUserId, service: CartServiceDep) -> CartOut:
    try:
        return await service.clear_coupon(user_id)
    except CartError as exc:
        _raise(exc)
        raise


@router.post("/merge", response_model=CartOut)
async def merge_cart(
    items: list[CartItemAdd], user_id: CurrentUserId, service: CartServiceDep
) -> CartOut:
    try:
        return await service.merge_guest_items(user_id, items)
    except CartError as exc:
        _raise(exc)
        raise

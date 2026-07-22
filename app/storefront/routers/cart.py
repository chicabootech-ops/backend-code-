from fastapi import APIRouter

router = APIRouter(prefix="/api/cart", tags=["cart"])


@router.get("")
async def get_cart():
    return {"message": "not implemented"}


@router.post("/items")
async def add_cart_item():
    return {"message": "not implemented"}


@router.patch("/items/{item_id}")
async def update_cart_item(item_id: str):
    return {"message": "not implemented"}


@router.delete("/items/{item_id}")
async def remove_cart_item(item_id: str):
    return {"message": "not implemented"}


@router.post("/apply-coupon")
async def apply_coupon():
    return {"message": "not implemented"}

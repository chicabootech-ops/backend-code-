from fastapi import APIRouter

router = APIRouter(prefix="/admin/coupons", tags=["admin-coupons"])


@router.post("")
async def create_coupon():
    return {"message": "not implemented"}


@router.patch("/{coupon_id}")
async def update_coupon(coupon_id: str):
    return {"message": "not implemented"}

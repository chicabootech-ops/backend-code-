from fastapi import APIRouter

router = APIRouter(prefix="/admin/orders", tags=["admin-orders"])


@router.get("")
async def list_orders():
    return {"message": "not implemented"}


@router.patch("/{order_id}/status")
async def update_order_status(order_id: str):
    return {"message": "not implemented"}


@router.post("/{order_id}/refund")
async def refund_order(order_id: str):
    return {"message": "not implemented"}

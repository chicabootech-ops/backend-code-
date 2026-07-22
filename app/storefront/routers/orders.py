from fastapi import APIRouter

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("")
async def create_order():
    return {"message": "not implemented"}


@router.get("")
async def list_orders():
    return {"message": "not implemented"}


@router.get("/{order_id}")
async def get_order(order_id: str):
    return {"message": "not implemented"}


@router.post("/{order_id}/cancel")
async def cancel_order(order_id: str):
    return {"message": "not implemented"}

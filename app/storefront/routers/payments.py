from fastapi import APIRouter

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.post("/initiate")
async def initiate_payment():
    return {"message": "not implemented"}


@router.post("/webhook")
async def payment_webhook():
    return {"message": "not implemented"}


@router.get("/{order_id}")
async def get_payment_status(order_id: str):
    return {"message": "not implemented"}

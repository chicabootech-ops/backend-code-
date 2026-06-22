from fastapi import APIRouter

router = APIRouter(prefix="/admin/inventory", tags=["admin-inventory"])


@router.patch("/{variant_id}")
async def adjust_inventory(variant_id: str):
    return {"message": "not implemented"}


@router.get("/low-stock")
async def low_stock_dashboard():
    return {"message": "not implemented"}

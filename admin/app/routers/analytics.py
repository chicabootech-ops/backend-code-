from fastapi import APIRouter

router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"])


@router.get("/revenue")
async def revenue_report():
    return {"message": "not implemented"}


@router.get("/top-products")
async def top_selling_products():
    return {"message": "not implemented"}

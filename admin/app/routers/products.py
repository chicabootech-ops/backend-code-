from fastapi import APIRouter

router = APIRouter(prefix="/admin/products", tags=["admin-products"])


@router.post("")
async def create_product():
    return {"message": "not implemented"}


@router.patch("/{product_id}")
async def update_product(product_id: str):
    return {"message": "not implemented"}


@router.delete("/{product_id}")
async def delete_product(product_id: str):
    return {"message": "not implemented"}

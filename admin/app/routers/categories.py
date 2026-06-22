from fastapi import APIRouter

router = APIRouter(prefix="/admin/categories", tags=["admin-categories"])


@router.post("")
async def create_category():
    return {"message": "not implemented"}

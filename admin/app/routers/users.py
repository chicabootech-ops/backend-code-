from fastapi import APIRouter

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("")
async def list_users():
    return {"message": "not implemented"}


@router.patch("/{user_id}/ban")
async def ban_user(user_id: str):
    return {"message": "not implemented"}

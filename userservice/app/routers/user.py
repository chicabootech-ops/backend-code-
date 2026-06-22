from fastapi import APIRouter

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("/me")
async def get_me():
    """Return current user profile."""
    return {"message": "not implemented"}


@router.patch("/me")
async def update_me():
    """Update name, phone, avatar."""
    return {"message": "not implemented"}


@router.get("/addresses")
async def list_addresses():
    return {"message": "not implemented"}


@router.post("/addresses")
async def create_address():
    return {"message": "not implemented"}


@router.patch("/addresses/{address_id}")
async def update_address(address_id: str):
    return {"message": "not implemented"}


@router.delete("/addresses/{address_id}")
async def delete_address(address_id: str):
    return {"message": "not implemented"}

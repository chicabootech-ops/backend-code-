from fastapi import APIRouter

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/validate-token")
async def validate_token():
    """Token introspection endpoint consumed by Gateway."""
    return {"message": "not implemented"}

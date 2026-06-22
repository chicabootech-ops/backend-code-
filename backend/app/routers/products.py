from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("")
async def list_products():
    """Paginated product list with filters."""
    return {"message": "not implemented"}


@router.get("/search")
async def search_products(q: str = Query(...)):
    """Full-text product search."""
    return {"message": "not implemented", "q": q}


@router.get("/{slug}")
async def get_product(slug: str):
    """Product detail with variants, stock, images."""
    return {"message": "not implemented", "slug": slug}


@router.post("/{product_id}/reviews")
async def create_review(product_id: str):
    return {"message": "not implemented"}


@router.get("/{product_id}/reviews")
async def list_reviews(product_id: str):
    return {"message": "not implemented"}

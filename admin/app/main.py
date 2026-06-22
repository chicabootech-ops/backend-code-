from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.middleware.admin_guard import AdminGuardMiddleware
from app.routers import analytics, categories, coupons, inventory, orders, products, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Chic A Boo Admin",
    description="Internal dashboard operations",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(AdminGuardMiddleware)

app.include_router(products.router)
app.include_router(categories.router)
app.include_router(orders.router)
app.include_router(inventory.router)
app.include_router(users.router)
app.include_router(analytics.router)
app.include_router(coupons.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "admin"}

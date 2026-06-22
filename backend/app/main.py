from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers import cart, categories, orders, payments, products


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Chic A Boo Backend",
    description="Core e-commerce logic — products, cart, orders, payments",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(products.router)
app.include_router(categories.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(payments.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "backend"}

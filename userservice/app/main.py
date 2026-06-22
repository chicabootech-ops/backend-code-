from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers import auth, internal, user


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Chic A Boo UserService",
    description="Auth and user identity management",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(user.router)
app.include_router(internal.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "userservice"}

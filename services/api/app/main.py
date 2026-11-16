"""Control-plane API. Run: uvicorn app.main:app"""

import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text

from .auth import CurrentUser, current_user
from .config import get_settings
from .db import get_engine
from .routers import tenants


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()  # fail fast on bad auth settings
    yield
    await get_engine().dispose()


app = FastAPI(title="Voice Agent Platform API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(tenants.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"ok": True}


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    is_admin: bool


@app.get("/me", response_model=MeOut, tags=["meta"])
async def me(me: CurrentUser = Depends(current_user)) -> MeOut:
    return MeOut(id=me.user.id, email=me.user.email, name=me.user.name, is_admin=me.is_admin)

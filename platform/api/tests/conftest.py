"""Test setup: a fresh `platform` schema in the platform_test database, migrated with Alembic, and an HTTP client.

Run inside the API container:
  docker compose -f platform/docker-compose.yml run --rm api pytest
"""

import asyncio
import os
import subprocess

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://platform:platform@db:5432/platform_test"
)
os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_MODE"] = "dev"

import httpx
import pytest
from sqlalchemy import text

from app.db import get_engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
async def migrated_db():
    async with get_engine().begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS platform CASCADE"))
    await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", "head"], check=True, env=os.environ)
    yield
    await get_engine().dispose()


@pytest.fixture(autouse=True)
async def clean_tables(migrated_db):
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text("TRUNCATE platform.tenants, platform.users, platform.audit_log CASCADE"))


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def as_user(email: str, admin: bool = False) -> dict:
    return {"Authorization": f"Bearer dev:{email}{':admin' if admin else ''}"}

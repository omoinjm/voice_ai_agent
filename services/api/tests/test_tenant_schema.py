"""Tenant schemas: created idempotently, and the tenant role can only reach its own call data and configs."""

import os
from urllib.parse import urlsplit

import asyncpg
import pytest

from app.db import get_engine
from app.tenant_schema import (
    create_tenant_schema,
    database_url,
    drop_tenant_schema,
    publish_agent_config,
    schema_name,
)

PASSWORD = "Abc123" * 5
OTHER_PASSWORD = "Xyz789" * 5


def _tenant_url(slug: str, env: str, password: str) -> str:
    admin = urlsplit(os.environ["DATABASE_URL"])
    return database_url(admin.hostname, slug, env, password, port=admin.port or 5432, database=admin.path[1:])


@pytest.fixture
async def tenants():
    """Two tenant environments (acme staging, other-co prod), dropped afterwards."""
    async with get_engine().begin() as conn:
        await create_tenant_schema(conn, "acme", "staging", PASSWORD)
        await create_tenant_schema(conn, "other-co", "prod", OTHER_PASSWORD)
    yield
    async with get_engine().begin() as conn:
        await drop_tenant_schema(conn, "acme", "staging")
        await drop_tenant_schema(conn, "other-co", "prod")


def test_schema_name_validation():
    assert schema_name("other-co", "prod") == "t_other_co_prod"
    for slug, env in [("Acme", "prod"), ("a", "prod"), ("acme;drop", "prod"), ("acme", "dev"), ("acme-", "prod")]:
        with pytest.raises(ValueError):
            schema_name(slug, env)


async def test_rejects_unsafe_password():
    async with get_engine().begin() as conn:
        with pytest.raises(ValueError):
            await create_tenant_schema(conn, "acme", "staging", "short'; DROP ROLE x; --")


async def test_tenant_role_uses_own_schema(tenants):
    conn = await asyncpg.connect(_tenant_url("acme", "staging", PASSWORD))
    try:
        # search_path is the tenant schema, so the agent's unqualified queries land there.
        assert await conn.fetchval("SHOW search_path") == "t_acme_staging"
        await conn.execute("INSERT INTO calls (id, channel, started_at) VALUES ('c1', 'web', now())")
        await conn.execute("INSERT INTO call_turns (call_id, role, content) VALUES ('c1', 'caller', 'hi')")
        await conn.execute("INSERT INTO tool_calls (call_id, tool, input) VALUES ('c1', 'x', '{}')")
        await conn.execute(
            "INSERT INTO bookings (reference, call_id, name, service, requested) VALUES ('BK1', 'c1', 'A', 'B', 'C')"
        )
        await conn.execute("UPDATE bookings SET status = 'cancelled' WHERE reference = 'BK1'")
        assert await conn.fetchval("SELECT count(*) FROM call_turns") == 1
    finally:
        await conn.close()


async def test_tenant_role_is_isolated(tenants):
    conn = await asyncpg.connect(_tenant_url("acme", "staging", PASSWORD))
    try:
        for query in [
            "SELECT * FROM platform.tenants",
            "SELECT * FROM t_other_co_prod.calls",
            "CREATE TABLE extra (id int)",
            "DROP TABLE calls",
            "DELETE FROM calls",
            "INSERT INTO agent_config (version, config) VALUES (99, '{}')",
        ]:
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(query)
    finally:
        await conn.close()


async def test_publish_config_is_immutable_and_readable(tenants):
    async with get_engine().begin() as conn:
        assert await publish_agent_config(conn, "acme", "staging", 1, {"business_name": "Acme"})
        assert not await publish_agent_config(conn, "acme", "staging", 1, {"business_name": "Changed"})

    conn = await asyncpg.connect(_tenant_url("acme", "staging", PASSWORD))
    try:
        config = await conn.fetchval("SELECT config->>'business_name' FROM agent_config WHERE version = 1")
        assert config == "Acme"
    finally:
        await conn.close()


async def test_rerun_keeps_data_and_rotates_password(tenants):
    conn = await asyncpg.connect(_tenant_url("acme", "staging", PASSWORD))
    await conn.execute("INSERT INTO calls (id, channel, started_at) VALUES ('keep', 'web', now())")
    await conn.close()

    async with get_engine().begin() as admin:
        await create_tenant_schema(admin, "acme", "staging", OTHER_PASSWORD)

    with pytest.raises(asyncpg.InvalidPasswordError):
        await asyncpg.connect(_tenant_url("acme", "staging", PASSWORD))
    conn = await asyncpg.connect(_tenant_url("acme", "staging", OTHER_PASSWORD))
    try:
        assert await conn.fetchval("SELECT count(*) FROM calls WHERE id = 'keep'") == 1
    finally:
        await conn.close()

"""Per-tenant-environment database schema and role, used by provisioning.

Each tenant environment gets a schema `t_<slug>_<env>` holding its call data (the tables the agent writes, from
legacy/db/10-schema.sql) and its published agent configs, plus a login role of the same name that can only use that
schema: read and write call data, read configs, nothing else. The role's search_path points at the schema, so the
agent's queries need no schema prefix. The tables are owned by the connecting (admin) user, so the tenant role can't
alter or drop them.

Identifiers can't be bound as parameters, so slugs, environments and passwords are validated strictly instead.
"""

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import ENVIRONMENT_NAMES

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
_PASSWORD = re.compile(r"^[A-Za-z0-9]{24,128}$")

# Tables the agent writes; created inside the tenant schema (search_path is set per statement batch below).
# Keep every statement idempotent: provisioning re-runs this on every deploy.
_TABLES = [
    """CREATE TABLE IF NOT EXISTS calls (
        id          text PRIMARY KEY,
        channel     text NOT NULL,
        phone       text,
        started_at  timestamptz NOT NULL,
        ended_at    timestamptz,
        summary     text
    )""",
    "CREATE INDEX IF NOT EXISTS calls_phone_idx ON calls (phone)",
    "CREATE INDEX IF NOT EXISTS calls_started_at_idx ON calls (started_at DESC)",
    """CREATE TABLE IF NOT EXISTS call_turns (
        id           bigserial PRIMARY KEY,
        call_id      text NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
        role         text NOT NULL CHECK (role IN ('caller', 'agent')),
        content      text NOT NULL,
        interrupted  boolean NOT NULL DEFAULT false,
        at           timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS call_turns_call_idx ON call_turns (call_id, id)",
    """CREATE TABLE IF NOT EXISTS tool_calls (
        id       bigserial PRIMARY KEY,
        call_id  text NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
        tool     text NOT NULL,
        input    jsonb NOT NULL,
        result   jsonb,
        at       timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS tool_calls_call_idx ON tool_calls (call_id, id)",
    """CREATE TABLE IF NOT EXISTS bookings (
        reference             text PRIMARY KEY,
        call_id               text REFERENCES calls (id) ON DELETE SET NULL,
        name                  text NOT NULL,
        service               text NOT NULL,
        requested             text NOT NULL,
        phone                 text,
        status                text NOT NULL DEFAULT 'requested',
        created_at            timestamptz NOT NULL DEFAULT now(),
        rescheduled_at        timestamptz,
        confirmation_sent_at  timestamptz
    )""",
    # Published agent configs (persona, business info, voice). Immutable: a change is a new version, and the agent's
    # CONFIG_VERSION picks one. Copied here from the control plane so the tenant role never reads `platform`.
    """CREATE TABLE IF NOT EXISTS agent_config (
        version       integer PRIMARY KEY,
        config        jsonb NOT NULL,
        published_at  timestamptz NOT NULL DEFAULT now()
    )""",
]
_CALL_DATA = ("calls", "call_turns", "tool_calls", "bookings")


def schema_name(slug: str, env: str) -> str:
    """t_<slug>_<env>, also the role name. Raises ValueError on anything that isn't a valid slug/environment."""
    if not _SLUG.match(slug) or env not in ENVIRONMENT_NAMES:
        raise ValueError(f"Invalid tenant slug or environment: {slug!r}, {env!r}")
    return f"t_{slug.replace('-', '_')}_{env}"


def database_url(host: str, slug: str, env: str, password: str, *, port: int = 5432, database: str = "platform") -> str:
    """The DATABASE_URL a tenant agent connects with (plain postgresql:// for asyncpg)."""
    return f"postgresql://{schema_name(slug, env)}:{password}@{host}:{port}/{database}"


async def create_tenant_schema(conn: AsyncConnection, slug: str, env: str, password: str) -> str:
    """Create (or bring up to date) the schema, its tables and the tenant role; sets the role's password.

    Run as the admin user inside a transaction. Returns the schema/role name.
    """
    name = schema_name(slug, env)
    if not _PASSWORD.match(password):
        raise ValueError("Tenant DB passwords must be 24-128 letters and digits")

    exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": name})
    verb = "ALTER" if exists else "CREATE"
    await conn.execute(text(f"{verb} ROLE {name} LOGIN PASSWORD '{password}'"))
    await conn.execute(text(f"ALTER ROLE {name} SET search_path = {name}"))

    await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {name}"))
    await conn.execute(text(f"REVOKE ALL ON SCHEMA {name} FROM PUBLIC"))
    await conn.execute(text(f"SET LOCAL search_path = {name}"))
    for statement in _TABLES:
        await conn.execute(text(statement))

    await conn.execute(text(f"GRANT USAGE ON SCHEMA {name} TO {name}"))
    await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {', '.join(_CALL_DATA)} TO {name}"))
    await conn.execute(text(f"GRANT SELECT ON agent_config TO {name}"))
    await conn.execute(text(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA {name} TO {name}"))
    await conn.execute(text("SET LOCAL search_path = DEFAULT"))
    return name


async def publish_agent_config(
    conn: AsyncConnection, slug: str, env: str, version: int, config: dict[str, Any]
) -> bool:
    """Copy one agent config version into the tenant schema. Versions are immutable: publishing an existing version
    again is a no-op. Returns True if it was inserted."""
    name = schema_name(slug, env)
    result = await conn.execute(
        text(
            f"INSERT INTO {name}.agent_config (version, config) VALUES (:version, CAST(:config AS jsonb))"
            " ON CONFLICT (version) DO NOTHING"
        ),
        {"version": version, "config": json.dumps(config)},
    )
    return result.rowcount == 1


async def drop_tenant_schema(conn: AsyncConnection, slug: str, env: str) -> None:
    """Teardown: drop the schema with all its data, then the role."""
    name = schema_name(slug, env)
    await conn.execute(text(f"DROP SCHEMA IF EXISTS {name} CASCADE"))
    exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": name})
    if exists:
        await conn.execute(text(f"DROP ROLE {name}"))

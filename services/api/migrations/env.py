"""Alembic environment: async engine from DATABASE_URL, version table inside the `platform` schema."""

import asyncio
import os

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import SCHEMA, Base

config = context.config
target_metadata = Base.metadata


def run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        # Only the control plane is managed here; tenant schemas (t_<slug>_<env>) belong to provisioning.
        include_name=lambda name, type_, _parent: type_ != "schema" or name == SCHEMA,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=pool.NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.commit()
        await conn.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations aren't supported; run against a database.")
asyncio.run(run_async())

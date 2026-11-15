"""Local development only: a "demo" tenant's staging schema and agent config, so the agent can run in tenant mode
against the local database (the `agent` service in the root compose.yaml).

  python -m app.dev_tenant        (run by the local `api` service on start; idempotent)

Refuses to run unless ENVIRONMENT is local or test. Stands in for provisioning until that exists.
"""

import asyncio

from .config import get_settings
from .db import get_engine
from .tenant_schema import create_tenant_schema, publish_agent_config

SLUG, ENV = "demo", "staging"
# Matches DATABASE_URL of the `agent` service in compose.yaml. Local only.
PASSWORD = "localdemotenantpassword1"

CONFIG = {
    "business_name": "Demo Co",
    "agent_name": "Ava",
    "timezone": "Africa/Johannesburg",
    "business_info": {
        "hours": "Monday to Friday 8am to 5pm, Saturday 8am to 1pm, closed Sunday and public holidays.",
        "address": "12 Example Street, Rosebank, Johannesburg.",
        "services": [
            {"name": "Consultation", "price": "R250", "duration_minutes": 30},
            {"name": "Standard service", "price": "R450", "duration_minutes": 60},
            {"name": "Premium service", "price": "R800", "duration_minutes": 90},
        ],
    },
    "voice": {"provider": "deepgram", "voice": "aura-2-asteria-en"},
}


async def main() -> None:
    if get_settings().environment not in ("local", "test"):
        raise SystemExit("dev_tenant only runs locally")
    async with get_engine().begin() as conn:
        await create_tenant_schema(conn, SLUG, ENV, PASSWORD)
        await publish_agent_config(conn, SLUG, ENV, 1, CONFIG)
    await get_engine().dispose()
    print(f"Dev tenant ready: schema t_{SLUG}_{ENV}, agent config v1")


if __name__ == "__main__":
    asyncio.run(main())

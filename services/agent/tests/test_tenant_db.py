"""Against the local "demo" tenant schema (seeded by the api service's dev_tenant), as the tenant's own role.

Run in compose: docker compose --profile agent run --rm agent pytest. Skipped without TENANT_ID/DATABASE_URL.
The tenant role can't delete rows, so each run adds a few rows under a unique test phone number.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest

import agent_config
from store import CallStore

pytestmark = pytest.mark.skipif(
    not (os.getenv("TENANT_ID") and os.getenv("DATABASE_URL")), reason="needs the local demo tenant database"
)


async def test_loads_the_published_config(monkeypatch):
    monkeypatch.setattr(agent_config, "_config", None)
    config = await agent_config.get_config()
    assert config.tenant_mode and config.version == int(os.environ["CONFIG_VERSION"])
    assert config.business_name == "Demo Co"
    assert "services" in config.business_info


async def test_missing_config_version_fails(monkeypatch):
    monkeypatch.setattr(agent_config, "_config", None)
    monkeypatch.setenv("CONFIG_VERSION", "9999")
    with pytest.raises(RuntimeError, match="9999"):
        await agent_config.get_config()
    monkeypatch.setattr(agent_config, "_config", None)


async def test_booking_lifecycle_as_tenant_role():
    phone, call_id = f"test-{uuid.uuid4().hex[:12]}", str(uuid.uuid4())
    store = CallStore()
    await store.connect()
    try:
        await store.start_call(call_id, "whatsapp_call", phone, datetime.now(UTC))
        reference = await store.create_booking(
            call_id, {"name": "Test", "service": "Consultation", "datetime": "2099-01-01T10:00:00+02:00", "phone": phone}
        )
        assert [b["reference"] for b in await store.active_bookings(phone)] == [reference]

        moved = await store.change_booking(phone, reference, requested="2099-01-02T10:00:00+02:00")
        assert moved["requested"] == "2099-01-02T10:00:00+02:00"
        assert await store.change_booking("someone-else", reference, cancel=True) is None

        await store.change_booking(phone, reference, cancel=True)
        assert await store.active_bookings(phone) == []
        assert await store.earlier_calls_today(phone, "another-call", "Africa/Johannesburg") == 1
        assert await store.earlier_calls_today(phone, call_id, "Africa/Johannesburg") == 0
    finally:
        await store.close()

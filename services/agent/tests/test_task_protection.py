"""Scale-in protection against a fake ECS agent endpoint."""

import aiohttp
import pytest
from aiohttp import web

import task_protection


@pytest.fixture
async def agent_endpoint(monkeypatch):
    requests = []

    async def state(request):
        requests.append(await request.json())
        return web.json_response({"protection": {}})

    app = web.Application()
    app.router.add_put("/task-protection/v1/state", state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("ECS_AGENT_URI", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(task_protection, "_live_calls", 0)
    monkeypatch.setattr(task_protection, "_lock", None)
    yield requests
    await runner.cleanup()


async def test_protected_until_the_last_call_ends(agent_endpoint):
    async with aiohttp.ClientSession() as session:
        await task_protection.call_started(session)
        await task_protection.call_started(session)
        await task_protection.call_ended(session)
        assert [r["ProtectionEnabled"] for r in agent_endpoint] == [True, True]
        await task_protection.call_ended(session)
    assert agent_endpoint[-1] == {"ProtectionEnabled": False}
    assert agent_endpoint[0]["ExpiresInMinutes"] == task_protection.EXPIRES_MINUTES


async def test_noop_off_ecs(monkeypatch):
    monkeypatch.delenv("ECS_AGENT_URI", raising=False)
    async with aiohttp.ClientSession() as session:
        await task_protection.call_started(session)
        await task_protection.call_ended(session)

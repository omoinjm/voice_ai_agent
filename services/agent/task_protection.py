"""ECS task scale-in protection while calls are live, so prod autoscaling never stops a task mid-call.

On ECS the agent endpoint (ECS_AGENT_URI) lets a task protect itself; elsewhere this is a no-op. Protection is
switched on (and its expiry pushed out) whenever a call starts, and off when the last live call ends. It expires on its
own after EXPIRES_MINUTES, so a crashed process can't leave a task protected forever.
"""

import asyncio
import os

import aiohttp
from loguru import logger

EXPIRES_MINUTES = 120

_live_calls = 0
_lock: asyncio.Lock | None = None


async def _set(session: aiohttp.ClientSession, enabled: bool) -> None:
    agent_uri = os.getenv("ECS_AGENT_URI")
    if not agent_uri:
        return
    body = {"ProtectionEnabled": enabled, **({"ExpiresInMinutes": EXPIRES_MINUTES} if enabled else {})}
    try:
        async with session.put(
            f"{agent_uri}/task-protection/v1/state", json=body, timeout=aiohttp.ClientTimeout(total=5)
        ) as response:
            if response.status != 200:
                logger.warning(f"Task protection {enabled} failed: {response.status} {await response.text()}")
    except Exception as e:
        logger.warning(f"Task protection {enabled} failed: {e}")


def _get_lock() -> asyncio.Lock:
    """Created on first use, inside the event loop that serves calls. Serialises the requests so a call ending
    and another starting can't reach the agent endpoint out of order."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def call_started(session: aiohttp.ClientSession) -> None:
    global _live_calls
    async with _get_lock():
        _live_calls += 1
        await _set(session, True)


async def call_ended(session: aiohttp.ClientSession) -> None:
    global _live_calls
    async with _get_lock():
        _live_calls = max(0, _live_calls - 1)
        if _live_calls == 0:
            await _set(session, False)

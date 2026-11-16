"""What the agent says and does for one business: persona, business facts, voice and model.

Tenant mode (TENANT_ID set; the agent runs on the platform from infra/aws/tenant.yaml): the config version
CONFIG_VERSION, which provisioning copies into the tenant schema's agent_config table, is loaded once (at startup, so a
missing or broken config stops the task before it takes calls). Tools run in Python against the tenant's database.

Legacy mode (no TENANT_ID; the live Demo Co server): the persona in legacy/shared/system.md, tools from
legacy/shared/tools.json run by n8n, and the voice from env.

A published config (JSON) looks like:
    {
      "business_name": "Demo Co",                        required
      "agent_name": "Ava",                               default "Ava"
      "timezone": "Africa/Johannesburg",                 default
      "instructions": "Extra guidance for this business", optional, added to the persona
      "business_info": {                                 what get_business_info answers from
        "hours": "...", "address": "...",
        "services": [{"name": "Haircut", "price": "R250", "duration_minutes": 30}]
      },
      "voice": {"provider": "deepgram", "voice": "aura-2-asteria-en"},   provider: deepgram | voxcpm | cloudflare
      "llm": {"model": "us.anthropic.claude-haiku-4-5-20251001-v1:0"}    optional Bedrock model override
    }
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg
from loguru import logger

# /shared in Docker; the repo's legacy/shared/ folder when run from services/agent/ locally.
SHARED_DIR = Path(os.environ.get("SHARED_DIR") or Path(__file__).resolve().parents[2] / "legacy" / "shared")

TTS_PROVIDERS = ("deepgram", "voxcpm", "cloudflare")
DEFAULT_TIMEZONE = "Africa/Johannesburg"

PERSONA = """\
You are {agent_name}, the voice assistant for {business_name}. You speak with callers on live WhatsApp and web calls.

Everything you write is converted to speech, so:
- Answer in one to three short, natural spoken sentences. No lists, markdown, emojis, URLs or abbreviations that read \
badly aloud.
- Say numbers, times and prices the way a person would ("nine to five", "two hundred and fifty rand").
- Ask one question at a time. If you didn't catch something, ask the caller to repeat it.

Use the tools for business facts and actions; never invent hours, prices or booking confirmations. If a tool fails, \
apologise briefly and offer to have someone follow up.
Before calling a tool, say a few words first so the caller isn't left in silence, for example "Sure, let me check \
that." Then call the tool.
Whenever the caller asks what the business does, its services, hours, prices or address, call get_business_info \
first, even for a general or vague question, and answer only from what it returns.
"""


@dataclass(frozen=True)
class AgentConfig:
    business_name: str
    agent_name: str
    timezone: str
    system_prompt: str  # the persona; per-call context (date, caller, bookings) is added in bot.py
    # None in legacy mode: n8n answers get_business_info and takes bookings.
    business_info: dict[str, Any] | None
    tts_provider: str
    tts_voice: str | None
    llm_model: str | None
    version: int | None = None  # None in legacy mode

    @property
    def tenant_mode(self) -> bool:
        return self.business_info is not None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def parse_config(data: dict[str, Any], version: int) -> AgentConfig:
    """Validate a published config. Raises ValueError with what's wrong."""
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    business_name = str(data.get("business_name") or "").strip()
    if not business_name:
        raise ValueError("business_name is required")
    agent_name = str(data.get("agent_name") or "Ava").strip()
    timezone = data.get("timezone") or DEFAULT_TIMEZONE
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown timezone {timezone!r}") from e
    business_info = data.get("business_info") or {}
    if not isinstance(business_info, dict):
        raise ValueError("business_info must be an object")
    voice = data.get("voice") or {}
    provider = voice.get("provider", "deepgram")
    if provider not in TTS_PROVIDERS:
        raise ValueError(f"voice.provider must be one of {', '.join(TTS_PROVIDERS)}")

    prompt = PERSONA.format(agent_name=agent_name, business_name=business_name)
    if instructions := str(data.get("instructions") or "").strip():
        prompt += f"\nAbout this business:\n{instructions}\n"
    return AgentConfig(
        business_name=business_name,
        agent_name=agent_name,
        timezone=timezone,
        system_prompt=prompt,
        business_info=business_info,
        tts_provider=provider,
        tts_voice=voice.get("voice"),
        llm_model=(data.get("llm") or {}).get("model"),
        version=version,
    )


def legacy_config(shared_dir: Path = SHARED_DIR) -> AgentConfig:
    return AgentConfig(
        business_name=os.getenv("BUSINESS_NAME", "Demo Co"),
        agent_name="Ava",
        timezone=DEFAULT_TIMEZONE,
        system_prompt=(shared_dir / "system.md").read_text(),
        business_info=None,
        tts_provider=os.getenv("TTS_PROVIDER", "deepgram"),
        tts_voice=os.getenv("DEEPGRAM_TTS_VOICE"),
        llm_model=None,
    )


async def _load_tenant_config() -> AgentConfig:
    tenant, version = os.environ["TENANT_ID"], int(os.environ["CONFIG_VERSION"])
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        raw = await conn.fetchval("SELECT config FROM agent_config WHERE version = $1", version)
    finally:
        await conn.close()
    if raw is None:
        raise RuntimeError(f"Agent config version {version} isn't published for tenant {tenant}")
    config = parse_config(json.loads(raw) if isinstance(raw, str) else raw, version)
    # TENANT_ENV, not ENV: Pipecat's runner sets ENV=local when it's imported.
    env = os.getenv("TENANT_ENV", "?")
    logger.info(f"Tenant {tenant} ({env}): loaded agent config v{version} for {config.business_name}")
    return config


_config: AgentConfig | None = None


async def get_config() -> AgentConfig:
    """The config for this process, loaded on first use and then cached (it never changes for a running task).

    No lock: bot.py loads it at startup (in its own event loop, before Pipecat's), so calls only read the cache.
    """
    global _config
    if _config is None:
        _config = await _load_tenant_config() if os.getenv("TENANT_ID") else legacy_config()
    return _config

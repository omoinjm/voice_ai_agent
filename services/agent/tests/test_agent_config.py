import pytest

from agent_config import legacy_config, parse_config

VALID = {
    "business_name": "Acme Dental",
    "agent_name": "Lebo",
    "timezone": "Africa/Johannesburg",
    "instructions": "We only see patients over 12.",
    "business_info": {"hours": "9 to 5"},
    "voice": {"provider": "deepgram", "voice": "aura-2-thalia-en"},
    "llm": {"model": "us.anthropic.claude-sonnet-4-5"},
}


def test_parses_a_full_config():
    config = parse_config(VALID, 3)
    assert config.tenant_mode
    assert config.version == 3
    assert config.tts_provider == "deepgram" and config.tts_voice == "aura-2-thalia-en"
    assert config.llm_model == "us.anthropic.claude-sonnet-4-5"
    assert "You are Lebo, the voice assistant for Acme Dental" in config.system_prompt
    assert "We only see patients over 12." in config.system_prompt


def test_defaults():
    config = parse_config({"business_name": "Acme"}, 1)
    assert config.agent_name == "Ava"
    assert config.timezone == "Africa/Johannesburg"
    assert config.business_info == {} and config.tenant_mode
    assert config.tts_provider == "deepgram" and config.tts_voice is None
    assert config.llm_model is None
    assert "About this business" not in config.system_prompt


@pytest.mark.parametrize(
    "change",
    [
        {"business_name": ""},
        {"timezone": "Mars/Olympus"},
        {"business_info": ["not", "an", "object"]},
        {"voice": {"provider": "elevenlabs"}},
    ],
)
def test_rejects_invalid(change):
    with pytest.raises(ValueError):
        parse_config({**VALID, **change}, 1)


def test_legacy_config_reads_shared_dir(tmp_path, monkeypatch):
    (tmp_path / "system.md").write_text("You are Ava.")
    monkeypatch.setenv("BUSINESS_NAME", "Demo Co")
    monkeypatch.setenv("TTS_PROVIDER", "voxcpm")
    config = legacy_config(tmp_path)
    assert not config.tenant_mode
    assert config.system_prompt == "You are Ava."
    assert config.business_name == "Demo Co" and config.tts_provider == "voxcpm"

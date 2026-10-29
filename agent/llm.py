"""Builds the LLM service chosen by LLM_PROVIDER, shared by bot.py and _call_test.py.

bedrock (default) = Claude on Amazon Bedrock. Credentials come from the default AWS chain: the instance
role on EC2, or ~/.aws (mounted into the container) locally.
cloudflare = Workers AI (free daily allowance). anthropic = the Claude API (needs Anthropic credits).
"""

import os

from anthropic import AsyncAnthropic
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.aws.llm import AWSBedrockLLMService
from pipecat.services.llm_service import LLMService

from cloudflare_llm import CloudflareLLMService


def make_llm(system: str) -> LLMService:
    provider = os.getenv("LLM_PROVIDER", "bedrock")
    if provider == "bedrock":
        model = os.getenv("BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
        return AWSBedrockLLMService(
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            settings=AWSBedrockLLMService.Settings(
                model=model,
                system_instruction=system,
                max_tokens=300,
                # Caches the system prompt and tool definitions, which cuts time to first token on later turns.
                # Claude only: Nova rejects the cachePoint markers Pipecat adds to tool definitions.
                enable_prompt_caching="anthropic" in model,
            ),
        )
    if provider == "anthropic":
        # Keys not scoped to a workspace (sk-ant-usr-...) must name one on every request.
        workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
        return AnthropicLLMService(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            client=AsyncAnthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY"),
                default_headers={"anthropic-workspace-id": workspace_id},
            )
            if workspace_id
            else None,
            settings=AnthropicLLMService.Settings(
                model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5"),
                system_instruction=system,
                max_tokens=300,
            ),
        )
    # Workers AI's OpenAI-compatible Chat Completions endpoint (same account and token as the TTS).
    return CloudflareLLMService(
        account_id=os.environ["CLOUDFLARE_ACCOUNT_ID"],
        api_token=os.environ["CLOUDFLARE_AI_TOKEN"],
        settings=CloudflareLLMService.Settings(
            model=os.getenv("CF_LLM_MODEL", "@cf/openai/gpt-oss-120b"),
            system_instruction=system,
            # Reasoning models spend tokens thinking before they speak; 300 starved them into
            # slow or empty replies. Reply length is kept short by the system prompt instead.
            max_tokens=1024,
        ),
    )

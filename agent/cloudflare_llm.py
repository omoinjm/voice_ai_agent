"""Pipecat LLM service for Cloudflare Workers AI's OpenAI-compatible Chat Completions endpoint."""

import os

from pipecat.services.openai.llm import OpenAILLMService


class CloudflareLLMService(OpenAILLMService):
    """OpenAILLMService pointed at Workers AI, with its message-schema quirks handled.

    Workers AI rejects an assistant message whose `content` is null or missing (which is how OpenAI
    clients send tool calls), so the follow-up request carrying a tool result would fail with
    "oneOf at '/' not met". An empty string is accepted.
    """

    def __init__(self, *, account_id: str, api_token: str, **kwargs):
        super().__init__(
            api_key=api_token,
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
            **kwargs,
        )

    async def get_chat_completions(self, context):
        """Streamed tokens that look like numbers can arrive as JSON numbers ("content": 8), which
        Pipecat drops, so "8am" is spoken as "am". Convert them back to strings."""
        stream = await super().get_chat_completions(context)

        async def fixed():
            try:
                async for chunk in stream:
                    for choice in chunk.choices or []:
                        content = getattr(choice.delta, "content", None)
                        if content is not None and not isinstance(content, str):
                            choice.delta.content = str(content)
                    yield chunk
            finally:
                await stream.close()

        return fixed()

    def build_chat_completion_params(self, params_from_context) -> dict:
        params = super().build_chat_completion_params(params_from_context)
        # Qwen3 reasons silently before answering by default, adding 0.5-1 s before the first word.
        if "qwen3" in str(params.get("model")) and os.getenv("CF_LLM_THINKING", "false") != "true":
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        params["messages"] = [
            {**m, "content": ""} if m.get("role") == "assistant" and m.get("content") is None else m
            for m in params["messages"]
        ]
        return params

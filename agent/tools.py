"""LLM tools: n8n-backed business tools, plus booking changes run against the call store.

Tool schemas live in shared/tools.json (Anthropic format) so the WhatsApp voice-note flow in n8n
and this bot offer the same tools. Every tool is executed by POSTing
`{tool, input, channel, phone}` to `$N8N_URL/webhook/tool` and handing the JSON response back to Claude.
"""

import json
import os
from pathlib import Path

import aiohttp
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams, LLMService

from store import CallStore


def load_tools(shared_dir: Path) -> list[dict]:
    return json.loads((shared_dir / "tools.json").read_text())


def tools_schema(tools: list[dict]) -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name=t["name"],
                description=t["description"],
                properties=t["input_schema"].get("properties", {}),
                required=t["input_schema"].get("required", []),
            )
            for t in tools
        ]
    )


async def run_n8n_tool(session: aiohttp.ClientSession, tool: str, tool_input: dict, caller: dict):
    """POST one tool call to n8n and return its JSON result (or an error the LLM can relay)."""
    base_url = os.environ.get("N8N_URL", "http://localhost:5679").rstrip("/")
    try:
        async with session.post(
            f"{base_url}/webhook/tool",
            json={"tool": tool, "input": tool_input, **caller},
            headers={"X-Tools-Secret": os.environ.get("TOOLS_SECRET", "")},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            return await response.json(content_type=None)
    except Exception as e:
        logger.error(f"Tool {tool} failed: {e}")
        return {"error": f"The {tool} tool is unavailable right now."}


def register_n8n_tools(
    llm: LLMService, tools: list[dict], session: aiohttp.ClientSession, caller: dict, store: CallStore, call_id: str
):
    """Route every tool call to n8n. `caller` (channel, phone) is merged into each request.

    Each call and its result are saved to `store`; a confirmed create_booking also becomes a bookings row.
    """

    async def call_n8n(params: FunctionCallParams):
        result = await run_n8n_tool(session, params.function_name, dict(params.arguments), caller)
        await params.result_callback(result)
        await store.add_tool_call(call_id, params.function_name, dict(params.arguments), result)
        if params.function_name == "create_booking" and isinstance(result, dict) and result.get("ok"):
            booking = {"phone": caller.get("phone"), **dict(params.arguments)}
            await store.add_booking(call_id, result.get("reference") or f"{call_id}-booking", booking)

    for t in tools:
        llm.register_function(t["name"], call_n8n)


# Tools that change existing bookings. They run here, against the call store, rather than in n8n: the bookings
# live in Postgres. They aren't in shared/tools.json, so the n8n voice-note flow doesn't offer them.
BOOKING_TOOLS = [
    {
        "name": "reschedule_booking",
        "description": "Move one of the caller's active bookings to a new date and time. Confirm the new time "
                       "with the caller before calling this.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {"type": "string", "description": "Booking reference, e.g. BKC6HY8."},
                "datetime": {"type": "string",
                             "description": "New date and time, ISO 8601 in Africa/Johannesburg time."},
            },
            "required": ["reference", "datetime"],
        },
    },
    {
        "name": "cancel_booking",
        "description": "Cancel one of the caller's active bookings. Confirm with the caller before calling this.",
        "input_schema": {
            "type": "object",
            "properties": {"reference": {"type": "string", "description": "Booking reference, e.g. BKC6HY8."}},
            "required": ["reference"],
        },
    },
]


def register_booking_tools(llm: LLMService, caller: dict, store: CallStore, call_id: str):
    """reschedule_booking / cancel_booking, limited to bookings that belong to the calling number."""

    async def change_booking(params: FunctionCallParams):
        args = dict(params.arguments)
        reference = str(args.get("reference", "")).replace(" ", "").upper()
        if not caller.get("phone"):
            result = {"ok": False, "error": "Bookings can only be changed on a WhatsApp call."}
        else:
            try:
                booking = await store.change_booking(
                    caller["phone"], reference,
                    requested=args.get("datetime") if params.function_name == "reschedule_booking" else None,
                    cancel=params.function_name == "cancel_booking",
                )
                result = {"ok": True, **booking} if booking else {
                    "ok": False, "error": f"No active booking {reference} for this caller."}
            except Exception as e:
                logger.error(f"Tool {params.function_name} failed: {e}")
                result = {"ok": False, "error": "Bookings can't be changed right now."}
        await params.result_callback(result)
        await store.add_tool_call(call_id, params.function_name, args, result)

    for t in BOOKING_TOOLS:
        llm.register_function(t["name"], change_booking)

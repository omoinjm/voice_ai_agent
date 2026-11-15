"""LLM tools: the business tools (business info, new bookings), plus booking changes run against the call store.

Tenant mode (platform): the business tools run here, answering from the agent config and writing bookings to the
tenant's database (register_tenant_tools).

Legacy mode (the live Demo Co server): tool schemas live in legacy/shared/tools.json (Anthropic format) so the
WhatsApp voice-note flow in n8n and this bot offer the same tools. Every tool is executed by POSTing
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

from agent_config import AgentConfig
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
# live in Postgres. They aren't in legacy/shared/tools.json, so the n8n voice-note flow doesn't offer them.
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


# Tenant mode: the business tools, run here against the agent config and the tenant's database (no n8n).
BUSINESS_INFO_TOPICS = ["hours", "address", "services", "prices", "all"]


def business_tools(config: AgentConfig) -> list[dict]:
    """Same names and arguments as legacy/shared/tools.json, worded for this business."""
    return [
        {
            "name": "get_business_info",
            "description": f"Look up {config.business_name}'s opening hours, address, services and prices. "
                           "Call this before answering any question about the business.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "enum": BUSINESS_INFO_TOPICS,
                              "description": "What the caller is asking about."},
                },
                "required": ["topic"],
            },
        },
        {
            "name": "create_booking",
            "description": "Book an appointment once you have the caller's name, the service and a preferred date "
                           "and time. Confirm the details with the caller before calling this.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Caller's full name."},
                    "service": {"type": "string", "description": "The service being booked."},
                    "datetime": {"type": "string",
                                 "description": f"Requested date and time, ISO 8601 in {config.timezone} time."},
                    "phone": {"type": "string", "description": "Caller's phone number if known."},
                },
                "required": ["name", "service", "datetime"],
            },
        },
    ]


def business_info_answer(info: dict, topic: str | None) -> dict:
    """The part of the business info that answers `topic`; everything when the topic is 'all', unknown or missing
    from the info, so the model can still answer."""
    key = "services" if topic == "prices" else topic
    if key and key != "all" and key in info:
        return {key: info[key]}
    return dict(info)


def register_tenant_tools(llm: LLMService, config: AgentConfig, caller: dict, store: CallStore, call_id: str):
    """get_business_info answers from the config; create_booking writes the booking straight to the call store."""

    async def get_business_info(params: FunctionCallParams):
        args = dict(params.arguments)
        result = business_info_answer(config.business_info or {}, args.get("topic"))
        await params.result_callback(result)
        await store.add_tool_call(call_id, params.function_name, args, result)

    async def create_booking(params: FunctionCallParams):
        args = dict(params.arguments)
        missing = [field for field in ("name", "service", "datetime") if not str(args.get(field) or "").strip()]
        if missing:
            result = {"ok": False, "error": f"Missing {', '.join(missing)}."}
        else:
            # The calling WhatsApp number owns the booking (so the caller can change it later), over what was said.
            booking = {**args, "phone": caller.get("phone") or args.get("phone")}
            try:
                result = {"ok": True, "reference": await store.create_booking(call_id, booking)}
            except Exception as e:
                logger.error(f"Tool {params.function_name} failed: {e}")
                result = {"ok": False, "error": "Bookings can't be made right now."}
        await params.result_callback(result)
        await store.add_tool_call(call_id, params.function_name, args, result)

    llm.register_function("get_business_info", get_business_info)
    llm.register_function("create_booking", create_booking)

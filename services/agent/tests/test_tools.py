"""Tenant-mode business tools, with a fake LLM and call store."""

from types import SimpleNamespace

import pytest

from agent_config import parse_config
from store import new_reference
from tools import business_info_answer, business_tools, register_tenant_tools

INFO = {"hours": "9 to 5", "address": "1 Main Rd", "services": [{"name": "Cleaning", "price": "R500"}]}
CONFIG = parse_config({"business_name": "Acme Dental", "timezone": "Africa/Johannesburg", "business_info": INFO}, 1)


class FakeLLM:
    def __init__(self):
        self.functions = {}

    def register_function(self, name, handler):
        self.functions[name] = handler


class FakeStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.bookings = []
        self.tool_calls = []

    async def create_booking(self, call_id, booking):
        if self.fail:
            raise RuntimeError("db down")
        self.bookings.append((call_id, booking))
        return "BKTEST1"

    async def add_tool_call(self, call_id, tool, tool_input, result):
        self.tool_calls.append((tool, tool_input, result))


async def call(llm, name, arguments):
    results = []

    async def result_callback(result):
        results.append(result)

    await llm.functions[name](SimpleNamespace(function_name=name, arguments=arguments, result_callback=result_callback))
    return results[0]


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("hours", {"hours": "9 to 5"}),
        ("prices", {"services": INFO["services"]}),
        ("services", {"services": INFO["services"]}),
        ("all", INFO),
        (None, INFO),
        ("parking", INFO),
    ],
)
def test_business_info_answer(topic, expected):
    assert business_info_answer(INFO, topic) == expected


def test_business_tools_are_worded_for_the_business():
    tools = {t["name"]: t for t in business_tools(CONFIG)}
    assert set(tools) == {"get_business_info", "create_booking"}
    assert "Acme Dental" in tools["get_business_info"]["description"]
    assert "Africa/Johannesburg" in tools["create_booking"]["input_schema"]["properties"]["datetime"]["description"]


async def test_get_business_info_answers_from_config_and_is_logged():
    llm, store = FakeLLM(), FakeStore()
    register_tenant_tools(llm, CONFIG, {"channel": "web"}, store, "call-1")
    assert await call(llm, "get_business_info", {"topic": "address"}) == {"address": "1 Main Rd"}
    assert store.tool_calls == [("get_business_info", {"topic": "address"}, {"address": "1 Main Rd"})]


async def test_create_booking_uses_the_calling_number():
    llm, store = FakeLLM(), FakeStore()
    register_tenant_tools(llm, CONFIG, {"channel": "whatsapp_call", "phone": "27820000000"}, store, "call-1")
    args = {"name": "Thandi", "service": "Cleaning", "datetime": "2026-11-20T10:00:00+02:00", "phone": "0111"}
    assert await call(llm, "create_booking", args) == {"ok": True, "reference": "BKTEST1"}
    assert store.bookings[0][1]["phone"] == "27820000000"


async def test_create_booking_reports_missing_fields_and_failures():
    llm = FakeLLM()
    register_tenant_tools(llm, CONFIG, {"channel": "web"}, FakeStore(), "call-1")
    result = await call(llm, "create_booking", {"name": "Thandi", "service": " "})
    assert result == {"ok": False, "error": "Missing service, datetime."}

    llm = FakeLLM()
    register_tenant_tools(llm, CONFIG, {"channel": "web"}, FakeStore(fail=True), "call-1")
    result = await call(llm, "create_booking", {"name": "T", "service": "S", "datetime": "D"})
    assert result == {"ok": False, "error": "Bookings can't be made right now."}


def test_references_are_speakable():
    for _ in range(200):
        reference = new_reference()
        assert len(reference) == 7 and reference.startswith("BK")
        assert not set(reference[2:]) & set("01IO")

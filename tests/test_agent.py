import asyncio
import time
from types import SimpleNamespace

from langchain_mcp_adapters.interceptors import MCPToolCallRequest

from maritime_ratify import issue_authority
from maritime_ratify.agent import AuthorityInterceptor, ChallengeMaterial
import maritime_ratify.agent as agent_module


def test_interceptor_keeps_proof_out_of_arguments_and_adds_only_reference():
    asyncio.run(_exercise_interceptor())


async def _exercise_interceptor():
    now = int(time.time())
    authority = issue_authority(now=now - 1)
    seen = {}

    async def challenge_provider(arguments):
        seen["challenge_arguments"] = arguments
        return ChallengeMaterial(b"c" * 32, b"s" * 32)

    async def uploader(action, proof_wire):
        seen["uploaded_action"] = action
        seen["proof_size"] = len(proof_wire.encode())
        return "opaque-reference"

    async def handler(request):
        seen["handled"] = request
        return "ok"

    interceptor = AuthorityInterceptor(
        authority=authority,
        clock=lambda: now,
        challenge_provider=challenge_provider,
        presentation_uploader=uploader,
    )
    args = {
        "request_id": "req-interceptor",
        "resource": "site:warehouse-seattle-01",
        "category": "electrical",
        "amount_minor": 42_000,
        "currency": "USD",
        "description": "Inspect and repair loading-bay lighting",
    }
    request = MCPToolCallRequest(
        name="create_work_order",
        args=args,
        server_name="receiver",
        headers={"X-Ratify-Caller-Token": "Bearer agent-secret"},
    )
    assert await interceptor(request, handler) == "ok"
    handled = seen["handled"]
    assert handled.args == args
    assert set(handled.args) == set(args)
    assert handled.headers == {
        "X-Ratify-Caller-Token": "Bearer agent-secret",
        "X-Ratify-Proof-Reference": "opaque-reference",
    }
    assert seen["proof_size"] > 10_000


def test_interceptor_dispatches_the_action_snapshot_after_await():
    asyncio.run(_exercise_interceptor_snapshot())


async def _exercise_interceptor_snapshot():
    now = int(time.time())
    authority = issue_authority(now=now - 1)
    challenge_started = asyncio.Event()
    release_challenge = asyncio.Event()
    seen = {}

    async def challenge_provider(arguments):
        seen["challenge_arguments"] = arguments
        challenge_started.set()
        await release_challenge.wait()
        return ChallengeMaterial(b"c" * 32, b"s" * 32)

    async def uploader(action, proof_wire):
        seen["uploaded_action"] = action
        return "opaque-reference"

    async def handler(request):
        seen["handled"] = request
        return "ok"

    interceptor = AuthorityInterceptor(
        authority=authority,
        clock=lambda: now,
        challenge_provider=challenge_provider,
        presentation_uploader=uploader,
    )
    args = {
        "request_id": "req-interceptor-snapshot",
        "resource": "site:warehouse-seattle-01",
        "category": "electrical",
        "amount_minor": 42_000,
        "currency": "USD",
        "description": "Inspect and repair loading-bay lighting",
    }
    request = MCPToolCallRequest(
        name="create_work_order",
        args=args,
        server_name="receiver",
        headers={"X-Ratify-Caller-Token": "Bearer agent-secret"},
    )
    task = asyncio.create_task(interceptor(request, handler))
    await challenge_started.wait()
    args["amount_minor"] = 100_000_000
    release_challenge.set()
    assert await task == "ok"
    assert seen["challenge_arguments"]["amount_minor"] == 42_000
    assert seen["uploaded_action"].amount_minor == 42_000
    assert seen["handled"].args["amount_minor"] == 42_000


def test_agent_builder_exposes_only_work_order_tool(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, connections, tool_interceptors):
            captured["connections"] = connections
            captured["interceptors"] = tool_interceptors

        async def get_tools(self):
            return [
                SimpleNamespace(name="issue_work_order_challenge"),
                SimpleNamespace(name="create_work_order"),
            ]

    def fake_create_agent(*, model, tools):
        captured["model"] = model
        captured["tools"] = tools
        return "deterministic-agent"

    monkeypatch.setattr(agent_module, "MultiServerMCPClient", FakeClient)
    monkeypatch.setattr(agent_module, "create_agent", fake_create_agent)
    interceptor = object()

    async def build():
        return await agent_module.build_langchain_agent(
            model="fixed-model",
            connections={"receiver": {"transport": "http", "url": "http://receiver/mcp"}},
            interceptor=interceptor,
        )

    client, agent = asyncio.run(build())
    assert isinstance(client, FakeClient)
    assert agent == "deterministic-agent"
    assert [tool.name for tool in captured["tools"]] == ["create_work_order"]
    assert captured["interceptors"] == [interceptor]

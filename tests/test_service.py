import asyncio
import base64
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from ratify_protocol import encode_proof_bundle

from maritime_ratify import (
    CallerAuthenticator,
    PresentationRegistry,
    WorkOrder,
    WorkOrderReceiver,
    issue_authority,
)
from maritime_ratify.profile import WORK_ORDER_SCOPE
from maritime_ratify.service import create_receiver_app


def test_real_streamable_http_mcp_flow_and_business_only_schema():
    asyncio.run(_exercise_service())


def test_dependency_failure_returns_stable_denial_without_internal_text():
    asyncio.run(_exercise_dependency_failure())


def test_host_authentication_and_duplicate_json_fail_before_dispatch():
    asyncio.run(_exercise_service_rejections())


def test_maritime_private_proxy_host_is_bounded():
    asyncio.run(_exercise_maritime_proxy_host())


async def _exercise_service():
    now = int(time.time())
    authority = issue_authority(now=now - 1)
    receiver = WorkOrderReceiver(
        trusted_root_id=authority.root_id,
        trusted_root_public_key=authority.root_public_key,
        clock=lambda: now,
    )
    app = create_receiver_app(
        receiver=receiver,
        authenticator=CallerAuthenticator({"agent-secret": "caller-agent"}),
        presentations=PresentationRegistry(clock=lambda: now),
        expected_agent_id=authority.agent_id,
        allowed_hosts=["test"],
    )
    args = {
        "request_id": "req-mcp-integration",
        "resource": "site:warehouse-seattle-01",
        "category": "electrical",
        "amount_minor": 42_000,
        "currency": "USD",
        "description": "Inspect and repair loading-bay lighting",
    }
    action = WorkOrder(scope=WORK_ORDER_SCOPE, **args)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Ratify-Caller-Token": "Bearer agent-secret"},
        ) as http:
            assert (await http.get("/health")).json() == {"status": "ok"}
            async with streamable_http_client(
                "http://test/mcp/", http_client=http
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    work_order_tool = next(
                        tool for tool in tools.tools if tool.name == "create_work_order"
                    )
                    assert set(work_order_tool.inputSchema["properties"]) == set(args)
                    assert "proof" not in work_order_tool.inputSchema["properties"]
                    assert "reference" not in work_order_tool.inputSchema["properties"]

                    challenge_result = await session.call_tool(
                        "issue_work_order_challenge", args
                    )
                    challenge = _structured_result(challenge_result)
                    assert challenge["decision"] == "ALLOW"
                    proof = authority.present(
                        challenge=base64.b64decode(challenge["challenge"]),
                        session_context=base64.b64decode(challenge["session_context"]),
                        now=now,
                    )
                    upload = await http.post(
                        "/presentations",
                        json={"action": {"scope": WORK_ORDER_SCOPE, **args},
                              "proof": encode_proof_bundle(proof)},
                    )
                    assert upload.status_code == 200
                    reference = upload.json()["reference"]
                    http.headers["X-Ratify-Proof-Reference"] = reference
                    result = _structured_result(
                        await session.call_tool("create_work_order", args)
                    )
                    assert result["decision"] == "ALLOW"
                    assert result["handler_invocations"] == 1


async def _exercise_dependency_failure():
    class RaisingRegistry(PresentationRegistry):
        def consume(self, **kwargs):
            raise RuntimeError("redis://secret-host:6379 password=hunter2")

    now = int(time.time())
    authority = issue_authority(now=now - 1)
    receiver = WorkOrderReceiver(
        trusted_root_id=authority.root_id,
        trusted_root_public_key=authority.root_public_key,
        clock=lambda: now,
    )
    app = create_receiver_app(
        receiver=receiver,
        authenticator=CallerAuthenticator({"agent-secret": "caller-agent"}),
        presentations=RaisingRegistry(clock=lambda: now),
        expected_agent_id=authority.agent_id,
        allowed_hosts=["test"],
    )
    args = {
        "request_id": "req-dependency-failure",
        "resource": "site:warehouse-seattle-01",
        "category": "electrical",
        "amount_minor": 42_000,
        "currency": "USD",
        "description": "Inspect and repair loading-bay lighting",
    }
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={
                "X-Ratify-Caller-Token": "Bearer agent-secret",
                "X-Ratify-Proof-Reference": "opaque-reference",
            },
        ) as http:
            async with streamable_http_client(
                "http://test/mcp/", http_client=http
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = _structured_result(
                        await session.call_tool("create_work_order", args)
                    )
    assert result == {
        "decision": "DENY",
        "reason": "DENY_VERIFIER_UNAVAILABLE",
        "decided_by": "receiver_error",
        "verification_status": None,
        "handler_invoked": False,
        "handler_invocations": 0,
    }
    assert "redis" not in str(result)
    assert "hunter2" not in str(result)


async def _exercise_service_rejections():
    now = int(time.time())
    authority = issue_authority(now=now - 1)
    receiver = WorkOrderReceiver(
        trusted_root_id=authority.root_id,
        trusted_root_public_key=authority.root_public_key,
        clock=lambda: now,
    )
    app = create_receiver_app(
        receiver=receiver,
        authenticator=CallerAuthenticator({"agent-secret": "caller-agent"}),
        presentations=PresentationRegistry(clock=lambda: now),
        expected_agent_id=authority.agent_id,
        allowed_hosts=["test"],
    )
    args = {
        "request_id": "req-service-rejections",
        "resource": "site:warehouse-seattle-01",
        "category": "electrical",
        "amount_minor": 42_000,
        "currency": "USD",
        "description": "Inspect and repair loading-bay lighting",
    }
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            standard_header = await http.post(
                "/presentations",
                headers={"Authorization": "Bearer agent-secret"},
                content=b"{}",
            )
            assert standard_header.json()["reason"] == "DENY_INVALID_REQUEST"

            wrong_custom_header = await http.post(
                "/presentations",
                headers={"X-Ratify-Caller-Token": "Bearer wrong"},
                content=b"{}",
            )
            assert wrong_custom_header.json()["reason"] == "DENY_TRANSPORT_AUTH"

            duplicate = await http.post(
                "/presentations",
                headers={"X-Ratify-Caller-Token": "Bearer agent-secret"},
                content=b'{"action":{},"proof":"one","proof":"two"}',
            )
            assert duplicate.status_code == 400
            assert duplicate.json()["reason"] == "DENY_AMBIGUOUS_INPUT"

            async with streamable_http_client(
                "http://test/mcp/", http_client=http
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    denied = _structured_result(
                        await session.call_tool("create_work_order", args)
                    )
                    assert denied["decision"] == "DENY"
                    assert denied["reason"] == "DENY_INVALID_REQUEST"

        async with httpx.AsyncClient(
            transport=transport, base_url="http://evil.example.com"
        ) as hostile_host:
            response = await hostile_host.post(
                "/mcp/",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "host-probe", "version": "1"},
                    },
                },
            )
            assert response.status_code == 421
    assert receiver.handler_invocations == 0


async def _exercise_maritime_proxy_host():
    now = int(time.time())
    authority = issue_authority(now=now - 1)
    app = create_receiver_app(
        receiver=WorkOrderReceiver(
            trusted_root_id=authority.root_id,
            trusted_root_public_key=authority.root_public_key,
            clock=lambda: now,
        ),
        authenticator=CallerAuthenticator({"agent-secret": "caller-agent"}),
        presentations=PresentationRegistry(clock=lambda: now),
        expected_agent_id=authority.agent_id,
        allowed_hosts=["test"],
        allow_maritime_proxy_host=True,
    )
    transport = httpx.ASGITransport(app=app)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "host-probe", "version": "1"},
        },
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            headers={"Accept": "application/json, text/event-stream"},
        ) as http:
            accepted = await http.post(
                "http://10.6.110.2:8080/mcp/", json=request
            )
            wrong_port = await http.post(
                "http://10.6.110.2:18789/mcp/", json=request
            )
            public_address = await http.post(
                "http://8.8.8.8:8080/mcp/", json=request
            )
    assert accepted.status_code == 200
    assert wrong_port.status_code == 421
    assert public_address.status_code == 421


def _structured_result(result):
    structured = result.structuredContent
    assert structured is not None
    return structured.get("result", structured)

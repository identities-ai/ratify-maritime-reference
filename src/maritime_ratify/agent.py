"""LangChain MCP authority interceptor; proof material stays outside the model."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from ratify_protocol import encode_proof_bundle

from .action import WorkOrder
from .authority import AuthorityFixture
from .profile import WORK_ORDER_SCOPE

WORK_ORDER_TOOL = "create_work_order"
BUSINESS_ARGUMENTS = {
    "request_id", "resource", "category", "amount_minor", "currency", "description"
}


@dataclass(frozen=True)
class ChallengeMaterial:
    challenge: bytes
    session_context: bytes


class AuthorityInterceptor:
    def __init__(
        self,
        *,
        authority: AuthorityFixture,
        clock: Callable[[], int],
        challenge_provider: Callable[[dict[str, Any]], Awaitable[ChallengeMaterial]],
        presentation_uploader: Callable[[WorkOrder, str], Awaitable[str]],
    ) -> None:
        self._authority = authority
        self._clock = clock
        self._challenge_provider = challenge_provider
        self._presentation_uploader = presentation_uploader

    async def __call__(self, request: MCPToolCallRequest, handler):
        if request.name != WORK_ORDER_TOOL:
            return await handler(request)
        if set(request.args) != BUSINESS_ARGUMENTS:
            raise ValueError("DENY_INVALID_REQUEST")
        action = WorkOrder(scope=WORK_ORDER_SCOPE, **request.args)
        action.validate()
        challenge = await self._challenge_provider(dict(request.args))
        proof = self._authority.present(
            challenge=challenge.challenge,
            session_context=challenge.session_context,
            now=self._clock(),
        )
        reference = await self._presentation_uploader(
            action, encode_proof_bundle(proof)
        )
        headers = dict(request.headers or {})
        headers["X-Ratify-Proof-Reference"] = reference
        dispatched_args = {
            "request_id": action.request_id,
            "resource": action.resource,
            "category": action.category,
            "amount_minor": action.amount_minor,
            "currency": action.currency,
            "description": action.description,
        }
        return await handler(request.override(headers=headers, args=dispatched_args))


class MCPChallengeProvider:
    def __init__(self, client: MultiServerMCPClient, server_name: str) -> None:
        self._client = client
        self._server_name = server_name

    async def __call__(self, arguments: dict[str, Any]) -> ChallengeMaterial:
        async with self._client.session(self._server_name) as session:
            result = await session.call_tool("issue_work_order_challenge", arguments)
        structured = result.structuredContent or {}
        payload = structured.get("result", structured)
        if payload.get("decision") != "ALLOW":
            raise ValueError(payload.get("reason", "DENY_VERIFICATION_FAILED"))
        try:
            return ChallengeMaterial(
                base64.b64decode(payload["challenge"], validate=True),
                base64.b64decode(payload["session_context"], validate=True),
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("DENY_INVALID_REQUEST") from None


class HTTPPresentationUploader:
    def __init__(self, client: httpx.AsyncClient, endpoint: str) -> None:
        self._client = client
        self._endpoint = endpoint

    async def __call__(self, action: WorkOrder, proof_wire: str) -> str:
        response = await self._client.post(
            self._endpoint,
            json={
                "action": {
                    "request_id": action.request_id,
                    "scope": action.scope,
                    "resource": action.resource,
                    "category": action.category,
                    "amount_minor": action.amount_minor,
                    "currency": action.currency,
                    "description": action.description,
                },
                "proof": proof_wire,
            },
        )
        payload = response.json()
        if response.status_code != 200 or payload.get("decision") != "ALLOW":
            raise ValueError(payload.get("reason", "DENY_VERIFICATION_FAILED"))
        reference = payload.get("reference")
        if not isinstance(reference, str) or not reference:
            raise ValueError("DENY_INVALID_REQUEST")
        return reference


async def build_langchain_agent(
    *,
    model: Any,
    connections: dict[str, Any],
    interceptor: AuthorityInterceptor,
):
    """Build the public agent while exposing only the business action tool."""
    client = MultiServerMCPClient(
        connections, tool_interceptors=[interceptor]
    )
    tools = await client.get_tools()
    visible_tools = [tool for tool in tools if tool.name == WORK_ORDER_TOOL]
    if len(visible_tools) != 1:
        raise RuntimeError("expected exactly one create_work_order MCP tool")
    return client, create_agent(model=model, tools=visible_tools)

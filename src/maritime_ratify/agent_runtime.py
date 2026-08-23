"""Maritime runtime for the authority-aware LangChain agent."""

from __future__ import annotations

import base64
from collections import deque
import hmac
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from ratify_protocol import (
    HybridPrivateKey,
    decode_delegation_cert,
    sign_both,
    verify_both,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .agent import (
    AuthorityInterceptor,
    HTTPPresentationUploader,
    MCPChallengeProvider,
    build_langchain_agent,
)
from .authority import AuthorityFixture
from .profile import DEFAULT_CATEGORY, DEFAULT_CURRENCY, DEFAULT_RESOURCE
from .transport import CallerAuthenticator, CarrierDenied

_SCENARIO_AMOUNTS = {"allow": 42_000, "over_limit": 50_100}
_CHAT_RATE_LIMIT = 30
_CHAT_RATE_WINDOW_SECONDS = 60
_DEMO_TOKEN_HEADER = b"x-ratify-demo-token"


@dataclass(frozen=True)
class AgentSettings:
    authority: AuthorityFixture = field(repr=False)
    receiver_mcp_url: str
    presentation_url: str
    receiver_token: str = field(repr=False)
    demo_token: str = field(repr=False)
    model_mode: str
    model_id: str | None

    @classmethod
    def from_environment(cls) -> "AgentSettings":
        delegation_path = os.environ.get("RATIFY_DELEGATION_PATH")
        try:
            if delegation_path:
                with open(delegation_path, encoding="utf-8") as stream:
                    delegation_wire = stream.read().strip()
            else:
                delegation_wire = base64.b64decode(
                    _required("RATIFY_DELEGATION_B64"), validate=True
                ).decode("utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            raise RuntimeError("invalid deployment delegation") from None
        delegation = decode_delegation_cert(delegation_wire)
        private_key = HybridPrivateKey(
            ed25519=_private_key("RATIFY_AGENT_ED25519_PRIVATE_B64", 32),
            ml_dsa_65=_private_key("RATIFY_AGENT_ML_DSA_65_PRIVATE_B64", 4032),
        )
        probe = b"ratify-maritime-agent-key-check"
        if verify_both(probe, sign_both(probe, private_key), delegation.subject_pub_key):
            raise RuntimeError("agent private key does not match deployment delegation")
        mode = os.environ.get("RATIFY_MODEL_MODE", "deterministic")
        if mode not in {"deterministic", "production"}:
            raise RuntimeError("invalid RATIFY_MODEL_MODE")
        model_id = os.environ.get("RATIFY_MODEL_ID")
        if mode == "production" and not model_id:
            raise RuntimeError("missing required environment setting: RATIFY_MODEL_ID")
        receiver_token = _required("RATIFY_RECEIVER_TOKEN")
        demo_token = _required("RATIFY_DEMO_TOKEN")
        if hmac.compare_digest(receiver_token, demo_token):
            raise RuntimeError("RATIFY_DEMO_TOKEN must differ from RATIFY_RECEIVER_TOKEN")
        return cls(
            authority=AuthorityFixture(
                delegation.issuer_id,
                delegation.issuer_pub_key,
                delegation.subject_id,
                private_key,
                delegation,
            ),
            receiver_mcp_url=_required("RATIFY_RECEIVER_MCP_URL"),
            presentation_url=_required("RATIFY_PRESENTATION_URL"),
            receiver_token=receiver_token,
            demo_token=demo_token,
            model_mode=mode,
            model_id=model_id,
        )


class _RateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._events and self._events[0] <= now - self._window_seconds:
                self._events.popleft()
            if len(self._events) >= self._limit:
                return False
            self._events.append(now)
            return True


class DeterministicToolModel(BaseChatModel):
    """Fixed tool-calling model used by the free acceptance path."""

    tool_arguments: dict[str, Any]

    @property
    def _llm_type(self) -> str:
        return "ratify-deterministic-tool-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
    ) -> ChatResult:
        if messages and isinstance(messages[-1], ToolMessage):
            message = AIMessage(content="Work-order decision received.")
        else:
            message = AIMessage(
                content="",
                tool_calls=[{
                    "name": "create_work_order",
                    "args": dict(self.tool_arguments),
                    "id": "deterministic-work-order",
                    "type": "tool_call",
                }],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


def create_agent_app(settings: AgentSettings) -> Starlette:
    authenticator = CallerAuthenticator({settings.demo_token: "demo-console"})
    limiter = _RateLimiter(_CHAT_RATE_LIMIT, _CHAT_RATE_WINDOW_SECONDS)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def chat(request: Request) -> JSONResponse:
        try:
            demo_headers = [
                (b"authorization", value)
                for name, value in request.scope.get("headers", ())
                if name.lower() == _DEMO_TOKEN_HEADER
            ]
            authenticator.authenticate(demo_headers)
        except CarrierDenied:
            return JSONResponse({"response": "Unauthorized."}, status_code=401)
        if not limiter.allow():
            return JSONResponse({"response": "Rate limit exceeded."}, status_code=429)
        try:
            payload = await request.json()
            if type(payload) is not dict or set(payload) - {"message", "source"}:
                raise ValueError
            scenario = payload.get("message")
            if type(scenario) is not str:
                raise ValueError
            if scenario not in _SCENARIO_AMOUNTS:
                return JSONResponse(
                    {"response": "Unsupported demo scenario."}, status_code=400
                )
            decision = await run_scenario(settings, scenario)
            return JSONResponse({
                "response": f"{decision['decision']}: {decision.get('reason', 'ALLOW')}",
                "scenario": scenario,
                **decision,
            })
        except (TypeError, ValueError, json.JSONDecodeError):
            return JSONResponse({"response": "Invalid request."}, status_code=400)
        except Exception:
            return JSONResponse({"response": "Agent unavailable."}, status_code=503)

    return Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        Route("/chat", chat, methods=["POST"]),
    ])


async def run_scenario(settings: AgentSettings, scenario: str) -> dict[str, Any]:
    arguments = _scenario_arguments(scenario)
    model = _model(settings, arguments)
    headers = {"Authorization": f"Bearer {settings.receiver_token}"}
    connections = {
        "receiver": {
            "transport": "streamable_http",
            "url": settings.receiver_mcp_url,
            "headers": headers,
        }
    }
    async with httpx.AsyncClient(headers=headers, timeout=30) as http:
        challenge_provider = MCPChallengeProvider(None, "receiver")
        client, agent = await build_langchain_agent(
            model=model,
            connections=connections,
            interceptor=AuthorityInterceptor(
                authority=settings.authority,
                clock=lambda: int(time.time()),
                challenge_provider=challenge_provider,
                presentation_uploader=HTTPPresentationUploader(
                    http, settings.presentation_url
                ),
            ),
        )
        challenge_provider.bind_client(client)
        result = await agent.ainvoke({
            "messages": [{
                "role": "user",
                "content": f"Execute the enumerated demo scenario: {scenario}",
            }]
        })
    return _tool_decision(result["messages"])


def _model(settings: AgentSettings, arguments: dict[str, Any]):
    if settings.model_mode == "deterministic":
        return DeterministicToolModel(tool_arguments=arguments)
    return init_chat_model(settings.model_id, temperature=0)


def _scenario_arguments(scenario: str) -> dict[str, Any]:
    return {
        "request_id": f"demo-{uuid.uuid4().hex}",
        "resource": DEFAULT_RESOURCE,
        "category": DEFAULT_CATEGORY,
        "amount_minor": _SCENARIO_AMOUNTS[scenario],
        "currency": DEFAULT_CURRENCY,
        "description": "Inspect and repair loading-bay lighting",
    }


def _tool_decision(messages: list[BaseMessage]) -> dict[str, Any]:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            artifact = message.artifact
            if type(artifact) is dict:
                structured = artifact.get("structured_content", artifact)
                if type(structured) is dict:
                    decision = structured.get("result", structured)
                    if type(decision) is dict and decision.get("decision") in {
                        "ALLOW", "DENY"
                    }:
                        return decision
            blocks = message.content if isinstance(message.content, list) else [message.content]
            for block in blocks:
                text = block.get("text") if type(block) is dict else block
                if isinstance(text, str):
                    try:
                        decision = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if type(decision) is dict and decision.get("decision") in {
                        "ALLOW", "DENY"
                    }:
                        return decision
    raise RuntimeError("tool result missing decision")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment setting: {name}")
    return value


def _private_key(name: str, expected_bytes: int) -> bytes:
    try:
        value = base64.b64decode(_required(name), validate=True)
    except ValueError:
        raise RuntimeError(f"invalid private key setting: {name}") from None
    if len(value) != expected_bytes:
        raise RuntimeError(f"invalid private key setting: {name}")
    return value

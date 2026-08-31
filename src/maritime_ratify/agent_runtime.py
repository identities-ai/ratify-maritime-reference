"""Maritime runtime for the authority-aware LangChain agent."""

from __future__ import annotations

import base64
from collections import deque
import hmac
import json
import os
from pathlib import Path
import threading
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
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
from .profile import (
    AUDIENCE_CONSTRAINT,
    CATEGORY_CONSTRAINT,
    DEFAULT_CATEGORY,
    DEFAULT_CURRENCY,
    DEFAULT_RESOURCE,
    SECOND_RESOURCE,
)
from .transport import CallerAuthenticator, CarrierDenied

# One image serves both runtimes. Which scenarios a runtime can execute is
# decided by the authority injected into it, not by a build flag.
_PRIMARY_SCENARIOS = frozenset({
    "allow",
    "over_limit",
    "wrong_resource",
    "altered_operation",
    "expired",
    "revoked",
    "replay",
    "wrong_agent",
    "copied_certificate",
})
_SECONDARY_SCENARIOS = frozenset({
    "isolation_own",
    "isolation_wrong_site",
    "isolation_borrowed_subject",
    "isolation_borrowed_certificate",
})
_CHAT_RATE_LIMIT = 30
_CHAT_RATE_WINDOW_SECONDS = 60
_DEMO_TOKEN_HEADER = b"x-ratify-demo-token"


@dataclass(frozen=True)
class AgentSettings:
    authority: AuthorityFixture = field(repr=False)
    scenario_authorities: dict[str, AuthorityFixture] = field(repr=False)
    supported_scenarios: frozenset[str]
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
        authority = _authority_fixture(delegation, private_key)
        scenario_authorities, supported = _scenario_configuration(authority)
        return cls(
            authority=authority,
            scenario_authorities=scenario_authorities,
            supported_scenarios=supported,
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
            if scenario not in settings.supported_scenarios:
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
    scenario_authority = settings.scenario_authorities.get(
        scenario, settings.authority
    )
    model = _model(settings, arguments)
    headers = {"X-Ratify-Caller-Token": f"Bearer {settings.receiver_token}"}
    connections = {
        "receiver": {
            "transport": "streamable_http",
            "url": settings.receiver_mcp_url,
            "headers": headers,
        }
    }
    async with httpx.AsyncClient(headers=headers, timeout=30) as http:
        challenge_provider = MCPChallengeProvider(None, "receiver")
        interceptor = AuthorityInterceptor(
            authority=scenario_authority,
            clock=lambda: int(time.time()),
            challenge_provider=challenge_provider,
            presentation_uploader=HTTPPresentationUploader(
                http, settings.presentation_url
            ),
            dispatch_transform=_dispatch_transform(scenario),
            replay_dispatch=scenario == "replay",
        )
        client, agent = await build_langchain_agent(
            model=model,
            connections=connections,
            interceptor=interceptor,
        )
        challenge_provider.bind_client(client)
        result = await agent.ainvoke({
            "messages": [{
                "role": "user",
                "content": f"Execute the enumerated demo scenario: {scenario}",
            }]
        })
    decision = _tool_decision(result["messages"])
    maximum, authorized_currency = _delegation_amount_limit(
        settings.authority.delegation
    )
    dispatched = _dispatched_arguments(scenario, arguments)
    return {
        **decision,
        # Spans the agent measured on its own side. Paired with the proxy's
        # upstream duration and the browser's total, these separate our
        # cryptography from platform transit rather than leaving one number to
        # stand for everything.
        **interceptor.timings,
        "requested_amount_minor": dispatched["amount_minor"],
        "requested_resource": dispatched["resource"],
        "requested_category": dispatched["category"],
        "requested_description": dispatched["description"],
        "currency": dispatched["currency"],
        "authorized_max_amount_minor": maximum,
        "authorized_currency": authorized_currency,
        **_delegation_public_facts(scenario_authority.delegation),
    }


def _delegation_amount_limit(delegation) -> tuple[int, str]:
    limits = [
        constraint for constraint in delegation.constraints
        if constraint.type == "max_amount"
    ]
    if len(limits) != 1 or not isinstance(limits[0].currency, str):
        raise RuntimeError("deployment delegation has invalid amount constraint")
    try:
        minor = Decimal(str(limits[0].max_amount)) * 100
    except (InvalidOperation, TypeError, ValueError):
        raise RuntimeError("deployment delegation has invalid amount constraint") from None
    if minor != minor.to_integral_value() or minor < 0:
        raise RuntimeError("deployment delegation has invalid amount constraint")
    return int(minor), limits[0].currency


def _delegation_public_facts(delegation) -> dict[str, str | int]:
    if len(delegation.scope) != 1 or not isinstance(delegation.scope[0], str):
        raise RuntimeError("deployment delegation has invalid scope")
    resource = [
        constraint for constraint in delegation.constraints
        if constraint.type == "resource_path"
    ]
    category = [
        constraint for constraint in delegation.constraints
        if constraint.type == CATEGORY_CONSTRAINT
    ]
    audience = [
        constraint for constraint in delegation.constraints
        if constraint.type == AUDIENCE_CONSTRAINT
    ]
    if (
        len(resource) != 1 or not isinstance(resource[0].resource_id, str)
        or len(category) != 1 or not isinstance(category[0].params, dict)
        or not isinstance(category[0].params.get("allowed"), str)
        or len(audience) != 1 or not isinstance(audience[0].params, dict)
        or not isinstance(audience[0].params.get("allowed"), str)
        or not isinstance(delegation.issued_at, int)
        or not isinstance(delegation.expires_at, int)
    ):
        raise RuntimeError("deployment delegation has invalid public facts")
    return {
        "delegation_scope": delegation.scope[0],
        "delegation_resource": resource[0].resource_id,
        "delegation_category": category[0].params["allowed"],
        "delegation_audience": audience[0].params["allowed"],
        "delegation_issued_at": delegation.issued_at,
        "delegation_expires_at": delegation.expires_at,
    }


def _model(settings: AgentSettings, arguments: dict[str, Any]):
    if settings.model_mode == "deterministic":
        return DeterministicToolModel(tool_arguments=arguments)
    return init_chat_model(settings.model_id, temperature=0)


def _scenario_arguments(scenario: str) -> dict[str, Any]:
    arguments = {
        "request_id": f"demo-{uuid.uuid4().hex}",
        "resource": DEFAULT_RESOURCE,
        "category": DEFAULT_CATEGORY,
        "amount_minor": 42_000,
        "currency": DEFAULT_CURRENCY,
        "description": "Inspect and repair loading-bay lighting",
    }
    if scenario == "over_limit":
        arguments["amount_minor"] = 50_100
    elif scenario == "wrong_resource":
        arguments["resource"] = SECOND_RESOURCE
    elif scenario == "isolation_own":
        arguments["resource"] = SECOND_RESOURCE
        arguments["amount_minor"] = 15_000
        arguments["description"] = "Inspect Portland loading-bay lighting"
    elif scenario == "isolation_wrong_site":
        arguments["amount_minor"] = 15_000
    return arguments


def _dispatch_transform(
    scenario: str,
) -> Any:
    if scenario != "altered_operation":
        return None

    def alter(arguments: dict[str, Any]) -> dict[str, Any]:
        arguments["description"] = "Replace loading-bay electrical panel"
        return arguments

    return alter


def _dispatched_arguments(
    scenario: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    transform = _dispatch_transform(scenario)
    return transform(dict(arguments)) if transform is not None else dict(arguments)


def _authority_fixture(delegation, private_key: HybridPrivateKey) -> AuthorityFixture:
    probe = b"ratify-maritime-agent-key-check"
    if verify_both(probe, sign_both(probe, private_key), delegation.subject_pub_key):
        raise RuntimeError("agent private key does not match deployment delegation")
    return AuthorityFixture(
        delegation.issuer_id,
        delegation.issuer_pub_key,
        delegation.subject_id,
        private_key,
        delegation,
    )


def _scenario_configuration(
    primary: AuthorityFixture,
) -> tuple[dict[str, AuthorityFixture], frozenset[str]]:
    """Derive this runtime's role from the authority material it was given."""
    path = os.environ.get("RATIFY_SCENARIO_AUTHORITIES_PATH")
    if not path:
        return {}, _PRIMARY_SCENARIOS
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if type(payload) is not dict:
            raise ValueError
        if set(payload) == {"peer_delegation"}:
            return _peer_authorities(primary, payload["peer_delegation"]), (
                _SECONDARY_SCENARIOS
            )
        if set(payload) != {
            "expired", "revoked", "wrong_agent",
            "wrong_agent_fixture_private_key",
        }:
            raise ValueError
        expired = decode_delegation_cert(payload["expired"])
        revoked = decode_delegation_cert(payload["revoked"])
        wrong_agent = decode_delegation_cert(payload["wrong_agent"])
        fixture_private = payload["wrong_agent_fixture_private_key"]
        if type(fixture_private) is not dict or set(fixture_private) != {
            "ed25519", "ml_dsa_65"
        }:
            raise ValueError
        wrong_private = HybridPrivateKey(
            ed25519=_decode_fixture_key(fixture_private["ed25519"], 32),
            ml_dsa_65=_decode_fixture_key(fixture_private["ml_dsa_65"], 4032),
        )
        authorities = {
            "expired": _authority_fixture(expired, primary.agent_private_key),
            "revoked": _authority_fixture(revoked, primary.agent_private_key),
            "wrong_agent": _authority_fixture(wrong_agent, wrong_private),
            # The genuine public delegation presented by a holder who does not
            # own its subject key. The bundle names the authorized agent, so
            # the receiver's subject precheck passes and Ratify verification is
            # the layer that has to reject it. Constructed directly because
            # _authority_fixture requires the key to match the certificate,
            # which is exactly what this case violates.
            "copied_certificate": AuthorityFixture(
                primary.root_id,
                primary.root_public_key,
                primary.agent_id,
                wrong_private,
                primary.delegation,
            ),
        }
        if any(
            authority.root_id != primary.root_id
            or authority.root_public_key != primary.root_public_key
            for authority in authorities.values()
        ):
            raise ValueError
        return authorities, _PRIMARY_SCENARIOS
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("invalid deployment scenario authorities") from None


def _peer_authorities(
    primary: AuthorityFixture, peer_wire: object
) -> dict[str, AuthorityFixture]:
    """Cross-runtime attempts built from a peer's public certificate alone.

    This runtime holds the other runtime's delegation, which is public, and
    never its private key. Both attempts are therefore what an operator of this
    runtime could actually mount.
    """
    if type(peer_wire) is not str:
        raise ValueError
    peer = decode_delegation_cert(peer_wire)
    if peer.issuer_id != primary.root_id or (
        peer.issuer_pub_key != primary.root_public_key
    ):
        raise ValueError
    if peer.subject_id == primary.agent_id:
        raise ValueError
    return {
        # Declaring the peer's subject. The receiver issued its challenge for
        # this runtime's subject, so the precheck refuses it.
        "isolation_borrowed_subject": AuthorityFixture(
            primary.root_id,
            primary.root_public_key,
            peer.subject_id,
            primary.agent_private_key,
            peer,
        ),
        # Declaring this runtime's own subject while presenting the peer's
        # certificate. The precheck passes and verification refuses it.
        "isolation_borrowed_certificate": AuthorityFixture(
            primary.root_id,
            primary.root_public_key,
            primary.agent_id,
            primary.agent_private_key,
            peer,
        ),
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


def _decode_fixture_key(value: object, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise ValueError
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) != expected_bytes:
        raise ValueError
    return decoded

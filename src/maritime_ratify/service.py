"""ASGI and Streamable HTTP MCP receiver service."""

from __future__ import annotations

import base64
import contextlib
import ipaddress
import json
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .action import WorkOrder
from .profile import WORK_ORDER_SCOPE
from .receiver import WorkOrderReceiver
from .transport import CallerAuthenticator, CarrierDenied, PresentationRegistry

MAX_PRESENTATION_UPLOAD_BYTES = 150_000
CALLER_TOKEN_HEADER = b"x-ratify-caller-token"
_MARITIME_PROXY_HOST = b"maritime-internal"
_MARITIME_PROXY_NETWORK = ipaddress.ip_network("10.0.0.0/8")
_ACTION_FIELDS = {
    "request_id", "scope", "resource", "category", "amount_minor", "currency",
    "description",
}


def create_receiver_app(
    *,
    receiver: WorkOrderReceiver,
    authenticator: CallerAuthenticator,
    presentations: PresentationRegistry,
    expected_agent_id: str,
    allowed_hosts: list[str] | None = None,
    allow_maritime_proxy_host: bool = False,
) -> Starlette:
    # MCP 1.29 leaves one generic Settings annotation unresolved under
    # pydantic-settings 2.15. Rebuild from the module's complete namespace so
    # construction remains warning-free; remove after the upstream fix lands.
    FastMCPSettings.model_rebuild()
    transport_hosts = list(allowed_hosts or ["localhost:*", "127.0.0.1:*"])
    if allow_maritime_proxy_host:
        transport_hosts.append(_MARITIME_PROXY_HOST.decode("ascii"))
    mcp = FastMCP(
        "Ratify Maritime Work Orders",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=transport_hosts
        ),
    )

    @mcp.tool()
    def issue_work_order_challenge(
        request_id: str,
        resource: str,
        category: str,
        amount_minor: int,
        currency: str,
        description: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """Issue a challenge for one exact work-order action."""
        try:
            caller_id = authenticator.authenticate(
                _caller_auth_headers(_raw_context_headers(ctx))
            )
            action = _work_order(
                request_id, resource, category, amount_minor, currency, description
            )
            result = receiver.issue_challenge(
                action, expected_agent_id=expected_agent_id, caller_id=caller_id
            )
            if result.grant is None:
                return {"decision": result.decision, "reason": result.reason}
            return {
                "decision": "ALLOW",
                "reason": "ALLOW",
                "challenge": base64.b64encode(result.grant.challenge).decode("ascii"),
                "session_context": base64.b64encode(
                    result.grant.session_context
                ).decode("ascii"),
            }
        except CarrierDenied as error:
            return {"decision": "DENY", "reason": error.reason}
        except Exception:
            return {"decision": "DENY", "reason": "DENY_VERIFIER_UNAVAILABLE"}

    @mcp.tool()
    def create_work_order(
        request_id: str,
        resource: str,
        category: str,
        amount_minor: int,
        currency: str,
        description: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """Create one maintenance work order within delegated authority."""
        try:
            raw_headers = _raw_context_headers(ctx)
            caller_id = authenticator.authenticate(_caller_auth_headers(raw_headers))
            action = _work_order(
                request_id, resource, category, amount_minor, currency, description
            )
            bundle = presentations.consume(
                raw_headers=raw_headers, caller_id=caller_id, action=action
            )
            return receiver.execute(action, bundle, caller_id=caller_id)
        except CarrierDenied as error:
            return {
                "decision": "DENY",
                "reason": error.reason,
                "decided_by": "proof_carrier",
                "verification_status": None,
                "handler_invoked": False,
                "handler_invocations": receiver.handler_invocations,
            }
        except Exception:
            return {
                "decision": "DENY",
                "reason": "DENY_VERIFIER_UNAVAILABLE",
                "decided_by": "receiver_error",
                "verification_status": None,
                "handler_invoked": False,
                "handler_invocations": receiver.handler_invocations,
            }

    async def upload_presentation(request: Request) -> JSONResponse:
        try:
            caller_id = authenticator.authenticate(
                _caller_auth_headers(request.scope["headers"])
            )
            raw = await request.body()
            action, proof_wire = _decode_upload(raw)
            reference = presentations.register(
                caller_id=caller_id, action=action, proof_wire=proof_wire
            )
            return JSONResponse({"decision": "ALLOW", "reference": reference})
        except CarrierDenied as error:
            return JSONResponse(
                {"decision": "DENY", "reason": error.reason}, status_code=400
            )
        except Exception:
            return JSONResponse(
                {"decision": "DENY", "reason": "DENY_VERIFIER_UNAVAILABLE"},
                status_code=503,
            )

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        async with mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/presentations", upload_presentation, methods=["POST"]),
            Mount(
                "/mcp",
                app=_MaritimeProxyHost(mcp.streamable_http_app())
                if allow_maritime_proxy_host
                else mcp.streamable_http_app(),
            ),
        ],
        lifespan=lifespan,
    )
    app.state.mcp = mcp
    return app


class _MaritimeProxyHost:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = list(scope["headers"])
            for index, (name, value) in enumerate(headers):
                if name.lower() == b"host" and _is_maritime_proxy_host(value):
                    headers[index] = (name, _MARITIME_PROXY_HOST)
                    scope = {**scope, "headers": headers}
                    break
        await self.app(scope, receive, send)


def _is_maritime_proxy_host(value: bytes) -> bool:
    try:
        host, separator, port = value.decode("ascii").rpartition(":")
        return (
            separator == ":"
            and port == "8080"
            and ipaddress.ip_address(host) in _MARITIME_PROXY_NETWORK
        )
    except (UnicodeDecodeError, ValueError):
        return False


def _work_order(
    request_id: str,
    resource: str,
    category: str,
    amount_minor: int,
    currency: str,
    description: str,
) -> WorkOrder:
    return WorkOrder(
        request_id, WORK_ORDER_SCOPE, resource, category, amount_minor, currency,
        description,
    )


def _raw_context_headers(ctx: Context) -> list[tuple[bytes, bytes]]:
    request = ctx.request_context.request
    return list(request.scope["headers"])


def _caller_auth_headers(
    raw_headers: list[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    return [
        (b"authorization", value)
        for name, value in raw_headers
        if name.lower() == CALLER_TOKEN_HEADER
    ]


def _decode_upload(raw: bytes) -> tuple[WorkOrder, str]:
    if len(raw) > MAX_PRESENTATION_UPLOAD_BYTES:
        raise CarrierDenied("DENY_OVERSIZED_INPUT")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CarrierDenied("DENY_AMBIGUOUS_INPUT")
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=unique_object)
    except CarrierDenied:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CarrierDenied("DENY_INVALID_REQUEST") from None
    if type(payload) is not dict or set(payload) != {"action", "proof"}:
        raise CarrierDenied("DENY_INVALID_REQUEST")
    action_data = payload["action"]
    if type(action_data) is not dict or set(action_data) != _ACTION_FIELDS:
        raise CarrierDenied("DENY_INVALID_REQUEST")
    if type(payload["proof"]) is not str:
        raise CarrierDenied("DENY_INVALID_REQUEST")
    try:
        action = WorkOrder(**action_data)
        action.validate()
    except (TypeError, ValueError):
        raise CarrierDenied("DENY_INVALID_REQUEST") from None
    return action, payload["proof"]

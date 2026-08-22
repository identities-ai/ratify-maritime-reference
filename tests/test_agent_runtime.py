import base64
import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest
from ratify_protocol import encode_delegation_cert
import uvicorn
from starlette.testclient import TestClient

from maritime_ratify import (
    CallerAuthenticator,
    PresentationRegistry,
    WorkOrderReceiver,
    issue_authority,
)
from maritime_ratify.agent_runtime import (
    AgentSettings,
    DeterministicToolModel,
    create_agent_app,
    run_scenario,
    _model,
)
import maritime_ratify.agent_runtime as runtime_module
from maritime_ratify.service import create_receiver_app

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _environment(monkeypatch, authority):
    values = {
        "RATIFY_DELEGATION_B64": base64.b64encode(
            encode_delegation_cert(authority.delegation).encode()
        ).decode(),
        "RATIFY_AGENT_ED25519_PRIVATE_B64": base64.b64encode(
            authority.agent_private_key.ed25519
        ).decode(),
        "RATIFY_AGENT_ML_DSA_65_PRIVATE_B64": base64.b64encode(
            authority.agent_private_key.ml_dsa_65
        ).decode(),
        "RATIFY_RECEIVER_MCP_URL": "https://receiver.example/mcp/",
        "RATIFY_PRESENTATION_URL": "https://receiver.example/presentations",
        "RATIFY_RECEIVER_TOKEN": "test-transport-token",
        "RATIFY_DEMO_TOKEN": "test-demo-token",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_agent_settings_load_matching_private_authority(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)

    settings = AgentSettings.from_environment()

    assert settings.authority.agent_id == authority.agent_id
    assert settings.model_mode == "deterministic"
    assert settings.model_id is None


def test_agent_settings_reject_mismatched_private_authority(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    other = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)
    monkeypatch.setenv(
        "RATIFY_AGENT_ED25519_PRIVATE_B64",
        base64.b64encode(other.agent_private_key.ed25519).decode(),
    )

    with pytest.raises(RuntimeError, match="does not match RATIFY_DELEGATION_B64"):
        AgentSettings.from_environment()


def test_agent_settings_reject_invalid_delegation_transport(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)
    monkeypatch.setenv("RATIFY_DELEGATION_B64", "not base64")

    with pytest.raises(RuntimeError, match="invalid RATIFY_DELEGATION_B64"):
        AgentSettings.from_environment()


def test_production_model_is_explicit_and_uses_configured_model(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)
    monkeypatch.setenv("RATIFY_MODEL_MODE", "production")
    monkeypatch.setenv("RATIFY_MODEL_ID", "openai:gpt-5-mini")
    settings = AgentSettings.from_environment()
    captured = {}

    def fake_init(model_id, **kwargs):
        captured.update(model_id=model_id, kwargs=kwargs)
        return "production-model"

    monkeypatch.setattr(runtime_module, "init_chat_model", fake_init)
    assert _model(settings, {}) == "production-model"
    assert captured == {
        "model_id": "openai:gpt-5-mini",
        "kwargs": {"temperature": 0},
    }


def test_deterministic_model_requires_no_provider_configuration(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)
    settings = AgentSettings.from_environment()

    assert isinstance(_model(settings, {"request_id": "req-fixed"}), DeterministicToolModel)


def test_deterministic_agent_crosses_real_mcp_boundary():
    asyncio.run(_exercise_real_agent_boundary())


async def _exercise_real_agent_boundary():
    now = int(time.time())
    authority = issue_authority(now=now - 1)
    receiver = WorkOrderReceiver(
        trusted_root_id=authority.root_id,
        trusted_root_public_key=authority.root_public_key,
    )
    port = _unused_port()
    receiver_app = create_receiver_app(
        receiver=receiver,
        authenticator=CallerAuthenticator({"agent-token": "maritime-agent"}),
        presentations=PresentationRegistry(),
        expected_agent_id=authority.agent_id,
        allowed_hosts=[f"127.0.0.1:{port}"],
    )
    server = uvicorn.Server(uvicorn.Config(
        receiver_app, host="127.0.0.1", port=port, log_level="error"
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started
    try:
        settings = AgentSettings(
            authority=authority,
            receiver_mcp_url=f"http://127.0.0.1:{port}/mcp/",
            presentation_url=f"http://127.0.0.1:{port}/presentations",
            receiver_token="agent-token",
            demo_token="demo-token",
            model_mode="deterministic",
            model_id=None,
        )
        allowed = await run_scenario(settings, "allow")
        denied = await run_scenario(settings, "over_limit")
        assert allowed["decision"] == "ALLOW"
        assert allowed["handler_invocations"] == 1
        assert denied["decision"] == "DENY"
        assert denied["reason"] == "DENY_LIMIT_EXCEEDED"
        assert denied["handler_invocations"] == 1
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_agent_settings_repr_redacts_private_authority_and_tokens(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)

    settings = AgentSettings.from_environment()
    rendered = repr(settings)

    assert "test-transport-token" not in rendered
    assert "test-demo-token" not in rendered
    assert base64.b64encode(authority.agent_private_key.ed25519).decode() not in rendered
    assert "authority=" not in rendered
    assert repr(authority.agent_private_key.ed25519) not in repr(settings.authority)


def test_agent_settings_reject_reused_demo_and_receiver_token(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)
    monkeypatch.setenv("RATIFY_DEMO_TOKEN", "test-transport-token")

    with pytest.raises(RuntimeError, match="must differ"):
        AgentSettings.from_environment()


def test_chat_requires_authentication_and_rejects_non_string_message(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)
    app = create_agent_app(AgentSettings.from_environment())

    with TestClient(app) as client:
        assert client.post("/chat", json={"message": "allow"}).status_code == 401
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer test-demo-token"},
            json={"message": ["allow"]},
        )

    assert response.status_code == 400
    assert response.json() == {"response": "Invalid request."}


def test_chat_rate_limits_authenticated_requests(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)
    monkeypatch.setattr(runtime_module, "_CHAT_RATE_LIMIT", 1)
    app = create_agent_app(AgentSettings.from_environment())
    headers = {"Authorization": "Bearer test-demo-token"}

    with TestClient(app) as client:
        first = client.post("/chat", headers=headers, json={"message": []})
        second = client.post("/chat", headers=headers, json={"message": []})

    assert first.status_code == 400
    assert second.status_code == 429
    assert second.json() == {"response": "Rate limit exceeded."}


def test_agent_image_context_excludes_environment_secret_files():
    patterns = set((_REPOSITORY_ROOT / ".dockerignore").read_text().splitlines())

    assert ".env" in patterns
    assert ".env.*" in patterns


def test_runtime_images_are_pinned_minimal_non_root_and_health_checked():
    dockerfiles = [
        _REPOSITORY_ROOT / "Dockerfile",
        _REPOSITORY_ROOT / "apps/receiver/Dockerfile",
    ]

    for dockerfile in dockerfiles:
        content = dockerfile.read_text()
        assert "python:3.12-slim@sha256:" in content
        assert "COPY . /app" not in content
        assert "USER appuser" in content
        assert "HEALTHCHECK" in content
        assert "CMD [\"/app/.venv/bin/python\"" in content
        assert "CMD /app/.venv/bin/python -c" in content


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

import base64
import asyncio
import socket
import threading
import time

import pytest
from ratify_protocol import encode_delegation_cert
import uvicorn

from maritime_ratify import (
    CallerAuthenticator,
    PresentationRegistry,
    WorkOrderReceiver,
    issue_authority,
)
from maritime_ratify.agent_runtime import (
    AgentSettings,
    DeterministicToolModel,
    run_scenario,
    _model,
)
import maritime_ratify.agent_runtime as runtime_module
from maritime_ratify.service import create_receiver_app


def _environment(monkeypatch, authority):
    values = {
        "RATIFY_DELEGATION": encode_delegation_cert(authority.delegation),
        "RATIFY_AGENT_ED25519_PRIVATE_B64": base64.b64encode(
            authority.agent_private_key.ed25519
        ).decode(),
        "RATIFY_AGENT_ML_DSA_65_PRIVATE_B64": base64.b64encode(
            authority.agent_private_key.ml_dsa_65
        ).decode(),
        "RATIFY_RECEIVER_MCP_URL": "https://receiver.example/mcp/",
        "RATIFY_PRESENTATION_URL": "https://receiver.example/presentations",
        "RATIFY_RECEIVER_TOKEN": "test-transport-token",
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

    with pytest.raises(RuntimeError, match="does not match RATIFY_DELEGATION"):
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


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

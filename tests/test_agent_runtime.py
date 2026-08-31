import base64
import asyncio
import json
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
    _SECONDARY_SCENARIOS,
)
from maritime_ratify.deployment_issuance import issue_deployment
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

    with pytest.raises(RuntimeError, match="does not match deployment delegation"):
        AgentSettings.from_environment()


def test_agent_settings_reject_invalid_delegation_transport(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)
    monkeypatch.setenv("RATIFY_DELEGATION_B64", "not base64")

    with pytest.raises(RuntimeError, match="invalid deployment delegation"):
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


def test_full_adversarial_gate_crosses_real_mcp_boundary(tmp_path, monkeypatch):
    asyncio.run(_exercise_full_adversarial_gate(tmp_path, monkeypatch))


async def _exercise_full_adversarial_gate(tmp_path, monkeypatch):
    issuance = tmp_path / "issuance"
    issue_deployment(issuance, now=int(time.time()))
    agent_env = dict(
        line.split("=", 1) for line in (issuance / "agent.env").read_text().splitlines()
    )
    receiver_env = dict(
        line.split("=", 1)
        for line in (issuance / "receiver.env").read_text().splitlines()
    )
    for name, value in agent_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("RATIFY_DELEGATION_PATH", str(issuance / "delegation.json"))
    monkeypatch.setenv(
        "RATIFY_SCENARIO_AUTHORITIES_PATH",
        str(issuance / "scenario-authorities.json"),
    )
    monkeypatch.setenv("RATIFY_RECEIVER_MCP_URL", "unused")
    monkeypatch.setenv("RATIFY_PRESENTATION_URL", "unused")
    settings = AgentSettings.from_environment()
    receiver = WorkOrderReceiver(
        trusted_root_id=settings.authority.root_id,
        trusted_root_public_key=settings.authority.root_public_key,
    )
    receiver.revocation.revoke(receiver_env["RATIFY_REVOKED_CERT_IDS"])
    port = _unused_port()
    receiver_app = create_receiver_app(
        receiver=receiver,
        authenticator=CallerAuthenticator({
            settings.receiver_token: "maritime-agent"
        }),
        presentations=PresentationRegistry(),
        caller_subjects={"maritime-agent": settings.authority.agent_id},
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
    settings = AgentSettings(
        authority=settings.authority,
        scenario_authorities=settings.scenario_authorities,
        receiver_mcp_url=f"http://127.0.0.1:{port}/mcp/",
        presentation_url=f"http://127.0.0.1:{port}/presentations",
        receiver_token=settings.receiver_token,
        demo_token=settings.demo_token,
        supported_scenarios=settings.supported_scenarios,
        model_mode="deterministic",
        model_id=None,
    )
    # The deciding layer is part of the claim. A denial that never reaches
    # Ratify verification proves the receiver's own binding, not the protocol,
    # so the required layer is pinned in the shared contract that the live gate
    # and the local reproduction also read.
    contract = json.loads(
        (Path(__file__).resolve().parents[1] / "docs" / "gate-expectations.json")
        .read_text(encoding="utf-8")
    )["scenarios"]
    expected = {
        name: (
            required["decision"],
            required["reason"],
            required["handler_invoked"],
            required["decided_by"],
            required["verification_status"],
        )
        for name, required in contract.items()
    }
    try:
        for scenario, outcome in expected.items():
            result = await run_scenario(settings, scenario)
            assert (
                result["decision"],
                result["reason"],
                result["handler_invoked"],
                result["decided_by"],
                result["verification_status"],
            ) == outcome
        assert receiver.handler_invocations == 2
    finally:
        server.should_exit = True
        thread.join(timeout=5)


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
        caller_subjects={"maritime-agent": authority.agent_id},
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
            scenario_authorities={},
            receiver_mcp_url=f"http://127.0.0.1:{port}/mcp/",
            presentation_url=f"http://127.0.0.1:{port}/presentations",
            receiver_token="agent-token",
            demo_token="demo-token",
            supported_scenarios=frozenset({"allow", "over_limit"}),
            model_mode="deterministic",
            model_id=None,
        )
        allowed = await run_scenario(settings, "allow")
        denied = await run_scenario(settings, "over_limit")
        assert allowed["decision"] == "ALLOW"
        assert allowed["handler_invocations"] == 1
        assert allowed["handler_invoked"] is True
        assert allowed["requested_amount_minor"] == 42_000
        assert allowed["authorized_max_amount_minor"] == 50_000
        assert allowed["currency"] == "USD"
        assert allowed["authorized_currency"] == "USD"
        assert allowed["delegation_scope"] == "custom:work_order:create"
        assert allowed["delegation_resource"] == "site:warehouse-seattle-01"
        assert allowed["delegation_category"] == "electrical"
        assert allowed["delegation_audience"] == "maritime-ratify-demo-receiver"
        assert allowed["delegation_issued_at"] == authority.delegation.issued_at
        assert allowed["delegation_expires_at"] == authority.delegation.expires_at
        assert denied["decision"] == "DENY"
        assert denied["reason"] == "DENY_LIMIT_EXCEEDED"
        assert denied["handler_invocations"] == 1
        assert denied["handler_invoked"] is False
        assert denied["requested_amount_minor"] == 50_100
        assert denied["authorized_max_amount_minor"] == 50_000
        assert denied["delegation_expires_at"] == authority.delegation.expires_at
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
        assert client.post(
            "/chat",
            headers={"Authorization": "Bearer test-demo-token"},
            json={"message": "allow"},
        ).status_code == 401
        response = client.post(
            "/chat",
            headers={"X-Ratify-Demo-Token": "Bearer test-demo-token"},
            json={"message": ["allow"]},
        )

    assert response.status_code == 400
    assert response.json() == {"response": "Invalid request."}


def test_chat_rate_limits_authenticated_requests(monkeypatch):
    authority = issue_authority(now=int(time.time()) - 1)
    _environment(monkeypatch, authority)
    monkeypatch.setattr(runtime_module, "_CHAT_RATE_LIMIT", 1)
    app = create_agent_app(AgentSettings.from_environment())
    headers = {"X-Ratify-Demo-Token": "Bearer test-demo-token"}

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

    agent_dockerfile = dockerfiles[0].read_text()
    assert "--mount=type=secret,id=ratify_delegation_b64" in agent_dockerfile
    assert "--mount=type=secret,id=ratify_scenario_authorities_gzip_b64" in agent_dockerfile
    assert "gzip --decompress" in agent_dockerfile
    assert "RATIFY_DELEGATION_SHA256" in agent_dockerfile
    assert "RATIFY_SCENARIO_AUTHORITIES_SHA256" in agent_dockerfile
    assert "RATIFY_DELEGATION_PATH=/app/deployment/delegation.json" in agent_dockerfile


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_runtime_isolation_across_two_agent_runtimes(tmp_path, monkeypatch):
    asyncio.run(_exercise_runtime_isolation(tmp_path, monkeypatch))


def _settings_from(issuance, env_name, delegation, authorities, monkeypatch):
    for name, value in dict(
        line.split("=", 1)
        for line in (issuance / env_name).read_text().splitlines()
    ).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("RATIFY_DELEGATION_PATH", str(issuance / delegation))
    monkeypatch.setenv("RATIFY_SCENARIO_AUTHORITIES_PATH", str(issuance / authorities))
    monkeypatch.setenv("RATIFY_RECEIVER_MCP_URL", "unused")
    monkeypatch.setenv("RATIFY_PRESENTATION_URL", "unused")
    return AgentSettings.from_environment()


def test_measured_spans_separate_our_work_from_transit(tmp_path, monkeypatch):
    asyncio.run(_exercise_measured_spans(tmp_path, monkeypatch))


async def _exercise_measured_spans(tmp_path, monkeypatch):
    """Every reported span must be observed, not asserted.

    Maritime pushed back on treating one round-trip figure as our cost. These
    spans exist to separate our cryptography from platform transit, so a
    hardcoded or missing value would defeat the reason they are published.
    """
    issuance = tmp_path / "issuance"
    issue_deployment(issuance, now=int(time.time()))
    receiver_env = dict(
        line.split("=", 1)
        for line in (issuance / "receiver.env").read_text().splitlines()
    )
    settings = _settings_from(
        issuance, "agent.env", "delegation.json",
        "scenario-authorities.json", monkeypatch,
    )
    receiver = WorkOrderReceiver(
        trusted_root_id=settings.authority.root_id,
        trusted_root_public_key=settings.authority.root_public_key,
    )
    receiver.revocation.revoke(receiver_env["RATIFY_REVOKED_CERT_IDS"])
    port = _unused_port()
    app = create_receiver_app(
        receiver=receiver,
        authenticator=CallerAuthenticator({
            settings.receiver_token: receiver_env["RATIFY_CALLER_ID_PRIMARY"]
        }),
        presentations=PresentationRegistry(),
        caller_subjects={
            receiver_env["RATIFY_CALLER_ID_PRIMARY"]: settings.authority.agent_id
        },
        allowed_hosts=[f"127.0.0.1:{port}"],
    )
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error"
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started
    connected = AgentSettings(
        authority=settings.authority,
        scenario_authorities=settings.scenario_authorities,
        supported_scenarios=settings.supported_scenarios,
        receiver_mcp_url=f"http://127.0.0.1:{port}/mcp/",
        presentation_url=f"http://127.0.0.1:{port}/presentations",
        receiver_token=settings.receiver_token,
        demo_token=settings.demo_token,
        model_mode="deterministic",
        model_id=None,
    )
    spans = (
        "challenge_duration_ms", "proof_build_duration_ms",
        "proof_upload_duration_ms", "dispatch_duration_ms",
        "interceptor_duration_ms",
    )
    try:
        allowed = await run_scenario(connected, "allow")
        denied = await run_scenario(connected, "wrong_agent")
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    for name in spans:
        assert isinstance(allowed[name], int), name
        assert allowed[name] >= 0, name
    # The parts cannot exceed the whole they were measured inside.
    assert allowed["interceptor_duration_ms"] >= max(
        allowed[name] for name in spans if name != "interceptor_duration_ms"
    )
    # Hybrid signing is real work, so a zero here would mean nothing was timed.
    assert allowed["proof_build_duration_ms"] > 0
    assert allowed["verification_duration_ms"] > 0

    # A refusal reached before verification must report no verification time,
    # which is what keeps the number meaningful rather than decorative.
    assert denied["decided_by"] == "receiver_precheck"
    assert denied["verification_duration_ms"] is None


async def _exercise_runtime_isolation(tmp_path, monkeypatch):
    """Two separately delegated runtimes against one receiver.

    Neither runtime holds the other's private key. The second runtime carries
    the first runtime's public certificate, which is what an operator of that
    runtime could actually obtain.
    """
    issuance = tmp_path / "issuance"
    issue_deployment(issuance, now=int(time.time()))
    receiver_env = dict(
        line.split("=", 1)
        for line in (issuance / "receiver.env").read_text().splitlines()
    )
    primary = _settings_from(
        issuance, "agent.env", "delegation.json",
        "scenario-authorities.json", monkeypatch,
    )
    secondary = _settings_from(
        issuance, "agent-b.env", "delegation-b.json",
        "scenario-authorities-b.json", monkeypatch,
    )
    assert secondary.supported_scenarios == _SECONDARY_SCENARIOS
    assert secondary.authority.agent_id != primary.authority.agent_id

    receiver = WorkOrderReceiver(
        trusted_root_id=primary.authority.root_id,
        trusted_root_public_key=primary.authority.root_public_key,
    )
    port = _unused_port()
    app = create_receiver_app(
        receiver=receiver,
        authenticator=CallerAuthenticator({
            primary.receiver_token: receiver_env["RATIFY_CALLER_ID_PRIMARY"],
            secondary.receiver_token: receiver_env["RATIFY_CALLER_ID_SECONDARY"],
        }),
        presentations=PresentationRegistry(),
        caller_subjects={
            receiver_env["RATIFY_CALLER_ID_PRIMARY"]: primary.authority.agent_id,
            receiver_env["RATIFY_CALLER_ID_SECONDARY"]: secondary.authority.agent_id,
        },
        allowed_hosts=[f"127.0.0.1:{port}"],
    )
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error"
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started

    def connected(settings):
        return AgentSettings(
            authority=settings.authority,
            scenario_authorities=settings.scenario_authorities,
            supported_scenarios=settings.supported_scenarios,
            receiver_mcp_url=f"http://127.0.0.1:{port}/mcp/",
            presentation_url=f"http://127.0.0.1:{port}/presentations",
            receiver_token=settings.receiver_token,
            demo_token=settings.demo_token,
            model_mode="deterministic",
            model_id=None,
        )

    contract = json.loads(
        (Path(__file__).resolve().parents[1] / "docs"
         / "runtime-isolation-expectations.json").read_text(encoding="utf-8")
    )["attempts"]
    runtimes = {"primary": connected(primary), "secondary": connected(secondary)}
    try:
        for name, required in contract.items():
            result = await run_scenario(
                runtimes[required["runtime"]], required["scenario"]
            )
            assert (
                result["decision"],
                result["reason"],
                result["handler_invoked"],
                result["decided_by"],
                result["verification_status"],
            ) == (
                required["decision"],
                required["reason"],
                required["handler_invoked"],
                required["decided_by"],
                required["verification_status"],
            ), name
        # Exactly the two in-bounds attempts reached protected code.
        assert receiver.handler_invocations == 2
    finally:
        server.should_exit = True
        thread.join(timeout=5)

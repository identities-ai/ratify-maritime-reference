"""Import and exercise the executable gate scripts.

These run against the live deployment, so they are not covered by the rest of
the suite. Nothing imported them either, which let a wrong variable name reach
main: the script parsed, and the failure only appeared when someone ran it.
These tests give the scripts the coverage their file extension implies.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves a class's module through sys.modules, so a module
    # executed without being registered there fails to define one.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[name]
        raise
    return module


@pytest.mark.parametrize("name", [
    "run_live_adversarial_gate",
    "run_runtime_isolation_gate",
    "reproduce_gate_locally",
    "measure_decision_latency",
    "issue_demo_authority",
])
def test_every_gate_script_imports(name):
    _load(name)


@pytest.mark.parametrize("name, required", [
    ("run_live_adversarial_gate", {"deployment_identity", "image_binding"}),
    ("run_runtime_isolation_gate", {"runtime_identity", "image_binding"}),
])
def test_gates_disclose_what_a_digest_cannot_bind(name, required):
    disclosures = _load(name).DISCLOSURES
    assert required <= set(disclosures)
    binding = disclosures["image_binding"]
    # The specific limit Maritime confirmed, so a future edit cannot quietly
    # soften it back into implying the platform verified a digest.
    assert "no digest verification at launch" in binding
    assert "maritime_attestation" in binding


def test_issuance_exposes_both_commands_without_running_them():
    parser = _load("issue_demo_authority").build_parser()
    assert parser.parse_args(["issue", "/tmp/out"]).command == "issue"
    renewal = parser.parse_args(["renew", "/tmp/principal.json", "/tmp/out"])
    assert (renewal.command, str(renewal.principal)) == (
        "renew", "/tmp/principal.json"
    )


def test_both_gates_read_one_shared_contract():
    """The required results must not drift between the gate and the test."""
    adversarial = _load("run_live_adversarial_gate")
    contract = json.loads(
        (SCRIPTS.parent / "docs" / "gate-expectations.json").read_text()
    )["scenarios"]
    assert set(adversarial.EXPECTED) == set(contract)
    for scenario, expected in adversarial.EXPECTED.items():
        required = contract[scenario]
        assert expected == (
            required["decision"],
            required["reason"],
            required["handler_invoked"],
            required["decided_by"],
            required["verification_status"],
        )


@pytest.mark.parametrize("name", [
    "run_live_adversarial_gate", "run_runtime_isolation_gate",
])
def test_both_gates_disclose_retried_platform_stalls(name):
    """A retried stall must be recorded, never silently absorbed."""
    disclosures = _load(name).DISCLOSURES
    assert "transient_failures" in disclosures
    assert "never retried" in disclosures["transient_failures"]


def test_registry_check_rejects_a_revision_mismatch(monkeypatch):
    gate = _load("run_live_adversarial_gate")
    monkeypatch.setattr(gate, "_observed_revision", lambda image: "aaa")
    assert gate._verify_image_identity("ghcr.io/x@sha256:1", "aaa") == (
        "observed", "aaa"
    )
    assert gate._verify_image_identity("ghcr.io/x@sha256:1", "bbb") == (
        "mismatch", "aaa"
    )
    monkeypatch.setattr(gate, "_observed_revision", lambda image: None)
    assert gate._verify_image_identity("ghcr.io/x@sha256:1", "aaa") == (
        "unverified", None
    )


def test_acceptance_gate_detects_stale_evidence(monkeypatch, tmp_path):
    """The gate has to fail on the staleness it exists to catch."""
    gate = _load("run_acceptance_gate")
    assert gate._evidence_is_current()[0] == "pass"

    stale = tmp_path / "repo"
    (stale / "evidence").mkdir(parents=True)
    (stale / "scripts").mkdir()
    (stale / "scripts" / "reproduce_gate_locally.py").write_text("sha256:deadbeef")
    for name in ("adversarial-results", "runtime-isolation-results"):
        (stale / "evidence" / f"{name}.json").write_text(json.dumps({
            "passed": True,
            "deployment": {
                "maritime_attestation": None,
                "agent_image": "ghcr.io/a@sha256:aaa",
                "receiver_image": "ghcr.io/b@sha256:bbb",
            },
            "disclosures": {
                "image_binding": "", "evidence_sha256": "",
                "transient_failures": "",
            },
        }))
    monkeypatch.setattr(gate, "REPOSITORY", stale)
    status, detail = gate._evidence_is_current()
    assert status == "FAIL"
    assert "does not default to the deployed agent image" in detail


def test_live_gate_checks_are_built_from_the_recorded_deployment():
    """The live gates must target the deployment the evidence describes.

    Hardcoding the arguments would let the gate pass against a deployment the
    published evidence no longer describes, which is the drift this repository
    keeps finding.
    """
    gate = _load("run_acceptance_gate")
    isolation = json.loads(
        (SCRIPTS.parent / "evidence" / "runtime-isolation-results.json")
        .read_text(encoding="utf-8")
    )["deployment"]
    checks = gate._live_gate_checks()

    assert [c.name for c in checks] == [
        "Live adversarial gate", "Live runtime isolation gate"
    ]
    assert all(c.live for c in checks)
    arguments = checks[1].command
    for value in (
        isolation["primary_runtime_id"], isolation["secondary_runtime_id"],
        isolation["receiver_runtime_id"], isolation["agent_image"],
        isolation["worker_version"],
    ):
        assert value in arguments
    # Results go to scratch, never over the published artifacts.
    assert any(".acceptance" in part for part in arguments)
    assert not any("evidence/" in part for part in arguments)

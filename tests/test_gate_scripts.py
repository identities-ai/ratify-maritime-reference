"""Import and exercise the executable gate scripts.

These run against the live deployment, so they are not covered by the rest of
the suite. Nothing imported them either, which let a wrong variable name reach
main: the script parsed, and the failure only appeared when someone ran it.
These tests give the scripts the coverage their file extension implies.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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

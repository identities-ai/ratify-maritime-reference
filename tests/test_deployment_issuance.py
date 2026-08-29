import base64
import hashlib
import json
import stat

import pytest
from ratify_protocol import decode_delegation_cert, verify_delegation_signature
from ratify_protocol import generate_human_root

from maritime_ratify.deployment_issuance import (
    DEMO_VALIDITY_SECONDS,
    issue_deployment,
    renew_deployment,
)


def _env(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def test_issuance_separates_principal_receiver_agent_and_public_manifest(tmp_path):
    output = tmp_path / "issuance"

    issue_deployment(output, now=1_800_000_000)

    principal = json.loads((output / "principal.json").read_text())
    receiver = _env(output / "receiver.env")
    agent = _env(output / "agent.env")
    manifest_text = (output / "manifest.json").read_text()
    manifest = json.loads(manifest_text)
    delegation = decode_delegation_cert((output / "delegation.json").read_text())
    scenarios = json.loads((output / "scenario-authorities.json").read_text())

    assert set(path.name for path in output.iterdir()) == {
        "agent.env", "delegation.json", "manifest.json", "principal.json",
        "receiver.env", "scenario-authorities.json"
    }
    assert "root_private_key" in principal
    assert "agent_private_key" in principal
    assert "wrong_agent_private_key" in principal
    assert not any("PRIVATE" in key for key in receiver)
    assert "RATIFY_ROOT_ID" not in agent
    assert "RATIFY_AGENT_ED25519_PRIVATE_B64" in agent
    assert agent["RATIFY_DELEGATION_PATH"] == "/app/deployment/delegation.json"
    assert agent["RATIFY_SCENARIO_AUTHORITIES_PATH"] == "/app/deployment/scenario-authorities.json"
    assert "RATIFY_WRONG_AGENT_ED25519_PRIVATE_B64" in agent
    assert receiver["RATIFY_REVOKED_CERT_IDS"] == decode_delegation_cert(
        scenarios["revoked"]
    ).cert_id
    assert agent["RATIFY_RECEIVER_TOKEN"] == receiver["RATIFY_CALLER_TOKEN"]
    assert agent["RATIFY_DEMO_TOKEN"] != agent["RATIFY_RECEIVER_TOKEN"]
    assert delegation.expires_at - delegation.issued_at == DEMO_VALIDITY_SECONDS
    assert verify_delegation_signature(delegation)
    assert manifest["agent_id"] == delegation.subject_id
    assert manifest["expires_at"] == delegation.expires_at
    delegation_bytes = (output / "delegation.json").read_bytes()
    assert not delegation_bytes.endswith(b"\n")
    assert manifest["delegation_sha256"] == hashlib.sha256(delegation_bytes).hexdigest()
    assert manifest["scenario_authorities_sha256"] == hashlib.sha256(
        (output / "scenario-authorities.json").read_bytes()
    ).hexdigest()
    assert principal["root_private_key"]["ed25519"] not in manifest_text
    assert principal["agent_private_key"]["ed25519"] not in manifest_text

    for name in (
        "principal.json", "receiver.env", "agent.env", "scenario-authorities.json"
    ):
        assert stat.S_IMODE((output / name).stat().st_mode) == 0o600
    assert stat.S_IMODE((output / "manifest.json").stat().st_mode) == 0o644


def test_renewal_preserves_identity_and_emits_only_new_delegation(tmp_path):
    initial = tmp_path / "initial"
    renewal = tmp_path / "renewal"
    issue_deployment(initial, now=1_800_000_000)
    original = decode_delegation_cert((initial / "delegation.json").read_text())

    renew_deployment(initial / "principal.json", renewal, now=1_800_432_000)

    renewed = decode_delegation_cert((renewal / "delegation.json").read_text())
    assert set(path.name for path in renewal.iterdir()) == {
        "delegation.json", "manifest.json", "scenario-authorities.json"
    }
    assert renewed.issuer_id == original.issuer_id
    assert renewed.subject_id == original.subject_id
    assert renewed.subject_pub_key == original.subject_pub_key
    assert renewed.scope == original.scope
    assert renewed.constraints == original.constraints
    assert renewed.cert_id != original.cert_id
    assert renewed.issued_at == 1_800_432_000
    assert renewed.expires_at - renewed.issued_at == DEMO_VALIDITY_SECONDS
    assert verify_delegation_signature(renewed)
    delegation_bytes = (renewal / "delegation.json").read_bytes()
    manifest = json.loads((renewal / "manifest.json").read_text())
    assert not delegation_bytes.endswith(b"\n")
    assert manifest["delegation_sha256"] == hashlib.sha256(delegation_bytes).hexdigest()
    assert stat.S_IMODE((renewal / "delegation.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((renewal / "scenario-authorities.json").stat().st_mode) == 0o600


def test_issuance_refuses_to_overwrite_an_existing_directory(tmp_path):
    output = tmp_path / "issuance"
    output.mkdir()

    try:
        issue_deployment(output, now=1_800_000_000)
    except FileExistsError:
        pass
    else:
        raise AssertionError("issuance must not overwrite an existing directory")


@pytest.mark.parametrize("tamper", [
    "foreign_root_private", "short_root_public", "short_agent_public",
    "swapped_root_id", "swapped_agent_id", "short_root_private",
    "missing_root_private", "invalid_root_private",
])
def test_renewal_rejects_inconsistent_principal_without_output(tmp_path, tamper):
    initial = tmp_path / "initial"
    renewal = tmp_path / "renewal"
    issue_deployment(initial, now=1_800_000_000)
    path = initial / "principal.json"
    principal = json.loads(path.read_text())

    if tamper == "foreign_root_private":
        _, foreign = generate_human_root()
        principal["root_private_key"] = {
            "ed25519": base64.b64encode(foreign.ed25519).decode(),
            "ml_dsa_65": base64.b64encode(foreign.ml_dsa_65).decode(),
        }
    elif tamper == "short_root_public":
        principal["root_public_key"]["ed25519"] = base64.b64encode(b"bad").decode()
    elif tamper == "short_agent_public":
        principal["agent_public_key"]["ed25519"] = base64.b64encode(b"bad").decode()
    elif tamper == "swapped_root_id":
        principal["root_id"] = "root:tampered"
    elif tamper == "swapped_agent_id":
        principal["agent_id"] = "agent:tampered"
    elif tamper == "short_root_private":
        principal["root_private_key"]["ed25519"] = base64.b64encode(b"bad").decode()
    elif tamper == "missing_root_private":
        principal.pop("root_private_key")
    else:
        principal["root_private_key"]["ed25519"] = "not-base64"
    path.write_text(json.dumps(principal))

    with pytest.raises(RuntimeError, match="principal artifact"):
        renew_deployment(path, renewal, now=1_800_432_000)

    assert not renewal.exists()

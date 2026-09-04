import base64
import hashlib
import json
import stat
from pathlib import Path

import pytest
from ratify_protocol import decode_delegation_cert, verify_delegation_signature
from ratify_protocol import generate_human_root

from maritime_ratify.deployment_issuance import (
    DEMO_VALIDITY_SECONDS,
    issue_deployment,
    renew_deployment,
)
from maritime_ratify.profile import (
    SECOND_MAX_AMOUNT_MINOR,
    SECOND_RESOURCE,
    VOLUME_DIRECTORY,
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
        "agent.env", "agent-b.env", "delegation.json", "delegation-b.json",
        "manifest.json", "principal.json", "receiver.env",
        "scenario-authorities.json", "scenario-authorities-b.json",
    }
    assert "root_private_key" in principal
    assert "agent_private_key" in principal
    assert "wrong_agent_private_key" in principal
    assert not any("PRIVATE" in key for key in receiver)
    assert "RATIFY_ROOT_ID" not in agent
    assert "RATIFY_AGENT_ED25519_PRIVATE_B64" in agent
    # Issued configuration must point at the volume the deployment reads from,
    # and must carry the digests. Naming the files without them would start a
    # runtime with the integrity check silently disabled.
    assert agent["RATIFY_DELEGATION_PATH"] == f"{VOLUME_DIRECTORY}/delegation.json"
    assert agent["RATIFY_SCENARIO_AUTHORITIES_PATH"] == (
        f"{VOLUME_DIRECTORY}/scenario-authorities.json"
    )
    assert agent["RATIFY_DELEGATION_SHA256"] == hashlib.sha256(
        (output / "delegation.json").read_bytes()
    ).hexdigest()
    assert agent["RATIFY_SCENARIO_AUTHORITIES_SHA256"] == hashlib.sha256(
        (output / "scenario-authorities.json").read_bytes()
    ).hexdigest()
    assert "RATIFY_WRONG_AGENT_ED25519_PRIVATE_B64" not in agent
    assert set(scenarios["wrong_agent_fixture_private_key"]) == {
        "ed25519", "ml_dsa_65"
    }
    assert receiver["RATIFY_REVOKED_CERT_IDS"] == decode_delegation_cert(
        scenarios["revoked"]
    ).cert_id
    assert agent["RATIFY_RECEIVER_TOKEN"] == receiver["RATIFY_CALLER_TOKEN_PRIMARY"]
    assert agent["RATIFY_DEMO_TOKEN"] != agent["RATIFY_RECEIVER_TOKEN"]

    # The second runtime is a separate tenant: its own subject, its own
    # transport credential, its own bounds, and no access to the first agent's
    # private key.
    second_agent = _env(output / "agent-b.env")
    second_delegation = decode_delegation_cert(
        (output / "delegation-b.json").read_text()
    )
    peer = json.loads((output / "scenario-authorities-b.json").read_text())
    assert second_agent["RATIFY_RECEIVER_TOKEN"] == receiver[
        "RATIFY_CALLER_TOKEN_SECONDARY"
    ]
    assert second_agent["RATIFY_RECEIVER_TOKEN"] != agent["RATIFY_RECEIVER_TOKEN"]
    assert second_agent["RATIFY_DEMO_TOKEN"] != agent["RATIFY_DEMO_TOKEN"]
    assert second_agent["RATIFY_AGENT_ED25519_PRIVATE_B64"] != agent[
        "RATIFY_AGENT_ED25519_PRIVATE_B64"
    ]
    assert second_delegation.subject_id == receiver["RATIFY_AGENT_ID_SECONDARY"]
    assert second_delegation.subject_id != delegation.subject_id
    assert verify_delegation_signature(second_delegation)
    assert [
        constraint.resource_id for constraint in second_delegation.constraints
        if constraint.type == "resource_path"
    ] == [SECOND_RESOURCE]
    assert [
        constraint.max_amount for constraint in second_delegation.constraints
        if constraint.type == "max_amount"
    ] == [SECOND_MAX_AMOUNT_MINOR / 100]
    assert set(peer) == {"peer_delegation"}
    assert decode_delegation_cert(peer["peer_delegation"]).subject_id == (
        delegation.subject_id
    )
    assert principal["agent_private_key"]["ed25519"] not in json.dumps(peer)
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
        "principal.json", "receiver.env", "agent.env", "agent-b.env",
        "scenario-authorities.json", "delegation-b.json",
        "scenario-authorities-b.json",
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
        "delegation.json", "delegation-b.json", "manifest.json",
        "scenario-authorities.json", "scenario-authorities-b.json",
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

    # Renewal has to replace both tenants, or the second runtime keeps an
    # expiring delegation while the first is refreshed.
    original_second = decode_delegation_cert(
        (initial / "delegation-b.json").read_text()
    )
    renewed_second = decode_delegation_cert(
        (renewal / "delegation-b.json").read_text()
    )
    assert renewed_second.subject_id == original_second.subject_id
    assert renewed_second.constraints == original_second.constraints
    assert renewed_second.cert_id != original_second.cert_id
    assert renewed_second.issued_at == 1_800_432_000
    assert verify_delegation_signature(renewed_second)
    assert json.loads(
        (renewal / "scenario-authorities-b.json").read_text()
    )["peer_delegation"] == (renewal / "delegation.json").read_text()

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


def test_issued_configuration_points_where_the_image_reads(tmp_path):
    """Issued configuration has to work against the image that is deployed.

    It previously named /app/deployment, a path the image stopped carrying when
    authority moved to the volume. Following the issuance output would have
    configured a runtime that could not start, and nothing noticed because no
    test compared the two.
    """
    output = tmp_path / "issuance"
    issue_deployment(output, now=1_800_000_000)
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()

    for name in ("agent.env", "agent-b.env"):
        environment = _env(output / name)
        for key in ("RATIFY_DELEGATION_PATH", "RATIFY_SCENARIO_AUTHORITIES_PATH"):
            assert environment[key].startswith(f"{VOLUME_DIRECTORY}/"), (name, key)
        # The image must not be expected to supply what the volume now holds.
        assert "/app/deployment" not in dockerfile

    # Both runtimes read the same path on their own volume, and are told apart
    # by the material written there rather than by the filename.
    assert _env(output / "agent.env")["RATIFY_DELEGATION_PATH"] == (
        _env(output / "agent-b.env")["RATIFY_DELEGATION_PATH"]
    )
    assert _env(output / "agent.env")["RATIFY_DELEGATION_SHA256"] != (
        _env(output / "agent-b.env")["RATIFY_DELEGATION_SHA256"]
    )

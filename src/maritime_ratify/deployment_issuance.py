"""Offline issuance artifacts for the Maritime demonstration."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from ratify_protocol.v2 import (
    HybridPrivateKey,
    HybridPublicKey,
    V2Error,
    generate_keypair_v2,
    key_id_v2,
)
from ratify_protocol.v2.protocol_v2 import (
    decode_revocation_list_v2,
    encode_delegation_cert_v2,
    verify_delegation_cert_v2_signature,
)
from ratify_protocol.v2.signing_v2 import public_key_of

from .authority import issue_bounded_delegation
from .profile import (
    VOLUME_DIRECTORY,
    DEFAULT_CATEGORY,
    DEFAULT_CURRENCY,
    DEFAULT_MAX_AMOUNT_MINOR,
    DEFAULT_RESOURCE,
    PROTOCOL_LINE,
    RECEIVER_ID,
    SECOND_MAX_AMOUNT_MINOR,
    SECOND_RESOURCE,
    WORK_ORDER_SCOPE,
)
from .status import StatusAuthority

DEMO_VALIDITY_SECONDS = 7 * 24 * 60 * 60
STATUS_STATE_FILE = "status-state.json"
STATUS_LIST_FILE = "status-list.json"
_PRINCIPAL_KEYS = ("root", "status", "agent", "wrong_agent", "second_agent")


def issue_deployment(output: Path, *, now: int | None = None) -> None:
    issued_at = int(time.time()) if now is None else now
    keys = {name: generate_keypair_v2() for name in _PRINCIPAL_KEYS}
    _, verifier_private = generate_keypair_v2()
    delegations = _issue_scenario_delegations(keys, issued_at)
    status = StatusAuthority(
        root_public_key=keys["root"][0],
        status_private_key=keys["status"][1],
        status_public_key=keys["status"][0],
    )
    status_wire = status.publish(
        revoked=[delegations["revoked"].cert_id], at=issued_at
    )
    receiver_token = secrets.token_urlsafe(32)
    demo_token = secrets.token_urlsafe(32)
    second_receiver_token = secrets.token_urlsafe(32)
    second_demo_token = secrets.token_urlsafe(32)
    _prepare_output(output)
    _write_private_json(output / "principal.json", {
        f"{name}_{part}": value
        for name, (public, private) in keys.items()
        for part, value in (
            ("id", key_id_v2(public)),
            ("public_key", _public(public)),
            ("private_key", _private(private)),
        )
    })
    _write_private(output / STATUS_STATE_FILE, status.state_json())
    _write_private_env(output / "receiver.env", _receiver_environment(
        root_public=keys["root"][0],
        status_public=keys["status"][0],
        verifier_private=verifier_private,
        primary_agent_public=keys["agent"][0],
        primary_token=receiver_token,
        secondary_agent_public=keys["second_agent"][0],
        secondary_token=second_receiver_token,
    ))
    wire = _wire(delegations["active"])
    scenario_wire = _scenario_authorities_wire(delegations, keys["wrong_agent"][1])
    second_wire = _wire(delegations["second"])
    peer_wire = _peer_authorities_wire(wire)
    _write_private_env(output / "agent.env", _agent_environment(
        keys["agent"][1], receiver_token, demo_token,
        delegation_wire=wire, authorities_wire=scenario_wire,
    ))
    _write_private_env(output / "agent-b.env", _agent_environment(
        keys["second_agent"][1], second_receiver_token, second_demo_token,
        delegation_wire=second_wire, authorities_wire=peer_wire,
    ))
    _write_private(output / "delegation.json", wire)
    _write_private(output / "scenario-authorities.json", scenario_wire)
    _write_private(output / "delegation-b.json", second_wire)
    _write_private(output / "scenario-authorities-b.json", peer_wire)
    _write_public(output / STATUS_LIST_FILE, status_wire.decode("utf-8"))
    _write_manifest(
        output / "manifest.json", delegations, wire, scenario_wire,
        second_wire, peer_wire, status_wire,
    )


def renew_deployment(principal_path: Path, output: Path, *, now: int | None = None) -> None:
    """Re-issue every delegation for the same keys and re-sign status.

    The new revoked fixture certificate is added to the cumulative signed
    revocation list. The status publisher state next to ``principal.json`` is
    advanced in place, because the next signing must continue its sequence.
    """
    issued_at = int(time.time()) if now is None else now
    keys = _load_principal(principal_path)
    status = _load_status_authority(principal_path, keys)
    delegations = _issue_scenario_delegations(keys, issued_at)
    if any(
        _verification_error(delegation) for delegation in delegations.values()
    ):
        raise RuntimeError("principal artifact is inconsistent")
    status_wire = status.publish(
        revoked=[delegations["revoked"].cert_id], at=issued_at
    )
    _prepare_output(output)
    wire = _wire(delegations["active"])
    scenario_wire = _scenario_authorities_wire(delegations, keys["wrong_agent"][1])
    second_wire = _wire(delegations["second"])
    peer_wire = _peer_authorities_wire(wire)
    _write_private(output / "delegation.json", wire)
    _write_private(output / "scenario-authorities.json", scenario_wire)
    _write_private(output / "delegation-b.json", second_wire)
    _write_private(output / "scenario-authorities-b.json", peer_wire)
    _write_public(output / STATUS_LIST_FILE, status_wire.decode("utf-8"))
    _write_manifest(
        output / "manifest.json", delegations, wire, scenario_wire,
        second_wire, peer_wire, status_wire,
    )
    _replace_private(principal_path.parent / STATUS_STATE_FILE, status.state_json())


def refresh_status(principal_path: Path, output: Path, *, now: int | None = None) -> None:
    """Re-sign the current revocation list with a later ``updated_at``.

    A signed list is current for at most one day, so the principal runs this
    at least daily and writes the result to the receiver's volume.
    """
    at = int(time.time()) if now is None else now
    keys = _load_principal(principal_path)
    status = _load_status_authority(principal_path, keys)
    status_wire = status.publish(revoked=[], at=at)
    _prepare_output(output)
    _write_public(output / STATUS_LIST_FILE, status_wire.decode("utf-8"))
    _replace_private(principal_path.parent / STATUS_STATE_FILE, status.state_json())


def _issue_scenario_delegations(keys: dict, issued_at: int) -> dict[str, object]:
    root_public, root_private = keys["root"]

    def issue(agent: str, **options):
        options.setdefault("issued_at", issued_at)
        options.setdefault("expires_at", issued_at + DEMO_VALIDITY_SECONDS)
        return issue_bounded_delegation(
            root_public_key=root_public,
            root_private_key=root_private,
            agent_public_key=keys[agent][0],
            **options,
        )

    return {
        "active": issue("agent"),
        "expired": issue(
            "agent",
            issued_at=issued_at - (2 * DEMO_VALIDITY_SECONDS),
            expires_at=issued_at - DEMO_VALIDITY_SECONDS,
        ),
        "revoked": issue("agent"),
        # A second tenant on the same receiver: different subject, different
        # site, and a lower ceiling, so the pair separates agent authority from
        # receiver capability.
        "second": issue(
            "second_agent",
            resource=SECOND_RESOURCE,
            max_amount_minor=SECOND_MAX_AMOUNT_MINOR,
        ),
        "wrong_agent": issue("wrong_agent"),
    }


def _verification_error(delegation) -> bool:
    try:
        verify_delegation_cert_v2_signature(delegation)
    except (V2Error, ValueError):
        return True
    return False


def _receiver_environment(
    *,
    root_public: HybridPublicKey,
    status_public: HybridPublicKey,
    verifier_private: HybridPrivateKey,
    primary_agent_public: HybridPublicKey,
    primary_token: str,
    secondary_agent_public: HybridPublicKey,
    secondary_token: str,
) -> dict[str, str]:
    """Receiver configuration binding each caller to exactly one agent key.

    Trust is pinned to complete public keys: the root that issues
    delegations and the separate status key that signs revocation status.
    The verifier key signs this receiver's receipts and stays in the
    receiver alone.
    """
    return {
        "RATIFY_ROOT_ID": key_id_v2(root_public),
        "RATIFY_ROOT_ED25519_B64": _b64(root_public.ed25519),
        "RATIFY_ROOT_ML_DSA_65_B64": _b64(root_public.ml_dsa_65),
        "RATIFY_STATUS_KEY_ID": key_id_v2(status_public),
        "RATIFY_STATUS_ED25519_B64": _b64(status_public.ed25519),
        "RATIFY_STATUS_ML_DSA_65_B64": _b64(status_public.ml_dsa_65),
        "RATIFY_STATUS_LIST_PATH": f"{VOLUME_DIRECTORY}/{STATUS_LIST_FILE}",
        "RATIFY_VERIFIER_ED25519_PRIVATE_B64": _b64(verifier_private.ed25519),
        "RATIFY_VERIFIER_ML_DSA_65_PRIVATE_B64": _b64(verifier_private.ml_dsa_65),
        "RATIFY_CALLER_SLOTS": "primary,secondary",
        "RATIFY_CALLER_ID_PRIMARY": "maritime-demo-agent",
        "RATIFY_CALLER_TOKEN_PRIMARY": primary_token,
        "RATIFY_AGENT_ID_PRIMARY": key_id_v2(primary_agent_public),
        "RATIFY_AGENT_ED25519_B64_PRIMARY": _b64(primary_agent_public.ed25519),
        "RATIFY_AGENT_ML_DSA_65_B64_PRIMARY": _b64(primary_agent_public.ml_dsa_65),
        "RATIFY_CALLER_ID_SECONDARY": "maritime-demo-agent-b",
        "RATIFY_CALLER_TOKEN_SECONDARY": secondary_token,
        "RATIFY_AGENT_ID_SECONDARY": key_id_v2(secondary_agent_public),
        "RATIFY_AGENT_ED25519_B64_SECONDARY": _b64(secondary_agent_public.ed25519),
        "RATIFY_AGENT_ML_DSA_65_B64_SECONDARY": _b64(secondary_agent_public.ml_dsa_65),
    }


def _agent_environment(
    private_key: HybridPrivateKey,
    receiver_token: str,
    demo_token: str,
    *,
    delegation_wire: str,
    authorities_wire: str,
) -> dict[str, str]:
    """Configuration for one runtime, pointing at its own volume.

    Both runtimes run the same image and differ only by injected authority. The
    image carries none of it: each runtime reads its delegation and fixture
    from its persistent volume and verifies them against the digests emitted
    here, so a rotation is a write and a restart rather than an image rebuild.

    The digests are emitted alongside the paths deliberately. Configuration that
    named the files without them would start a runtime with the integrity check
    silently disabled.
    """
    return {
        "RATIFY_DELEGATION_PATH": f"{VOLUME_DIRECTORY}/delegation.json",
        "RATIFY_SCENARIO_AUTHORITIES_PATH":
            f"{VOLUME_DIRECTORY}/scenario-authorities.json",
        "RATIFY_DELEGATION_SHA256":
            hashlib.sha256(delegation_wire.encode()).hexdigest(),
        "RATIFY_SCENARIO_AUTHORITIES_SHA256":
            hashlib.sha256(authorities_wire.encode()).hexdigest(),
        "RATIFY_AGENT_ED25519_PRIVATE_B64": _b64(private_key.ed25519),
        "RATIFY_AGENT_ML_DSA_65_PRIVATE_B64": _b64(private_key.ml_dsa_65),
        "RATIFY_RECEIVER_TOKEN": receiver_token,
        "RATIFY_DEMO_TOKEN": demo_token,
        "RATIFY_MODEL_MODE": "deterministic",
    }


def _wire(delegation) -> str:
    return encode_delegation_cert_v2(delegation).decode("utf-8")


def _peer_authorities_wire(peer_delegation_wire: str) -> str:
    """The second runtime carries the first runtime's public certificate only.

    It never receives the first agent's private key, which is what makes the
    cross-runtime attempts meaningful rather than staged.
    """
    return json.dumps(
        {"peer_delegation": peer_delegation_wire},
        sort_keys=True,
        separators=(",", ":"),
    )


def _scenario_authorities_wire(
    delegations: dict[str, object], wrong_agent_private: HybridPrivateKey
) -> str:
    return json.dumps({
        **{
            name: _wire(delegations[name])
            for name in ("expired", "revoked", "wrong_agent")
        },
        "wrong_agent_fixture_private_key": _private(wrong_agent_private),
    }, sort_keys=True, separators=(",", ":"))


def _prepare_output(output: Path) -> None:
    output.mkdir(mode=0o700, parents=False, exist_ok=False)


def _write_private_json(path: Path, value: dict) -> None:
    _write_private(path, json.dumps(value, sort_keys=True, indent=2) + "\n")


def _write_private_env(path: Path, values: dict[str, str]) -> None:
    _write_private(path, "".join(
        f"{key}={value}\n" for key, value in values.items()
    ))


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)


def _write_public(path: Path, content: str) -> None:
    """A signed status list is public; only its signature makes it count."""
    _write_private(path, content)
    os.chmod(path, 0o644)


def _replace_private(path: Path, content: str) -> None:
    staging = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    _write_private(staging, content)
    os.replace(staging, path)


def _write_manifest(
    path: Path,
    delegations: dict[str, object],
    wire: str,
    scenario_wire: str,
    second_wire: str,
    peer_wire: str,
    status_wire: bytes,
) -> None:
    delegation = delegations["active"]
    status_list = decode_revocation_list_v2(status_wire)
    path.write_text(json.dumps({
        "protocol_line": PROTOCOL_LINE,
        "agent_id": delegation.subject_id,
        "audience": RECEIVER_ID,
        "category": DEFAULT_CATEGORY,
        "cert_id": delegation.cert_id,
        "currency": DEFAULT_CURRENCY,
        "delegation_sha256": hashlib.sha256(wire.encode()).hexdigest(),
        "scenario_authorities_sha256": hashlib.sha256(
            scenario_wire.encode()
        ).hexdigest(),
        "second_agent_id": delegations["second"].subject_id,
        "second_cert_id": delegations["second"].cert_id,
        "second_delegation_sha256": hashlib.sha256(second_wire.encode()).hexdigest(),
        "peer_authorities_sha256": hashlib.sha256(peer_wire.encode()).hexdigest(),
        "revoked_cert_id": delegations["revoked"].cert_id,
        "status_list_sha256": hashlib.sha256(status_wire).hexdigest(),
        "status_sequence": status_list.sequence,
        "status_next_update": status_list.next_update,
        "status_revoked_cert_ids": list(status_list.revoked or []),
        "wrong_agent_id": delegations["wrong_agent"].subject_id,
        "expires_at": delegation.expires_at,
        "issued_at": delegation.issued_at,
        "max_amount_minor": DEFAULT_MAX_AMOUNT_MINOR,
        "resource": DEFAULT_RESOURCE,
        "scope": WORK_ORDER_SCOPE,
    }, sort_keys=True, indent=2) + "\n")
    os.chmod(path, 0o644)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode()


def _public(value: HybridPublicKey) -> dict[str, str]:
    return {"ed25519": _b64(value.ed25519), "ml_dsa_65": _b64(value.ml_dsa_65)}


def _private(value: HybridPrivateKey) -> dict[str, str]:
    return {"ed25519": _b64(value.ed25519), "ml_dsa_65": _b64(value.ml_dsa_65)}


def _decode_public(value: dict[str, str]) -> HybridPublicKey:
    public = HybridPublicKey(
        ed25519=base64.b64decode(value["ed25519"], validate=True),
        ml_dsa_65=base64.b64decode(value["ml_dsa_65"], validate=True),
    )
    if len(public.ed25519) != 32 or len(public.ml_dsa_65) != 1952:
        raise ValueError("invalid public key length")
    return public


def _decode_private(value: dict[str, str]) -> HybridPrivateKey:
    private = HybridPrivateKey(
        ed25519=base64.b64decode(value["ed25519"], validate=True),
        ml_dsa_65=base64.b64decode(value["ml_dsa_65"], validate=True),
    )
    if len(private.ed25519) != 32 or len(private.ml_dsa_65) != 4032:
        raise ValueError("invalid private key length")
    return private


def _load_principal(path: Path) -> dict[str, tuple[HybridPublicKey, HybridPrivateKey]]:
    """Load and cross-check every key: ID, public key and private key agree."""
    expected = {
        f"{name}_{part}"
        for name in _PRINCIPAL_KEYS
        for part in ("id", "public_key", "private_key")
    }
    try:
        principal = json.loads(path.read_text())
        if type(principal) is not dict or set(principal) != expected:
            raise ValueError
        keys = {}
        for name in _PRINCIPAL_KEYS:
            if type(principal[f"{name}_id"]) is not str:
                raise ValueError
            for part in ("public_key", "private_key"):
                value = principal[f"{name}_{part}"]
                if type(value) is not dict or set(value) != {
                    "ed25519", "ml_dsa_65"
                } or not all(type(item) is str for item in value.values()):
                    raise ValueError
            keys[name] = (
                _decode_public(principal[f"{name}_public_key"]),
                _decode_private(principal[f"{name}_private_key"]),
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("invalid principal artifact") from None
    for name, (public, private) in keys.items():
        try:
            consistent = (
                key_id_v2(public) == principal[f"{name}_id"]
                and public_key_of(private) == public
            )
        except (TypeError, ValueError, V2Error):
            consistent = False
        if not consistent:
            raise RuntimeError("principal artifact is inconsistent")
    return keys


def _load_status_authority(principal_path: Path, keys: dict) -> StatusAuthority:
    try:
        state = StatusAuthority.state_from_json(
            (principal_path.parent / STATUS_STATE_FILE).read_text()
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("invalid status publisher state") from None
    return StatusAuthority(
        root_public_key=keys["root"][0],
        status_private_key=keys["status"][1],
        status_public_key=keys["status"][0],
        state=state,
    )

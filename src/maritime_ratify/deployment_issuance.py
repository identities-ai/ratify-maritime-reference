"""Offline issuance artifacts for the Maritime demonstration."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from ratify_protocol import (
    HybridPrivateKey,
    HybridPublicKey,
    derive_id,
    encode_delegation_cert,
    generate_agent,
    generate_human_root,
    sign_both,
    verify_both,
    verify_delegation_signature_e,
)

from .authority import issue_bounded_delegation
from .profile import (
    DEFAULT_CATEGORY,
    DEFAULT_CURRENCY,
    DEFAULT_MAX_AMOUNT_MINOR,
    DEFAULT_RESOURCE,
    VERIFIER_ID,
    WORK_ORDER_SCOPE,
)

DEMO_VALIDITY_SECONDS = 7 * 24 * 60 * 60


def issue_deployment(output: Path, *, now: int | None = None) -> None:
    issued_at = int(time.time()) if now is None else now
    root, root_private = generate_human_root()
    agent, agent_private = generate_agent("Maritime Work Order Agent", "custom")
    wrong_agent, wrong_agent_private = generate_agent(
        "Maritime Wrong-Agent Adversary", "custom"
    )
    delegations = _issue_scenario_delegations(
        root_id=root.id,
        root_public_key=root.public_key,
        root_private_key=root_private,
        agent_id=agent.id,
        agent_public_key=agent.public_key,
        wrong_agent_id=wrong_agent.id,
        wrong_agent_public_key=wrong_agent.public_key,
        issued_at=issued_at,
    )
    receiver_token = secrets.token_urlsafe(32)
    demo_token = secrets.token_urlsafe(32)
    _prepare_output(output)
    _write_private_json(output / "principal.json", {
        "root_id": root.id,
        "root_public_key": _public(root.public_key),
        "root_private_key": _private(root_private),
        "agent_id": agent.id,
        "agent_public_key": _public(agent.public_key),
        "agent_private_key": _private(agent_private),
        "wrong_agent_id": wrong_agent.id,
        "wrong_agent_public_key": _public(wrong_agent.public_key),
        "wrong_agent_private_key": _private(wrong_agent_private),
    })
    _write_private_env(output / "receiver.env", {
        "RATIFY_ROOT_ID": root.id,
        "RATIFY_ROOT_ED25519_B64": _b64(root.public_key.ed25519),
        "RATIFY_ROOT_ML_DSA_65_B64": _b64(root.public_key.ml_dsa_65),
        "RATIFY_AGENT_ID": agent.id,
        "RATIFY_CALLER_ID": "maritime-demo-agent",
        "RATIFY_CALLER_TOKEN": receiver_token,
        "RATIFY_REVOKED_CERT_IDS": delegations["revoked"].cert_id,
    })
    wire = encode_delegation_cert(delegations["active"])
    scenario_wire = _scenario_authorities_wire(delegations)
    _write_private_env(output / "agent.env", {
        "RATIFY_DELEGATION_PATH": "/app/deployment/delegation.json",
        "RATIFY_SCENARIO_AUTHORITIES_PATH": "/app/deployment/scenario-authorities.json",
        "RATIFY_AGENT_ED25519_PRIVATE_B64": _b64(agent_private.ed25519),
        "RATIFY_AGENT_ML_DSA_65_PRIVATE_B64": _b64(agent_private.ml_dsa_65),
        "RATIFY_WRONG_AGENT_ED25519_PRIVATE_B64": _b64(
            wrong_agent_private.ed25519
        ),
        "RATIFY_WRONG_AGENT_ML_DSA_65_PRIVATE_B64": _b64(
            wrong_agent_private.ml_dsa_65
        ),
        "RATIFY_RECEIVER_TOKEN": receiver_token,
        "RATIFY_DEMO_TOKEN": demo_token,
        "RATIFY_MODEL_MODE": "deterministic",
    })
    _write_private(output / "delegation.json", wire)
    _write_private(output / "scenario-authorities.json", scenario_wire)
    _write_manifest(
        output / "manifest.json", delegations, wire, scenario_wire
    )


def renew_deployment(principal_path: Path, output: Path, *, now: int | None = None) -> None:
    issued_at = int(time.time()) if now is None else now
    principal = _load_principal(principal_path)
    try:
        root_public = _decode_public(principal["root_public_key"])
        root_private = _decode_private(principal["root_private_key"])
        agent_public = _decode_public(principal["agent_public_key"])
        agent_private = _decode_private(principal["agent_private_key"])
        wrong_agent_public = _decode_public(principal["wrong_agent_public_key"])
        wrong_agent_private = _decode_private(principal["wrong_agent_private_key"])
        delegations = _issue_scenario_delegations(
            root_id=principal["root_id"],
            root_public_key=root_public,
            root_private_key=root_private,
            agent_id=principal["agent_id"],
            agent_public_key=agent_public,
            wrong_agent_id=principal["wrong_agent_id"],
            wrong_agent_public_key=wrong_agent_public,
            issued_at=issued_at,
        )
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("invalid principal artifact") from None
    probe = b"ratify-maritime-principal-artifact-check"
    if (
        derive_id(root_public) != principal["root_id"]
        or derive_id(agent_public) != principal["agent_id"]
        or derive_id(wrong_agent_public) != principal["wrong_agent_id"]
        or verify_both(probe, sign_both(probe, agent_private), agent_public) is not None
        or verify_both(
            probe, sign_both(probe, wrong_agent_private), wrong_agent_public
        ) is not None
        or any(
            verify_delegation_signature_e(delegation) is not None
            for delegation in delegations.values()
        )
    ):
        raise RuntimeError("principal artifact is inconsistent")
    _prepare_output(output)
    wire = encode_delegation_cert(delegations["active"])
    scenario_wire = _scenario_authorities_wire(delegations)
    _write_private(output / "delegation.json", wire)
    _write_private(output / "scenario-authorities.json", scenario_wire)
    _write_manifest(
        output / "manifest.json", delegations, wire, scenario_wire
    )


def _issue_scenario_delegations(
    *,
    root_id: str,
    root_public_key: HybridPublicKey,
    root_private_key: HybridPrivateKey,
    agent_id: str,
    agent_public_key: HybridPublicKey,
    wrong_agent_id: str,
    wrong_agent_public_key: HybridPublicKey,
    issued_at: int,
) -> dict[str, object]:
    common = {
        "root_id": root_id,
        "root_public_key": root_public_key,
        "root_private_key": root_private_key,
    }
    return {
        "active": issue_bounded_delegation(
            **common,
            agent_id=agent_id,
            agent_public_key=agent_public_key,
            issued_at=issued_at,
            expires_at=issued_at + DEMO_VALIDITY_SECONDS,
        ),
        "expired": issue_bounded_delegation(
            **common,
            agent_id=agent_id,
            agent_public_key=agent_public_key,
            issued_at=issued_at - (2 * DEMO_VALIDITY_SECONDS),
            expires_at=issued_at - DEMO_VALIDITY_SECONDS,
        ),
        "revoked": issue_bounded_delegation(
            **common,
            agent_id=agent_id,
            agent_public_key=agent_public_key,
            issued_at=issued_at,
            expires_at=issued_at + DEMO_VALIDITY_SECONDS,
        ),
        "wrong_agent": issue_bounded_delegation(
            **common,
            agent_id=wrong_agent_id,
            agent_public_key=wrong_agent_public_key,
            issued_at=issued_at,
            expires_at=issued_at + DEMO_VALIDITY_SECONDS,
        ),
    }


def _scenario_authorities_wire(delegations: dict[str, object]) -> str:
    return json.dumps({
        name: encode_delegation_cert(delegations[name])
        for name in ("expired", "revoked", "wrong_agent")
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


def _write_manifest(
    path: Path,
    delegations: dict[str, object],
    wire: str,
    scenario_wire: str,
) -> None:
    delegation = delegations["active"]
    path.write_text(json.dumps({
        "agent_id": delegation.subject_id,
        "audience": VERIFIER_ID,
        "category": DEFAULT_CATEGORY,
        "cert_id": delegation.cert_id,
        "currency": DEFAULT_CURRENCY,
        "delegation_sha256": hashlib.sha256(wire.encode()).hexdigest(),
        "scenario_authorities_sha256": hashlib.sha256(
            scenario_wire.encode()
        ).hexdigest(),
        "revoked_cert_id": delegations["revoked"].cert_id,
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


def _load_principal(path: Path) -> dict:
    try:
        principal = json.loads(path.read_text())
        if type(principal) is not dict or set(principal) != {
            "root_id", "root_public_key", "root_private_key",
            "agent_id", "agent_public_key", "agent_private_key",
            "wrong_agent_id", "wrong_agent_public_key", "wrong_agent_private_key",
        }:
            raise ValueError
        for name in ("root_id", "agent_id", "wrong_agent_id"):
            if type(principal[name]) is not str:
                raise ValueError
        for name in (
            "root_public_key", "root_private_key",
            "agent_public_key", "agent_private_key",
            "wrong_agent_public_key", "wrong_agent_private_key",
        ):
            if type(principal[name]) is not dict or set(principal[name]) != {
                "ed25519", "ml_dsa_65"
            } or not all(type(value) is str for value in principal[name].values()):
                raise ValueError
        return principal
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("invalid principal artifact") from None

"""Maritime entry point for the separately configured receiver runtime."""

import base64
import os

import uvicorn
from ratify_protocol_v2 import HybridPrivateKey, HybridPublicKey, key_id_v2

from maritime_ratify import CallerAuthenticator, PresentationRegistry, WorkOrderReceiver
from maritime_ratify.service import create_receiver_app
from maritime_ratify.status import FileStatusFeed


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment setting: {name}")
    return value


def required_key_bytes(name: str, expected_bytes: int) -> bytes:
    try:
        value = base64.b64decode(required(name), validate=True)
    except ValueError:
        raise RuntimeError(f"invalid key setting: {name}") from None
    if len(value) != expected_bytes:
        raise RuntimeError(f"invalid key setting: {name}")
    return value


def required_public_key(prefix: str, suffix: str = "", id_setting: str | None = None) -> HybridPublicKey:
    """A complete hybrid public key, cross-checked against its key ID."""
    key = HybridPublicKey(
        ed25519=required_key_bytes(f"{prefix}_ED25519_B64{suffix}", 32),
        ml_dsa_65=required_key_bytes(f"{prefix}_ML_DSA_65_B64{suffix}", 1952),
    )
    if id_setting is not None and key_id_v2(key) != required(id_setting):
        raise RuntimeError(f"{id_setting} does not match its configured key")
    return key


def build_receiver() -> WorkOrderReceiver:
    if os.environ.get("RATIFY_REVOKED_CERT_IDS"):
        # Protocol v2 evaluates only signed status. Refuse the v1 setting
        # rather than silently ignoring a revocation an operator configured.
        raise RuntimeError(
            "RATIFY_REVOKED_CERT_IDS is not supported; publish a signed "
            "status list to RATIFY_STATUS_LIST_PATH"
        )
    return WorkOrderReceiver(
        trusted_root_public_key=required_public_key(
            "RATIFY_ROOT", id_setting="RATIFY_ROOT_ID"
        ),
        status_public_key=required_public_key(
            "RATIFY_STATUS", id_setting="RATIFY_STATUS_KEY_ID"
        ),
        status_feed=FileStatusFeed(required("RATIFY_STATUS_LIST_PATH")),
        verifier_private_key=HybridPrivateKey(
            ed25519=required_key_bytes("RATIFY_VERIFIER_ED25519_PRIVATE_B64", 32),
            ml_dsa_65=required_key_bytes(
                "RATIFY_VERIFIER_ML_DSA_65_PRIVATE_B64", 4032
            ),
        ),
    )


def build_app():
    receiver = build_receiver()
    hosts = [
        host.strip() for host in required("RATIFY_ALLOWED_HOSTS").split(",")
        if host.strip()
    ]
    credentials, caller_subjects = _caller_configuration()
    return create_receiver_app(
        receiver=receiver,
        authenticator=CallerAuthenticator(credentials),
        presentations=PresentationRegistry(),
        caller_subjects=caller_subjects,
        allowed_hosts=hosts,
        allow_maritime_proxy_host=True,
    )


def _caller_configuration() -> tuple[dict[str, str], dict[str, HybridPublicKey]]:
    """Bind each configured transport credential to exactly one agent key.

    Authentication identifies a caller. It does not let that caller choose
    which agent it presents as, so every slot names its own expected subject.
    """
    slots = [
        slot.strip().upper()
        for slot in required("RATIFY_CALLER_SLOTS").split(",")
        if slot.strip()
    ]
    if not slots:
        raise RuntimeError("missing required environment setting: RATIFY_CALLER_SLOTS")
    credentials: dict[str, str] = {}
    caller_subjects: dict[str, HybridPublicKey] = {}
    for slot in slots:
        token = required(f"RATIFY_CALLER_TOKEN_{slot}")
        caller_id = required(f"RATIFY_CALLER_ID_{slot}")
        agent_key = required_public_key(
            "RATIFY_AGENT", f"_{slot}", id_setting=f"RATIFY_AGENT_ID_{slot}"
        )
        if token in credentials or caller_id in caller_subjects:
            raise RuntimeError("duplicate caller slot configuration")
        credentials[token] = caller_id
        caller_subjects[caller_id] = agent_key
    return credentials, caller_subjects


if __name__ == "__main__":
    uvicorn.run(
        build_app(),
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )

"""Maritime entry point for the separately configured receiver runtime."""

import base64
import os

import uvicorn
from ratify_protocol import HybridPublicKey

from maritime_ratify import CallerAuthenticator, PresentationRegistry, WorkOrderReceiver
from maritime_ratify.service import create_receiver_app


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment setting: {name}")
    return value


def required_public_key(name: str, expected_bytes: int) -> bytes:
    try:
        value = base64.b64decode(required(name), validate=True)
    except ValueError:
        raise RuntimeError(f"invalid public key setting: {name}") from None
    if len(value) != expected_bytes:
        raise RuntimeError(f"invalid public key setting: {name}")
    return value


def build_app():
    root_key = HybridPublicKey(
        ed25519=required_public_key("RATIFY_ROOT_ED25519_B64", 32),
        ml_dsa_65=required_public_key("RATIFY_ROOT_ML_DSA_65_B64", 1952),
    )
    receiver = WorkOrderReceiver(
        trusted_root_id=required("RATIFY_ROOT_ID"),
        trusted_root_public_key=root_key,
    )
    revoked = [
        cert_id.strip()
        for cert_id in os.environ.get("RATIFY_REVOKED_CERT_IDS", "").split(",")
        if cert_id.strip()
    ]
    for cert_id in revoked:
        receiver.revocation.revoke(cert_id)
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


def _caller_configuration() -> tuple[dict[str, str], dict[str, str]]:
    """Bind each configured transport credential to exactly one subject.

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
    caller_subjects: dict[str, str] = {}
    for slot in slots:
        token = required(f"RATIFY_CALLER_TOKEN_{slot}")
        caller_id = required(f"RATIFY_CALLER_ID_{slot}")
        agent_id = required(f"RATIFY_AGENT_ID_{slot}")
        if token in credentials or caller_id in caller_subjects:
            raise RuntimeError("duplicate caller slot configuration")
        credentials[token] = caller_id
        caller_subjects[caller_id] = agent_id
    return credentials, caller_subjects


if __name__ == "__main__":
    uvicorn.run(
        build_app(),
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )

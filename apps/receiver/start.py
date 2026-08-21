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
    hosts = [
        host.strip() for host in required("RATIFY_ALLOWED_HOSTS").split(",")
        if host.strip()
    ]
    return create_receiver_app(
        receiver=receiver,
        authenticator=CallerAuthenticator(
            {required("RATIFY_CALLER_TOKEN"): required("RATIFY_CALLER_ID")}
        ),
        presentations=PresentationRegistry(),
        expected_agent_id=required("RATIFY_AGENT_ID"),
        allowed_hosts=hosts,
    )


if __name__ == "__main__":
    uvicorn.run(
        build_app(),
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )

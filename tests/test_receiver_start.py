import base64
import importlib.util
from pathlib import Path

import pytest


START_PATH = Path(__file__).parents[1] / "apps" / "receiver" / "start.py"
SPEC = importlib.util.spec_from_file_location("receiver_start", START_PATH)
assert SPEC is not None and SPEC.loader is not None
receiver_start = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receiver_start)


def test_receiver_rejects_wrong_length_root_public_keys(monkeypatch):
    settings = {
        "RATIFY_ROOT_ID": "ratify:human:test-root",
        "RATIFY_ROOT_ED25519_B64": base64.b64encode(b"e" * 3).decode(),
        "RATIFY_ROOT_ML_DSA_65_B64": base64.b64encode(b"m" * 1952).decode(),
        "RATIFY_ALLOWED_HOSTS": "localhost:*",
        "RATIFY_CALLER_TOKEN": "test-token",
        "RATIFY_CALLER_ID": "test-caller",
        "RATIFY_AGENT_ID": "ratify:agent:test-agent",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="RATIFY_ROOT_ED25519_B64"):
        receiver_start.build_app()


def test_local_proxy_hosts_are_exact_private_addresses(monkeypatch):
    monkeypatch.setattr(receiver_start.socket, "gethostname", lambda: "receiver")
    monkeypatch.setattr(
        receiver_start.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (receiver_start.socket.AF_INET, 1, 6, "", ("10.6.110.2", 0)),
            (receiver_start.socket.AF_INET, 1, 6, "", ("8.8.8.8", 0)),
        ],
    )

    assert receiver_start.local_proxy_hosts() == ["10.6.110.2:8080"]

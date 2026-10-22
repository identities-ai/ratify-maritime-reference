import base64
import importlib.util
from pathlib import Path

import pytest
from ratify_protocol_v2 import generate_keypair_v2, key_id_v2

AGENT_A, _ = generate_keypair_v2()
AGENT_B, _ = generate_keypair_v2()


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode()


def _agent(slot: str, key) -> dict[str, str]:
    return {
        f"RATIFY_AGENT_ID_{slot}": key_id_v2(key),
        f"RATIFY_AGENT_ED25519_B64_{slot}": _b64(key.ed25519),
        f"RATIFY_AGENT_ML_DSA_65_B64_{slot}": _b64(key.ml_dsa_65),
    }


START_PATH = Path(__file__).parents[1] / "apps" / "receiver" / "start.py"
SPEC = importlib.util.spec_from_file_location("receiver_start", START_PATH)
assert SPEC is not None and SPEC.loader is not None
receiver_start = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receiver_start)


def test_receiver_rejects_wrong_length_root_public_keys(monkeypatch):
    settings = {
        "RATIFY_ROOT_ID": "0" * 64,
        "RATIFY_ROOT_ED25519_B64": base64.b64encode(b"e" * 3).decode(),
        "RATIFY_ROOT_ML_DSA_65_B64": base64.b64encode(b"m" * 1952).decode(),
        "RATIFY_ALLOWED_HOSTS": "localhost:*",
        "RATIFY_CALLER_SLOTS": "primary",
        "RATIFY_CALLER_TOKEN_PRIMARY": "test-token",
        "RATIFY_CALLER_ID_PRIMARY": "test-caller",
        **_agent("PRIMARY", AGENT_A),
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="RATIFY_ROOT_ED25519_B64"):
        receiver_start.build_app()


def _slot_settings(**overrides):
    settings = {
        "RATIFY_CALLER_SLOTS": "primary,secondary",
        "RATIFY_CALLER_TOKEN_PRIMARY": "token-a",
        "RATIFY_CALLER_ID_PRIMARY": "caller-a",
        **_agent("PRIMARY", AGENT_A),
        "RATIFY_CALLER_TOKEN_SECONDARY": "token-b",
        "RATIFY_CALLER_ID_SECONDARY": "caller-b",
        **_agent("SECONDARY", AGENT_B),
    }
    settings.update(overrides)
    return settings


def test_each_caller_slot_binds_one_credential_to_one_subject(monkeypatch):
    for name, value in _slot_settings().items():
        monkeypatch.setenv(name, value)

    credentials, caller_subjects = receiver_start._caller_configuration()

    assert credentials == {"token-a": "caller-a", "token-b": "caller-b"}
    assert caller_subjects == {"caller-a": AGENT_A, "caller-b": AGENT_B}


def test_caller_slot_key_id_must_match_its_complete_key(monkeypatch):
    """A configured agent ID is only a label; the complete key is the pin."""
    settings = _slot_settings(RATIFY_AGENT_ID_SECONDARY=key_id_v2(AGENT_A))
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="RATIFY_AGENT_ID_SECONDARY does not match"):
        receiver_start._caller_configuration()


def test_receiver_refuses_the_v1_revocation_setting(monkeypatch):
    """An unsigned revocation list must fail loudly, not be ignored."""
    monkeypatch.setenv("RATIFY_REVOKED_CERT_IDS", "maritime-revoked")

    with pytest.raises(RuntimeError, match="signed status list"):
        receiver_start.build_app()


@pytest.mark.parametrize("overrides", [
    {"RATIFY_CALLER_TOKEN_SECONDARY": "token-a"},
    {"RATIFY_CALLER_ID_SECONDARY": "caller-a"},
])
def test_receiver_refuses_duplicate_caller_slots(monkeypatch, overrides):
    """A shared credential or caller id would let one runtime act as another."""
    for name, value in _slot_settings(**overrides).items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="duplicate caller slot"):
        receiver_start._caller_configuration()


def test_receiver_requires_every_setting_a_declared_slot_names(monkeypatch):
    settings = _slot_settings()
    del settings["RATIFY_AGENT_ID_SECONDARY"]
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("RATIFY_AGENT_ID_SECONDARY", raising=False)

    with pytest.raises(RuntimeError, match="RATIFY_AGENT_ID_SECONDARY"):
        receiver_start._caller_configuration()

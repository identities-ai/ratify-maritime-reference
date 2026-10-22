"""Shared test construction for a receiver with signed status."""

from __future__ import annotations

from collections.abc import Callable
import importlib.util
from pathlib import Path
import time

from ratify_protocol_v2 import HybridPublicKey, generate_keypair_v2

from maritime_ratify import WorkOrderReceiver
from maritime_ratify.status import MemoryStatusFeed, StatusAuthority


class SignedStatus:
    """A principal-side status publisher feeding one receiver in memory."""

    def __init__(self, root_public_key: HybridPublicKey, at: int) -> None:
        public, private = generate_keypair_v2()
        self.public_key = public
        self._authority = StatusAuthority(
            root_public_key=root_public_key,
            status_private_key=private,
            status_public_key=public,
        )
        self._at = at - 10
        self.feed = MemoryStatusFeed(self._authority.publish(revoked=[], at=self._at))

    def revoke(self, cert_id: str) -> None:
        self._at += 1
        self.feed.set(self._authority.publish(revoked=[cert_id], at=self._at))


def make_receiver(
    root_public_key: HybridPublicKey,
    *,
    clock: Callable[[], int] | None = None,
    **options,
) -> WorkOrderReceiver:
    """A receiver pinned to this root, with a genesis signed status list.

    The publisher is reachable as ``receiver.test_status`` so a test can
    revoke a certificate the way the principal would.
    """
    now = clock() if clock is not None else int(time.time())
    status = SignedStatus(root_public_key, now)
    receiver = WorkOrderReceiver(
        trusted_root_public_key=root_public_key,
        status_public_key=status.public_key,
        status_feed=status.feed,
        clock=clock,
        **options,
    )
    receiver.test_status = status
    return receiver


def issued_receiver(issuance: Path, monkeypatch):
    """The receiver and caller bindings exactly as the entry point builds them
    from issued ``receiver.env``, reading the issued signed status list."""
    for line in (issuance / "receiver.env").read_text().splitlines():
        name, value = line.split("=", 1)
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("RATIFY_STATUS_LIST_PATH", str(issuance / "status-list.json"))
    start = Path(__file__).resolve().parents[1] / "apps" / "receiver" / "start.py"
    spec = importlib.util.spec_from_file_location("issued_receiver_start", start)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_receiver(), module._caller_configuration()[1]

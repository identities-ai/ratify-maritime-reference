"""Signed revocation status for the Maritime reference.

The principal holds a status key that is separate from the root key that
issues delegations. A status publisher signs a cumulative revocation list
under that key; the receiver pins the complete status public key and accepts
nothing else. An unsigned or locally configured revocation list is never
evaluated.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import json
import os
from pathlib import Path
import threading

from ratify_protocol_v2 import HybridPrivateKey, HybridPublicKey, background
from ratify_protocol_v2.protocol_v2 import (
    decode_revocation_list_v2,
    encode_revocation_list_v2,
)
from ratify_protocol_v2.security_journal_v2 import (
    configure_status_key_epoch_v2,
    record_security_key_role_v2,
)
from ratify_protocol_v2.signing_v2 import key_id_v2
from ratify_protocol_v2.status_publisher_v2 import (
    V2StatusPublisherState,
    new_v2_status_publisher_with_key,
)
from ratify_protocol_v2.v2reference.journal import new_journal_store

from .profile import STATUS_KEY_EPOCH, STATUS_LIST_LIFETIME_SECONDS

# The maximum signed status list the receiver reads. A list naming every
# revoked certificate of a long-running demonstration stays far below this.
MAX_STATUS_LIST_BYTES = 256 * 1024


class StatusAuthority:
    """The principal's status publisher and its persisted state.

    The role journal it builds is the SDK's in-memory reference journal, used
    here only to bind the status key to its issuer and to prove the two keys
    are role-separated before anything is signed. The publisher state is what
    must persist between signings; ``to_json`` and ``from_json`` carry it.
    """

    def __init__(
        self,
        *,
        root_public_key: HybridPublicKey,
        status_private_key: HybridPrivateKey,
        status_public_key: HybridPublicKey,
        state: V2StatusPublisherState | None = None,
    ) -> None:
        ctx = background()
        roles = new_journal_store()
        record_security_key_role_v2(ctx, roles, "issuer", root_public_key)
        configure_status_key_epoch_v2(
            ctx, roles, key_id_v2(root_public_key), STATUS_KEY_EPOCH,
            status_public_key,
        )
        # Refuses a status key that reuses the root key or either component.
        self._publisher = new_v2_status_publisher_with_key(
            ctx, roles, root_public_key, status_private_key,
            STATUS_LIST_LIFETIME_SECONDS,
        )
        self._state = state
        self._wire: bytes | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> V2StatusPublisherState | None:
        return self._state

    @property
    def list_wire(self) -> bytes | None:
        """The most recently signed list, or None before the first signing."""
        return self._wire

    def publish(self, *, revoked: list[str], at: int) -> bytes:
        """Sign a list whose revoked set is the current set plus ``revoked``.

        The first call signs the genesis list. Later calls revoke what is new
        or, when nothing is new, re-sign the same set with a later
        ``updated_at`` so the list stays current.
        """
        ctx = background()
        with self._lock:
            if self._state is None:
                # Genesis signs an empty set; revocations follow one second
                # later, because each list must be later than the last.
                self._apply(self._publisher.genesis(ctx, at - 1 if revoked else at))
                if not revoked:
                    return self._wire
            new = sorted(set(revoked) - set(self._state.revoked))
            if new:
                self._apply(self._publisher.revoke(ctx, self._state, new, at))
            else:
                self._apply(self._publisher.heartbeat(ctx, self._state, at))
            return self._wire

    def _apply(self, publication) -> None:
        self._state = publication.state
        self._wire = encode_revocation_list_v2(publication.list)

    def state_json(self) -> str:
        if self._state is None:
            raise RuntimeError("status authority has not signed a list")
        return json.dumps(asdict(self._state), sort_keys=True, indent=2) + "\n"

    @staticmethod
    def state_from_json(text: str) -> V2StatusPublisherState:
        value = json.loads(text)
        if type(value) is not dict or set(value) != {
            "issuer_id", "status_key_id", "sequence", "updated_at", "revoked"
        }:
            raise ValueError("invalid status publisher state")
        return V2StatusPublisherState(**value)


class MemoryStatusFeed:
    """Holds the latest signed list in memory. Used by tests and tooling."""

    def __init__(self, wire: bytes | None = None) -> None:
        self._wire = wire
        self._lock = threading.Lock()

    def set(self, wire: bytes) -> None:
        with self._lock:
            self._wire = bytes(wire)

    def __call__(self) -> bytes:
        with self._lock:
            if self._wire is None:
                raise RuntimeError("no signed status list is available")
            return self._wire


class FileStatusFeed:
    """Reads the signed list from the receiver's volume on every verification.

    A refresh is therefore a file write, with no restart. The bytes are only
    transport: the verifier checks the signature against the pinned status
    key and ingests the list into its journal before deriving any status.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def __call__(self) -> bytes:
        with self._path.open("rb") as stream:
            wire = stream.read(MAX_STATUS_LIST_BYTES + 1)
        if len(wire) > MAX_STATUS_LIST_BYTES:
            raise RuntimeError("signed status list exceeds the configured bound")
        return wire.strip()


StatusFeed = Callable[[], bytes]


def revoked_ids(wire: bytes) -> list[str]:
    """The certificate IDs a signed list revokes, for tooling output only."""
    return list(decode_revocation_list_v2(wire).revoked or [])

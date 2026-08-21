"""Strict Stage 3 carrier boundary for MCP-over-HTTP calls."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import secrets
import threading
import time
from collections.abc import Callable, Iterable, Mapping

from ratify_protocol import MAX_PROOF_BUNDLE_BYTES, ProofBundle, decode_proof_bundle

from .action import WorkOrder

PROOF_REFERENCE_HEADER = b"x-ratify-proof-reference"
AUTHORIZATION_HEADER = b"authorization"
MAX_PROOF_REFERENCE_BYTES = 128


class CarrierDenied(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class _StoredPresentation:
    caller_id: str
    canonical_action: bytes
    bundle: ProofBundle
    expires_at: int


class CallerAuthenticator:
    """Resolve bearer credentials to receiver-owned caller identities."""

    def __init__(self, credentials: Mapping[str, str]) -> None:
        self._credentials = dict(credentials)

    def authenticate(self, raw_headers: Iterable[tuple[bytes, bytes]]) -> str:
        value = _single_header(raw_headers, AUTHORIZATION_HEADER, 1024)
        if not value.startswith("Bearer "):
            raise CarrierDenied("DENY_TRANSPORT_AUTH")
        supplied = value.removeprefix("Bearer ")
        for token, caller_id in self._credentials.items():
            if hmac.compare_digest(supplied, token):
                return caller_id
        raise CarrierDenied("DENY_TRANSPORT_AUTH")


class PresentationRegistry:
    """Bounded, single-use bridge between proof upload and an MCP tool call."""

    def __init__(
        self,
        *,
        clock: Callable[[], int] | None = None,
        ttl_seconds: int = 60,
        max_entries: int = 128,
    ) -> None:
        self._clock = clock or (lambda: int(time.time()))
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[str, _StoredPresentation] = {}
        self._lock = threading.Lock()

    def register(self, *, caller_id: str, action: WorkOrder, proof_wire: str | bytes) -> str:
        if not caller_id or type(action) is not WorkOrder:
            raise CarrierDenied("DENY_INVALID_REQUEST")
        size = len(proof_wire) if isinstance(proof_wire, bytes) else len(proof_wire.encode())
        if size > MAX_PROOF_BUNDLE_BYTES:
            raise CarrierDenied("DENY_OVERSIZED_INPUT")
        try:
            canonical_action = action.canonical_bytes()
            bundle = decode_proof_bundle(proof_wire)
        except (TypeError, ValueError):
            raise CarrierDenied("DENY_INVALID_REQUEST") from None
        with self._lock:
            now = self._clock()
            self._reap(now)
            if len(self._entries) >= self._max_entries:
                raise CarrierDenied("DENY_VERIFIER_UNAVAILABLE")
            reference = secrets.token_urlsafe(32)
            self._entries[reference] = _StoredPresentation(
                caller_id, canonical_action, bundle, now + self._ttl_seconds
            )
            return reference

    def consume(
        self,
        *,
        raw_headers: Iterable[tuple[bytes, bytes]],
        caller_id: str,
        action: WorkOrder,
    ) -> ProofBundle:
        reference = _single_header(
            raw_headers, PROOF_REFERENCE_HEADER, MAX_PROOF_REFERENCE_BYTES
        )
        with self._lock:
            now = self._clock()
            self._reap(now)
            stored = self._entries.get(reference)
            if stored is None:
                raise CarrierDenied("DENY_REPLAY")
            if stored.caller_id != caller_id:
                raise CarrierDenied("DENY_CALLER_MISMATCH")
            try:
                canonical_action = action.canonical_bytes()
            except (TypeError, ValueError):
                raise CarrierDenied("DENY_INVALID_REQUEST") from None
            if canonical_action != stored.canonical_action:
                raise CarrierDenied("DENY_OPERATION_MISMATCH")
            self._entries.pop(reference)
            return stored.bundle

    def _reap(self, now: int) -> None:
        self._entries = {
            reference: stored
            for reference, stored in self._entries.items()
            if stored.expires_at > now
        }


def _single_header(
    raw_headers: Iterable[tuple[bytes, bytes]], name: bytes, max_bytes: int
) -> str:
    values = [value for key, value in raw_headers if key.lower() == name]
    if len(values) != 1:
        reason = "DENY_AMBIGUOUS_INPUT" if len(values) > 1 else "DENY_INVALID_REQUEST"
        raise CarrierDenied(reason)
    value = values[0]
    if len(value) > max_bytes:
        raise CarrierDenied("DENY_OVERSIZED_INPUT")
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError:
        raise CarrierDenied("DENY_INVALID_REQUEST") from None
    if not decoded:
        raise CarrierDenied("DENY_INVALID_REQUEST")
    return decoded

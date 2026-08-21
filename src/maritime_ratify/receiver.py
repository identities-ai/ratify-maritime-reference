"""Receiver-owned authorization boundary and protected handler."""

from __future__ import annotations

from collections.abc import Callable
import copy
from dataclasses import dataclass, replace
import hashlib
import threading
import time
from typing import Any

from ratify_protocol import (
    MemoryChallengeStore,
    DelegationCert,
    OperationContext,
    ProofBundle,
    SessionContextInputs,
    VerifierContext,
    VerifyOptions,
    build_session_context,
    operation_context_hash,
    verify_bundle,
)

from .action import WorkOrder
from .profile import (
    AUDIENCE_CONSTRAINT,
    CATEGORY_CONSTRAINT,
    DEFAULT_CATEGORY,
    DEFAULT_CURRENCY,
    DEFAULT_MAX_AMOUNT_MINOR,
    DEFAULT_RESOURCE,
    VERIFIER_ID,
    WORKSPACE_ID,
    WORK_ORDER_SCOPE,
)


@dataclass(frozen=True)
class ChallengeGrant:
    challenge: bytes
    session_context: bytes


@dataclass(frozen=True)
class ChallengeResult:
    decision: str
    reason: str
    grant: ChallengeGrant | None = None


@dataclass(frozen=True)
class _Pending:
    canonical_action: bytes
    caller_id: str
    expected_agent_id: str
    session_context: bytes
    expires_at: int


class StaticRevocationProvider:
    def __init__(self) -> None:
        self._revoked: set[str] = set()

    def revoke(self, cert_id: str) -> None:
        self._revoked.add(cert_id)

    def is_revoked(self, cert_id: str) -> tuple[bool, None]:
        return cert_id in self._revoked, None


class ExactValueEvaluator:
    def __init__(self, *, field: str, expected: str) -> None:
        self.field = field
        self.expected = expected

    def evaluate(
        self, constraint: Any, cert_id: str, context: Any, now_unix: int
    ) -> tuple[bool, str | None]:
        allowed = constraint.params.get("allowed") if isinstance(constraint.params, dict) else None
        if not isinstance(allowed, str):
            return False, f"{self.field} constraint is unavailable"
        if allowed != self.expected:
            return False, f"requested {self.field} was not delegated"
        return True, None


class WorkOrderReceiver:
    def __init__(
        self,
        *,
        trusted_root_id: str,
        trusted_root_public_key: Any,
        clock: Callable[[], int] | None = None,
        resource: str = DEFAULT_RESOURCE,
        category: str = DEFAULT_CATEGORY,
        max_amount_minor: int = DEFAULT_MAX_AMOUNT_MINOR,
        currency: str = DEFAULT_CURRENCY,
    ) -> None:
        self.trusted_root_id = trusted_root_id
        self.trusted_root_public_key = trusted_root_public_key
        self.challenge_store = MemoryChallengeStore(max_size=128)
        self.revocation = StaticRevocationProvider()
        self._clock = clock or (lambda: int(time.time()))
        self._resource = resource
        self._category = category
        self._max_amount_minor = max_amount_minor
        self._currency = currency
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.RLock()
        self._handler_invocations = 0

    @property
    def handler_invocations(self) -> int:
        with self._lock:
            return self._handler_invocations

    def issue_challenge(
        self, action: WorkOrder, *, expected_agent_id: str, caller_id: str = "local"
    ) -> ChallengeResult:
        if type(action) is not WorkOrder or not expected_agent_id or not caller_id:
            return ChallengeResult("DENY", "DENY_INVALID_REQUEST")
        try:
            canonical_action = action.canonical_bytes()
        except (TypeError, ValueError):
            return ChallengeResult("DENY", "DENY_INVALID_REQUEST")
        now = self._clock()
        operation = OperationContext(
            required_scope=WORK_ORDER_SCOPE,
            operation="work_order.create",
            resource_id=action.resource,
            payload_digest=hashlib.sha256(canonical_action).digest(),
        )
        session_context = build_session_context(
            SessionContextInputs(
                verifier_id=VERIFIER_ID,
                workspace_id=WORKSPACE_ID,
                agent_id=expected_agent_id,
                session_id="maritime-reference",
                invocation_id=action.request_id,
                request_hash=operation_context_hash(operation),
            )
        )
        with self._lock:
            self._reap_pending(now)
            if self.challenge_store is None or self.revocation is None:
                return ChallengeResult("DENY", "DENY_VERIFIER_UNAVAILABLE")
            if action.request_id in self._pending:
                return ChallengeResult("DENY", "DENY_AMBIGUOUS_INPUT")
            try:
                challenge, expires_at = self.challenge_store.issue(session_context, 300)
            except Exception:
                return ChallengeResult("DENY", "DENY_VERIFIER_UNAVAILABLE")
            self._pending[action.request_id] = _Pending(
                canonical_action, caller_id, expected_agent_id, session_context, expires_at
            )
        return ChallengeResult(
            "ALLOW", "ALLOW", ChallengeGrant(challenge, session_context)
        )

    def execute(
        self, action: WorkOrder, bundle: ProofBundle, *, caller_id: str = "local"
    ) -> dict[str, Any]:
        if type(action) is not WorkOrder or type(bundle) is not ProofBundle:
            return self._deny("DENY_INVALID_REQUEST")
        try:
            action = replace(action)
            bundle = copy.deepcopy(bundle)
            if type(action) is not WorkOrder or type(bundle) is not ProofBundle or any(
                type(cert) is not DelegationCert for cert in bundle.delegations
            ):
                return self._deny("DENY_INVALID_REQUEST")
            canonical_action = action.canonical_bytes()
        except Exception:
            return self._deny("DENY_INVALID_REQUEST")

        with self._lock:
            now = self._clock()
            self._reap_pending(now)
            if self.challenge_store is None or self.revocation is None:
                return self._deny("DENY_VERIFIER_UNAVAILABLE")
            pending = self._pending.get(action.request_id)
            if pending is None:
                return self._deny("DENY_REPLAY")
            if not caller_id or caller_id != pending.caller_id:
                return self._deny("DENY_CALLER_MISMATCH")
            if canonical_action != pending.canonical_action:
                return self._deny("DENY_OPERATION_MISMATCH")
            if bundle.agent_id != pending.expected_agent_id:
                return self._deny("DENY_SUBJECT_MISMATCH")
            if not bundle.delegations:
                return self._deny("DENY_INVALID_REQUEST")
            root = bundle.delegations[-1]
            if (
                root.issuer_id != self.trusted_root_id
                or root.issuer_pub_key != self.trusted_root_public_key
            ):
                return self._deny("DENY_UNTRUSTED_ISSUER")

            try:
                result = verify_bundle(
                    bundle,
                    VerifyOptions(
                        required_scope=WORK_ORDER_SCOPE,
                        now=now,
                        session_context=pending.session_context,
                        challenge_store=self.challenge_store,
                        revocation=self.revocation,
                        force_revocation_check=True,
                        context=VerifierContext(
                            requested_amount=action.amount_minor / 100,
                            requested_currency=action.currency,
                            requested_resource_id=action.resource,
                            has_resource=True,
                        ),
                        constraint_evaluators={
                            CATEGORY_CONSTRAINT: ExactValueEvaluator(
                                field="category", expected=action.category
                            ),
                            AUDIENCE_CONSTRAINT: ExactValueEvaluator(
                                field="audience", expected=VERIFIER_ID
                            ),
                        },
                    ),
                )
            except Exception:
                self._pending.pop(action.request_id, None)
                return self._deny("DENY_VERIFIER_UNAVAILABLE")
            if not result.valid:
                self._pending.pop(action.request_id, None)
                return self._deny(self._reason_code(result.identity_status, result.error_reason))

            policy_reason = self._evaluate_local_policy(action, bundle)
            if policy_reason is not None:
                self._pending.pop(action.request_id, None)
                return self._deny(policy_reason)

            self._pending.pop(action.request_id, None)
            return self._invoke_handler(action)

    def _evaluate_local_policy(
        self, action: WorkOrder, bundle: ProofBundle
    ) -> str | None:
        required_constraints = {
            "resource_path", "max_amount", CATEGORY_CONSTRAINT, AUDIENCE_CONSTRAINT
        }
        present = {constraint.type for constraint in bundle.delegations[0].constraints}
        if not required_constraints.issubset(present):
            return "DENY_CONSTRAINT_MISMATCH"
        if action.resource != self._resource:
            return "DENY_RESOURCE_MISMATCH"
        if action.category != self._category:
            return "DENY_CONSTRAINT_MISMATCH"
        if action.currency != self._currency:
            return "DENY_CONSTRAINT_MISMATCH"
        if action.amount_minor > self._max_amount_minor:
            return "DENY_LIMIT_EXCEEDED"
        return None

    def _invoke_handler(self, action: WorkOrder) -> dict[str, Any]:
        self._handler_invocations += 1
        return {
            "decision": "ALLOW",
            "reason": "ALLOW",
            "handler_invocations": self._handler_invocations,
            "work_order_id": f"demo-{action.request_id}",
        }

    def _reap_pending(self, now: int) -> None:
        self._pending = {
            request_id: pending
            for request_id, pending in self._pending.items()
            if pending.expires_at > now
        }

    def _deny(self, reason: str) -> dict[str, Any]:
        return {
            "decision": "DENY",
            "reason": reason,
            "handler_invocations": self._handler_invocations,
        }

    @staticmethod
    def _reason_code(status: str, error: str | None) -> str:
        detail = error or ""
        if detail.startswith("revocation_error"):
            return "DENY_VERIFIER_UNAVAILABLE"
        if "session_context" in detail:
            return "DENY_AUDIENCE_MISMATCH"
        if "currency mismatch" in detail:
            return "DENY_CONSTRAINT_MISMATCH"
        if "max_amount" in detail:
            return "DENY_LIMIT_EXCEEDED"
        if "resource_path" in detail:
            return "DENY_RESOURCE_MISMATCH"
        if AUDIENCE_CONSTRAINT in detail or "session context" in detail.lower():
            return "DENY_AUDIENCE_MISMATCH"
        if "revocation" in detail.lower() and "unavailable" in detail.lower():
            return "DENY_VERIFIER_UNAVAILABLE"
        return {
            "constraint_denied": "DENY_CONSTRAINT_MISMATCH",
            "constraint_unverifiable": "DENY_VERIFIER_UNAVAILABLE",
            "expired": "DENY_EXPIRED",
            "revoked": "DENY_REVOKED",
            "scope_denied": "DENY_SCOPE_MISMATCH",
        }.get(status, "DENY_VERIFICATION_FAILED")

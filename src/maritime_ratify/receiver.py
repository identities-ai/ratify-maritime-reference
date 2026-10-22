"""Receiver-owned authorization boundary and protected handler.

The receiver runs the complete Ratify Protocol v2 live decision path:
authenticated challenge issuance, verification, receipt publication, the
checkpoint acknowledgement, and release. The handler runs only for a released
decision whose ``allowed()`` is true at the receiver's own clock.

Part B stores. This reference uses the SDK's in-memory reference stores
(``ratify_protocol_v2.v2reference``) for the claim store, receipt log,
checkpoint, challenge issuance store and security journal. They are not
durable, do not survive a restart, and the checkpoint shares this process, so
it is not an independent failure domain. That is sufficient for a
demonstration whose state is disposable. It is not a production-shaped
verifier: a production receiver supplies durable Part B stores and an
independent checkpoint (SPEC-v2 §7).
"""

from __future__ import annotations

from collections.abc import Callable
import copy
from dataclasses import dataclass, replace
import hashlib
import threading
import time
from typing import Any
import uuid

from ratify_protocol_v2 import (
    HybridPrivateKey,
    HybridPublicKey,
    V2Error,
    V2TrustResolution,
    V2VerifyCallbacks,
    V2VerifyRequest,
    background,
    generate_keypair_v2,
    key_id_v2,
    operation_context_v2_hash,
    session_context_v2_hash,
    verify_proof_bundle_v2,
)
from ratify_protocol_v2.challenge_issuance_v2 import (
    V2ChallengeIssueRequest,
    V2ChallengeIssuer,
    load_expected_challenge_v2,
)
from ratify_protocol_v2.constraint_evaluator_v2 import (
    new_constraint_evaluator_registry_v2,
    standard_allowed_values_evaluator_v2,
)
from ratify_protocol_v2.constraints_v2 import (
    CONSTRAINT_MAX_AMOUNT,
    CONSTRAINT_RESOURCE_PATH,
    TZDB_VERSION_V2,
    ConstraintContextV2,
)
from ratify_protocol_v2.context_policy_v2 import new_v2_context_policy
from ratify_protocol_v2.protocol_v2 import (
    DelegationCertV2,
    ProofBundleV2,
    encode_proof_bundle_v2,
)
from ratify_protocol_v2.release_v2 import (
    V2_RELEASE_RELEASED,
    V2ReleaseConfig,
    release_v2_decision,
)
from ratify_protocol_v2.security_journal_v2 import (
    configure_status_key_epoch_v2,
    record_security_key_role_v2,
)
from ratify_protocol_v2.signing_v2 import public_key_of
from ratify_protocol_v2.status_batch_v2 import (
    V2StatusEvidenceBatch,
    V2StatusEvidenceObject,
)
from ratify_protocol_v2.status_provenance_v2 import V2StatusKeyConfig
from ratify_protocol_v2.status_state_v2 import RevocationStatusPolicyV2
from ratify_protocol_v2.v2reference.challenges import TestOnlyChallengeIssuanceStore
from ratify_protocol_v2.v2reference.journal import new_journal_store
from ratify_protocol_v2.v2reference.store import Store
from ratify_protocol_v2.verify_v2 import V2_STAGE_COMMITTED, V2_STAGE_REPLAY

from .action import WorkOrder
from .profile import (
    AUDIENCE_CONSTRAINT,
    CATEGORY_CONSTRAINT,
    CATEGORY_FIELD,
    CHALLENGE_LIFETIME_SECONDS,
    CONTEXT_POLICY_FIELDS,
    CONTEXT_POLICY_PROFILE,
    DEFAULT_CATEGORY,
    DEFAULT_CURRENCY,
    MANAGED_RESOURCES,
    MICROS_PER_MINOR,
    RECEIVER_CEILING_MINOR,
    RECEIVER_ID,
    REPLAY_EPOCH,
    SESSION_ID,
    STATUS_KEY_EPOCH,
    STATUS_LIST_LIFETIME_SECONDS,
    VERIFIER_NAMESPACE,
    WORK_ORDER_OPERATION,
    WORK_ORDER_SCOPE,
    WORKSPACE_ID,
)
from .status import StatusFeed

_REVOCATION_LIST_OBJECT_TYPE = 3
_CLAIM_LEASE_SECONDS = 30
_FUTURE_SKEW_SECONDS = 5
_STATUS_POLICY = RevocationStatusPolicyV2(
    max_list_lifetime_seconds=STATUS_LIST_LIFETIME_SECONDS,
    max_status_age_seconds=STATUS_LIST_LIFETIME_SECONDS,
    clock_skew_seconds=_FUTURE_SKEW_SECONDS,
)


@dataclass(frozen=True)
class ChallengeGrant:
    challenge: bytes
    challenge_at: int
    verifier_id: str
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
    challenge: bytes
    expires_at: int


def work_order_session_context(
    action: WorkOrder, *, verifier_id: str, agent_id: str
) -> bytes:
    """The 32-byte session context for one exact work-order action."""
    request_hash = operation_context_v2_hash(
        WORK_ORDER_SCOPE,
        WORK_ORDER_OPERATION,
        action.resource,
        None,
        hashlib.sha256(action.canonical_bytes()).digest(),
    )
    return session_context_v2_hash(
        verifier_id, WORKSPACE_ID, agent_id, SESSION_ID, action.request_id,
        request_hash,
    )


class WorkOrderReceiver:
    def __init__(
        self,
        *,
        trusted_root_public_key: HybridPublicKey,
        status_public_key: HybridPublicKey,
        status_feed: StatusFeed,
        verifier_private_key: HybridPrivateKey | None = None,
        clock: Callable[[], int] | None = None,
        managed_resources: tuple[str, ...] = MANAGED_RESOURCES,
        category: str = DEFAULT_CATEGORY,
        ceiling_minor: int = RECEIVER_CEILING_MINOR,
        currency: str = DEFAULT_CURRENCY,
    ) -> None:
        self._clock = clock or (lambda: int(time.time()))
        ctx = background()
        if verifier_private_key is None:
            _, verifier_private_key = generate_keypair_v2()
        self._verifier_private_key = verifier_private_key
        self.verifier_public_key = public_key_of(verifier_private_key)
        self.verifier_id = key_id_v2(self.verifier_public_key)
        # Trust is pinned to complete public keys, never to a key ID alone.
        self.trusted_root_public_key = trusted_root_public_key
        self.trusted_root_id = key_id_v2(trusted_root_public_key)
        self.status_public_key = status_public_key
        self.status_feed: StatusFeed | None = status_feed
        self._log_id = uuid.uuid4().hex
        self.claim_store: Store | None = Store(
            self._clock(), _CLAIM_LEASE_SECONDS, self._log_id, verifier_private_key
        )
        self._issuance = TestOnlyChallengeIssuanceStore()
        self._journal = new_journal_store()
        record_security_key_role_v2(ctx, self._journal, "issuer", trusted_root_public_key)
        record_security_key_role_v2(
            ctx, self._journal, "verifier", self.verifier_public_key
        )
        # Refuses a status key equal to the root key or reusing a component.
        configure_status_key_epoch_v2(
            ctx, self._journal, self.trusted_root_id, STATUS_KEY_EPOCH,
            status_public_key,
        )
        self._evaluators = new_constraint_evaluator_registry_v2(
            1.0, standard_allowed_values_evaluator_v2()
        )
        self._managed_resources = frozenset(managed_resources)
        self._category = category
        self._ceiling_minor = ceiling_minor
        self._currency = currency
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.RLock()
        self._handler_invocations = 0

    @property
    def handler_invocations(self) -> int:
        with self._lock:
            return self._handler_invocations

    def issue_challenge(
        self,
        action: WorkOrder,
        *,
        expected_agent_public_key: HybridPublicKey,
        caller_id: str = "local",
    ) -> ChallengeResult:
        """Issue one challenge bound to this action, agent key and receiver.

        The expected agent is pinned by its complete public key, so only that
        key can consume the challenge.
        """
        if (
            type(action) is not WorkOrder
            or type(expected_agent_public_key) is not HybridPublicKey
            or not caller_id
        ):
            return ChallengeResult("DENY", "DENY_INVALID_REQUEST")
        try:
            expected_agent_id = key_id_v2(expected_agent_public_key)
            session_context = work_order_session_context(
                action, verifier_id=self.verifier_id, agent_id=expected_agent_id
            )
        except (TypeError, ValueError, V2Error):
            return ChallengeResult("DENY", "DENY_INVALID_REQUEST")
        canonical_action = action.canonical_bytes()
        with self._lock:
            now = self._clock()
            self._reap_pending(now)
            if self.claim_store is None or self.status_feed is None:
                return ChallengeResult("DENY", "DENY_VERIFIER_UNAVAILABLE")
            if action.request_id in self._pending:
                return ChallengeResult("DENY", "DENY_AMBIGUOUS_INPUT")
            try:
                self.claim_store.set_time(now)
                # The issuance helper persists the record before it returns
                # the challenge, so a challenge is never exposed unrecorded.
                record = V2ChallengeIssuer(
                    clock=self.claim_store, store=self._issuance
                ).issue_challenge_v2(background(), V2ChallengeIssueRequest(
                    verifier_namespace=VERIFIER_NAMESPACE,
                    replay_epoch=REPLAY_EPOCH,
                    verifier_id=self.verifier_id,
                    lifetime_seconds=CHALLENGE_LIFETIME_SECONDS,
                    expected_agent_id=expected_agent_id,
                    expected_agent_public_key=expected_agent_public_key,
                    expected_receiver_id=RECEIVER_ID,
                    expected_session_context=session_context,
                ))
            except Exception:
                return ChallengeResult("DENY", "DENY_VERIFIER_UNAVAILABLE")
            self._pending[action.request_id] = _Pending(
                canonical_action, caller_id, expected_agent_id, session_context,
                record.value, record.expires_at,
            )
        return ChallengeResult("ALLOW", "ALLOW", ChallengeGrant(
            record.value, record.issued_at, self.verifier_id, session_context
        ))

    def execute(
        self, action: WorkOrder, bundle: ProofBundleV2, *, caller_id: str = "local"
    ) -> dict[str, Any]:
        if type(action) is not WorkOrder or type(bundle) is not ProofBundleV2:
            return self._deny("DENY_INVALID_REQUEST", "receiver_precheck")
        try:
            action = replace(action)
            bundle = copy.deepcopy(bundle)
            if type(action) is not WorkOrder or type(bundle) is not ProofBundleV2 or any(
                type(cert) is not DelegationCertV2 for cert in bundle.delegations or []
            ):
                return self._deny("DENY_INVALID_REQUEST", "receiver_precheck")
            canonical_action = action.canonical_bytes()
            wire = encode_proof_bundle_v2(bundle)
        except Exception:
            return self._deny("DENY_INVALID_REQUEST", "receiver_precheck")

        with self._lock:
            now = self._clock()
            self._reap_pending(now)
            if self.claim_store is None or self.status_feed is None:
                return self._deny("DENY_VERIFIER_UNAVAILABLE", "receiver_precheck")
            pending = self._pending.get(action.request_id)
            if pending is None:
                return self._deny("DENY_REPLAY", "receiver_precheck")
            if not caller_id or caller_id != pending.caller_id:
                return self._deny("DENY_CALLER_MISMATCH", "receiver_precheck")
            if canonical_action != pending.canonical_action:
                return self._deny("DENY_OPERATION_MISMATCH", "receiver_precheck")
            if bundle.agent_id != pending.expected_agent_id:
                return self._deny("DENY_SUBJECT_MISMATCH", "receiver_precheck")
            if not bundle.delegations:
                return self._deny("DENY_INVALID_REQUEST", "receiver_precheck")
            root = bundle.delegations[-1]
            if (
                root.issuer_id != self.trusted_root_id
                or root.issuer_pub_key != self.trusted_root_public_key
            ):
                return self._deny("DENY_UNTRUSTED_ISSUER", "receiver_precheck")

            # One attempt per issued challenge, whatever the outcome.
            self._pending.pop(action.request_id, None)
            verification_started = time.perf_counter()
            try:
                result = self._verify_and_release(action, wire, pending, now)
            except Exception:
                return self._deny("DENY_VERIFIER_UNAVAILABLE", "ratify_verification")
            # Measured around verification, receipt publication and release,
            # so the receiver's own cost can be separated from platform
            # transit.
            verification_ms = round(
                (time.perf_counter() - verification_started) * 1000
            )
            kind, status, decision = result
            if kind == "rejected":
                return self._deny(
                    self._rejection_reason(status), "ratify_verification",
                    status, verification_ms,
                )
            if kind == "unreleased":
                return self._deny(
                    "DENY_VERIFIER_UNAVAILABLE", "ratify_verification", status,
                    verification_ms,
                )
            allowed = decision.allowed(background(), self.claim_store)
            if not allowed:
                return self._deny(
                    self._decision_reason(decision.reason(), action, bundle),
                    "ratify_verification",
                    decision.identity_status(),
                    verification_ms,
                )

            policy_reason = self._evaluate_local_policy(action, bundle)
            if policy_reason is not None:
                return self._deny(
                    policy_reason, "receiver_policy", decision.identity_status(),
                    verification_ms,
                )
            return self._invoke_handler(
                action, decision.identity_status(), verification_ms
            )

    def _verify_and_release(
        self, action: WorkOrder, wire: bytes, pending: _Pending, now: int
    ):
        """Run verification, publish the signed receipt, and release.

        Returns ``("rejected", error_class, None)`` for a rejection before
        challenge claim (no Protocol receipt exists), ``("unreleased",
        status, None)`` when a committed result could not be released, and
        ``("released", identity_status, decision)`` otherwise.
        """
        ctx = background()
        store = self.claim_store
        store.set_time(now)
        expected = load_expected_challenge_v2(
            ctx, self._issuance, VERIFIER_NAMESPACE, REPLAY_EPOCH, pending.challenge
        )
        feed = self.status_feed

        def trust(_ctx, chain):
            if chain[-1].issuer_id == self.trusted_root_id:
                return V2TrustResolution(
                    outcome="trusted", root_key=self.trusted_root_public_key
                )
            return V2TrustResolution(outcome="untrusted")

        def status_evidence(_ctx, _request):
            return V2StatusEvidenceBatch(objects=[V2StatusEvidenceObject(
                object_type=_REVOCATION_LIST_OBJECT_TYPE, wire=feed()
            )])

        outcome = verify_proof_bundle_v2(ctx, V2VerifyRequest(
            bundle=wire,
            expected=expected,
            context_policy=new_v2_context_policy(
                CONTEXT_POLICY_PROFILE, True, list(CONTEXT_POLICY_FIELDS),
                pending.session_context,
            ),
            required_scope=WORK_ORDER_SCOPE,
            verifier_namespace=VERIFIER_NAMESPACE,
            replay_epoch=REPLAY_EPOCH,
            now=store.now(),
            max_challenge_age_seconds=CHALLENGE_LIFETIME_SECONDS,
            future_skew_seconds=_FUTURE_SKEW_SECONDS,
            # Every value comes from the authenticated action the challenge
            # was issued for, never from the proof.
            context=ConstraintContextV2(
                has_amount=True,
                requested_amount_micros=action.amount_minor * MICROS_PER_MINOR,
                requested_currency=action.currency,
                has_resource=True,
                requested_resource_id=action.resource,
                extension_values={CATEGORY_FIELD: action.category},
            ),
            constraint_evaluators=self._evaluators,
            tzdb_version=TZDB_VERSION_V2,
            status_keys={self.trusted_root_id: V2StatusKeyConfig(
                epoch=STATUS_KEY_EPOCH, key=self.status_public_key
            )},
            status_policy=_STATUS_POLICY,
        ), V2VerifyCallbacks(
            claim_store=store,
            trust=trust,
            status_evidence=status_evidence,
            status_journal=self._journal,
        ))
        if outcome.stage == V2_STAGE_REPLAY:
            return "rejected", "claim_replay", None
        if outcome.stage != V2_STAGE_COMMITTED:
            return "rejected", outcome.error_class or "unclassified", None
        receipt_wire = store.publish_v2_receipt(
            ctx, outcome.committed.claim_id(), outcome.committed.receipt_body()
        )
        released = release_v2_decision(ctx, outcome.committed, receipt_wire, V2ReleaseConfig(
            verifier_public_key=self.verifier_public_key,
            log_id=self._log_id,
            claim_store=store,
            checkpoint=store,
        ))
        if released.status != V2_RELEASE_RELEASED:
            return "unreleased", f"release_{released.status}", None
        return "released", released.decision.identity_status(), released.decision

    def _evaluate_local_policy(
        self, action: WorkOrder, bundle: ProofBundleV2
    ) -> str | None:
        required_constraints = {
            CONSTRAINT_RESOURCE_PATH, CONSTRAINT_MAX_AMOUNT, CATEGORY_CONSTRAINT,
            AUDIENCE_CONSTRAINT,
        }
        present = {
            constraint.type for constraint in bundle.delegations[0].constraints or []
        }
        if not required_constraints.issubset(present):
            return "DENY_CONSTRAINT_MISMATCH"
        # Receiver capability, not agent authority. Whether this particular
        # agent may act on a managed site, and for how much, is decided by its
        # delegation during verification, which runs before this check.
        if action.resource not in self._managed_resources:
            return "DENY_RESOURCE_MISMATCH"
        if action.category != self._category:
            return "DENY_CONSTRAINT_MISMATCH"
        if action.currency != self._currency:
            return "DENY_CONSTRAINT_MISMATCH"
        if action.amount_minor > self._ceiling_minor:
            return "DENY_LIMIT_EXCEEDED"
        return None

    def _invoke_handler(
        self, action: WorkOrder, verification_status: str, verification_ms: int
    ) -> dict[str, Any]:
        self._handler_invocations += 1
        return {
            "decision": "ALLOW",
            "reason": "ALLOW",
            "decided_by": "ratify_verification",
            "verification_status": verification_status,
            "verification_duration_ms": verification_ms,
            "handler_invoked": True,
            "handler_invocations": self._handler_invocations,
            "work_order_id": f"demo-{action.request_id}",
        }

    def _reap_pending(self, now: int) -> None:
        self._pending = {
            request_id: pending
            for request_id, pending in self._pending.items()
            if pending.expires_at > now
        }

    def _deny(
        self,
        reason: str,
        decided_by: str,
        verification_status: str | None = None,
        verification_ms: int | None = None,
    ) -> dict[str, Any]:
        return {
            "decision": "DENY",
            "reason": reason,
            "decided_by": decided_by,
            "verification_status": verification_status,
            # Null when the request was refused before verification ran, which
            # is what distinguishes a receiver-side check from a protocol one.
            "verification_duration_ms": verification_ms,
            "handler_invoked": False,
            "handler_invocations": self._handler_invocations,
        }

    @staticmethod
    def _rejection_reason(error_class: str) -> str:
        """Public reason for a rejection before challenge claim."""
        return {
            "claim_replay": "DENY_REPLAY",
            "session_context_mismatch": "DENY_AUDIENCE_MISMATCH",
            "verifier_id_mismatch": "DENY_AUDIENCE_MISMATCH",
            "receiver_audience_mismatch": "DENY_AUDIENCE_MISMATCH",
            "receiver_audience_required": "DENY_AUDIENCE_MISMATCH",
            "expected_agent_mismatch": "DENY_SUBJECT_MISMATCH",
            "claim_store_unavailable": "DENY_VERIFIER_UNAVAILABLE",
            "configuration_invalid": "DENY_VERIFIER_UNAVAILABLE",
            "clock_regressed": "DENY_VERIFIER_UNAVAILABLE",
            "expected_challenge_unavailable": "DENY_VERIFIER_UNAVAILABLE",
        }.get(error_class, "DENY_VERIFICATION_FAILED")

    @classmethod
    def _decision_reason(
        cls, reason: str, action: WorkOrder, bundle: ProofBundleV2
    ) -> str:
        """Public reason for a released decision that does not allow."""
        if reason == "constraint_denied":
            return cls._constraint_reason(action, bundle)
        return {
            "delegation_expired": "DENY_EXPIRED",
            "delegation_revoked": "DENY_REVOKED",
            "scope_denied": "DENY_SCOPE_MISMATCH",
            "invalid_scope": "DENY_SCOPE_MISMATCH",
            "constraint_unknown": "DENY_CONSTRAINT_MISMATCH",
            "unauthorized": "DENY_UNTRUSTED_ISSUER",
            "constraint_unverifiable": "DENY_VERIFIER_UNAVAILABLE",
            "status_unavailable": "DENY_VERIFIER_UNAVAILABLE",
            "status_stale": "DENY_VERIFIER_UNAVAILABLE",
            "trust_unavailable": "DENY_VERIFIER_UNAVAILABLE",
            "evaluation_interrupted": "DENY_VERIFIER_UNAVAILABLE",
            "action_context_unverified": "DENY_VERIFIER_UNAVAILABLE",
        }.get(reason, "DENY_VERIFICATION_FAILED")

    @staticmethod
    def _constraint_reason(action: WorkOrder, bundle: ProofBundleV2) -> str:
        """Name which signed bound a Protocol constraint denial came from.

        The Protocol receipt records constraint_denied without naming the
        constraint. This reads the already-verified certificates to choose
        the public reason code; it runs only after a released deny and can
        never turn a deny into an allow.
        """
        requested_micros = action.amount_minor * MICROS_PER_MINOR
        for cert in bundle.delegations or []:
            for constraint in cert.constraints or []:
                if constraint.type == CONSTRAINT_MAX_AMOUNT:
                    if constraint.currency != action.currency:
                        return "DENY_CONSTRAINT_MISMATCH"
                    if requested_micros > constraint.max_amount_micros:
                        return "DENY_LIMIT_EXCEEDED"
                if constraint.type == CONSTRAINT_RESOURCE_PATH and (
                    constraint.resource_id != action.resource
                ):
                    return "DENY_RESOURCE_MISMATCH"
        return "DENY_CONSTRAINT_MISMATCH"

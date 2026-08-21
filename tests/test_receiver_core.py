from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import threading
import time

import pytest
from ratify_protocol import (
    Constraint,
    DelegationCert,
    HybridSignature,
    MemoryChallengeStore,
    OperationContext,
    PROTOCOL_VERSION,
    SessionContextInputs,
    build_session_context,
    generate_agent,
    generate_human_root,
    issue_delegation,
    operation_context_hash,
    sign_challenge,
)

from maritime_ratify import WorkOrder, WorkOrderReceiver, issue_authority
from maritime_ratify.authority import AuthorityFixture, WORK_ORDER_SCOPE
from maritime_ratify.profile import AUDIENCE_CONSTRAINT, CATEGORY_CONSTRAINT, VERIFIER_ID


def setup_reference(**authority_options):
    now = int(time.time())
    authority = issue_authority(now=now - 1, **authority_options)
    receiver = WorkOrderReceiver(
        trusted_root_id=authority.root_id,
        trusted_root_public_key=authority.root_public_key,
        clock=lambda: now,
    )
    return now, authority, receiver


def action(**changes):
    values = {
        "request_id": "req-allow",
        "scope": WORK_ORDER_SCOPE,
        "resource": "site:warehouse-seattle-01",
        "category": "electrical",
        "amount_minor": 42_000,
        "currency": "USD",
        "description": "Inspect and repair loading-bay lighting",
    }
    values.update(changes)
    return WorkOrder(**values)


def present(authority, receiver, requested, now):
    challenge = receiver.issue_challenge(requested, expected_agent_id=authority.agent_id)
    assert challenge.decision == "ALLOW"
    assert challenge.grant is not None
    return authority.present(
        challenge=challenge.grant.challenge,
        session_context=challenge.grant.session_context,
        now=now,
    )


def custom_authority(now, constraints):
    root, root_private = generate_human_root()
    agent, agent_private = generate_agent("Maritime Work Order Agent", "custom")
    cert = DelegationCert(
        cert_id="maritime-test-custom",
        version=PROTOCOL_VERSION,
        issuer_id=root.id,
        issuer_pub_key=root.public_key,
        subject_id=agent.id,
        subject_pub_key=agent.public_key,
        scope=[WORK_ORDER_SCOPE],
        constraints=list(constraints),
        issued_at=now - 1,
        expires_at=now + 3600,
        signature=HybridSignature(ed25519=b"", ml_dsa_65=b""),
    )
    issue_delegation(cert, root_private)
    authority = AuthorityFixture(root.id, root.public_key, agent.id, agent_private, cert)
    receiver = WorkOrderReceiver(
        trusted_root_id=root.id,
        trusted_root_public_key=root.public_key,
        clock=lambda: now,
    )
    return authority, receiver


def test_exact_delegated_action_invokes_handler_once():
    now, authority, receiver = setup_reference()
    requested = action()
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["decision"] == "ALLOW"
    assert result["reason"] == "ALLOW"
    assert result["handler_invocations"] == 1


@pytest.mark.parametrize(
    ("requested", "expected_reason"),
    [
        (action(request_id="req-limit", amount_minor=65_000), "DENY_LIMIT_EXCEEDED"),
        (action(request_id="req-resource", resource="site:warehouse-portland-01"), "DENY_RESOURCE_MISMATCH"),
        (action(request_id="req-category", category="plumbing"), "DENY_CONSTRAINT_MISMATCH"),
    ],
)
def test_signed_bounds_deny_before_handler(requested, expected_reason):
    now, authority, receiver = setup_reference()
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["decision"] == "DENY"
    assert result["reason"] == expected_reason
    assert result["handler_invocations"] == 0


def test_changed_action_denies_before_handler():
    now, authority, receiver = setup_reference()
    original = action(request_id="req-altered")
    proof = present(authority, receiver, original, now)
    result = receiver.execute(replace(original, amount_minor=43_000), proof)
    assert result["reason"] == "DENY_OPERATION_MISMATCH"
    assert result["handler_invocations"] == 0


def test_receiver_owns_clock_and_expired_authority_denies():
    now = int(time.time())
    authority = issue_authority(now=now - 7200, expires_at=now - 3600)
    receiver = WorkOrderReceiver(
        trusted_root_id=authority.root_id,
        trusted_root_public_key=authority.root_public_key,
        clock=lambda: now,
    )
    requested = action(request_id="req-expired")
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["reason"] == "DENY_EXPIRED"
    assert result["handler_invocations"] == 0
    assert "now" not in inspect.signature(receiver.execute).parameters


def test_revoked_authority_denies_before_handler():
    now, authority, receiver = setup_reference()
    receiver.revocation.revoke(authority.delegation.cert_id)
    requested = action(request_id="req-revoked")
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["reason"] == "DENY_REVOKED"
    assert result["handler_invocations"] == 0


def test_replay_invokes_handler_at_most_once():
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-replay")
    proof = present(authority, receiver, requested, now)
    assert receiver.execute(requested, proof)["decision"] == "ALLOW"
    replay = receiver.execute(requested, proof)
    assert replay["reason"] == "DENY_REPLAY"
    assert replay["handler_invocations"] == 1


def test_concurrent_replay_invokes_handler_at_most_once():
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-concurrent")
    proof = present(authority, receiver, requested, now)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: receiver.execute(requested, proof), range(16)))
    assert sum(result["decision"] == "ALLOW" for result in results) == 1
    assert receiver.handler_invocations == 1


class NaiveSharedStore:
    """Protocol-compatible store with a deliberately non-atomic consume."""

    def __init__(self):
        self._inner = MemoryChallengeStore(max_size=128)

    def issue(self, session_context, ttl_seconds):
        return self._inner.issue(session_context, ttl_seconds)

    def validate(self, challenge, session_context, now):
        return self._inner.validate(challenge, session_context, now)

    def consume(self, challenge, session_context, now):
        error = self._inner.validate(challenge, session_context, now)
        if error is not None:
            return error
        time.sleep(0.002)
        self._inner.consume(challenge, session_context, now)
        return None


def test_receiver_owns_single_invocation_with_non_atomic_store():
    now, authority, receiver = setup_reference()
    receiver.challenge_store = NaiveSharedStore()
    requested = action(request_id="req-naive-store")
    proof = present(authority, receiver, requested, now)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: receiver.execute(requested, proof), range(16)))
    assert sum(result["decision"] == "ALLOW" for result in results) == 1
    assert receiver.handler_invocations == 1


def test_wrong_agent_denies_before_handler():
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-agent")
    challenge = receiver.issue_challenge(requested, expected_agent_id=authority.agent_id)
    assert challenge.grant is not None
    hostile, hostile_private = generate_agent("Hostile", "custom")
    proof = authority.present(
        challenge=challenge.grant.challenge,
        session_context=challenge.grant.session_context,
        now=now,
    )
    proof = replace(
        proof,
        agent_id=hostile.id,
        agent_pub_key=hostile.public_key,
        challenge_sig=sign_challenge(
            challenge.grant.challenge,
            now,
            hostile_private,
            challenge.grant.session_context,
        ),
    )
    result = receiver.execute(requested, proof)
    assert result["reason"] == "DENY_SUBJECT_MISMATCH"
    assert result["handler_invocations"] == 0


def test_untrusted_issuer_denies_before_handler():
    now, authority, receiver = setup_reference()
    hostile = issue_authority(now=now - 1)
    requested = action(request_id="req-issuer")
    challenge = receiver.issue_challenge(requested, expected_agent_id=hostile.agent_id)
    assert challenge.grant is not None
    proof = hostile.present(
        challenge=challenge.grant.challenge,
        session_context=challenge.grant.session_context,
        now=now,
    )
    result = receiver.execute(requested, proof)
    assert result["reason"] == "DENY_UNTRUSTED_ISSUER"
    assert result["handler_invocations"] == 0


@pytest.mark.parametrize("missing", ["challenge_store", "revocation"])
def test_missing_verifier_state_fails_closed(missing):
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-state")
    proof = present(authority, receiver, requested, now)
    setattr(receiver, missing, None)
    result = receiver.execute(requested, proof)
    assert result["reason"] == "DENY_VERIFIER_UNAVAILABLE"
    assert result["handler_invocations"] == 0


def test_wrong_audience_denies_before_handler():
    now, authority, receiver = setup_reference(audience="other-receiver")
    requested = action(request_id="req-audience")
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["reason"] == "DENY_AUDIENCE_MISMATCH"
    assert result["handler_invocations"] == 0


def test_scope_declared_by_caller_must_match_profile():
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-scope", scope="custom:unrelated")
    challenge = receiver.issue_challenge(requested, expected_agent_id=authority.agent_id)
    assert challenge.reason == "DENY_INVALID_REQUEST"
    assert receiver.handler_invocations == 0


@pytest.mark.parametrize("amount", [True, 1.5, -1, 100_000_001])
def test_invalid_amount_is_rejected_at_challenge(amount):
    _, authority, receiver = setup_reference()
    result = receiver.issue_challenge(
        action(request_id=f"req-invalid-{amount}", amount_minor=amount),
        expected_agent_id=authority.agent_id,
    )
    assert result.reason == "DENY_INVALID_REQUEST"
    assert receiver.handler_invocations == 0


def test_signed_amount_is_major_units_while_action_uses_minor_units():
    _, authority, _ = setup_reference()
    amount = next(
        constraint
        for constraint in authority.delegation.constraints
        if constraint.type == "max_amount"
    )
    assert amount.max_amount == 500.0


def test_duck_typed_action_cannot_bypass_operation_binding():
    now, authority, receiver = setup_reference()
    original = action(request_id="req-duck")
    proof = present(authority, receiver, original, now)

    class HostileAction:
        def validate(self):
            return None

        def __eq__(self, other):
            return True

    result = receiver.execute(HostileAction(), proof)
    assert result["reason"] == "DENY_INVALID_REQUEST"
    assert result["handler_invocations"] == 0


def test_missing_required_signed_bound_denies_in_local_policy():
    now = int(time.time())
    constraints = [
        Constraint(type="resource_path", resource_id="site:warehouse-seattle-01"),
        Constraint(type=CATEGORY_CONSTRAINT, params={"allowed": "electrical"}),
        Constraint(type=AUDIENCE_CONSTRAINT, params={"allowed": VERIFIER_ID}),
    ]
    authority, receiver = custom_authority(now, constraints)
    requested = action(request_id="req-unbounded")
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["reason"] == "DENY_CONSTRAINT_MISMATCH"
    assert result["handler_invocations"] == 0


def test_bundle_and_nested_certificate_types_are_closed():
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-bundle-type")
    proof = present(authority, receiver, requested, now)

    class Passthrough:
        def __init__(self, real):
            self.real = real

        def __getattr__(self, name):
            return getattr(self.real, name)

    assert receiver.execute(requested, Passthrough(proof))["reason"] == "DENY_INVALID_REQUEST"
    proof.delegations = [Passthrough(proof.delegations[0])]
    assert receiver.execute(requested, proof)["reason"] == "DENY_INVALID_REQUEST"
    assert receiver.handler_invocations == 0


@pytest.mark.parametrize("mode", ["error", "raise"])
def test_revocation_backend_failures_are_stable_and_redacted(mode):
    now, authority, receiver = setup_reference()
    requested = action(request_id=f"req-revocation-{mode}")
    proof = present(authority, receiver, requested, now)

    class BrokenRevocation:
        def is_revoked(self, cert_id):
            if mode == "raise":
                raise ConnectionError("redis://secret-host:6379 refused")
            return False, "backend unreachable"

    receiver.revocation = BrokenRevocation()
    result = receiver.execute(requested, proof)
    assert result["reason"] == "DENY_VERIFIER_UNAVAILABLE"
    assert "secret" not in repr(result)
    assert receiver.handler_invocations == 0


def test_wrong_currency_has_stable_constraint_reason():
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-currency", currency="EUR")
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["reason"] == "DENY_CONSTRAINT_MISMATCH"
    assert result["handler_invocations"] == 0


def test_foreign_session_context_has_stable_audience_reason():
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-session-audience")
    challenge = receiver.issue_challenge(requested, expected_agent_id=authority.agent_id)
    assert challenge.grant is not None
    foreign = build_session_context(
        SessionContextInputs(
            verifier_id="other-receiver",
            workspace_id="maritime-demo",
            agent_id=authority.agent_id,
            session_id="maritime-reference",
            invocation_id=requested.request_id,
            request_hash=operation_context_hash(
                OperationContext(
                    required_scope=WORK_ORDER_SCOPE,
                    operation="work_order.create",
                    resource_id=requested.resource,
                    payload_digest=hashlib.sha256(requested.canonical_bytes()).digest(),
                )
            ),
        )
    )
    proof = authority.present(
        challenge=challenge.grant.challenge,
        session_context=foreign,
        now=now,
    )
    result = receiver.execute(requested, proof)
    assert result["reason"] == "DENY_AUDIENCE_MISMATCH"
    assert result["handler_invocations"] == 0


@pytest.mark.parametrize(
    ("field", "hostile"),
    [
        ("amount_minor", 100_000_000),
        ("currency", "EUR"),
        ("resource", "site:warehouse-portland-01"),
        ("category", "demolition"),
        ("description", "Demolish the north wall"),
        ("request_id", "req-other-live"),
    ],
)
def test_action_mutation_during_verification_cannot_change_handler_input(
    monkeypatch, field, hostile
):
    now, authority, receiver = setup_reference()
    requested = action(request_id=f"req-action-snapshot-{field}")
    proof = present(authority, receiver, requested, now)
    entered = threading.Event()
    release = threading.Event()

    class BlockingRevocation:
        def is_revoked(self, cert_id):
            entered.set()
            assert release.wait(timeout=2)
            return False, None

    captured = []
    real_handler = receiver._invoke_handler

    def capture(snapshot):
        captured.append(snapshot)
        return real_handler(snapshot)

    receiver.revocation = BlockingRevocation()
    monkeypatch.setattr(receiver, "_invoke_handler", capture)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result_future = pool.submit(receiver.execute, requested, proof)
        assert entered.wait(timeout=2)
        object.__setattr__(requested, field, hostile)
        release.set()
        result = result_future.result(timeout=2)

    assert result["decision"] == "ALLOW"
    assert len(captured) == 1
    assert getattr(captured[0], field) != hostile


def test_foreign_caller_cannot_evict_or_use_pending_challenge():
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-caller-owned")
    challenge = receiver.issue_challenge(
        requested,
        expected_agent_id=authority.agent_id,
        caller_id="caller-victim",
    )
    assert challenge.grant is not None
    proof = authority.present(
        challenge=challenge.grant.challenge,
        session_context=challenge.grant.session_context,
        now=now,
    )
    hostile = receiver.execute(requested, proof, caller_id="caller-attacker")
    assert hostile["reason"] == "DENY_CALLER_MISMATCH"
    assert hostile["handler_invocations"] == 0
    legitimate = receiver.execute(requested, proof, caller_id="caller-victim")
    assert legitimate["decision"] == "ALLOW"
    assert legitimate["handler_invocations"] == 1


def test_handler_is_separate_and_never_called_on_denial(monkeypatch):
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-handler", amount_minor=65_000)
    proof = present(authority, receiver, requested, now)

    def forbidden(_action):
        raise AssertionError("handler reached on denial")

    monkeypatch.setattr(receiver, "_invoke_handler", forbidden)
    result = receiver.execute(requested, proof)
    assert result["reason"] == "DENY_LIMIT_EXCEEDED"

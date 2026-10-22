from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import inspect
import threading
import time

import pytest
from ratify_protocol_v2 import generate_keypair_v2, key_id_v2
from ratify_protocol_v2.constraints_v2 import CONSTRAINT_MAX_AMOUNT
from ratify_protocol_v2.v2reference.store import Store

from maritime_ratify import WorkOrder, WorkOrderReceiver, issue_authority
from maritime_ratify.authority import (
    AuthorityFixture,
    WORK_ORDER_SCOPE,
    bounded_constraints,
    issue_bounded_delegation,
)
from maritime_ratify.profile import (
    RECEIVER_CEILING_MINOR,
    SECOND_RESOURCE,
)
from maritime_ratify.receiver import work_order_session_context
from support import SignedStatus, make_receiver


def setup_reference(**authority_options):
    now = int(time.time())
    authority = issue_authority(now=now - 1, **authority_options)
    receiver = make_receiver(authority.root_public_key, clock=lambda: now)
    return now, authority, receiver


def agent_key(authority):
    return authority.delegation.subject_pub_key


def sign(authority, grant, *, session_context=None):
    return authority.present(
        challenge=grant.challenge,
        challenge_at=grant.challenge_at,
        verifier_id=grant.verifier_id,
        session_context=grant.session_context if session_context is None else session_context,
    )


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
    challenge = receiver.issue_challenge(
        requested, expected_agent_public_key=agent_key(authority)
    )
    assert challenge.decision == "ALLOW"
    assert challenge.grant is not None
    assert challenge.grant.challenge_at == now
    return sign(authority, challenge.grant)


def custom_authority(now, constraints):
    root_public, root_private = generate_keypair_v2()
    agent_public, agent_private = generate_keypair_v2()
    cert = issue_bounded_delegation(
        root_public_key=root_public,
        root_private_key=root_private,
        agent_public_key=agent_public,
        issued_at=now - 1,
        expires_at=now + 3600,
        constraints=list(constraints),
    )
    authority = AuthorityFixture(
        key_id_v2(root_public), root_public, key_id_v2(agent_public),
        agent_private, cert,
    )
    receiver = make_receiver(root_public, clock=lambda: now)
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
    receiver = make_receiver(authority.root_public_key, clock=lambda: now)
    requested = action(request_id="req-expired")
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["reason"] == "DENY_EXPIRED"
    assert result["handler_invocations"] == 0
    assert "now" not in inspect.signature(receiver.execute).parameters


def test_revoked_authority_denies_before_handler():
    now, authority, receiver = setup_reference()
    # Revocation reaches the receiver only as a list signed by the separate
    # status key.
    receiver.test_status.revoke(authority.delegation.cert_id)
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


class SlowClaimStore(Store):
    """The reference claim store with a deliberate pause before each claim."""

    def claim_v2(self, ctx, claim):
        time.sleep(0.002)
        return super().claim_v2(ctx, claim)


def test_receiver_owns_single_invocation_with_slow_claim_store():
    now, authority, receiver = setup_reference()
    receiver.claim_store = SlowClaimStore(
        now, 30, receiver._log_id, receiver._verifier_private_key
    )
    requested = action(request_id="req-naive-store")
    proof = present(authority, receiver, requested, now)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: receiver.execute(requested, proof), range(16)))
    assert sum(result["decision"] == "ALLOW" for result in results) == 1
    assert receiver.handler_invocations == 1


def test_wrong_agent_denies_before_handler():
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-agent")
    challenge = receiver.issue_challenge(
        requested, expected_agent_public_key=agent_key(authority)
    )
    assert challenge.grant is not None
    hostile_public, _ = generate_keypair_v2()
    proof = replace(
        sign(authority, challenge.grant),
        agent_id=key_id_v2(hostile_public),
        agent_pub_key=hostile_public,
    )
    result = receiver.execute(requested, proof)
    assert result["reason"] == "DENY_SUBJECT_MISMATCH"
    assert result["handler_invocations"] == 0


def test_untrusted_issuer_denies_before_handler():
    now, authority, receiver = setup_reference()
    hostile = issue_authority(now=now - 1)
    requested = action(request_id="req-issuer")
    challenge = receiver.issue_challenge(
        requested, expected_agent_public_key=agent_key(hostile)
    )
    assert challenge.grant is not None
    proof = sign(hostile, challenge.grant)
    result = receiver.execute(requested, proof)
    assert result["reason"] == "DENY_UNTRUSTED_ISSUER"
    assert result["handler_invocations"] == 0


@pytest.mark.parametrize("missing", ["claim_store", "status_feed"])
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
    challenge = receiver.issue_challenge(
        requested, expected_agent_public_key=agent_key(authority)
    )
    assert challenge.reason == "DENY_INVALID_REQUEST"
    assert receiver.handler_invocations == 0


@pytest.mark.parametrize("amount", [True, 1.5, -1, 100_000_001])
def test_invalid_amount_is_rejected_at_challenge(amount):
    _, authority, receiver = setup_reference()
    result = receiver.issue_challenge(
        action(request_id=f"req-invalid-{amount}", amount_minor=amount),
        expected_agent_public_key=agent_key(authority),
    )
    assert result.reason == "DENY_INVALID_REQUEST"
    assert receiver.handler_invocations == 0


def test_signed_amount_is_integer_micros_while_action_uses_cents():
    """v2 signs amounts in integer micro-units of the major unit, never a float."""
    _, authority, _ = setup_reference()
    amount = next(
        constraint
        for constraint in authority.delegation.constraints
        if constraint.type == CONSTRAINT_MAX_AMOUNT
    )
    assert amount.max_amount_micros == 500_000_000
    assert type(amount.max_amount_micros) is int


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
        constraint for constraint in bounded_constraints()
        if constraint.type != CONSTRAINT_MAX_AMOUNT
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

    def broken_status():
        if mode == "raise":
            raise ConnectionError("redis://secret-host:6379 refused")
        return b'{"unsigned": "backend unreachable"}'

    receiver.status_feed = broken_status
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
    challenge = receiver.issue_challenge(
        requested, expected_agent_public_key=agent_key(authority)
    )
    assert challenge.grant is not None
    # The same action bound for a different verifier key.
    foreign = work_order_session_context(
        requested, verifier_id="0" * 64, agent_id=authority.agent_id
    )
    proof = sign(authority, challenge.grant, session_context=foreign)
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

    real_status = receiver.status_feed

    def blocking_status():
        entered.set()
        assert release.wait(timeout=2)
        return real_status()

    captured = []
    real_handler = receiver._invoke_handler

    def capture(snapshot, verification_status, verification_ms):
        captured.append(snapshot)
        return real_handler(snapshot, verification_status, verification_ms)

    receiver.status_feed = blocking_status
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
        expected_agent_public_key=agent_key(authority),
        caller_id="caller-victim",
    )
    assert challenge.grant is not None
    proof = sign(authority, challenge.grant)
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


def test_receiver_capability_is_separate_from_agent_authority():
    """A site the receiver manages is still denied unless this agent holds it.

    This is the distinction the two-runtime demonstration rests on. The
    receiver manages Seattle and Portland; an agent delegated only Seattle must
    still be denied for Portland, and the denial has to come from verifying its
    delegation rather than from the receiver's own capability check.
    """
    now, authority, receiver = setup_reference()
    requested = action(request_id="req-managed-not-delegated", resource=SECOND_RESOURCE)
    proof = present(authority, receiver, requested, now)
    result = receiver.execute(requested, proof)
    assert result["decision"] == "DENY"
    assert result["reason"] == "DENY_RESOURCE_MISMATCH"
    assert result["decided_by"] == "ratify_verification"
    assert result["verification_status"] == "constraint_denied"
    assert result["handler_invoked"] is False


def test_receiver_refuses_a_site_it_does_not_manage():
    now, authority, receiver = setup_reference(resource="site:warehouse-denver-01")
    requested = action(
        request_id="req-unmanaged", resource="site:warehouse-denver-01"
    )
    proof = present(authority, receiver, requested, now)
    result = receiver.execute(requested, proof)
    assert result["decision"] == "DENY"
    assert result["reason"] == "DENY_RESOURCE_MISMATCH"
    assert result["decided_by"] == "receiver_policy"
    assert result["handler_invoked"] is False


def test_receiver_ceiling_applies_above_every_delegated_ceiling():
    now, authority, receiver = setup_reference(
        max_amount_minor=RECEIVER_CEILING_MINOR + 100_000
    )
    requested = action(
        request_id="req-above-ceiling", amount_minor=RECEIVER_CEILING_MINOR + 1
    )
    proof = present(authority, receiver, requested, now)
    result = receiver.execute(requested, proof)
    assert result["decision"] == "DENY"
    assert result["reason"] == "DENY_LIMIT_EXCEEDED"
    assert result["decided_by"] == "receiver_policy"
    assert result["handler_invoked"] is False


def test_second_agent_cannot_use_the_first_agents_subject():
    """The deployed cross-runtime case, without the deployment.

    One receiver, two separately delegated agents. The challenge is issued for
    agent B, and agent A's authority presented against it is refused before any
    verification, because the receiver binds the challenge to one subject.
    """
    now, first, receiver = setup_reference()
    second = issue_authority(now=now - 1, resource=SECOND_RESOURCE)
    requested = action(request_id="req-cross-runtime")
    challenge = receiver.issue_challenge(
        requested, expected_agent_public_key=agent_key(second)
    )
    assert challenge.grant is not None
    borrowed = sign(first, challenge.grant)
    result = receiver.execute(requested, borrowed)
    assert result["decision"] == "DENY"
    assert result["reason"] == "DENY_SUBJECT_MISMATCH"
    assert result["decided_by"] == "receiver_precheck"
    assert result["handler_invoked"] is False


def test_status_key_must_be_separate_from_the_root_key():
    """The root that issues delegations can never sign status for itself."""
    root_public, _ = generate_keypair_v2()
    with pytest.raises(Exception):
        WorkOrderReceiver(
            trusted_root_public_key=root_public,
            status_public_key=root_public,
            status_feed=lambda: b"",
        )


def test_status_signed_by_an_unpinned_key_never_allows():
    now, authority, receiver = setup_reference()
    receiver.status_feed = SignedStatus(authority.root_public_key, now).feed
    requested = action(request_id="req-unpinned-status")
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["decision"] == "DENY"
    assert result["reason"] == "DENY_VERIFIER_UNAVAILABLE"
    assert result["decided_by"] == "ratify_verification"
    assert result["handler_invocations"] == 0


def test_stale_signed_status_never_allows():
    """A list past its one-day freshness bound is not current status."""
    now = int(time.time())
    authority = issue_authority(now=now - 1)
    status = SignedStatus(authority.root_public_key, now - 2 * 86_400)
    receiver = WorkOrderReceiver(
        trusted_root_public_key=authority.root_public_key,
        status_public_key=status.public_key,
        status_feed=status.feed,
        clock=lambda: now,
    )
    requested = action(request_id="req-stale-status")
    result = receiver.execute(requested, present(authority, receiver, requested, now))
    assert result["reason"] == "DENY_VERIFIER_UNAVAILABLE"
    assert result["verification_status"] == "authorization_unverified"
    assert result["handler_invocations"] == 0


def test_every_decision_is_released_from_a_published_receipt():
    """Allow and deny both pass through the receipt log and the checkpoint."""
    now, authority, receiver = setup_reference()
    allowed = action(request_id="req-receipt-allow")
    denied = action(request_id="req-receipt-deny", amount_minor=65_000)
    assert receiver.execute(
        allowed, present(authority, receiver, allowed, now)
    )["decision"] == "ALLOW"
    assert receiver.execute(
        denied, present(authority, receiver, denied, now)
    )["decision"] == "DENY"
    assert receiver.claim_store.log_length() == 2


def test_verifier_id_is_the_receivers_64_hex_key_id():
    _, _, receiver = setup_reference()
    assert receiver.verifier_id == key_id_v2(receiver.verifier_public_key)
    assert len(receiver.verifier_id) == 64
    assert receiver.trusted_root_id == key_id_v2(receiver.trusted_root_public_key)


def test_profile_constraint_names_are_the_sdk_registered_names():
    """The profile spells these out so scripts can read it without the SDK."""
    from ratify_protocol_v2.constraint_evaluator_v2 import CONSTRAINT_ALLOWED_VALUES_V2
    from ratify_protocol_v2.constraints_v2 import CONSTRAINT_RECEIVER_AUDIENCE

    from maritime_ratify import profile

    assert profile.CATEGORY_CONSTRAINT == CONSTRAINT_ALLOWED_VALUES_V2
    assert profile.AUDIENCE_CONSTRAINT == CONSTRAINT_RECEIVER_AUDIENCE

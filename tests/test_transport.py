from concurrent.futures import ThreadPoolExecutor
import time

import pytest
from ratify_protocol import MAX_PROOF_BUNDLE_BYTES, encode_proof_bundle

from maritime_ratify import (
    CallerAuthenticator,
    CarrierDenied,
    PresentationRegistry,
    WorkOrder,
    WorkOrderReceiver,
    issue_authority,
)
from maritime_ratify.profile import WORK_ORDER_SCOPE


def fixture():
    now = int(time.time())
    authority = issue_authority(now=now - 1)
    receiver = WorkOrderReceiver(
        trusted_root_id=authority.root_id,
        trusted_root_public_key=authority.root_public_key,
        clock=lambda: now,
    )
    action = WorkOrder(
        "req-transport", WORK_ORDER_SCOPE, "site:warehouse-seattle-01",
        "electrical", 42_000, "USD", "Inspect and repair loading-bay lighting",
    )
    challenge = receiver.issue_challenge(
        action, expected_agent_id=authority.agent_id, caller_id="caller-a"
    ).grant
    assert challenge is not None
    bundle = authority.present(
        challenge=challenge.challenge,
        session_context=challenge.session_context,
        now=now,
    )
    return now, authority, receiver, action, encode_proof_bundle(bundle)


def headers(reference):
    return [(b"x-ratify-proof-reference", reference.encode())]


def test_uploaded_proof_reference_is_single_use_and_caller_bound():
    now, _, receiver, action, wire = fixture()
    registry = PresentationRegistry(clock=lambda: now)
    reference = registry.register(caller_id="caller-a", action=action, proof_wire=wire)
    with pytest.raises(CarrierDenied, match="DENY_CALLER_MISMATCH"):
        registry.consume(raw_headers=headers(reference), caller_id="caller-b", action=action)
    bundle = registry.consume(
        raw_headers=headers(reference), caller_id="caller-a", action=action
    )
    assert receiver.execute(action, bundle, caller_id="caller-a")["decision"] == "ALLOW"
    with pytest.raises(CarrierDenied, match="DENY_REPLAY"):
        registry.consume(raw_headers=headers(reference), caller_id="caller-a", action=action)


def test_concurrent_reference_consumption_invokes_handler_once():
    now, _, receiver, action, wire = fixture()
    registry = PresentationRegistry(clock=lambda: now)
    reference = registry.register(caller_id="caller-a", action=action, proof_wire=wire)

    def attempt():
        try:
            bundle = registry.consume(
                raw_headers=headers(reference), caller_id="caller-a", action=action
            )
            return receiver.execute(action, bundle, caller_id="caller-a")["decision"]
        except CarrierDenied as error:
            return error.reason

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda _: attempt(), range(32)))

    assert results.count("ALLOW") == 1
    assert receiver.handler_invocations == 1


def test_duplicate_and_oversized_carriers_fail_before_consumption():
    now, _, _, action, wire = fixture()
    registry = PresentationRegistry(clock=lambda: now)
    reference = registry.register(caller_id="caller-a", action=action, proof_wire=wire)
    duplicate = headers(reference) + headers(reference)
    with pytest.raises(CarrierDenied, match="DENY_AMBIGUOUS_INPUT"):
        registry.consume(raw_headers=duplicate, caller_id="caller-a", action=action)
    oversized = [(b"x-ratify-proof-reference", b"a" * 129)]
    with pytest.raises(CarrierDenied, match="DENY_OVERSIZED_INPUT"):
        registry.consume(raw_headers=oversized, caller_id="caller-a", action=action)
    registry.consume(raw_headers=headers(reference), caller_id="caller-a", action=action)


def test_oversized_proof_body_fails_before_decode():
    now, _, _, action, _ = fixture()
    registry = PresentationRegistry(clock=lambda: now)
    with pytest.raises(CarrierDenied, match="DENY_OVERSIZED_INPUT"):
        registry.register(
            caller_id="caller-a",
            action=action,
            proof_wire=b"{" + b"x" * MAX_PROOF_BUNDLE_BYTES,
        )


def test_transport_auth_rejects_missing_duplicate_and_wrong_tokens():
    auth = CallerAuthenticator({"secret-a": "caller-a"})
    assert auth.authenticate([(b"authorization", b"Bearer secret-a")]) == "caller-a"
    for raw in (
        [],
        [(b"authorization", b"Bearer wrong")],
        [(b"authorization", b"Bearer secret-a"), (b"authorization", b"Bearer secret-a")],
    ):
        with pytest.raises(CarrierDenied):
            auth.authenticate(raw)

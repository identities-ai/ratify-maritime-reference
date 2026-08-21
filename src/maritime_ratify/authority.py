"""Principal-controlled issuance and agent-side presentation fixture."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid

from ratify_protocol import (
    Constraint,
    DelegationCert,
    HybridPrivateKey,
    HybridPublicKey,
    HybridSignature,
    PROTOCOL_VERSION,
    ProofBundle,
    generate_agent,
    generate_human_root,
    issue_delegation,
    sign_challenge,
)

from .profile import (
    AUDIENCE_CONSTRAINT,
    CATEGORY_CONSTRAINT,
    DEFAULT_CATEGORY,
    DEFAULT_CURRENCY,
    DEFAULT_MAX_AMOUNT_MINOR,
    DEFAULT_RESOURCE,
    VERIFIER_ID,
    WORK_ORDER_SCOPE,
)


@dataclass(frozen=True)
class AuthorityFixture:
    root_id: str
    root_public_key: HybridPublicKey
    agent_id: str
    agent_private_key: HybridPrivateKey = field(repr=False)
    delegation: DelegationCert

    def present(self, *, challenge: bytes, session_context: bytes, now: int) -> ProofBundle:
        return ProofBundle(
            agent_id=self.agent_id,
            agent_pub_key=self.delegation.subject_pub_key,
            delegations=[self.delegation],
            challenge=challenge,
            challenge_at=now,
            challenge_sig=sign_challenge(
                challenge, now, self.agent_private_key, session_context
            ),
            session_context=session_context,
        )


def issue_authority(
    *,
    now: int | None = None,
    expires_at: int | None = None,
    resource: str = DEFAULT_RESOURCE,
    category: str = DEFAULT_CATEGORY,
    max_amount_minor: int = DEFAULT_MAX_AMOUNT_MINOR,
    currency: str = DEFAULT_CURRENCY,
    audience: str = VERIFIER_ID,
) -> AuthorityFixture:
    issued_at = int(time.time()) if now is None else now
    expiry = issued_at + 3600 if expires_at is None else expires_at
    root, root_private = generate_human_root()
    agent, agent_private = generate_agent("Maritime Work Order Agent", "custom")
    delegation = DelegationCert(
        cert_id=f"maritime-{uuid.uuid4().hex}",
        version=PROTOCOL_VERSION,
        issuer_id=root.id,
        issuer_pub_key=root.public_key,
        subject_id=agent.id,
        subject_pub_key=agent.public_key,
        scope=[WORK_ORDER_SCOPE],
        constraints=[
            Constraint(type="resource_path", resource_id=resource),
            Constraint(
                type="max_amount",
                max_amount=max_amount_minor / 100,
                currency=currency,
            ),
            Constraint(type=CATEGORY_CONSTRAINT, params={"allowed": category}),
            Constraint(type=AUDIENCE_CONSTRAINT, params={"allowed": audience}),
        ],
        issued_at=issued_at,
        expires_at=expiry,
        signature=HybridSignature(ed25519=b"", ml_dsa_65=b""),
    )
    issue_delegation(delegation, root_private)
    return AuthorityFixture(
        root.id, root.public_key, agent.id, agent_private, delegation
    )

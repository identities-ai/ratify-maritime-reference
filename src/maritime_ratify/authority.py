"""Principal-controlled issuance and agent-side presentation fixture."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid

from ratify_protocol_v2 import (
    HybridPrivateKey,
    HybridPublicKey,
    generate_keypair_v2,
    key_id_v2,
)
from ratify_protocol_v2 import background
from ratify_protocol_v2.signing_v2 import (
    _new_local_signer_v2,
    _sign_v2_statement_with_signer,
    public_key_of,
)
from ratify_protocol_v2.constraints_v2 import (
    CONSTRAINT_MAX_AMOUNT,
    CONSTRAINT_RESOURCE_PATH,
    ConstraintV2,
)
from ratify_protocol_v2.protocol_v2 import (
    CHALLENGE_PRESENTATION,
    DelegationCertV2,
    ProofBundleV2,
    delegation_digest_v2,
    placeholder_signature_v2,
    presentation_signable_bytes_v2,
    sign_delegation_cert_v2,
    sign_proof_bundle_v2,
)

from .profile import (
    AUDIENCE_CONSTRAINT,
    CATEGORY_CONSTRAINT,
    CATEGORY_FIELD,
    DEFAULT_CATEGORY,
    DEFAULT_CURRENCY,
    DEFAULT_MAX_AMOUNT_MINOR,
    DEFAULT_RESOURCE,
    MICROS_PER_MINOR,
    RECEIVER_ID,
    WORK_ORDER_SCOPE,
)


@dataclass(frozen=True)
class AuthorityFixture:
    root_id: str
    root_public_key: HybridPublicKey
    agent_id: str
    agent_private_key: HybridPrivateKey = field(repr=False)
    delegation: DelegationCertV2

    def present(
        self,
        *,
        challenge: bytes,
        challenge_at: int,
        verifier_id: str,
        session_context: bytes,
    ) -> ProofBundleV2:
        """Sign one presentation over a receiver-issued challenge.

        ``challenge_at`` and ``verifier_id`` are copied from the receiver's
        challenge record; the agent never chooses them.
        """
        return sign_proof_bundle_v2(
            ProofBundleV2(
                version=2,
                verifier_id=verifier_id,
                agent_id=self.agent_id,
                agent_pub_key=self.delegation.subject_pub_key,
                delegations=[self.delegation],
                delegation_digest=b"",  # computed by sign_proof_bundle_v2
                challenge=challenge,
                challenge_at=challenge_at,
                challenge_sig=None,
                session_context=session_context,
            ),
            self.agent_private_key,
        )



@dataclass(frozen=True)
class MisheldAuthority(AuthorityFixture):
    """Adversarial fixture: a certificate held by a key that does not own it.

    The SDK refuses to sign a presentation whose declared agent is not the
    signing key, so an honest client cannot produce one. An attacker can: it
    signs as itself and then declares ``agent_id``. When ``agent_id`` names
    the certificate's subject, the bundle carries that subject's public key
    and the holder's signature, which cannot verify. When it names the
    holder, the leaf certificate's subject is someone else. The receiver must
    refuse both.
    """

    def present(
        self,
        *,
        challenge: bytes,
        challenge_at: int,
        verifier_id: str,
        session_context: bytes,
    ) -> ProofBundleV2:
        holder = public_key_of(self.agent_private_key)
        declared = (
            self.delegation.subject_pub_key
            if self.agent_id == self.delegation.subject_id
            else holder
        )
        bundle = ProofBundleV2(
            version=2,
            verifier_id=verifier_id,
            agent_id=self.agent_id,
            agent_pub_key=declared,
            delegations=[self.delegation],
            delegation_digest=delegation_digest_v2([self.delegation]),
            challenge=challenge,
            challenge_at=challenge_at,
            challenge_sig=placeholder_signature_v2(),
            session_context=session_context,
        )
        # Deliberately below the SDK's public signing API, which refuses to
        # produce this presentation: sign the exact presentation bytes with
        # the holder's key, whatever the bundle declares.
        bundle.challenge_sig = _sign_v2_statement_with_signer(
            background(), CHALLENGE_PRESENTATION,
            presentation_signable_bytes_v2(bundle),
            _new_local_signer_v2(self.agent_private_key), holder,
        )
        return bundle


def issue_authority(
    *,
    now: int | None = None,
    expires_at: int | None = None,
    resource: str = DEFAULT_RESOURCE,
    category: str = DEFAULT_CATEGORY,
    max_amount_minor: int = DEFAULT_MAX_AMOUNT_MINOR,
    currency: str = DEFAULT_CURRENCY,
    audience: str = RECEIVER_ID,
    root: tuple[HybridPublicKey, HybridPrivateKey] | None = None,
) -> AuthorityFixture:
    issued_at = int(time.time()) if now is None else now
    expiry = issued_at + 3600 if expires_at is None else expires_at
    root_public, root_private = root or generate_keypair_v2()
    agent_public, agent_private = generate_keypair_v2()
    delegation = issue_bounded_delegation(
        root_public_key=root_public,
        root_private_key=root_private,
        agent_public_key=agent_public,
        issued_at=issued_at,
        expires_at=expiry,
        resource=resource,
        category=category,
        max_amount_minor=max_amount_minor,
        currency=currency,
        audience=audience,
    )
    return AuthorityFixture(
        key_id_v2(root_public), root_public, key_id_v2(agent_public),
        agent_private, delegation,
    )


def bounded_constraints(
    *,
    resource: str = DEFAULT_RESOURCE,
    category: str = DEFAULT_CATEGORY,
    max_amount_minor: int = DEFAULT_MAX_AMOUNT_MINOR,
    currency: str = DEFAULT_CURRENCY,
    audience: str = RECEIVER_ID,
) -> list[ConstraintV2]:
    """The four signed bounds every Maritime work-order delegation carries."""
    return [
        ConstraintV2(type=CONSTRAINT_RESOURCE_PATH, resource_id=resource),
        ConstraintV2(
            type=CONSTRAINT_MAX_AMOUNT,
            currency=currency,
            max_amount_micros=max_amount_minor * MICROS_PER_MINOR,
        ),
        ConstraintV2(
            type=CATEGORY_CONSTRAINT,
            params={"allowed": [category], "field": CATEGORY_FIELD},
        ),
        ConstraintV2(
            type=AUDIENCE_CONSTRAINT,
            params={"exact_receiver_ids": [audience], "trust_domain_ids": []},
        ),
    ]


def issue_bounded_delegation(
    *,
    root_public_key: HybridPublicKey,
    root_private_key: HybridPrivateKey,
    agent_public_key: HybridPublicKey,
    issued_at: int,
    expires_at: int,
    resource: str = DEFAULT_RESOURCE,
    category: str = DEFAULT_CATEGORY,
    max_amount_minor: int = DEFAULT_MAX_AMOUNT_MINOR,
    currency: str = DEFAULT_CURRENCY,
    audience: str = RECEIVER_ID,
    constraints: list[ConstraintV2] | None = None,
) -> DelegationCertV2:
    return sign_delegation_cert_v2(
        DelegationCertV2(
            version=2,
            cert_id=f"maritime-{uuid.uuid4().hex}",
            issuer_id=key_id_v2(root_public_key),
            issuer_pub_key=root_public_key,
            subject_id=key_id_v2(agent_public_key),
            subject_pub_key=agent_public_key,
            scope=[WORK_ORDER_SCOPE],
            constraints=constraints if constraints is not None else bounded_constraints(
                resource=resource,
                category=category,
                max_amount_minor=max_amount_minor,
                currency=currency,
                audience=audience,
            ),
            issued_at=issued_at,
            expires_at=expires_at,
        ),
        root_private_key,
    )

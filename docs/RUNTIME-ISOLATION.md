# Runtime isolation

The adversarial gate asks whether one agent can exceed its own authority. This
asks a different question, and it is the one a platform reader asks first:

> When two separately delegated agents run on the same platform and talk to the
> same receiver, does each agent's authority stay bound to its own runtime?

Isolation answers where an agent's code runs. It does not answer what that
agent may do to systems outside its sandbox. This is the demonstration that the
second boundary exists.

## The deployment

Two Maritime runtimes run **the same agent image digest**. They differ only by
injected authority, so a reader can confirm that nothing in the code separates
them.

| | Runtime A | Runtime B |
|---|---|---|
| Site | Seattle warehouse 01 | Portland warehouse 01 |
| Ceiling | $500.00 | $200.00 |
| Subject | its own | its own |
| Transport credential | its own | its own |
| Private key | its own, and never the other's | its own, and never the other's |

Runtime B holds runtime A's delegation, which is public, and never A's private
key. Both borrow attempts are therefore what an operator of runtime B could
actually mount, rather than staged failures.

The receiver serves both. Its local policy states its own capability, the sites
it manages and an absolute ceiling, and never one agent's bounds. Each
authenticated caller maps to exactly one expected subject, so authentication
identifies a caller without letting that caller choose which agent it presents
as.

## The five attempts

| Attempt | Required result | Deciding layer |
|---|---|---|
| A with its own authority | `ALLOW` | `ratify_verification` |
| B with its own authority | `ALLOW` | `ratify_verification` |
| B requesting A's site | `DENY_RESOURCE_MISMATCH` | `ratify_verification` |
| B declaring A's subject | `DENY_SUBJECT_MISMATCH` | `receiver_precheck` |
| B declaring its own subject with A's certificate | `DENY_VERIFICATION_FAILED` | `ratify_verification` |

The third attempt is the one that carries the claim. Runtime B is a
legitimately deployed, fully authorized agent, and it is stopped on its own
bounds rather than on its identity. That is what separates agent authority from
receiver capability.

The last two cover both shapes of borrowing. Declaring the peer's subject is
refused by the receiver's own bookkeeping, because the challenge was issued for
this runtime's subject. Declaring its own subject while presenting the peer's
certificate passes that check and is refused by verification. Recording both
means the claim does not rest on a string comparison.

## Reproduce it

```bash
python3 scripts/reproduce_gate_locally.py
```

Docker and Python 3.10 or newer. It issues a fresh principal inside the
published agent image, starts one receiver and two agent containers from the
published digests on a private network, and reproduces the nine enumerated
scenarios followed by these five attempts. No Ratify credential is involved and
the live deployment is never contacted.

`docs/runtime-isolation-expectations.json` is the contract. The local
two-runtime test, this reproduction, and the deployed gate all read it.

## Executed evidence

`evidence/runtime-isolation-results.json` records a run against the deployment.
Its `disclosures` object states what it does not establish: runtime identifiers
are operator-supplied and unattested by Maritime, the handler count is
receiver-wide, and the checksum is an integrity check rather than a signature.

Both runtimes and the receiver are operated by Ratify. This demonstrates
isolation between separately delegated agents, not between organizations.

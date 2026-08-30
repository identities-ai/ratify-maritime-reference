# Inspectable adversarial results

The Maritime pilot is complete only when the public deployment executes one
permitted request and eight distinct denied requests. The result is recorded as
JSON in `evidence/adversarial-results.json`; it is not inferred from unit tests
or prefilled by the console.

| Scenario | Required result | Deciding layer | Protected handler for denied request |
|---|---|---|---|
| Within authority | `ALLOW` | `ratify_verification` | Invoked |
| Exceeded limit | `DENY_LIMIT_EXCEEDED` | `ratify_verification` | Not invoked |
| Wrong resource | `DENY_RESOURCE_MISMATCH` | `ratify_verification` | Not invoked |
| Altered operation | `DENY_OPERATION_MISMATCH` | `proof_carrier` | Not invoked |
| Expired delegation | `DENY_EXPIRED` | `ratify_verification` | Not invoked |
| Revoked delegation | `DENY_REVOKED` | `ratify_verification` | Not invoked |
| Replayed proof | `DENY_REPLAY` | `proof_carrier` | Not invoked for the replay attempt |
| Wrong agent | `DENY_SUBJECT_MISMATCH` | `receiver_precheck` | Not invoked |
| Copied certificate | `DENY_VERIFICATION_FAILED` | `ratify_verification` | Not invoked |

Every result records `decided_by` and, where verification was reached, the
Ratify `verification_status`. The deciding layer is part of the required
result, so a scenario that starts being denied somewhere else fails the gate.

Six of the nine decisions are reached by Ratify verification: the allow case
returns `authorized_agent`, the exceeded-limit and wrong-resource cases return
`constraint_denied`, the expired and revoked cases return their matching
statuses, and the copied-certificate case returns `invalid`. Those six
demonstrate the signed delegation, its constraints, and its binding to one
agent key.

The remaining three demonstrate receiver-owned binding rather than the
protocol. The altered-operation and replay cases are stopped by the
proof-carrier registry, which binds one opaque reference to one authenticated
caller and one exact canonical action and consumes it once. The wrong-agent
case is stopped by the receiver's subject precheck, which compares the
presented agent against the agent the challenge was issued for, before
verification runs. The bundle would also fail verification, but the recorded
denial comes from the precheck, and this table says so rather than implying a
cryptographic result the run did not reach.

The copied-certificate case exists for that reason. It presents the genuine
delegation, correctly signed by the principal and naming the authorized agent,
while signing the challenge with a key the presenter does not own. The subject
precheck passes because the bundle names the right agent, so Ratify
verification is the layer that rejects it. That is the recorded demonstration
that holding a copy of the certificate is not the same as holding the
authority.

The replay case deliberately performs one valid setup use and then resubmits
the consumed proof reference. The recorded result describes the second,
rejected attempt. This is why the receiver-wide counter can increase during
that scenario even though `handler_invoked` is false for the replay itself.

The wrong-agent case uses an inspectable negative-test key embedded with its
fixture certificate. That certificate is validly signed by the demo principal
but names a subject the receiver does not accept. The authorized agent key is
separate and remains a Maritime secret.

Run and record the public gate from a clean checkout:

```bash
uv run --python 3.12 python scripts/run_live_adversarial_gate.py \
  evidence/adversarial-results.json \
  --agent-source-revision AGENT_COMMIT \
  --agent-image AGENT_IMAGE_DIGEST \
  --receiver-source-revision RECEIVER_COMMIT \
  --receiver-image RECEIVER_IMAGE_DIGEST \
  --worker-version WORKER_VERSION
```

The runner sends the same closed scenario identifiers accepted by the public
console. It fails unless every decision, stable reason code, and per-request
handler fact matches the table. The artifact also records the execution time,
endpoint, repository revision, redacted execution facts, and SHA-256 of the
canonical evidence object. It also records the exact agent and receiver source
revisions, immutable image digests, and scenario Worker version supplied by the
operator. It never records credentials, proofs, private keys, or unredacted
certificates.

Recompute the recorded evidence hash:

```bash
uv run --python 3.12 python -c 'import hashlib,json,pathlib; p=json.loads(pathlib.Path("evidence/adversarial-results.json").read_text()); h=p.pop("evidence_sha256"); print(hashlib.sha256(json.dumps(p,sort_keys=True,separators=(",",":")).encode()).hexdigest(), h)'
```

The two printed values must match.

## Reproduce it without trusting us

The artifact is a record of what the deployment did. It is not the check a
skeptical reader should rely on, because every field in it is reported by
infrastructure Ratify operates. The check is this one, which needs only Docker
and Python 3.10 or newer:

```bash
python3 scripts/reproduce_gate_locally.py
```

It resolves both published image digests against the public registry, issues a
fresh principal inside the published agent image, runs both images on a private
Docker network, executes every enumerated scenario, and compares each result
against `docs/gate-expectations.json`. No Ratify credential is involved, the
live deployment is never contacted, and the deployment's own delegation is
replaced by the principal issued on the reader's machine seconds earlier.

Pass `--agent-image` and `--receiver-image` to reproduce a different published
pair. If the Docker engine does not share the default workspace directory, pass
`--workspace-root` with a path it does share.

`docs/gate-expectations.json` is the single contract. The live gate, this local
reproduction, and the in-repository full-gate test all read it, so the required
result and its deciding layer cannot drift between them.

## What the artifact does not establish

The recorded `disclosures` object states these limits in the artifact itself.

- The checksum covers this file's canonical form. It is an integrity check
  against accidental edit, not a signature and not evidence that the recorded
  execution took place. Recomputing it proves only internal consistency.
- The runner resolves each image digest against the public registry and fails
  if the recorded `org.opencontainers.image.revision` label disagrees with the
  supplied source revision, so that pairing is observed. Which digests the live
  deployment actually runs, and the Worker version, remain operator-supplied.
  Maritime has attested none of it.
- The handler count is reported by the same receiver process that reaches the
  authorization decision. There is no independent read path for it, and it
  resets when the runtime restarts.
- Every recorded field is reported by infrastructure that Ratify operates. A
  reader who does not already trust that infrastructure cannot distinguish this
  artifact from a generated one. Reproducing the gate from source is the
  intended check, not the artifact.

The artifact records observed deployment behavior at its timestamp. It is not a
permanent availability guarantee and not a Maritime endorsement.

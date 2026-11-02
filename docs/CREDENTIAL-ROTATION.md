# Rotating deployment authority

Rotation is a write and a restart. It does not rebuild or republish an image.

> **Protocol v2 (this branch, 2026-10-22).** Issuance and renewal now produce
> Protocol v2 material: 64-hex key IDs, an exact `custom:work_order:create`
> scope, integer `max_amount_micros`, the `allowed_values/v1` category bound,
> a `ratify.receiver_audience` constraint, and a revocation list signed by a
> status key that is separate from the root key. The steps below are the v2
> runbook. They have not been run against the live deployment: the deployed
> runtimes still run the Protocol v1 images recorded in `evidence/`, no v2
> image has been published, and the image digests named in earlier runbooks
> are the v1 builds. Do not rotate v2 material onto the v1 runtimes; a v1
> runtime cannot read it.

That was not always true. The hybrid delegation is about 10 KB and the
adversarial fixture about 37 KB, and Maritime delivers the environment through
a metadata channel with a 50 KB ceiling for the whole environment, so the two
together did not fit. Both were baked into the reviewed image instead, which
made a seven day credential rotation cost an image rebuild and a redeploy of
every runtime. Maritime's file API removes that: it writes into the agent's
persistent volume, which survives a redeploy.

## Where the material lives

| | |
|---|---|
| Delegation and fixture | `/data/ratify/` on each runtime's persistent volume |
| Expected digests | `RATIFY_DELEGATION_SHA256`, `RATIFY_SCENARIO_AUTHORITIES_SHA256` |
| Signed status list | `/data/ratify/status-list.json` on the receiver's volume |
| Root and status public keys, agent public keys | receiver environment |
| Receiver verifier signing key | receiver environment only |
| Agent signing key, transport credentials | environment only, never on a volume and never in an image |
| Principal root key and status key | outside the deployment entirely |
| Status publisher state | `status-state.json` beside `principal.json`, outside the deployment |
| Image | carries no authority material at all |

### What the volume holds, precisely

The first runtime's fixture contains `wrong_agent_fixture_private_key`. That is
a signing key, and it is on the volume on purpose. It exists so the wrong-agent
case is inspectable rather than asserted, its certificate names a subject no
configured caller maps to, and the deployed wrong-agent and copied-certificate
scenarios are both denied. It was equally readable when the fixture was baked
into the published image, so moving it to the volume changed nothing about who
can see it.

The second runtime's fixture holds only the first runtime's public certificate,
with no key of any kind, because the cross-runtime cases need a certificate to
present rather than a key to sign with.

So the volume is not free of private bytes. It is free of anything that can
authorize an action: no agent's own signing key, no transport credential, no
root key.

The runtime reads each artifact once and hashes the bytes it read. A mismatch
stops startup. The digest lives in the environment and the artifact lives on
the volume, so writing the volume alone cannot change what the runtime accepts.

## Rotating

Run issuance on the principal machine, outside this repository:

```bash
uv run --python 3.12 python scripts/issue_demo_authority.py renew \
  /secure/path/principal.json /secure/path/renewal
```

For each runtime, write the two artifacts and set the two digests:

```bash
python3 scripts/rotate_deployment_authority.py /secure/path/renewal \
  --image <agent image digest> \
  --token-file ~/.ratify/maritime-file-api.token
```

`--image` is required. Both agent runtimes run the same agent image, and the
digest is the one the runtimes are already on, because rotation restarts them
rather than moving them to a new build. Maritime reports a derived tag rather
than the source digest, so take it from the recorded current deployment and
confirm it against the registry before running this.

It writes through the file API, sets the expected digests, restarts each
runtime, and refuses to continue if a written file does not read back with the
digest it just configured.

Then write the new signed status list to the receiver's volume. Renewal
issues a fresh revoked fixture certificate and adds it to the cumulative list,
signed by the status key, at the next sequence. The receiver reads the file on
every verification, so no restart is needed. `rotate_deployment_authority.py`
writes the agent runtimes only; extending it to the receiver's volume is open
work, so this write is a manual file API call today.

There is no `RATIFY_REVOKED_CERT_IDS` in v2. The receiver evaluates only a
list signed by the configured status key, and refuses to start if the old
setting is present, so an operator cannot believe a revocation is configured
when it is not.

### Daily status signing

A signed list is current for at most one day, which is the Protocol v2
maximum. After `next_update`, or one day after `updated_at`, the receiver can
no longer treat status as current, and every scenario, including allow, is
refused with `DENY_VERIFIER_UNAVAILABLE`. The principal therefore re-signs the
same revoked set at least daily:

```bash
uv run --python 3.12 python scripts/issue_demo_authority.py status \
  /secure/path/principal.json /secure/path/status-<date>
```

and writes the resulting `status-list.json` to the receiver's volume. This
advances the publisher state in `status-state.json` beside `principal.json`,
which must persist between signings. The seven-day delegation renewal is
unchanged; the daily status signing is new in v2 and is the step that most
needs automation before a v2 deployment.

Finally, verify against the deployment rather than a local stand-in:

```bash
python3 scripts/run_acceptance_gate.py --live-gates
```

On this branch that command refuses to run, because the recorded deployment
is Protocol v1 and a live v1 result says nothing about v2 code. It applies
again once a v2 deployment exists and its evidence is recorded.

Skipping the status write is still a silent failure for up to a day. The
previous list stays current until it expires and does not name the new
revoked certificate, so the revoked scenario returns ALLOW. After that the
list is stale and every request is refused. A missing or unsigned list
refuses every request at once. The live gates are what catch the silent case,
because the test suite and the local reproduction both issue fresh material
against a receiver they configure themselves.

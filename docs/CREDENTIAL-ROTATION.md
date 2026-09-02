# Rotating deployment authority

Rotation is a write and a restart. It does not rebuild or republish an image.

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
| Agent signing key, transport credentials | environment only, never on a volume and never in an image |
| Principal root key | outside the deployment entirely |
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
  --token-file ~/.ratify/maritime-file-api.token
```

It writes through the file API, sets the expected digests, restarts each
runtime, and refuses to continue if a written file does not read back with the
digest it just configured.

Then update the receiver's revocation list and restart it, because renewal
issues a fresh revoked fixture certificate with a new id:

```bash
maritime env set ratify-maritime-receiver \
  RATIFY_REVOKED_CERT_IDS=<revoked_cert_id from the new manifest>
maritime deploy ratify-maritime-receiver --source docker --image <receiver digest>
```

Finally, verify against the deployment rather than a local stand-in:

```bash
python3 scripts/run_acceptance_gate.py --live-gates
```

Skipping the revocation step fails silently: the revoked scenario returns ALLOW
and nothing else looks wrong. The live gates are what catch it, because the
test suite and the local reproduction both issue fresh material against a
receiver they configure themselves.

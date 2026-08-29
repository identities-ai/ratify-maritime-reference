# Inspectable adversarial results

The Maritime pilot is complete only when the public deployment executes one
permitted request and seven distinct denied requests. The result is recorded as
JSON in `evidence/adversarial-results.json`; it is not inferred from unit tests
or prefilled by the console.

| Scenario | Required result | Protected handler for denied request |
|---|---|---|
| Within authority | `ALLOW` | Invoked |
| Exceeded limit | `DENY_LIMIT_EXCEEDED` | Not invoked |
| Wrong resource | `DENY_RESOURCE_MISMATCH` | Not invoked |
| Altered operation | `DENY_OPERATION_MISMATCH` | Not invoked |
| Expired delegation | `DENY_EXPIRED` | Not invoked |
| Revoked delegation | `DENY_REVOKED` | Not invoked |
| Replayed proof | `DENY_REPLAY` | Not invoked for the replay attempt |
| Wrong agent | `DENY_SUBJECT_MISMATCH` | Not invoked |

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
  evidence/adversarial-results.json
```

The runner sends the same closed scenario identifiers accepted by the public
console. It fails unless every decision, stable reason code, and per-request
handler fact matches the table. The artifact also records the execution time,
endpoint, repository revision, redacted execution facts, and SHA-256 of the
canonical evidence object. It never records credentials, proofs, private keys,
or unredacted certificates.

Recompute the recorded evidence hash:

```bash
uv run --python 3.12 python -c 'import hashlib,json,pathlib; p=json.loads(pathlib.Path("evidence/adversarial-results.json").read_text()); h=p.pop("evidence_sha256"); print(hashlib.sha256(json.dumps(p,sort_keys=True,separators=(",",":")).encode()).hexdigest(), h)'
```

The two printed values must match. The artifact proves the observed deployment
behavior at its timestamp; it is not a permanent availability guarantee or a
Maritime endorsement.

# Ratify Maritime Reference

This is the complete implementation repository for the live
[Maritime × Ratify lab](https://labs.ratifyprotocol.com/maritime). It is not the
shared Labs catalog or a marketing-only demonstration.

The repository contains the LangChain agent, separately deployed MCP receiver,
Ratify authorization boundary, deployment images, issuance tooling, Cloudflare
scenario proxy, public console, adversarial tests, threat model, and deployment
evidence. The separate
[`identities-ai/ratify-labs`](https://github.com/identities-ai/ratify-labs)
repository contains only the shared catalog and closed routing layer.

This open reference implements an authority-aware LangChain agent running on
Maritime and acting through a public MCP tool interceptor against a separately
deployed Ratify-verifying Streamable HTTP MCP receiver.

![Maritime and Ratify Phase 1 authority flow](docs/architecture/phase-1-flow.svg)

The frozen Stage 2 receiver core and Stage 3 transport boundary cover exact
allow, signed bounds, receiver-owned time and policy, revocation, replay,
hostile concurrency, caller ownership, strict proof upload, duplicate and
oversized carriers, real Streamable HTTP MCP discovery and execution, and the
LangChain authority-interceptor seam. Stage 4 now includes a deterministic
LangChain agent runtime and an optional production-model configuration; image
verification now passes locally and across two deployed Maritime runtimes; the
public demo console is live at `https://labs.ratifyprotocol.com/maritime`.

Licensed under Apache-2.0. This reference is not a Maritime endorsement or
partnership claim.

![Receiver-owned authorization decision pipeline](docs/architecture/receiver-decision-pipeline.svg)

Run the current gate:

```bash
uv sync --python 3.12
uv run --python 3.12 pytest
```

The gate requires no model API and includes a real TCP Streamable HTTP flow
through LangChain, the proof interceptor, and the receiver. It executes one
in-bounds allow plus exceeded-limit, wrong-resource, altered-operation,
expired, revoked, replayed, wrong-agent, and copied-certificate denials. See
[`docs/ADVERSARIAL-RESULTS.md`](docs/ADVERSARIAL-RESULTS.md) for the live result
contract and inspectable artifact procedure.

Run every check that has to pass before the pilot is called finished:

```bash
python3 scripts/run_acceptance_gate.py
```

It runs both test suites, the console build and lint, the proxy typecheck, the
deployed-asset check, and the local reproduction, then verifies that the
recorded evidence still carries its own limits and reproduces the deployment it
describes. `--offline` skips the checks that contact the deployment;
`--skip-reproduction` skips the slowest one; `--evidence-only` checks the
recorded evidence with no toolchain required.

After renewing deployment authority, or after any redeploy, add `--live-gates`:

```bash
python3 scripts/run_acceptance_gate.py --live-gates
```

That re-executes both deployed gates against the deployment the committed
evidence describes, using its recorded arguments. It is the check that catches
a renewal mistake, because the tests and the local reproduction both use fresh
material issued on the spot and would not notice one. Its results are written
under `.acceptance/` and never over the published artifacts.

To reproduce the same nine results from the published images, without a
repository install, a Ratify credential, or any call to the live deployment:

```bash
python3 scripts/reproduce_gate_locally.py
```

It needs only Docker and Python 3.10 or newer. It issues a fresh principal
inside the published agent image, runs one receiver and two agent containers
from the published digests on a private Docker network, and checks every result
against [`docs/gate-expectations.json`](docs/gate-expectations.json) and
[`docs/runtime-isolation-expectations.json`](docs/runtime-isolation-expectations.json),
the contracts that the deployed gates and the in-repository tests also read.

Two Maritime runtimes run the same agent image and differ only by injected
authority: one delegated Seattle at five hundred dollars, the other Portland at
two hundred. Neither holds the other's private key. See
[`docs/RUNTIME-ISOLATION.md`](docs/RUNTIME-ISOLATION.md) for what that
demonstrates and how to reproduce it.

## Maritime agent runtime

The root `Dockerfile` is the Maritime GitHub-source build for the agent. It
starts a long-lived server on the injected `PORT` and exposes:

- `GET /health`: side-effect-free readiness
- `POST /chat`: accepts only the nine enumerated adversarial-gate scenarios,
  requires `X-Ratify-Demo-Token: Bearer <RATIFY_DEMO_TOKEN>`, and applies an
  in-process request limit. The dedicated header avoids Maritime's
  platform-level use of `Authorization`.

The default `RATIFY_MODEL_MODE=deterministic` path exercises the real LangChain
and MCP integration without a paid model. Set `RATIFY_MODEL_MODE=production`
and an explicit `RATIFY_MODEL_ID` to use LangChain's OpenAI-compatible
integration. On Maritime, `OPENAI_API_KEY` and `OPENAI_BASE_URL` can be injected
for its metered model proxy; a provider credential is never used for Ratify
proof construction or receiver authorization.

Copy `.env.example` only as a key-name reference. Private agent material and
the receiver token must be configured through Maritime secrets, never committed
or built into an image. Configure `RATIFY_DEMO_TOKEN` as a separate secret for
the public console; it authenticates access to the demo but grants no Ratify
authority.

The agent and receiver images pin their Python base by digest, include only
runtime source, run as an unprivileged user, and expose container health checks.

## Deployment authority issuance

Create deployment material outside the repository on a trusted principal
machine. The command writes separate principal, receiver, agent, and
secret-free manifest artifacts with restrictive permissions:

```bash
uv run --python 3.12 python scripts/issue_demo_authority.py issue \
  /secure/path/maritime-demo-issuance
```

The delegation is valid for seven days. Renew it on day five without changing
the configured root or agent identity:

```bash
uv run --python 3.12 python scripts/issue_demo_authority.py renew \
  /secure/path/maritime-demo-issuance/principal.json \
  /secure/path/maritime-demo-renewal
```

The generated env files contain authority and credential material, not complete
runtime configuration. Import `receiver.env` only into the receiver, then add
`RATIFY_ALLOWED_HOSTS` after Maritime assigns its hostname. Import `agent.env`
only into the agent, then add the assigned receiver's
`RATIFY_RECEIVER_MCP_URL` and `RATIFY_PRESENTATION_URL`. Maritime injects
`PORT` into both runtimes. `principal.json` must remain outside Maritime and
must never be committed, uploaded, or copied into either runtime. The public,
signature-protected `delegation.json` is injected into the immutable agent
image during its reviewed CI build because Maritime's runtime configuration is
not suitable for the large hybrid certificate. Renewal emits a replacement
`delegation.json` and public manifest, followed by a new agent image.

## Proof carrier

The model-visible MCP tool contains only the six business arguments shown in
the architecture. The LangChain interceptor obtains a challenge, uploads the
canonical Ratify proof through a body capped before decoding, receives a short
single-use reference, and adds only that reference to the selected MCP call.
Agent-to-receiver requests authenticate with the dedicated
`X-Ratify-Caller-Token` header because the deployed Maritime gateway removes
the standard `Authorization` header from public runtime requests.

The current hybrid proof is approximately 18 KB. Carrying it directly in an
HTTP header would be brittle across ingress and proxy limits, so this reference
does not do that. The opaque reference is bound to authenticated caller,
Ratify subject, exact canonical action, and a short TTL, and is consumed once.

The frozen build requirements are in
[`docs/REFERENCE-REQUIREMENTS.md`](docs/REFERENCE-REQUIREMENTS.md).

## Demo scenario proxy

`apps/demo-console/worker/` contains the reviewed-before-deployment Cloudflare
Worker boundary for the static console. It accepts only the nine enumerated
scenarios, constructs the Maritime agent request server-side, projects the
response onto documented public fields, and keeps the demo credential in a
Cloudflare secret binding. An exact Durable Object limiter applies both a
per-client and a global budget across Worker instances. No browser credential,
general proxying, evidence API, user account, or analytics store is included.

Deployed pilot topology:

- `apps/agent/`: LangChain agent runtime built by the root `Dockerfile`
- `apps/receiver/`: Ratify-verifying MCP receiver in a second isolated runtime
- `apps/demo-console/`: reviewed proxy followed by a standalone Ratify-branded
  UI published through the shared Ratify Labs catalog at
  `labs.ratifyprotocol.com/maritime`

## Open reference and managed product boundary

This repository uses the open Ratify protocol and local reference components;
it does not require a Ratify service. Ratify Verify is a separate managed
product under development, intended to operationalize functions such as
issuance, trust administration, revocation, and audit without introducing a
proprietary proof format. It is not required to reproduce this pilot.

The pilot is deployed, but it is not yet complete under the frozen reference
criteria. Current-runtime log evidence, Maritime review, publication recording
and screenshots, and the required `ratify-protocol` discovery entries remain
open. The public UI therefore labels this a live pilot rather than a completed
or Maritime-reviewed reference.

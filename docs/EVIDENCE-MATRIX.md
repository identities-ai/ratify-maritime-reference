# Evidence matrix

Status: Stage 2 receiver core, Stage 3 local MCP boundary, and Stage 4 agent
runtime complete; Maritime deployment verified except for log-pipeline evidence.

| Requirement | Evidence | Status |
|---|---|---|
| ISS-004 | `uv.lock`; PyPI wheel hash recorded below | Complete |
| AGT-001..006 | LangChain builder, filtered MCP tools, fixed interceptor call | Partial |
| PRS-001..004 | Deterministic presentation, replay, expiry, and race tests | Complete |
| PRS-005 | Strict upload and carrier parsing tests | Complete |
| RCV-001..010 | Receiver-core regression gate, including hostile concurrency | Complete |
| T-001..T-012, T-015..T-016 | Receiver-core acceptance cases | Complete |
| T-013..T-014 | Duplicate and oversized MCP carrier cases | Complete |
| MCP-001..007 | Real Streamable HTTP flow, business-only schema, caller-bound reference | Complete locally |
| MAR-001..004, MAR-006 | Maritime deployment record below | Complete |
| MAR-005 | Current application-log inspection | Blocked by platform log pipeline |
| UI-001..013 | Executed console evidence and visual QA | Pending |
| PXY-001..024 | Demo proxy unit, type, bundle, hostile, and live gates | Deployed; closure retest pending |
| VAL-001..007 | Clean-checkout run and public-copy review | Pending |
| PUB-001..005 | Public repository and protocol index | Pending |

## Frozen Stage 2 checkpoint

- Date: 2026-08-17
- Interpreter requirement: CPython 3.12 or 3.13
- Ratify package: `ratify-protocol==1.0.0a16`
- PyPI wheel SHA-256: `bd7d5aad86020bc14face268566c6f2c5e97df88ca87bf8beb45587a0dd3780f`
- Command: `uv run --python 3.12 pytest`
- Result: 35 collected, 35 passed, 0 skipped, 0 xfailed, 0 failed, 0 errors
- Coverage: receiver-core cases only; MCP carrier and deployment cases remain pending
- Independent hostile closure review: 141 collected, 141 passed, 0 failed

## Frozen Stage 3 local checkpoint

- Closure date: 2026-08-20

- MCP SDK: `mcp==1.29.0`
- LangChain: `langchain==1.3.15`
- LangChain MCP adapters: `langchain-mcp-adapters==0.3.2`
- Command: `uv run --python 3.12 pytest -W error`
- Closure-review result: 46 collected, 46 passed, 0 skipped, 0 xfailed, 0
  failed, 0 errors
- Post-freeze permanent transport additions: 48 collected, 48 passed before
  Stage 4 agent-runtime work
- Proof carrier measurement: 17,966-byte canonical proof body; opaque reference header
- Coverage: real in-memory ASGI Streamable HTTP discovery, challenge, upload,
  single-use reference resolution, verification, and handler execution
- Receiver image: `rmr:closure`, independently built and exercised locally from
  `apps/receiver/Dockerfile` on 2026-08-20
- Container smoke: receiver service import succeeded with
  `ratify-protocol==1.0.0a16`, `pqcrypto==0.3.4`, and `uv==0.7.6`
- Hostile-review remediation: dependency failures return stable denials, the
  interceptor dispatches its validated action snapshot, and malformed root
  public-key lengths prevent startup; the threat model covers the implemented
  HTTP/MCP boundary and remaining deployment risks
- Permanent transport regressions: concurrent single-use consumption, Host
  protection, unauthenticated MCP denial, and duplicate-key upload rejection
- Independent closure review: 203 Stage 3 probes and 141 Stage 2 regressions
  passed; verdict `STAGE 3 PASSES`
- Deployment status: the image has not yet been started on Maritime; MAR-001..006
  remain pending

## Stage 4 agent-runtime checkpoint

- Date: 2026-08-21
- Command: `uv run --python 3.12 pytest -q -W error`
- Result after deployment-issuance remediation: 70 collected, 70 passed, 0 skipped,
  0 xfailed, 0 failed, 0 errors
- Current deployment gate after Maritime compatibility work: 72 collected,
  72 passed, 0 skipped, 0 xfailed, 0 failed, 0 errors
- Coverage: strict agent-authority loading, private-key/delegation match,
  deterministic and optional production model selection, and a real TCP
  LangChain-to-MCP allow and same-agent over-limit denial
- Agent image: `ratify-maritime-agent:predeploy`, built locally from the root
  `Dockerfile`
- Receiver image: `ratify-maritime-receiver:predeploy`, built locally from
  `apps/receiver/Dockerfile`
- Container smoke: agent runtime import succeeded with
  `ratify-protocol==1.0.0a16`, `pqcrypto==0.3.4`, and
  `langchain-openai==1.6.0`; patched image contains `starlette==1.6.0`
- Dependency security: GitHub Dependabot reported zero open alerts after
  security commit `a92bdc1`
- Production model status: configuration is implemented and unit-tested; no
  provider call has been executed or claimed
- Agent-boundary hardening: `.env` files are excluded from the build context;
  settings representations redact private authority and tokens; `/chat`
  requires a separate bearer credential and applies an in-process rate limit
- Secret-exclusion smoke: a sentinel `.env` was present in the checkout during
  the image build; `/app/.env` and the sentinel value were both absent from the
  resulting image
- Deployment-image hardening: both images pin the verified Python base digest,
  copy only runtime source, run as UID 10001, and define local health checks;
  agent startup rejects reuse of its demo and receiver transport tokens
- Image inspection: both image configurations select `appuser`; runtime probes
  report UID 10001; neither image contains `tests/`, `docs/`, `reviews/`, or the
  other runtime's entrypoint
- Issuance gate: offline principal, receiver, agent, and public-manifest
  artifacts are separated; private files use mode 0600; seven-day renewal keeps
  the same issuer, subject, and constraints while producing a valid new
  signature
- Principal-integrity gate: renewal rejects wrong-length keys, foreign issuer
  keys, changed identifiers, missing fields, and malformed encoding before
  creating any output
- Maritime deployment status: both runtimes active from immutable GHCR digests;
  live same-agent allow and over-limit denial verified below

## Maritime deployment record

- Date: 2026-08-23
- Receiver runtime: `73de1f04-5fe3-43d7-afc7-715206e9241e`
- Receiver source: `d85eebd9939410fb1d50c1114415916047b3384b`
- Receiver image: `ghcr.io/identities-ai/ratify-maritime-receiver@sha256:c3670aedef6d12f6ebad87441a2d76249164b4219c0be0bc88abfc37f84ba120`
- Agent runtime: `526e13bb-5a8c-47fc-94bf-96a0dc417983`
- Agent source: `f3e173ede141ada02144add553318ca7d73645a0`
- Agent image: `ghcr.io/identities-ai/ratify-maritime-agent@sha256:29b93714cd131a1cd4960631b46a64b80f52409d3503c7eff6629a14b2b6a42c`
- Delegation SHA-256: `6fad6d3d3c7c115be5067321dddf706dae94b97b360ccd7d0ae4e919a4ec3e52`
- Both health endpoints returned `{"status":"ok"}` after deployment and
  receiver replacement
- Live `over_limit`: `DENY_LIMIT_EXCEEDED`, `handler_invocations: 0`
- Live `allow`: `ALLOW`, `handler_invocations: 1`
- The zero count proves the earlier timed-out allow did not enter the handler
- Maritime's rewritten private `Host` is normalized only for `10.0.0.0/8` on
  proxy port 8080; other private ports and public IP hosts remain rejected
- All three temporary Maritime API keys report `is_active: false`
- MAR-005 remains open because Maritime returns stale logs from a superseded
  runtime generation instead of current application output

## Demo proxy implementation checkpoint

- Date: 2026-08-23
- Worker tests: 29 collected, 29 passed, 0 skipped, 0 failed
- TypeScript: `tsc --noEmit` passed
- Wrangler production dry run: 5.25 KiB upload, 1.79 KiB gzip
- Bindings: one Durable Object limiter, public agent URL, and public console
  origin; `RATIFY_DEMO_TOKEN` is configured separately with `wrangler secret`
- Closure hardening: duplicate scenario keys reject as ambiguous, expired
  client-address keys are deleted, and a missing token fails before limiting or
  agent contact
- Limits: five requests per attested client and twenty requests globally per
  minute, below the agent's thirty-per-minute fallback budget
- Scope: exact scenario trigger and response projection only; no evidence read
  endpoint, user store, analytics, or console UI
- Hostile review: all PXY-001..024 and TP-001..021 passed at `a375038`; three
  low findings were remediated at `218957b`
- Cloudflare identity: deployed by `chuks@identities.ai`
- Worker URL: `https://ratify-maritime-demo-proxy.chuks-04d.workers.dev`
- Worker version: `6240a107-b5e2-4169-aefe-b6426f4a77a0`
- Secret inspection exposes only the binding name and type `secret_text`
- Live invalid scenario: HTTP 400 `INVALID_REQUEST`
- Live over-limit: HTTP 200, `DENY_LIMIT_EXCEEDED`, shared handler count 1
- Live allow: HTTP 200, `ALLOW`, shared handler count 2
- Live four-request concurrent limiter check after two accepted calls: three
  HTTP 200 responses and one HTTP 429, exactly reaching the five-client budget
- Foreign-origin preflight: HTTP 403; all observed responses carried
  `Cache-Control: no-store`

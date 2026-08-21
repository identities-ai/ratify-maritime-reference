# Claude Stage 3 Hostile Review Prompt

Perform a hostile, evidence-driven security and correctness review of the
Maritime × Ratify Stage 3 local MCP boundary. Do not modify the repository.
Write all probes and temporary tests outside the repository. Record the exact
environment, commands, test counts, and package origins you actually observe.

Repository:

`products/ratify/repositories/ratify-maritime-reference`

Read these sources before testing:

- `docs/REFERENCE-REQUIREMENTS.md`
- `docs/THREAT-MODEL.md`
- `docs/EVIDENCE-MATRIX.md`
- `src/maritime_ratify/receiver.py`
- `src/maritime_ratify/transport.py`
- `src/maritime_ratify/service.py`
- `src/maritime_ratify/agent.py`
- `apps/receiver/Dockerfile`
- `apps/receiver/start.py`
- every file under `tests/`

## Review posture

Assume all HTTP headers, JSON bodies, MCP arguments, proof references, bearer
tokens, timing, concurrency, and caller-owned Python objects are hostile. Do not
accept test names, comments, README claims, counters, or in-memory ASGI success
as proof. Observe the protected handler boundary directly. A passing test that
asserts vulnerable behavior is a finding, not evidence of safety.

The model must not be part of the authorization boundary. Transport identity
must not substitute for Ratify authority, and Ratify authority must not
substitute for transport authentication.

Do not report speculative vulnerabilities without a reproduction. Separate:

1. authorization bypasses or secret disclosures;
2. fail-open, replay, caller-isolation, ambiguity, or handler-ordering defects;
3. stable-reason-code and public-claim defects;
4. Stage 3 deployment sizing or hardening notes that do not violate a frozen
   requirement.

## Establish the environment of record

From a clean local environment:

1. Remove or move aside `.venv`, pytest caches, and Python bytecode caches.
2. Run `uv sync --python 3.12` from the lockfile.
3. Run `uv run --python 3.12 pytest -q -W error`.
4. Record Python, uv, MCP, LangChain, LangChain MCP adapters,
   `ratify-protocol`, `pqcrypto`, cryptography, and pytest versions.
5. Prove the Ratify package comes from the published PyPI wheel rather than the
   workspace SDK. Reconfirm its wheel hash against `uv.lock` and the evidence
   matrix.
6. Inspect git status before and after review and prove the repository was not
   modified.

Then independently build:

`docker build --progress=plain -f apps/receiver/Dockerfile -t ratify-maritime-receiver:stage3 .`

Confirm the image imports the receiver service and contains exactly
`ratify-protocol==1.0.0a16`, `pqcrypto==0.3.4`, and `uv==0.7.6`. Treat local
network stalls as environment evidence, not an application vulnerability.

## Required hostile probes

### Transport authentication and carrier parsing

Attempt missing, empty, malformed, non-ASCII, oversized, differently cased,
whitespace-mutated, comma-folded, and duplicate Authorization and
`X-Ratify-Proof-Reference` headers. Test duplicates introduced as separate raw
headers and as proxy-style combined values. Confirm ambiguous security input is
rejected before reference consumption or MCP dispatch.

Probe bearer-token comparison, credential-map edge cases, duplicate configured
tokens or caller IDs, and accidental acceptance of alternate authorization
schemes. Confirm denial responses never echo tokens or internal exceptions.

### Presentation upload boundary

Attack `/presentations` with:

- bodies above the application limit, including misleading Content-Length;
- chunked bodies and bodies whose declared size differs from delivered size;
- invalid UTF-8, duplicate JSON keys at every nesting level, unknown fields,
  missing fields, non-object roots, extreme nesting, and trailing data;
- proof strings at and around both configured size limits;
- bools, floats, huge integers, Unicode normalization differences, and mutated
  action fields;
- valid proofs uploaded under another authenticated caller;
- concurrent registration at capacity and clock or TTL boundaries.

Determine whether Starlette buffers an oversized body before the application
checks its length. If so, classify this accurately as a resource-exhaustion or
deployment-boundary issue unless it creates a frozen-requirement violation.

### Reference lifecycle and caller isolation

Try sequential and concurrent consumption of one reference with 2, 8, 32, and
64 workers. Instrument the real handler entry. Require at most one successful
handler invocation.

Attempt cross-caller theft, cross-action substitution, cross-request eviction,
expired-reference reuse, guessed references, missing references, and reuse after
every denial path. Test whether an attacker can burn or evict another caller's
pending challenge or presentation reference. Distinguish intentionally retained
anti-ambiguity state from unbounded or attacker-controlled lockout.

Mutate caller-owned `WorkOrder`, `ProofBundle`, delegation, header, and mapping
objects during registration and consumption. Look for check-then-copy or
check-then-use windows. A reported race must show the hostile value at actual
handler entry or show concrete victim-state eviction.

### MCP service boundary

Exercise a real Streamable HTTP MCP client against the ASGI app and, if the
container can be configured safely with generated test-only keys, against the
running container over a bound local port.

Verify:

- discovery exposes the intended tools and business-only schemas;
- proof bytes, references, credentials, keys, and hidden security parameters do
  not enter the model-visible schema;
- only `create_work_order` can consume a proof reference;
- health output discloses no authority or identity material;
- malformed authentication and carrier failures return stable closed reason
  codes without uncaught exceptions;
- Host and origin protections behave correctly for allowed, missing, malformed,
  forwarded, IPv6, and attacker-controlled host values;
- MCP path normalization, redirects, alternate methods, and content types do not
  bypass authentication or dispatch rules.

Directly test whether challenge issuance and MCP calls can be abused without
rate limiting to exhaust the 128-entry registries. Classify observed bounded
availability risk separately from authorization.

### Agent interceptor

Use hostile `MCPToolCallRequest` fixtures and a deterministic fake model. Try:

- extra, missing, duplicated, mutated, or wrongly typed business arguments;
- a different tool name, server name, or receiver connection;
- preexisting Authorization and proof-reference headers;
- case variants of security header names;
- mutation while awaiting the challenge provider or uploader;
- malformed challenge responses, oversized base64, invalid session context,
  upload failures, non-JSON failures, redirects, and timeouts;
- attempts to make challenge arguments differ from uploaded or dispatched
  arguments;
- attempts to expose proof material through exceptions, tracing, request
  objects, model messages, or tool schemas.

Confirm the challenge, proof, upload, and dispatched MCP call all bind one exact
receiver-owned action snapshot. Confirm a receiver denial cannot be transformed
into success or retried as a different action by the agent layer.

### Container and configuration

Inspect the image history and build context for secrets, local virtual
environments, caches, review scratch files, and unintended workspace content.
Verify required settings fail loudly without printing values. Probe malformed
base64 keys, empty allowed-host entries, missing PORT, invalid ports, duplicate
credentials, and unsafe log levels.

Determine whether the image runs as root, whether that violates a frozen
requirement, and whether the Dockerfile is reproducible enough for MAR-001 and
MAR-006. Do not mark MAR-001..006 complete merely because the receiver image
builds; the agent image and actual Maritime deployment do not yet exist.

## Evidence and public-copy audit

Check every implemented, tested, pending, and deployed claim in the README,
threat model, requirements, architecture diagrams, and evidence matrix against
what you reproduced. In particular, challenge:

- `MCP-001..007 | Complete locally`;
- `AGT-001..006 | Partial`;
- the exact 43-test count;
- the Docker build and container smoke evidence;
- statements that imply Maritime deployment, a complete LangChain runtime,
  production readiness, endorsement, or an independently operated receiver.

## Required report format

Return:

1. environment and exact test counts;
2. a finding table with severity, requirement, location, reproduction, expected
   and observed results, handler delta, and smallest safe correction;
3. attacks that survived falsification;
4. missing or weak permanent tests;
5. incorrect or overstated evidence and public copy;
6. container and reproducibility findings;
7. later-stage deployment or sizing notes that are not blockers;
8. locations and commands for all temporary tests;
9. one verdict: `STAGE 3 PASSES` or `STAGE 3 NEEDS CHANGES`.

The stage must not pass if any demonstrated authorization bypass, cross-caller
state attack violating MCP-007, secret disclosure, fail-open verification,
multiple handler invocation from one presentation, proof exposure to the model,
or material frozen-requirement contradiction remains.

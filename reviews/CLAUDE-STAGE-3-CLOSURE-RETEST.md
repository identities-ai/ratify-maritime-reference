# Claude Stage 3 Closure Retest Prompt

Retest the Maritime × Ratify Stage 3 remediation. Do not modify the repository.
Keep all probes outside it and verify source checksums and git status are
unchanged after review.

The prior hostile review reported F-1 through F-4 and returned
`STAGE 3 NEEDS CHANGES`. Determine whether each finding is genuinely closed
through the public boundary. Do not accept source inspection or test names as
proof.

## Environment and regression gates

From a clean Python 3.12 environment:

1. Run `uv sync --python 3.12`.
2. Run `uv run --python 3.12 pytest -q -W error`.
3. Record exact totals, including skips, xfails, warnings, failures, and errors.
4. Reconfirm `ratify-protocol==1.0.0a16`, `pqcrypto==0.3.4`, package origin,
   and the published wheel hash.
5. Rerun the prior 151-case Stage 3 probe suite with only the two documented
   unreachable-surrogate harness expectations corrected.
6. Rerun the 141-case Stage 2 closure suite, adapting only the already accepted
   keyword-only `caller_id` signature expectation.

Expected repository gate after remediation: 46 collected and 46 passed.

## F-1: dependency exception leakage

Inject dependencies that raise exceptions containing unmistakable secret text
at each public boundary:

- challenge issuance;
- presentation registration;
- presentation consumption;
- Ratify verification;
- protected handler invocation.

Exercise the real Streamable HTTP MCP client or HTTP upload route as applicable.
Require a stable `DENY_VERIFIER_UNAVAILABLE` response, zero exception text,
zero connection strings or secret markers, and no traceback returned to the
caller. Every pre-handler failure must leave the handler count unchanged.

Check that ordinary `CarrierDenied` reasons remain exact and are not collapsed
into verifier-unavailable. Confirm malformed hostile inputs still receive their
specific stable codes.

## F-2: interceptor action snapshot

Suspend execution independently while awaiting the challenge provider and the
presentation uploader. Mutate every caller-owned argument field during each
suspension. Inspect copies of:

- challenge-provider arguments;
- uploaded `WorkOrder`;
- final dispatched MCP request arguments;
- receiver handler argument in an end-to-end control.

All four stages must bind the same original validated action snapshot. The
final request argument mapping must not be the caller's mutable dictionary.
Later caller mutation must not change it. Keep the valid control path live and
confirm it still reaches the handler exactly once.

Also recheck that only the proof reference is added to headers and proof bytes
never enter model-visible arguments, tool schemas, messages, or returned errors.

## F-3: threat-model accuracy

Audit `docs/THREAT-MODEL.md` against the implementation and evidence matrix.
It must now accurately describe:

- the local Streamable HTTP MCP boundary;
- transport authentication as distinct from Ratify authority;
- proof-body upload and opaque single-use references;
- duplicate, oversized, replay, caller-isolation, Host, and dependency-failure
  threats;
- the absence of Maritime deployment, durable state, ingress limits, rate
  limiting, production handler, and complete agent container;
- root execution, moving base image, copied tests, restart behavior, registry
  capacity, public discovery, and clock synchronization as residual risks or
  hardening notes where applicable.

Flag any statement that implies completed Maritime deployment, production
readiness, endorsement, or an independently operated receiver.

## F-4: root-key startup validation

Start or construct the receiver with:

- valid generated public keys;
- malformed base64 for either key;
- empty values;
- Ed25519 lengths 0, 3, 31, 33, and a very large value;
- ML-DSA-65 lengths 0, 3, 1951, 1953, and a very large value.

Valid 32-byte Ed25519 and 1,952-byte ML-DSA-65 public keys must construct the
app. Every malformed or wrong-length setting must prevent startup and identify
only the setting name, never its value. A rejected configuration must not serve
`/health`.

## Non-regression attacks

At minimum, rerun:

- duplicate and oversized auth and proof-reference carriers;
- malformed and duplicate-key uploads;
- cross-caller reference theft and eviction attempts;
- 2, 8, 32, and 64 worker single-reference races;
- action and bundle mutation races;
- Host protection;
- unauthenticated tool calls;
- full allow, same-agent over-limit denial, and replay through real Streamable
  HTTP MCP;
- container build, import, locked versions, missing-secret failure, and live
  `/health` with generated test-only configuration.

Do not convert the previously documented deployment-sizing notes into blockers
unless the remediation introduced a frozen-requirement violation, authorization
bypass, secret disclosure, fail-open path, replay, cross-caller state attack,
multiple handler entry, or materially false public claim.

## Report

Return exact counts, an F-1 through F-4 closure table, any new findings with
reproduction and handler delta, attacks that survived falsification, missing
permanent tests, evidence-copy discrepancies, temporary-test locations, and one
verdict:

`STAGE 3 PASSES`

or

`STAGE 3 NEEDS CHANGES`

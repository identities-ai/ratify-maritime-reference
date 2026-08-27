# Maritime × Ratify Authority-Aware Agent Reference

Status: Frozen Phase 1 baseline; deployed pilot; completion pending

Version: 1.2

Date: 2026-08-27
Owner: Ratify Protocol / Identities.AI, Inc.

## 1. Purpose

This document is the normative source of truth for the Phase 1 Maritime ×
Ratify reference implementation and the compatibility boundary for an optional
Phase 2 XMTP extension.

The baseline is frozen before implementation. A mandatory requirement may be
changed only by updating this version, recording the reason in the change log,
and identifying affected tests and public claims. Implementation convenience is
not sufficient reason to weaken an acceptance case.

The reference must prove one claim:

> An agent running in an isolated Maritime runtime can carry principal-signed,
> bounded authority to an external receiver, which independently verifies the
> exact requested action before its protected handler runs.

The public value proposition is: **Maritime isolates where the agent runs;
Ratify controls what it may do beyond that runtime.** Phase 1 is about the
runtime-to-tool authorization boundary. It is not an agent-to-agent handoff,
cross-organization federation, or remote kill-switch demonstration.

The reference must visibly distinguish identity from authority. The same
recognized agent must be allowed for an in-bounds action and denied for an
out-of-bounds action.

## 2. Normative language

The words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are to
be interpreted as normative requirements. A requirement identified by an ID is
not complete until a test or recorded inspection provides evidence for it.

## 3. Scope

### 3.1 Phase 1

Phase 1 consists of:

1. A principal-controlled issuance step outside the agent runtime.
2. A Python LangChain task agent deployable as a custom container on Maritime.
3. A `MultiServerMCPClient` tool interceptor that creates a Ratify presentation
   after LangChain selects a typed MCP tool action.
4. A separately deployed Streamable HTTP MCP receiver that verifies the
   presentation, applies its own policy, and conditionally invokes a simulated
   work-order handler.
5. A deterministic adversarial acceptance gate requiring no paid model API.
6. A thin demonstration console driven by real, redacted execution results.

The initial deployed transport is MCP over direct Streamable HTTP. The receiver
may be operated by Ratify in Phase 1, but it must have separate keys,
configuration, storage, and process boundaries from the agent.

### 3.2 Optional Phase 2

Phase 2 preserves the Phase 1 action envelope, Ratify proof, verifier policy,
denial reasons, and protected handler while:

1. Replacing direct delivery with an XMTP application-layer message.
2. Binding the authenticated XMTP sender to the Ratify subject.
3. Moving receiver administration and execution authority to an independent
   organization.

Phase 2 is not required for Phase 1 completion.

## 4. Non-goals

The reference MUST NOT:

- Reimplement Ratify Verify or depend on the unfinished commercial platform.
- Claim that Ratify is an agent, runtime, identity provider, transport, policy
  engine, or business application.
- Claim that Phase 1 represents two independent legal entities.
- Require changes to Maritime core, LangChain core, XMTP core, or Ratify core.
- Use actual money, production procurement, or irreversible business actions.
- Treat an LLM response, prompt instruction, API key, or authenticated session
  as proof of delegated authority.
- Allow the model to view or generate private keys, raw delegation secrets, or
  Ratify presentation bytes.
- Require an LLM to run the security acceptance suite.
- Claim endorsement by Maritime, LangChain, or XMTP without written approval.
- Use XMTP, agent-to-agent delegation, cross-deployment federation, or a remote
  revocation event as the headline Phase 1 story.
- Make the open reference depend on Ratify Verify, lead capture, or a paid
  service to reproduce its security claim.

## 5. Actors and trust boundaries

| Actor | Controls | Must not control |
|---|---|---|
| Principal | Issuer key and delegation terms | Receiver decision or handler |
| Agent | Agent key and proposed action | Issuer key or receiver policy |
| Maritime | Isolated agent runtime and injected secrets | Principal or receiver keys |
| Receiver | Trust anchors, challenge/replay state, local policy, handler | Principal or agent keys |
| Model | Selection of typed business arguments | Proof construction or authorization decision |
| Demo console | Scenario trigger and redacted evidence display | Any private key or allow/deny decision |

The receiver is the load-bearing authorization boundary. A denial is only real
when the protected handler is not invoked.

## 6. Reference use case

The principal delegates authority for an electrical maintenance work order:

```text
scope:       custom:work_order:create
resource:    site:warehouse-seattle-01
category:    electrical
max_amount:  500 USD
audience:    maritime-ratify-demo-receiver
validity:    short-lived demonstration window
```

The protected action schema is:

```json
{
  "request_id": "req-...",
  "scope": "custom:work_order:create",
  "resource": "site:warehouse-seattle-01",
  "category": "electrical",
  "amount_minor": 42000,
  "currency": "USD",
  "description": "Inspect and repair loading-bay lighting"
}
```

Money MUST use integer minor units. The operation MUST be validated against a
closed schema before it is canonicalized or signed. Unknown fields MUST fail.

## 7. Required architecture

```text
Principal issuance environment
        |
        | short-lived delegation to agent key
        v
Maritime isolated runtime
  LangChain agent -> typed MCP tool -> Ratify tool interceptor
        |
        | Streamable HTTP MCP: exact action + Ratify presentation
        v
Separately deployed MCP receiver
  schema -> freshness -> Ratify verification -> local policy -> handler
```

The direct and future XMTP transports MUST use the same versioned application
envelope. Transport-specific identity binding is additional verifier context;
it MUST NOT replace Ratify subject verification.

## 8. Functional requirements

### 8.1 Issuance

- **ISS-001:** The issuer key MUST remain outside the Maritime agent runtime.
- **ISS-002:** The delegation MUST identify the agent public key as subject.
- **ISS-003:** The delegation MUST bind scope, resource, category, amount,
  audience, and expiry.
- **ISS-004:** The build MUST use the published Python package
  `ratify-protocol==1.0.0a16`, not an unpublished local SDK checkout.
- **ISS-005:** Demo fixtures MUST be reproducible without publishing private
  production key material.

### 8.2 LangChain agent

- **AGT-001:** The agent MUST use LangChain's released `create_agent` API and
  `MultiServerMCPClient` tool-interceptor API.
- **AGT-002:** The model-visible tool schema MUST contain business arguments
  only.
- **AGT-003:** The MCP interceptor MUST validate and canonicalize the operation
  after tool selection.
- **AGT-004:** Ratify proof material MUST be attached by the interceptor outside
  model context and MUST NOT appear in the model-visible MCP tool schema.
- **AGT-005:** The model MUST NOT receive authority to bypass, weaken, or
  reinterpret a receiver denial.
- **AGT-006:** Tests MUST use a deterministic model or fixed tool-call fixture.
- **AGT-007:** A real model provider MUST remain optional and configured only
  through deployment environment settings.
- **AGT-008:** The Phase 1 implementation MUST preserve the tested seam and
  security invariants of Ratify Protocol draft PR #66; divergence MUST be
  documented and independently tested.

### 8.3 MCP boundary

- **MCP-001:** The receiver MUST expose challenge and work-order tools through
  Streamable HTTP MCP using released public SDK APIs.
- **MCP-002:** Only the work-order tool call MAY carry a Ratify presentation.
- **MCP-003:** Transport authentication MUST be distinct from Ratify authority.
- **MCP-004:** Duplicate presentation or transport-auth carriers MUST fail as
  ambiguous input.
- **MCP-005:** Oversized presentation carriers MUST fail before MCP dispatch.
- **MCP-006:** The receiver MUST document the current proof carrier and its
  production size limitations.
- **MCP-007:** Pending challenge state MUST be owned by the authenticated
  transport caller and Ratify subject. A proof from another caller or subject
  MUST NOT consume or evict a legitimate caller's pending challenge.

### 8.4 Freshness and presentation

- **PRS-001:** The receiver MUST issue a single-use, short-lived challenge.
- **PRS-002:** The presentation MUST bind the challenge, receiver audience,
  agent subject, and digest of the exact canonical operation.
- **PRS-003:** A challenge MUST be consumed atomically on successful use.
- **PRS-004:** Expired, unknown, or previously consumed challenges MUST fail
  closed.
- **PRS-005:** Presentation parsing MUST reject malformed, ambiguous, duplicate,
  and oversized security inputs.

### 8.5 Receiver

- **RCV-001:** The receiver MUST reconstruct the operation from validated input;
  it MUST NOT trust a caller-supplied digest without recomputation.
- **RCV-002:** The receiver MUST supply expected scope, resource, audience, and
  constraint context to Ratify verification.
- **RCV-003:** The receiver MUST verify the configured trusted issuer and agent
  subject.
- **RCV-004:** The receiver MUST check expiry and configured revocation state.
- **RCV-005:** Missing required revocation or freshness state MUST fail closed.
- **RCV-006:** Ratify verification MUST complete before local business policy.
- **RCV-007:** Local policy MUST complete before handler invocation.
- **RCV-008:** The handler MUST be a separate function with an observable,
  concurrency-safe invocation counter.
- **RCV-009:** Every denial MUST leave the invocation counter unchanged.
- **RCV-010:** Denial responses MUST use stable reason codes and MUST NOT expose
  sensitive proof or key material.

### 8.6 Maritime deployment

- **MAR-001:** The agent MUST build from a documented Dockerfile and public
  repository layout supported by Maritime's custom deployment path.
- **MAR-002:** Agent private material MUST enter through Maritime secrets, never
  the image, repository, logs, or demo UI.
- **MAR-003:** The deployment MUST persist only state required by the agent and
  MUST document restart behavior.
- **MAR-004:** Health and readiness endpoints MUST not disclose authority or
  identity material.
- **MAR-005:** Logs MUST identify decision stages without logging prompts,
  presentations, private keys, or full delegations.
- **MAR-006:** The same image MUST run locally and on Maritime without code
  changes; environment configuration MAY differ.

### 8.7 Demo console

- **UI-001:** The console MUST show the principal, Maritime agent, external
  boundary, receiver, and protected handler as distinct stages.
- **UI-002:** It MUST display requested values beside authorized bounds.
- **UI-003:** It MUST show agent recognition separately from authority result.
- **UI-004:** It MUST show the receiver reason code and handler invocation count.
- **UI-005:** Every displayed decision, reason, handler count, and authority
  fact MUST originate from an executed request, not a pre-scripted outcome.
  Client-side waiting copy MUST be identified as waiting state and MUST NOT
  claim that an unobserved server stage completed.
- **UI-006:** The console MUST redact keys, full proofs, sensitive identifiers,
  and internal exception text.
- **UI-007:** The default view MUST be understandable without cryptographic or
  agent-framework knowledge; technical evidence MAY be expandable.
- **UI-008:** The console MUST use the official Ratify Protocol logo and the
  approved Ratify visual system; partner marks MUST NOT appear without approval.
- **UI-009:** The console MUST be a standalone application in this repository;
  it MUST NOT depend on or be embedded in the unfinished Ratify Verify web app.
- **UI-010:** The console MUST be responsive and visually verified at desktop
  and mobile widths with no clipped, overlapping, or unreadable content.
- **UI-011:** Color MUST NOT be the only decision signal. ALLOW and DENY states
  MUST include text, icons, and accessible status semantics.
- **UI-012:** Motion MUST NOT imply unobserved server-stage completion. Outcome
  stage state MAY render only after a verified result, and all motion MUST
  remain restrained and honor reduced-motion preferences.
- **UI-013:** The public console MUST expose only enumerated demo scenarios and
  rate-limited read-only evidence; arbitrary tool or receiver invocation is
  forbidden.

### 8.8 Developer value and open-reference boundary

- **VAL-001:** The first-run path MUST let a developer execute one allow and one
  same-agent denial locally without a paid model or Ratify service.
- **VAL-002:** The primary demonstration MUST keep agent identity constant while
  changing only an authority-bound business value.
- **VAL-003:** Public materials MUST explain isolation, authentication,
  delegated authority, and receiver policy as distinct controls.
- **VAL-004:** The principal call to action MUST be to run the reference; a
  Maritime deployment path and Ratify production-pilot path MAY follow.
- **VAL-005:** The reference MUST describe Ratify Verify as a separate managed
  product under development and MUST NOT imply general availability.
- **VAL-006:** Ratify Verify positioning MUST distinguish its planned managed
  operational capabilities from the open protocol and MUST NOT imply a
  proprietary proof format.
- **VAL-007:** The demo MUST NOT require contact information, account creation,
  or a sales interaction before the local security claim can be reproduced.

### 8.9 Repository and publication lifecycle

- **PUB-001:** This standalone repository MUST be the canonical home of the
  complete agent, receiver, console, deployment assets, and evidence.
- **PUB-002:** After the standalone repository is public and its gate passes,
  `ratify-protocol` MUST add a concise Maritime reference entry, catalog entry,
  and registry record in the same publication change.
- **PUB-003:** The protocol entry MUST link to an immutable tested revision of
  this repository and provide the fastest clean-checkout run command.
- **PUB-004:** Maritime MUST NOT be advertised as an available Ratify reference
  until both repositories are public, the linked revision passes, and all
  required discovery surfaces are merged into `ratify-protocol` `main`.
- **PUB-005:** Creating or publishing this repository MUST NOT imply Maritime
  review, endorsement, or partnership; those claims require separate written
  approval.

## 9. Deployment topology

The deployed Phase 1 pilot topology is:

```text
labs.ratifyprotocol.com/maritime     Standalone static demo console
        |
        | restricted scenario trigger + redacted event reads
        v
Maritime runtime A                  LangChain agent container
        |
        | Streamable HTTP MCP action + Ratify presentation
        v
Maritime runtime B                  Separately isolated MCP receiver
```

The console source MUST live at `apps/demo-console/` in this repository. The
site is deployed through Sites behind the secret-bound Ratify Labs router at
`labs.ratifyprotocol.com/maritime`; a separate Cloudflare Worker exposes only
the enumerated, origin-bound scenario API. This is separate code and deployment
from `ratify-web`.

The agent MUST live at `apps/agent/` and deploy to one Maritime isolated
runtime. The receiver MUST live at `apps/receiver/` and deploy to a second
Maritime isolated runtime with separate secrets, configuration, state, and
public endpoint. Hosting both Phase 1 services on Maritime demonstrates runtime
isolation but MUST NOT be described as two independently operated legal
entities.

Phase 2 moves `apps/receiver/` or a compatible implementation to the independent
receiving organization's infrastructure. The console hostname MAY remain
unchanged because it is an observation and control surface, not a trust anchor.

## 10. Acceptance scenarios

| ID | Scenario | Expected decision | Handler delta |
|---|---|---:|---:|
| T-001 | Exact agent, scope, site, category, amount, audience, and validity | ALLOW | +1 |
| T-002 | Amount above 500 USD | DENY_LIMIT_EXCEEDED | 0 |
| T-003 | Different site | DENY_RESOURCE_MISMATCH | 0 |
| T-004 | Different category | DENY_CONSTRAINT_MISMATCH | 0 |
| T-005 | Operation changed after presentation | DENY_OPERATION_MISMATCH | 0 |
| T-006 | Expired delegation | DENY_EXPIRED | 0 |
| T-007 | Revoked delegation | DENY_REVOKED | 0 |
| T-008 | Reused challenge/presentation | DENY_REPLAY | 0 |
| T-009 | Presentation from another agent | DENY_SUBJECT_MISMATCH | 0 |
| T-010 | Untrusted issuer | DENY_UNTRUSTED_ISSUER | 0 |
| T-011 | Wrong audience | DENY_AUDIENCE_MISMATCH | 0 |
| T-012 | Malformed or unknown application field | DENY_INVALID_REQUEST | 0 |
| T-013 | Duplicate security carrier | DENY_AMBIGUOUS_INPUT | 0 |
| T-014 | Oversized security carrier | DENY_OVERSIZED_INPUT | 0 |
| T-015 | Required revocation store unavailable | DENY_VERIFIER_UNAVAILABLE | 0 |
| T-016 | Two concurrent uses of one challenge | One ALLOW maximum | +1 maximum |

The final gate MAY add cases but MUST NOT remove or weaken these cases without a
versioned requirements change.

## 11. Evidence requirements

The reference is complete only when it produces:

- A lockfile and dependency inventory.
- A one-command local acceptance gate.
- Exact test totals with zero skips, xfails, failures, or errors.
- Evidence that the published Ratify package is loaded during the gate.
- Evidence that one valid request increments the handler exactly once.
- Evidence that every denial leaves the handler unchanged.
- A recorded Maritime deployment identifier and health result.
- A threat model naming protected assets, trust assumptions, attacks, and
  residual risks.
- A README that separates implemented, tested, deployed, and proposed claims.
- A short demo recording and screenshots generated from the working system.
- Desktop and mobile visual-QA renders of the final demo console.

## 12. Phase 2 compatibility requirements

- **XMT-001:** Phase 2 MUST use XMTP's public Agent SDK application layer and
  MUST NOT require an XMTP core change.
- **XMT-002:** The Phase 1 action envelope MUST remain semantically unchanged.
- **XMT-003:** The receiver MUST bind the authenticated XMTP sender identity to
  the Ratify subject.
- **XMT-004:** A valid proof forwarded by a different XMTP sender MUST fail.
- **XMT-005:** XMTP delivery, retries, and duplicate messages MUST NOT weaken
  replay protection.
- **XMT-006:** A cross-organization claim requires the receiving organization to
  control its receiver, trust configuration, policy, execution credential, and
  audit trail.

## 13. Public-claims policy

Before Phase 1 evidence exists, wording MUST use "proposed," "will," or
"designed to." After evidence exists, claims MUST identify what was tested and
where it was deployed.

Approved Phase 1 claim shape:

> An open LangChain reference agent running on Maritime presented bounded Ratify
> authority to a separately deployed receiver, which verified the exact action
> before its simulated handler ran.

Phase 1 MUST NOT be described as a production integration, formal partnership,
two-company transaction, or independently operated receiver unless those facts
become true and are documented.

The initial joint publication MUST lead with the runtime-to-tool boundary and
same-agent/different-authority result. Agent-to-agent handoffs, federation, and
XMTP MUST NOT be used as the headline or primary diagram. Revocation remains an
acceptance case but is not the central public demonstration.

## 14. Completion definition

Phase 1 is complete only when:

1. All mandatory requirements have traceable evidence.
2. Every acceptance scenario passes with zero skips or expected failures.
3. The local Docker workflow is reproducible from a clean checkout.
4. The agent is successfully deployed on Maritime.
5. The receiver verifies before every protected handler invocation.
6. The demo console displays real allow and deny evidence.
7. Maritime has had an opportunity to review technical and publication claims.
8. Every `VAL-*` requirement has inspectable evidence.
9. Every `PUB-*` requirement has inspectable evidence.

Until all nine conditions are met, the deployed pilot remains incomplete under
this baseline and MUST NOT be advertised as a Maritime-reviewed or completed
Ratify reference.

## 15. Baseline change log

- **1.2, 2026-08-27:** Corrected the frozen use-case scope to the implemented
  `custom:work_order:create` profile; replaced pre-deployment topology language
  with the deployed Sites, Labs-router, and scenario-Worker topology; and
  narrowed UI-005/UI-012 so execution claims remain response-sourced while
  honest client-side runtime waiting states cannot imply unobserved progress.
- **1.1, 2026-08-17:** Added MCP-007 after the Stage 2 hostile closure review
  identified cross-caller pending-state eviction as a transport-boundary
  liveness risk. Stage 2 authorization behavior is unchanged.
- **1.0, 2026-08-17:** Froze the pre-implementation Phase 1 baseline; made the
  Maritime runtime-to-tool thesis explicit; excluded federation and handoff
  claims from the Phase 1 story; added developer-value and open-reference
  requirements; froze the standalone implementation and protocol-index
  publication lifecycle.

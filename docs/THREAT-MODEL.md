# Threat model

Status: Stage 4 local agent and MCP boundary. Maritime deployment threats
remain incomplete.

## Protected assets

- Principal and agent private keys.
- Delegation and presentation integrity.
- Receiver trust anchors, challenge state, revocation state, and policy.
- Transport credentials and caller-to-presentation isolation.
- Uploaded proof bodies and opaque, single-use proof references.
- The protected handler and its invocation state.
- Redacted public evidence.
- Agent private signing material and demo-access credentials.

## Security objective

Only an exact, fresh action within the principal's signed authority may reach
the protected handler. Authentication, model output, and runtime location are
not substitutes for receiver-side authorization.

## Current trust assumptions

- The principal issuance environment is outside the agent runtime.
- The published Ratify Python package implements its documented verification
  semantics.
- The receiver controls its trust root, policy, challenge state, and clock.
- Transport authentication identifies a caller but grants no Ratify authority.
- Ratify verification grants bounded authority but does not authenticate the
  HTTP caller.
- The local presentation registry and challenge store are process-local and
  protected by receiver-owned locks.
- Phase 1 agent and receiver runtimes are separately configured but operated by
  Ratify; they are not separate legal organizations.
- Deployment issuance runs outside Maritime. Its principal artifact retains the
  issuer key for renewal and must remain under principal control; only public
  trust material enters the receiver and only delegated agent material enters
  the agent runtime.

## Implemented attack surface

| Attack | Required result |
|---|---|
| Exceed amount | Deny before handler |
| Substitute resource or category | Deny before handler |
| Change action after challenge | Deny before handler |
| Replay or race one challenge | At most one handler invocation |
| Present as another agent or issuer | Deny before handler |
| Expired or revoked delegation | Deny before handler |
| Remove verifier state | Fail closed |
| Malformed, duplicate, or oversized proof input | Reject before dispatch |
| Missing, wrong, duplicate, or oversized transport credential | Reject before MCP tool logic |
| Duplicate or oversized proof-reference carrier | Reject without consuming the reference |
| Malformed, duplicate-key, unknown-field, or oversized proof upload | Reject before proof registration |
| Steal or consume another caller's proof reference | Deny without evicting legitimate state |
| Replay or race one proof reference | At most one handler invocation |
| Substitute an action between challenge, upload, and MCP dispatch | Deny; dispatch one immutable snapshot |
| Use a disallowed Host value | Reject at the MCP transport boundary |
| Raise from a verifier dependency | Stable denial without internal exception text |
| Call the public demo endpoint without its bearer credential | Reject before agent execution |
| Flood the authenticated demo endpoint | Bound requests with an in-process rate limit |
| Supply an unsupported or malformed demo scenario | Reject before agent execution |
| Render settings in diagnostics | Keep private authority and tokens out of their representation |

The local boundary uses Streamable HTTP MCP. A bearer credential resolves to a
receiver-owned caller ID. The proof body is uploaded separately, bounded before
decoding, and replaced with a short opaque reference. The registry binds that
reference to the authenticated caller and exact canonical action, expires it
after 60 seconds, and consumes it once. The model-visible tool schema contains
business arguments only.

The Stage 4 agent container exposes a side-effect-free health route and an
authenticated `/chat` route limited to the enumerated `allow` and `over_limit`
scenarios. Its demo bearer credential controls endpoint access only. The agent
still constructs a Ratify presentation for every selected action, and the
receiver independently verifies that authority before its handler runs. The
deterministic model is the free acceptance path; production mode selects an
explicit OpenAI-compatible model without giving the model access to Ratify
private keys, proofs, or receiver policy.

## Residual risks

The receiver is deployed on Maritime from an immutable public GHCR digest. The
agent deployment remains incomplete, so the two-runtime Maritime validation is
not yet proven. There is still no durable shared state, ingress body limit,
distributed rate limiting, or production handler.

Agent keys and transport credentials are supplied at runtime and excluded from
the Docker build context. The demo and receiver transport tokens must be
distinct. Both runtime images use a pinned base-image digest, copy only their
runtime source, run as an unprivileged fixed UID, and define a local health
check. The in-process `/chat` limiter is suitable only for the bounded public
demonstration; it resets on restart and is not shared across replicas.
Production exposure still requires ingress-level rate and body limits.

The deployed Maritime gateway was observed on 2026-08-23 to remove the standard
`Authorization` header before forwarding public requests. The demo and receiver
boundaries therefore accept their separate credentials only through
`X-Ratify-Demo-Token` and `X-Ratify-Caller-Token`, respectively.

Demo delegations expire after seven days and are renewed on day five from the
principal-controlled artifact. Renewal preserves the issuer, subject, and
authority bounds and replaces only the signed delegation in the agent runtime.
Maritime's environment injection corrupts the current hybrid certificate above
its working payload size, and its custom-file deployment API failed during the
first live deployment. The demo image therefore receives the public signed
delegation through a BuildKit secret during CI; private keys remain runtime
secrets. Renewal requires a new immutable agent image.

Starlette buffers an authenticated upload before the application checks its
size. The deployed ingress must enforce a body limit. MCP discovery is public,
although every protected tool call authenticates independently. One
authenticated caller can fill the bounded 128-entry in-memory presentation
registry and temporarily deny registrations to other callers. Challenge and
tool-call rate limiting remain deployment requirements.

The local stores do not survive restart and are not shared across replicas.

The receiver deliberately retains a pending challenge after an operation
mismatch so a hostile submission cannot burn the legitimate grant. The request
ID cannot be challenged again until that grant succeeds or its 300-second TTL
expires. Verification is serialized inside one receiver process; deployment
must measure queueing before exposing the endpoint. Agent and receiver clocks
must also be synchronized because Ratify rejects negative challenge age.

The published Ratify alpha.16 Python metadata allows `pqcrypto>=0.3.4`, but
`pqcrypto` 1.0 removed the key-generation API used by alpha.16. This reference
pins `pqcrypto==0.3.4` until the SDK publishes a corrected dependency range or
compatibility update. Python is constrained below 3.14 because the specifically
pinned 0.3.4 release publishes wheels only through CPython 3.13.

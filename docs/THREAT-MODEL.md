# Threat model

Status: Stage 3 local MCP boundary. Maritime deployment threats remain
incomplete.

## Protected assets

- Principal and agent private keys.
- Delegation and presentation integrity.
- Receiver trust anchors, challenge state, revocation state, and policy.
- Transport credentials and caller-to-presentation isolation.
- Uploaded proof bodies and opaque, single-use proof references.
- The protected handler and its invocation state.
- Redacted public evidence.

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

The local boundary uses Streamable HTTP MCP. A bearer credential resolves to a
receiver-owned caller ID. The proof body is uploaded separately, bounded before
decoding, and replaced with a short opaque reference. The registry binds that
reference to the authenticated caller and exact canonical action, expires it
after 60 seconds, and consumes it once. The model-visible tool schema contains
business arguments only.

## Residual risks

This stage has local network transport and a receiver container, but no Maritime
deployment, durable shared state, ingress body limit, rate limiting, production
handler, or complete agent container. Those properties must not be claimed.

Starlette buffers an authenticated upload before the application checks its
size. The deployed ingress must enforce a body limit. MCP discovery is public,
although every protected tool call authenticates independently. One
authenticated caller can fill the bounded 128-entry in-memory presentation
registry and temporarily deny registrations to other callers. Challenge and
tool-call rate limiting remain deployment requirements.

The receiver image currently runs as root, copies the repository test files,
and uses a moving `python:3.12-slim` base tag. These are deployment-hardening and
reproducibility items for MAR-001..006, which remain pending. The local stores
do not survive restart and are not shared across replicas.

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

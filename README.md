# Ratify Maritime Reference

Planned open reference for an authority-aware LangChain agent running on
Maritime and acting through a public MCP tool interceptor against a separately
deployed Ratify-verifying Streamable HTTP MCP receiver.

![Maritime and Ratify Phase 1 authority flow](docs/architecture/phase-1-flow.svg)

The frozen Stage 2 receiver core and Stage 3 transport boundary now cover exact
allow, signed bounds, receiver-owned time and policy, revocation, replay,
hostile concurrency, caller ownership, strict proof upload, duplicate and
oversized carriers, real Streamable HTTP MCP discovery and execution, and the
LangChain authority-interceptor seam. Maritime deployment and the public demo
console remain pending.

![Receiver-owned authorization decision pipeline](docs/architecture/receiver-decision-pipeline.svg)

Run the current gate:

```bash
uv sync --python 3.12
uv run --python 3.12 pytest
```

## Proof carrier

The model-visible MCP tool contains only the six business arguments shown in
the architecture. The LangChain interceptor obtains a challenge, uploads the
canonical Ratify proof through a body capped before decoding, receives a short
single-use reference, and adds only that reference to the selected MCP call.

The current hybrid proof is approximately 18 KB. Carrying it directly in an
HTTP header would be brittle across ingress and proxy limits, so this reference
does not do that. The opaque reference is bound to authenticated caller,
Ratify subject, exact canonical action, and a short TTL, and is consumed once.

The frozen build requirements are in
[`docs/REFERENCE-REQUIREMENTS.md`](docs/REFERENCE-REQUIREMENTS.md).

Planned deployment:

- `apps/agent/`: LangChain agent in one Maritime isolated runtime
- `apps/receiver/`: Ratify-verifying MCP receiver in a second isolated runtime
- `apps/demo-console/`: standalone Ratify-branded UI deployed independently at
  `labs.ratifyprotocol.com/maritime`

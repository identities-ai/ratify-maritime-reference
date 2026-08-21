# Evidence matrix

Status: Stage 2 receiver core and Stage 3 local MCP boundary complete;
Maritime deployment pending.

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
| MAR-001..006 | Maritime deployment record | Pending |
| UI-001..013 | Executed console evidence and visual QA | Pending |
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
- Result: 48 collected, 48 passed, 0 skipped, 0 xfailed, 0 failed, 0 errors
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

#!/usr/bin/env python3
"""Execute and record the public Maritime adversarial gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


EXPECTED = {
    "allow": ("ALLOW", "ALLOW", True),
    "over_limit": ("DENY", "DENY_LIMIT_EXCEEDED", False),
    "wrong_resource": ("DENY", "DENY_RESOURCE_MISMATCH", False),
    "altered_operation": ("DENY", "DENY_OPERATION_MISMATCH", False),
    "expired": ("DENY", "DENY_EXPIRED", False),
    "revoked": ("DENY", "DENY_REVOKED", False),
    "replay": ("DENY", "DENY_REPLAY", False),
    "wrong_agent": ("DENY", "DENY_SUBJECT_MISMATCH", False),
}


def _revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _execute(endpoint: str, origin: str, scenario: str) -> dict[str, object]:
    request = Request(
        endpoint,
        data=json.dumps({"scenario": scenario}).encode(),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST",
    )
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--endpoint",
        default="https://maritime-api.ratifyprotocol.com/api/scenario",
    )
    parser.add_argument(
        "--origin", default="https://labs.ratifyprotocol.com"
    )
    args = parser.parse_args()

    results = []
    failures = []
    try:
        for scenario, expected in EXPECTED.items():
            result = _execute(args.endpoint, args.origin, scenario)
            observed = (
                result.get("decision"),
                result.get("reason"),
                result.get("handler_invoked"),
            )
            passed = observed == expected
            if not passed:
                failures.append({
                    "scenario": scenario,
                    "expected": expected,
                    "observed": observed,
                })
            results.append({"scenario": scenario, "passed": passed, **result})
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"gate execution failed: {exc}", file=sys.stderr)
        return 2

    evidence = {
        "schema": "ratify-maritime-adversarial-results/v1",
        "executed_at": datetime.now(UTC).isoformat(),
        "source_revision": _revision(),
        "endpoint": args.endpoint,
        "origin": args.origin,
        "claim": "one allow plus seven distinct receiver denials",
        "passed": not failures and len(results) == len(EXPECTED),
        "results": results,
    }
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    artifact = {
        **evidence,
        "evidence_sha256": hashlib.sha256(canonical).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n")
    if failures:
        print(json.dumps(failures, indent=2), file=sys.stderr)
        return 1
    print(f"PASS: {len(results)} executed scenarios recorded in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

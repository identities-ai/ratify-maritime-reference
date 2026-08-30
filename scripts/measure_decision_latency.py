#!/usr/bin/env python3
"""Measure what a verified authorization decision costs on the live deployment.

The pilot's correctness claim is that no denied request reaches protected code.
This measures the other question a platform reader asks: what does the
authorization boundary cost, and how does that compare to the cost of the
isolated runtimes it protects.

Every sample is one full public round trip: proxy, the agent runtime, a
Streamable HTTP MCP call into the separately deployed receiver runtime, Ratify
verification, and the decision back. When both Maritime runtimes have gone to
sleep, the first sample also pays for waking both of them, which is why it is
recorded separately rather than folded into the distribution.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_ENDPOINT = "https://maritime-api.ratifyprotocol.com/api/scenario"
DEFAULT_ORIGIN = "https://labs.ratifyprotocol.com"
DEFAULT_HEALTH = (
    "https://api.maritime.sh/a/526e13bb-5a8c-47fc-94bf-96a0dc417983/health"
)
PROBE_AGENT = "ratify-maritime-latency-probe/1.0"


def _sample(endpoint: str, origin: str, scenario: str) -> tuple[float, dict]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"scenario": scenario}).encode(),
        headers={
            "Content-Type": "application/json",
            "Origin": origin,
            # The edge rejects the default urllib signature before the request
            # reaches the Worker. Identify the probe honestly instead.
            "User-Agent": PROBE_AGENT,
        },
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.load(response)
    return (time.perf_counter() - started) * 1000, body


def _health(url: str) -> float:
    """Time a side-effect-free readiness call, which wakes a sleeping runtime."""
    request = urllib.request.Request(url, headers={"User-Agent": PROBE_AGENT})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=180) as response:
        response.read()
    return (time.perf_counter() - started) * 1000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--scenario", default="allow")
    parser.add_argument("--health-url", default=DEFAULT_HEALTH)
    parser.add_argument("--health-samples", type=int, default=3)
    parser.add_argument("--warm-samples", type=int, default=12)
    parser.add_argument(
        "--interval",
        type=float,
        default=9.0,
        help="Seconds between samples. The public demo rate limit is ten "
             "requests per client per minute, so keep this above six.",
    )
    parser.add_argument(
        "--runtimes-were-sleeping",
        action="store_true",
        help="Record that both Maritime runtimes were confirmed asleep before "
             "the first sample, which makes it a true cold start.",
    )
    arguments = parser.parse_args()

    started_at = datetime.now(UTC).isoformat()
    try:
        readiness = [_health(arguments.health_url)]
        print(f"agent readiness {readiness[0]:8.0f} ms  (first call)")
        for _ in range(max(0, arguments.health_samples - 1)):
            time.sleep(1)
            readiness.append(_health(arguments.health_url))
            print(f"agent readiness {readiness[-1]:8.0f} ms")

        # A first scenario call against sleeping runtimes has to wake the agent
        # and then the receiver, which can exceed the proxy's own timeout. That
        # outcome is recorded rather than treated as a measurement error.
        first_attempt_failed = False
        try:
            cold_ms, cold_body = _sample(
                arguments.endpoint, arguments.origin, arguments.scenario
            )
        except urllib.error.HTTPError as error:
            if error.code != 502:
                raise
            first_attempt_failed = True
            print("first scenario call returned 502 while the runtimes woke")
            time.sleep(arguments.interval)
            cold_ms, cold_body = _sample(
                arguments.endpoint, arguments.origin, arguments.scenario
            )
        print(f"first sample {cold_ms:8.0f} ms  {cold_body['reason']}")
        warm = []
        failures = 0
        for index in range(arguments.warm_samples):
            time.sleep(arguments.interval)
            try:
                elapsed, body = _sample(
                    arguments.endpoint, arguments.origin, arguments.scenario
                )
            except urllib.error.HTTPError as error:
                # A failed sample is a property of the deployment worth
                # recording, not a reason to abandon the measurement.
                failures += 1
                print(f"  warm {index + 1:>2}    failed  HTTP {error.code}")
                continue
            if body.get("decision") != cold_body.get("decision"):
                print("decision changed during measurement", file=sys.stderr)
                return 1
            warm.append(elapsed)
            print(f"  warm {index + 1:>2} {elapsed:8.0f} ms")
        if not warm:
            print("every warm sample failed", file=sys.stderr)
            return 1
    except (urllib.error.URLError, TimeoutError, OSError, KeyError) as error:
        print(f"measurement failed: {error}", file=sys.stderr)
        return 2

    artifact = {
        "schema": "ratify-maritime-decision-latency/v1",
        "measured_at": started_at,
        "endpoint": arguments.endpoint,
        "scenario": arguments.scenario,
        "decision": cold_body.get("decision"),
        "decided_by": cold_body.get("decided_by"),
        "agent_readiness_first_ms": round(readiness[0]),
        "agent_readiness_warm_median_ms": round(statistics.median(readiness[1:]))
            if len(readiness) > 1 else None,
        "first_scenario_attempt_failed": first_attempt_failed,
        "first_sample_ms": round(cold_ms),
        "first_sample_was_cold_start": arguments.runtimes_were_sleeping,
        "warm_samples_attempted": arguments.warm_samples,
        "warm_samples_succeeded": len(warm),
        "warm_samples_failed": failures,
        "warm_min_ms": round(min(warm)),
        "warm_median_ms": round(statistics.median(warm)),
        "warm_max_ms": round(max(warm)),
        "disclosures": {
            "scope": (
                "Every sample is a full public round trip through the proxy, "
                "the agent runtime, and the separately deployed receiver "
                "runtime. It is not an isolated measurement of cryptographic "
                "verification."
            ),
            "client": (
                "Measured from a single client location over the public "
                "internet, so the numbers include network transit and are not "
                "a controlled benchmark."
            ),
            "reliability": (
                "Failed samples are recorded rather than discarded. A non-zero "
                "warm_samples_failed count means the public endpoint did not "
                "return a decision within the proxy's budget for that attempt."
            ),
            "cold_start": (
                "The first sample includes waking both sleeping Maritime "
                "runtimes when the recorded flag says they were asleep. "
                "Readiness is measured against the agent's side-effect-free "
                "health route, so it covers one runtime rather than both."
            ),
        },
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(artifact, indent=2) + "\n")
    print(
        f"\nreadiness first {artifact['agent_readiness_first_ms']} ms · "
        f"first scenario {artifact['first_sample_ms']} ms · warm median "
        f"{artifact['warm_median_ms']} ms over {len(warm)} samples "
        f"({artifact['warm_min_ms']}-{artifact['warm_max_ms']} ms) · "
        f"{failures} failed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

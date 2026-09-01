#!/usr/bin/env python3
"""Execute and record the deployed runtime-isolation gate.

The adversarial gate answers whether one agent can exceed its own authority.
This answers a different question: whether authority stays bound to the runtime
it was delegated to, when two separately delegated agents run on the same
platform and talk to the same receiver.

Both runtimes run the same image digest and differ only by injected authority.
Neither holds the other's private key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from ratify_protocol import decode_delegation_cert

REPOSITORY = Path(__file__).resolve().parents[1]
EXPECTATIONS = REPOSITORY / "docs" / "runtime-isolation-expectations.json"
COMPARED = ("decision", "reason", "handler_invoked", "decided_by", "verification_status")

DISCLOSURES = {
    "runtime_identity": (
        "Runtime identifiers are supplied by the operator and are not "
        "observable in a response."
    ),
    "image_binding": 'A digest cannot bind a running Maritime runtime to an image. Maritime converts an image to a root filesystem before boot and performs no digest verification at launch, and it records a manifest digest only for images it builds itself. The digest here binds our published build to its source revision in the public registry, which is a claim about the registry and not about the runtime. Maritime can attest the runtime identifiers and the image reference each runtime launched from; maritime_attestation records that confirmation when it has been given, and is null until then.',
    "transient_failures": (
        "A recorded transient_proxy_failures count means the deployment stalled "
        "beyond the proxy budget for that attempt and it was retried. Any "
        "decision the receiver returned is final and was never retried."
    ),
    "handler_count": (
        "The handler count is receiver-wide and shared by every caller, so the "
        "recorded delta for one attempt can include unrelated public traffic. "
        "The per-attempt handler_invoked fact is the authoritative one."
    ),
    "evidence_sha256": (
        "Integrity checksum over this file's canonical form. It is not a "
        "signature and it does not attest that the recorded execution occurred."
    ),
    "operator": (
        "Both runtimes and the receiver are operated by Ratify. This "
        "demonstrates isolation between separately delegated agents, not "
        "between separate organizations."
    ),
}


def _observed_revision(image: str) -> str | None:
    prefix = "ghcr.io/"
    if not image.startswith(prefix) or "@" not in image:
        return None
    repository, digest = image[len(prefix):].split("@", 1)
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            token = client.get(
                "https://ghcr.io/token",
                params={"scope": f"repository:{repository}:pull", "service": "ghcr.io"},
            ).json().get("token")
            if not isinstance(token, str) or not token:
                return None
            authorization = {"Authorization": f"Bearer {token}"}
            manifest = client.get(
                f"https://ghcr.io/v2/{repository}/manifests/{digest}",
                headers={
                    **authorization,
                    "Accept": (
                        "application/vnd.oci.image.manifest.v1+json,"
                        "application/vnd.docker.distribution.manifest.v2+json"
                    ),
                },
            ).json()
            config = manifest.get("config", {}).get("digest")
            if not isinstance(config, str) or not config:
                return None
            blob = client.get(
                f"https://ghcr.io/v2/{repository}/blobs/{config}",
                headers=authorization,
            ).json()
    except (httpx.HTTPError, json.JSONDecodeError, AttributeError):
        return None
    revision = blob.get("config", {}).get("Labels", {}).get(
        "org.opencontainers.image.revision"
    )
    return revision if isinstance(revision, str) else None


def _execute(
    endpoint: str, origin: str, scenario: str, attempts: int = 3
) -> tuple[dict, float, int]:
    """Execute one scenario, retrying only a proxy-level transient failure.

    The deployment intermittently stalls beyond the proxy's budget, including
    on traffic that performs no verification work. That is a recorded platform
    property rather than an authorization result. A stall surfaces either as a
    502 from the proxy or as a transport timeout, so both are retried and the
    count is kept in the artifact. Any decision the receiver actually
    returns is final and is never retried.
    """
    transient = 0
    for remaining in range(attempts - 1, -1, -1):
        started = time.perf_counter()
        try:
            response = httpx.post(
                endpoint,
                headers={"Content-Type": "application/json", "Origin": origin},
                json={"scenario": scenario},
                timeout=90,
            )
        except (httpx.TimeoutException, httpx.TransportError):
            if not remaining:
                raise
            transient += 1
            time.sleep(15)
            continue
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code == 502 and remaining:
            transient += 1
            time.sleep(15)
            continue
        response.raise_for_status()
        return response.json(), elapsed, transient
    raise httpx.HTTPError("scenario did not return a decision")


def _prewarm(*health_urls: str) -> None:
    """Wake both runtimes before measuring, so the gate is not a wake test."""
    for url in health_urls:
        try:
            httpx.get(url, timeout=120)
        except httpx.HTTPError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--endpoint", default="https://maritime-api.ratifyprotocol.com/api/scenario"
    )
    parser.add_argument("--origin", default="https://labs.ratifyprotocol.com")
    parser.add_argument("--agent-image", required=True)
    parser.add_argument("--agent-source-revision", required=True)
    parser.add_argument("--receiver-image", required=True)
    parser.add_argument("--receiver-source-revision", required=True)
    parser.add_argument("--primary-runtime-id", required=True)
    parser.add_argument("--secondary-runtime-id", required=True)
    parser.add_argument("--receiver-runtime-id", required=True)
    # Taken as delegation files rather than identifier strings. A transcribed
    # subject reached the published evidence once, wrong by one character, and
    # nothing caught it because the only check was that the two differed.
    parser.add_argument("--primary-delegation", type=Path, required=True)
    parser.add_argument("--secondary-delegation", type=Path, required=True)
    parser.add_argument("--worker-version", required=True)
    parser.add_argument(
        "--maritime-attestation",
        default=None,
        help="Reference for a Maritime confirmation of the runtime identifiers "
             "and launched image reference, once given. Left unset the artifact "
             "records that no such confirmation exists.",
    )
    parser.add_argument("--interval", type=float, default=8.0)
    arguments = parser.parse_args()

    try:
        primary_subject = decode_delegation_cert(
            arguments.primary_delegation.read_text(encoding="utf-8")
        ).subject_id
        secondary_subject = decode_delegation_cert(
            arguments.secondary_delegation.read_text(encoding="utf-8")
        ).subject_id
    except (OSError, ValueError) as error:
        print(f"could not read a delegation: {error}", file=sys.stderr)
        return 3
    if primary_subject == secondary_subject:
        print("the two runtimes must carry different subjects", file=sys.stderr)
        return 3

    identity = {}
    for role, image, revision in (
        ("agent", arguments.agent_image, arguments.agent_source_revision),
        ("receiver", arguments.receiver_image, arguments.receiver_source_revision),
    ):
        observed = _observed_revision(image)
        identity[f"{role}_image_revision_check"] = (
            "unverified" if observed is None
            else "observed" if observed == revision else "mismatch"
        )
        identity[f"{role}_image_revision_observed"] = observed
        if identity[f"{role}_image_revision_check"] == "mismatch":
            print(
                f"{role} image records revision {observed}, not {revision}",
                file=sys.stderr,
            )
            return 3

    _prewarm(
        f"https://api.maritime.sh/a/{arguments.primary_runtime_id}/health",
        f"https://api.maritime.sh/a/{arguments.secondary_runtime_id}/health",
        f"https://api.maritime.sh/a/{arguments.receiver_runtime_id}/health",
    )
    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))["attempts"]
    attempts = []
    failures = []
    previous_count = None
    try:
        for name, required in expectations.items():
            if attempts:
                time.sleep(arguments.interval)
            result, elapsed, transient = _execute(
                arguments.endpoint, arguments.origin, required["scenario"]
            )
            observed = {field: result.get(field) for field in COMPARED}
            passed = all(observed[field] == required[field] for field in COMPARED)
            if not passed:
                failures.append({
                    "attempt": name,
                    "expected": {f: required[f] for f in COMPARED},
                    "observed": observed,
                })
            count = result.get("handler_invocations")
            attempts.append({
                "attempt": name,
                "runtime": required["runtime"],
                "scenario": required["scenario"],
                "passed": passed,
                "latency_ms": round(elapsed),
                "transient_proxy_failures": transient,
                "handler_invocations_before": previous_count,
                "handler_invocations_after": count,
                **result,
            })
            previous_count = count
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        print(f"gate execution failed: {exc}", file=sys.stderr)
        return 2

    evidence = {
        "schema": "ratify-maritime-runtime-isolation-results/v1",
        "executed_at": datetime.now(UTC).isoformat(),
        "source_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip(),
        "endpoint": arguments.endpoint,
        "origin": arguments.origin,
        "claim": (
            "two separately delegated agents on one platform, each allowed "
            "within its own authority and neither able to use the other's"
        ),
        "deployment": {
            "agent_image": arguments.agent_image,
            "agent_source_revision": arguments.agent_source_revision,
            "receiver_image": arguments.receiver_image,
            "receiver_source_revision": arguments.receiver_source_revision,
            "primary_runtime_id": arguments.primary_runtime_id,
            "secondary_runtime_id": arguments.secondary_runtime_id,
            "receiver_runtime_id": arguments.receiver_runtime_id,
            "primary_agent_subject": primary_subject,
            "secondary_agent_subject": secondary_subject,
            "worker_version": arguments.worker_version,
            "runtimes_share_one_image": True,
            "maritime_attestation": arguments.maritime_attestation,
            **identity,
        },
        "passed": not failures and len(attempts) == len(expectations),
        "attempts": attempts,
        "disclosures": DISCLOSURES,
    }
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(
        {**evidence, "evidence_sha256": hashlib.sha256(canonical).hexdigest()},
        indent=2,
    ) + "\n")
    if failures:
        print(json.dumps(failures, indent=2), file=sys.stderr)
        return 1
    print(f"PASS: {len(attempts)} cross-runtime attempts recorded in {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

import httpx


# The required result for every scenario, including the layer that must decide
# it, is the shared contract in docs/gate-expectations.json. The deciding layer
# is part of the claim: a denial that never reaches Ratify verification
# demonstrates the receiver's own binding rather than the protocol, and the gate
# fails if a scenario changes which layer decided it.
REPOSITORY = Path(__file__).resolve().parents[1]
EXPECTATIONS = REPOSITORY / "docs" / "gate-expectations.json"


def _expected() -> dict[str, tuple[object, ...]]:
    scenarios = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))["scenarios"]
    return {
        name: (
            required["decision"],
            required["reason"],
            required["handler_invoked"],
            required["decided_by"],
            required["verification_status"],
        )
        for name, required in scenarios.items()
    }


EXPECTED = _expected()

DISCLOSURES = {
    "deployment_identity": (
        "Each image digest is checked against the source revision the public "
        "registry records for it, so that pairing is observed rather than "
        "asserted. That the live deployment runs these digests remains "
        "operator-asserted."
    ),
    "image_binding": 'A digest cannot bind a running Maritime runtime to an image. Maritime converts an image to a root filesystem before boot and performs no digest verification at launch, and it records a manifest digest only for images it builds itself. The digest here binds our published build to its source revision in the public registry, which is a claim about the registry and not about the runtime. Maritime can attest the runtime identifiers and the image reference each runtime launched from; maritime_attestation records that confirmation when it has been given, and is null until then.',
    "evidence_sha256": (
        "Integrity checksum over this file's canonical form. It is not a "
        "signature and it does not attest that the recorded execution occurred."
    ),
    "model": (
        "Deterministic tool-call harness. Scenarios are enumerated rather than "
        "chosen by a reasoning model."
    ),
    "operator": (
        "The agent and receiver runtimes are separately configured but both "
        "operated by Ratify. This is not a cross-organization deployment."
    ),
}


def _revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _observed_revision(image: str) -> str | None:
    """Read the source revision the public registry records for an image digest.

    Returns None when the digest cannot be resolved, which the caller records as
    an unverified identifier rather than treating as a passing check.
    """
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
    labels = blob.get("config", {}).get("Labels") or {}
    revision = labels.get("org.opencontainers.image.revision")
    return revision if isinstance(revision, str) else None


def _verify_image_identity(
    image: str, claimed_revision: str
) -> tuple[str, str | None]:
    """Compare a claimed source revision against the registry's own record."""
    observed = _observed_revision(image)
    if observed is None:
        return "unverified", None
    if observed != claimed_revision:
        return "mismatch", observed
    return "observed", observed


def _execute(endpoint: str, origin: str, scenario: str) -> dict[str, object]:
    response = httpx.post(
        endpoint,
        headers={"Content-Type": "application/json", "Origin": origin},
        json={"scenario": scenario},
        timeout=45,
    )
    response.raise_for_status()
    return response.json()


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
    parser.add_argument("--agent-source-revision", required=True)
    parser.add_argument("--agent-image", required=True)
    parser.add_argument("--receiver-source-revision", required=True)
    parser.add_argument("--receiver-image", required=True)
    parser.add_argument("--worker-version", required=True)
    parser.add_argument(
        "--maritime-attestation",
        default=None,
        help="Reference for a Maritime confirmation of the runtime identifiers "
             "and launched image reference, once given. Left unset the artifact "
             "records that no such confirmation exists.",
    )
    args = parser.parse_args()

    identity = {}
    for role, image, revision in (
        ("agent", args.agent_image, args.agent_source_revision),
        ("receiver", args.receiver_image, args.receiver_source_revision),
    ):
        status, observed = _verify_image_identity(image, revision)
        identity[f"{role}_image_revision_check"] = status
        identity[f"{role}_image_revision_observed"] = observed
        if status == "mismatch":
            print(
                f"{role} image {image} records revision {observed}, "
                f"not the supplied {revision}",
                file=sys.stderr,
            )
            return 3

    results = []
    failures = []
    try:
        for scenario, expected in EXPECTED.items():
            result = _execute(args.endpoint, args.origin, scenario)
            observed = (
                result.get("decision"),
                result.get("reason"),
                result.get("handler_invoked"),
                result.get("decided_by"),
                result.get("verification_status"),
            )
            passed = observed == expected
            if not passed:
                failures.append({
                    "scenario": scenario,
                    "expected": expected,
                    "observed": observed,
                })
            results.append({"scenario": scenario, "passed": passed, **result})
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        print(f"gate execution failed: {exc}", file=sys.stderr)
        return 2

    evidence = {
        "schema": "ratify-maritime-adversarial-results/v1",
        "executed_at": datetime.now(UTC).isoformat(),
        "source_revision": _revision(),
        "endpoint": args.endpoint,
        "origin": args.origin,
        "deployment": {
            "agent_source_revision": args.agent_source_revision,
            "agent_image": args.agent_image,
            "receiver_source_revision": args.receiver_source_revision,
            "receiver_image": args.receiver_image,
            "worker_version": args.worker_version,
            "maritime_attestation": args.maritime_attestation,
            **identity,
        },
        "claim": "one allow plus eight distinct receiver denials",
        "disclosures": DISCLOSURES,
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

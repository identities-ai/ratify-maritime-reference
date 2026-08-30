#!/usr/bin/env python3
"""Reproduce the adversarial gate locally from the published images.

This is the check the published results artifact cannot be: it does not ask the
reader to trust a response from Ratify-operated infrastructure. It runs the
published agent and receiver image digests on the reader's own machine, against
a principal issued on that machine seconds earlier, and compares every result
against `docs/gate-expectations.json`.

Requirements are Docker and Python 3.10 or newer. There is no repository
install, no Ratify credential, and no call to the live deployment.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
EXPECTATIONS = REPOSITORY / "docs" / "gate-expectations.json"

DEFAULT_AGENT_IMAGE = (
    "ghcr.io/identities-ai/ratify-maritime-agent"
    "@sha256:d03f6173557b4654da91af9cd16f8d478186b908c868279b0e2843720419a0d6"
)
DEFAULT_RECEIVER_IMAGE = (
    "ghcr.io/identities-ai/ratify-maritime-receiver"
    "@sha256:baa50190790a6734fedc9976e0d7a60424a1a4d18a86a9ee0dd531a68a0e0bed"
)
RECEIVER_ALIAS = "receiver"
CONTAINER_PORT = "8080"
COMPARED = ("decision", "reason", "handler_invoked", "decided_by", "verification_status")


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _observed_revision(image: str) -> str | None:
    """Read the source revision the public registry records for an image digest."""
    prefix = "ghcr.io/"
    if not image.startswith(prefix) or "@" not in image:
        return None
    repository, digest = image[len(prefix):].split("@", 1)
    try:
        with urllib.request.urlopen(
            f"https://ghcr.io/token?scope=repository:{repository}:pull&service=ghcr.io",
            timeout=30,
        ) as response:
            token = json.load(response).get("token")
        if not isinstance(token, str) or not token:
            return None
        manifest_request = urllib.request.Request(
            f"https://ghcr.io/v2/{repository}/manifests/{digest}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": (
                    "application/vnd.oci.image.manifest.v1+json,"
                    "application/vnd.docker.distribution.manifest.v2+json"
                ),
            },
        )
        with urllib.request.urlopen(manifest_request, timeout=30) as response:
            configuration = json.load(response).get("config", {}).get("digest")
        if not isinstance(configuration, str) or not configuration:
            return None
        blob_request = urllib.request.Request(
            f"https://ghcr.io/v2/{repository}/blobs/{configuration}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(blob_request, timeout=30) as response:
            labels = json.load(response).get("config", {}).get("Labels") or {}
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None
    revision = labels.get("org.opencontainers.image.revision")
    return revision if isinstance(revision, str) else None


def _issue(directory: Path, image: str, platform: str) -> Path:
    """Issue a fresh principal inside the published agent image."""
    _run([
        "docker", "run", "--rm", "--platform", platform, "--user", "0:0",
        "-v", f"{directory}:/out", image, "sh", "-c",
        "python -c \"from pathlib import Path; "
        "from maritime_ratify.deployment_issuance import issue_deployment; "
        "issue_deployment(Path('/out/issuance'))\" && chmod -R a+rX /out",
    ])
    return directory / "issuance"


def _start_receiver(
    name: str, network: str, image: str, platform: str, environment: Path
) -> None:
    _run([
        "docker", "run", "--rm", "--detach", "--platform", platform,
        "--name", name, "--network", network, "--network-alias", RECEIVER_ALIAS,
        "--env-file", str(environment),
        "-e", f"PORT={CONTAINER_PORT}",
        "-e", f"RATIFY_ALLOWED_HOSTS={RECEIVER_ALIAS}:{CONTAINER_PORT}",
        image,
    ])


def _start_agent(
    name: str,
    network: str,
    image: str,
    platform: str,
    issuance: Path,
    environment: Path,
    port: int,
) -> None:
    receiver = f"http://{RECEIVER_ALIAS}:{CONTAINER_PORT}"
    _run([
        "docker", "run", "--rm", "--detach", "--platform", platform,
        "--name", name, "--network", network,
        "--env-file", str(environment),
        "-e", f"PORT={CONTAINER_PORT}",
        "-e", f"RATIFY_RECEIVER_MCP_URL={receiver}/mcp/",
        "-e", f"RATIFY_PRESENTATION_URL={receiver}/presentations",
        # The published image carries the deployment's own delegation. These
        # mounts replace it with the principal issued on this machine, so the
        # reproduction never depends on deployment authority material.
        "-v", f"{issuance / 'delegation.json'}:/app/deployment/delegation.json:ro",
        "-v", (
            f"{issuance / 'scenario-authorities.json'}"
            ":/app/deployment/scenario-authorities.json:ro"
        ),
        "-p", f"127.0.0.1:{port}:{CONTAINER_PORT}",
        image,
    ])


def _await_health(port: int, name: str, seconds: int = 180) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=5
            ) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2)
    logs = subprocess.run(
        ["docker", "logs", "--tail", "40", name], capture_output=True, text=True
    )
    raise RuntimeError(f"{name} did not become healthy\n{logs.stdout}{logs.stderr}")


def _execute(port: int, token: str, scenario: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/chat",
        data=json.dumps({"message": scenario}).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Ratify-Demo-Token": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def _remove(*names: str) -> None:
    for name in names:
        subprocess.run(
            ["docker", "rm", "--force", name], capture_output=True, text=True
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-image", default=DEFAULT_AGENT_IMAGE)
    parser.add_argument("--receiver-image", default=DEFAULT_RECEIVER_IMAGE)
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path.home(),
        help=(
            "Where to create the temporary issuance directory. It must be a "
            "path the Docker engine shares with this machine, which is why the "
            "default is the home directory rather than the system temporary "
            "directory."
        ),
    )
    parser.add_argument(
        "--skip-registry-check",
        action="store_true",
        help="Do not resolve the image digests against the public registry.",
    )
    arguments = parser.parse_args()

    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))["scenarios"]

    print(f"agent image    {arguments.agent_image}")
    print(f"receiver image {arguments.receiver_image}")
    if not arguments.skip_registry_check:
        for role, image in (
            ("agent", arguments.agent_image),
            ("receiver", arguments.receiver_image),
        ):
            revision = _observed_revision(image)
            print(
                f"{role} digest records source revision "
                f"{revision or 'unavailable, continuing'}"
            )
    print()

    suffix = uuid.uuid4().hex[:8]
    network = f"ratify-repro-{suffix}"
    receiver_name = f"ratify-repro-receiver-{suffix}"
    agent_name = f"ratify-repro-agent-{suffix}"
    port = _free_port()

    with tempfile.TemporaryDirectory(
        prefix=".ratify-repro-", dir=str(arguments.workspace_root)
    ) as workspace:
        directory = Path(workspace)
        try:
            print("issuing a fresh principal inside the published agent image")
            issuance = _issue(directory, arguments.agent_image, arguments.platform)
            if not (issuance / "agent.env").is_file():
                raise RuntimeError(
                    f"issuance produced no files under {directory}. The Docker "
                    "engine is not sharing that path, so the container wrote "
                    "into its own virtual machine. Re-run with "
                    "--workspace-root pointing at a shared directory."
                )
            agent_environment = _read_env(issuance / "agent.env")
            _run(["docker", "network", "create", network])
            print("starting both published images on a private network")
            _start_receiver(
                receiver_name, network, arguments.receiver_image,
                arguments.platform, issuance / "receiver.env",
            )
            _start_agent(
                agent_name, network, arguments.agent_image, arguments.platform,
                issuance, issuance / "agent.env", port,
            )
            _await_health(port, agent_name)
            print("executing every enumerated scenario\n")

            failures = []
            width = max(len(name) for name in expectations)
            for scenario, expected in expectations.items():
                observed = _execute(
                    port, agent_environment["RATIFY_DEMO_TOKEN"], scenario
                )
                actual = {field: observed.get(field) for field in COMPARED}
                passed = all(actual[field] == expected[field] for field in COMPARED)
                if not passed:
                    failures.append({
                        "scenario": scenario,
                        "expected": {f: expected[f] for f in COMPARED},
                        "observed": actual,
                    })
                print(
                    f"  {'PASS' if passed else 'FAIL'}  {scenario:<{width}}  "
                    f"{actual['reason']}  decided by {actual['decided_by']}"
                )
        except subprocess.CalledProcessError as error:
            print(f"\ncommand failed: {error.stderr.strip()}", file=sys.stderr)
            return 2
        except (RuntimeError, urllib.error.URLError, OSError) as error:
            print(f"\nreproduction failed: {error}", file=sys.stderr)
            return 2
        finally:
            _remove(agent_name, receiver_name)
            subprocess.run(
                ["docker", "network", "rm", network], capture_output=True, text=True
            )

    if failures:
        print(json.dumps(failures, indent=2), file=sys.stderr)
        return 1
    print(f"\nPASS: {len(expectations)} scenarios reproduced from the published images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Reproduce the adversarial gate locally from the published images.

This is the check the published results artifact cannot be: it does not ask the
reader to trust a response from Ratify-operated infrastructure. It runs the
published agent and receiver image digests on the reader's own machine, against
a principal issued on that machine seconds earlier, and compares every result
against `docs/gate-expectations.json`, then starts a second agent container from
the same image digest and reproduces the cross-runtime attempts in
`docs/runtime-isolation-expectations.json`.

Requirements are Docker and Python 3.10 or newer. There is no repository
install, no Ratify credential, and no call to the live deployment.
"""

from __future__ import annotations

import argparse
import json
import platform as host_platform
import shutil
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
ISOLATION_EXPECTATIONS = (
    REPOSITORY / "docs" / "runtime-isolation-expectations.json"
)

DEFAULT_AGENT_IMAGE = (
    "ghcr.io/identities-ai/ratify-maritime-agent"
    "@sha256:e933dc0d49b2ca154f8d89af7cb81c23dafdedc09a6d1b8fe32e015bfba0b4c5"
)
DEFAULT_RECEIVER_IMAGE = (
    "ghcr.io/identities-ai/ratify-maritime-receiver"
    "@sha256:dbbef49f304ab682799137745c949b0bf07340725b07fc6a3d98177a9bb9c5e4"
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


def _shares(root: Path, image: str, platform: str) -> bool:
    """Check that the engine bind-mounts this directory back to the host.

    Engines differ: Docker Desktop shares /tmp and /Users, colima shares only
    the home directory by default. An unshared path is not an error, it is
    worse, because the container writes into the engine's virtual machine and
    the host sees an empty directory.
    """
    if not root.is_dir():
        return False
    try:
        probe = Path(tempfile.mkdtemp(prefix=".ratify-probe-", dir=str(root)))
    except OSError:
        return False
    try:
        _run([
            "docker", "run", "--rm", "--platform", platform, "--user", "0:0",
            "-v", f"{probe}:/probe", image, "sh", "-c", "echo ok > /probe/shared",
        ])
        return (probe / "shared").is_file()
    except subprocess.CalledProcessError:
        return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def _resolve_root(requested: Path | None, image: str, platform: str) -> Path:
    candidates = [requested] if requested else [
        Path.home(), Path(tempfile.gettempdir()), Path.cwd()
    ]
    for candidate in candidates:
        if _shares(candidate, image, platform):
            return candidate
    tried = ", ".join(str(candidate) for candidate in candidates)
    raise RuntimeError(
        f"the Docker engine does not share any of: {tried}. Containers would "
        "write into the engine's virtual machine instead of this host. Re-run "
        "with --workspace-root pointing at a directory the engine shares, or "
        "add one in the engine's file-sharing settings."
    )


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
    delegation: str = "delegation.json",
    authorities: str = "scenario-authorities.json",
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
        "-e", f"RATIFY_DELEGATION_PATH=/app/deployment/{delegation}",
        "-e", f"RATIFY_SCENARIO_AUTHORITIES_PATH=/app/deployment/{authorities}",
        "-v", f"{issuance / delegation}:/app/deployment/{delegation}:ro",
        "-v", f"{issuance / authorities}:/app/deployment/{authorities}:ro",
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
        default=None,
        help=(
            "Where to create the temporary issuance directory. It must be a "
            "path the Docker engine shares with this machine. When omitted, "
            "the home directory, the system temporary directory, and the "
            "working directory are probed in that order."
        ),
    )
    parser.add_argument(
        "--skip-registry-check",
        action="store_true",
        help="Do not resolve the image digests against the public registry.",
    )
    arguments = parser.parse_args()

    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))["scenarios"]
    isolation = json.loads(
        ISOLATION_EXPECTATIONS.read_text(encoding="utf-8")
    )["attempts"]

    print(f"agent image    {arguments.agent_image}")
    print(f"receiver image {arguments.receiver_image}")
    machine = host_platform.machine()
    if arguments.platform.endswith("/amd64") and machine not in {"x86_64", "AMD64"}:
        print(
            f"note: the published images are {arguments.platform} and this host "
            f"is {machine}, so both containers run under emulation. Every "
            "scenario is correct but slower."
        )
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
    try:
        workspace_root = _resolve_root(
            arguments.workspace_root, arguments.agent_image, arguments.platform
        )
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"reproduction failed: {error}", file=sys.stderr)
        return 2
    print(f"workspace root {workspace_root}\n")

    suffix = uuid.uuid4().hex[:8]
    network = f"ratify-repro-{suffix}"
    receiver_name = f"ratify-repro-receiver-{suffix}"
    agent_name = f"ratify-repro-agent-{suffix}"
    second_name = f"ratify-repro-agent-b-{suffix}"
    port = _free_port()
    second_port = _free_port()

    with tempfile.TemporaryDirectory(
        prefix=".ratify-repro-", dir=str(workspace_root)
    ) as workspace:
        directory = Path(workspace)
        try:
            print("issuing a fresh principal inside the published agent image")
            issuance = _issue(directory, arguments.agent_image, arguments.platform)
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
            # The same image digest, a different injected authority. This is the
            # second runtime, and it never receives the first agent's key.
            _start_agent(
                second_name, network, arguments.agent_image, arguments.platform,
                issuance, issuance / "agent-b.env", second_port,
                delegation="delegation-b.json",
                authorities="scenario-authorities-b.json",
            )
            _await_health(port, agent_name)
            _await_health(second_port, second_name)
            second_environment = _read_env(issuance / "agent-b.env")
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

            print("\nexecuting every cross-runtime attempt\n")
            ports = {"primary": port, "secondary": second_port}
            tokens = {
                "primary": agent_environment["RATIFY_DEMO_TOKEN"],
                "secondary": second_environment["RATIFY_DEMO_TOKEN"],
            }
            width = max(len(name) for name in isolation)
            for name, expected in isolation.items():
                runtime = expected["runtime"]
                observed = _execute(
                    ports[runtime], tokens[runtime], expected["scenario"]
                )
                actual = {field: observed.get(field) for field in COMPARED}
                passed = all(actual[field] == expected[field] for field in COMPARED)
                if not passed:
                    failures.append({
                        "attempt": name,
                        "expected": {f: expected[f] for f in COMPARED},
                        "observed": actual,
                    })
                print(
                    f"  {'PASS' if passed else 'FAIL'}  {name:<{width}}  "
                    f"{actual['reason']}  decided by {actual['decided_by']}"
                )
        except subprocess.CalledProcessError as error:
            print(f"\ncommand failed: {error.stderr.strip()}", file=sys.stderr)
            return 2
        except (RuntimeError, urllib.error.URLError, OSError) as error:
            print(f"\nreproduction failed: {error}", file=sys.stderr)
            return 2
        finally:
            _remove(agent_name, second_name, receiver_name)
            subprocess.run(
                ["docker", "network", "rm", network], capture_output=True, text=True
            )

    if failures:
        print(json.dumps(failures, indent=2), file=sys.stderr)
        return 1
    print(
        f"\nPASS: {len(expectations)} scenarios and {len(isolation)} cross-runtime "
        "attempts reproduced from the published images"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

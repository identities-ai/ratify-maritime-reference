#!/usr/bin/env python3
"""Reproduce the adversarial gate locally from container images.

This is the check the published results artifact cannot be: it does not ask the
reader to trust a response from Ratify-operated infrastructure. It runs the
agent and receiver images on the reader's own machine, against a principal
issued on that machine seconds earlier, and compares every result against
`docs/gate-expectations.json`, then starts a second agent container from the
same image and reproduces the cross-runtime attempts in
`docs/runtime-isolation-expectations.json`.

Protocol v2. No reviewed Protocol v2 image is published yet, so by default
this builds both images from this checkout (which needs the protocol source
exported by `scripts/vendor_ratify_v2.sh`) and runs those. `--agent-image` and
`--receiver-image` run existing images instead. The published image digests
recorded in `evidence/` are the Protocol v1 deployment of 2026-09 and cannot
satisfy the v2 contracts.

Requirements are Docker and Python 3.10 or newer. There is no Ratify
credential and no call to the live deployment.
"""

from __future__ import annotations

import argparse
import importlib.util
from datetime import UTC, datetime
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


# Load the profile module by path so this script needs no package install;
# the package itself imports the Protocol v2 SDK, the profile does not.
_PROFILE_SPEC = importlib.util.spec_from_file_location(
    "maritime_profile",
    Path(__file__).resolve().parents[1] / "src" / "maritime_ratify" / "profile.py",
)
_PROFILE = importlib.util.module_from_spec(_PROFILE_SPEC)
_PROFILE_SPEC.loader.exec_module(_PROFILE)
VOLUME_DIRECTORY = _PROFILE.VOLUME_DIRECTORY

EXPECTATIONS = REPOSITORY / "docs" / "gate-expectations.json"
ISOLATION_EXPECTATIONS = (
    REPOSITORY / "docs" / "runtime-isolation-expectations.json"
)

# RATIFY_V2_PIN: once reviewed Protocol v2 images are published, default to
# their digests again (as the v1 reproduction did) and keep the local build
# behind a flag.
LOCAL_AGENT_IMAGE = "ratify-maritime-agent:v2-local"
LOCAL_RECEIVER_IMAGE = "ratify-maritime-receiver:v2-local"
PROTOCOL_REVISION = REPOSITORY / "vendor" / "ratify-protocol" / "REVISION"
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
    """Issue a fresh principal inside the agent image."""
    _run([
        "docker", "run", "--rm", "--platform", platform, "--user", "0:0",
        "-v", f"{directory}:/out", image, "sh", "-c",
        "python -c \"from pathlib import Path; "
        "from maritime_ratify.deployment_issuance import issue_deployment; "
        "issue_deployment(Path('/out/issuance'))\" && chmod -R a+rX /out",
    ])
    return directory / "issuance"


def _start_receiver(
    name: str, network: str, image: str, platform: str, issuance: Path
) -> None:
    _run([
        "docker", "run", "--rm", "--detach", "--platform", platform,
        "--name", name, "--network", network, "--network-alias", RECEIVER_ALIAS,
        "--env-file", str(issuance / "receiver.env"),
        "-e", f"PORT={CONTAINER_PORT}",
        "-e", f"RATIFY_ALLOWED_HOSTS={RECEIVER_ALIAS}:{CONTAINER_PORT}",
        # The signed status list sits where receiver.env says the volume
        # holds it. Its signature, not this mount, is what makes it count.
        "-v", f"{issuance / 'status-list.json'}:{VOLUME_DIRECTORY}/status-list.json:ro",
        image,
    ])


def _build(platform: str) -> tuple[str, str]:
    """Build both images from this checkout, tagged for local use only."""
    if not PROTOCOL_REVISION.is_file():
        raise RuntimeError(
            "vendor/ratify-protocol is missing. Run "
            "scripts/vendor_ratify_v2.sh /path/to/ratify-protocol first."
        )
    for image, dockerfile in (
        (LOCAL_AGENT_IMAGE, "Dockerfile"),
        (LOCAL_RECEIVER_IMAGE, "apps/receiver/Dockerfile"),
    ):
        print(f"building {image} from {dockerfile}")
        _run([
            "docker", "build", "--platform", platform, "-t", image,
            "-f", str(REPOSITORY / dockerfile), str(REPOSITORY),
        ])
    return LOCAL_AGENT_IMAGE, LOCAL_RECEIVER_IMAGE


def _image_id(image: str) -> str | None:
    completed = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True, text=True,
    )
    return completed.stdout.strip() or None


def _source_revision() -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    revision = completed.stdout.strip() or None
    dirty = subprocess.run(
        ["git", "-C", str(REPOSITORY), "status", "--porcelain", "--", "src", "apps",
         "pyproject.toml", "uv.lock", "Dockerfile"],
        capture_output=True, text=True,
    ).stdout.strip()
    return f"{revision}+uncommitted" if revision and dirty else revision


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
        # Mounted at the same path the deployment reads from, so the
        # reproduction exercises the deployed configuration rather than a
        # look-alike. The material is the principal issued on this machine, so
        # it never depends on deployment authority.
        "-e", f"RATIFY_DELEGATION_PATH={VOLUME_DIRECTORY}/delegation.json",
        "-e", f"RATIFY_SCENARIO_AUTHORITIES_PATH="
              f"{VOLUME_DIRECTORY}/scenario-authorities.json",
        "-v", f"{issuance / delegation}:{VOLUME_DIRECTORY}/delegation.json:ro",
        "-v", (
            f"{issuance / authorities}:{VOLUME_DIRECTORY}"
            "/scenario-authorities.json:ro"
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
    parser.add_argument(
        "--agent-image", default=None,
        help="Run this image instead of building one from this checkout.",
    )
    parser.add_argument(
        "--receiver-image", default=None,
        help="Run this image instead of building one from this checkout.",
    )
    parser.add_argument(
        "--platform", default=None,
        help="Container platform. Defaults to this host's architecture for a "
             "local build and to linux/amd64 for given images.",
    )
    parser.add_argument(
        "--record", type=Path, default=None,
        help="Write a JSON evidence record of this run to this path.",
    )
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
    if (arguments.agent_image is None) != (arguments.receiver_image is None):
        parser.error("--agent-image and --receiver-image go together")
    build = arguments.agent_image is None
    if arguments.platform is None:
        native = host_platform.machine() in {"arm64", "aarch64"}
        arguments.platform = (
            "linux/arm64" if build and native else "linux/amd64"
        )
    if build:
        try:
            arguments.agent_image, arguments.receiver_image = _build(
                arguments.platform
            )
        except (RuntimeError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", None) or str(error)
            print(f"reproduction failed: {detail}", file=sys.stderr)
            return 2

    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))["scenarios"]
    isolation = json.loads(
        ISOLATION_EXPECTATIONS.read_text(encoding="utf-8")
    )["attempts"]

    print(f"agent image    {arguments.agent_image}")
    print(f"receiver image {arguments.receiver_image}")
    machine = host_platform.machine()
    if arguments.platform.endswith("/amd64") and machine not in {"x86_64", "AMD64"}:
        print(
            f"note: the images are {arguments.platform} and this host "
            f"is {machine}, so both containers run under emulation. Every "
            "scenario is correct but slower."
        )
    if not arguments.skip_registry_check and not build:
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
            print("issuing a fresh principal inside the agent image")
            issuance = _issue(directory, arguments.agent_image, arguments.platform)
            agent_environment = _read_env(issuance / "agent.env")
            _run(["docker", "network", "create", network])
            print("starting both images on a private network")
            _start_receiver(
                receiver_name, network, arguments.receiver_image,
                arguments.platform, issuance,
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
            observed_results: dict[str, dict] = {"scenarios": {}, "attempts": {}}
            width = max(len(name) for name in expectations)
            for scenario, expected in expectations.items():
                observed = _execute(
                    port, agent_environment["RATIFY_DEMO_TOKEN"], scenario
                )
                actual = {field: observed.get(field) for field in COMPARED}
                observed_results["scenarios"][scenario] = actual
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
                observed_results["attempts"][name] = actual
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

    if arguments.record is not None:
        _write_record(arguments, build, observed_results, not failures)
    if failures:
        print(json.dumps(failures, indent=2), file=sys.stderr)
        return 1
    origin = "built from this checkout" if build else "given"
    print(
        f"\nPASS: {len(expectations)} scenarios and {len(isolation)} cross-runtime "
        f"attempts reproduced from the images {origin}"
    )
    return 0


def _write_record(arguments, build: bool, observed: dict, passed: bool) -> None:
    revision = (
        PROTOCOL_REVISION.read_text(encoding="utf-8").strip()
        if PROTOCOL_REVISION.is_file() else None
    )
    record = {
        "schema": "ratify-maritime-local-reproduction/v1",
        "protocol_line": "v2",
        "recorded_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "passed": passed,
        "command": "python3 scripts/reproduce_gate_locally.py"
                   + ("" if build else " --agent-image ... --receiver-image ..."),
        "platform": arguments.platform,
        "images": {
            "built_from_checkout": build,
            "agent": arguments.agent_image,
            "agent_image_id": _image_id(arguments.agent_image),
            "receiver": arguments.receiver_image,
            "receiver_image_id": _image_id(arguments.receiver_image),
        },
        "reference_source_revision": _source_revision(),
        "ratify_protocol_revision": revision,
        "results": observed,
        "limits": (
            "Local containers on one machine with fresh issuance. Not a "
            "deployment: no Maritime runtime, no public console and no live "
            "proxy is involved, and the images are local builds, not "
            "published or reviewed digests. The receiver uses the SDK's "
            "in-memory reference stores (claim store, receipt log, "
            "checkpoint, issuance store and journal), which are not durable "
            "and do not form an independent failure domain."
        ),
    }
    arguments.record.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())

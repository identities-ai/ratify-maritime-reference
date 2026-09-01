#!/usr/bin/env python3
"""Rotate deployment authority without rebuilding an image.

The delegation and the adversarial fixture live on each runtime's persistent
volume, written through Maritime's file API, and the runtime verifies each
against a digest supplied in its environment. Rotation is therefore a write, a
digest update, and a restart.

The order matters and this script enforces it: write the artifact, read it back
and confirm the digest, then set the expected digest, then restart. Setting a
digest the volume does not match would stop the runtime from starting, and
writing an artifact without updating the digest would do the same. Neither
half is safe alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

API = "https://api.maritime.sh"
VOLUME_DIRECTORY = "/data/ratify"


@dataclass(frozen=True)
class Runtime:
    name: str
    agent_id: str
    delegation: str
    authorities: str


RUNTIMES = (
    Runtime(
        "ratify-maritime-agent",
        "526e13bb-5a8c-47fc-94bf-96a0dc417983",
        "delegation.json",
        "scenario-authorities.json",
    ),
    Runtime(
        "ratify-maritime-agent-b",
        "2923663f-093a-48c3-bdf7-55e86256ced6",
        "delegation-b.json",
        "scenario-authorities-b.json",
    ),
)


def _write(client: httpx.Client, agent_id: str, path: str, contents: str) -> None:
    response = client.put(
        f"{API}/api/agents/{agent_id}/files/write",
        json={"path": path, "content": contents},
        timeout=120,
    )
    response.raise_for_status()
    if response.json().get("ok") is not True:
        raise RuntimeError(f"write to {path} was not acknowledged")


def _observed_digest(name: str, path: str) -> str:
    """Read the digest back from inside the runtime, not from the local copy."""
    completed = subprocess.run(
        ["maritime", "exec", name, "sha256sum", path],
        capture_output=True, text=True, check=True,
    )
    return completed.stdout.split()[0]


def _set_environment(name: str, assignments: list[str]) -> None:
    subprocess.run(
        ["maritime", "env", "set", name, *assignments],
        capture_output=True, text=True, check=True,
    )


def _restart(name: str, image: str) -> None:
    subprocess.run(
        [
            "maritime", "deploy", name,
            "--source", "docker", "--image", image, "--json",
        ],
        capture_output=True, text=True, check=True,
    )


def _await_health(agent_id: str, seconds: int = 300) -> bool:
    deadline = time.monotonic() + seconds
    url = f"{API}/a/{agent_id}/health"
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=30).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("material", type=Path, help="Directory of issued artifacts.")
    parser.add_argument("--image", required=True, help="Agent image digest to restart on.")
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path.home() / ".ratify" / "maritime-file-api.token",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without changing anything.",
    )
    arguments = parser.parse_args()

    try:
        token = arguments.token_file.read_text(encoding="utf-8").strip()
    except OSError:
        print(f"no token at {arguments.token_file}", file=sys.stderr)
        return 2
    if not token:
        print("token file is empty", file=sys.stderr)
        return 2

    planned = []
    for runtime in RUNTIMES:
        for local, remote in (
            (runtime.delegation, "delegation.json"),
            (runtime.authorities, "scenario-authorities.json"),
        ):
            source = arguments.material / local
            if not source.is_file():
                print(f"missing {source}", file=sys.stderr)
                return 2
            contents = source.read_bytes()
            planned.append((
                runtime, f"{VOLUME_DIRECTORY}/{remote}", contents,
                hashlib.sha256(contents).hexdigest(),
            ))

    for runtime, path, contents, digest in planned:
        print(f"  {runtime.name:<26}{path}  {len(contents)} bytes  {digest[:12]}")
    if arguments.dry_run:
        print("\ndry run, nothing was changed")
        return 0

    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(headers=headers) as client:
        for runtime, path, contents, digest in planned:
            _write(client, runtime.agent_id, path, contents.decode("utf-8"))
            observed = _observed_digest(runtime.name, path)
            if observed != digest:
                print(
                    f"{runtime.name}: {path} read back as {observed}, expected {digest}",
                    file=sys.stderr,
                )
                return 1
            print(f"  written and confirmed  {runtime.name}  {path}")

    # Only once every artifact is confirmed on its volume is it safe to move the
    # expected digests, because a runtime restarted between the two would refuse
    # to start.
    for runtime in RUNTIMES:
        digests = {
            "RATIFY_DELEGATION_SHA256": next(
                d for r, p, _, d in planned
                if r is runtime and p.endswith("/delegation.json")
            ),
            "RATIFY_SCENARIO_AUTHORITIES_SHA256": next(
                d for r, p, _, d in planned
                if r is runtime and p.endswith("/scenario-authorities.json")
            ),
        }
        _set_environment(
            runtime.name,
            [f"{name}={value}" for name, value in digests.items()] + [
                f"RATIFY_DELEGATION_PATH={VOLUME_DIRECTORY}/delegation.json",
                f"RATIFY_SCENARIO_AUTHORITIES_PATH="
                f"{VOLUME_DIRECTORY}/scenario-authorities.json",
            ],
        )
        _restart(runtime.name, arguments.image)
        print(f"  restarting             {runtime.name}")

    for runtime in RUNTIMES:
        if not _await_health(runtime.agent_id):
            print(f"{runtime.name} did not become healthy", file=sys.stderr)
            return 1
        print(f"  healthy                {runtime.name}")

    print(
        "\nrotated. Update the receiver's RATIFY_REVOKED_CERT_IDS and restart it,"
        "\nthen run: python3 scripts/run_acceptance_gate.py --live-gates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run every check that has to pass before the pilot is called finished.

The acceptance gate was a list held in conversation and run by hand, across two
languages and three deployed components. That is how a step gets skipped on the
day it matters. This runs them in one command and prints what passed, what
failed, and what was deliberately not run.

Checks that touch the deployment are separated from checks that do not, so the
suite still gives a useful answer offline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SITE = REPOSITORY / "apps" / "demo-console" / "site"
PROXY = REPOSITORY / "apps" / "demo-console" / "worker"
CONSOLE_URL = "https://labs.ratifyprotocol.com"


@dataclass
class Check:
    name: str
    command: list[str]
    directory: Path
    live: bool = False
    detail: str = ""
    outcome: str = "not run"
    seconds: float = 0.0
    output: str = field(default="", repr=False)


CHECKS = [
    Check("Python suite", ["uv", "run", "--python", "3.12", "pytest", "-q"], REPOSITORY),
    Check("Console build and tests", ["npm", "test"], SITE),
    Check("Console lint", ["npm", "run", "lint"], SITE),
    Check("Proxy typecheck", ["npm", "run", "typecheck"], PROXY),
    Check("Proxy tests", ["npm", "test"], PROXY),
    Check(
        "Deployed assets serve bytes",
        ["node", "scripts/verify-deployed-assets.mjs", CONSOLE_URL],
        SITE,
        live=True,
    ),
    Check(
        "Local reproduction from published images",
        ["python3", "scripts/reproduce_gate_locally.py"],
        REPOSITORY,
        detail="requires Docker; downloads the published image digests",
    ),
]


DELEGATION_CACHE = Path.home() / ".ratify" / "deployed-material"


def _resolved_delegations(isolation: dict) -> dict[str, Path] | None:
    """Locate a delegation for each runtime, preferring what the runtime uses.

    The isolation gate derives each agent's subject from its delegation rather
    than accepting a transcribed identifier. Pointing that at an untracked local
    directory made the gate depend on a copy that can drift or simply not exist
    on another machine, so a missing file is repaired by reading the delegation
    the runtime is actually serving from its volume.
    """
    wanted = {
        "delegation.json": isolation["primary_runtime_id"],
        "delegation-b.json": isolation["secondary_runtime_id"],
    }
    resolved: dict[str, Path] = {}
    for name, runtime_id in wanted.items():
        local = DELEGATION_CACHE / name
        if local.is_file():
            resolved[name] = local
            continue
        agent = subprocess.run(
            ["maritime", "exec", runtime_id, "cat", "/data/ratify/delegation.json"],
            capture_output=True, text=True,
        )
        if agent.returncode != 0 or not agent.stdout.strip():
            return None
        DELEGATION_CACHE.mkdir(parents=True, exist_ok=True)
        local.write_text(agent.stdout.strip(), encoding="utf-8")
        resolved[name] = local
    return resolved


def _live_gate_checks() -> list[Check]:
    """Re-execute both deployed gates against the deployment on record.

    Every argument comes from the committed evidence, so this also answers a
    question the artifacts cannot ask themselves: does the deployment they
    describe still behave the way they recorded. Renewal is the case that
    matters. A stale revocation id leaves the revoked scenario returning ALLOW,
    which no test or reproduction would notice because both use fresh material
    issued locally.
    """
    try:
        adversarial = json.loads(
            (REPOSITORY / "evidence" / "adversarial-results.json")
            .read_text(encoding="utf-8")
        )
        isolation = json.loads(
            (REPOSITORY / "evidence" / "runtime-isolation-results.json")
            .read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return []
    a, i = adversarial["deployment"], isolation["deployment"]
    delegations = _resolved_delegations(i)
    if delegations is None:
        print(
            "  unavailable  Live gates: no delegation for the recorded runtimes.\n"
            f"               Place delegation.json and delegation-b.json in "
            f"{DELEGATION_CACHE},\n"
            "               or authenticate the maritime CLI so they can be read "
            "from the runtimes.",
        )
        return []
    scratch = REPOSITORY / ".acceptance"
    return [
        Check(
            "Live adversarial gate",
            [
                "uv", "run", "--python", "3.12", "python",
                "scripts/run_live_adversarial_gate.py", str(scratch / "adversarial.json"),
                "--agent-image", a["agent_image"],
                "--agent-source-revision", a["agent_source_revision"],
                "--receiver-image", a["receiver_image"],
                "--receiver-source-revision", a["receiver_source_revision"],
                "--worker-version", a["worker_version"],
            ],
            REPOSITORY,
            live=True,
        ),
        Check(
            "Live runtime isolation gate",
            [
                "uv", "run", "--python", "3.12", "python",
                "scripts/run_runtime_isolation_gate.py", str(scratch / "isolation.json"),
                "--agent-image", i["agent_image"],
                "--agent-source-revision", i["agent_source_revision"],
                "--receiver-image", i["receiver_image"],
                "--receiver-source-revision", i["receiver_source_revision"],
                "--primary-runtime-id", i["primary_runtime_id"],
                "--secondary-runtime-id", i["secondary_runtime_id"],
                "--receiver-runtime-id", i["receiver_runtime_id"],
                "--primary-delegation", str(delegations["delegation.json"]),
                "--secondary-delegation", str(delegations["delegation-b.json"]),
                "--worker-version", i["worker_version"],
            ],
            REPOSITORY,
            live=True,
        ),
    ]


def _run(check: Check) -> Check:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            check.command,
            cwd=check.directory,
            capture_output=True,
            text=True,
            timeout=3_600,
        )
        check.output = (completed.stdout + completed.stderr)[-4_000:]
        check.outcome = "pass" if completed.returncode == 0 else "FAIL"
    except FileNotFoundError as error:
        check.outcome = "unavailable"
        check.output = str(error)
    except subprocess.TimeoutExpired:
        check.outcome = "FAIL"
        check.output = "timed out"
    check.seconds = time.monotonic() - started
    return check


def _evidence_is_current() -> tuple[str, str]:
    """Both artifacts must record a pass and carry their own limits.

    A stale artifact is the failure this repository keeps finding: the file
    still says pass while describing a deployment that no longer exists.
    """
    problems = []
    for name in ("adversarial-results", "runtime-isolation-results"):
        path = REPOSITORY / "evidence" / f"{name}.json"
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"{name}: unreadable ({error})")
            continue
        if not recorded.get("passed"):
            problems.append(f"{name}: recorded a failing run")
        for required in ("image_binding", "evidence_sha256", "transient_failures"):
            if required not in recorded.get("disclosures", {}):
                problems.append(f"{name}: missing the {required} disclosure")
        if "maritime_attestation" not in recorded.get("deployment", {}):
            problems.append(f"{name}: missing the maritime_attestation field")

    # The reproduction is the check a sceptic runs, so it has to reproduce the
    # deployment the evidence describes. These drifted apart once already,
    # silently, because both sides passed on their own.
    reproducer = (REPOSITORY / "scripts" / "reproduce_gate_locally.py").read_text()
    isolation = json.loads(
        (REPOSITORY / "evidence" / "runtime-isolation-results.json")
        .read_text(encoding="utf-8")
    )["deployment"]
    for role in ("agent", "receiver"):
        digest = isolation[f"{role}_image"].split("@", 1)[1]
        if digest not in reproducer:
            problems.append(
                f"reproduction does not default to the deployed {role} image"
            )
    return ("FAIL" if problems else "pass", "; ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip the checks that contact the deployment or a registry.",
    )
    parser.add_argument(
        "--live-gates",
        action="store_true",
        help="Also execute both deployed gates against the deployment the "
             "committed evidence describes. Run this after any renewal or "
             "redeploy; it writes its results under .acceptance/ rather than "
             "overwriting the published artifacts.",
    )
    parser.add_argument(
        "--evidence-only",
        action="store_true",
        help="Check only the recorded evidence, with no toolchain required.",
    )
    parser.add_argument(
        "--skip-reproduction",
        action="store_true",
        help="Skip the Docker reproduction, which is the slowest check.",
    )
    arguments = parser.parse_args()

    if arguments.evidence_only:
        status, detail = _evidence_is_current()
        print(f"  {status:<12}Recorded evidence is complete")
        if detail:
            print(f"               {detail}")
        return 0 if status == "pass" else 1

    catalogue = CHECKS + (_live_gate_checks() if arguments.live_gates else [])
    selected = [
        check for check in catalogue
        if not (arguments.offline and check.live)
        and not (arguments.skip_reproduction and "reproduction" in check.name.lower())
    ]
    skipped = [check for check in catalogue if check not in selected]
    if arguments.live_gates:
        (REPOSITORY / ".acceptance").mkdir(exist_ok=True)

    print(f"running {len(selected)} checks\n")
    for check in selected:
        print(f"  ... {check.name}", flush=True)
        _run(check)
        print(f"  {check.outcome:<12}{check.name}  ({check.seconds:.0f}s)")

    status, detail = _evidence_is_current()
    print(f"  {status:<12}Recorded evidence is complete")
    if detail:
        print(f"               {detail}")

    print()
    failures = [c for c in selected if c.outcome != "pass"]
    for check in failures:
        print(f"--- {check.name} ---\n{check.output}\n", file=sys.stderr)
    for check in skipped:
        print(f"  not run     {check.name}")
    if status != "pass":
        failures.append(Check("evidence", [], REPOSITORY, outcome="FAIL"))

    if failures:
        print(f"\nFAIL: {len(failures)} of {len(selected) + 1} checks did not pass")
        return 1
    print(f"\nPASS: {len(selected) + 1} checks")
    if skipped:
        print(f"{len(skipped)} check(s) were not run, listed above")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

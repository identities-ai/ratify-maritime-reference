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

    selected = [
        check for check in CHECKS
        if not (arguments.offline and check.live)
        and not (arguments.skip_reproduction and "reproduction" in check.name.lower())
    ]
    skipped = [check for check in CHECKS if check not in selected]

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

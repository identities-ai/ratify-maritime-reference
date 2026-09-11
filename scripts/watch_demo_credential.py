#!/usr/bin/env python3
"""Warn before the deployed demonstration credential lapses.

The deployment authority lives seven days. When it expires every scenario
fails, including the allow case, while the console keeps serving and returning
200, so the outage is invisible until a visitor clicks something. That happened
on 2026-09-10 and went unnoticed for a day.

This checks the same public path a visitor uses. It reads the delegation expiry
the proxy reports and fails while there is still time to rotate, and it exercises
the revoked scenario, because a renewal that skips the receiver's revocation list
leaves that case returning ALLOW with nothing else looking wrong.

Only stdlib, so it runs on a bare runner with no setup.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = "https://maritime-api.ratifyprotocol.com/api/scenario"
ORIGIN = "https://labs.ratifyprotocol.com"

# Cloudflare's browser integrity check answers the default Python-urllib agent
# with error 1010 before the proxy sees the request, which reads as a 403 and
# looks exactly like the proxy's own origin rejection. Any descriptive agent
# passes, so name this one.
USER_AGENT = "ratify-demo-credential-watch/1.0"

# The deployment has a known intermittent stall, roughly one request in six,
# which the proxy surfaces as SCENARIO_UNAVAILABLE. Retrying distinguishes that
# from a credential that has actually lapsed; without it this job would page
# on a flake several times a week.
ATTEMPTS = 6
BACKOFF_SECONDS = 20


def _scenario(name: str, attempts: int, backoff: int) -> dict:
    """Return the first real decision, or raise if every attempt stalled."""
    last = ""
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps({"scenario": name}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": ORIGIN,
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read())
            except (ValueError, OSError):
                payload = {"error": f"HTTP {error.code}"}
        except (urllib.error.URLError, OSError, ValueError) as error:
            payload = {"error": str(error)}
        if "decision" in payload:
            return payload
        last = str(payload.get("error", payload))
        print(f"  {name}: attempt {attempt} of {attempts} did not decide ({last})")
        if attempt < attempts:
            time.sleep(backoff)
    raise SystemExit(f"FAIL: {name} never returned a decision. Last: {last}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warn-days", type=float, default=2.0,
        help="Fail when fewer than this many days of validity remain.",
    )
    parser.add_argument("--attempts", type=int, default=ATTEMPTS)
    parser.add_argument("--backoff", type=int, default=BACKOFF_SECONDS)
    arguments = parser.parse_args()

    failures: list[str] = []

    allow = _scenario("allow", arguments.attempts, arguments.backoff)
    if allow.get("decision") != "ALLOW":
        failures.append(
            f"allow returned {allow.get('decision')} ({allow.get('reason')}), "
            "which is what an expired or mis-rotated credential looks like"
        )
    else:
        print(f"  allow: ALLOW via {allow.get('decided_by')}")

    expires_at = allow.get("delegation_expires_at")
    if not isinstance(expires_at, int):
        failures.append("the proxy did not report delegation_expires_at")
    else:
        expiry = dt.datetime.fromtimestamp(expires_at, dt.timezone.utc)
        remaining = (expiry - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400
        print(f"  expiry: {expiry:%Y-%m-%d %H:%M UTC} ({remaining:.2f} days remaining)")
        if remaining < arguments.warn_days:
            failures.append(
                f"only {remaining:.2f} days of authority remain, below the "
                f"{arguments.warn_days} day threshold. Rotate now: see "
                "docs/CREDENTIAL-ROTATION.md"
            )

    revoked = _scenario("revoked", arguments.attempts, arguments.backoff)
    if revoked.get("reason") != "DENY_REVOKED" or revoked.get("handler_invoked"):
        failures.append(
            f"revoked returned {revoked.get('decision')} "
            f"({revoked.get('reason')}, handler_invoked="
            f"{revoked.get('handler_invoked')}). The receiver's "
            "RATIFY_REVOKED_CERT_IDS is probably still on a previous renewal"
        )
    else:
        print("  revoked: DENY_REVOKED, handler not invoked")

    if failures:
        print()
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("\nPASS: the deployed credential is valid and the revoked case denies")
    return 0


if __name__ == "__main__":
    sys.exit(main())

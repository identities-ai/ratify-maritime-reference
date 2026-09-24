#!/usr/bin/env python3
"""Keep the three demo runtimes reachable while Maritime public wake is blocked."""

import json
import os
import urllib.request
from pathlib import Path


API = "https://api.maritime.sh"
EVIDENCE = Path(__file__).resolve().parents[1] / "evidence/runtime-isolation-results.json"


def main() -> None:
    token = os.environ["MARITIME_WAKE_TOKEN"]
    deployment = json.loads(EVIDENCE.read_text())["deployment"]
    for role in ("primary", "secondary", "receiver"):
        agent_id = deployment[f"{role}_runtime_id"]
        request = urllib.request.Request(
            f"{API}/api/agents/{agent_id}/exec",
            data=b'{"command":["true"],"timeout":30}',
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response)
        if result.get("exitCode") != 0:
            raise RuntimeError(f"{role} wake failed")
        with urllib.request.urlopen(f"{API}/a/{agent_id}/health", timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"{role} health check failed")
        print(f"{role}: healthy")


if __name__ == "__main__":
    main()

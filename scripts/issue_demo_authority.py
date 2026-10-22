#!/usr/bin/env python3
"""Create or renew separated deployment authority artifacts, or re-sign status."""

import argparse
from pathlib import Path

from maritime_ratify.deployment_issuance import (
    issue_deployment,
    refresh_status,
    renew_deployment,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    issue = subcommands.add_parser("issue")
    issue.add_argument("output", type=Path)
    renew = subcommands.add_parser("renew")
    renew.add_argument("principal", type=Path)
    renew.add_argument("output", type=Path)
    # A signed status list is current for at most one day; re-sign it daily.
    status = subcommands.add_parser("status")
    status.add_argument("principal", type=Path)
    status.add_argument("output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "issue":
        issue_deployment(arguments.output)
    elif arguments.command == "renew":
        renew_deployment(arguments.principal, arguments.output)
    else:
        refresh_status(arguments.principal, arguments.output)


# Parsing at import made this the one script that could not be imported, so
# nothing could test the command that rotates deployment authority.
if __name__ == "__main__":
    main()

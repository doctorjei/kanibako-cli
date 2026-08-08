"""kanibako fork: fork the current project from inside a container."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "fork",
        help="Fork this project into a new directory",
        description=(
            "Fork the current project into a sibling directory. "
            "The fork is a full copy of the workspace and metadata, "
            "assigned a new project name."
        ),
    )
    p.add_argument(
        "name",
        help="Fork name (appended with dot to workspace path)",
    )
    p.set_defaults(func=run_fork, command="fork")


def run_fork(args: argparse.Namespace) -> int:
    from kanibako.settings.settings_resolve import BOX_PINNED_STATE_RELPATH

    # The socket lives under the FIXED pinned root, not $XDG_STATE_HOME — that is
    # where the mount lands, because a mount dest is fixed host-side before the box
    # is live.  This runs IN-BOX, so Path.home() is the box's own home.
    socket_path = Path.home() / BOX_PINNED_STATE_RELPATH / "helper.sock"
    if not socket_path.exists():
        print(
            "Error: fork requires a running kanibako session with helpers enabled.",
            file=sys.stderr,
        )
        return 1

    from kanibako.channels.helper_client import send_request

    try:
        resp = send_request(socket_path, {"action": "fork", "name": args.name})
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if resp.get("status") == "ok":
        print(f"Forked to: {resp['path']}")
        print(f"Project name: {resp['name']}")
        print("Open a new terminal and run kanibako in that directory.")
        return 0
    else:
        print(f"Error: {resp.get('message', 'unknown error')}", file=sys.stderr)
        return 1

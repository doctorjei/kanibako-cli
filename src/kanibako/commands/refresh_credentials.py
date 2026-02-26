"""kanibako reauth: manually verify or re-establish agent authentication."""

from __future__ import annotations

import argparse
import sys

from kanibako.config import config_file_path, load_config
from kanibako.paths import xdg
from kanibako.targets import resolve_target


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "reauth",
        help="Check authentication and login if needed",
        description="Verify agent authentication status and run interactive "
        "login if credentials are expired or missing.",
    )
    p.add_argument(
        "-p", "--project", default=None, help="Target a specific project directory",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    # Resolve project to check auth mode.
    from kanibako.paths import load_std_paths, resolve_any_project
    std = load_std_paths(config)
    proj = resolve_any_project(std, config, getattr(args, "project", None))

    try:
        target = resolve_target(config.target_name or None)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not target.has_binary:
        print("No agent target configured.", file=sys.stderr)
        return 1

    if proj.auth == "distinct":
        # Check project's own credentials instead of host.
        creds_path = target.credential_check_path(proj.shell_path)
        if creds_path and creds_path.is_file():
            print(f"{target.display_name}: distinct auth (project credentials exist).", file=sys.stderr)
            return 0
        else:
            print(
                f"{target.display_name}: distinct auth — no credentials found. "
                "Launch the container to authenticate.",
                file=sys.stderr,
            )
            return 1

    if target.check_auth():
        print(f"{target.display_name}: authenticated.", file=sys.stderr)
        return 0
    else:
        print(f"{target.display_name}: authentication failed.", file=sys.stderr)
        return 1

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
    # -p/--project was REMOVED outright in 1.6.0 (CLEAN BREAK, no deprecation
    # alias — §Design 8).  The target now comes from the blanket --box flag
    # (added by the parent-parser injection) or the cwd ancestor-walk.
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    # Resolve project to check auth mode.  Target comes from --box (or cwd);
    # -p/--project was removed (clean break).
    from kanibako.config import BOX_META_FILE, load_merged_config, resolve_agent
    from kanibako.paths import load_std_paths, resolve_box_target
    std = load_std_paths(config)
    proj = resolve_box_target(std, config, getattr(args, "box", None))

    # Resolve the agent UP FRONT via the unified cascade (explicit > box >
    # workset > system default → installed-count rule).  Typed
    # AgentResolutionError (Gate-2a/2b) propagates to the top-level cli.py
    # handler — never a silent auto-detect.
    project_toml = proj.metadata_path / BOX_META_FILE
    workset_path = (
        (proj.group.root / "settings.yaml") if proj.group is not None else None
    )
    merged = load_merged_config(
        config_file,
        project_toml if project_toml.exists() else None,
        workset_path=workset_path,
    )
    agent_name = resolve_agent(
        explicit_agent=getattr(args, "agent", None),  # Phase D seam (--agent)
        box_agent=merged.box_agent,
        workset_agent=None,  # merged.box_agent already folds the workset tier
        system_default_path=std.settings,
        project_path=proj.project_path,
    )
    target = resolve_target(agent_name, proj.project_path)

    if not target.has_binary:
        print("No agent target configured.", file=sys.stderr)
        return 1

    if not proj.group_auth:
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
        # Sync refreshed credentials to the project shell directory
        if proj.group_auth:
            target.refresh_credentials(proj.shell_path)
        print(f"{target.display_name}: authenticated.", file=sys.stderr)
        return 0
    else:
        print(f"{target.display_name}: authentication failed.", file=sys.stderr)
        return 1

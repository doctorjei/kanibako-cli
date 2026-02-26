"""kanibako workset: create, manage, and inspect working sets."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kanibako.config import config_file_path, load_config
from kanibako.errors import WorksetError
from kanibako.paths import xdg, load_std_paths
from kanibako.utils import confirm_prompt
from kanibako.workset import (
    _write_workset_toml,
    add_project,
    create_workset,
    delete_workset,
    list_worksets,
    load_workset,
    remove_project,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "workset",
        help="Working set commands (create, list, delete, add, remove, info)",
        description="Create and manage working sets of related projects.",
    )
    ws_sub = p.add_subparsers(dest="workset_command", metavar="COMMAND")

    # kanibako workset create <name> <path>
    create_p = ws_sub.add_parser(
        "create",
        help="Create a new working set",
        description="Create a new working set directory and register it globally.",
    )
    create_p.add_argument("name", help="Name for the new working set")
    create_p.add_argument("path", help="Root directory for the working set")
    create_p.set_defaults(func=run_create)

    # kanibako workset list (default)
    list_p = ws_sub.add_parser(
        "list",
        help="List all registered working sets (default)",
        description="Show all registered working sets.",
    )
    list_p.set_defaults(func=run_list)

    # kanibako workset delete <name> [--remove-files] [--force]
    delete_p = ws_sub.add_parser(
        "delete",
        help="Unregister a working set",
        description="Unregister a working set and optionally remove its files.",
    )
    delete_p.add_argument("name", help="Name of the working set to delete")
    delete_p.add_argument(
        "--remove-files", action="store_true",
        help="Also remove the working set directory tree",
    )
    delete_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt",
    )
    delete_p.set_defaults(func=run_delete)

    # kanibako workset add <workset> [source] [--name N]
    add_p = ws_sub.add_parser(
        "add",
        help="Add a project to a working set",
        description="Add a project to an existing working set.",
    )
    add_p.add_argument("workset", help="Name of the working set")
    add_p.add_argument(
        "source", nargs="?", default=None,
        help="Source project directory (default: current directory)",
    )
    add_p.add_argument(
        "--name", dest="project_name", default=None,
        help="Project name within the working set (default: directory basename)",
    )
    add_p.set_defaults(func=run_add)

    # kanibako workset remove <workset> <project> [--remove-files] [--force]
    remove_p = ws_sub.add_parser(
        "remove",
        help="Remove a project from a working set",
        description="Remove a project from a working set and optionally delete its files.",
    )
    remove_p.add_argument("workset", help="Name of the working set")
    remove_p.add_argument("project", help="Name of the project to remove")
    remove_p.add_argument(
        "--remove-files", action="store_true",
        help="Also remove per-project directories",
    )
    remove_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt",
    )
    remove_p.set_defaults(func=run_remove)

    # kanibako workset info <name>
    info_p = ws_sub.add_parser(
        "info",
        help="Show working set details",
        description="Show name, root, creation date, and projects for a working set.",
    )
    info_p.add_argument("name", help="Name of the working set")
    info_p.set_defaults(func=run_info)

    # kanibako workset auth <name> [shared|distinct]
    auth_p = ws_sub.add_parser(
        "auth",
        help="Show or change workset auth mode",
        description="Show or change auth mode for a working set. "
        "'shared' syncs credentials from host; 'distinct' uses per-workset credentials.",
    )
    auth_p.add_argument("name", help="Name of the working set")
    auth_p.add_argument(
        "auth_mode", nargs="?", default=None, choices=["shared", "distinct"],
        help="New auth mode (omit to show current)",
    )
    auth_p.set_defaults(func=run_auth)

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list)


def _load_std():
    """Load config and standard paths."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    return load_std_paths(config)


def run_create(args: argparse.Namespace) -> int:
    std = _load_std()
    try:
        ws = create_workset(args.name, Path(args.path), std)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Created working set '{ws.name}' at {ws.root}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    std = _load_std()
    registry = list_worksets(std)
    if not registry:
        print("No working sets registered.")
        return 0

    # Load each workset to get project count.
    rows: list[tuple[str, int, str]] = []
    for name in sorted(registry):
        root = registry[name]
        try:
            ws = load_workset(root)
            count = len(ws.projects)
        except WorksetError:
            count = 0
        rows.append((name, count, str(root)))

    print(f"{'NAME':<20} {'PROJECTS':>8}  {'ROOT'}")
    for ws_name, ws_count, ws_root in rows:
        print(f"{ws_name:<20} {ws_count:>8}  {ws_root}")
    return 0


def run_delete(args: argparse.Namespace) -> int:
    std = _load_std()
    if not args.force:
        label = "and remove files " if args.remove_files else ""
        confirm_prompt(
            f"Unregister {label}working set '{args.name}'? Type 'yes' to confirm: "
        )
    try:
        root = delete_workset(args.name, std, remove_files=args.remove_files)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Deleted working set '{args.name}' (root was {root})")
    return 0


def run_add(args: argparse.Namespace) -> int:
    import os

    std = _load_std()
    registry = list_worksets(std)
    if args.workset not in registry:
        print(f"Error: Working set '{args.workset}' is not registered.", file=sys.stderr)
        return 1

    try:
        ws = load_workset(registry[args.workset])
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    source = Path(args.source) if args.source else Path(os.getcwd())
    project_name = args.project_name or source.resolve().name

    try:
        proj = add_project(ws, project_name, source)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Added project '{proj.name}' to working set '{ws.name}'")
    return 0


def run_remove(args: argparse.Namespace) -> int:
    std = _load_std()
    registry = list_worksets(std)
    if args.workset not in registry:
        print(f"Error: Working set '{args.workset}' is not registered.", file=sys.stderr)
        return 1

    try:
        ws = load_workset(registry[args.workset])
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not args.force:
        label = "and remove files " if args.remove_files else ""
        confirm_prompt(
            f"Remove {label}project '{args.project}' from '{ws.name}'? "
            "Type 'yes' to confirm: "
        )

    try:
        proj = remove_project(ws, args.project, remove_files=args.remove_files)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Removed project '{proj.name}' from working set '{ws.name}'")
    return 0


def run_info(args: argparse.Namespace) -> int:
    std = _load_std()
    registry = list_worksets(std)
    if args.name not in registry:
        print(f"Error: Working set '{args.name}' is not registered.", file=sys.stderr)
        return 1

    try:
        ws = load_workset(registry[args.name])
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Name:     {ws.name}")
    print(f"Root:     {ws.root}")
    print(f"Created:  {ws.created}")
    print(f"Auth:     {ws.auth}")
    if ws.projects:
        print(f"Projects: {len(ws.projects)}")
        for proj in ws.projects:
            print(f"  - {proj.name}  ({proj.source_path})")
    else:
        print("Projects: (none)")
    return 0


def run_auth(args: argparse.Namespace) -> int:
    std = _load_std()
    registry = list_worksets(std)
    if args.name not in registry:
        print(f"Error: Working set '{args.name}' is not registered.", file=sys.stderr)
        return 1

    try:
        ws = load_workset(registry[args.name])
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.auth_mode is None:
        # Show current auth mode.
        print(ws.auth)
        return 0

    old_auth = ws.auth
    ws.auth = args.auth_mode
    _write_workset_toml(ws)

    if args.auth_mode == "distinct" and old_auth != "distinct":
        # Invalidate credentials in all project shells.
        from kanibako.targets import resolve_target
        try:
            target = resolve_target(None)
        except KeyError:
            target = None
        if target:
            for proj in ws.projects:
                shell_path = ws.projects_dir / proj.name / "shell"
                if shell_path.is_dir():
                    target.invalidate_credentials(shell_path)
        print(
            f"Set auth mode to 'distinct' for '{ws.name}'. "
            f"Credentials cleared in {len(ws.projects)} project(s).",
        )
    else:
        print(f"Set auth mode to '{args.auth_mode}' for '{ws.name}'.")
    return 0

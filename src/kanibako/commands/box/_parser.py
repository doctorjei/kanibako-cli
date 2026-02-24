"""Parser setup, list, info, get, and set commands for kanibako box."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kanibako.config import (
    config_file_path,
    load_config,
    read_project_meta,
    read_resource_overrides,
    remove_resource_override,
    write_project_meta,
    write_resource_override,
)
from kanibako.errors import ProjectError
from kanibako.paths import (
    ProjectLayout,
    xdg,
    iter_projects,
    iter_workset_projects,
    load_std_paths,
    resolve_any_project,
)
from kanibako.utils import short_hash

_MODE_CHOICES = ["account-centric", "decentralized", "workset"]

# Keys that box get can read.
_GET_KEYS = [
    "shell", "vault_ro", "vault_rw", "layout", "vault_enabled", "auth",
    "metadata", "project_hash", "global_shared", "local_shared", "mode",
]

# Keys that box set can write (path keys, enum keys, bool keys).
_SET_PATH_KEYS = {"shell", "vault_ro", "vault_rw"}
_SET_KEYS = _SET_PATH_KEYS | {"layout", "vault_enabled", "auth"}


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    from kanibako.commands.box._duplicate import run_duplicate
    from kanibako.commands.box._migrate import run_migrate

    p = subparsers.add_parser(
        "box",
        help="Project lifecycle commands (list, migrate, duplicate, archive, purge, restore)",
        description="Manage per-project session data: list, migrate, duplicate, archive, purge, restore.",
    )
    box_sub = p.add_subparsers(dest="box_command", metavar="COMMAND")

    # kanibako box list (default behavior)
    list_p = box_sub.add_parser(
        "list",
        help="List known projects and their status (default)",
        description="List all known kanibako projects with their hash, status, and path.",
    )
    list_p.set_defaults(func=run_list)

    # kanibako box migrate
    migrate_p = box_sub.add_parser(
        "migrate",
        help="Remap project data from old path to new path, or convert between modes",
        description=(
            "Move project session data from one path hash to another.\n"
            "Use this after moving or renaming a project directory.\n"
            "With --to, convert a project between modes (e.g. account-centric to decentralized)."
        ),
    )
    migrate_p.add_argument(
        "old_path", nargs="?", default=None,
        help="Original project directory path (for path remap), or project path (for --to)",
    )
    migrate_p.add_argument(
        "new_path", nargs="?", default=None,
        help="New project directory path (default: current working directory)",
    )
    migrate_p.add_argument(
        "--to", dest="to_mode", choices=_MODE_CHOICES, default=None,
        help="Convert project to a different mode",
    )
    migrate_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt",
    )
    migrate_p.add_argument(
        "--workset", default=None,
        help="Target workset name (required when --to workset)",
    )
    migrate_p.add_argument(
        "--name", dest="project_name", default=None,
        help="Project name in workset (default: directory basename)",
    )
    migrate_p.add_argument(
        "--in-place", action="store_true", dest="in_place",
        help="Keep workspace at current location (don't move into workset)",
    )
    migrate_p.set_defaults(func=run_migrate)

    # kanibako box duplicate
    duplicate_p = box_sub.add_parser(
        "duplicate",
        help="Duplicate a project (workspace + metadata) under a new path",
        description=(
            "Copy a project's workspace directory and kanibako metadata to a new path.\n"
            "The metadata is re-keyed under the new path's hash.\n"
            "With --to, duplicate into a different mode layout."
        ),
    )
    duplicate_p.add_argument("source_path", help="Existing project directory to duplicate")
    duplicate_p.add_argument("new_path", help="Destination path for the duplicate")
    duplicate_p.add_argument(
        "--bare", action="store_true",
        help="Copy only kanibako metadata, don't touch the workspace directory",
    )
    duplicate_p.add_argument(
        "--to", dest="to_mode", choices=_MODE_CHOICES, default=None,
        help="Duplicate into a different mode layout",
    )
    duplicate_p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation, overwrite existing data/metadata at destination",
    )
    duplicate_p.add_argument(
        "--workset", default=None,
        help="Target workset name (required when --to workset)",
    )
    duplicate_p.add_argument(
        "--name", dest="project_name", default=None,
        help="Project name in workset (default: directory basename)",
    )
    duplicate_p.set_defaults(func=run_duplicate)

    # kanibako box orphan
    orphan_p = box_sub.add_parser(
        "orphan",
        help="List orphaned projects (metadata without a workspace)",
        description="List projects whose workspace directory no longer exists.",
    )
    orphan_p.set_defaults(func=run_orphan)

    # kanibako box info
    info_p = box_sub.add_parser(
        "info",
        help="Show project details",
        description="Show project mode, paths, and status for a kanibako project.",
    )
    info_p.add_argument("path", nargs="?", default=None, help="Project directory (default: cwd)")
    info_p.set_defaults(func=run_info)

    # kanibako box get <key>
    get_p = box_sub.add_parser(
        "get",
        help="Get a project setting value",
        description="Print the current value of a project setting.",
    )
    get_p.add_argument("key", choices=_GET_KEYS, help="Setting key to read")
    get_p.add_argument("-p", "--project", default=None, help="Project directory (default: cwd)")
    get_p.set_defaults(func=run_get)

    # kanibako box set <key> <value>
    set_p = box_sub.add_parser(
        "set",
        help="Override a project setting value",
        description="Set or override a project setting in project.toml.",
    )
    set_p.add_argument("key", choices=sorted(_SET_KEYS), help="Setting key to write")
    set_p.add_argument("value", help="New value for the setting")
    set_p.add_argument("-p", "--project", default=None, help="Project directory (default: cwd)")
    set_p.set_defaults(func=run_set)

    # kanibako box resource {list,set,unset}
    resource_p = box_sub.add_parser(
        "resource",
        help="Manage per-project resource scope overrides",
        description="View and override how agent resources are shared across projects.",
    )
    resource_sub = resource_p.add_subparsers(dest="resource_command", metavar="COMMAND")

    res_list_p = resource_sub.add_parser(
        "list", help="List resource scopes (default and effective)",
    )
    res_list_p.add_argument("-p", "--project", default=None, help="Project directory (default: cwd)")
    res_list_p.set_defaults(func=run_resource_list)

    res_set_p = resource_sub.add_parser(
        "set", help="Override a resource scope",
    )
    res_set_p.add_argument("path", help="Resource path (e.g. plugins/)")
    res_set_p.add_argument("scope", choices=["shared", "project", "seeded"], help="Scope to set")
    res_set_p.add_argument("-p", "--project", default=None, help="Project directory (default: cwd)")
    res_set_p.set_defaults(func=run_resource_set)

    res_unset_p = resource_sub.add_parser(
        "unset", help="Remove a resource scope override",
    )
    res_unset_p.add_argument("path", help="Resource path to unset")
    res_unset_p.add_argument("-p", "--project", default=None, help="Project directory (default: cwd)")
    res_unset_p.set_defaults(func=run_resource_unset)

    resource_p.set_defaults(func=run_resource_list)

    # Reuse existing subcommand modules under box.
    from kanibako.commands.archive import add_parser as add_archive_parser
    from kanibako.commands.clean import add_parser as add_purge_parser
    from kanibako.commands.restore import add_parser as add_restore_parser

    add_archive_parser(box_sub)
    add_purge_parser(box_sub)
    add_restore_parser(box_sub)

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list)


def run_list(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    projects = iter_projects(std, config)
    ws_data = iter_workset_projects(std, config)

    if not projects and not ws_data:
        print("No known projects.")
        return 0

    if projects:
        print(f"{'HASH':<10} {'STATUS':<10} {'PATH'}")
        for settings_path, project_path in projects:
            h8 = short_hash(settings_path.name)
            if project_path is None:
                status = "unknown"
                label = "(no breadcrumb)"
            elif project_path.is_dir():
                status = "ok"
                label = str(project_path)
            else:
                status = "missing"
                label = str(project_path)
            print(f"{h8:<10} {status:<10} {label}")

    for ws_name, ws, project_list in ws_data:
        print()
        print(f"Working set: {ws_name} ({ws.root})")
        if project_list:
            print(f"  {'NAME':<18} {'STATUS':<10} {'SOURCE'}")
            for proj_name, status in project_list:
                # Look up source_path from workset projects.
                source = ""
                for p in ws.projects:
                    if p.name == proj_name:
                        source = str(p.source_path)
                        break
                print(f"  {proj_name:<18} {status:<10} {source}")
        else:
            print("  (no projects)")

    return 0


def run_orphan(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    projects = iter_projects(std, config)
    ws_data = iter_workset_projects(std, config)

    # Account-centric orphans: path missing or no breadcrumb.
    ac_orphans = []
    for metadata_path, project_path in projects:
        if project_path is None or not project_path.is_dir():
            ac_orphans.append((metadata_path, project_path))

    # Workset orphans: workspace directory missing but project data exists.
    ws_orphans: list[tuple[str, str]] = []
    for ws_name, ws, project_list in ws_data:
        for proj_name, status in project_list:
            if status == "missing":
                ws_orphans.append((ws_name, proj_name))

    if not ac_orphans and not ws_orphans:
        print("No orphaned projects found.")
        return 0

    if ac_orphans:
        print(f"{'HASH':<10} {'PATH'}")
        for metadata_path, project_path in ac_orphans:
            h8 = short_hash(metadata_path.name)
            label = str(project_path) if project_path else "(no breadcrumb)"
            print(f"{h8:<10} {label}")

    if ws_orphans:
        if ac_orphans:
            print()
        print(f"{'WORKSET':<18} {'PROJECT'}")
        for ws_name, proj_name in ws_orphans:
            print(f"{ws_name:<18} {proj_name}")

    total = len(ac_orphans) + len(ws_orphans)
    print(f"\n{total} orphaned project(s).")
    print("Use 'kanibako box migrate' to remap, or 'kanibako box purge' to remove.")
    return 0


def run_info(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    try:
        proj = resolve_any_project(std, config, project_dir=args.path, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not proj.metadata_path.is_dir():
        print(f"Error: No project data found for {proj.project_path}", file=sys.stderr)
        return 1

    print(f"Mode:      {proj.mode.value}")
    print(f"Project:   {proj.project_path}")
    print(f"Hash:      {short_hash(proj.project_hash)}")
    print(f"Metadata:  {proj.metadata_path}")
    print(f"Shell:     {proj.shell_path}")
    print(f"Vault RO:  {proj.vault_ro_path}")
    print(f"Vault RW:  {proj.vault_rw_path}")
    if proj.global_shared_path:
        print(f"Shared:    {proj.global_shared_path}")
    if proj.local_shared_path:
        print(f"Local:     {proj.local_shared_path}")

    lock_file = proj.metadata_path / ".kanibako.lock"
    if lock_file.exists():
        print(f"Lock:      ACTIVE ({lock_file})")
    else:
        print("Lock:      none")

    return 0


def _validate_path_override(key: str, value: str) -> Path:
    """Validate a path override value. Returns a resolved Path.

    Rejects relative paths and paths whose parent directory doesn't exist.
    """
    p = Path(value)
    if not p.is_absolute():
        raise ValueError(f"{key}: path must be absolute, got '{value}'")
    if not p.parent.exists():
        raise ValueError(f"{key}: parent directory does not exist: {p.parent}")
    return p


def run_get(args: argparse.Namespace) -> int:
    """Print the current value of a project setting."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    project_dir = getattr(args, "project", None)
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    project_toml = proj.metadata_path / "project.toml"
    meta = read_project_meta(project_toml)
    if meta is None:
        print("Error: No project metadata found. Initialize the project first.", file=sys.stderr)
        return 1

    key = args.key
    if key in meta:
        value = meta[key]
        # vault_enabled is a bool, print as lowercase string.
        if isinstance(value, bool):
            print(str(value).lower())
        else:
            print(value)
    else:
        print(f"Error: Unknown key '{key}'", file=sys.stderr)
        return 1

    return 0


def run_set(args: argparse.Namespace) -> int:
    """Set or override a project setting in project.toml."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    project_dir = getattr(args, "project", None)
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    project_toml = proj.metadata_path / "project.toml"
    meta = read_project_meta(project_toml)
    if meta is None:
        print("Error: No project metadata found. Initialize the project first.", file=sys.stderr)
        return 1

    key = args.key
    value = args.value

    # Validate based on key type.
    try:
        if key in _SET_PATH_KEYS:
            _validate_path_override(key, value)
        elif key == "layout":
            try:
                ProjectLayout(value)
            except ValueError:
                valid = ", ".join(e.value for e in ProjectLayout)
                raise ValueError(f"layout: invalid value '{value}'. Valid: {valid}")
        elif key == "vault_enabled":
            if value.lower() in ("true", "1", "yes"):
                value = True
            elif value.lower() in ("false", "0", "no"):
                value = False
            else:
                raise ValueError(f"vault_enabled: expected true/false, got '{value}'")
        elif key == "auth":
            if value not in ("shared", "distinct"):
                raise ValueError(f"auth: expected shared/distinct, got '{value}'")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Update the meta dict and write back.
    meta[key] = value
    write_project_meta(
        project_toml,
        mode=meta["mode"],
        layout=meta["layout"],
        workspace=meta["workspace"],
        shell=meta["shell"],
        vault_ro=meta["vault_ro"],
        vault_rw=meta["vault_rw"],
        vault_enabled=meta["vault_enabled"],
        auth=meta["auth"],
        metadata=meta.get("metadata", ""),
        project_hash=meta.get("project_hash", ""),
        global_shared=meta.get("global_shared", ""),
        local_shared=meta.get("local_shared", ""),
    )

    print(f"{key} = {value}")
    return 0


def _resolve_target_for_project(proj):
    """Resolve the target for a project (for resource_mappings)."""
    from kanibako.targets import resolve_target
    try:
        return resolve_target(None)
    except Exception:
        return None


def run_resource_list(args: argparse.Namespace) -> int:
    """List resource scopes (default and effective) for a project."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    project_dir = getattr(args, "project", None)
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    target = _resolve_target_for_project(proj)
    if target is None:
        print("Error: No target detected. Cannot list resource mappings.", file=sys.stderr)
        return 1

    mappings = target.resource_mappings()
    if not mappings:
        print("No resource mappings defined for this target.")
        return 0

    project_toml = proj.metadata_path / "project.toml"
    overrides = read_resource_overrides(project_toml)

    print(f"{'PATH':<25} {'DEFAULT':<10} {'EFFECTIVE'}")
    for m in mappings:
        default_scope = m.scope.value
        override = overrides.get(m.path)
        effective = override if override else default_scope
        marker = " *" if override else ""
        print(f"{m.path:<25} {default_scope:<10} {effective}{marker}")

    if overrides:
        print("\n* = overridden")

    return 0


def run_resource_set(args: argparse.Namespace) -> int:
    """Set a resource scope override."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    project_dir = getattr(args, "project", None)
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    target = _resolve_target_for_project(proj)
    if target is None:
        print("Error: No target detected.", file=sys.stderr)
        return 1

    # Validate path is in resource_mappings.
    mappings = target.resource_mappings()
    valid_paths = {m.path for m in mappings}
    if args.path not in valid_paths:
        print(f"Error: '{args.path}' is not a valid resource path.", file=sys.stderr)
        print(f"Valid paths: {', '.join(sorted(valid_paths))}", file=sys.stderr)
        return 1

    project_toml = proj.metadata_path / "project.toml"
    write_resource_override(project_toml, args.path, args.scope)
    print(f"{args.path} = {args.scope}")
    return 0


def run_resource_unset(args: argparse.Namespace) -> int:
    """Remove a resource scope override."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    project_dir = getattr(args, "project", None)
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    project_toml = proj.metadata_path / "project.toml"
    if remove_resource_override(project_toml, args.path):
        print(f"Removed override for {args.path}")
    else:
        print(f"No override found for {args.path}")
    return 0

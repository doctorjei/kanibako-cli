"""Parser setup, list, info, get, and set commands for kanibako box."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from kanibako.config import (
    config_file_path,
    load_config,
    load_merged_config,
    read_project_meta,
    read_resource_overrides,
    read_target_settings,
    remove_resource_override,
    remove_target_setting,
    write_project_config,
    write_project_meta,
    write_resource_override,
    write_target_setting,
)
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError, ProjectError
from kanibako.names import read_names, register_name, unregister_name
from kanibako.paths import (
    ProjectLayout,
    ProjectMode,
    xdg,
    iter_projects,
    iter_workset_projects,
    load_std_paths,
    resolve_any_project,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.targets import resolve_target
from kanibako.utils import container_name_for, short_hash, write_project_gitignore

_MODE_CHOICES = ["local", "standalone", "workset"]

# Keys that box get can read.
_GET_KEYS = [
    "name", "shell", "vault_ro", "vault_rw", "layout", "vault_enabled", "auth",
    "metadata", "project_hash", "global_shared", "local_shared", "mode",
]

# Keys that box set can write (path keys, enum keys, bool keys).
_SET_PATH_KEYS = {"shell", "vault_ro", "vault_rw"}
_SET_KEYS = _SET_PATH_KEYS | {"layout", "vault_enabled", "auth", "name"}


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    from kanibako.commands.box._duplicate import run_duplicate
    from kanibako.commands.box._migrate import run_migrate

    p = subparsers.add_parser(
        "box",
        help="Project lifecycle commands (create, list, migrate, duplicate, archive, purge, restore)",
        description="Manage per-project session data: create, list, migrate, duplicate, archive, purge, restore.",
    )
    box_sub = p.add_subparsers(dest="box_command", metavar="COMMAND")

    # kanibako box create [path] [--name NAME] [--standalone] [--image IMAGE]
    #                     [--no-vault] [--distinct-auth]
    create_p = box_sub.add_parser(
        "create",
        help="Create a new kanibako project",
        description="Create a new kanibako project in the current or given directory.",
    )
    create_p.add_argument(
        "path", nargs="?", default=None,
        help="Project directory (default: cwd). Created if it doesn't exist.",
    )
    create_p.add_argument(
        "--name", default=None,
        help="Project name override (default: auto-assigned from directory name)",
    )
    create_p.add_argument(
        "--standalone", action="store_true",
        help="Use standalone mode (all state inside the project directory)",
    )
    create_p.add_argument(
        "-i", "--image", default=None,
        help="Container image to use for this project",
    )
    create_p.add_argument(
        "--no-vault", action="store_true",
        help="Disable vault directories (shared read-only and read-write mounts)",
    )
    create_p.add_argument(
        "--distinct-auth", action="store_true",
        help="Use distinct credentials (no sync from host)",
    )
    create_p.set_defaults(func=run_create)

    # kanibako box list (default behavior)
    list_p = box_sub.add_parser(
        "list",
        aliases=["ls"],
        help="List known projects and their status (default)",
        description="List all known kanibako projects with their hash, status, and path.",
    )
    list_p.add_argument(
        "--all", "-a", action="store_true", dest="show_all",
        help="Include orphaned projects in the listing",
    )
    list_p.add_argument(
        "--orphan", action="store_true",
        help="Show only orphaned projects (missing workspace)",
    )
    list_p.add_argument(
        "-q", "--quiet", action="store_true",
        help="Output project names only, one per line",
    )
    list_p.set_defaults(func=run_list)

    # kanibako box migrate
    migrate_p = box_sub.add_parser(
        "migrate",
        help="Remap project data from old path to new path, or convert between modes",
        description=(
            "Move project session data from one path hash to another.\n"
            "Use this after moving or renaming a project directory.\n"
            "With --to, convert a project between modes (e.g. local to standalone)."
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

    # kanibako box rm (was: forget)
    rm_p = box_sub.add_parser(
        "rm",
        aliases=["delete"],
        help="Unregister a project (optionally purge its metadata)",
        description=(
            "Remove a project from names.toml without touching the workspace.\n"
            "With --purge, also delete kanibako metadata (shell config, project.toml, vault symlinks, logs)."
        ),
    )
    rm_p.add_argument(
        "target",
        help="Project name or workspace path to remove",
    )
    rm_p.add_argument(
        "--purge", action="store_true",
        help="Also delete kanibako metadata for this project",
    )
    rm_p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompt (only relevant with --purge)",
    )
    rm_p.set_defaults(func=run_rm)

    # kanibako box info / inspect
    info_p = box_sub.add_parser(
        "info",
        aliases=["inspect"],
        help="Show project details, status, and configuration",
        description=(
            "Show per-project status: mode, paths, container state, image, and credentials.\n"
            "Replaces the top-level 'status' command."
        ),
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

    # kanibako box settings {list,get,set,unset}
    settings_p = box_sub.add_parser(
        "settings",
        help="Manage per-project target setting overrides",
        description="View and override target plugin settings (model, access, etc.).",
    )
    settings_sub = settings_p.add_subparsers(dest="settings_command", metavar="COMMAND")

    set_list_p = settings_sub.add_parser(
        "list", help="List target settings (default and effective)",
    )
    set_list_p.add_argument("-p", "--project", default=None, help="Project directory (default: cwd)")
    set_list_p.set_defaults(func=run_settings_list)

    set_get_p = settings_sub.add_parser(
        "get", help="Get a target setting value",
    )
    set_get_p.add_argument("key", help="Setting key (e.g. model)")
    set_get_p.add_argument("-p", "--project", default=None, help="Project directory (default: cwd)")
    set_get_p.set_defaults(func=run_settings_get)

    set_set_p = settings_sub.add_parser(
        "set", help="Override a target setting",
    )
    set_set_p.add_argument("key", help="Setting key (e.g. model)")
    set_set_p.add_argument("value", help="New value")
    set_set_p.add_argument("-p", "--project", default=None, help="Project directory (default: cwd)")
    set_set_p.set_defaults(func=run_settings_set)

    set_unset_p = settings_sub.add_parser(
        "unset", help="Remove a target setting override",
    )
    set_unset_p.add_argument("key", help="Setting key to unset")
    set_unset_p.add_argument("-p", "--project", default=None, help="Project directory (default: cwd)")
    set_unset_p.set_defaults(func=run_settings_unset)

    settings_p.set_defaults(func=run_settings_list)

    # Reuse existing subcommand modules under box.
    from kanibako.commands.archive import add_parser as add_archive_parser
    from kanibako.commands.clean import add_parser as add_purge_parser
    from kanibako.commands.restore import add_parser as add_restore_parser

    add_archive_parser(box_sub)
    add_purge_parser(box_sub)
    add_restore_parser(box_sub)

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list)


def run_create(args: argparse.Namespace) -> int:
    """Create a new kanibako project (replaces ``kanibako init``)."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    vault_enabled = not getattr(args, "no_vault", False)
    auth = "distinct" if getattr(args, "distinct_auth", False) else None
    project_dir = args.path

    # Create directory if it doesn't exist.
    if project_dir is not None:
        target = Path(project_dir)
        if not target.exists():
            target.mkdir(parents=True)

    if args.standalone:
        proj = resolve_standalone_project(
            std, config, project_dir, initialize=True,
            vault_enabled=vault_enabled, auth=auth,
        )
    else:
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
            vault_enabled=vault_enabled if not vault_enabled else None,
        )

    if not proj.is_new:
        print(
            f"Error: project already initialized in {proj.project_path}",
            file=sys.stderr,
        )
        return 1

    # Persist image setting.
    image = args.image or config.container_image
    project_toml = proj.metadata_path / "project.toml"
    write_project_config(project_toml, image)

    # Write .gitignore for standalone projects only.
    if args.standalone:
        write_project_gitignore(proj.project_path)

    mode = "standalone" if args.standalone else "local"
    print(f"Created {mode} project in {proj.project_path}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    show_all = getattr(args, "show_all", False)
    orphan_only = getattr(args, "orphan", False)
    quiet = getattr(args, "quiet", False)

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    projects = iter_projects(std, config)
    ws_data = iter_workset_projects(std, config)

    if orphan_only:
        return _list_orphans(projects, ws_data, std, quiet)

    if not projects and not ws_data:
        if not quiet:
            print("No known projects.")
        return 0

    if projects:
        # Build a reverse lookup from path → name using names.toml.
        names_data = read_names(std.data_path)
        path_to_name: dict[str, str] = {v: k for k, v in names_data["projects"].items()}

        if not quiet:
            print(f"{'NAME':<18} {'STATUS':<10} {'PATH'}")
        for settings_path, project_path in projects:
            # Directory name is now the project name (or hash for legacy).
            dir_name = settings_path.name
            proj_name = path_to_name.get(str(project_path), dir_name) if project_path else dir_name
            if project_path is None:
                status = "unknown"
                label = "(no breadcrumb)"
            elif project_path.is_dir():
                status = "ok"
                label = str(project_path)
            else:
                status = "missing"
                label = str(project_path)

            # Skip orphans unless --all is given.
            if status in ("missing", "unknown") and not show_all:
                continue

            if quiet:
                print(proj_name)
            else:
                print(f"{proj_name:<18} {status:<10} {label}")

    for ws_name, ws, project_list in ws_data:
        if quiet:
            for proj_name, status in project_list:
                if status == "missing" and not show_all:
                    continue
                print(proj_name)
        else:
            print()
            print(f"Workset: {ws_name} ({ws.root})")
            if project_list:
                print(f"  {'NAME':<18} {'STATUS':<10} {'SOURCE'}")
                for proj_name, status in project_list:
                    if status == "missing" and not show_all:
                        continue
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


def _list_orphans(
    projects: list,
    ws_data: list,
    std,
    quiet: bool,
) -> int:
    """List only orphaned projects (--orphan flag handler)."""
    # Local mode orphans: path missing or no breadcrumb.
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
        if not quiet:
            print("No orphaned projects found.")
        return 0

    names_data = read_names(std.data_path)
    path_to_name: dict[str, str] = {v: k for k, v in names_data["projects"].items()}

    if ac_orphans:
        if not quiet:
            print(f"{'NAME':<18} {'PATH'}")
        for metadata_path, project_path in ac_orphans:
            dir_name = metadata_path.name
            proj_name = path_to_name.get(str(project_path), dir_name) if project_path else dir_name
            if quiet:
                print(proj_name)
            else:
                label = str(project_path) if project_path else "(no breadcrumb)"
                print(f"{proj_name:<18} {label}")

    if ws_orphans:
        if not quiet:
            if ac_orphans:
                print()
            print(f"{'WORKSET':<18} {'PROJECT'}")
        for ws_name, proj_name in ws_orphans:
            if quiet:
                print(proj_name)
            else:
                print(f"{ws_name:<18} {proj_name}")

    if not quiet:
        total = len(ac_orphans) + len(ws_orphans)
        print(f"\n{total} orphaned project(s).")
        print("Use 'kanibako box migrate' to remap, or 'kanibako box rm' to remove.")
    return 0


def run_rm(args: argparse.Namespace) -> int:
    """Unregister a project from names.toml, optionally purging metadata."""
    import shutil

    from kanibako.names import lookup_by_path
    from kanibako.paths import (
        _remove_human_vault_symlink,
        _remove_project_vault_symlink,
    )
    from kanibako.utils import confirm_prompt

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    target = args.target
    names = read_names(std.data_path)

    # Resolve target: try as a registered name first, then as a path.
    name: str | None = None
    section: str | None = None
    path: str | None = None

    for sec in ("projects", "worksets"):
        if target in names[sec]:
            name = target
            section = sec
            path = names[sec][target]
            break

    if name is None:
        # Try as a path (reverse lookup).
        result = lookup_by_path(std.data_path, target)
        if result is not None:
            name, section = result
            path = names[section][name]

    if name is None or section is None:
        print(f"Error: '{target}' is not a registered project or workset.", file=sys.stderr)
        return 1

    kind = "workset" if section == "worksets" else "project"
    print(f"Removing {kind}: {name} ({path})")

    # Unregister from names.toml.
    unregister_name(std.data_path, name, section=section)
    print(f"Removed '{name}' from names.toml")

    if args.purge:
        metadata_dir = std.data_path / "boxes" / name

        if metadata_dir.is_dir():
            if not args.force:
                from kanibako.errors import UserCancelled
                print()
                try:
                    confirm_prompt(
                        f"Delete metadata at {metadata_dir}? This cannot be undone.\n"
                        "Type 'yes' to confirm: "
                    )
                except UserCancelled:
                    print("Aborted (name was already unregistered).")
                    return 2

            # Clean up vault symlinks before removing metadata.
            vault_dir = std.data_path / config.paths_vault
            _remove_human_vault_symlink(vault_dir, metadata_dir / "vault")
            if path:
                _remove_project_vault_symlink(Path(path))

            shutil.rmtree(metadata_dir)
            print(f"Removed metadata: {metadata_dir}")

            # Remove helper log directory if present.
            log_dir = std.data_path / "logs" / name
            if log_dir.is_dir():
                shutil.rmtree(log_dir)
                print(f"Removed logs: {log_dir}")
        else:
            print(f"No metadata directory found at {metadata_dir}")
    else:
        # Hint about --purge when metadata still exists.
        metadata_dir = std.data_path / "boxes" / name
        if metadata_dir.is_dir():
            print(
                f"Metadata still present at {metadata_dir}. "
                f"Run 'kanibako box rm {name} --purge' to delete."
            )

    return 0


def _format_credential_age(creds_path: Path) -> str:
    """Return a human-readable age string for a credentials file, or 'n/a'."""
    if not creds_path.is_file():
        return "n/a (no credentials file)"
    try:
        mtime = creds_path.stat().st_mtime
    except OSError:
        return "n/a (unreadable)"
    dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    delta = now - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        age = f"{total_seconds}s ago"
    elif total_seconds < 3600:
        age = f"{total_seconds // 60}m ago"
    elif total_seconds < 86400:
        age = f"{total_seconds // 3600}h ago"
    else:
        age = f"{total_seconds // 86400}d ago"
    return f"{age} ({dt.strftime('%Y-%m-%d %H:%M:%S UTC')})"


def _check_container_running(proj) -> tuple[bool, str]:
    """Check if a kanibako container is running for this project.

    Accepts a ``ProjectPaths`` (or duck-typed equivalent).
    Returns ``(is_running, detail_string)``.
    """
    container_name = container_name_for(proj)
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        return False, "unknown (no container runtime)"
    containers = runtime.list_running()
    for name, image, status in containers:
        if name == container_name:
            return True, f"running ({container_name}: {image})"
    # Check for stopped persistent container
    if runtime.container_exists(container_name):
        return False, f"stopped persistent ({container_name})"
    return False, f"not running ({container_name})"


def run_info(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    try:
        std = load_std_paths(config)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    project_dir = getattr(args, "path", None)
    raw = project_dir or os.getcwd()
    raw_dir = Path(raw).resolve()

    if not raw_dir.is_dir():
        print(f"Error: directory does not exist: {raw_dir}", file=sys.stderr)
        return 1

    # Detect mode and resolve project paths (without initializing).
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Check if the project has been initialized (has metadata on disk).
    has_data = proj.metadata_path.is_dir()

    if not has_data:
        print(f"No project data found for: {proj.project_path}")
        print()
        if proj.mode == ProjectMode.local:
            print("This directory has not been used with kanibako yet.")
            print("Start a session with 'kanibako start', or create with:")
            print("  kanibako box create")
        else:
            print("This directory has not been initialized.")
        return 1

    # Load merged config for image info.
    project_toml = proj.metadata_path / "project.toml"
    merged = load_merged_config(
        config_file,
        project_toml if project_toml.exists() else None,
    )

    # Gather status info.
    lock_file = proj.metadata_path / ".kanibako.lock"
    lock_held = lock_file.exists()

    container_running, container_detail = _check_container_running(proj)

    # Resolve target for credential check path
    try:
        target = resolve_target(merged.target_name or None)
        creds_file = target.credential_check_path(proj.shell_path)
    except (KeyError, Exception):
        creds_file = None
    cred_age = _format_credential_age(creds_file) if creds_file else "n/a (no target)"

    # Display mode name with dashes for readability.
    mode_display = proj.mode.value.replace("_", "-")

    # Format output.
    rows: list[tuple[str, str]] = [
        ("Name", proj.name or "(unnamed)"),
        ("Mode", mode_display),
        ("Project", str(proj.project_path)),
        ("Hash", short_hash(proj.project_hash)),
        ("Metadata", str(proj.metadata_path)),
        ("Shell", str(proj.shell_path)),
        ("Vault RO", str(proj.vault_ro_path)),
        ("Vault RW", str(proj.vault_rw_path)),
    ]
    if proj.global_shared_path:
        rows.append(("Shared", str(proj.global_shared_path)))
    if proj.local_shared_path:
        rows.append(("Local", str(proj.local_shared_path)))
    rows.extend([
        ("Image", merged.container_image),
        ("Lock", "ACTIVE" if lock_held else "none"),
        ("Container", container_detail),
        ("Credentials", cred_age),
    ])

    # Compute alignment width from longest label.
    label_width = max(len(label) for label, _ in rows) + 1  # +1 for colon
    for label, value in rows:
        print(f"  {label + ':':<{label_width}}  {value}")

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


def _set_name(std, proj, meta, project_toml, new_name: str) -> int:
    """Handle ``box set name <new-name>``: validate, update names.toml + project.toml."""
    old_name = meta.get("name", "")

    if new_name == old_name:
        print(f"name = {new_name} (unchanged)")
        return 0

    # Check uniqueness across both sections.
    names = read_names(std.data_path)
    all_names = set(names["projects"]) | set(names["worksets"])
    if new_name in all_names:
        print(f"Error: Name '{new_name}' is already in use.", file=sys.stderr)
        return 1

    # Update names.toml: unregister old, register new.
    if old_name:
        unregister_name(std.data_path, old_name)
    workspace = meta.get("workspace", str(proj.project_path))
    register_name(std.data_path, new_name, workspace)

    # Update project.toml.
    meta["name"] = new_name
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
        name=new_name,
    )

    print(f"name = {new_name}")
    return 0


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
        if key == "name":
            return _set_name(std, proj, meta, project_toml, value)
        elif key in _SET_PATH_KEYS:
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
        name=meta.get("name", ""),
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


# ---------------------------------------------------------------------------
# box settings {list, get, set, unset}
# ---------------------------------------------------------------------------

def _resolve_project_and_target(args):
    """Resolve project and target for settings commands.

    Returns (proj, target, project_toml) or prints an error and returns None.
    """
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    project_dir = getattr(args, "project", None)
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return None

    target = _resolve_target_for_project(proj)
    if target is None:
        print("Error: No target detected.", file=sys.stderr)
        return None

    project_toml = proj.metadata_path / "project.toml"
    return proj, target, project_toml


def run_settings_list(args: argparse.Namespace) -> int:
    """List target settings (default, effective, and source)."""
    result = _resolve_project_and_target(args)
    if result is None:
        return 1
    proj, target, project_toml = result

    from kanibako.agents import load_agent_config
    from kanibako.agents import agent_toml_path
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    agent_cfg_path = agent_toml_path(std.data_path, target.name, config.paths_agents)
    agent_cfg = load_agent_config(agent_cfg_path)

    descriptors = target.setting_descriptors()
    if not descriptors:
        print("No settings defined for this target.")
        return 0

    overrides = read_target_settings(project_toml)

    print(f"{'KEY':<15} {'DEFAULT':<15} {'EFFECTIVE':<15} {'SOURCE'}")
    for d in descriptors:
        default = d.default
        project_override = overrides.get(d.key)
        agent_value = agent_cfg.state.get(d.key)

        if project_override is not None:
            effective = project_override
            source = "project"
        elif agent_value is not None:
            effective = agent_value
            source = "agent"
        else:
            effective = default
            source = "default"

        print(f"{d.key:<15} {default:<15} {effective:<15} {source}")

    return 0


def run_settings_get(args: argparse.Namespace) -> int:
    """Get the effective value of a target setting."""
    result = _resolve_project_and_target(args)
    if result is None:
        return 1
    proj, target, project_toml = result

    key = args.key
    descriptors = {d.key: d for d in target.setting_descriptors()}
    if key not in descriptors:
        print(f"Error: Unknown setting '{key}'.", file=sys.stderr)
        valid = ", ".join(sorted(descriptors))
        if valid:
            print(f"Valid settings: {valid}", file=sys.stderr)
        return 1

    from kanibako.agents import load_agent_config
    from kanibako.agents import agent_toml_path
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    agent_cfg_path = agent_toml_path(std.data_path, target.name, config.paths_agents)
    agent_cfg = load_agent_config(agent_cfg_path)

    overrides = read_target_settings(project_toml)
    d = descriptors[key]

    project_override = overrides.get(key)
    if project_override is not None:
        print(project_override)
    elif key in agent_cfg.state:
        print(agent_cfg.state[key])
    else:
        print(d.default)

    return 0


def run_settings_set(args: argparse.Namespace) -> int:
    """Set a per-project target setting override."""
    result = _resolve_project_and_target(args)
    if result is None:
        return 1
    proj, target, project_toml = result

    key = args.key
    value = args.value
    descriptors = {d.key: d for d in target.setting_descriptors()}

    if key not in descriptors:
        print(f"Error: Unknown setting '{key}'.", file=sys.stderr)
        valid = ", ".join(sorted(descriptors))
        if valid:
            print(f"Valid settings: {valid}", file=sys.stderr)
        return 1

    d = descriptors[key]
    if d.choices and value not in d.choices:
        print(
            f"Error: Invalid value '{value}' for '{key}'. "
            f"Valid: {', '.join(d.choices)}",
            file=sys.stderr,
        )
        return 1

    write_target_setting(project_toml, key, value)
    print(f"{key} = {value}")
    return 0


def run_settings_unset(args: argparse.Namespace) -> int:
    """Remove a per-project target setting override."""
    result = _resolve_project_and_target(args)
    if result is None:
        return 1
    proj, target, project_toml = result

    if remove_target_setting(project_toml, args.key):
        print(f"Removed override for {args.key}")
    else:
        print(f"No override found for {args.key}")
    return 0

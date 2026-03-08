"""Parser setup, list, info, config, and lifecycle commands for kanibako box."""

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
    write_project_config,
)
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError, ProjectError
from kanibako.names import read_names, unregister_name
from kanibako.paths import (
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


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    from kanibako.commands.box._duplicate import run_duplicate
    from kanibako.commands.box._migrate import run_migrate

    p = subparsers.add_parser(
        "box",
        help="Project lifecycle commands (create, list, migrate, duplicate, archive, extract, purge)",
        description="Manage per-project session data: create, list, migrate, duplicate, archive, extract, purge.",
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

    # kanibako box config [project] [key[=value]] [--effective] [--reset KEY]
    #                     [--all] [--force] [--local]
    config_p = box_sub.add_parser(
        "config",
        help="View or modify project configuration",
        description=(
            "Unified config interface for project settings.\n\n"
            "  box config                       show overrides for cwd project\n"
            "  box config myproj                show overrides for named project\n"
            "  box config --effective           show resolved values\n"
            "  box config model                 get the value of 'model'\n"
            "  box config model=sonnet          set 'model' to 'sonnet'\n"
            "  box config env.MY_VAR=hello      set env var\n"
            "  box config resource.plugins=/p   set resource path\n"
            "  box config --reset model         reset one key\n"
            "  box config --reset --all         reset all overrides\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_p.add_argument(
        "args", nargs="*", default=[],
        help="[project] [key[=value]]",
    )
    config_p.add_argument(
        "--effective", action="store_true",
        help="Show resolved values including inherited defaults",
    )
    config_p.add_argument(
        "--reset", metavar="KEY", nargs="?", const="__ALL__", default=None,
        help="Remove override for KEY (or all overrides with --all)",
    )
    config_p.add_argument(
        "--all", action="store_true", dest="reset_all",
        help="Reset all overrides (only valid with --reset)",
    )
    config_p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompts",
    )
    config_p.add_argument(
        "--local", action="store_true",
        help="Set resource to project-isolated (resource keys only)",
    )
    config_p.set_defaults(func=run_config)

    # kanibako box ps [--all] [-q/--quiet]
    ps_p = box_sub.add_parser(
        "ps",
        help="List running kanibako containers",
        description="List running kanibako containers with their project name, image, and status.",
    )
    ps_p.add_argument(
        "--all", "-a", action="store_true", dest="show_all",
        help="Include stopped containers",
    )
    ps_p.add_argument(
        "-q", "--quiet", action="store_true",
        help="Output container names only, one per line",
    )
    ps_p.set_defaults(func=run_ps)

    # kanibako box move [project] <dest>
    move_p = box_sub.add_parser(
        "move",
        help="Relocate a project workspace to a new directory",
        description=(
            "Move a project's workspace directory to a new location.\n"
            "Updates names.toml and recreates vault symlinks.\n"
            "Cannot move projects that are inside a workset."
        ),
    )
    move_p.add_argument(
        "args", nargs="+", metavar="ARG",
        help="[project] <dest>  — project name/path (optional if cwd) and destination",
    )
    move_p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompt",
    )
    move_p.set_defaults(func=run_move)

    # Reuse existing subcommand modules under box.
    from kanibako.commands.archive import add_parser as add_archive_parser
    from kanibako.commands.clean import add_parser as add_purge_parser
    from kanibako.commands.restore import add_parser as add_extract_parser
    from kanibako.commands.start import add_start_parser as _add_start_parser
    from kanibako.commands.start import add_shell_parser as _add_shell_parser
    from kanibako.commands.stop import add_parser as _add_stop_parser

    from kanibako.commands.vault_cmd import add_vault_subparser

    add_archive_parser(box_sub)
    add_purge_parser(box_sub)
    add_extract_parser(box_sub)
    add_vault_subparser(box_sub)

    # Register start, shell, stop as box subcommands (delegates to start.py/stop.py).
    _add_start_parser(box_sub)
    _add_shell_parser(box_sub)
    _add_stop_parser(box_sub)

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


def run_ps(args: argparse.Namespace) -> int:
    """List running (or all) kanibako containers with project cross-reference."""
    show_all = getattr(args, "show_all", False)
    quiet = getattr(args, "quiet", False)

    try:
        runtime = ContainerRuntime()
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if show_all:
        containers = runtime.list_all()
    else:
        containers = runtime.list_running()

    if not containers:
        if not quiet:
            label = "kanibako containers" if show_all else "running kanibako containers"
            print(f"No {label} found.")
        return 0

    # Build reverse lookup: container name → project name from names.toml.
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    try:
        std = load_std_paths(config)
        names_data = read_names(std.data_path)
        # Container names are "kanibako-{project_name}" for local projects.
        name_to_project: dict[str, str] = {}
        for proj_name in names_data["projects"]:
            name_to_project[f"kanibako-{proj_name}"] = proj_name
    except Exception:
        name_to_project = {}

    if quiet:
        for cname, _image, _status in containers:
            proj_name = name_to_project.get(cname, cname)
            print(proj_name)
    else:
        print(f"{'PROJECT':<20} {'STATUS':<22} {'IMAGE'}")
        for cname, image, status in containers:
            proj_name = name_to_project.get(cname, cname)
            print(f"{proj_name:<20} {status:<22} {image}")

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


def run_move(args: argparse.Namespace) -> int:
    """Move a project workspace to a new directory."""
    import shutil as _shutil

    from kanibako.names import lookup_by_path, update_name_path
    from kanibako.paths import (
        _remove_project_vault_symlink,
        detect_project_mode,
    )
    from kanibako.utils import confirm_prompt as _confirm

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    positional = args.args  # 1 or 2 items: [project] <dest>
    if len(positional) == 1:
        project_dir = None
        dest = positional[0]
    elif len(positional) == 2:
        project_dir = positional[0]
        dest = positional[1]
    else:
        print("Error: expected [project] <dest>", file=sys.stderr)
        return 1

    # Resolve project.
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not proj.metadata_path.is_dir():
        print(f"Error: no project data found for {proj.project_path}", file=sys.stderr)
        return 1

    # Refuse if project is in a workset.
    dm = detect_project_mode(proj.project_path, std, config)
    if dm.mode == ProjectMode.workset:
        print(
            "Error: cannot move a workset project. "
            "Use workset-level operations instead.",
            file=sys.stderr,
        )
        return 1

    dest_path = Path(dest).resolve()
    source_path = proj.project_path

    if dest_path == source_path:
        print("Error: source and destination are the same.", file=sys.stderr)
        return 1

    if dest_path.exists():
        print(f"Error: destination already exists: {dest_path}", file=sys.stderr)
        return 1

    # Check for running container.
    lock_file = proj.metadata_path / ".kanibako.lock"
    if lock_file.exists():
        print(
            "Error: lock file found — a container may be running for this project.\n"
            "Stop the container first.",
            file=sys.stderr,
        )
        return 1

    # Confirm.
    if not args.force:
        print("Move project workspace:")
        print(f"  from: {source_path}")
        print(f"    to: {dest_path}")
        print()
        try:
            _confirm("Type 'yes' to confirm: ")
        except Exception:
            print("Aborted.")
            return 2

    # 1. Move workspace directory.
    try:
        _shutil.move(str(source_path), str(dest_path))
    except Exception as e:
        print(f"Error: failed to move workspace: {e}", file=sys.stderr)
        return 1

    # 2. Update names.toml path.
    result = lookup_by_path(std.data_path, str(source_path))
    if result is not None:
        name, section = result
        update_name_path(std.data_path, name, str(dest_path), section=section)
        print(f"Updated names.toml: {name} -> {dest_path}")
    elif proj.name:
        # Try by name directly.
        update_name_path(std.data_path, proj.name, str(dest_path))
        print(f"Updated names.toml: {proj.name} -> {dest_path}")

    # 3. Recreate vault symlinks (remove old, create new).
    _remove_project_vault_symlink(dest_path)
    vault_meta = proj.metadata_path / "vault"
    if vault_meta.is_dir():
        vault_link = dest_path / "vault"
        if not vault_link.exists():
            try:
                vault_link.symlink_to(vault_meta)
            except OSError:
                print("Warning: could not recreate vault symlink.", file=sys.stderr)

    print(f"Moved project to {dest_path}")
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


def run_config(args: argparse.Namespace) -> int:
    """Unified config interface for project settings.

    Handles get, set, show, reset operations via the config_interface engine.
    Uses the known-key heuristic to disambiguate project names from config keys.
    """
    from kanibako.config_interface import (
        ConfigAction,
        get_config_value,
        is_known_key,
        parse_config_arg,
        reset_all,
        reset_config_value,
        set_config_value,
        show_config,
    )

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    # Parse the positional args list: [project] [key[=value]]
    positional = args.args  # list of 0-2 items
    project_dir: str | None = None
    key_value_arg: str | None = None

    if len(positional) == 0:
        pass  # show mode
    elif len(positional) == 1:
        # Is it a known key (or key=value), or a project name?
        arg = positional[0]
        if "=" in arg or is_known_key(arg):
            key_value_arg = arg
        else:
            project_dir = arg
    elif len(positional) == 2:
        project_dir = positional[0]
        key_value_arg = positional[1]
    else:
        print("Error: too many arguments (expected [project] [key[=value]])", file=sys.stderr)
        return 1

    # Handle --reset mode
    if args.reset is not None:
        # --reset with --all: reset everything
        if args.reset_all or args.reset == "__ALL__":
            try:
                proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
            except ProjectError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1
            project_toml = proj.metadata_path / "project.toml"
            env_path = proj.metadata_path / "env"
            msg = reset_all(
                config_path=project_toml,
                env_path=env_path,
                force=args.force,
            )
            print(msg)
            return 0

        # --reset KEY: reset a specific key
        reset_key = args.reset
        try:
            proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
        except ProjectError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        project_toml = proj.metadata_path / "project.toml"
        env_path = proj.metadata_path / "env"
        msg = reset_config_value(
            reset_key,
            config_path=project_toml,
            env_path=env_path,
        )
        print(msg)
        return 0

    # Parse the key/value argument
    action, key, value = parse_config_arg(key_value_arg)

    # --local flag forces a set operation (sets resource to project-isolated)
    if args.local and action == ConfigAction.get:
        action = ConfigAction.set

    # Resolve the project
    try:
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    project_toml = proj.metadata_path / "project.toml"
    env_global = std.data_path / "env"
    env_project = proj.metadata_path / "env"

    if action == ConfigAction.show:
        return show_config(
            global_config_path=config_file,
            config_path=project_toml,
            env_global=env_global,
            env_project=env_project,
            effective=args.effective,
        )

    if action == ConfigAction.get:
        val = get_config_value(
            key,
            global_config_path=config_file,
            project_toml=project_toml,
            env_global=env_global,
            env_project=env_project,
        )
        if val is not None:
            print(val)
        else:
            print("(not set)", file=sys.stderr)
        return 0

    if action == ConfigAction.set:
        # Handle --local for resource keys
        if args.local:
            from kanibako.config_interface import _is_resource_key, _resolve_key
            canonical = _resolve_key(key)
            if not _is_resource_key(canonical):
                print("Error: --local only applies to resource.* keys", file=sys.stderr)
                return 1
            # --local means project-isolated (set scope to "project")
            value = "project"

        msg = set_config_value(
            key, value,
            config_path=project_toml,
            env_path=env_project,
        )
        print(msg)
        return 0

    return 0

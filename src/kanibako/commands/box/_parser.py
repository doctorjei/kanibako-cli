"""Parser setup, list, info, config, and lifecycle commands for kanibako box."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from kanibako.box_identity import validate_box_name
from kanibako.config import (
    BOX_META_FILE,
    config_file_path,
    load_config,
    load_merged_config,
    write_project_config,
)
from kanibako.container import ContainerRuntime
from kanibako.errors import ContainerError, ProjectError
from kanibako.names import read_names, unregister_name
from kanibako.paths import (
    xdg,
    iter_projects,
    iter_workset_projects,
    load_std_paths,
    resolve_any_project,
    resolve_box_target,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.targets import resolve_target
from kanibako.utils import container_name_for, short_hash, write_project_gitignore

_MODE_CHOICES = ["default", "standalone", "workset"]


def _add_target_group(
    parser: argparse.ArgumentParser, *, required: bool = False,
) -> None:
    """Attach the uniform ownership-target flags to *parser*.

    ``--default`` / ``--standalone`` / ``--workset <ws>`` are mutually
    exclusive.  Used identically by ``move`` (optional) and ``convert``
    (required).
    """
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument(
        "--default", dest="to_default", action="store_true",
        help="Target the default workset",
    )
    group.add_argument(
        "--standalone", dest="to_standalone", action="store_true",
        help="Target standalone mode (state inside the project directory)",
    )
    group.add_argument(
        "--workset", dest="to_workset", metavar="WS", default=None,
        help="Target the named workset",
    )


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    from kanibako.commands.box._duplicate import run_duplicate
    from kanibako.commands.box._lifecycle import (
        _BARE_MOVE,
        run_convert,
        run_move,
        run_remap,
    )

    p = subparsers.add_parser(
        "box",
        help="Project lifecycle commands for boxes (containers)",
        description="Manage per-project session data for boxes (containers): create, list, remap, move, convert, duplicate, archive, extract, purge.",
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
        help="Container image to use for this project (--rig is the preferred spelling)",
    )
    create_p.add_argument(
        "--rig", dest="image", default=None,
        help="Rig (image) to use; synonym for --image",
    )
    create_p.add_argument(
        "--no-vault", action="store_true",
        help="Disable vault directories (shared read-only and read-write mounts)",
    )
    create_p.add_argument(
        "--distinct-auth", action="store_true",
        help="Use distinct credentials (no sync from host)",
    )
    create_p.add_argument(
        "--allow-home", action="store_true",
        help="Permit a standalone project rooted at $HOME (mounts your entire "
             "home directory; required to create one there)",
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
        "--active", action="store_true",
        help="Show only active (running) boxes",
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

    # kanibako box remap <old> [<new>]   (records-only)
    remap_p = box_sub.add_parser(
        "remap",
        help="Update a project's recorded path after you moved the folder yourself",
        description=(
            "Records-only relocation. Use this when you have ALREADY moved or\n"
            "renamed a project's directory and just need kanibako to catch up.\n"
            "Updates the recorded workspace path, hash, and markers. Does NOT\n"
            "move files and never changes ownership."
        ),
    )
    remap_p.add_argument(
        "old", nargs="?", default=None,
        help="Current project (name or path; default: cwd)",
    )
    remap_p.add_argument(
        "new", nargs="?", default=None,
        help="New workspace location (default: cwd)",
    )
    remap_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt",
    )
    remap_p.set_defaults(func=run_remap)

    # kanibako box move <old> <new>   (alias: mv)   (relocate files)
    move_p = box_sub.add_parser(
        "move",
        aliases=["mv"],
        help="Physically relocate a project's workspace to a new directory",
        description=(
            "Move a project's workspace from <old> to <new> (both required) and\n"
            "update its records/markers. An optional target flag also changes\n"
            "ownership; without one the owner is unchanged.\n"
            "Refuses external-connected projects (use `remap` or `convert`)."
        ),
    )
    move_p.add_argument("old", help="Current project (name or path)")
    move_p.add_argument("new", help="Destination directory")
    _add_target_group(move_p)
    move_p.add_argument(
        "--name", default=None,
        help="Rename the project at the destination",
    )
    move_p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation; also override the cwd-inside-project guard",
    )
    move_p.set_defaults(func=run_move)

    # kanibako box convert [<old>] (--default|--standalone|--workset <ws>) [--move [path]]
    convert_p = box_sub.add_parser(
        "convert",
        help="Change a project's ownership/mode (default/standalone/workset)",
        description=(
            "Change which mode/workset owns a project. In-place by default for\n"
            "all modes (the workspace does not move). Add `--move <path>` to\n"
            "relocate, or a bare `--move` (only with --workset) to move into the\n"
            "target workset. `--name` renames in the target."
        ),
    )
    convert_p.add_argument(
        "old", nargs="?", default=None,
        help="Project to convert (name or path; default: cwd)",
    )
    _add_target_group(convert_p, required=True)
    convert_p.add_argument(
        "--move", dest="move", nargs="?", const=_BARE_MOVE, default=None,
        metavar="PATH",
        help="Relocate the workspace; bare --move moves into the target workset",
    )
    convert_p.add_argument(
        "--name", default=None,
        help="Rename the project in the target",
    )
    convert_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt",
    )
    convert_p.set_defaults(func=run_convert)

    # kanibako box duplicate
    duplicate_p = box_sub.add_parser(
        "duplicate",
        help="Duplicate a project (workspace + metadata) under a new path",
        description=(
            "Copy a project's workspace directory and kanibako metadata to a new path.\n"
            "The metadata is re-keyed under the new path's hash.\n"
            "With --to, duplicate into a different mode."
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
        help="Duplicate into a different mode",
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
            "Remove a project from names.yaml without touching the workspace.\n"
            "With --purge, also delete kanibako metadata (shell config, settings.yaml, vault symlinks, logs)."
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

    # Reuse existing subcommand modules under box.
    from kanibako.commands.archive import add_parser as add_archive_parser
    from kanibako.commands.clean import add_parser as add_purge_parser
    from kanibako.commands.restore import add_parser as add_extract_parser
    from kanibako.commands.start import add_start_parser as _add_start_parser
    from kanibako.commands.start import add_shell_parser as _add_shell_parser
    from kanibako.commands.stop import add_parser as _add_stop_parser

    from kanibako.commands.vault_cmd import add_vault_subparser

    # box diagnose [project]
    from kanibako.commands.diagnose import run_box_diagnose

    diagnose_p = box_sub.add_parser(
        "diagnose",
        help="Check project box health",
    )
    diagnose_p.add_argument(
        "project",
        nargs="?",
        default=None,
        help="Project name or workspace path (default: cwd)",
    )
    diagnose_p.set_defaults(func=run_box_diagnose)

    # box helper -- delegate to helper_cmd
    from kanibako.commands.helper_cmd import add_helper_subparsers

    helper_p = box_sub.add_parser(
        "helper",
        help="Manage helper instances",
        description="Spawn, list, stop, cleanup, and respawn helper instances.",
    )
    add_helper_subparsers(helper_p)

    # box fork <name> -- delegate to fork_cmd
    from kanibako.commands.fork_cmd import run_fork

    fork_p = box_sub.add_parser(
        "fork",
        help="Fork this project into a new directory",
        description=(
            "Fork the current project into a sibling directory. "
            "The fork is a full copy of the workspace and metadata, "
            "assigned a new project name."
        ),
    )
    fork_p.add_argument(
        "name",
        help="Fork name (appended with dot to workspace path)",
    )
    fork_p.set_defaults(func=run_fork)

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

    enable_vault = not getattr(args, "no_vault", False)
    group_auth = False if getattr(args, "distinct_auth", False) else None
    project_dir = args.path

    # R2: every box name is lowercase — silently fold a user-supplied --name.
    # After folding, a NEW box's --name is held to the §Design 8 blocklist.
    if getattr(args, "name", None):
        args.name = args.name.lower()
        validate_box_name(args.name)

    # $HOME guard: a home-directory project mounts the entire home tree, so it
    # must be (a) standalone and (b) an explicit opt-in via --allow-home. Local
    # mode at $HOME is never permitted.
    effective_path = Path(project_dir).resolve() if project_dir else Path.cwd().resolve()
    if effective_path == Path.home().resolve():
        if not args.standalone:
            print(
                "Error: Refusing to create a project at $HOME.\n"
                "A home-directory project must be standalone and explicit:\n"
                "  kanibako create --standalone ~ --allow-home",
                file=sys.stderr,
            )
            return 1
        if not getattr(args, "allow_home", False):
            print(
                "Error: Refusing to create a standalone project at $HOME "
                "without --allow-home.\n"
                "This mounts your entire home directory as the project. If you "
                "really mean it:\n"
                "  kanibako create --standalone ~ --allow-home",
                file=sys.stderr,
            )
            return 1

    # Create directory if it doesn't exist.
    if project_dir is not None:
        target = Path(project_dir)
        if not target.exists():
            target.mkdir(parents=True)

    if args.standalone:
        proj = resolve_standalone_project(
            std, config, project_dir, initialize=True,
            enable_vault=enable_vault, group_auth=group_auth,
            name=getattr(args, "name", None) or "",
        )
    else:
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
            enable_vault=enable_vault if not enable_vault else None,
            name_override=getattr(args, "name", None),
        )

    if not proj.is_new:
        print(
            f"Error: project already initialized in {proj.project_path}",
            file=sys.stderr,
        )
        return 1

    # Persist image setting.
    image = args.image or config.box_image
    project_toml = proj.metadata_path / BOX_META_FILE
    write_project_config(project_toml, image)

    # Write .gitignore for standalone projects only — at the project ROOT
    # (metadata_path), where box_data/ + vault/ live and need ignoring (drift
    # H+I: project_path is the workspace subdir, not the root).
    if args.standalone:
        write_project_gitignore(proj.metadata_path)

    mode = "standalone" if args.standalone else "default"
    print(f"Created {mode} project in {proj.project_path}")
    return 0


def run_ps(args: argparse.Namespace) -> int:
    """List running boxes (delegates to run_list with active-only filtering).

    ``ps`` shows active boxes by default.  ``ps --all`` / ``ps -a`` shows
    all boxes (active + inactive), equivalent to ``list``.
    """
    show_all = getattr(args, "show_all", False)
    # When ps --all is passed, show everything (like list).
    # Otherwise, show active only (like list --active).
    if not show_all:
        args.active = True
    return run_list(args)


def run_list(args: argparse.Namespace) -> int:
    show_all = getattr(args, "show_all", False)
    orphan_only = getattr(args, "orphan", False)
    active_only = getattr(args, "active", False) and not show_all
    quiet = getattr(args, "quiet", False)

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    projects = iter_projects(std, config)
    ws_data = iter_workset_projects(std, config)
    # STANDALONE boxes live only in registry.standalone (box name → root); they
    # are not in names.yaml / iter_projects, so list them explicitly (BUG-E).
    from kanibako import registry_store
    standalone = registry_store.load_standalone(std.data_path)

    if orphan_only:
        return _list_orphans(projects, ws_data, std, quiet)

    # Gather running container names for activity cross-reference.
    running_containers: set[str] = set()
    try:
        runtime = ContainerRuntime()
        for cname, _image, _status in runtime.list_running():
            running_containers.add(cname)
    except ContainerError:
        pass  # No runtime available — all projects show as stopped.

    if not projects and not ws_data and not standalone:
        if not quiet:
            print("No known projects.")
        return 0

    # Build reverse lookup from path → name using names.yaml.
    names_data = read_names(std.data_path)
    path_to_name: dict[str, str] = {v: k for k, v in names_data["projects"].items()}

    any_output = False

    if projects:
        header_printed = False
        for settings_path, project_path in projects:
            # Directory name is now the project name (or hash for legacy).
            dir_name = settings_path.name
            proj_name = path_to_name.get(str(project_path), dir_name) if project_path else dir_name
            if project_path is None:
                status = "unknown"
                label = "(no breadcrumb)"
            elif project_path.is_dir():
                # Check if container is running.
                cname = f"kanibako-{proj_name}"
                if cname in running_containers:
                    status = "active"
                else:
                    status = "stopped"
                label = str(project_path)
            else:
                status = "missing"
                label = str(project_path)

            # Skip orphans unless --all is given.
            if status in ("missing", "unknown") and not show_all:
                continue

            # Skip inactive when --active filter is set.
            if active_only and status != "active":
                continue

            any_output = True
            if quiet:
                print(proj_name)
            else:
                if not header_printed:
                    print(f"{'NAME':<18} {'STATUS':<10} {'PATH'}")
                    header_printed = True
                print(f"{proj_name:<18} {status:<10} {label}")

    for ws_name, ws, project_list in ws_data:
        ws_items: list[tuple[str, str, str]] = []
        for proj_name, proj_status in project_list:
            if proj_status == "missing" and not show_all:
                continue
            # Determine activity status for healthy workset projects.
            if proj_status not in ("missing",):
                cname = f"kanibako-{proj_name}"
                if cname in running_containers:
                    display_status = "active"
                else:
                    display_status = "stopped" if proj_status == "ok" else proj_status
            else:
                display_status = proj_status
            if active_only and display_status != "active":
                continue
            # Look up source_path from workset projects.
            source = ""
            for p in ws.projects:
                if p.name == proj_name:
                    source = str(p.source_path)
                    break
            ws_items.append((proj_name, display_status, source))

        if not ws_items:
            if not active_only and not quiet:
                any_output = True
                print()
                print(f"Workset: {ws_name} ({ws.root})")
                if not project_list:
                    print("  (no projects)")
            continue

        any_output = True
        if quiet:
            for proj_name, _status, _source in ws_items:
                print(proj_name)
        else:
            print()
            print(f"Workset: {ws_name} ({ws.root})")
            print(f"  {'NAME':<18} {'STATUS':<10} {'SOURCE'}")
            for proj_name, display_status, source in ws_items:
                print(f"  {proj_name:<18} {display_status:<10} {source}")

    # STANDALONE boxes (registry.standalone: box name → in-tree root).
    sa_items: list[tuple[str, str, str]] = []
    for box_name, root_str in sorted(standalone.items()):
        root = Path(root_str)
        if not root.is_dir():
            status = "missing"
        else:
            cname = f"kanibako-{box_name}"
            status = "active" if cname in running_containers else "stopped"
        if status == "missing" and not show_all:
            continue
        if active_only and status != "active":
            continue
        sa_items.append((box_name, status, root_str))

    if sa_items:
        any_output = True
        if quiet:
            for box_name, _status, _root in sa_items:
                print(box_name)
        else:
            print()
            print("Standalone boxes:")
            print(f"  {'NAME':<26} {'STATUS':<10} {'ROOT'}")
            for box_name, status, root_str in sa_items:
                print(f"  {box_name:<26} {status:<10} {root_str}")

    if not any_output and not quiet:
        if active_only:
            print("No active boxes.")
        else:
            print("No known projects.")

    return 0


def _list_orphans(
    projects: list,
    ws_data: list,
    std,
    quiet: bool,
) -> int:
    """List only orphaned projects (--orphan flag handler)."""
    # Default-mode orphans: path missing or no breadcrumb.
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
        print("Use 'kanibako box remap' to update paths, or 'kanibako box rm' to remove.")
    return 0


def _purge_dir(target: Path) -> bool:
    """Remove *target*, tolerating files a rootless container created.

    A box's shell dir can contain files owned by mapped subuids (root inside a
    ``--userns=keep-id`` container) that the host user cannot unlink, so a plain
    ``shutil.rmtree`` fails with EACCES. Fall back to ``podman unshare rm -rf``,
    which deletes from within the user namespace. Returns True if *target* is
    gone afterwards, False otherwise (caller warns rather than crashing).
    """
    import shutil

    try:
        shutil.rmtree(target)
        return True
    except OSError:
        pass
    try:
        from kanibako.container import ContainerError, ContainerRuntime

        if ContainerRuntime().unshare_rm(target):
            return True
    except ContainerError:
        pass
    return not target.exists()


def _resolve_standalone_target(
    std, config, target: str,
) -> tuple[str | None, Path | None]:
    """Resolve a ``box rm`` *target* to a registered standalone box.

    Returns ``(box_name, root)`` when *target* names a standalone box, else
    ``(None, None)``.  *target* may be a registered standalone box NAME (looked
    up in ``registry.standalone``) or a PATH (resolved by ancestor-walk
    detection, then matched to its registered root).  Mirrors how ``box purge``
    finds standalone boxes (BUG-C).
    """
    from kanibako import registry_store
    from kanibako.paths import BoxMode, detect_project_mode

    # 1) Direct standalone-name lookup.
    entries = registry_store.load_standalone(std.data_path)
    if target in entries:
        return target, Path(entries[target])

    # 2) Path target: detect the box by ancestor-walk, then match its root.
    candidate = Path(target)
    if candidate.exists():
        try:
            detection = detect_project_mode(candidate.resolve(), std, config)
        except Exception:  # noqa: BLE001 - a non-project path is simply a miss
            return None, None
        if detection.mode is BoxMode.standalone:
            root = detection.project_root
            sa_name = registry_store.standalone_name_for_root(std.data_path, root)
            if sa_name is not None:
                return sa_name, root
    return None, None


def _rm_standalone(std, box_name: str, root, args: argparse.Namespace) -> int:
    """Remove a standalone box: drop its registry entry (+ box_data/ on --purge).

    Standalone state lives in-tree under ``<root>/box_data`` (+ ``<root>/vault``)
    and the box is indexed in ``registry.standalone``.  Always drops the registry
    entry; with ``--purge`` also deletes the in-tree ``box_data/`` metadata (and,
    on confirmation, the ``vault/`` tree).  The user's workspace files are never
    touched.
    """
    from kanibako import registry_store
    from kanibako.errors import UserCancelled
    from kanibako.paths import _STANDALONE_META_DIR
    from kanibako.utils import confirm_prompt

    print(f"Removing standalone box: {box_name} ({root})")
    registry_store.unregister_standalone(std.data_path, box_name)
    print(f"Removed '{box_name}' from the registry")

    metadata_dir = Path(root) / _STANDALONE_META_DIR if root is not None else None
    if args.purge:
        if metadata_dir is not None and metadata_dir.is_dir():
            if not args.force:
                print()
                try:
                    confirm_prompt(
                        f"Delete metadata at {metadata_dir}? This cannot be undone.\n"
                        "Type 'yes' to confirm: "
                    )
                except UserCancelled:
                    print("Aborted (box was already unregistered).")
                    return 2
            if _purge_dir(metadata_dir):
                print(f"Removed metadata: {metadata_dir}")
                # Drift I: the box settings.yaml lives at the ROOT, not in
                # box_data/ — drop it too so the box is not re-detected.
                settings_file = Path(root) / BOX_META_FILE
                if settings_file.is_file():
                    settings_file.unlink()
                    print(f"Removed metadata: {settings_file}")
                vault_dir = Path(root) / "vault"
                if vault_dir.is_dir():
                    _purge_dir(vault_dir)
                    print(f"Removed vault: {vault_dir}")
            else:
                print(
                    f"Warning: could not fully remove {metadata_dir} "
                    "(it may contain files created inside a container). "
                    f"Try: podman unshare rm -rf {metadata_dir}",
                    file=sys.stderr,
                )
        else:
            print(f"No metadata directory found at {metadata_dir}")
    elif metadata_dir is not None and metadata_dir.is_dir():
        print(
            f"Metadata still present at {metadata_dir}. "
            f"Run 'kanibako box rm {box_name} --purge' to delete."
        )
    return 0


def run_rm(args: argparse.Namespace) -> int:
    """Unregister a project from names.yaml, optionally purging metadata."""
    from kanibako.names import lookup_by_path
    from kanibako.utils import confirm_prompt

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    from kanibako.commands.flags import resolve_subject_value
    target = resolve_subject_value(args.target, getattr(args, "box", None))
    if not target:
        print("Error: no box specified to remove.", file=sys.stderr)
        return 1
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

    if name is None:
        # STANDALONE boxes are not in names.yaml — they live in
        # registry.standalone (box name → root). Resolve the target as either a
        # registered standalone box name or a path (ancestor-walk detection), so
        # `box rm <canonical-name>` and `box rm <path>` both clean up an
        # otherwise-uncleanable standalone box (BUG-C).
        sa_name, sa_root = _resolve_standalone_target(std, config, target)
        if sa_name is not None:
            return _rm_standalone(std, sa_name, sa_root, args)

    if name is None or section is None:
        print(f"Error: '{target}' is not a registered project or workset.", file=sys.stderr)
        return 1

    kind = "workset" if section == "worksets" else "project"
    print(f"Removing {kind}: {name} ({path})")

    # Unregister from the registry.
    unregister_name(std.data_path, name, section=section)
    print(f"Removed '{name}' from the registry")

    if args.purge:
        metadata_dir = std.boxes / name

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

            if _purge_dir(metadata_dir):
                print(f"Removed metadata: {metadata_dir}")
                # Phase 5: PRIMARY vault lives under @config.primary_workset/
                # vault/{ro,rw}/<name> (not under metadata_dir) — remove it too.
                for vdir in (
                    std.primary_vault_ro / name,
                    std.primary_vault_rw / name,
                ):
                    if vdir.is_dir():
                        _purge_dir(vdir)
            else:
                print(
                    f"Warning: could not fully remove {metadata_dir} "
                    "(it may contain files created inside a container). "
                    f"Try: podman unshare rm -rf {metadata_dir}",
                    file=sys.stderr,
                )

            # Remove the per-box helper log if present.  PRIMARY logs live at
            # @config.primary_workset/logs/<box>.jsonl (box == registry name).
            log_file = std.primary_logs / f"{name}.jsonl"
            if log_file.is_file():
                log_file.unlink()
                print(f"Removed log: {log_file}")
        else:
            print(f"No metadata directory found at {metadata_dir}")
    else:
        # Hint about --purge when metadata still exists.
        metadata_dir = std.boxes / name
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

    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(
        getattr(args, "path", None), getattr(args, "box", None),
    )

    # Route the subject (positional path OR ``--box`` value) through the unified
    # path-or-name resolver (name-precedence), the same way the sibling box
    # commands / D2 wiring do — so a bare registered box NAME selects that box
    # instead of being treated as a (nonexistent) relative directory.  The old
    # premature ``Path(raw).is_dir()`` check rejected every name here.
    try:
        proj = resolve_box_target(std, config, project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Check if the project has been initialized (has metadata on disk).
    has_data = proj.metadata_path.is_dir()

    if not has_data:
        print(f"No project data found for: {proj.project_path}")
        print()
        if proj.group is not None and proj.group.is_default:
            print("This directory has not been used with kanibako yet.")
            print("Start a session with 'kanibako start', or create with:")
            print("  kanibako box create")
        else:
            print("This directory has not been initialized.")
        return 1

    # Load merged config for image info.
    project_toml = proj.metadata_path / BOX_META_FILE
    workset_path = (proj.group.root / "settings.yaml") if proj.group is not None else None
    merged = load_merged_config(
        config_file,
        project_toml if project_toml.exists() else None,
        workset_path=workset_path,
    )

    # Gather status info.
    lock_file = proj.metadata_path / ".kanibako.lock"
    lock_held = lock_file.exists()

    container_running, container_detail = _check_container_running(proj)

    # Resolve target for credential check path.  This is an INFORMATIONAL
    # display (box status), not an agent-requiring launch — so a resolution
    # failure (no default + 2+ agents, 0 agents, adapter missing) degrades to
    # "n/a (no target)" rather than erroring out.  Uses the unified
    # resolve_agent cascade (workset_agent=None: merged.box_agent_name already folds
    # the workset tier).
    try:
        from kanibako.config import resolve_agent
        agent_name = resolve_agent(
            explicit_agent=None,
            box_agent_name=merged.box_agent_name,
            workset_agent=None,
            system_default_path=std.settings,
            project_path=proj.project_path,
        )
        target = resolve_target(agent_name, proj.project_path)
        creds_file = target.credential_check_path(proj.shell_path)
    except Exception:
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
    rows.extend([
        ("Image", merged.box_image),
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

    # --box names the subject project; reconcile with the positional [project]
    # (same → warn / differ → error).
    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(project_dir, getattr(args, "box", None))

    # Handle --reset mode
    if args.reset is not None:
        # --reset with --all: reset everything
        if args.reset_all or args.reset == "__ALL__":
            try:
                proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
            except ProjectError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1
            project_toml = proj.metadata_path / BOX_META_FILE
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
        project_toml = proj.metadata_path / BOX_META_FILE
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

    project_toml = proj.metadata_path / BOX_META_FILE
    env_global = std.data_path / "env"
    env_project = proj.metadata_path / "env"

    if action == ConfigAction.show:
        workset_path = (
            (proj.group.root / "settings.yaml") if proj.group is not None else None
        )
        agent_state = None
        env_resolved = None
        if args.effective:
            from kanibako.config import load_merged_config
            from kanibako.agent_config import (
                agent_settings_path,
                load_agent_config,
            )
            from kanibako.targets import resolve_target
            from kanibako.commands.start import (
                _build_config_env,
                _effective_behavior_for_display,
            )
            merged = load_merged_config(
                config_file, project_toml if project_toml.exists() else None,
                workset_path=workset_path,
            )
            try:
                from kanibako.config import resolve_agent
                # Informational --effective display: tolerate a resolution
                # failure (degrade to the "general" no-agent state below)
                # rather than erroring.  Unified cascade; workset_agent=None
                # since merged.box_agent_name already folds the workset tier.
                agent_name = resolve_agent(
                    explicit_agent=None,
                    box_agent_name=merged.box_agent_name,
                    workset_agent=None,
                    system_default_path=std.settings,
                    project_path=proj.project_path,
                )
                target = resolve_target(agent_name, proj.project_path)
            except Exception:
                target = None
            agent_id = target.name if target else "general"
            agent_cfg_path = agent_settings_path(std.agents, agent_id)
            if target and not agent_cfg_path.exists():
                agent_cfg = target.generate_agent_config()
            elif agent_cfg_path.exists():
                agent_cfg = load_agent_config(agent_cfg_path)
            else:
                agent_cfg = None
            if target is not None and agent_cfg is not None:
                agent_state = _effective_behavior_for_display(
                    target, agent_cfg, project_toml,
                    global_config_path=std.settings,
                    workset_config_path=workset_path,
                )
            workset_env_path = (
                proj.group.root / "env"
                if (proj.group is not None and not proj.group.is_default)
                else None
            )
            env_resolved = _build_config_env(
                std.data_path / "env",
                agent_cfg.env if agent_cfg is not None else {},
                workset_env_path,
                proj.metadata_path / "env",
            )
        return show_config(
            global_config_path=config_file,
            config_path=project_toml,
            env_global=env_global,
            env_project=env_project,
            effective=args.effective,
            workset_path=workset_path,
            agent_state=agent_state,
            env_resolved=env_resolved,
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

        # Full launch cascade for a CATEGORY set's set-time E3 probe (Jei (b),
        # 2026-06-29): thread every scope's settings file + the active agent name so
        # a cross-scope @-ref in the new value resolves exactly as it would at
        # launch. The box handler already holds box (project_toml) / workset
        # (workset_path) / system (std.settings) files. The active agent NAME is
        # resolved best-effort: it selects the ``agent.<active>.*`` sub-table the
        # OTHER cascade files may carry (mirroring _effective_behavior_for_display,
        # which likewise passes agent_path=None — the per-agent file stores behavior
        # FLAT, so assemble_levels reads no category subtree from it). A resolution
        # failure just leaves the agent name empty.
        cascade_workset_path = (
            (proj.group.root / "settings.yaml") if proj.group is not None else None
        )
        cascade_agent_name = ""
        try:
            from kanibako.config import load_merged_config, resolve_agent
            merged = load_merged_config(
                config_file, project_toml if project_toml.exists() else None,
                workset_path=cascade_workset_path,
            )
            cascade_agent_name = resolve_agent(
                explicit_agent=None,
                box_agent_name=merged.box_agent_name,
                workset_agent=None,
                system_default_path=std.settings,
                project_path=proj.project_path,
            )
        except Exception:
            cascade_agent_name = ""

        msg = set_config_value(
            key, value,
            config_path=project_toml,
            env_path=env_project,
            cascade_system_path=std.settings,
            cascade_workset_path=cascade_workset_path,
            cascade_box_path=project_toml,
            cascade_agent_name=cascade_agent_name,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    return 0

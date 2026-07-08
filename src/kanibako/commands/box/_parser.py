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
    workset_env_path,
    workset_settings_path,
)
from kanibako.agent_ref import harness_of, with_harness
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
    #                     [--no-vault]
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

    # kanibako box set [project] <key>=<value> [--force] [--local]
    set_p = box_sub.add_parser(
        "set",
        help="Set a project configuration value",
        description=(
            "Set a project setting (key=value).\n\n"
            "  box set model=sonnet            set 'model' for cwd project\n"
            "  box set myproj model=sonnet     set 'model' for named project\n"
            "  box set env.MY_VAR=hello        set env var\n"
            "  box set resource.plugins=/p     set resource path\n"
            "  box set resource.plugins --local  project-isolated resource\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_p.add_argument("args", nargs="*", default=[], help="[project] key=value")
    set_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts",
    )
    set_p.add_argument(
        "--local", action="store_true",
        help="Set resource to project-isolated (resource keys only)",
    )
    set_p.set_defaults(func=run_set)

    # kanibako box reset [project] <key> | --all  [--force]
    reset_p = box_sub.add_parser(
        "reset",
        help="Reset (remove) a project configuration override",
        description=(
            "Remove a project override, reverting to the inherited value.\n\n"
            "  box reset model                 reset one key for cwd project\n"
            "  box reset myproj model          reset one key for named project\n"
            "  box reset --all                 reset all overrides\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reset_p.add_argument("args", nargs="*", default=[], help="[project] [key]")
    reset_p.add_argument(
        "--all", action="store_true", dest="reset_all",
        help="Reset all overrides",
    )
    reset_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts",
    )
    reset_p.set_defaults(func=run_reset)

    # kanibako box get [project] <key>
    get_p = box_sub.add_parser(
        "get",
        help="Get a project configuration value",
        description=(
            "Read one project setting.\n\n"
            "  box get model                   get 'model' for cwd project\n"
            "  box get myproj model            get 'model' for named project\n"
            "  box get env.MY_VAR              read an env var\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    get_p.add_argument("args", nargs="*", default=[], help="[project] key")
    get_p.set_defaults(func=run_get)

    # kanibako box show [project] [--effective]
    show_p = box_sub.add_parser(
        "show",
        help="Show project configuration overrides",
        description=(
            "Show project settings.\n\n"
            "  box show                        show overrides for cwd project\n"
            "  box show myproj                 show overrides for named project\n"
            "  box show --effective            show resolved values\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    show_p.add_argument("args", nargs="*", default=[], help="[project]")
    show_p.add_argument(
        "--effective", action="store_true",
        help="Show resolved values including inherited defaults",
    )
    show_p.set_defaults(func=run_show)

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

    from kanibako.commands.start import (
        _clear_create_entry,
        _name_new_box_probe,
        _pending_create_entry,
        _register_new_box,
        _write_create_entry,
        persona_create_verdict,
        seed_new_box,
    )

    # PERSONA LOAD-OR-ERROR — TRUE PRE-FLIGHT (F5, Director ruling 2026-07-03):
    # resolve a NON-materialising PROBE (``initialize=False`` → NO mkdir) and run
    # the persona load-or-error gate BEFORE any box dir / meta is created, applying
    # the SAME probe(named) → gate → initialize pattern the launch path uses.  An
    # unloadable persona `create` then refuses HERE with NOTHING left on disk (no
    # box dir, no meta, no journal entry, no seed) — mirroring the ordering clause
    # for the create path.  The probe carries the deterministic name it WILL
    # materialise under (:func:`_name_new_box_probe`) so the gate's channel-address
    # derivation resolves instead of raising "box has no name".
    if args.standalone:
        _probe = resolve_standalone_project(
            std, config, project_dir, initialize=False,
            enable_vault=enable_vault,
            name=getattr(args, "name", None) or "",
            register=False,
        )
    else:
        _probe = resolve_project(
            std, config, project_dir=project_dir, initialize=False,
            enable_vault=enable_vault if not enable_vault else None,
            name_override=getattr(args, "name", None),
            register=False,
        )
    _name_new_box_probe(std, _probe)
    _persona_err = persona_create_verdict(
        std, config, _probe, explicit_agent=getattr(args, "agent", None)
    )
    if _persona_err is not None:
        print(_persona_err, file=sys.stderr)
        return 1

    # Loadability resolved → MATERIALISE the box for real.
    #
    # J1 lifecycle journal: resolve with register=False so registration is
    # DEFERRED past the home seed (write-entry -> seed -> register -> clear-entry
    # below), giving the invariant "registered ==> fully seeded".  The resolver
    # creates the box dir + meta and sets is_new; only the registry write is held
    # back to the caller.  On a RE-CREATE of an interrupted box the register=False
    # import HONORS the flag (resolves the box name from on-disk meta without
    # registering), so the box resolves with is_new False BUT a pending create
    # journal entry — the recovery signal handled below.
    if args.standalone:
        proj = resolve_standalone_project(
            std, config, project_dir, initialize=True,
            enable_vault=enable_vault,
            name=getattr(args, "name", None) or "",
            register=False,
        )
    else:
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
            enable_vault=enable_vault if not enable_vault else None,
            name_override=getattr(args, "name", None),
            register=False,
        )

    # J1 interrupted-create RECOVERY: a box that resolves NOT-new but carries a
    # pending create journal entry is a half-completed create (crash between
    # seed-start and the registry write).  COMPLETE it by replay (seed
    # create-if-absent -> register-if-absent -> clear-entry) instead of bailing
    # "already initialized".  A box that is NOT new AND has no pending entry is
    # genuinely already initialized → the original error.  This is the central
    # J1 fix: the journal entry (not is_new) drives completion, restoring the
    # HARD INVARIANT "registered ==> no pending entry" for PRIMARY and STANDALONE.
    is_recovery = (not proj.is_new) and _pending_create_entry(std, proj) is not None
    if not proj.is_new and not is_recovery:
        print(
            f"Error: project already initialized in {proj.project_path}",
            file=sys.stderr,
        )
        return 1

    # Persist image + standalone .gitignore only on a FRESH create — a recovery
    # re-create reuses the half-built box's already-written meta (the on-disk
    # record is authoritative; do not overwrite it with possibly-different args).
    if proj.is_new:
        # Persist image setting.
        image = args.image or config.box_image
        project_toml = proj.metadata_path / BOX_META_FILE
        write_project_config(project_toml, image)

        # Write .gitignore for standalone projects only — at the project ROOT
        # (metadata_path), where box_data/ + vault/ live and need ignoring (drift
        # H+I: project_path is the workspace subdir, not the root).
        if args.standalone:
            write_project_gitignore(proj.metadata_path)

    # Seed the box home NOW, atomically with creation (keyspace spec §0/§5).
    # The one-time home seed runs at `create`, not at first launch — registry
    # MEMBERSHIP is the seed signal, so `start` never re-seeds an existing box.
    #
    # J1 lifecycle journal (Jei 2026-06-30b): the four ordered steps are
    # write-ahead.  Write the create journal entry (intent), seed the home
    # (create-if-absent), THEN register (deferred via register=False above), then
    # clear the entry — clearing is the IMMEDIATE step after the registry write
    # (HARD INVARIANT: registered ==> no pending entry at rest).  A crash anywhere
    # before the entry is cleared leaves it, so the next `create`/launch re-seeds
    # + completes registration + clears the entry (forward-recovery, not
    # rollback).  ``_register_new_box`` is register-if-absent so a recovery of an
    # already-registered box (register -> clear-entry window crash) is a no-op
    # + entry clear.  If register raises a genuine collision the entry is
    # intentionally LEFT (the box is incomplete) and propagates.
    # PERSONA LOAD-OR-ERROR ran as a TRUE PRE-FLIGHT above (before box-dir
    # creation), so by here the persona is known loadable — proceed to seed +
    # register.  The guard still precedes the write-ahead journal entry (Director
    # ruling #3): an abort after the entry would leave a pending entry whose
    # recovery replays the seed.
    _write_create_entry(std, proj)
    seed_new_box(std, config, proj, explicit_agent=getattr(args, "agent", None))
    _register_new_box(std, proj)
    _clear_create_entry(std, proj)

    mode = "standalone" if args.standalone else "default"
    if is_recovery:
        print(f"Resumed interrupted {mode} project in {proj.project_path}")
    else:
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
    standalone = registry_store.load_standalone(std.registry)

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
    names_data = read_names(std.registry)
    path_to_name: dict[str, str] = {v: k for k, v in names_data["projects"].items()}

    def _norm(p: object) -> str:
        """Normalize a path (Path or str, possibly None) for row-identity keys."""
        if not p:
            return ""
        try:
            return str(Path(str(p)).resolve())
        except OSError:
            return str(p)

    # NAME column width: floor 18 (the historical fixed width), grown to fit the
    # longest displayed name (capped) so long names like
    # ``ai-java-course-materials`` don't overflow the column.
    _candidate_names: list[str] = []
    for _sp, _pp in projects:
        _candidate_names.append(
            path_to_name.get(str(_pp), _sp.name) if _pp else _sp.name
        )
    for _wn, _ws, _plist in ws_data:
        _candidate_names.extend(pn for pn, _st in _plist)
    _candidate_names.extend(standalone.keys())
    name_width = min(40, max([18, *(len(n) for n in _candidate_names)]))

    # Cross-source row dedup (BUG-A): collapse rows that share the same
    # ``(name, resolved path)`` so an already-duplicated registry entry (e.g. a
    # box double-registered under the same workspace path) prints exactly once.
    seen_rows: set[tuple[str, str]] = set()

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

            # Cross-source dedup (BUG-A).
            row_key = (proj_name, _norm(project_path))
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)

            any_output = True
            if quiet:
                print(proj_name)
            else:
                if not header_printed:
                    print(f"{'NAME':<{name_width}} {'STATUS':<10} {'PATH'}")
                    header_printed = True
                print(f"{proj_name:<{name_width}} {status:<10} {label}")

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
            # Cross-source dedup (BUG-A): collapse identical (name, path) rows.
            row_key = (proj_name, _norm(source))
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)
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
            print(f"  {'NAME':<{name_width}} {'STATUS':<10} {'SOURCE'}")
            for proj_name, display_status, source in ws_items:
                print(f"  {proj_name:<{name_width}} {display_status:<10} {source}")

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
        # Cross-source dedup (BUG-A).
        row_key = (box_name, _norm(root_str))
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)
        sa_items.append((box_name, status, root_str))

    if sa_items:
        any_output = True
        if quiet:
            for box_name, _status, _root in sa_items:
                print(box_name)
        else:
            sa_width = max(name_width, 26)
            print()
            print("Standalone boxes:")
            print(f"  {'NAME':<{sa_width}} {'STATUS':<10} {'ROOT'}")
            for box_name, status, root_str in sa_items:
                print(f"  {box_name:<{sa_width}} {status:<10} {root_str}")

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

    names_data = read_names(std.registry)
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
    entries = registry_store.load_standalone(std.registry)
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
            sa_name = registry_store.standalone_name_for_root(std.registry, root)
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
    registry_store.unregister_standalone(std.registry, box_name)
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
    names = read_names(std.registry)

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
        result = lookup_by_path(std.registry, target)
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
    unregister_name(std.registry, name, section=section)
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
    workset_path = workset_settings_path(proj.group)
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
        target = resolve_target(harness_of(agent_name), proj.project_path)
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


# ---------------------------------------------------------------------------
# Project config verbs (set / reset / get / show)
#
# Each verb parser registers its own thin entry point below; they normalize the
# per-verb Namespace into the shared shape the engine dispatch (_run_box_config)
# expects, then thread the SAME context.  The config_interface engine — and with
# it the B2 config.*-forbid guard, the B4/R2 scope-direction guard, and the Q9
# full-cascade set-time validation — is unchanged: every set still routes
# through set_config_value with command_scope=ConfigLevel.box.
# ---------------------------------------------------------------------------

def run_set(args: argparse.Namespace) -> int:
    """``box set [project] <key>=<value>`` — set a project setting."""
    args.reset = None
    args.reset_all = False
    args.effective = False
    return _run_box_config(args)


def run_reset(args: argparse.Namespace) -> int:
    """``box reset [project] <key>`` / ``box reset [project] --all``."""
    from kanibako.config_interface import is_known_key

    positional = list(getattr(args, "args", []))
    reset_all = getattr(args, "reset_all", False)
    # Positional shape: [project] [key].  Disambiguate a lone token as a key
    # when it looks like one (matches the get/show heuristic).
    project: str | None = None
    key: str | None = None
    if len(positional) == 0:
        pass
    elif len(positional) == 1:
        tok = positional[0]
        if is_known_key(tok):
            key = tok
        else:
            project = tok
    elif len(positional) == 2:
        project, key = positional[0], positional[1]
    else:
        print("Error: too many arguments (expected [project] [key])", file=sys.stderr)
        return 1

    if not reset_all and key is None:
        print("Error: reset requires a key (or --all)", file=sys.stderr)
        return 1

    # Rebuild the legacy shape: positionals carry only [project]; the key (or the
    # all-sentinel) rides on ``reset``.
    args.args = [project] if project is not None else []
    args.reset = "__ALL__" if reset_all else key
    args.effective = False
    args.local = False
    return _run_box_config(args)


def run_get(args: argparse.Namespace) -> int:
    """``box get [project] <key>`` — read one project setting."""
    if not getattr(args, "args", []):
        print("Error: get requires a key", file=sys.stderr)
        return 1
    args.reset = None
    args.reset_all = False
    args.effective = False
    args.local = False
    return _run_box_config(args)


def run_show(args: argparse.Namespace) -> int:
    """``box show [project] [--effective]`` — show overrides / resolved values."""
    args.reset = None
    args.reset_all = False
    args.local = False
    return _run_box_config(args)


def _run_box_config(args: argparse.Namespace) -> int:
    """Shared project-config engine dispatch.

    Handles get, set, show, reset operations via the config_interface engine.
    Uses the known-key heuristic to disambiguate project names from config keys.
    """
    from kanibako.config_interface import (
        ConfigAction,
        ConfigLevel,
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
                command_scope=ConfigLevel.box,
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
        # Full launch cascade so the honest cleared-message can name the
        # now-effective value + source tier (item 1) — the SAME context the box
        # SET handler threads. A resolution failure just leaves the agent name
        # empty → the message degrades to the cleared-only form.
        reset_ws_path = workset_settings_path(proj.group)
        reset_agent_name = ""
        try:
            from kanibako.config import load_merged_config, resolve_agent
            _merged = load_merged_config(
                config_file, project_toml if project_toml.exists() else None,
                workset_path=reset_ws_path,
            )
            reset_agent_name = resolve_agent(
                explicit_agent=None,
                box_agent_name=_merged.box_agent_name,
                workset_agent=None,
                system_default_path=std.settings,
                project_path=proj.project_path,
            )
        except Exception:
            reset_agent_name = ""
        # Bug 2: thread the context-light CORE box-mount floor registry (the SAME
        # one the box SET path folds) so the honest cleared-message can name the
        # reverted-to FLOOR value when a core bind (``box.bindings.{ro,rw}.<key>``)
        # is reset. ``core_default_bind_keys`` does NO proj/std probe.
        from kanibako.core_defaults import core_default_bind_keys
        msg = reset_config_value(
            reset_key,
            config_path=project_toml,
            env_path=env_path,
            command_scope=ConfigLevel.box,
            cascade_system_path=std.settings,
            cascade_workset_path=reset_ws_path,
            cascade_box_path=project_toml,
            cascade_agent_name=reset_agent_name,
            default_categories=dict(core_default_bind_keys()),
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
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
        workset_path = workset_settings_path(proj.group)
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
                target = resolve_target(harness_of(agent_name), proj.project_path)
            except Exception:
                target = None
            # NODE-name keys the agent.<node>.* keyspace slot / agents/<node>/ dir
            # for the --effective display; with_harness reflects the resolved target
            # (fallback-safe), persona preserved. Bare + as-requested == target.name.
            agent_id = with_harness(agent_name, target.name) if target else "general"
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
                    system_settings_path=std.settings,
                    workset_config_path=workset_path,
                    node_name=agent_id,
                )
            # Workset env for named AND primary worksets (F9) — the primary's
            # tier file lives under @config.primary_workset.
            ws_env_path = workset_env_path(proj.group)
            env_resolved = _build_config_env(
                std.data_path / "env",
                agent_cfg.env if agent_cfg is not None else {},
                ws_env_path,
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
        cascade_workset_path = workset_settings_path(proj.group)
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

        # F10: expose the launch-only CORE box-mount floor (``box.bindings.{ro,rw}.
        # <key>`` — home/workspace/vault) to the set-time cascade so a source-only
        # repoint of a core bind is no longer refused as "nowhere in the cascade".
        # The registry is CONTEXT-LIGHT — box_dest/options straight from the
        # declarative ``core:`` doc + a placeholder host_src the repoint discards
        # (``core_default_bind_keys`` does NO proj/std probe); it is folded into the
        # box-scope set-time floor, NEVER the launch snapshot.
        from kanibako.core_defaults import core_default_bind_keys
        set_default_categories: dict[str, object] = dict(core_default_bind_keys())

        msg = set_config_value(
            key, value,
            config_path=project_toml,
            env_path=env_project,
            cascade_system_path=std.settings,
            cascade_workset_path=cascade_workset_path,
            cascade_box_path=project_toml,
            cascade_agent_name=cascade_agent_name,
            command_scope=ConfigLevel.box,
            default_categories=set_default_categories,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    return 0

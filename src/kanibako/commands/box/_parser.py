"""The ``box`` verb tree: parser setup, create, list, info, rm/register, config verbs.

**_Terminology_**
- _primary_ box: metadata under ``std.boxes/<name>``, indexed in the primary workset membership
- _standalone_ box: metadata in-tree (``<root>/box_data``), indexed in ``registry.standalone``
- _deregistered_: ``rm`` without ``--purge`` — membership dropped, metadata retained for readopt
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from kanibako.launch.box_identity import validate_box_name
from kanibako.commands.flags import add_null_flag
from kanibako.settings.config import (
    WORKSET_META_FILE,
    config_file_path,
    load_config,
    load_merged_config,
    persist_creation_flags,
)
from kanibako.runtime.container import ContainerRuntime
from kanibako.errors import ContainerError, ProjectError
from kanibako.project.names import read_names, unregister_name
from kanibako.settings.paths import (
    xdg,
    BoxMode,
    _box_settings_files,
    _standalone_settings_files,
    box_tree_materialized,
    box_workset_settings_paths,
    check_primary_box_name_free,
    iter_projects,
    iter_workset_projects,
    load_primary_boxes,
    load_std_paths,
    primary_box_name_for_workspace,
    resolve_any_project,
    resolve_box_target,
    resolve_project,
    resolve_standalone_project,
    unregister_primary_box_name,
)
from kanibako.agent_ref import harness_of, with_harness
from kanibako.targets import resolve_target
from kanibako.utils import container_name_for, short_hash, write_project_gitignore

_MODE_CHOICES = ["default", "standalone", "workset"]


def _add_target_group(
    parser: argparse.ArgumentParser, *, required: bool = False,
) -> None:
    """Attach the uniform, mutually-exclusive ownership-target flags to *parser*."""
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
        help="Project name override (default: auto-assigned from directory name). "
             "For a standalone box this names the registry entry, so it is inert "
             "without --register.",
    )
    create_p.add_argument(
        "--standalone", action="store_true",
        help="Use standalone mode (all state inside the project directory)",
    )
    create_p.add_argument(
        "--register", action="store_true",
        help="Index a new STANDALONE box in the registry so it resolves by name "
             "from other directories (default: unregistered and independent; "
             "--name is ignored without this). Default-mode boxes always register.",
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
    create_p.add_argument(
        "--private", action="store_true",
        help="Create a PRIVATE box: disable global and workset credential "
             "sharing so the host's OAuth token is never seeded into it.",
    )
    create_p.add_argument(
        "--force", action="store_true",
        help="Create even if --name is already used by a workset (the box "
             "shadows that workset in bare-name resolution)",
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
            "With --purge, also delete kanibako metadata (shell config, box.yaml, vault symlinks, logs)."
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

    # kanibako box register <name|path>   (readopt / late standalone register)
    register_p = box_sub.add_parser(
        "register",
        help="Re-register a deregistered box, or register a standalone box on disk",
        description=(
            "Bring a box back into the registry — an INDEX-ONLY operation that\n"
            "never re-seeds or touches the box's home content. Two uses:\n"
            "  register <name>   readopt a box deregistered by 'rm' (restore\n"
            "                    its active membership)\n"
            "  register <path>   register a standalone box that exists on disk\n"
            "                    (box_data/ + box.yaml) but has no index entry\n"
            "Refuses if an active box already occupies the name or workspace."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    register_p.add_argument(
        "target",
        help="Deregistered box name, or path to a standalone box on disk",
    )
    register_p.add_argument(
        "--force", action="store_true",
        help="Re-register even if the name is used by a workset (the box then "
             "shadows that workset in bare-name resolution)",
    )
    register_p.set_defaults(func=run_register)

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

    # kanibako box set [project] <key>=<value> [--force]
    set_p = box_sub.add_parser(
        "set",
        help="Set a project configuration value",
        description=(
            "Set a project setting (key=value).\n\n"
            "  box set model=sonnet            set 'model' for cwd project\n"
            "  box set myproj model=sonnet     set 'model' for named project\n"
            "  box set box.env.MY_VAR=hello    set env var\n"
            "  box set --null pref.agent.claude.common\n"
            "                                  request that the agent's common\n"
            "                                  binds be SUPPRESSED for this box\n"
            "                                  (spec §2h)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_p.add_argument("args", nargs="*", default=[], help="[project] key=value")
    set_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts",
    )
    add_null_flag(set_p, undo="box reset [project] <key>")
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
            "  box get box.env.MY_VAR          read an env var\n"
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


def _assert_primary_home_free_for_create(std, name: str) -> None:
    """⚑ DATA-LOSS GUARD (I4): refuse a ``create --name`` that would reuse a box home."""
    from kanibako.project import registry_store
    from kanibako.launch import journal

    box_dir = std.boxes / name

    # ⚑ ORDER: this refusal MUST precede the pending-create allow below — a STALE create
    # entry would otherwise FALSE-ALLOW a merge into a deregistered box's retained home.
    if registry_store.lookup_deregistered(std.registry, name) is not None:
        raise ProjectError(
            f"a box named '{name}' already exists as a deregistered box "
            f"(metadata retained by 'rm'); run 'kanibako box register {name}' to "
            f"recover it or 'kanibako box rm {name} --purge' to delete it, then "
            f"retry."
        )

    # A half-create being recovered is the legitimate re-entry — never refuse it.
    if journal.pending_create(std.journal, box_dir) is not None:
        return

    if box_dir.is_dir():
        raise ProjectError(
            f"a box named '{name}' already has orphaned metadata at {box_dir} "
            f"(no active or deregistered registration); remove it (e.g. "
            f"'rm -rf {box_dir}') or choose a different --name, then retry."
        )


def _check_persona_store_for_create(agent_ref: str, project_path) -> str | None:
    """⚑ READ-ONLY create-side persona-grata store CHECK; an ``"Error: …"`` or ``None``."""
    from kanibako.agent_ref import display_agent_ref
    from kanibako.errors import ConfigError
    from kanibako.log import get_logger
    from kanibako.persona_store import locate_entry, read_persona_bundle
    from kanibako.targets.base import PersonaProbeOutcome, PersonaProbeVerdict

    try:
        entry = locate_entry(agent_ref)
    except ConfigError as e:
        return f"Error: {e}"
    if entry is None:
        return None  # not a persona / no store entry -> normal create handling
    target = resolve_target(entry.harness, project_path)
    if target is None:
        return None  # harness not installed -> the normal machinery errors
    display = display_agent_ref(entry.node)
    bundle = read_persona_bundle(entry.node, target)
    if bundle is None or bundle.no_reader:
        # ⚑ ``no_reader`` is a FALL-THROUGH, never a refusal (a goose persona may own a
        # store dir purely for its ``.secret_path``).
        return None
    if bundle.reject_reason is not None:
        return f"Error: {bundle.reject_reason}"
    if not bundle.endpoint:
        return (
            f"Error: persona store config at {entry.config_dir} names no "
            f"endpoint; a persona needs one"
        )
    if bundle.token_error is not None:
        print(
            f"Warning: persona '{display}': token pointer did not resolve "
            f"({bundle.token_error}); the store contributes no token.",
            file=sys.stderr,
        )
    # ⚑ WARN-ONLY probe, and it warns for the two ANSWERED non-PASS verdicts only —
    # ``NOT_APPLICABLE`` learned nothing and goes to the log, as on the launch path.
    if bundle.token_path is not None:
        try:
            outcome = target.verify_persona(
                bundle.endpoint, bundle.token_path, bundle.model,
            )
        except Exception:
            # The probe contract is never-raise; hold plugins to it, and warn on a bug.
            outcome = PersonaProbeOutcome.inconclusive("the probe itself failed")
        if outcome.verdict is PersonaProbeVerdict.REJECTED:
            print(
                f"Warning: the persona endpoint rejected the token for "
                f"'{display}'; creating anyway — fix the token in the store "
                f"before starting.",
                file=sys.stderr,
            )
        elif outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE:
            print(
                f"Warning: could not verify the persona endpoint for "
                f"'{display}' ({outcome.reason}); creating unverified.",
                file=sys.stderr,
            )
        elif outcome.verdict is PersonaProbeVerdict.NOT_APPLICABLE:
            get_logger(__name__).debug(
                "persona '%s': no verify probe was possible (%s); "
                "creating unprobed", display, outcome.reason,
            )
    print(
        f"Recognised persona '{display}' in the persona store; its values are "
        f"resolved from the store at every launch."
    )
    return None


def run_create(args: argparse.Namespace) -> int:
    """Create a new kanibako project (replaces ``kanibako init``)."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    enable_vault = not getattr(args, "no_vault", False)
    project_dir = args.path

    # ⚑ R2: fold --name to lowercase BEFORE validating (the blocklist sees the fold).
    if getattr(args, "name", None):
        args.name = args.name.lower()
        validate_box_name(args.name)

    # ⚑ §D4a: a STANDALONE box is indexed only on ``--register``; the default is an
    # unregistered, independent box, which is what lets it move freely.  The registry
    # is the by-name-from-elsewhere index and nothing else, so without it ``--name``
    # has nothing to name and is ignored (with it, ``--name`` sources the entry).
    # Read AFTER the R2 fold, so what is threaded is the folded name.
    standalone_register = bool(getattr(args, "register", False))
    standalone_name = (getattr(args, "name", None) or "") if standalone_register else ""

    # $HOME guard: a home project must be BOTH standalone and an explicit --allow-home.
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

    # ⚑ Cross-kind name guard, run HERE so it refuses BEFORE the box dir + seed materialize.
    if getattr(args, "name", None) and not args.standalone:
        from kanibako.errors import ProjectError
        try:
            check_primary_box_name_free(
                std.primary_workset, std.registry,
                args.name, str(effective_path),
                force=getattr(args, "force", False),
            )
            # ⚑ I4 data-loss guard — the HOME check the name check above does not make.
            _assert_primary_home_free_for_create(std, args.name)
        except ProjectError as e:
            print(f"Error: {e}", file=sys.stderr)
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
    from kanibako.settings.core_defaults import materialize_canon_skeleton

    # ⚑ PERSONA LOAD-OR-ERROR IS A TRUE PRE-FLIGHT: this probe is NON-materialising
    # (``initialize=False``) so an unloadable persona refuses with NOTHING left on disk.
    if args.standalone:
        _probe = resolve_standalone_project(
            std, config, project_dir, initialize=False,
            enable_vault=enable_vault,
            name=standalone_name,
            register=False,
        )
    else:
        _probe = resolve_project(
            std, config, project_dir=project_dir, initialize=False,
            enable_vault=enable_vault if not enable_vault else None,
            name_override=getattr(args, "name", None),
            register=False,
        )
    # ⚑ CAPTURE BEFORE ``_name_new_box_probe``, which mutates ``_probe.name``.
    _already = box_tree_materialized(_probe)
    _name_new_box_probe(std, _probe)
    # ⚑ The store check runs BEFORE the verdict below, so a broken store is reported as
    # itself rather than as the verdict's downstream "no endpoint configured".
    _agent_arg = getattr(args, "agent", None)
    if isinstance(_agent_arg, str) and _agent_arg:
        _store_err = _check_persona_store_for_create(
            _agent_arg, _probe.project_path,
        )
        if _store_err is not None:
            print(_store_err, file=sys.stderr)
            return 1
    _persona_err = persona_create_verdict(
        std, config, _probe, explicit_agent=getattr(args, "agent", None)
    )
    if _persona_err is not None:
        print(_persona_err, file=sys.stderr)
        return 1

    # ⚑ THE ALREADY-INITIALIZED REFUSAL IS HOISTED ABOVE THE MATERIALISING RESOLVE, and
    # asks the PROBE: the resolve's recovery arms would bootstrap the home the message
    # then claims already existed.  ⚑ The JOURNAL ENTRY, not ``is_new``, drives recovery.
    is_recovery = _already and _pending_create_entry(std, _probe) is not None
    if _already and not is_recovery:
        print(
            f"Error: project already initialized in {_probe.project_path}",
            file=sys.stderr,
        )
        return 1

    # Loadability resolved → MATERIALISE the box for real.  ⚑ ``register=False`` DEFERS
    # registration past the home seed, giving the invariant "registered ==> fully seeded".
    if args.standalone:
        proj = resolve_standalone_project(
            std, config, project_dir, initialize=True,
            enable_vault=enable_vault,
            name=standalone_name,
            register=False,
        )
    else:
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
            enable_vault=enable_vault if not enable_vault else None,
            name_override=getattr(args, "name", None),
            register=False,
        )

    # ⚑ FRESH CREATE ONLY — a recovery re-create reuses the half-built box's on-disk meta.
    if proj.is_new:
        # ⚑ §1A CREATE EXCEPTION (R-11a) via the ONE shared gate, writing the BOX-TIER
        # file from the ONE pair (M-8).  Only an EXPLICIT ``-i``/``--image`` persists.
        project_toml, _ = box_workset_settings_paths(proj)
        persist_creation_flags(
            project_toml, materializing=proj.is_new, image=args.image,
        )

        # ⚑ AUTH-CRITICAL: --private must run BEFORE the seed, and must write through the
        # SANCTIONED settings write into the SAME box-tier file the snapshot reads.
        if getattr(args, "private", False):
            from kanibako.settings.config_interface import set_config_value
            from kanibako.settings.config_keys import ConfigLevel
            from kanibako.errors import KanibakoError
            for _auth_key in (
                "box.auth.global_enabled",
                "box.auth.workset_enabled",
            ):
                # ⚑ set_config_value RETURNS an "Error: …" string, never raises — so this
                # AUTH-CRITICAL write must be checked or it fails silently and leaks.
                _msg = set_config_value(
                    _auth_key, "false",
                    config_path=project_toml,
                    command_scope=ConfigLevel.box,
                )
                if not _msg.startswith("Set "):
                    raise KanibakoError(
                        f"--private: failed to persist {_auth_key} to the box "
                        f"settings ({_msg}); refusing to create a box that would "
                        f"forward host credentials."
                    )

        # ⚑ `create --agent` persists the §2h REQUEST `pref.system.agent`, NOT the retired
        # `box.agent_name`, and stores the RAW ref (selection canonicalizes on read).
        _agent_sel = getattr(args, "agent", None)
        if isinstance(_agent_sel, str) and _agent_sel.strip():
            from kanibako.settings.config_interface import set_config_value
            from kanibako.settings.config_keys import ConfigLevel
            _msg = set_config_value(
                "pref.system.agent", _agent_sel.strip(),
                config_path=project_toml,
                command_scope=ConfigLevel.box,
            )
            if not _msg.startswith("Set "):
                # ⚑ A silent no-op would make plain `start` launch a DIFFERENT agent.
                print(
                    f"Error: failed to persist the box agent selection "
                    f"({_msg}).",
                    file=sys.stderr,
                )
                return 1

        # ⚑ At the project ROOT (``metadata_path``), not ``project_path`` — box_data/ and
        # vault/ live there, and for standalone the workspace is a SUBDIR of the root.
        if args.standalone:
            write_project_gitignore(proj.metadata_path)

    # ⚑ THE J1 WRITE-AHEAD ORDER, AND IT IS THE WHOLE MECHANISM: write entry → seed →
    # register → clear entry.  Clearing is IMMEDIATE after the registry write (HARD
    # INVARIANT: registered ==> no pending entry at rest).  Do not reorder these four.
    _write_create_entry(std, proj)
    seed_new_box(std, config, proj, explicit_agent=getattr(args, "agent", None))
    # ⚑ THE CANON SKELETON (J-7) — AFTER the seed (it makes the root 555; protect first
    # and the seed's copies die EACCES) and INSIDE the journal window (it must replay).
    materialize_canon_skeleton(proj.shell_path)
    # ⚑ The §D4a gate lives HERE, at the create verb: a PRIMARY box's membership IS its
    # workset, so it always registers; a standalone box only opts in.  An unregistered
    # box is adopted later by ``kanibako box register <path>`` (index-only, seed-free).
    if not args.standalone or standalone_register:
        _register_new_box(std, proj, force=getattr(args, "force", False))
    _clear_create_entry(std, proj)

    mode = "standalone" if args.standalone else "default"
    # Explicit-create: `create` MAKES the box but does NOT launch it, so name the verb.
    start_hint = (
        "Start the box by executing 'kanibako' (shortcuts to 'kanibako start') "
        "from its path (or add a path argument)."
    )
    if is_recovery:
        print(f"Resumed interrupted {mode} project in {proj.project_path}")
    else:
        print(f"Created {mode} project in {proj.project_path}")
    print(start_hint)
    # ⚑ The behavior signal for the §D4a flip: v1.7.2 registered here and said nothing,
    # so silence would report the OLD outcome.  ⚑ Gated on the registry STATE, not on
    # the flag: a journal-recovery re-run of a box that registered before the interrupt
    # passes no ``--register`` and is registered all the same, and "Not registered"
    # would be a lie about it.  ⚑ The cure names ``metadata_path`` (the box ROOT) — the
    # line above prints ``project_path``, the workspace SUBDIR, which carries no
    # standalone marker and would NOT paste.
    from kanibako.project import registry_store

    if args.standalone and registry_store.standalone_name_for_root(
        std.registry, Path(proj.metadata_path),
    ) is None:
        print(
            f"Not registered; run 'kanibako box register {proj.metadata_path}' "
            f"to address it by name from elsewhere."
        )
    return 0


def run_ps(args: argparse.Namespace) -> int:
    """List running boxes — :func:`run_list` with active-only filtering."""
    show_all = getattr(args, "show_all", False)
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
    # ⚑ STANDALONE boxes are NOT in names.yaml / iter_projects — list them explicitly.
    from kanibako.project import registry_store
    standalone = registry_store.load_standalone(std.registry)
    # DEREGISTERED boxes are never active, so they are skipped under an active-only filter.
    deregistered = (
        registry_store.list_deregistered(std.registry) if not active_only else {}
    )

    if orphan_only:
        return _list_orphans(projects, ws_data, std, quiet)

    # Gather running container names for activity cross-reference.
    running_containers: set[str] = set()
    try:
        runtime = ContainerRuntime()
        for cname, _image, _status in runtime.list_running():
            running_containers.add(cname)
    except ContainerError:
        pass  # No runtime available — every project shows as stopped.

    if not projects and not ws_data and not standalone and not deregistered:
        if not quiet:
            print("No known projects.")
        return 0

    # Build reverse lookup from path → name using the PRIMARY membership.
    path_to_name: dict[str, str] = {
        v: k for k, v in load_primary_boxes(std.primary_workset).items()
    }

    def _norm(p: object) -> str:
        """Normalize a path (Path or str, possibly None) for row-identity keys."""
        if not p:
            return ""
        try:
            return str(Path(str(p)).resolve())
        except OSError:
            return str(p)

    # NAME column width: floor 18, grown to fit the longest displayed name, capped at 40.
    _candidate_names: list[str] = []
    for _sp, _pp in projects:
        _candidate_names.append(
            path_to_name.get(str(_pp), _sp.name) if _pp else _sp.name
        )
    for _wn, _ws, _plist in ws_data:
        _candidate_names.extend(pn for pn, _st in _plist)
    _candidate_names.extend(standalone.keys())
    name_width = min(40, max([18, *(len(n) for n in _candidate_names)]))

    # Cross-source row dedup: rows sharing a ``(name, resolved path)`` print once.
    seen_rows: set[tuple[str, str]] = set()

    any_output = False

    if projects:
        header_printed = False
        for settings_path, project_path in projects:
            # The directory name IS the project name (or a hash, for legacy trees).
            dir_name = settings_path.name
            proj_name = path_to_name.get(str(project_path), dir_name) if project_path else dir_name
            if project_path is None:
                status = "unknown"
                label = "(no breadcrumb)"
            elif project_path.is_dir():
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

            if active_only and status != "active":
                continue

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
            # Activity status, for healthy workset projects only.
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
            source = ""
            for p in ws.projects:
                if p.name == proj_name:
                    source = str(p.source_path)
                    break
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

    # DEREGISTERED boxes get their own section so a user can SEE what they may
    # `register` or `rm --purge`; shown only when entries exist.
    if deregistered:
        any_output = True
        if quiet:
            for box_name in sorted(deregistered):
                print(box_name)
        else:
            dr_width = min(40, max(26, name_width, *(len(n) for n in deregistered)))
            print()
            print("Deregistered boxes (metadata retained):")
            print(f"  {'NAME':<{dr_width}} {'STATUS':<14} {'PATH'}")
            for box_name in sorted(deregistered):
                entry = deregistered[box_name]
                path = str(entry.get("workspace") or entry.get("metadata") or "")
                print(f"  {box_name:<{dr_width}} {'deregistered':<14} {path}")
            print(
                "  Recover with 'kanibako box register <name>', or delete with "
                "'kanibako box rm <name> --purge'."
            )

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
    """List only orphaned projects (the ``--orphan`` handler)."""
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

    path_to_name: dict[str, str] = {
        v: k for k, v in load_primary_boxes(std.primary_workset).items()
    }

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
    """Thin alias for :func:`kanibako.runtime.container.remove_box_tree`, kept for its callers."""
    from kanibako.runtime.container import remove_box_tree

    return remove_box_tree(target)


def _assert_deletable(path, *, must_be_under: Path | None = None) -> Path:
    """⚑ DESTRUCTIVE-SAFETY gate: validate *path* is safe to ``rm -rf``, return it resolved."""
    raw = str(path).strip()
    if not raw:
        raise ProjectError("refusing to delete: empty metadata path")
    resolved = Path(raw).resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve()}
    if resolved in forbidden:
        raise ProjectError(f"refusing to delete a protected path: {resolved}")
    if must_be_under is not None:
        root = must_be_under.resolve()
        # ⚑ STRICT containment — never the root itself (that would take every box).
        if resolved == root or root not in resolved.parents:
            raise ProjectError(
                f"refusing to delete {resolved}: not contained under {root}"
            )
    return resolved


def _teardown_primary_box(std, name: str, metadata_dir: Path) -> bool:
    """Delete a PRIMARY box's metadata: box dir + vault ro/rw + helper log."""
    removed = _purge_dir(metadata_dir)
    if removed:
        print(f"Removed metadata: {metadata_dir}")
        # ⚑ The PRIMARY vault is NOT under metadata_dir — remove it separately.
        for vdir in (std.primary_vault_ro / name, std.primary_vault_rw / name):
            if vdir.is_dir():
                _purge_dir(vdir)
    else:
        print(
            f"Warning: could not fully remove {metadata_dir} "
            "(it may contain files created inside a container). "
            f"Try: podman unshare rm -rf {metadata_dir}",
            file=sys.stderr,
        )
    # The per-box helper log, keyed by the registry name.
    log_file = std.primary_logs / f"{name}.jsonl"
    if log_file.is_file():
        log_file.unlink()
        print(f"Removed log: {log_file}")
    return removed


def _teardown_standalone_box(root: Path) -> bool:
    """Delete a STANDALONE box's in-tree metadata; the workspace and *root* are never touched."""
    from kanibako.settings.paths import STANDALONE_META_DIR

    metadata_dir = root / STANDALONE_META_DIR
    if _purge_dir(metadata_dir):
        print(f"Removed metadata: {metadata_dir}")
        # ⚑ The ROOT workset.yaml is the WORKSET tier AND half the §5 detection marker —
        # drop it too, or the box is re-detected.  (The BOX tier went with box_data/.)
        settings_file = root / WORKSET_META_FILE
        if settings_file.is_file():
            settings_file.unlink()
            print(f"Removed metadata: {settings_file}")
        vault_dir = root / "vault"
        if vault_dir.is_dir():
            _purge_dir(vault_dir)
            print(f"Removed vault: {vault_dir}")
        return True
    print(
        f"Warning: could not fully remove {metadata_dir} "
        "(it may contain files created inside a container). "
        f"Try: podman unshare rm -rf {metadata_dir}",
        file=sys.stderr,
    )
    return False


def _read_box_image(settings_file: Path) -> str | None:
    """Best-effort read of a box's ``box.image`` from its box.yaml; failure is ``None``."""
    try:
        from kanibako.settings.config_io import load_doc

        data = load_doc(settings_file)
        image = dict(data.get("box", {})).get("image")
        return str(image) if image else None
    except Exception:  # noqa: BLE001 - image capture is best-effort
        return None


def _read_box_image_tiered(box_tier: Path, workset_tier: Path) -> str | None:
    """:func:`_read_box_image` over a ``(box_tier, workset_tier)`` pair, box tier wins."""
    return _read_box_image(box_tier) or _read_box_image(workset_tier)


def _purge_deregistered(std, name: str, entry: dict, args: argparse.Namespace) -> int:
    """Handle ``rm <name>`` when *name* resolves only to a deregistered entry."""
    from kanibako.project import registry_store
    from kanibako.errors import UserCancelled
    from kanibako.settings.paths import STANDALONE_META_DIR
    from kanibako.utils import confirm_prompt

    kind = entry.get("kind")
    metadata = entry.get("metadata")

    if not args.purge:
        print(f"'{name}' is already deregistered (metadata retained at {metadata}).")
        print(
            f"Restore it with 'kanibako box register {name}', "
            f"or delete it with 'kanibako box rm {name} --purge'."
        )
        return 0

    # ⚑ I4 STALE-ENTRY guard: if an ACTIVE box now owns this metadata path, purging by
    # the stale entry would delete a LIVE box's home.  Refuse, and drop the stale entry.
    if kind == "standalone":
        active_owner = registry_store.standalone_name_for_root(
            std.registry, Path(str(metadata)),
        ) if metadata else None
    else:
        active_owner = (
            name if name in load_primary_boxes(std.primary_workset) else None
        )
    if active_owner is not None:
        print(
            f"'{name}' now refers to an active box (its metadata at {metadata} is "
            f"a live box's home); the old deregistered entry is stale — not "
            f"purging.  Dropped the stale entry (it will no longer shadow the "
            f"active box).",
            file=sys.stderr,
        )
        registry_store.unregister_deregistered(std.registry, name)
        return 1

    # ⚑ The stored metadata path is UNTRUSTED — validate before any delete.
    try:
        if kind == "standalone":
            root = _assert_deletable(metadata)
        else:
            metadata_dir = _assert_deletable(metadata, must_be_under=std.boxes)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if kind == "standalone":
        exists = (root / STANDALONE_META_DIR).is_dir()
    else:
        exists = metadata_dir.is_dir()

    if not exists:
        # Idempotent: the dir is already gone → drop the stale entry, no error, no prompt.
        registry_store.unregister_deregistered(std.registry, name)
        print(f"No metadata directory found for '{name}' (dropped stale entry).")
        return 0

    if not args.force:
        target_desc = root if kind == "standalone" else metadata_dir
        print(f"Removing deregistered box: {name}")
        print()
        try:
            confirm_prompt(
                f"Delete metadata at {target_desc}? This cannot be undone.\n"
                "Type 'yes' to confirm: "
            )
        except UserCancelled:
            print("Aborted (box remains deregistered).")
            return 2

    if kind == "standalone":
        _teardown_standalone_box(root)
    else:
        _teardown_primary_box(std, name, metadata_dir)

    registry_store.unregister_deregistered(std.registry, name)
    return 0


def _resolve_standalone_target(
    std, config, target: str,
) -> tuple[str | None, Path | None]:
    """Resolve a ``box rm`` *target* (NAME or PATH) to ``(box_name, root)``, else two ``None``."""
    from kanibako.errors import LegacyWorksetIdentityError
    from kanibako.project import registry_store
    from kanibako.settings.paths import BoxMode, detect_project_mode

    # 1) Direct standalone-NAME lookup.
    entries = registry_store.load_standalone(std.registry)
    if target in entries:
        return target, Path(entries[target])

    # 2) PATH target: detect the box by ancestor-walk, then match its registered root.
    candidate = Path(target)
    if candidate.exists():
        try:
            detection = detect_project_mode(candidate.resolve(), std, config)
        except LegacyWorksetIdentityError:
            # ⚑ THE ONE EXCEPTION THE BLANKET MISS MUST NOT EAT: an un-migrated workset
            # root in the walk is a NAMED thing to fix, not a path that failed to be a box.
            raise
        except Exception:  # noqa: BLE001 - a non-project path is simply a miss
            return None, None
        if detection.mode is BoxMode.standalone:
            root = detection.project_root
            sa_name = registry_store.standalone_name_for_root(std.registry, root)
            if sa_name is not None:
                return sa_name, root
    return None, None


def _rm_standalone(std, box_name: str, root, args: argparse.Namespace) -> int:
    """Remove a standalone box: always drop its registry entry, its ``box_data/`` on --purge."""
    from datetime import datetime, timezone

    from kanibako.project import registry_store
    from kanibako.errors import UserCancelled
    from kanibako.settings.paths import STANDALONE_META_DIR
    from kanibako.utils import confirm_prompt

    print(f"Removing standalone box: {box_name} ({root})")
    registry_store.unregister_standalone(std.registry, box_name)
    print(f"Removed '{box_name}' from the registry")

    root_path = Path(root) if root is not None else None
    metadata_dir = root_path / STANDALONE_META_DIR if root_path is not None else None
    if args.purge:
        if root_path is not None and metadata_dir is not None and metadata_dir.is_dir():
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
            _teardown_standalone_box(root_path)
        else:
            print(f"No metadata directory found at {metadata_dir}")
    elif root_path is not None and metadata_dir is not None and metadata_dir.is_dir():
        # Park a deregistered entry: the index was dropped above, so BY NAME is the
        # only way a later `rm --purge` / `register` can find the retained metadata.
        registry_store.register_deregistered(
            std.registry,
            box_name,
            kind="standalone",
            workspace=str(root_path),
            metadata=str(root_path),
            # Best-effort capture for a later readopt; either read failing is ``None``.
            image=_read_box_image_tiered(
                *_standalone_settings_files(root_path)
            ),
            deregistered_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        print(
            f"Deregistered '{box_name}' (metadata retained). "
            f"Restore it with 'kanibako box register {box_name}', "
            f"or delete it with 'kanibako box rm {box_name} --purge'."
        )
    return 0


def run_rm(args: argparse.Namespace) -> int:
    """Unregister a project/workset from the registry, optionally purging metadata."""
    from datetime import datetime, timezone

    from kanibako.project import registry_store
    from kanibako.project.names import lookup_by_path
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
    # ⚑ ``section`` is "projects" for a PRIMARY box (its membership drives the unregister
    # below) or "worksets" — the global ``projects:`` section is retired.
    primary_boxes = load_primary_boxes(std.primary_workset)

    # Resolve the target: as a registered name first, then as a path.
    name: str | None = None
    section: str | None = None
    path: str | None = None

    if target in primary_boxes:
        name, section, path = target, "projects", primary_boxes[target]
    elif target in names["worksets"]:
        name, section, path = target, "worksets", names["worksets"][target]

    if name is None:
        # Reverse path lookup: the primary membership first, then the worksets index.
        primary_hit = primary_box_name_for_workspace(std.primary_workset, target)
        if primary_hit is not None:
            name, section, path = primary_hit, "projects", primary_boxes.get(primary_hit)
        else:
            result = lookup_by_path(std.registry, target)
            if result is not None:
                name, section = result
                path = names[section][name]

    if name is None:
        # ⚑ STANDALONE boxes are not in the name index — resolve them separately.
        sa_name, sa_root = _resolve_standalone_target(std, config, target)
        if sa_name is not None:
            return _rm_standalone(std, sa_name, sa_root, args)

    if name is None:
        # ⚑ Not active anywhere — a re-`rm` after a plain `rm` must resolve the retained
        # metadata HERE rather than erroring "not registered".
        dereg = registry_store.lookup_deregistered(std.registry, target)
        if dereg is not None:
            return _purge_deregistered(std, target, dereg, args)

    if name is None or section is None:
        print(f"Error: '{target}' is not a registered project or workset.", file=sys.stderr)
        return 1

    kind = "workset" if section == "worksets" else "project"
    print(f"Removing {kind}: {name} ({path})")

    # ⚑ A PRIMARY box unregisters from the MEMBERSHIP, a workset from the global index.
    if section == "worksets":
        unregister_name(std.registry, name, section="worksets")
    else:
        unregister_primary_box_name(std.primary_workset, name)
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

            # The shared teardown — same paths/order/guards as the deregistered purge.
            _teardown_primary_box(std, name, metadata_dir)
        else:
            print(f"No metadata directory found at {metadata_dir}")
    else:
        # No --purge: retain the metadata and park a ``deregistered`` entry, so a later
        # `rm --purge` / `register` finds it BY NAME.  ⚑ Worksets are NEVER parked here.
        metadata_dir = std.boxes / name
        if section != "worksets" and metadata_dir.is_dir():
            registry_store.register_deregistered(
                std.registry,
                name,
                kind="primary",
                workspace=path,
                metadata=str(metadata_dir),
                image=_read_box_image(
                    _box_settings_files(BoxMode.primary, metadata_dir, None)[0],
                ),
                deregistered_at=datetime.now(tz=timezone.utc).isoformat(),
            )
            print(
                f"Deregistered '{name}' (metadata retained). "
                f"Restore it with 'kanibako box register {name}', "
                f"or delete it with 'kanibako box rm {name} --purge'."
            )

    return 0


def _readopt_deregistered(std, name: str, entry: dict, *, force: bool) -> int:
    """⚑ INDEX-ONLY, SEED-FREE readopt: move a box from ``deregistered`` back to active."""
    from kanibako.project import registry_store
    from kanibako.launch.box_resolve import standalone_settings_present

    kind = entry.get("kind")
    workspace = entry.get("workspace")
    metadata = entry.get("metadata")

    if kind == "standalone":
        root = Path(str(metadata)).resolve() if metadata else None
        # Self-heal: the in-tree marker is gone → nothing to restore, drop the entry.
        if root is None or not standalone_settings_present(root):
            registry_store.unregister_deregistered(std.registry, name)
            print(
                f"No standalone metadata found for '{name}' (dropped stale entry); "
                "nothing to restore.",
                file=sys.stderr,
            )
            return 1
        # Conflict: an ACTIVE standalone box at a DIFFERENT root — refuse, never clobber.
        registered = registry_store.load_standalone(std.registry)
        other = registered.get(name)
        if other is not None and Path(other).resolve() != root:
            print(
                f"Error: an active standalone box already owns the name '{name}' "
                f"({other}); refusing to readopt over it. Purge or move it first.",
                file=sys.stderr,
            )
            return 1
        registry_store.register_standalone(std.registry, name, root)
        registry_store.unregister_deregistered(std.registry, name)
        print(f"Registered standalone box '{name}' at {root}.")
        return 0

    # PRIMARY (default) box.
    from kanibako.settings.paths import register_primary_box_name

    metadata_dir = Path(str(metadata)).resolve() if metadata else None
    # Self-heal: the box dir is gone → nothing to restore, drop the entry.
    if metadata_dir is None or not metadata_dir.is_dir():
        registry_store.unregister_deregistered(std.registry, name)
        print(
            f"No metadata directory found for '{name}' (dropped stale entry); "
            "nothing to restore.",
            file=sys.stderr,
        )
        return 1
    if not workspace:
        print(
            f"Error: the deregistered entry for '{name}' has no workspace path; "
            "cannot readopt it.",
            file=sys.stderr,
        )
        return 1
    # ⚑ The REUSED registration API carries every conflict guard — do not inline a write.
    try:
        register_primary_box_name(
            std.primary_workset, std.registry, name, str(workspace), force=force,
        )
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    registry_store.unregister_deregistered(std.registry, name)
    print(f"Registered primary box '{name}' at {workspace}.")
    return 0


def run_register(args: argparse.Namespace) -> int:
    """⚑ Re-register a box: INDEX-ONLY and SEED-FREE, by NAME (readopt) or PATH (standalone)."""
    from kanibako.project import registry_store
    from kanibako.launch.box_resolve import standalone_settings_present

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    from kanibako.commands.flags import resolve_subject_value
    target = resolve_subject_value(args.target, getattr(args, "box", None))
    if not target:
        print("Error: no box specified to register.", file=sys.stderr)
        return 1

    force = getattr(args, "force", False)

    # 1. DEREGISTERED readopt — a bare name resolves here FIRST; the blob's ``kind`` routes.
    dereg = registry_store.lookup_deregistered(std.registry, target)
    if dereg is not None:
        return _readopt_deregistered(std, target, dereg, force=force)

    # 2. Already-ACTIVE guards — rc 0 no-op for a live box, a redirect for a workset.
    primary_boxes = load_primary_boxes(std.primary_workset)
    if target in primary_boxes:
        print(f"'{target}' is already registered (primary box at {primary_boxes[target]}).")
        return 0
    standalone = registry_store.load_standalone(std.registry)
    if target in standalone:
        print(f"'{target}' is already registered (standalone box at {standalone[target]}).")
        return 0
    worksets = read_names(std.registry)["worksets"]
    if target in worksets:
        print(
            f"Error: '{target}' is a workset, not a box. Worksets keep their own "
            "lifecycle; 'register' applies to primary and standalone boxes only.",
            file=sys.stderr,
        )
        return 1

    # 3. STANDALONE register-later — a PATH to an on-disk box with no index entry.
    #    ⚑ REUSE ``import_standalone``: it is already index-only + seed-free.
    candidate = Path(target)
    if candidate.is_dir():
        root = candidate.resolve()
        if standalone_settings_present(root):
            already = registry_store.standalone_name_for_root(std.registry, root)
            if already is not None:
                print(f"'{already}' is already registered (standalone box at {root}).")
                return 0
            from kanibako.project import import_reconcile
            try:
                sa_name = import_reconcile.import_standalone(
                    std.registry, root, journal=std.journal,
                )
            except import_reconcile.ImportConflictError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1
            print(f"Registered standalone box '{sa_name}' at {root}.")
            return 0

    # 4. Nothing to register.
    print(
        f"Error: '{target}' is neither a deregistered box nor a standalone box on "
        "disk; nothing to register.",
        file=sys.stderr,
    )
    return 1


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
    """Is a kanibako container running for this project? Returns ``(is_running, detail)``."""
    container_name = container_name_for(proj)
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        return False, "unknown (no container runtime)"
    containers = runtime.list_running()
    for name, image, status in containers:
        if name == container_name:
            return True, f"running ({container_name}: {image})"
    # A stopped PERSISTENT container still exists.
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

    # ⚑ Route the subject through the unified path-or-NAME resolver — a bare registered
    # box name must select that box, not be read as a relative directory.
    try:
        proj = resolve_box_target(std, config, project_dir, initialize=False)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Initialized == metadata on disk.
    has_data = proj.metadata_path.is_dir()

    if not has_data:
        print(f"No project data found for: {proj.project_path}")
        print()
        # ⚑ DEFER to the launch's own refusal rather than restating it — otherwise
        # ``info`` and a launch can name DIFFERENT cures for the same box.
        from kanibako.commands.start import _unbuilt_box_error
        unbuilt = _unbuilt_box_error(proj) if proj.name else None
        if unbuilt is not None:
            print(unbuilt, file=sys.stderr)
            return 1
        if proj.group is not None and proj.group.is_default:
            # ⚑ NOT "start a session": under explicit-create a launch NEVER makes a box.
            print("This directory has not been used with kanibako yet.")
            print("A launch will not create a box for it — create it with:")
            print("  kanibako box create")
        else:
            print("This directory has not been initialized.")
        return 1

    # The merged config, for the image row.
    project_toml, workset_path = box_workset_settings_paths(proj)
    merged = load_merged_config(
        config_file,
        project_toml if project_toml.exists() else None,
        workset_path=workset_path,
    )

    lock_file = proj.metadata_path / ".kanibako.lock"
    lock_held = lock_file.exists()

    container_running, container_detail = _check_container_running(proj)

    # ⚑ INFORMATIONAL, not a launch: a resolution failure DEGRADES rather than errors.
    # Selection still goes through the ONE seam (:func:`select_agent`).
    agent_display = "n/a (unresolved)"
    try:
        from kanibako.agent_ref import display_agent_ref
        from kanibako.settings.agent_select import select_agent
        _sel = select_agent(std=std, proj=proj)
        agent_name = _sel.node
        # ⚑ SHOW WHERE IT CAME FROM (P7) — this is the ONE place a user can read
        # ``AgentSelection.source``, and "which agent, and WHY" is not in any one file.
        agent_display = (
            f"{display_agent_ref(agent_name)}  (from: {_sel.source})"
            if agent_name
            else f"none — plain shell  (from: {_sel.source})"
        )
        target = (
            resolve_target(harness_of(agent_name), proj.project_path)
            if agent_name
            else None
        )
        creds_file = (
            target.credential_check_path(proj.shell_path) if target else None
        )
    except Exception as exc:
        creds_file = None
        # ⚑ A refused RETIRED key SURFACES here rather than being swallowed as
        # "unresolved" — `box info` is where a user looks when a box will not start.
        agent_display = f"n/a ({exc})" if "RETIRED" in str(exc) else agent_display
    cred_age = _format_credential_age(creds_file) if creds_file else "n/a (no target)"

    # Dashes read better than underscores in the mode row.
    mode_display = proj.mode.value.replace("_", "-")

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
        ("Agent", agent_display),
        ("Credentials", cred_age),
    ])

    label_width = max(len(label) for label, _ in rows) + 1  # +1 for the colon
    for label, value in rows:
        print(f"  {label + ':':<{label_width}}  {value}")

    return 0


# ---------------------------------------------------------------------------
# Project config verbs (set / reset / get / show) — thin per-verb entry points
# that normalize their Namespace into the shape :func:`_run_box_config` expects.
# ---------------------------------------------------------------------------

def run_set(args: argparse.Namespace) -> int:
    """``box set [project] <key>=<value>`` — set a project setting."""
    args.reset = None
    args.reset_all = False
    args.effective = False
    return _run_box_config(args)


def run_reset(args: argparse.Namespace) -> int:
    """``box reset [project] <key>`` / ``box reset [project] --all``."""
    from kanibako.settings.config_keys import is_known_key

    positional = list(getattr(args, "args", []))
    reset_all = getattr(args, "reset_all", False)
    # Positional shape ``[project] [key]``; a lone token is a KEY when it is known.
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

    # Rebuild the engine shape: positionals carry ``[project]`` only; the key (or the
    # all-sentinel) rides on ``args.reset``.
    args.args = [project] if project is not None else []
    args.reset = "__ALL__" if reset_all else key
    args.effective = False
    return _run_box_config(args)


def run_get(args: argparse.Namespace) -> int:
    """``box get [project] <key>`` — read one project setting."""
    if not getattr(args, "args", []):
        print("Error: get requires a key", file=sys.stderr)
        return 1
    args.reset = None
    args.reset_all = False
    args.effective = False
    return _run_box_config(args)


def run_show(args: argparse.Namespace) -> int:
    """``box show [project] [--effective]`` — show overrides / resolved values."""
    args.reset = None
    args.reset_all = False
    return _run_box_config(args)


def _resolve_config_subject(std, config, project_dir: str | None):
    """⚑ Resolve the box the config verbs address, refusing the ``__unregistered__`` phantom."""
    proj = resolve_any_project(
        std, config, project_dir=project_dir, initialize=False,
    )
    if proj.mode is BoxMode.primary and not proj.name:
        raise ProjectError(
            f"no box at {proj.project_path} — kanibako has no box registered "
            "for this directory, and a setting has to belong to a box.\n"
            "  Name the box:   kanibako box set <box> <key>=<value>\n"
            "  Or make one:    kanibako create"
        )
    return proj


def _run_box_config(args: argparse.Namespace) -> int:
    """Shared box-config dispatch into the ``config_interface`` engine."""
    from kanibako.settings.config_keys import ConfigLevel, is_known_key
    from kanibako.settings.config_interface import (
        ConfigAction,
        get_config_value,
        parse_config_arg,
        reset_all,
        reset_config_value,
        set_config_value,
        show_config,
    )

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    # Positional shape ``[project] [key[=value]]`` — 0-2 items.
    positional = args.args
    project_dir: str | None = None
    key_value_arg: str | None = None

    if len(positional) == 0:
        pass  # show mode
    elif len(positional) == 1:
        # A known key (or a key=value), else a project name.
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

    # --box also names the subject; reconcile it with the positional (same → warn).
    from kanibako.commands.flags import resolve_subject_value
    project_dir = resolve_subject_value(project_dir, getattr(args, "box", None))

    if args.reset is not None:
        # reset --all: everything at this level.
        if args.reset_all or args.reset == "__ALL__":
            try:
                proj = _resolve_config_subject(std, config, project_dir)
            except ProjectError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1
            project_toml, _ = box_workset_settings_paths(proj)
            msg = reset_all(
                config_path=project_toml,
                force=args.force,
                command_scope=ConfigLevel.box,
            )
            print(msg)
            return 0

        # reset KEY: one key.
        reset_key = args.reset
        try:
            proj = _resolve_config_subject(std, config, project_dir)
        except ProjectError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        # ⚑ The M-8 PAIR: reset clears exactly the file set/get address.
        project_toml, reset_ws_path = box_workset_settings_paths(proj)
        # The full launch cascade, so the cleared-message can name the now-effective
        # value and its tier.  A resolution failure degrades to the cleared-only form.
        reset_agent_name = ""
        try:
            from kanibako.settings.agent_select import select_agent
            reset_agent_name = select_agent(std=std, proj=proj).node
        except Exception:
            reset_agent_name = ""
        msg = reset_config_value(
            reset_key,
            config_path=project_toml,
            command_scope=ConfigLevel.box,
            cascade_system_path=std.settings,
            cascade_workset_path=reset_ws_path,
            cascade_box_path=project_toml,
            cascade_agent_name=reset_agent_name,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    action, key, value = parse_config_arg(
        key_value_arg, set_null=getattr(args, "null", False),
    )

    try:
        proj = _resolve_config_subject(std, config, project_dir)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # ⚑ THE M-8 CHOKEPOINT: every verb addresses the box through the ONE pair, so a
    # box-scope write and the read that follows it CANNOT disagree.
    project_toml, workset_path = box_workset_settings_paths(proj)

    if action == ConfigAction.show:
        agent_state = None
        env_resolved = None
        category_snapshot = None
        category_ctx = None
        category_error = None
        if args.effective:
            from kanibako.settings.agent_config import agent_settings_path
            from kanibako.settings.agent_file import load as load_agent_file
            from kanibako.targets import resolve_target
            from kanibako.commands.start import (
                _build_config_env,
                _effective_behavior_for_display,
                _launch_env_map,
            )
            agent_name = ""
            effective_selection = None
            try:
                from kanibako.settings.agent_select import select_agent
                # Informational display: tolerate a resolution failure, degrading to
                # the "general" no-agent state below.  Still the ONE selection seam.
                effective_selection = select_agent(std=std, proj=proj)
                agent_name = effective_selection.node
                target = (
                    resolve_target(harness_of(agent_name), proj.project_path)
                    if agent_name
                    else None
                )
            except Exception:
                target = None
            # ⚑ The NODE-name keys both the ``agent.<node>.*`` slot and the
            # ``agents/<node>/`` dir; ``with_harness`` follows the RESOLVED target.
            agent_id = with_harness(agent_name, target.name) if target else "general"
            agent_cfg_path = agent_settings_path(std.agents, agent_id)
            if target and not agent_cfg_path.exists():
                agent_cfg = target.generate_agent_config()
            elif agent_cfg_path.exists():
                agent_cfg = load_agent_file(agent_cfg_path)
            else:
                agent_cfg = None
            if target is not None and agent_cfg is not None:
                agent_state = _effective_behavior_for_display(
                    target, agent_cfg, project_toml,
                    system_settings_path=std.settings,
                    workset_config_path=workset_path,
                    node_name=agent_id,
                )
            # ⚑ Resolved off the SAME launch pipeline a start takes, so the display
            # cannot drift from what mounts.  A collision is REPORTED, never raised —
            # this view IS the detection recipe for that fault.
            from kanibako.commands.start import (
                _persona_values_for,
                _resolve_launch_snapshot,
            )
            from kanibako.errors import KanibakoError
            from kanibako.settings.agent_select import launch_resolve_ctx
            try:
                # ⚑ THE LAUNCH'S OWN ctx, off the ONE builder (P7) with the SAME
                # arguments ``_resolve_launch_snapshot`` passes it — so the block's
                # destination resolution and the collapse's cannot disagree about what
                # ``$XDG_*`` / ``$AGENT`` / ``~`` mean.
                category_ctx = launch_resolve_ctx(std, proj, agent_id)
                category_snapshot, _deliveries = _resolve_launch_snapshot(
                    std=std, proj=proj, agent_name=agent_id,
                    system_settings_path=std.settings,
                    agent_cfg_path=agent_cfg_path,
                    desc=None, install=None,
                    target=target, agent_cfg=agent_cfg,
                    # ⚑ The SAME persona-store tier the launch resolves against — without
                    # it this view lists a persona box's mounts MINUS its token.
                    persona_values=_persona_values_for(agent_id, target),
                    # ⚑ A DISPLAY verb must not write to disk: create-if-missing is a
                    # LAUNCH guarantee, not a read one.  The binds are emitted either way.
                    guarantee_create=False,
                    # ⚑ The SAME §1A selection level the launch installs (P7): without
                    # it this display resolves ``@system.agent`` differently from the
                    # launch it claims to show.
                    # ⚑ SELECTION ONLY, and for a READ verb that is the WHOLE CLI level
                    # (P8) — do not add an ephemeral VALUE flag here.
                    # ⚑ ``--agent`` PARSES here and is silently IGNORED — a pre-existing
                    # defect of the blanket flag injector, deliberately left as found.
                    cli_level=(
                        effective_selection.selection_level
                        if effective_selection is not None
                        else None
                    ),
                )
                # ⚑ The SAME helper the launch uses, off the SAME collapsed leaf
                # (``meta.assembly.env``), so the display cannot claim an env the box
                # will not get.  It needs the RESOLVE — hence its position here.
                # ⚑ And it inherits the collapse's REFUSAL: a config whose variables
                # a launch would refuse is reported HERE as the category error rather
                # than displayed as if it would start.
                env_resolved = _build_config_env(
                    _launch_env_map(category_snapshot),
                )
            except KanibakoError as exc:
                category_error = str(exc)
        return show_config(
            global_config_path=config_file,
            config_path=project_toml,
            effective=args.effective,
            workset_path=workset_path,
            agent_state=agent_state,
            env_resolved=env_resolved,
            category_snapshot=category_snapshot,
            category_ctx=category_ctx,
            category_error=category_error,
        )

    if action == ConfigAction.get:
        # ⚑ Bare ``env.*`` is RETIRED, and must be refused at the HANDLER: the get
        # engine returns VALUES, never error strings.
        from kanibako.settings.config_keys import bare_env_retired_error
        _env_err = bare_env_retired_error(
            key, verb="read", command_scope=ConfigLevel.box,
        )
        if _env_err is not None:
            print(_env_err, file=sys.stderr)
            return 1
        # The resolved agent NODE names the ``pref.agent.<active>.<key>`` a BARE agent
        # key redirects to.  Best-effort: an unresolvable agent just means no redirect.
        _get_agent_name = ""
        try:
            from kanibako.settings.agent_select import select_agent
            _get_agent_name = select_agent(std=std, proj=proj).node
        except Exception:
            _get_agent_name = ""
        # ⚑ THE CLOSED-KEYSPACE READ GATE (spec §0) — an undeclared name is refused
        # NAMING it, never answered "(not set)".  It runs AFTER the resolution above
        # because it must judge the key the ENGINE will read, redirect included, and
        # AFTER the retired-spelling guard above for the reason
        # ``scope_read_key_error`` states: a generic refusal must not overwrite a
        # specific one.
        from kanibako.settings.config_keys import scope_read_key_error
        _key_err = scope_read_key_error(
            key, ConfigLevel.box, active_agent=_get_agent_name or None,
        )
        if _key_err is not None:
            print(_key_err, file=sys.stderr)
            return 1
        val = get_config_value(
            key,
            global_config_path=config_file,
            project_toml=project_toml,
            command_scope=ConfigLevel.box,
            active_agent=_get_agent_name or None,
        )
        # ⚑ Name the value with the canonical redirect form, so the READ teaches the
        # request — mirroring the refusal message ``set`` prints.
        from kanibako.settings.config_keys import resolve_key, box_agent_redirect_key
        redirect = box_agent_redirect_key(
            resolve_key(key), ConfigLevel.box, _get_agent_name or None,
        )
        if val is not None:
            print(f"{redirect}={val}" if redirect is not None else val)
        else:
            print("(not set)", file=sys.stderr)
        return 0

    if action == ConfigAction.set:
        # ⚑ Thread the FULL launch cascade so a cross-scope ``@``-ref in the new value
        # resolves at set time exactly as it would at launch.  ⚑ The workset tier is the
        # one the M-8 pair named above — NEVER a second derivation.
        cascade_workset_path = workset_path
        cascade_agent_name = ""
        try:
            from kanibako.settings.agent_select import select_agent
            cascade_agent_name = select_agent(std=std, proj=proj).node
        except Exception:
            cascade_agent_name = ""

        msg = set_config_value(
            key, value,
            config_path=project_toml,
            cascade_system_path=std.settings,
            cascade_workset_path=cascade_workset_path,
            cascade_box_path=project_toml,
            cascade_agent_name=cascade_agent_name,
            command_scope=ConfigLevel.box,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    return 0

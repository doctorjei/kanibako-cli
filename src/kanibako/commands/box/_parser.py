"""Parser setup, list, info, config, and lifecycle commands for kanibako box."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from kanibako.launch.box_identity import validate_box_name
from kanibako.commands.flags import add_null_flag
from kanibako.settings.config import (
    BOX_META_FILE,
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
            "                    (box_data/ + settings.yaml) but has no index entry\n"
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
            "  box set --null pref.agent.claude.common.plugins\n"
            "                                  request that the agent's bind be\n"
            "                                  SUPPRESSED for this box (spec §2h)\n"
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
    """Refuse a primary ``create --name <name>`` that would reuse an existing box home.

    ⚑ DATA-LOSS GUARD (box-lifecycle I4).  ``create --name X`` materialises the box
    at ``std.boxes/X`` with ``mkdir(exist_ok=True)`` — so if that home is already
    occupied the create MERGES into the existing box's data instead of failing.
    Combined with the retained metadata of a ``deregistered`` box, a later
    ``rm X --purge`` (resolving the deregistered entry) would then delete the
    freshly-created box's home — silent data loss.  Refuse the create up front,
    naming the ``register`` / ``purge`` recovery.

    Detects, for the target home ``std.boxes/<name>``:

    * DEREGISTERED — a ``registry_store`` deregistered entry parked by ``rm`` (no
      ``--purge``); the exact hazard.  Recoverable via ``register`` (readopt) or
      deletable via ``rm --purge``.
    * ORPHANED metadata — a ``std.boxes/<name>`` dir with NO active membership and
      NO deregistered entry (a hand-left dir, or a create that crashed before its
      write-ahead journal entry).  Neither ``register`` nor ``rm`` resolves it by
      name today, so the guidance points at removing the dir / choosing another name.

    A genuine half-create being RE-RUN (a pending ``create`` journal entry keyed by
    this home) is the sanctioned recovery re-entry and is NOT refused — it is left
    to :func:`run_create`'s recovery path.  ACTIVE-name collisions are handled
    UPSTREAM by :func:`check_primary_box_name_free` (which raises before this runs),
    so this guard never sees a live-membership name.

    Fires UNCONDITIONALLY of the ``register`` flag: ``run_create`` always resolves
    with ``register=False`` (deferring the up-front name-uniqueness check), so this
    is what closes the reuse hole on the deferred path.  Raises
    :class:`ProjectError` on a conflict; returns ``None`` when the home is free —
    the common case, leaving a normal create byte-identical.
    """
    from kanibako.project import registry_store
    from kanibako.launch import journal

    box_dir = std.boxes / name

    # DEREGISTERED wins over any journal crumb: a box that was deliberately
    # `rm`-deregistered must be recovered via `register` / deleted via `--purge`,
    # never silently reused by `create`.  This refusal MUST precede the
    # pending-create allow below — otherwise a STALE `create` journal entry
    # (left by a register->clear-window crash that `rm` does not clear) would
    # FALSE-ALLOW a `create --name X <new path>` to merge into the deregistered
    # box's retained home (reopening the data-loss window).  A legitimate
    # half-create has NO deregistered entry, so this ordering never refuses one.
    if registry_store.lookup_deregistered(std.registry, name) is not None:
        raise ProjectError(
            f"a box named '{name}' already exists as a deregistered box "
            f"(metadata retained by 'rm'); run 'kanibako box register {name}' to "
            f"recover it or 'kanibako box rm {name} --purge' to delete it, then "
            f"retry."
        )

    # A half-create being recovered (pending `create` entry for this home, and
    # NOT deregistered per the check above) is the legitimate re-entry — never
    # refuse it (run_create completes the replay).
    if journal.pending_create(std.journal, box_dir) is not None:
        return

    if box_dir.is_dir():
        raise ProjectError(
            f"a box named '{name}' already has orphaned metadata at {box_dir} "
            f"(no active or deregistered registration); remove it (e.g. "
            f"'rm -rf {box_dir}') or choose a different --name, then retry."
        )


def _check_persona_store_for_create(agent_ref: str, project_path) -> str | None:
    """Create-side persona-grata store CHECK for ``create --agent <pid>+<hid>``.

    The create-side trigger (DESIGN §4): when the explicit agent ref names a
    persona whose persona-grata store entry EXISTS, read the store and refuse
    the create NOW if it cannot yield a usable persona — BEFORE the persona
    create verdict, so a broken store is reported as itself rather than as the
    verdict's downstream "no endpoint configured".

    ⚑ NOTHING IS WRITTEN.  This used to IMPORT the store into
    ``agents/<node>/settings.yaml``; the store is a LIVE resolution input now
    (``read_persona_bundle`` → the launch's persona cascade level), and the
    agent settings file holds user-intent values only, so there is nothing to
    persist and no ``settings.yaml`` for a corrupt-file arm to trip over.  The
    gates themselves are UNCHANGED: same hard errors, same WARN-ONLY probe.

    Returns an ``"Error: …"`` message for the caller to print-and-refuse
    (malformed ref, unusable store config, a store naming no endpoint), or
    ``None`` when there is nothing to do (bare/plain ref, no store entry,
    harness not installed, a harness with no persona reader — all fall through
    to today's create behavior) or the store checked out (a soft token-pointer
    warning is printed here).
    """
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
        # ``None`` cannot happen (the entry located a moment ago) but costs
        # nothing to honour.  ``no_reader`` DOES: a goose persona is configured
        # entirely through the keyspace and may own a store dir purely for its
        # ``.secret_path``, so "this harness cannot read a store config" is a
        # fall-through, never a refusal.
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
    # WARN-ONLY first-ever probe (see the docstring).  ⚑ WARN-ONLY applies to
    # the two ANSWERED non-PASS verdicts; ``NOT_APPLICABLE`` means nothing was
    # learned about the token and nothing will be for this input (a harness with
    # no probe, an unreadable token, an endpoint that requires a model this
    # persona does not name) — a valid configuration nobody can act on, so it
    # goes to the log, exactly as on the launch path
    # (:func:`~kanibako.commands.start._persona_probe_error`).  ⚑ A persona that
    # simply names no model is still PROBED (model field omitted), so a dead
    # token still warns here.
    if bundle.token_path is not None:
        try:
            outcome = target.verify_persona(
                bundle.endpoint, bundle.token_path, bundle.model,
            )
        except Exception:
            # Probe contract is never-raise; hold plugins to it. A plugin bug IS
            # an anomaly, so it warns rather than going quiet.
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

    # Cross-kind name guard (per-kind name policy, Jei 2026-07-08): an EXPLICIT
    # --name for a PRIMARY box that collides with a WORKSET name shadows that
    # workset in bare-name resolution (the box wins), so refuse UNLESS --force.
    # SAME-KIND (another primary box already owns the name) is unconditional.
    # The deferred registration (``_register_new_box``) re-checks with the same
    # flag; doing it HERE refuses cleanly BEFORE the box dir + seed materialize
    # (standalone boxes are not in the primary/workset name domain — skip them).
    if getattr(args, "name", None) and not args.standalone:
        from kanibako.errors import ProjectError
        try:
            check_primary_box_name_free(
                std.primary_workset, std.registry,
                args.name, str(effective_path),
                force=getattr(args, "force", False),
            )
            # ⚑ I4 data-loss guard: ALSO refuse when the box HOME std.boxes/<name>
            # is occupied by a DEREGISTERED entry or an ORPHANED metadata dir
            # (active-name collisions are already refused by
            # check_primary_box_name_free above).  This fires on the register=False
            # create path that skips the up-front uniqueness check — the reuse hole
            # a later deregistered-purge would exploit to delete a live box's home.
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
    # ALREADY-INITIALIZED, read off the PROBE — captured BEFORE
    # ``_name_new_box_probe`` below, which mutates ``_probe.name``.  The refusal
    # itself is deferred to just past the persona pre-flight so error ORDERING is
    # unchanged; only the MUTATION it used to trail now happens after it.
    _already = box_tree_materialized(_probe)
    _name_new_box_probe(std, _probe)
    # PERSONA-GRATA STORE CHECK (create trigger): an explicit --agent persona
    # ref whose store entry exists is READ NOW, BEFORE the verdict below, so a
    # store that cannot yield a usable persona is refused as ITSELF rather than
    # as the verdict's downstream "no endpoint configured".  Bare refs / absent
    # store entries fall through untouched.  ⚑ Nothing is written: the store is
    # a live resolution input, so a create leaves no persona artifact behind.
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

    # ALREADY-INITIALIZED REFUSAL — HOISTED ABOVE THE MATERIALISING RESOLVE.
    #
    # It used to sit AFTER that resolve, reading ``proj.is_new``.  But the resolve
    # runs with ``initialize=True``, and its recovery arms re-create a missing home
    # (``resolve_project``) and a missing home + workspace
    # (``resolve_standalone_project``) BEFORE returning — so a box whose home had
    # been deleted got it silently bootstrapped and was THEN told "already
    # initialized", a message that was false at the moment it was printed.  Asking
    # the NON-materialising probe instead makes the message TRUE: nothing has been
    # written when it prints.  (Standalone AND primary — both resolvers have the
    # shape.)  Message and exit code are unchanged.
    #
    # J1 interrupted-create RECOVERY is unchanged and still takes precedence: a box
    # that already exists but carries a pending create journal entry is a
    # half-completed create (crash between seed-start and the registry write), and
    # is COMPLETED by replay below rather than refused.  The journal entry (not
    # ``is_new``) drives completion — that is the central J1 fix, restoring the HARD
    # INVARIANT "registered ==> no pending entry" for PRIMARY and STANDALONE.
    # ``_pending_create_entry`` keys on the box dir, which the probe resolves
    # identically to the materialising call (same inputs, ``initialize`` aside).
    is_recovery = _already and _pending_create_entry(std, _probe) is not None
    if _already and not is_recovery:
        print(
            f"Error: project already initialized in {_probe.project_path}",
            file=sys.stderr,
        )
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

    # Persist image + standalone .gitignore only on a FRESH create — a recovery
    # re-create reuses the half-built box's already-written meta (the on-disk
    # record is authoritative; do not overwrite it with possibly-different args).
    if proj.is_new:
        # §1A CREATE EXCEPTION (R-11a) via the ONE shared gate — the SAME gate the
        # launch-materialization path calls (`start.py`), so the persist rule has
        # exactly one home. Only an EXPLICITLY-GIVEN ``-i``/``--image`` persists:
        # a no-flag create bakes NOTHING into the box tier, and the box resolves
        # the live cascade for its image (single source — the stored default
        # stays at its own tier and later default changes reach this box).
        # The write target is the BOX-TIER settings file from the ONE pair (M-8)
        # — for standalone that is ``box_data/settings.yaml``, the same file
        # ``box set box.image=…`` writes and the launch cascade reads as the box
        # tier.  Deriving it independently here is exactly the split M-8 exists
        # to prevent.
        project_toml, _ = box_workset_settings_paths(proj)
        persist_creation_flags(
            project_toml, materializing=proj.is_new, image=args.image,
        )

        # --private: turn the box PRIVATE *before* the home seed runs, so the
        # host OAuth cred is never forwarded.  Persist both box-scope auth
        # toggles OFF into the box settings.yaml via the SANCTIONED settings
        # write (set_config_value; NOT a raw yaml dump) — the same box-scope
        # path `config set` uses, so the keys land in the box.auth.* slot the
        # launch snapshot reads.  `project_toml` IS the box-tier file from
        # `box_workset_settings_paths` — the SAME file seed_new_box's
        # `resolve_auth_source` reads through the snapshot's box tier — and this
        # runs BEFORE the seed below, so the seed then resolves tier="box"
        # (source_root=None) and `seed_cred_files` no-ops.  ⚑ AUTH-CRITICAL that the
        # two agree: if this wrote a file the snapshot did not read as the box tier,
        # a supposedly-private box would resolve a sharing tier and leak the host
        # OAuth token into the seed.  Additive/non-destructive: no scrub, only two
        # boolean overrides.
        if getattr(args, "private", False):
            from kanibako.settings.config_interface import set_config_value
            from kanibako.settings.config_keys import ConfigLevel
            from kanibako.errors import KanibakoError
            for _auth_key in (
                "box.auth.global_enabled",
                "box.auth.workset_enabled",
            ):
                # set_config_value RETURNS an "Error: …" string (never raises) on a
                # refused/unknown-key write; success is a "Set …" string.  This is
                # an AUTH-CRITICAL write — a silent no-op here would leave a
                # supposedly-private box resolving a sharing tier and leak the host
                # OAuth token into the seed.  Fail LOUD (before the seed runs) if
                # the write did not succeed, rather than leak silently.
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

        # Persist the box's AGENT SELECTION (`create --agent <ref>`): write the
        # REQUEST `pref.system.agent` into the box settings via the SANCTIONED
        # settings write (the same route `box set pref.system.agent=<ref>` uses —
        # single-route, no bespoke YAML poke), so a plain `start` resolves this
        # agent through the ordinary §2h pref path instead of falling through to
        # the system default.  ⮕ P7: this used to write the RETIRED `box.agent_name`
        # (spec §2b); a pref is how a box influences a key that resolves above it.
        # Applies to ANY selector — persona or plain (`create --agent goose` must
        # make plain start launch goose too).  The RAW user ref is stored
        # (selection canonicalizes on read, exactly as for a hand-set key);
        # `start --agent <other>` remains an ephemeral override on top (CLI args
        # ephemeral over settings — start never persists).  Fresh create only: a
        # recovery re-create reuses the half-built box's on-disk record (parity
        # with the image/--private writes above).
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
                # A silent no-op would create a box whose plain start launches
                # a DIFFERENT agent than the one the user just selected.
                print(
                    f"Error: failed to persist the box agent selection "
                    f"({_msg}).",
                    file=sys.stderr,
                )
                return 1

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
    # ⚑ THE CANON SKELETON (J-7) — AFTER the seed, INSIDE the journal window.
    # After, because the seed writes ``canon/{notebook,workbook}`` UNDER the same
    # root this step makes 555: protect first and those copies die with EACCES.
    # Inside, because an interrupted create must replay it like every other step.
    materialize_canon_skeleton(proj.shell_path)
    _register_new_box(std, proj, force=getattr(args, "force", False))
    _clear_create_entry(std, proj)

    mode = "standalone" if args.standalone else "default"
    # Explicit-create (Jei 2026-07-11g): `create` MAKES the box but does NOT launch
    # it — a launch (`start` / bare `kanibako` / `code` / `shell`) no longer
    # auto-creates, so tell the user exactly how to start what they just made.
    start_hint = (
        "Start the box by executing 'kanibako' (shortcuts to 'kanibako start') "
        "from its path (or add a path argument)."
    )
    if is_recovery:
        print(f"Resumed interrupted {mode} project in {proj.project_path}")
    else:
        print(f"Created {mode} project in {proj.project_path}")
    print(start_hint)
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
    from kanibako.project import registry_store
    standalone = registry_store.load_standalone(std.registry)
    # DEREGISTERED boxes (rm without --purge) are never active, so they are only
    # surfaced when NOT filtering to active-only (plain `list` / `list --all`,
    # not `--active` / `ps`).  list_deregistered self-heals genuinely-stale
    # entries; with none parked it returns {} and writes nothing (list stays
    # byte-identical for a tree without deregistered boxes).
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
        pass  # No runtime available — all projects show as stopped.

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

    # DEREGISTERED boxes (registry.deregistered: name → recovery blob).  Surfaced
    # in a dedicated section — with the DEREGISTERED status as the marker — so a
    # user can SEE what they may `register` (recover) or `rm --purge` (delete);
    # today these are invisible.  Only shown when entries exist, keeping a tree
    # with no deregistered boxes byte-identical.  `quiet` prints bare names.
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
    """Remove *target*, tolerating files a rootless container created.

    Thin alias for :func:`kanibako.runtime.container.remove_box_tree`, which is where this
    body now lives so that EVERY box-tree deleter can reuse it — ``extract``,
    ``move``, ``duplicate`` and ``purge`` all need the same ``podman unshare``
    escalation, and since J-7 they need it on every box (the canon skeleton is
    root-owned by construction, not only when an agent happened to write as root).
    Kept as a name because ``rm``'s call sites and tests read against it.
    """
    from kanibako.runtime.container import remove_box_tree

    return remove_box_tree(target)


def _assert_deletable(path, *, must_be_under: Path | None = None) -> Path:
    """Validate *path* is safe to ``rm -rf`` and return its resolved form.

    ⚑ DESTRUCTIVE-SAFETY gate for purge.  A ``deregistered`` entry stores a
    metadata path that a later ``--purge`` deletes; that stored path is untrusted
    (a corrupt/crafted registry must never let purge delete outside a box's own
    metadata).  Refuses — rather than silently no-op-deleting — when the resolved
    path is empty, ``/``, ``$HOME``, or (when *must_be_under* is given) not a
    strict descendant of that root.  ``resolve()`` collapses ``..`` and follows
    symlinks, so a ``..``/symlink escape resolves OUTSIDE *must_be_under* and is
    rejected.  Raises :class:`ProjectError` on any violation.
    """
    raw = str(path).strip()
    if not raw:
        raise ProjectError("refusing to delete: empty metadata path")
    resolved = Path(raw).resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve()}
    if resolved in forbidden:
        raise ProjectError(f"refusing to delete a protected path: {resolved}")
    if must_be_under is not None:
        root = must_be_under.resolve()
        # Strict containment: a direct-or-deeper descendant, never the root
        # itself (deleting std.boxes/ would take every box with it).
        if resolved == root or root not in resolved.parents:
            raise ProjectError(
                f"refusing to delete {resolved}: not contained under {root}"
            )
    return resolved


def _teardown_primary_box(std, name: str, metadata_dir: Path) -> bool:
    """Delete a PRIMARY box's metadata: box dir + vault ro/rw + helper log.

    The single deletion routine shared by the active ``--purge`` path and the
    deregistered ``--purge`` path — same paths, same order, same guards as the
    former inline block.  Returns True if the box dir was removed.  The caller is
    responsible for containment-validating *metadata_dir* first when it comes
    from an untrusted ``deregistered`` entry (see :func:`_assert_deletable`).
    """
    removed = _purge_dir(metadata_dir)
    if removed:
        print(f"Removed metadata: {metadata_dir}")
        # Phase 5: PRIMARY vault lives under @config.primary_workset/vault/
        # {ro,rw}/<name> (not under metadata_dir) — remove it too.
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
    # Remove the per-box helper log if present.  PRIMARY logs live at
    # @config.primary_workset/logs/<box>.jsonl (box == registry name).
    log_file = std.primary_logs / f"{name}.jsonl"
    if log_file.is_file():
        log_file.unlink()
        print(f"Removed log: {log_file}")
    return removed


def _teardown_standalone_box(root: Path) -> bool:
    """Delete a STANDALONE box's in-tree metadata, never the workspace root.

    Removes the in-tree ``box_data/`` marker + root ``settings.yaml`` + ``vault/``
    — the same set the active standalone purge and ``purge`` command delete.  The
    user's workspace files (and *root* itself) are never touched.  Returns True if
    the ``box_data/`` marker was removed.
    """
    from kanibako.settings.paths import _STANDALONE_META_DIR

    metadata_dir = root / _STANDALONE_META_DIR
    if _purge_dir(metadata_dir):
        print(f"Removed metadata: {metadata_dir}")
        # The ROOT settings.yaml is the WORKSET tier and the other half of the §5
        # detection marker — drop it too so the box is not re-detected.  (The BOX
        # tier lives INSIDE box_data/ and went with the dir above; the old comment
        # here said settings lived "at the ROOT, not in box_data/", which is the
        # retired pre-P2 model.)
        settings_file = root / BOX_META_FILE
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
    """Best-effort read of a box's ``box.image`` from its settings.yaml.

    Captured into the deregistered blob for a later readopt (I2); purge does not
    need it, so any read failure degrades to ``None`` rather than erroring.
    """
    try:
        from kanibako.settings.config_io import load_doc

        data = load_doc(settings_file)
        image = dict(data.get("box", {})).get("image")
        return str(image) if image else None
    except Exception:  # noqa: BLE001 - image capture is best-effort
        return None


def _read_box_image_tiered(box_tier: Path, workset_tier: Path) -> str | None:
    """:func:`_read_box_image` over a ``(box_tier, workset_tier)`` pair, box wins.

    ``box.image`` is a BOX-scope key, so it is read from the box tier; the workset
    tier is consulted only as the R2 downward-default.  That fallback is what keeps
    a pre-P2 standalone box — whose ``box.image`` was written to its ROOT file when
    that file WAS the box tier (M-8) — capturable into the deregistered blob.
    """
    return _read_box_image(box_tier) or _read_box_image(workset_tier)


def _purge_deregistered(std, name: str, entry: dict, args: argparse.Namespace) -> int:
    """Handle ``rm <name>`` when *name* resolves only to a deregistered entry.

    Without ``--purge`` the box is already deregistered — print the recovery
    guidance and return 0 (no re-error, no re-deregister).  With ``--purge``,
    validate the stored metadata path for containment, delete it via the shared
    per-kind teardown, then drop the deregistered entry.  IDEMPOTENT: an entry
    whose dir is already gone just gets dropped, no error.
    """
    from kanibako.project import registry_store
    from kanibako.errors import UserCancelled
    from kanibako.settings.paths import _STANDALONE_META_DIR
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

    # ⚑ I4 STALE-ENTRY belt-and-suspenders: the name may have been REUSED by a new
    # ACTIVE box that now occupies the SAME metadata path (e.g. a create that
    # re-claimed `std.boxes/<name>`).  Purging by this stale deregistered entry
    # would then delete the LIVE box's home — so refuse, and drop the stale entry
    # (it is definitively wrong: an active box owns that path) so it stops
    # shadowing.  NEVER delete the active box's home.
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

    # --purge on a deregistered box: validate + delete its retained metadata.
    try:
        if kind == "standalone":
            # metadata is the in-tree ROOT; only fixed children are deleted, but
            # still refuse a bare/protected root.
            root = _assert_deletable(metadata)
        else:
            # PRIMARY: metadata must be strictly under std.boxes/.
            metadata_dir = _assert_deletable(metadata, must_be_under=std.boxes)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if kind == "standalone":
        exists = (root / _STANDALONE_META_DIR).is_dir()
    else:
        exists = metadata_dir.is_dir()

    if not exists:
        # Idempotent: dir already gone (deleted out-of-band) → drop the stale
        # entry, no error, no prompt (nothing to delete).
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
    """Resolve a ``box rm`` *target* to a registered standalone box.

    Returns ``(box_name, root)`` when *target* names a standalone box, else
    ``(None, None)``.  *target* may be a registered standalone box NAME (looked
    up in ``registry.standalone``) or a PATH (resolved by ancestor-walk
    detection, then matched to its registered root).  Mirrors how ``box purge``
    finds standalone boxes (BUG-C).
    """
    from kanibako.project import registry_store
    from kanibako.settings.paths import BoxMode, detect_project_mode

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
    from datetime import datetime, timezone

    from kanibako.project import registry_store
    from kanibako.errors import UserCancelled
    from kanibako.settings.paths import _STANDALONE_META_DIR
    from kanibako.utils import confirm_prompt

    print(f"Removing standalone box: {box_name} ({root})")
    registry_store.unregister_standalone(std.registry, box_name)
    print(f"Removed '{box_name}' from the registry")

    root_path = Path(root) if root is not None else None
    metadata_dir = root_path / _STANDALONE_META_DIR if root_path is not None else None
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
        # Park a deregistered entry so a later `rm <name> --purge` can find the
        # retained in-tree metadata BY NAME (the registry.standalone index is
        # already dropped above).
        registry_store.register_deregistered(
            std.registry,
            box_name,
            kind="standalone",
            workspace=str(root_path),
            metadata=str(root_path),
            # The box's ``box.image`` lives in the BOX tier (M-8); the ROOT file is
            # the workset tier and may still carry it as a pre-P2 downward default,
            # so fall back to it.  Best-effort capture for a later readopt — either
            # read failing degrades to None.
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
    # PRIMARY boxes live in the primary per-workset ``boxes:`` membership (the
    # sole store since the global ``projects:`` section retired); worksets in the
    # global ``worksets:`` name section.  ``section`` carries "projects" for a
    # primary box (drives the membership unregister below) or "worksets".
    primary_boxes = load_primary_boxes(std.primary_workset)

    # Resolve target: try as a registered name first, then as a path.
    name: str | None = None
    section: str | None = None
    path: str | None = None

    if target in primary_boxes:
        name, section, path = target, "projects", primary_boxes[target]
    elif target in names["worksets"]:
        name, section, path = target, "worksets", names["worksets"][target]

    if name is None:
        # Try as a path (reverse lookup): the primary membership first, then the
        # global worksets index.
        primary_hit = primary_box_name_for_workspace(std.primary_workset, target)
        if primary_hit is not None:
            name, section, path = primary_hit, "projects", primary_boxes.get(primary_hit)
        else:
            result = lookup_by_path(std.registry, target)
            if result is not None:
                name, section = result
                path = names[section][name]

    if name is None:
        # STANDALONE boxes are not in the name index — they live in
        # registry.standalone (box name → root). Resolve the target as either a
        # registered standalone box name or a path (ancestor-walk detection), so
        # `box rm <canonical-name>` and `box rm <path>` both clean up an
        # otherwise-uncleanable standalone box (BUG-C).
        sa_name, sa_root = _resolve_standalone_target(std, config, target)
        if sa_name is not None:
            return _rm_standalone(std, sa_name, sa_root, args)

    if name is None:
        # Not active anywhere — check the global ``deregistered`` section by
        # name.  THE REPORTED-BUG FIX: after `rm` (no --purge) the active
        # membership is gone, so `rm <name> --purge` (and a re-`rm`) must resolve
        # the retained metadata here instead of erroring "not registered".
        dereg = registry_store.lookup_deregistered(std.registry, target)
        if dereg is not None:
            return _purge_deregistered(std, target, dereg, args)

    if name is None or section is None:
        print(f"Error: '{target}' is not a registered project or workset.", file=sys.stderr)
        return 1

    kind = "workset" if section == "worksets" else "project"
    print(f"Removing {kind}: {name} ({path})")

    # Unregister from the registry (membership for a primary box, the global
    # worksets index for a workset) — re-routing the primary arm to membership
    # closes the pre-existing gap where `rm` left the membership stale.
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

            # Shared teardown — same paths/order/guards as the deregistered-purge
            # path (box dir + PRIMARY vault ro/rw + helper log).
            _teardown_primary_box(std, name, metadata_dir)
        else:
            print(f"No metadata directory found at {metadata_dir}")
    else:
        # No --purge: retain the metadata and park a ``deregistered`` entry so a
        # later `rm <name> --purge` (or, in I2, `register <name>`) can find it BY
        # NAME — the active membership was just dropped above.  Worksets are NOT
        # boxes (they keep their own lifecycle), so they are never parked here.
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
    """Readopt a DEREGISTERED box: move it from ``deregistered`` back to active.

    INDEX-ONLY and SEED-FREE — writes ONLY the active membership index (primary
    per-workset ``boxes:`` / ``registry.standalone``) and drops the deregistered
    entry; NEVER touches the box's home content or re-materializes templates
    (membership is itself the seed signal, so a re-index must not re-seed).

    Per-kind routing off the entry's ``kind``.  Self-heals a stale entry whose
    metadata is gone (drops it, reports nothing to restore).  Conflict-safe: the
    reused registration APIs refuse when an ACTIVE box already owns the name
    (``check_primary_box_name_free`` / the ``standalone`` name check) or the
    workspace path (``register_workset_box``'s one-box-per-path guard), so a
    readopt never clobbers a live box.
    """
    from kanibako.project import registry_store
    from kanibako.launch.box_resolve import standalone_settings_present

    kind = entry.get("kind")
    workspace = entry.get("workspace")
    metadata = entry.get("metadata")

    if kind == "standalone":
        root = Path(str(metadata)).resolve() if metadata else None
        # Self-heal: the in-tree marker is gone → nothing to restore, drop entry.
        if root is None or not standalone_settings_present(root):
            registry_store.unregister_deregistered(std.registry, name)
            print(
                f"No standalone metadata found for '{name}' (dropped stale entry); "
                "nothing to restore.",
                file=sys.stderr,
            )
            return 1
        # Conflict: the name is already an ACTIVE standalone box at a DIFFERENT
        # root — refuse rather than clobber the live entry.
        registered = registry_store.load_standalone(std.registry)
        other = registered.get(name)
        if other is not None and Path(other).resolve() != root:
            print(
                f"Error: an active standalone box already owns the name '{name}' "
                f"({other}); refusing to readopt over it. Purge or move it first.",
                file=sys.stderr,
            )
            return 1
        # Index-only, seed-free re-register (overwrites a matching name->root).
        registry_store.register_standalone(std.registry, name, root)
        registry_store.unregister_deregistered(std.registry, name)
        print(f"Registered standalone box '{name}' at {root}.")
        return 0

    # PRIMARY (default) box.
    from kanibako.settings.paths import register_primary_box_name

    metadata_dir = Path(str(metadata)).resolve() if metadata else None
    # Self-heal: the box dir is gone → nothing to restore, drop entry.
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
    # Index-only, seed-free re-register into the PRIMARY membership.  The reused
    # API enforces the conflict guards ($HOME, SAME-kind active-box name, the
    # one-box-per-workspace-path invariant, and the CROSS-kind workset-shadow
    # refusal unless --force); a violation refuses cleanly with guidance.
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
    """Re-register a box into the registry (INDEX-ONLY, SEED-FREE).

    Unifies two index-only operations (design box-lifecycle I2):

    * READOPT a box deregistered by ``rm`` (no ``--purge``): move it from the
      global ``deregistered`` section back to active membership (primary
      per-workset ``boxes:`` / ``registry.standalone``).  Resolved by NAME first.
    * REGISTER a never-registered STANDALONE box that exists on disk (``box_data/``
      + root ``settings.yaml``) but has no ``registry.standalone`` entry.
      Resolved by PATH.

    NEVER re-seeds, NEVER touches the box's home content — registration is purely
    an index write (membership is itself the seed signal).  Refuses if an ACTIVE
    box already occupies the target name or workspace path; worksets are refused
    (they keep their own lifecycle).
    """
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

    # 1. DEREGISTERED readopt — resolve by NAME first (design: a bare name → the
    #    deregistered section first).  The retained blob carries ``kind``, so the
    #    per-kind readopt routes off it.
    dereg = registry_store.lookup_deregistered(std.registry, target)
    if dereg is not None:
        return _readopt_deregistered(std, target, dereg, force=force)

    # 2. Already-ACTIVE guards — a clear "already registered" (rc 0, no-op) for a
    #    live box, and a redirect for a workset (register is box-only).
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

    # 3. STANDALONE register-later — the target is a PATH to an on-disk standalone
    #    box (box_data/ + root settings.yaml) with no index entry.  Reuse
    #    import_standalone: it is INDEX-ONLY + SEED-FREE (the box was seeded where
    #    it was created) and already composes the kuid-first name + refuses a
    #    name collision to a different root.
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
        # "No box directory" collapses TWO states that need different words.
        # A box with a name is REGISTERED: its directory is gone, which is not
        # an unused directory — it is exactly what a launch refuses (MBR-6), and
        # its cure is MODE-DEPENDENT (``create`` for a PRIMARY box, ``workset
        # disconnect`` + ``connect`` for a NAMED one).  Defer to that refusal's
        # own message rather than restating it, so ``info`` and a launch can
        # never name different cures.
        from kanibako.commands.start import _unbuilt_box_error
        unbuilt = _unbuilt_box_error(proj) if proj.name else None
        if unbuilt is not None:
            print(unbuilt, file=sys.stderr)
            return 1
        if proj.group is not None and proj.group.is_default:
            # ⚑ NOT "start a session with 'kanibako start'": since the v1.7.0
            # explicit-create gate a launch NEVER materialises a box — it errors
            # and points at ``create``.  Naming ``start`` here described
            # behaviour we do not have.
            print("This directory has not been used with kanibako yet.")
            print("A launch will not create a box for it — create it with:")
            print("  kanibako box create")
        else:
            print("This directory has not been initialized.")
        return 1

    # Load merged config for image info.
    project_toml, workset_path = box_workset_settings_paths(proj)
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
    # "n/a (no target)" rather than erroring out.  Uses the ONE selection seam
    # (agent_select.select_agent — system.agent < workset pref < box pref).
    agent_display = "n/a (unresolved)"
    try:
        from kanibako.agent_ref import display_agent_ref
        from kanibako.settings.agent_select import select_agent
        _sel = select_agent(std=std, proj=proj)
        agent_name = _sel.node
        # ⚑ SHOW WHERE IT CAME FROM (P7). "Which agent does this box run, and WHY?"
        # is the question a retired-key or wrong-default box makes urgent, and the
        # answer is not in any one file: it may be the stored ``system.agent``, a
        # workset/box ``pref.system.agent``, or the installed-count rule picking the
        # only agent installed. ``AgentSelection.source`` already carries it — this
        # is the one place a user can read it.
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
        # A refused RETIRED key (migration M-4) surfaces HERE rather than being
        # swallowed as "unresolved" — `box info` is where a user looks when a box
        # will not start.
        agent_display = f"n/a ({exc})" if "RETIRED" in str(exc) else agent_display
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
        ("Agent", agent_display),
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
    from kanibako.settings.config_keys import is_known_key

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
    """Resolve the box the ``box`` config verbs address — refusing the
    ``__unregistered__`` phantom (MBR-6, Jei 2026-08-02f).

    ``_resolve_local_dir`` answers "no PRIMARY box is registered for this
    workspace" with the SENTINEL ``("", std.boxes / "__unregistered__")`` — a
    name-assignment placeholder for the resolvers that go on to pick a real name
    (``resolve_project``'s create block; ``restore``, which rewrites it with the
    box's real name).  The config verbs pick NO name: they simply address the box
    the sentinel points at, so ``box set box.image=…`` from a cwd that is no box
    used to WRITE ``boxes/__unregistered__/settings.yaml`` and report success —
    materialising a placeholder instead of refusing.

    Refused here, at the verb's own seam, so the sentinel keeps working for the
    resolvers that legitimately replace it.  ``mode is primary and not name`` IS
    the sentinel: for a PRIMARY box an empty name means the membership
    reverse-lookup missed, which is the only way that path is returned; NAMED
    raises :class:`WorksetError` for a non-member, and STANDALONE addresses its
    own in-tree files rather than ``std.boxes``.

    Raises :class:`ProjectError` (the shape every caller here already handles).
    """
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
    """Shared project-config engine dispatch.

    Handles get, set, show, reset operations via the config_interface engine.
    Uses the known-key heuristic to disambiguate project names from config keys.
    """
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

        # --reset KEY: reset a specific key
        reset_key = args.reset
        try:
            proj = _resolve_config_subject(std, config, project_dir)
        except ProjectError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        # The box-tier target AND the workset tier come from the ONE pair (M-8), so
        # reset clears exactly the file set/get address.
        project_toml, reset_ws_path = box_workset_settings_paths(proj)
        # Full launch cascade so the honest cleared-message can name the
        # now-effective value + source tier (item 1) — the SAME context the box
        # SET handler threads. A resolution failure just leaves the agent name
        # empty → the message degrades to the cleared-only form.
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

    # Parse the key/value argument
    action, key, value = parse_config_arg(
        key_value_arg, set_null=getattr(args, "null", False),
    )

    # Resolve the project
    try:
        proj = _resolve_config_subject(std, config, project_dir)
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # ⚑ THE M-8 CHOKEPOINT: get / set / show / reset all address the box through the
    # ONE pair, so a box-scope write and the read that follows it CANNOT disagree.
    # (The box's docker ``env`` FILE tier is GONE — R-39/RQ-1; the env family is
    # the settings key ``box.env.<VAR>``, which lives in this same pair's file.)
    project_toml, workset_path = box_workset_settings_paths(proj)

    if action == ConfigAction.show:
        agent_state = None
        env_resolved = None
        category_snapshot = None
        category_error = None
        if args.effective:
            from kanibako.settings.agent_config import (
                agent_settings_path,
                load_agent_config,
            )
            from kanibako.targets import resolve_target
            from kanibako.commands.start import (
                _build_config_env,
                _effective_behavior_for_display,
            )
            agent_name = ""
            effective_selection = None
            try:
                from kanibako.settings.agent_select import select_agent
                # Informational --effective display: tolerate a resolution
                # failure (degrade to the "general" no-agent state below)
                # rather than erroring.  The ONE selection seam (§1A/§2h).
                effective_selection = select_agent(std=std, proj=proj)
                agent_name = effective_selection.node
                target = (
                    resolve_target(harness_of(agent_name), proj.project_path)
                    if agent_name
                    else None
                )
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
            # The PATH-DELIVERY categories + their materialised derivations (§0):
            # resolved off the SAME single launch pipeline a start takes, so the
            # display cannot drift from what actually mounts. A collision is
            # REPORTED rather than raised — this display is the M-7 detection
            # recipe ("resolve the snapshot and look for duplicate dests"), so
            # dying on the very fault it exists to surface would be backwards.
            from kanibako.commands.start import (
                _persona_values_for,
                _resolve_launch_snapshot,
            )
            from kanibako.errors import KanibakoError
            try:
                category_snapshot, _reconciled = _resolve_launch_snapshot(
                    std=std, proj=proj, agent_name=agent_id,
                    system_settings_path=std.settings,
                    agent_cfg_path=agent_cfg_path,
                    desc=None, install=None,
                    target=target, agent_cfg=agent_cfg,
                    # The SAME persona-store tier the launch resolves against —
                    # required for the promise the comment above makes. The store
                    # supplies ``secret_path.<VAR>``, a PATH-DELIVERY category:
                    # without the tier this view would list a persona box's mounts
                    # MINUS the token the launch actually mounts (and minus its
                    # ``env`` passthrough). Tolerant + ``None`` for a bare agent,
                    # so a store-less box renders exactly as before.
                    persona_values=_persona_values_for(agent_id, target),
                    # A DISPLAY verb must not write to disk: the core table's
                    # vault create-if-missing is a LAUNCH guarantee, not a
                    # read one. The binds are emitted either way.
                    guarantee_create=False,
                    # ⚑ The SAME §1A selection level the launch installs (P7) —
                    # without it this display would resolve ``@system.agent``
                    # differently from the launch it claims to show (e.g. an
                    # autopicked or ``--agent`` box: the displayed
                    # ``meta.box.auth.workset_path`` would drop the agent segment).
                    # A display that disagrees with the launch is worse than no
                    # display.
                    #
                    # ⚑ SELECTION ONLY — and that is the whole of the CLI level for a
                    # READ verb (P8). ``box config`` carries none of the launch's
                    # ephemeral VALUE flags (``-M`` / ``-N``-``-C``-``-R`` /
                    # ``--image`` / ``--share-images`` live on ``start``), so there
                    # is nothing ephemeral to install here. Rendering a flag from
                    # some OTHER invocation would be the "a flag mutated a stored
                    # value" failure §1A forbids, one screen removed:
                    # ``--effective`` reports what the box IS configured to do, and
                    # a per-launch override is by definition not that.
                    #
                    # ⚑ ``--agent`` is the EXCEPTION, and it is a PRE-EXISTING
                    # DEFECT, not a P8 decision: the blanket flag injector puts it
                    # on every leaf parser, so ``box config --agent goose
                    # --effective`` PARSES — but this call site (and the other four
                    # read-verb ``select_agent`` sites) never passes it through, so
                    # it is silently IGNORED. It is advertised and inert. P8 leaves
                    # the behaviour exactly as it found it rather than quietly
                    # changing what a read verb reports; honouring it (or refusing
                    # it) is tracked as a follow-on.
                    cli_level=(
                        effective_selection.selection_level
                        if effective_selection is not None
                        else None
                    ),
                )
                # The box's resolved config ENV, composed by the SAME helper the
                # launch uses (agent tier under the reconciled
                # ``<scope>.env.<VAR>`` winners), so the display cannot claim an
                # env the box will not get — or miss one it will. It needs the
                # RECONCILE, which is why it lands here rather than beside the
                # agent state above.
                env_resolved = _build_config_env(
                    agent_cfg.env if agent_cfg is not None else {},
                    _reconciled.envs,
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
            category_error=category_error,
        )

    if action == ConfigAction.get:
        # Bare env.* — RETIRED (R-39): refused at the HANDLER because the get
        # engine returns values, never error strings (the same handler-side split
        # as the workset bare-agent-key read guard).
        from kanibako.settings.config_keys import bare_env_retired_error
        _env_err = bare_env_retired_error(
            key, verb="read", command_scope=ConfigLevel.box,
        )
        if _env_err is not None:
            print(_env_err, file=sys.stderr)
            return 1
        # The box's resolved agent NODE — needed to name/READ the box-scoped
        # request a BARE agent key redirects to (``pref.agent.<active>.<key>``,
        # P7). Best-effort: an unresolvable agent just means no redirect.
        _get_agent_name = ""
        try:
            from kanibako.settings.agent_select import select_agent
            _get_agent_name = select_agent(std=std, proj=proj).node
        except Exception:
            _get_agent_name = ""
        val = get_config_value(
            key,
            global_config_path=config_file,
            project_toml=project_toml,
            command_scope=ConfigLevel.box,
            active_agent=_get_agent_name or None,
        )
        # A BARE agent behavior key at box scope has no box-writable value of its
        # own: get_config_value redirected the READ to the box's §2h request
        # ``pref.agent.<active>.<key>``. Name the value with that canonical form so
        # the read teaches the request (mirrors the refuse-message ``set`` prints).
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
        # Full launch cascade for a CATEGORY set's set-time E3 probe (Jei (b),
        # 2026-06-29): thread every scope's settings file + the active agent name so
        # a cross-scope @-ref in the new value resolves exactly as it would at
        # launch. The box handler already holds box (project_toml) / workset
        # (workset_path) / system (std.settings) files. The active agent NAME is
        # resolved best-effort: it selects the ``agent.<active>.*`` sub-table the
        # OTHER cascade files may carry (mirroring _effective_behavior_for_display,
        # which likewise passes agent_path=None — the per-agent file stores behavior
        # FLAT, so assemble_levels reads no category subtree from it). A resolution
        # failure just leaves the agent name empty.  The workset tier is the one the
        # pair named above — NOT a second derivation (M-8).
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

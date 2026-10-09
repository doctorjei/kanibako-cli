"""The ``workset`` verb tree: create, list, info, rm, connect/disconnect, config verbs, share.

**_Terminology_**
- _named_ workset: registered under a user-chosen name, rooted at a user directory it owns
- _default_ workset: the synthesized PRIMARY workset, rooted at ``@config.primary_workset``
(kanibako's internal store, NOT a project dir) — so it has NO bindings root
- _share_: a ``workset.bindings.{ro,rw}`` entry, keyed BY its box DESTINATION (R-10)
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.commands.flags import add_null_flag, add_set_force_flag
from kanibako.settings.config import user_config_file, load_config
from kanibako.launch.box_identity import box_name_cure, validate_box_name
from kanibako.settings.messages import ERR_WS_CONNECT_PATH_IS_PRIMARY_BOX
from kanibako.errors import BoxNameError, ConfigError, ProjectError, WorksetError
from kanibako.settings.settings_resolve import UNSET
from kanibako.settings.paths import (
    BoxMode,
    _early_scope,
    load_std_paths,
    primary_box_name_for_workspace,
    refuse_derived_box_name,
    workset_settings_path,
)
from kanibako.utils import confirm_prompt, literal_path, logical_cwd
from kanibako.identifiers import find_identifier
from kanibako.project.workset_registry import (
    load_workset_boxes,
    resolve_workset_registry_path,
)
from kanibako.project.workset import (
    DEFAULT_WORKSET_ALIAS,
    RESERVED_WORKSET_IDENTIFIERS,
    StoreRemovalError,
    add_project,
    create_workset,
    delete_workset,
    is_in_tree_workspace,
    list_worksets,
    load_workset,
    logs_share_refusal,
    purge_box_logs,
    refuse_existing_box,
    refuse_null_workspaces,
    remove_project,
    resolve_workset_name,
)

if TYPE_CHECKING:
    # ⚑ TYPE-ONLY, deliberately: every ``settings`` import in this module is deferred
    # into a function body, and a runtime one here would undo that.
    from collections.abc import Sequence

    from kanibako.errors import CategoryCollisionError
    from kanibako.settings.settings_assemble import SettingsFile
    from kanibako.settings.settings_categories import CategoryEntry
    from kanibako.settings.settings_resolve import SettingsError
    from kanibako.settings.store_collapse import CollapsedStore


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "workset",
        help="Working set commands (create, list, info, rm, set, get, show, reset, connect, disconnect, share)",
        description="Create and manage working sets of related projects.",
    )
    ws_sub = p.add_subparsers(dest="workset_command", metavar="COMMAND")

    # workset create [path] [--name N] [--standalone] [-i IMAGE] [--no-vault]
    create_p = ws_sub.add_parser(
        "create",
        help="Create a new working set",
        description="Create a new working set directory and register it globally.",
    )
    create_p.add_argument(
        "path", nargs="?", default=None,
        help="Root directory for the working set (default: cwd)",
    )
    create_p.add_argument(
        "--name", default=None,
        help="Name for the working set (default: directory basename)",
    )
    # ⚑ DECLARED so the refusal can NAME it and hand back a cure — an undeclared
    # flag gets argparse's "unrecognized arguments", which names it and teaches
    # nothing. The flag is inert by construction: nothing reads ``args.standalone``.
    create_p.add_argument(
        "--standalone", action="store_true",
        help="REFUSED: standalone is a single box's mode, not a working set's — "
             "use 'kanibako box create --standalone [path]'",
    )
    create_p.add_argument(
        "-i", "--image", default=None,
        help="Container image to use for projects in this working set",
    )
    create_p.add_argument(
        "--no-vault", action="store_true",
        help="Disable vault directories",
    )
    create_p.add_argument(
        "--force", action="store_true",
        help="Accept a workset.logs directory shared with another working set",
    )
    create_p.set_defaults(func=run_create)

    # kanibako workset list / ls (default)
    list_p = ws_sub.add_parser(
        "list",
        aliases=["ls"],
        help="List all registered working sets (default)",
        description="Show all registered working sets.",
    )
    list_p.add_argument(
        "-q", "--quiet", action="store_true",
        help="Print only working set names, one per line",
    )
    list_p.set_defaults(func=run_list)

    # kanibako workset rm <workset> [--purge] [--force]
    rm_p = ws_sub.add_parser(
        "rm",
        aliases=["delete"],
        help="Unregister a working set",
        description="Unregister a working set and optionally remove its files.",
    )
    rm_p.add_argument("name", help="Name of the working set to remove")
    rm_p.add_argument(
        "--purge", action="store_true",
        help="Also remove the working set directory tree",
    )
    rm_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt",
    )
    rm_p.set_defaults(func=run_rm)

    # kanibako workset connect <workset> [source] [--name N] [--force]
    connect_p = ws_sub.add_parser(
        "connect",
        help="Add a project to a working set",
        description="Add a project to an existing working set.",
    )
    connect_p.add_argument("workset", help="Name of the working set")
    connect_p.add_argument(
        "source", nargs="?", default=None,
        help="Source project directory (default: current directory)",
    )
    connect_p.add_argument(
        "--name", dest="project_name", default=None,
        help="Project name within the working set (default: directory basename)",
    )
    connect_p.add_argument(
        "--force", action="store_true",
        help="Connect even if the source is a standalone box (absorb it as a "
             "workset box)",
    )
    connect_p.set_defaults(func=run_connect)

    # kanibako workset disconnect <workset> <project> [--force]
    disconnect_p = ws_sub.add_parser(
        "disconnect",
        help="Remove a project from a working set",
        description="Remove a project from a working set and optionally delete its files.",
    )
    disconnect_p.add_argument("workset", help="Name of the working set")
    disconnect_p.add_argument("project", help="Name of the project to remove")
    disconnect_p.add_argument(
        "--remove-files", action="store_true",
        help="Also remove per-project directories and the box's log files",
    )
    disconnect_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt",
    )
    disconnect_p.set_defaults(func=run_disconnect)

    # kanibako workset info / inspect <name>
    info_p = ws_sub.add_parser(
        "info",
        aliases=["inspect"],
        help="Show working set details",
        description="Show name, root, creation date, and projects for a working set.",
    )
    info_p.add_argument("name", help="Name of the working set")
    info_p.set_defaults(func=run_info)

    # kanibako workset set <workset> <key>=<value> [--force]
    set_p = ws_sub.add_parser(
        "set",
        help="Set a working set configuration value",
        description=(
            "Set a working set setting (key=value).\n\n"
            "  workset set myws model=sonnet      set 'model'\n"
            "  workset set myws workset.auth.share_allowed=false  set sharing\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_p.add_argument("workset", help="Name of the working set")
    set_p.add_argument("key_value", nargs="?", help="key=value pair")
    add_null_flag(set_p, undo="workset reset <workset> <key>")
    add_set_force_flag(set_p)
    set_p.set_defaults(func=run_set)

    # kanibako workset reset <workset> <key> | --all  [--force]
    reset_p = ws_sub.add_parser(
        "reset",
        help="Reset (remove) a working set configuration override",
        description=(
            "Remove a working set override, reverting to the inherited value.\n\n"
            "  workset reset myws model           reset one key\n"
            "  workset reset myws --all           reset all overrides\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reset_p.add_argument("workset", help="Name of the working set")
    reset_p.add_argument("key", nargs="?", default=None, help="Config key to reset")
    reset_p.add_argument(
        "--all", action="store_true", dest="reset_all",
        help="Reset all overrides",
    )
    reset_p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompts; accept a workset.logs directory shared with "
             "another working set",
    )
    reset_p.set_defaults(func=run_reset)

    # kanibako workset get <workset> <key>
    get_p = ws_sub.add_parser(
        "get",
        help="Get a working set configuration value",
        description=(
            "Read one working set setting.\n\n"
            "  workset get myws model             get 'model'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    get_p.add_argument("workset", help="Name of the working set")
    get_p.add_argument("key", help="Config key to read")
    get_p.set_defaults(func=run_get)

    # kanibako workset show <workset> [--effective]
    show_p = ws_sub.add_parser(
        "show",
        help="Show working set configuration overrides",
        description=(
            "Show working set settings.\n\n"
            "  workset show myws                  show overrides\n"
            "  workset show myws --effective      show resolved values\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    show_p.add_argument("workset", help="Name of the working set")
    show_p.add_argument(
        "--effective", action="store_true",
        help="Show resolved values including inherited defaults",
    )
    show_p.set_defaults(func=run_show)

    # kanibako workset share add|rm|list
    share_p = ws_sub.add_parser(
        "share",
        help="Manage directories shared into a working set's boxes",
        description=(
            "Share host directories into every box launched in a working set.\n\n"
            "Shares are live bind mounts decided at container creation, so they\n"
            "take effect on the NEXT box launch (a running box is unaffected).\n"
            "There is no content sync: 'updating' a share means re-running\n"
            "'share add' with the same DESTINATION, which overwrites its source.\n\n"
            "A share is identified by its box DESTINATION — there is no share\n"
            "name. Nothing may be mounted twice at one destination, so a\n"
            "destination names at most one share (adding at a destination that\n"
            "already exists in the OTHER mode is still a launch-time conflict).\n\n"
            "  workset share add myws /host/data:/home/agent/data\n"
            "  workset share add myws /host/docs:/srv/docs --mode ro\n"
            "  workset share rm myws /home/agent/data\n"
            "  workset share list myws\n"
            "  workset share list myws --effective\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    share_sub = share_p.add_subparsers(dest="share_command", metavar="COMMAND")

    # share add WORKSET BIND [--mode {ro,rw}]
    share_add_p = share_sub.add_parser(
        "add",
        help="Add (or overwrite) a shared directory",
        description=(
            "Add a shared directory to a working set. BIND is "
            "'host_src:guest_dest'; the guest DESTINATION identifies the share, so "
            "re-running 'add' with the same destination overwrites its source "
            "(this is how you 'update' a share). The host source must "
            "resolve on its own — give an absolute path, '~/…', '$VAR' or an "
            "'@'-reference. A plain relative path is resolved against the working "
            "set root WHEN THE SHARE IS ADDED and stored absolute; it is not "
            "re-interpreted later. (The default working set has no BINDINGS root, "
            "so a relative path is refused there.)"
        ),
    )
    share_add_p.add_argument("workset", help="Name of the working set")
    share_add_p.add_argument(
        "bind", metavar="BIND", help="Bind mapping 'host_src:guest_dest'",
    )
    share_add_p.add_argument(
        "--mode", choices=["ro", "rw"], default="rw",
        help="Mount mode: 'rw' (read-write, default) or 'ro' (read-only)",
    )
    share_add_p.set_defaults(func=run_share_add)

    # share rm WORKSET DEST [--mode {ro,rw}]
    share_rm_p = share_sub.add_parser(
        "rm",
        aliases=["remove"],
        help="Remove a shared directory",
        description=(
            "Remove a shared directory from a working set, BY ITS BOX "
            "DESTINATION — exactly as 'share list' prints it in the DEST column. "
            "With no --mode, the share is removed from whichever mode (ro/rw) "
            "contains it; --mode is required when the same destination exists in "
            "both."
        ),
    )
    share_rm_p.add_argument("workset", help="Name of the working set")
    share_rm_p.add_argument(
        "dest", metavar="DEST",
        help="Box destination of the share to remove (see 'share list')",
    )
    share_rm_p.add_argument(
        "--mode", choices=["ro", "rw"], default=None,
        help="Disambiguate when DEST exists in both ro and rw",
    )
    share_rm_p.set_defaults(func=run_share_remove)

    # share list WORKSET [--effective]
    share_list_p = share_sub.add_parser(
        "list",
        aliases=["ls"],
        help="List a working set's shared directories (default)",
        description=(
            "List the shared directories configured for a working set. With "
            "--effective, resolve AND arbitrate each share the way a box launch "
            "would: a delivered share shows as source -> dest [mode], and one a "
            "mask or another binding swallows is named with the reason it "
            "produces no mount."
        ),
    )
    share_list_p.add_argument("workset", help="Name of the working set")
    share_list_p.add_argument(
        "--effective", action="store_true",
        help="Show what a launch would actually mount, and why a share would not",
    )
    share_list_p.set_defaults(func=run_share_list)

    # Default to list if no share subcommand given.
    share_p.set_defaults(func=run_share_list, effective=False)

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list, quiet=False)


def _load_std():
    """Load config and standard paths."""
    config_file = user_config_file()
    config = load_config(config_file)
    return load_std_paths(config)


def _workset_config_path(ws) -> Path:
    """The workset-tier settings file — ONE derivation for every mode (spec §2c)."""
    return workset_settings_path(ws)


#: Refusal for ``workset create --standalone`` (Jei, 2026-08-27).  The flag asked for
#: something no working set can be: a standalone box's workset ROOT is its own project
#: directory and its ws_name is ``__STANDALONE__`` (``settings_launch``), so it is not a
#: member of any working set — and mode is DETECTED from the box directory's marker
#: (``detect_project_mode``), so no key could carry the request either.  Refused rather
#: than accepted-and-inert for the reason ``commands/flags.py`` states for the blanket
#: flags: a flag outside the set it means something for is a user error, never a no-op.
_STANDALONE_REFUSAL = (
    "Error: 'workset create --standalone' is refused: standalone is a single "
    "box's mode, not a working set's. A standalone box keeps its own state and "
    "its own workset-tier settings inside its project directory and belongs to "
    "no working set, so a working set cannot have standalone members; mode is "
    "detected from the box directory, never stored.\n"
    "  For a standalone box:  kanibako box create --standalone [path]\n"
    "  For a working set:     re-run without --standalone; boxes created in it "
    "or connected to it are 'named' mode."
)


def run_create(args: argparse.Namespace) -> int:
    # ⚑ FIRST, before any path work or store read: the refusal is a pure argv verdict,
    # and a working set half-registered before it would be the defect twice over.
    if getattr(args, "standalone", False):
        print(_STANDALONE_REFUSAL, file=sys.stderr)
        return 1

    std = _load_std()
    path = Path(literal_path(args.path if args.path is not None else logical_cwd()))
    name = args.name or path.name

    # ⚑ PRE-FLIGHT the workset mold BEFORE anything is registered or created — a
    # mid-stamp whitelist refusal would be loud but NOT atomic. Do not reorder.
    from kanibako.errors import TemplateScopeError
    from kanibako.launch.templates import check_workset_template, install_workset_template

    try:
        check_workset_template(std, path, workset_name=name)
    except TemplateScopeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        ws = create_workset(name, path, std,
                            force_logs_share=getattr(args, "force", False))
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # J-6 A-action (INSTANTIATION): stamp the new workset store from the host mold.
    install_workset_template(std, ws.root, workset_name=ws.name)

    # ⚑ These flags set BOX-SCOPE keys at the WORKSET tier — ``box.image`` and
    # ``box.enable_vault``.  Both reach a contained box as an OVERRIDABLE DOWNWARD DEFAULT
    # through the ordinary settings cascade (``paths.resolve_box_enable_vault`` for the
    # vault flag).  A
    # top-level ``enable_vault``/``standalone`` is not a declared key at all
    # (spec §0: the keyspace is CLOSED), so it would be carried into the store as
    # an undeclared path, not merely ignored.
    # ⚑⚑ ``--standalone`` is NOT among them: it never reaches here (refused above),
    # and no declared key says "this workset's boxes are standalone" — mode is RO
    # identity (``meta.box.mode``).  Do NOT invent a key for it.
    box_updates: dict = {}
    if getattr(args, "image", None):
        box_updates["image"] = args.image
    if getattr(args, "no_vault", False):
        box_updates["enable_vault"] = False

    # ⚑ MERGE into the existing file, never overwrite — a workset.yaml already on
    # disk carries settings that must survive, and the merge has to reach INSIDE
    # the ``box:`` table: assigning the table whole drops every other ``box.*``
    # key in it.  (The workset's IDENTITY is not here: it lives in registry.yaml.)
    if box_updates:
        from kanibako.settings.config_io import dump_doc, load_doc
        ws_config = _workset_config_path(ws)
        config_data = load_doc(ws_config) if ws_config.is_file() else {}
        if not isinstance(config_data, dict):
            config_data = {}
        box_table = config_data.get("box")
        if not isinstance(box_table, dict):
            box_table = {}
            config_data["box"] = box_table
        box_table.update(box_updates)
        dump_doc(ws_config, config_data)

    print(f"Created working set '{ws.name}' at {ws.root}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    from kanibako.project.workset import default_workset

    std = _load_std()
    registry = list_worksets(std)
    quiet = getattr(args, "quiet", False)

    if quiet:
        print(DEFAULT_WORKSET_ALIAS)
        for name in sorted(registry):
            print(name)
        return 0

    # The default workset is always present (synthesized).
    dflt = default_workset(std)

    # ⚑ A refused entry is NOT an empty one: name it, mark its row, keep sweeping.
    rows: list[tuple[str, int | None, str]] = [
        (f"{DEFAULT_WORKSET_ALIAS} (default)", len(dflt.projects), "<default workset>"),
    ]
    for name in sorted(registry):
        root = registry[name]
        try:
            ws = load_workset(root, name, early_system=std.early_system)
            count: int | None = len(ws.projects)
        except WorksetError as e:
            print(f"Warning: working set '{name}' could not be read: {e}", file=sys.stderr)
            count = None
        rows.append((name, count, str(root)))

    print(f"{'NAME':<20} {'PROJECTS':>8}  {'ROOT'}")
    for ws_name, ws_count, ws_root in rows:
        shown = "ERROR" if ws_count is None else ws_count
        print(f"{ws_name:<20} {shown:>8}  {ws_root}")
    return 0


def run_rm(args: argparse.Namespace) -> int:
    std = _load_std()

    # ⚑ Case-blind (spec §0, ⚑ NAMING RULES), against the SAME set ``create`` reserves —
    # a tuple literal here would be a second spelling of it, and an exact one at that,
    # so ``workset rm Default`` fell past this guard into "not registered".
    if find_identifier(args.name, RESERVED_WORKSET_IDENTIFIERS) is not None:
        print("Error: The default workset cannot be removed.", file=sys.stderr)
        return 1

    # Check if workset has projects — error unless --force.
    registry = list_worksets(std)
    stored = find_identifier(args.name, registry)  # ⚑ case-blind (§0)
    # ⚑ Every message below names the workset as REGISTERED once we have found it — the
    # user is about to confirm a destructive act, and the name they typed may not be the
    # one on disk.  Falls back to what they typed when there is no match to name.
    label_name = args.name if stored is None else stored
    if stored is not None:
        try:
            members = len(load_workset(
                registry[stored], stored, early_system=std.early_system).projects)
            unreadable: ConfigError | None = None
        except WorksetError:
            members, unreadable = 0, None
        except ConfigError as exc:
            # ⚑⚑ A BROKEN workset.yaml KEEPS THE PROJECT GUARD, ``--purge`` included: members
            # are counted at the DEFAULT registry path, ``delete_workset``'s fallback.
            members = 0 if args.force else len(load_workset_boxes(
                resolve_workset_registry_path(
                    registry[stored].resolve(), None,
                    early=_early_scope(std, BoxMode.named, stored)),
            ))
            unreadable = exc
        if members and not args.force:
            print(
                f"Error: workset '{label_name}' has {members} project(s). "
                f"Use --force to remove anyway.",
                file=sys.stderr,
            )
            return 1
        # ⚑ Unregistering alone keeps a tree whose settings cannot be read, so it refuses.
        if unreadable is not None and not args.purge and not args.force:
            print(
                f"Error: workset '{label_name}': {unreadable} Use --purge to remove it and "
                f"its files, or --force to unregister it only.",
                file=sys.stderr,
            )
            return 1

    if not args.force:
        label = "and remove files " if args.purge else ""
        confirm_prompt(
            f"Unregister {label}working set '{label_name}'? Type 'yes' to confirm: "
        )
    try:
        root = delete_workset(args.name, std, remove_files=args.purge)
    except (WorksetError, StoreRemovalError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        cure = registry[stored] if stored is not None else e.filename
        print(f"Error: unregistered working set '{label_name}' but could not remove its "
              f"files: {e}", file=sys.stderr)
        print(f"  Try: podman unshare rm -rf {shlex.quote(str(cure))}", file=sys.stderr)
        return 1
    print(f"Deleted working set '{label_name}' (root was {root})")
    return 0


def run_connect(args: argparse.Namespace) -> int:
    import os

    from kanibako.settings.settings_resolve import SettingsError

    std = _load_std()
    registry = list_worksets(std)
    stored = find_identifier(args.workset, registry)  # ⚑ case-blind (§0)
    if stored is None:
        print(f"Error: Working set '{args.workset}' is not registered.", file=sys.stderr)
        return 1

    try:
        ws = load_workset(registry[stored], stored, early_system=std.early_system)
        _ = ws.registry_path
    except (WorksetError, SettingsError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    source = Path(literal_path(args.source if args.source else logical_cwd()))
    project_name = args.project_name or source.name
    # ⚑ The box-name rule (spec §0), typed or derived, before any write.
    try:
        if args.project_name:
            validate_box_name(project_name)
        else:
            refuse_derived_box_name(project_name)
    except BoxNameError as e:
        cure = f"kanibako workset connect {shlex.quote(stored)} {shlex.quote(str(source))}"
        print(f"Error: {e.with_cure(box_name_cure(cure, e.name))}", file=sys.stderr)
        return 1
    except ProjectError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # ⚑ EVERY REFUSAL BELOW FIRES BEFORE THE JOURNAL BRACKET, so a refused connect leaves
    # no pending ``connect`` entry behind (``add_project`` refuses the same null case for
    # its other callers).
    #
    # ⚑⚑ IN-TREE IS ``is_in_tree_workspace`` ON THE RESOLVED SOURCE — the call
    # ``add_project`` makes; a root-only test walks past a REPOINTED workspaces key.
    resolved = source.resolve()
    in_tree = is_in_tree_workspace(ws, resolved)
    if in_tree:
        try:
            refuse_null_workspaces(ws.root, f"a workspace for '{project_name}'",
                                   early=ws.early_scope)
        except WorksetError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        # ⚑ ``connect`` REGISTERS a member; it CREATES none.  The ONE in-tree source it
        # takes is the member's own leaf ``workspaces/<name>`` — the dir ``add_project``
        # records; any other in-tree dir would record a SIBLING, an empty workspace.
        workspaces = ws.workspaces_dir
        assert workspaces is not None  # refused above
        leaf = workspaces.resolve() / project_name
        if resolved != leaf or not resolved.is_dir():
            if resolved.parent == leaf.parent and resolved.name != project_name:
                why = (f"its directory name '{resolved.name}' and --name "
                       f"'{project_name}' differ")
            elif resolved == leaf:
                why = ("it is not a directory" if os.path.lexists(resolved)
                       else "no directory there")
            else:
                why = ("it is inside the working set root or its workspaces "
                       "directory, not a project")
            print(
                f"Error: Cannot connect '{source}': {why}. 'connect' registers a "
                f"member and creates none — it takes an existing "
                f"'{workspaces / project_name}' directory.",
                file=sys.stderr,
            )
            return 1
        try:
            refuse_existing_box(resolved, std, force=args.force)
        except WorksetError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    elif not source.is_dir():
        # ⚑ ``connect`` REGISTERS an EXTERNAL dir; a dangling symlink is PRESENT, not missing.
        present = "it is not a directory." if os.path.lexists(source) else "no such directory."
        print(f"Error: Cannot connect '{source}': {present}", file=sys.stderr)
        return 1

    # ⚑ ONE BOX PER WORKSPACE: a primary box's workspace is never connected.  In-tree,
    # the in-place cure must land on THIS leaf, so it carries the leaf's name.
    owner = primary_box_name_for_workspace(std.primary_workset, literal_path(source),
                                           early=_early_scope(std, BoxMode.primary))
    if owner is not None:
        rename = (f" --name {shlex.quote(project_name)}"
                  if in_tree and owner != project_name else "")
        box, ws_ref = shlex.quote(owner), shlex.quote(ws.name)
        print(
            f"Error: Cannot connect '{source}': "
            + ERR_WS_CONNECT_PATH_IS_PRIMARY_BOX % (
                owner, ws.name, box, ws_ref, rename, box, ws_ref, box, box,
            ),
            file=sys.stderr,
        )
        return 1

    # ⚑ THE J2 WRITE-AHEAD BRACKET, AND IT BELONGS HERE, NOT IN ``add_project``: entry
    # BEFORE the membership write, cleared immediately after (HARD INVARIANT: registered
    # ==> no pending entry at rest). Key = the box dir, the uniform J1/J2 key.
    from kanibako.project.workset import _journal_connect

    try:
        with _journal_connect(
            std.journal, ws.projects_dir / project_name,
            name=project_name, workset=ws.name,
            workspace=literal_path(source),
        ):
            proj = add_project(ws, project_name, source, std, force=args.force)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Added project '{proj.name}' to working set '{ws.name}'")
    return 0


def run_disconnect(args: argparse.Namespace) -> int:
    std = _load_std()

    # ⚑ Case-blind (§0), against the reserved-IDENTIFIER set rather than a second,
    # exact-matching spelling of it.
    if find_identifier(args.workset, RESERVED_WORKSET_IDENTIFIERS) is not None:
        print("Error: The default workset cannot be removed.", file=sys.stderr)
        return 1

    registry = list_worksets(std)
    stored = find_identifier(args.workset, registry)  # ⚑ case-blind (§0)
    if stored is None:
        print(f"Error: Working set '{args.workset}' is not registered.", file=sys.stderr)
        return 1

    try:
        ws = load_workset(registry[stored], stored, early_system=std.early_system)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Reconcile the positional <project> with the blanket --box, then resolve
    # path-or-name through the SHARED box resolver (§Design 8). A bare member name
    # falls back to the raw token, which remove_project matches by name.
    from kanibako.commands.flags import resolve_subject_value
    from kanibako.settings.paths import resolve_box_target
    project_token = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    if not project_token:
        print("Error: no project specified to disconnect.", file=sys.stderr)
        return 1
    member: str = project_token
    if project_token:
        try:
            from kanibako.settings.config import user_config_file, load_config
            config = load_config(user_config_file())
            resolved = resolve_box_target(std, config, project_token)
            if resolved.name:
                member = resolved.name
        except Exception:
            member = project_token

    if args.remove_files:
        from kanibako.settings.workset_dirkeys import refuse_inherited_per_owner
        refuse_inherited_per_owner(ws.root, ws.early_scope)
    # ⚑ RESOLVED BEFORE ANYTHING IS DELETED: an unresolvable ``workset.logs`` refuses
    # the disconnect whole, not after the box tree is already gone.
    logs_dir = ws.logs_dir if args.remove_files else None
    # ⚑ RESOLVED HERE TOO: its OWN store, NOT in ``remove_member_store``
    # (shared with the relocation retire path, STEP 4a, before the 4c carry).
    snapshot_store = None
    if args.remove_files:
        from kanibako.commands.clean import _resolve_snapshot_store
        from kanibako.project.workset import resolve_workset_vault_pair
        _vault_ro, vault_rw = resolve_workset_vault_pair(ws.root, early=ws.early_scope)
        if vault_rw is not None:
            # The box's OWN vault-rw LEAF — the store is that leaf's sibling.
            snapshot_store = _resolve_snapshot_store(vault_rw / member, member)
    if not args.force:
        label = "and remove files " if args.remove_files else ""
        confirm_prompt(
            f"Remove {label}project '{member}' from '{ws.name}'? "
            "Type 'yes' to confirm: "
        )

    try:
        proj = remove_project(
            ws, member, remove_files=args.remove_files, std=std,
        )
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except StoreRemovalError as e:
        # ⚑ The message already names the refusing leaf and its cure.
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        # ⚑ NOT redundant with the WorksetError arm: the WORKSPACE leaf's plain
        # ``rmtree`` can refuse (a store leaf refusing is the arm above).
        print(f"Error: could not remove project '{member}': {e}", file=sys.stderr)
        cure = e.filename or str(ws.projects_dir / member)
        print(f"  Try: podman unshare rm -rf {shlex.quote(str(cure))}",
              file=sys.stderr)
        return 1
    if logs_dir is not None:
        # ⚑ HERE, not in ``remove_project``/``release_project``: a move releases the box
        # through ``release_project``, and its logs must survive the move.
        purge_box_logs(std, logs_dir, proj.name, workset_root=ws.root)
    if snapshot_store is not None:
        # The box's OWN store only — never the shared base or a neighbor's.
        from kanibako.commands.clean import _remove_snapshot_store
        _remove_snapshot_store(snapshot_store)
    print(f"Removed project '{proj.name}' from working set '{ws.name}'")
    return 0


def run_info(args: argparse.Namespace) -> int:
    std = _load_std()
    try:
        ws = resolve_workset_name(args.name, std)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    root_display = "<default workset>" if ws.is_default else str(ws.root)
    print(f"Name:     {ws.name}")
    print(f"Root:     {root_display}")
    if ws.projects:
        print(f"Projects: {len(ws.projects)}")
        for proj in ws.projects:
            print(f"  - {proj.name}  ({proj.source_path})")
    else:
        print("Projects: (none)")
    return 0


def run_set(args: argparse.Namespace) -> int:
    """``workset set <workset> <key>=<value>``."""
    args.reset = None
    args.reset_all = False
    args.effective = False
    return _run_workset_config(args)


def run_reset(args: argparse.Namespace) -> int:
    """``workset reset <workset> <key>`` / ``workset reset <workset> --all``."""
    reset_all = getattr(args, "reset_all", False)
    key = getattr(args, "key", None)
    if not reset_all and not key:
        print("Error: reset requires a key (or --all)", file=sys.stderr)
        return 1
    args.reset = "__ALL__" if reset_all else key
    args.key_value = None
    args.effective = False
    return _run_workset_config(args)


def run_get(args: argparse.Namespace) -> int:
    """``workset get <workset> <key>``."""
    args.key_value = args.key
    args.reset = None
    args.reset_all = False
    args.effective = False
    return _run_workset_config(args)


def run_show(args: argparse.Namespace) -> int:
    """``workset show <workset> [--effective]``."""
    args.key_value = None
    args.reset = None
    args.reset_all = False
    return _run_workset_config(args)


def _run_workset_config(args: argparse.Namespace) -> int:
    """Shared get/set/show/reset dispatch into the ``config_interface`` engine."""
    from kanibako.agent_ref import GENERAL_SLOT
    from kanibako.settings.config_keys import ConfigLevel
    from kanibako.settings.settings_launch import ResolveSubject, resolve_inputs
    from kanibako.settings.settings_resolve import SettingsError
    from kanibako.settings.config_interface import (
        ConfigAction,
        get_config_value,
        parse_config_arg,
        reset_all,
        reset_config_value,
        set_config_value,
        show_config,
    )

    std = _load_std()
    ws_name = args.workset
    try:
        ws = resolve_workset_name(ws_name, std)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    config_file = user_config_file()
    ws_config = _workset_config_path(ws)
    # The workset-tier docker env FILE is GONE (R-39/RQ-1); ``workset.env.<VAR>`` is an
    # ordinary key in ``ws_config``, so there is no second write target here.

    key_value = getattr(args, "key_value", None)

    # Handle --reset mode
    if args.reset is not None:
        # ⚑ Keyspec § 0: dropping the own value can fall through onto another's log dir.
        _collision = logs_share_refusal(
            "workset.logs" if args.reset_all or args.reset == "__ALL__" else args.reset,
            UNSET, std, force=args.force, scope="workset",
            target_name=ws.name, target_root=ws.root,
        )
        if _collision is not None:
            print(_collision, file=sys.stderr)
            return 1
        if args.reset_all or args.reset == "__ALL__":
            msg = reset_all(
                config_path=ws_config,
                force=args.force,
                command_scope=ConfigLevel.workset,
            )
            print(msg)
            return 0

        reset_key = args.reset
        # ⚑ Full launch cascade so the cleared-message can name the now-effective value
        # and its source tier — same two arms as the SET branch below.
        msg = reset_config_value(
            reset_key,
            config_path=ws_config,
            command_scope=ConfigLevel.workset,
            cascade_system_path=std.settings,
            cascade_workset_path=ws_config,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    # Parse the key/value argument
    action, key, value = parse_config_arg(
        key_value, set_null=getattr(args, "null", False),
    )

    if action == ConfigAction.show:
        # ⚑ THE WORKSET KEEPS ITS SETTINGS APART FROM ITS CONFIG FILE, exactly as the
        # system noun does, so it passes the pair the SAME way (``noun_settings_file``'s
        # own test): Layer-1 ``kanibako.cfg`` as the config file, ``workset.yaml`` as the
        # settings file.  Passing ``workset.yaml`` as *config_path* instead made it a
        # BOX-tier file to ``load_merged_config`` — which dropped it with an
        # upward-scope warning — and left the nested settings flatten unfed, so the
        # abstract trio (``common``/``caches``/``seeded``) this noun OWNS was invisible
        # in both views, against spec §0 ("``config show`` lists them").
        # ⚑ ONE CARRIER: the rows come from the flatten the system noun already renders
        # them with, never from a second read of the same file.
        try:
            rc = show_config(
                global_config_path=config_file,
                command_scope=ConfigLevel.workset,
                config_path=config_file,
                effective=args.effective,
                system_settings_path=ws_config,
                inputs=resolve_inputs(
                    subject=ResolveSubject.WORKSET, std=std, ws=ws,
                    agent_name=GENERAL_SLOT, system_path=std.settings,
                ) if args.effective else None,
            )
        except SettingsError as e:
            return _preview_refusal(ws, e)
        # ⚑ THE OTHER HALF OF THE SAME §0 CLAUSE. ``show_config`` renders the
        # DECLARATIONS; the binding each abstract one DERIVES is an ``--effective``
        # obligation and needs the workset-scope COLLAPSE, which the display engine
        # neither has nor should grow — the block it renders for a box is fed a
        # LAUNCH's snapshot, and a working set has no box to launch.
        if rc != 0 or not args.effective:
            return rc
        return _print_effective_derivations(ws, std)

    if action == ConfigAction.get:
        # ⚑ Refused at the HANDLER, not in the engine: the get engine returns VALUES and
        # never error strings, so both guards below have to fire before it is called.
        from kanibako.settings.config_keys import (
            bare_agent_key_scope_error,
            bare_env_retired_error,
            resolve_key,
            scope_read_key_error,
        )
        _bare_err = bare_agent_key_scope_error(
            resolve_key(key), ConfigLevel.workset, verb="read",
        )
        if _bare_err is not None:
            print(_bare_err, file=sys.stderr)
            return 1
        # Bare env.* — RETIRED (R-39, spec §2a: the env family is scoped).
        _env_err = bare_env_retired_error(
            key, verb="read", command_scope=ConfigLevel.workset,
        )
        if _env_err is not None:
            print(_env_err, file=sys.stderr)
            return 1
        # ⚑ THE CLOSED-KEYSPACE READ GATE (spec §0), THIRD and last for the reason
        # ``scope_read_key_error`` states — a generic "not a key" must not overwrite
        # either cure above.  No redirect arm: a workset spans several boxes, so there
        # is no single agent to mirror, which is exactly what the first guard says.
        _key_err = scope_read_key_error(key, ConfigLevel.workset)
        if _key_err is not None:
            print(_key_err, file=sys.stderr)
            return 1
        val = get_config_value(
            key,
            global_config_path=config_file,
            project_toml=ws_config,
            # Threaded as ``system get`` threads it, and like it never reading the
            # node's own file: that is the agent scope's (spec §2a, plain ``get``).
            agents_root=std.agents, node_store=False,
            command_scope=ConfigLevel.workset,
            cascade_system_path=std.settings,
            std=std, ws=ws,
        )
        if val is not None:
            print(val)
        else:
            print("(not set)", file=sys.stderr)
        return 0

    if action == ConfigAction.set:
        # ⚑ Keyspec § 0: two worksets on one log dir share its files — refused by name.
        _collision = logs_share_refusal(
            key, value, std, force=args.force, scope="workset",
            target_name=ws.name, target_root=ws.root,
        )
        if _collision is not None:
            print(_collision, file=sys.stderr)
            return 1
        # ⚑ The WORKING SET is the target (spec §2a): ``resolve_inputs`` builds its full
        # cascade — the system file, ``workset.yaml`` and the workset anchors. It names
        # no box, so every ``@meta.box.*`` anchor is OMITTED, never fabricated.
        msg = set_config_value(
            key, value,
            config_path=ws_config,
            cascade_system_path=std.settings,
            command_scope=ConfigLevel.workset,
            std=std, ws=ws,
            force=args.force,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    return 0


# ---------------------------------------------------------------------------
# workset share add | rm | list
# ---------------------------------------------------------------------------

# ⚑ ``_SHARE_NAME_RE`` was RETIRED here (R-10) and was DELIBERATELY NOT reborn as a
# destination validator. Do not add one: llm-docs, "Why there is no destination
# validator". Pinned by ``tests/test_commands/test_workset_share.py``.

# Reminder printed after a mutation: bind mounts are fixed at creation time.
_NEXT_LAUNCH_REMINDER = (
    "Shares apply on the next box launch (bind mounts are fixed at container "
    "creation; a running box is unaffected)."
)


def _share_source_display(value: object) -> str:
    """Render a stored binding entry's HOST SOURCE for the raw listing's SOURCE column."""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        src = str(value[0])
        # ⚑ ``value[2:]`` IS ALWAYS EMPTY under the live entry shape — see llm-docs,
        # "The dead options bracket". Do not read this slice as live behavior.
        extra = [str(p) for p in value[2:] if str(p)]
        return f"{src}  [{', '.join(extra)}]" if extra else src
    return str(value)


def _resolve_share_workset(name: str):
    """Resolve *name*: ``(ws, std)``, or a printed error and ``(None, None)`` — caller returns 1"""
    std = _load_std()
    try:
        ws = resolve_workset_name(name, std)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return None, None
    return ws, std


def _load_share_doc(ws_config: Path) -> dict:
    """Load the workset workset.yaml as a nested dict (missing → {})."""
    from kanibako.settings.config_io import load_doc

    return load_doc(ws_config)


def run_share_add(args: argparse.Namespace) -> int:
    """Add (or overwrite) a workset binding, keyed by its box DESTINATION (R-10)."""
    from kanibako.settings.agent_config import is_self_resolving
    from kanibako.settings.config_io import dump_doc
    from kanibako.settings.settings_launch import internal_bind_refusals
    from kanibako.settings.settings_resolve import (
        normalize_bind_dest,
        split_bind,
    )

    bind = args.bind
    # ⚑ The CANONICAL escape-aware splitter, not a raw ':' scan — the same parser
    # ``config set`` and the resolver use. The share grammar is EXACTLY two fields, so a
    # second unescaped ':' is caught by RE-SPLITTING the already-unescaped guest half.
    host_src, guest_dest = split_bind(bind)
    has_extra_colon = guest_dest is not None and split_bind(guest_dest)[1] is not None
    if guest_dest is None or not host_src or not guest_dest or has_extra_colon:
        print(
            f"Error: invalid bind '{bind}' "
            "(expected exactly one ':' as 'host_src:guest_dest', non-empty halves; "
            "escape a literal ':' in a path as '\\:').",
            file=sys.stderr,
        )
        return 1

    ws, _ = _resolve_share_workset(args.workset)
    if ws is None:
        return 1

    typed_host_src = host_src
    if not is_self_resolving(host_src):
        if ws.is_default:
            print(
                f"Error: relative host source '{host_src}' cannot be resolved for "
                f"the default working set (it has no bindings root — its own "
                f"directory is kanibako's internal store, not a project dir). "
                f"Give a path "
                "that resolves on its own: an absolute path, '~/…', '$VAR' or an "
                "'@'-reference.",
                file=sys.stderr,
            )
            return 1
        # ⚑ The SAME join the launch used to apply, moved to WRITE time so the stored
        # value resolves on its own (spec §2a). Same input, same mount.
        host_src = str(ws.root / host_src)

    ws_config = _workset_config_path(ws)
    data = _load_share_doc(ws_config)
    subtree = data.setdefault("workset", {}).setdefault("bindings", {}).setdefault(
        args.mode, {}
    )
    # ⚑ R-11: the DESTINATION is canonicalized before it is used as a key; the SOURCE
    # never is (its ``~`` is the invoking user's home). Do not make these symmetric.
    guest_dest = normalize_bind_dest(guest_dest)
    # ⚑ The resolve's own §2c refusal, run BEFORE the write: never store what it refuses.
    refusals = internal_bind_refusals(
        f"workset.bindings.{args.mode}", {guest_dest: [host_src]}, where=str(ws_config),
        when="write",
    )
    if refusals:
        print("Error: " + "\n".join(refusals), file=sys.stderr)
        return 1
    existed = guest_dest in subtree
    # ⚑ The 1-ELEMENT dest-keyed entry (R-6): the destination is the KEY and appears
    # exactly once. Storage is structured (spec §2a); the colon form is input/display only.
    subtree[guest_dest] = [host_src]
    dump_doc(ws_config, data)

    verb = "Updated" if existed else "Added"
    print(
        f"{verb} {args.mode} share at '{guest_dest}' for working set "
        f"'{ws.name}': {bind}"
    )
    if host_src != typed_host_src:
        # Say what was actually stored — a silent rewrite of a path should be told once.
        print(
            f"  (relative source resolved under the working set root and stored "
            f"as {host_src})"
        )
    print(_NEXT_LAUNCH_REMINDER)
    return 0


def run_share_remove(args: argparse.Namespace) -> int:
    """Remove a workset binding BY ITS BOX DESTINATION (R-10) — ``share list``'s DEST column."""
    from kanibako.settings.config_io import dump_doc
    from kanibako.settings.settings_resolve import normalize_bind_dest

    ws, _ = _resolve_share_workset(args.workset)
    if ws is None:
        return 1

    ws_config = _workset_config_path(ws)
    data = _load_share_doc(ws_config)
    path_tree = data.get("workset", {}).get("bindings", {})
    # ⚑ R-11 on the LOOKUP side: canonicalize the ARGUMENT, never the stored keys —
    # those are already canonical by construction. Deleting whatever key the user gives
    # is also what makes the retired-name-keyed cure in ``_workset_raw_shares`` spellable.
    dest = normalize_bind_dest(args.dest)

    def _present(mode: str) -> bool:
        sub = path_tree.get(mode, {})
        return isinstance(sub, dict) and dest in sub

    if args.mode is not None:
        modes = [args.mode] if _present(args.mode) else []
    else:
        modes = [m for m in ("ro", "rw") if _present(m)]

    if not modes:
        scope = f" ({args.mode})" if args.mode else ""
        print(
            f"Error: no share at '{args.dest}'{scope} configured for "
            f"working set '{ws.name}'.",
            file=sys.stderr,
        )
        return 1

    if len(modes) > 1:
        print(
            f"Error: a share at '{args.dest}' exists in both ro and rw for "
            f"working set '{ws.name}'; pass --mode to disambiguate.",
            file=sys.stderr,
        )
        return 1

    mode = modes[0]
    del path_tree[mode][dest]
    dump_doc(ws_config, data)

    print(
        f"Removed {mode} share at '{dest}' from working set '{ws.name}'."
    )
    print(_NEXT_LAUNCH_REMINDER)
    return 0


def run_share_list(args: argparse.Namespace) -> int:
    """List a workset's bindings: raw DEST/MODE/SOURCE, or ARBITRATED mounts if ``--effective``."""
    from kanibako.settings.settings_assemble import (
        ReadPurpose,
        read_settings_files,
        refuse_undeclared_per_file,
    )
    from kanibako.settings.settings_resolve import SettingsError

    ws, std = _resolve_share_workset(args.workset)
    if ws is None:
        return 1

    effective = getattr(args, "effective", False)
    try:
        files = read_settings_files(
            (("workset", _workset_config_path(ws)),), purpose=ReadPurpose.NARROW,
        )
        raw_shares = _workset_raw_shares(files)
        # Stage (h) LAST, after this listing's own refusals; the preview's launch runs it
        # after the launch's.
        if not effective:
            refuse_undeclared_per_file(files)
    except SettingsError as e:
        # A malformed bindings table must not leave a traceback out of a listing command.
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # ⚑ ``--effective`` BRANCHES BEFORE THE EMPTY-SHARES RETURN, never after it. The
    # preview runs the launch's refusals, and a working set with NO shares can still
    # carry a value every box in it refuses (``workset: {frob: 1}``); returning "No
    # bindings" first answered rc 0 for it.
    if effective:
        return _print_effective_shares(ws, std)

    if not raw_shares:
        _print_no_shares(ws)
        return 0

    # Raw view: DEST, MODE, SOURCE (pre-resolution, from the workset file).
    rows: list[tuple[str, str, str]] = [
        (dest, mode, _share_source_display(value))
        for (mode, dest), value in raw_shares.items()
    ]
    rows.sort(key=lambda r: (r[1], r[0]))

    print(f"Shares for working set '{ws.name}':")
    print(f"  {'DEST':<36} {'MODE':<4}  {'SOURCE'}")
    for dest, mode, source in rows:
        print(f"  {dest:<36} {mode:<4}  {source}")
    return 0


def _print_no_shares(ws) -> None:
    """The EMPTY answer both views of ``workset share list`` give — ONE line, one carrier."""
    print(f"No bindings configured for working set '{ws.name}'.")


def _workset_raw_shares(files: Sequence[SettingsFile]) -> dict[tuple[str, str], object]:
    """The workset file's ``workset.bindings.{ro,rw}`` as a ``{(mode, dest): raw}`` map (the
    RAW view); *files* is its ``NARROW`` read."""
    from kanibako.agent_ref import GENERAL_SLOT
    from kanibako.settings.agent_config import is_self_resolving
    from kanibako.settings.kb_store import BindEntry
    from kanibako.settings.kb_store import __MISSING__
    from kanibako.settings.keystore import KeyStore
    from kanibako.settings.settings_assemble import assemble_levels
    from kanibako.settings.settings_resolve import SettingsError

    # ⚑ assemble_levels returns [box, workset, agent.<active>, agent.default, system,
    # base] — index 1 is the workset partial, the only file read.
    levels = assemble_levels(agent_name=GENERAL_SLOT, files=files)
    workset_partial = levels[1]
    out: dict[tuple[str, str], object] = {}
    ws_node = dict.get(workset_partial, "workset", __MISSING__)
    if not isinstance(ws_node, KeyStore):
        return out
    bindings = dict.get(ws_node, "bindings", __MISSING__)
    if not isinstance(bindings, KeyStore):
        return out
    for mode in ("ro", "rw"):
        mode_node = dict.get(bindings, mode, __MISSING__)
        if not isinstance(mode_node, KeyStore):
            continue
        for dest in dict.keys(mode_node):
            leaf = dict.__getitem__(mode_node, dest)
            # ⚑ KNOWN, RULED GAP: a RETIRED 2-element ``[src, dest]`` under a
            # destination-shaped key is UNDETECTABLE here and reads as
            # ``BindEntry(src, opts=dest)``. Accepted (R-4 rules NO MIGRATION); do NOT
            # invent a mount-options grammar to close it — llm-docs, "The arity trap".
            if isinstance(leaf, BindEntry):
                # ⚑ REFUSES a RETIRED name-keyed entry, on the KEY: a bare leaf cannot be
                # DISPLAYED honestly, so it is named rather than mis-rendered.
                if not is_self_resolving(dest):
                    raise SettingsError(
                        f"workset.bindings.{mode} has an entry keyed '{dest}', "
                        f"which is not a box DESTINATION: a destination is "
                        f"absolute or begins with '~' / '$' / '@' (spec §2a). A "
                        f"binding is keyed BY its destination and has no entry "
                        f"name (the name was dropped 2026-08-06c), so '{dest}' is "
                        f"the RETIRED name-keyed shape. Fix it: "
                        f"`kanibako workset share rm <workset> "
                        f"{shlex.quote(dest)} --mode {shlex.quote(mode)}` "
                        f"then `kanibako workset share add "
                        f"<workset> {shlex.quote(leaf.src)}:<box_dest> "
                        f"--mode {shlex.quote(mode)}` — or "
                        f"re-key the entry to its destination in the settings "
                        f"file."
                    )
                out[(mode, dest)] = (
                    [leaf.src, leaf.opts] if leaf.opts is not None else [leaf.src]
                )
            else:
                out[(mode, dest)] = leaf
    return out


#: The pid-0 FOUNDATION this preview folds every scope over. ⚑ A workset names no
#: BOX, and the home SOURCE is per-box (``@meta.box.path/home``), so it is SPELLED as
#: what it is rather than guessed — and the guess could not surface anyway: only the
#: foundation's DEST takes part in arbitration, a share AT that dest is refused as a
#: second bind at the foundation's point, and one INSIDE it holds its own destination.
#: ⚑ NO OPTIONS. Home's mount options are seam machinery (spec ``:1015``), not a facet
#: of any key; ``config_display`` declines to print them for the same reason, and a
#: second copy of the seam's literal here would be a copy to keep in step.
_PREVIEW_HOME_SRC: str = "(each box's own home store)"


def _workset_preview_entries(ws, std) -> "list[CategoryEntry]":
    """The working set's launch resolve → the ONE ``CategoryEntry`` list both listings read.

    ⚑ THE LAUNCH'S OWN RESOLVE, with the ``WORKSET`` subject: the inputs come from
    ``settings_launch.resolve_inputs`` and the snapshot from ``build_launch_snapshot``,
    so the system and base files, the workset's ``pref:`` requests, the §2c auth chain,
    every workset anchor and the launch's refusals all apply here as they do at launch.
    A working set names no box: every box-scope anchor is OMITTED, never fabricated, and
    the agent is the ``GENERAL_SLOT`` placeholder with no agent file. ⚑ SHARED —
    ``workset share list --effective`` and the derived block of ``workset show
    --effective`` must give one answer about one working set.

    Raises :class:`~kanibako.settings.settings_resolve.SettingsError` for anything the
    launch refuses; the ARBITRATION is :func:`_workset_preview_collapse`'s.
    """
    from dataclasses import replace

    from kanibako.agent_ref import GENERAL_SLOT
    from kanibako.settings.settings_expand import RefsRead
    from kanibako.settings.settings_launch import (
        DEPENDS_ON_THE_BOX,
        ResolveSubject,
        build_launch_snapshot,
        depends_on_the_box,
        resolve_inputs,
        snapshot_category_entries,
    )

    inputs = resolve_inputs(
        subject=ResolveSubject.WORKSET, std=std, ws=ws,
        agent_name=GENERAL_SLOT, system_path=std.settings,
    )
    refs: RefsRead = {}
    expanded = build_launch_snapshot(
        **inputs.as_kwargs(),
        agent_name=GENERAL_SLOT,
        agent_path=None,
        # The resolved ``system.*`` tier, as in every box resolve.
        default_categories=dict(inputs.system_floor),
        refs_read=refs,
    )
    return [
        replace(entry, host_src=DEPENDS_ON_THE_BOX)
        if depends_on_the_box(refs.get(tuple(entry.key_segments), ()), in_workset=True)
        else entry
        for entry in snapshot_category_entries(
            expanded, active_agent=GENERAL_SLOT, box_ctx=inputs.ctx,
        )
    ]


def _workset_preview_collapse(entries: "list[CategoryEntry]") -> "CollapsedStore":
    """ARBITRATE *entries* over the preview foundation — the collapse, called ONCE.

    Every §0 refusal a working set can trip (bind-over-bind, mask-on-mask,
    seed-outside-home, …) is raised from here, so a caller that has nothing to
    arbitrate may skip it and keep the output it had.
    """
    from kanibako.settings.kb_store import BindEntry
    from kanibako.settings.store_collapse import collapse_store_shapes
    from kanibako.settings.store_shape import build_store_shape_set

    # ⚑⚑ THE ARBITRATION IS THE LAUNCH'S OWN — the same two calls
    # ``commands.start._install_assembly_collapse`` makes, not a second walk.
    # ⚑ The COLLISION WARNINGS a launch emits are not raised
    # here: §0's exempt pair is an ambiguity between two ABSTRACT declarations, and a
    # share is never one of the two — the surviving share is unaffected.
    # ⚑ THE ENTRY LIST GOES IN AS WELL, and it buys exactly one thing: the
    # DECLARATION KEY behind each collapsed mount
    # (``CollapsedStore.declared_by``). A mask is the row that needs it — every
    # other loss names a host source the reader can recognize their own key by,
    # and a mask has none, so "the mask at /opt/x" was the only diagnosis this
    # listing could give and /opt/x is not a path the swallowed share's key
    # names. ⚑ Passing it changes NO arbitration: the fold is byte-identical
    # either way, and the map is read, never re-derived.
    return collapse_store_shapes(
        build_store_shape_set(entries),
        BindEntry(_PREVIEW_HOME_SRC, None),
        entries,
    )


def _preview_refusal(ws, exc: CategoryCollisionError | SettingsError) -> int:
    """Report a refusal the preview raised, and the rc — ONE arm for both listings.

    ⚑ *exc* is NARROWED to the two refusals the preview raises (P3), so an unrelated
    exception cannot reach the ``Error:`` frame below and be reported as one.
    """
    from kanibako.errors import CategoryCollisionError

    if isinstance(exc, CategoryCollisionError):
        # ⚑⚑ FRAMING ONLY, AND THE FRAME IS THE WHOLE ADDITION. The user asked what a
        # box in this working set would MOUNT; a bare collision message answers a
        # question they did not ask, and reads as a listing failure rather than as
        # the answer. So one line of context goes ABOVE it — before the refusal, not
        # after, because that refusal is a dozen lines and carries a YAML remedy
        # block a reader should be told the purpose of before wading in.
        # 🛑 THE COLLISION IS NOT RESTATED HERE, EVER. Every word of it is printed
        # FROM the caught exception; a second carrier of that wording would drift
        # from the launch's, and the launch's is the one the user has to act on.
        # ``box_dest`` is READ OFF the exception for the same reason — carried, not
        # re-derived.
        print(
            f"Cannot say what working set '{ws.name}' would mount: its "
            f"declarations collide at '{exc.box_dest}', so no box in it can launch. "
            f"The refusal a launch gives follows.",
            file=sys.stderr,
        )
    # ⚑ BYTE-IDENTICAL to what ``cli.main`` would have printed had this
    # propagated (``cli.py``'s ``KanibakoError`` arm: ``print(f"Error: {e}",
    # file=sys.stderr)`` then rc 1). Catching it here ADDS the line above and
    # changes nothing else — no second prefix, no reflowed message, same rc.
    print(f"Error: {exc}", file=sys.stderr)
    return 1


def _print_effective_shares(ws, std) -> int:
    """Resolve, ARBITRATE and print the workset's bindings as launch-time mounts."""
    from kanibako.errors import CategoryCollisionError
    from kanibako.settings.settings_categories import is_read_only
    from kanibako.settings.settings_resolve import SettingsError
    from kanibako.settings.store_collapse import (
        DERIVED_MOUNT,
        Declaration,
        derivation_result,
        pair_declarations,
    )

    try:
        entries = _workset_preview_entries(ws, std)
        collapsed = _workset_preview_collapse(entries)
    except (CategoryCollisionError, SettingsError) as e:
        return _preview_refusal(ws, e)

    # ⚑ THE SHARES ARE THE SUBJECT, not the collapsed map: a share's IDENTITY is its
    # destination (R-10) and that is what ``share rm`` takes, so a share the collapse
    # swallowed must keep its ROW and gain a reason — dropping it is the silent
    # omission this display was already measured wrong for once.
    shares = [
        (entry, "ro" if is_read_only(entry.options) else "rw")
        for entry in entries
        if entry.category in ("bindings.ro", "bindings.rw")
    ]
    if not shares:
        # Reached only AFTER the resolve and the arbitration above, so an empty
        # working set is answered as empty only once a launch would accept it.
        _print_no_shares(ws)
        return 0
    derivations = pair_declarations(
        [
            Declaration(entry.label, entry.box_dest, entry.host_src, entry.delivery)
            for entry, _ in shares
        ],
        collapsed.bindings,
    )

    print(f"Effective bindings for working set '{ws.name}':")
    for (entry, mode), row in zip(shares, derivations, strict=True):
        # ⚑ THE ARROW IS THE DELIVERY, and only a delivered share earns one. A share
        # that receives nothing is printed in DECLARATION form with the reason
        # beneath it, so a reader skimming for mounts cannot take a loss for one.
        if row.outcome == DERIVED_MOUNT:
            print(f"  {entry.host_src} -> {entry.box_dest}  [{mode}]")
            continue
        print(f"  {entry.box_dest}  [{mode}]  (declared: {entry.host_src})")
        print(f"    {derivation_result(row, collapsed.declared_by)}")
    return 0


def _print_effective_derivations(ws, std) -> int:
    """Print each ABSTRACT declaration WITH the binding it derives (keyspec §0).

    §0 on the abstract trio: *"The binding they produce is MATERIALIZED beside the
    declaration, so ``--effective`` shows BOTH the declaration and the derived binding
    and a user can see WHY a mount exists."*  The declaration half is rendered by
    ``config_display`` from this noun's own settings file; this is the DERIVED half,
    and the same clause leaves WHERE it is read from to the implementation — *"This
    clause reserves a NAME; it does not name a storage site … The obligation above is
    what the user must SEE."*

    🛑 **THE ``binding_derivations`` NODE IS NOT READ, AND MAY NOT BE.** It is
    materialized BEFORE arbitration for winners and losers alike (R-8), so every row
    in it reads as a live mount — a ``common`` declaration a mask swallowed would
    print ``(mount)`` with the mask invisible.  The answer comes from PAIRING the
    declarations against the COLLAPSE, which is the one decision function
    (``store_collapse.pair_declarations``), fed off the SAME entry list and the SAME
    fold ``workset share list --effective`` reads.  Nothing is re-derived.

    ⚑ **WHAT THIS CLAIMS, EXACTLY: what the base, system and working-set settings files
    derive for a box in this working set** — not what a named box receives.  The working
    set's ``box:`` defaults-down table is read like the rest of it, so a ``box.masks``
    written there does apply here.  A working set names no box and no agent, so no box's
    OWN settings file and no agent file is read, and a ``masks`` entry in one of those that
    would swallow one of these at launch cannot be seen from here.  The pid-0 foundation is spelled
    :data:`_PREVIEW_HOME_SRC` for the same reason, and a ``seeded`` row prints its
    GUEST destination only: §0's tuple direction resolves a seed to the host store
    when the copy runs, and that store is the box's home.

    ⚑ THE COLLAPSE RUNS ONLY WHEN THERE IS AN ABSTRACT DECLARATION TO ARBITRATE — so
    no §0 arbitration refusal (bind-over-bind, mask-on-mask, seed-outside-home) can
    reach a working set that declares none.

    🛑 **THE RESOLVE IS NOT GATED, AND THIS VERB CAN NOW EXIT 1 WHERE IT EXITED 0.**
    :func:`_workset_preview_entries` runs BEFORE the gate, ``expand`` is strict, and
    ``snapshot_category_entries`` refuses an undeclared shape — while the ``show``
    arm's own rendering is a YAML flatten that expands nothing.  So a working set
    carrying, say, ``bindings.ro`` sourced at an unknown ``$VAR`` printed its rows at
    rc 0 and now reports ``Error: Unknown variable: …`` at rc 1, with no abstract
    declaration anywhere in the file.  MEASURED both ways.  That is the intended
    answer — a view that cannot resolve the file must not claim rc 0 — but it is a
    BEHAVIOR CHANGE to the shipped verb, not a no-op for the ungated case.
    """
    from kanibako.errors import CategoryCollisionError
    from kanibako.settings.kb_store import BINDING_DERIVATIONS_NODE
    from kanibako.settings.settings_categories import ABSTRACT_CATEGORIES
    from kanibako.settings.settings_resolve import SettingsError
    from kanibako.settings.store_collapse import (
        Declaration,
        derivation_result,
        pair_declarations,
    )

    try:
        entries = _workset_preview_entries(ws, std)
        abstract = [e for e in entries if e.category in ABSTRACT_CATEGORIES]
        if not abstract:
            return 0
        collapsed = _workset_preview_collapse(entries)
    except (CategoryCollisionError, SettingsError) as e:
        return _preview_refusal(ws, e)

    # ⚑ THE SAME FOUR FIELDS the share half above builds a ``Declaration`` from, off
    # the same list. ⚑ ``entry.delivery`` is READ, never re-derived from the key
    # spelling: the entry already carries what its category delivers, and ``seeded``
    # derives a COPY where the other two derive a MOUNT.
    derivations = pair_declarations(
        [
            Declaration(e.label, e.box_dest, e.host_src, e.delivery)
            for e in abstract
        ],
        collapsed.bindings,
        [*collapsed.seeded, *collapsed.synced],
    )

    # ⚑ THE SHAPE IS THE BOX BLOCK'S (``config_display._print_category_block``'s
    # abstract half): declaration line, then the derivation indented beneath it. One
    # form at both nouns — a third spelling of one answer is the confusion Convention
    # 0 is about. The HEADING is this noun's own addition, and it is needed here: this
    # view ALSO prints the declaration rows through the flatten, so the block needs to
    # say which working set it is answering for.
    print("")
    print(f"Derived bindings for working set '{ws.name}':")
    # ⚑ WHAT THE HEADING DOES NOT COVER, said where the user reads it: the resolve
    # above reads no agent file and no box's own file, so a mask in one of those that
    # would swallow one of these rows at launch is not applied.
    print(
        "  (Masks from an agent file or a box's own file are not applied here; "
        "'kanibako box show <box> --effective' shows what a box receives.)"
    )
    for row in derivations:
        print(f"  {row.declaration.key} = {row.declaration.src}")
        print(
            f"    {BINDING_DERIVATIONS_NODE}.{row.declaration.key} = "
            f"{derivation_result(row, collapsed.declared_by)}"
        )
    return 0

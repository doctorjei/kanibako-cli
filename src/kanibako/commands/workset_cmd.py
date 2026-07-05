"""kanibako workset: create, manage, and inspect working sets."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from kanibako.config import config_file_path, load_config
from kanibako.errors import WorksetError
from kanibako.paths import (
    load_std_paths,
    workset_env_path,
    workset_settings_path,
    xdg,
)
from kanibako.utils import confirm_prompt
from kanibako.workset import (
    DEFAULT_WORKSET_ALIAS,
    DEFAULT_WORKSET_ID,
    add_project,
    create_workset,
    delete_workset,
    list_worksets,
    load_workset,
    remove_project,
    resolve_workset_name,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "workset",
        help="Working set commands (create, list, info, rm, set, get, show, reset, connect, disconnect, share)",
        description="Create and manage working sets of related projects.",
    )
    ws_sub = p.add_subparsers(dest="workset_command", metavar="COMMAND")

    # kanibako workset create [path] [--name NAME] [--standalone] [--image IMAGE]
    #                         [--no-vault]
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
    create_p.add_argument(
        "--standalone", action="store_true",
        help="Use standalone mode for projects in this working set",
    )
    create_p.add_argument(
        "-i", "--image", default=None,
        help="Container image to use for projects in this working set",
    )
    create_p.add_argument(
        "--no-vault", action="store_true",
        help="Disable vault directories",
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

    # kanibako workset connect <workset> [source] [--name N]
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
        help="Also remove per-project directories",
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

    # kanibako workset set <workset> <key>=<value> [--force] [--local]
    set_p = ws_sub.add_parser(
        "set",
        help="Set a working set configuration value",
        description=(
            "Set a working set setting (key=value).\n\n"
            "  workset set myws model=sonnet      set 'model'\n"
            "  workset set myws workset.auth.share_allowed=false  set sharing\n"
            "  workset set myws resource.plugins=/p  set resource path\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_p.add_argument("workset", help="Name of the working set")
    set_p.add_argument("key_value", help="key=value pair")
    set_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts",
    )
    set_p.add_argument(
        "--local", action="store_true",
        help="Set resource to project-isolated (resource keys only)",
    )
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
        "--force", action="store_true", help="Skip confirmation prompts",
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
            "'share add' with a new bind, which overwrites the mapping.\n\n"
            "  workset share add myws data /host/data:/home/agent/data\n"
            "  workset share add myws docs /host/docs:/srv/docs --mode ro\n"
            "  workset share rm myws data\n"
            "  workset share list myws\n"
            "  workset share list myws --effective\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    share_sub = share_p.add_subparsers(dest="share_command", metavar="COMMAND")

    # share add WORKSET NAME BIND [--mode {ro,rw}]
    share_add_p = share_sub.add_parser(
        "add",
        help="Add (or overwrite) a shared directory",
        description=(
            "Add a shared directory to a working set. Re-running 'add' with the "
            "same NAME overwrites the existing mapping (this is how you 'update' "
            "a share). BIND is 'host_src:guest_dest'; a relative host_src is "
            "resolved under the working set root."
        ),
    )
    share_add_p.add_argument("workset", help="Name of the working set")
    share_add_p.add_argument("name", help="Share name (identifier: [A-Za-z0-9._-]+)")
    share_add_p.add_argument(
        "bind", metavar="BIND", help="Bind mapping 'host_src:guest_dest'",
    )
    share_add_p.add_argument(
        "--mode", choices=["ro", "rw"], default="rw",
        help="Mount mode: 'rw' (read-write, default) or 'ro' (read-only)",
    )
    share_add_p.set_defaults(func=run_share_add)

    # share rm WORKSET NAME [--mode {ro,rw}]
    share_rm_p = share_sub.add_parser(
        "rm",
        aliases=["remove"],
        help="Remove a shared directory",
        description=(
            "Remove a shared directory from a working set. With no --mode, the "
            "share is removed from whichever mode (ro/rw) contains it; --mode is "
            "required when the same NAME exists in both."
        ),
    )
    share_rm_p.add_argument("workset", help="Name of the working set")
    share_rm_p.add_argument("name", help="Share name to remove")
    share_rm_p.add_argument(
        "--mode", choices=["ro", "rw"], default=None,
        help="Disambiguate when NAME exists in both ro and rw",
    )
    share_rm_p.set_defaults(func=run_share_remove)

    # share list WORKSET [--effective]
    share_list_p = share_sub.add_parser(
        "list",
        aliases=["ls"],
        help="List a working set's shared directories (default)",
        description=(
            "List the shared directories configured for a working set. With "
            "--effective, resolve each share the way a box launch would and show "
            "the final source -> dest [mode] mounts (relative host paths joined "
            "under the working set root)."
        ),
    )
    share_list_p.add_argument("workset", help="Name of the working set")
    share_list_p.add_argument(
        "--effective", action="store_true",
        help="Show resolved mounts (source -> dest [mode]) as a launch would",
    )
    share_list_p.set_defaults(func=run_share_list)

    # Default to list if no share subcommand given.
    share_p.set_defaults(func=run_share_list, effective=False)

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list, quiet=False)


def _load_std():
    """Load config and standard paths."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    return load_std_paths(config)


def _workset_config_path(ws) -> Path:
    """Return the path to the workset-level settings file.

    ONE derivation for every mode (spec §2c: ``meta.workset.settings`` =
    ``@meta.workset.path/settings.yaml``): ``<root>/settings.yaml``.  A NAMED
    workset's file also carries the workset identity (``workset.meta``); the
    cascade-settings tables (box/agent/workset.bindings) coexist there without
    colliding.  The PRIMARY ("default") workset roots at
    ``@config.primary_workset`` (F4 — its old ``@config.data/config.yaml``
    write target was a dead write: the launch cascade never read it).
    """
    return workset_settings_path(ws)


def run_create(args: argparse.Namespace) -> int:
    import os

    std = _load_std()
    path = args.path
    if path is None:
        path = os.getcwd()
    path = Path(path).resolve()
    name = args.name or path.name

    try:
        ws = create_workset(name, path, std)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Credential SHARING is now a settable cascade key (workset.auth.share_allowed
    # via config), NOT a create-time flag — the --distinct-auth flag is retired.

    # Store additional cascade settings in the workset settings.yaml.  Merge
    # into the existing file (created with the workset.meta identity by
    # create_workset) rather than overwriting it, so the identity survives.
    image = getattr(args, "image", None)
    standalone = getattr(args, "standalone", False)
    no_vault = getattr(args, "no_vault", False)
    if image or standalone or no_vault:
        from kanibako.config_io import dump_doc, load_doc
        ws_config = _workset_config_path(ws)
        config_data = load_doc(ws_config) if ws_config.is_file() else {}
        if not isinstance(config_data, dict):
            config_data = {}
        if image:
            config_data["box"] = {"image": image}
        if standalone:
            config_data["standalone"] = True
        if no_vault:
            config_data["enable_vault"] = False
        dump_doc(ws_config, config_data)

    print(f"Created working set '{ws.name}' at {ws.root}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    from kanibako.workset import default_workset

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

    # Load each named workset to get project count.
    rows: list[tuple[str, int, str]] = [
        (f"{DEFAULT_WORKSET_ALIAS} (default)", len(dflt.projects), "<default workset>"),
    ]
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


def run_rm(args: argparse.Namespace) -> int:
    std = _load_std()

    if args.name in (DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS):
        print("Error: The default workset cannot be removed.", file=sys.stderr)
        return 1

    # Check if workset has projects — error unless --force.
    registry = list_worksets(std)
    if args.name in registry:
        try:
            ws = load_workset(registry[args.name])
            if ws.projects and not args.force:
                print(
                    f"Error: workset '{args.name}' has {len(ws.projects)} project(s). "
                    f"Use --force to remove anyway.",
                    file=sys.stderr,
                )
                return 1
        except WorksetError:
            pass

    if not args.force:
        label = "and remove files " if args.purge else ""
        confirm_prompt(
            f"Unregister {label}working set '{args.name}'? Type 'yes' to confirm: "
        )
    try:
        root = delete_workset(args.name, std, remove_files=args.purge)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Deleted working set '{args.name}' (root was {root})")
    return 0


def run_connect(args: argparse.Namespace) -> int:
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

    # J2 lifecycle journal: write-ahead a ``op: connect`` entry around the
    # register-only membership write (connect REGISTERS an externally-existing
    # dir into a workset and NEVER seeds).  The bracket lives HERE (the actual
    # connect command), not in ``add_project`` — which is also the membership-
    # write seam for the deferred move/convert/duplicate pipelines that must NOT
    # journal a ``connect`` op.  Write-ahead order: write entry BEFORE
    # ``add_project`` (the durable membership write), clear immediately after it
    # returns (HARD INVARIANT: registered ==> no pending entry at rest).  The key
    # is the host-side box dir (``ws.projects_dir / project_name``, the dir
    # CONTAINING ``home/`` — uniform J1/J2 key).  On a crash before the clear the
    # entry lingers; ``resolve_workset_project`` clears a stale ``connect`` entry
    # on the next resolve of the now-member box (self-heal, symmetric with the
    # import path).  If ``add_project`` raises, the entry is LEFT (incomplete) and
    # the error propagates after ``_Unwind`` rolls back the in-process effects.
    from kanibako.workset import _journal_connect

    try:
        with _journal_connect(
            std.journal, ws.projects_dir / project_name,
            name=project_name, workset=ws.name,
            workspace=str(source.resolve()),
        ):
            proj = add_project(ws, project_name, source, std)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Added project '{proj.name}' to working set '{ws.name}'")
    return 0


def run_disconnect(args: argparse.Namespace) -> int:
    std = _load_std()

    if args.workset in (DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS):
        print("Error: The default workset cannot be removed.", file=sys.stderr)
        return 1

    registry = list_worksets(std)
    if args.workset not in registry:
        print(f"Error: Working set '{args.workset}' is not registered.", file=sys.stderr)
        return 1

    try:
        ws = load_workset(registry[args.workset])
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Reconcile the positional <project> with the blanket --box (same → warn /
    # differ → error), then resolve path-or-name to the canonical box name via
    # the shared resolver (§Design 8 — same resolver even though "box" reads
    # oddly for a workset member; consistency wins).  A bare member name that
    # is not an independently-registered box still falls back to the raw token
    # (remove_project matches it against the workset's member list by name).
    from kanibako.commands.flags import resolve_subject_value
    from kanibako.paths import resolve_box_target
    project_token = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    if not project_token:
        print("Error: no project specified to disconnect.", file=sys.stderr)
        return 1
    member: str = project_token
    if project_token:
        try:
            from kanibako.config import config_file_path, load_config
            from kanibako.paths import xdg
            config = load_config(config_file_path(xdg("XDG_CONFIG_HOME", ".config")))
            resolved = resolve_box_target(std, config, project_token)
            if resolved.name:
                member = resolved.name
        except Exception:
            member = project_token

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
    print(f"Created:  {ws.created}")
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
    args.local = False
    return _run_workset_config(args)


def run_get(args: argparse.Namespace) -> int:
    """``workset get <workset> <key>``."""
    args.key_value = args.key
    args.reset = None
    args.reset_all = False
    args.effective = False
    args.local = False
    return _run_workset_config(args)


def run_show(args: argparse.Namespace) -> int:
    """``workset show <workset> [--effective]``."""
    args.key_value = None
    args.reset = None
    args.reset_all = False
    args.local = False
    return _run_workset_config(args)


def _run_workset_config(args: argparse.Namespace) -> int:
    """Shared working-set config engine dispatch.

    Handles get, set, show, reset operations via the config_interface engine.
    Credential sharing is an ordinary settable cascade key
    (``workset.auth.share_allowed``) routed through the engine like any other — no
    special-casing (the old ``group_auth`` workset.meta identity key is retired).
    """
    from kanibako.config_interface import (
        ConfigAction,
        ConfigLevel,
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

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    ws_config = _workset_config_path(ws)
    # The workset-tier env FILE (F9): threaded into the engine exactly like the
    # box handler threads its ``<metadata>/env`` — named AND primary worksets
    # (the primary's lives under ``@config.primary_workset``, distinct from the
    # system tier's ``@config.data/env``).
    ws_env = workset_env_path(ws)

    key_value = getattr(args, "key_value", None)

    # Handle --reset mode
    if args.reset is not None:
        if args.reset_all or args.reset == "__ALL__":
            msg = reset_all(
                config_path=ws_config,
                env_path=ws_env,
                force=args.force,
                command_scope=ConfigLevel.workset,
            )
            print(msg)
            return 0

        reset_key = args.reset
        # Full launch cascade so the honest cleared-message can name the
        # now-effective value + source tier (item 1) — mirrors the workset SET
        # handler (system settings file + this workset file; no box scope here).
        msg = reset_config_value(
            reset_key,
            config_path=ws_config,
            env_path=ws_env,
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
    action, key, value = parse_config_arg(key_value)

    # --local flag forces a set operation
    if args.local and action == ConfigAction.get:
        action = ConfigAction.set

    if action == ConfigAction.show:
        return show_config(
            global_config_path=config_file,
            config_path=ws_config,
            env_global=std.data_path / "env",
            env_project=ws_env,
            effective=args.effective,
        )

    if action == ConfigAction.get:
        val = get_config_value(
            key,
            global_config_path=config_file,
            project_toml=ws_config,
            env_global=std.data_path / "env",
            env_project=ws_env,
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
            value = "project"

        # Full launch cascade for a CATEGORY set's set-time E3 probe (Jei (b),
        # 2026-06-29): the workset is the command scope (ws_config lands in the
        # workset slot); thread the system settings file so an @system.* / lower-
        # scope ref in the new value resolves as it would at launch. No box scope
        # at the workset command level.
        msg = set_config_value(
            key, value,
            config_path=ws_config,
            env_path=ws_env,
            cascade_system_path=std.settings,
            cascade_workset_path=ws_config,
            command_scope=ConfigLevel.workset,
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

# Share names are identifiers; the resolver lets a name contain dots, but the
# user-facing surface keeps them simple/unambiguous.
_SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Reminder printed after a mutation: bind mounts are fixed at creation time.
_NEXT_LAUNCH_REMINDER = (
    "Shares apply on the next box launch (bind mounts are fixed at container "
    "creation; a running box is unaffected)."
)


def _share_key(mode: str, name: str) -> str:
    """Build the dotted config key for a workset-scoped share (binding)."""
    return f"workset.bindings.{mode}.{name}"


def _bind_display(value: object) -> str:
    """Render a STORED structured bind value as the user-facing input grammar.

    Storage is a structured ``[host_src, box_dest[, options]]`` list (spec §2a);
    the raw-listing BIND column echoes the ``host_src:box_dest[:options]`` form a
    user would type at ``workset share add`` (mirroring podman ``-v``). A
    non-list legacy scalar falls back to ``str``.
    """
    if isinstance(value, (list, tuple)):
        return ":".join(str(part) for part in value)
    return str(value)


def _resolve_share_workset(name: str):
    """Resolve *name* to a :class:`Workset`, printing + returning on error.

    Returns ``(ws, std)`` on success or ``(None, None)`` on failure (the caller
    returns 1).
    """
    std = _load_std()
    try:
        ws = resolve_workset_name(name, std)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return None, None
    return ws, std


def _load_share_doc(ws_config: Path) -> dict:
    """Load the workset settings.yaml as a nested dict (missing → {})."""
    from kanibako.config_io import load_doc

    return load_doc(ws_config)


def run_share_add(args: argparse.Namespace) -> int:
    """Add (or overwrite) a workset-scoped shared directory.

    Writes ``workset.bindings.{mode}.{name} = host_src:guest_dest`` into the
    working set's ``settings.yaml``. Re-running with the same name overwrites the
    mapping (this is how a share is "updated"; shares are live bind mounts and
    no content sync exists).
    """
    from kanibako.config_io import dump_doc

    name = args.name
    if not _SHARE_NAME_RE.match(name):
        print(
            f"Error: invalid share name '{name}' "
            "(allowed characters: letters, digits, '.', '_', '-').",
            file=sys.stderr,
        )
        return 1

    bind = args.bind
    host_src, sep, guest_dest = bind.partition(":")
    if not sep or not host_src or not guest_dest or ":" in guest_dest:
        print(
            f"Error: invalid bind '{bind}' "
            "(expected exactly one ':' as 'host_src:guest_dest', non-empty halves).",
            file=sys.stderr,
        )
        return 1

    ws, _ = _resolve_share_workset(args.workset)
    if ws is None:
        return 1

    ws_config = _workset_config_path(ws)
    data = _load_share_doc(ws_config)
    subtree = data.setdefault("workset", {}).setdefault("bindings", {}).setdefault(
        args.mode, {}
    )
    existed = name in subtree
    # CLI-INPUT edge: the ``host_src:guest_dest`` grammar (mirroring podman -v) is
    # parsed HERE and STORED in the structured form (spec §2a — a YAML list, NOT a
    # colon-joined string). Storage stays pure structured; the colon form is only
    # the user-facing input/display grammar.
    subtree[name] = [host_src, guest_dest]
    dump_doc(ws_config, data)

    verb = "Updated" if existed else "Added"
    print(
        f"{verb} {args.mode} share '{name}' for working set '{ws.name}': {bind}"
    )
    print(_NEXT_LAUNCH_REMINDER)
    return 0


def run_share_remove(args: argparse.Namespace) -> int:
    """Remove a workset-scoped shared directory from the working set config.

    With ``--mode`` omitted, removes from whichever mode (ro/rw) contains the
    name; errors if the name exists in both (ambiguous) or in neither (missing).
    """
    from kanibako.config_io import dump_doc

    ws, _ = _resolve_share_workset(args.workset)
    if ws is None:
        return 1

    ws_config = _workset_config_path(ws)
    data = _load_share_doc(ws_config)
    path_tree = data.get("workset", {}).get("bindings", {})

    def _present(mode: str) -> bool:
        sub = path_tree.get(mode, {})
        return isinstance(sub, dict) and args.name in sub

    if args.mode is not None:
        modes = [args.mode] if _present(args.mode) else []
    else:
        modes = [m for m in ("ro", "rw") if _present(m)]

    if not modes:
        scope = f" ({args.mode})" if args.mode else ""
        print(
            f"Error: no share '{args.name}'{scope} configured for "
            f"working set '{ws.name}'.",
            file=sys.stderr,
        )
        return 1

    if len(modes) > 1:
        print(
            f"Error: share '{args.name}' exists in both ro and rw for "
            f"working set '{ws.name}'; pass --mode to disambiguate.",
            file=sys.stderr,
        )
        return 1

    mode = modes[0]
    del path_tree[mode][args.name]
    dump_doc(ws_config, data)

    print(
        f"Removed {mode} share '{args.name}' from working set '{ws.name}'."
    )
    print(_NEXT_LAUNCH_REMINDER)
    return 0


def run_share_list(args: argparse.Namespace) -> int:
    """List a working set's shared directories.

    Default: print the working set's own configured shares (raw NAME/MODE →
    bind). With ``--effective``: resolve through the KeyStore snapshot pipeline
    (``assemble_levels → merge → expand → snapshot_category_entries``, scoped to
    the workset file) using the same workset-root join a launch would apply, and
    print the final mounts. Single-route (7c): no second ``resolve_shares`` /
    ``read_shares`` resolver path.
    """
    ws, std = _resolve_share_workset(args.workset)
    if ws is None:
        return 1

    ws_config = _workset_config_path(ws)
    raw_shares = _workset_raw_shares(ws_config)

    if not raw_shares:
        print(f"No shares configured for working set '{ws.name}'.")
        return 0

    if getattr(args, "effective", False):
        return _print_effective_shares(ws, std, ws_config)

    # Raw view: NAME, MODE, BIND (pre-resolution, from the workset file).
    rows: list[tuple[str, str, str]] = [
        (name, mode, _bind_display(value))
        for (mode, name), value in raw_shares.items()
    ]
    rows.sort(key=lambda r: (r[1], r[0]))

    print(f"Shares for working set '{ws.name}':")
    print(f"  {'NAME':<20} {'MODE':<4}  {'BIND'}")
    for name, mode, bind in rows:
        print(f"  {name:<20} {mode:<4}  {bind}")
    return 0


def _workset_raw_shares(ws_config: Path) -> dict[tuple[str, str], object]:
    """Read the workset file's ``workset.bindings.{ro,rw}.{name}`` leaves as a
    ``{(mode, name): raw_value}`` map for the RAW display view.

    Reads the workset partial through the committed ``assemble_levels`` (the SAME
    file reader the launch snapshot uses — single-route, no ``read_shares``), then
    walks its ``workset.bindings.{ro,rw}`` subtree. The raw value is the structured
    ``Bind`` (``@``-refs / ``$XDG`` / ``~`` UNRESOLVED, per §0). Missing file → {}.
    """
    from kanibako.settings_assemble import assemble_levels
    from kanibako.settings_store import Bind, KeyStore, _MISSING

    # assemble_levels returns [box, workset, agent.<active>, agent.default,
    # system, base]; index 1 is the workset partial (the only file we pass).
    levels = assemble_levels(
        agent_name="general",
        base_path=ws_config.parent / "__absent_base__",
        workset_path=ws_config,
    )
    workset_partial = levels[1]
    out: dict[tuple[str, str], object] = {}
    ws_node = dict.get(workset_partial, "workset", _MISSING)
    if not isinstance(ws_node, KeyStore):
        return out
    bindings = dict.get(ws_node, "bindings", _MISSING)
    if not isinstance(bindings, KeyStore):
        return out
    for mode in ("ro", "rw"):
        mode_node = dict.get(bindings, mode, _MISSING)
        if not isinstance(mode_node, KeyStore):
            continue
        for name in dict.keys(mode_node):
            leaf = dict.__getitem__(mode_node, name)
            # Render the Bind back to its on-disk pair shape for _bind_display.
            if isinstance(leaf, Bind):
                out[(mode, name)] = (
                    [leaf.host, leaf.box, leaf.opts]
                    if leaf.opts is not None
                    else [leaf.host, leaf.box]
                )
            else:
                out[(mode, name)] = leaf
    return out


def _print_effective_shares(ws, std, ws_config: Path) -> int:
    """Resolve and print the workset's shares as launch-time mounts.

    Single-route (7c): resolves through the committed KeyStore snapshot pipeline
    (``assemble_levels → merge → expand → snapshot_category_entries``) scoped to
    the workset file — the SAME resolver the launch uses — replacing the retired
    ``resolve_shares``/``read_shares``/``LevelView`` path. A relative host_src is
    joined under the working set root; an absolute host_src passes through; the
    default workset has no root, so relative paths are not joined.
    """
    from kanibako.paths import host_xdg_map
    from kanibako.settings_assemble import assemble_levels
    from kanibako.settings_expand import expand
    from kanibako.settings_launch import snapshot_category_entries
    from kanibako.settings_merge import merge
    from kanibako.settings_resolve import ResolveCtx, SettingsError

    scope_roots: dict[str, str] = {}
    if not ws.is_default:
        ws_root = str(ws.root)
        scope_roots["workset.bindings.ro"] = ws_root
        scope_roots["workset.bindings.rw"] = ws_root

    # Resolver SPLIT (spec §1A / JC-2): Layer-1 ``config.*`` → ``ctx.config``
    # foundation; Layer-2 ``system.*`` → the snapshot floor.  The xdg map is the
    # canonical FULL host map (a data-home-only partial map raised on stored
    # ``$XDG_CACHE_HOME/...`` values), anchored on the resolved ``std.data_home``.
    ctx = ResolveCtx(
        agent_name=None,
        workset_name=None if ws.is_default else ws.name,
        host_home=str(Path.home()),
        xdg=host_xdg_map(std.data_home),
        config={
            "config.data": str(std.data),
            "config.agents": str(std.agents),
            "config.registry": str(std.registry),
            "config.primary_workset": str(std.primary_workset),
            "config.settings": str(std.settings),
        },
    )

    # Fold the resolved Layer-2 system.* tier into the snapshot floor so a share
    # value's @-ref (e.g. @system.channelroot) resolves from the snapshot itself
    # (replicating the old ``_lookup`` map). Keys are flat dotted; assemble
    # explodes them.
    floor: dict[str, object] = {
        "system.channelroot": str(std.channels),
        "system.base_template": str(std.base_template),
    }

    try:
        levels = assemble_levels(
            agent_name="general",
            base_path=ws_config.parent / "__absent_base__",
            workset_path=ws_config,
            floor=floor,
        )
        snapshot = merge(levels)
        expanded = expand(snapshot, ctx)
        entries = snapshot_category_entries(
            expanded, active_agent="general", box_ctx=ctx, scope_roots=scope_roots,
        )
    except SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Effective shares for working set '{ws.name}':")
    for entry in entries:
        if entry.category not in ("bindings.ro", "bindings.rw"):
            continue
        mode = "ro" if entry.options == "ro" else "rw"
        print(f"  {entry.host_src} -> {entry.box_dest}  [{mode}]")
    return 0

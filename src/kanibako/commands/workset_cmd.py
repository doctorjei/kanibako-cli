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
        help="Working set commands (create, list, info, rm, config, connect, disconnect)",
        description="Create and manage working sets of related projects.",
    )
    ws_sub = p.add_subparsers(dest="workset_command", metavar="COMMAND")

    # kanibako workset create [path] [--name NAME] [--standalone] [--image IMAGE]
    #                         [--no-vault] [--distinct-auth]
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
    create_p.add_argument(
        "--distinct-auth", action="store_true",
        help="Use distinct credentials (no sync from host)",
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

    # kanibako workset config <workset> [key[=value]] [--effective] [--reset]
    #                         [--all] [--force] [--local]
    config_p = ws_sub.add_parser(
        "config",
        help="View or modify working set configuration",
        description=(
            "Unified config interface for working set settings.\n\n"
            "  workset config myws                show overrides\n"
            "  workset config myws --effective     show resolved values\n"
            "  workset config myws model           get the value of 'model'\n"
            "  workset config myws model=sonnet    set 'model' to 'sonnet'\n"
            "  workset config myws auth=distinct   set auth mode\n"
            "  workset config myws --reset model   reset one key\n"
            "  workset config myws --reset --all   reset all overrides\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_p.add_argument("workset", help="Name of the working set")
    config_p.add_argument(
        "key_value", nargs="?", default=None,
        help="Config key or key=value pair",
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

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list, quiet=False)


def _load_std():
    """Load config and standard paths."""
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    return load_std_paths(config)


def _workset_config_path(ws) -> Path:
    """Return the path to the workset-level config.toml."""
    return ws.root / "config.toml"


def run_create(args: argparse.Namespace) -> int:
    import os

    std = _load_std()
    path = args.path
    if path is None:
        path = os.getcwd()
    path = Path(path).resolve()
    name = args.name or path.name

    # Store additional flags in workset config if provided.
    auth = "distinct" if getattr(args, "distinct_auth", False) else "shared"

    try:
        ws = create_workset(name, path, std)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Set auth mode if distinct.
    if auth == "distinct":
        ws.auth = auth
        _write_workset_toml(ws)

    # Store additional settings in workset-level config.toml.
    image = getattr(args, "image", None)
    standalone = getattr(args, "standalone", False)
    no_vault = getattr(args, "no_vault", False)
    if image or standalone or no_vault:
        from kanibako.config_interface import _serialize_toml
        config_data: dict = {}
        if image:
            config_data["container_image"] = image
        if standalone:
            config_data["standalone"] = True
        if no_vault:
            config_data["vault_enabled"] = False
        ws_config = _workset_config_path(ws)
        ws_config.write_text(_serialize_toml(config_data))

    print(f"Created working set '{ws.name}' at {ws.root}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    std = _load_std()
    registry = list_worksets(std)
    quiet = getattr(args, "quiet", False)

    if not registry:
        if not quiet:
            print("No working sets registered.")
        return 0

    if quiet:
        for name in sorted(registry):
            print(name)
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


def run_rm(args: argparse.Namespace) -> int:
    std = _load_std()

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

    try:
        proj = add_project(ws, project_name, source)
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Added project '{proj.name}' to working set '{ws.name}'")
    return 0


def run_disconnect(args: argparse.Namespace) -> int:
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


def run_config(args: argparse.Namespace) -> int:
    """Unified config interface for working set settings.

    Handles get, set, show, reset operations via the config_interface engine.
    The ``auth`` key is special-cased to update workset.toml directly.
    """
    from kanibako.config_interface import (
        ConfigAction,
        get_config_value,
        parse_config_arg,
        reset_all,
        reset_config_value,
        set_config_value,
        show_config,
    )

    std = _load_std()
    registry = list_worksets(std)
    ws_name = args.workset
    if ws_name not in registry:
        print(f"Error: Working set '{ws_name}' is not registered.", file=sys.stderr)
        return 1

    try:
        ws = load_workset(registry[ws_name])
    except WorksetError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    ws_config = _workset_config_path(ws)

    key_value = getattr(args, "key_value", None)

    # Handle --reset mode
    if args.reset is not None:
        if args.reset_all or args.reset == "__ALL__":
            msg = reset_all(
                config_path=ws_config,
                force=args.force,
            )
            print(msg)
            return 0

        reset_key = args.reset
        # Special case: resetting auth reverts to "shared"
        if reset_key == "auth":
            ws.auth = "shared"
            _write_workset_toml(ws)
            print("Reset auth (reverts to default: shared)")
            return 0

        msg = reset_config_value(
            reset_key,
            config_path=ws_config,
        )
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
            effective=args.effective,
        )

    if action == ConfigAction.get:
        # Special case: auth key lives in workset.toml
        if key == "auth":
            print(ws.auth)
            return 0

        val = get_config_value(
            key,
            global_config_path=config_file,
            project_toml=ws_config,
        )
        if val is not None:
            print(val)
        else:
            print("(not set)", file=sys.stderr)
        return 0

    if action == ConfigAction.set:
        # Special case: auth key updates workset.toml directly
        if key == "auth":
            if value not in ("shared", "distinct"):
                print(
                    f"Error: auth mode must be 'shared' or 'distinct', got '{value}'",
                    file=sys.stderr,
                )
                return 1

            old_auth = ws.auth
            ws.auth = value
            _write_workset_toml(ws)

            if value == "distinct" and old_auth != "distinct":
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
                print(f"Set auth mode to '{value}' for '{ws.name}'.")
            return 0

        # Handle --local for resource keys
        if args.local:
            from kanibako.config_interface import _is_resource_key, _resolve_key
            canonical = _resolve_key(key)
            if not _is_resource_key(canonical):
                print("Error: --local only applies to resource.* keys", file=sys.stderr)
                return 1
            value = "project"

        msg = set_config_value(
            key, value,
            config_path=ws_config,
        )
        print(msg)
        return 0

    return 0

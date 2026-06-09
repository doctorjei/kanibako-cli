"""kanibako crab: crab management, authentication, and coordination."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kanibako.crabs import CrabConfig
    from kanibako.paths import StandardPaths


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "crab",
        help="Crab (agent) management, authentication, and settings",
        description="Manage crab configurations, authentication, and helper instances.",
    )
    crab_sub = p.add_subparsers(dest="crab_command", metavar="COMMAND")

    # crab list (default)
    list_p = crab_sub.add_parser(
        "list",
        aliases=["ls"],
        help="List configured crabs",
    )
    list_p.add_argument("-q", "--quiet", action="store_true", help="Names only")
    list_p.set_defaults(func=run_list)

    # crab info <crab>
    info_p = crab_sub.add_parser(
        "info",
        aliases=["inspect"],
        help="Show crab configuration details",
    )
    info_p.add_argument("crab_id", help="Crab identifier")
    info_p.set_defaults(func=run_info)

    # crab config <crab> [key[=value]] [--effective] [--reset] [--all] [--force]
    config_p = crab_sub.add_parser(
        "config",
        help="View or modify crab configuration",
        description=(
            "Unified config interface for crab settings.\n\n"
            "  crab config mycrab                 show all settings\n"
            "  crab config mycrab model            get the value of 'model'\n"
            "  crab config mycrab model=sonnet     set 'model' to 'sonnet'\n"
            "  crab config mycrab env.FOO=bar      set env var FOO\n"
            "  crab config mycrab --reset model    reset one key\n"
            "  crab config mycrab --reset --all    reset all overrides\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_p.add_argument("crab_id", help="Crab identifier")
    config_p.add_argument(
        "key_value", nargs="?", default=None,
        help="Config key or key=value pair",
    )
    config_p.add_argument(
        "--effective", action="store_true",
        help="Show resolved values including defaults",
    )
    config_p.add_argument(
        "--reset", nargs="?", const="__RESET__", default=None,
        help="Remove override for the given key",
    )
    config_p.add_argument(
        "--all", action="store_true", dest="all_keys",
        help="Reset all overrides (only valid with --reset)",
    )
    config_p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompts",
    )
    config_p.set_defaults(func=run_config)

    # agent reauth [project]
    reauth_p = crab_sub.add_parser(
        "reauth",
        help="Check authentication and login if needed",
        description=(
            "Verify agent authentication status and run interactive "
            "login if credentials are expired or missing."
        ),
    )
    reauth_p.add_argument(
        "project", nargs="?", default=None,
        help="Target project directory or name",
    )
    reauth_p.set_defaults(func=run_reauth)

    # crab helper -- delegate to helper_cmd
    from kanibako.commands.helper_cmd import add_helper_subparsers

    helper_p = crab_sub.add_parser(
        "helper",
        help="Manage helper instances",
        description="Spawn, list, stop, cleanup, and respawn helper instances.",
    )
    add_helper_subparsers(helper_p)

    # crab fork <name> -- delegate to fork_cmd
    from kanibako.commands.fork_cmd import run_fork

    fork_p = crab_sub.add_parser(
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
    fork_p.set_defaults(func=run_fork, command="crab")

    # crab diagnose
    from kanibako.commands.diagnose import run_crab_diagnose

    diagnose_p = crab_sub.add_parser(
        "diagnose",
        help="Check crab status and configuration",
    )
    diagnose_p.set_defaults(func=run_crab_diagnose)

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list, quiet=False)


# ---------------------------------------------------------------------------
# Crab list / info / config + agent reauth handlers
# ---------------------------------------------------------------------------


def _load_std() -> StandardPaths:
    """Load config and return the resolved standard paths."""
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import xdg, load_std_paths

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    return load_std_paths(config)


def run_list(args: argparse.Namespace) -> int:
    """List configured crabs."""
    from kanibako.crabs import load_crab_config

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    adir = std.crabs
    if not adir.is_dir():
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print("No crabs configured.")
        return 0

    toml_files = sorted(adir.glob("*.toml"))
    if not toml_files:
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print("No crabs configured.")
        return 0

    quiet = getattr(args, "quiet", False)
    if quiet:
        for f in toml_files:
            print(f.stem)
        return 0

    print(f"{'NAME':<20} {'SHELL':<12} {'MODEL'}")
    for f in toml_files:
        cfg = load_crab_config(f)
        name = f.stem
        shell = cfg.shell or "standard"
        model = cfg.state.get("model", "-")
        print(f"{name:<20} {shell:<12} {model}")
    return 0


def run_info(args: argparse.Namespace) -> int:
    """Show crab configuration details."""
    from kanibako.crabs import load_crab_config

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    crab_id = args.crab_id
    path = std.crabs / f"{crab_id}.toml"
    if not path.exists():
        print(f"Error: crab '{crab_id}' not found ({path})", file=sys.stderr)
        return 1

    cfg = load_crab_config(path)
    print(f"Name:         {cfg.name or crab_id}")
    print(f"Shell:        {cfg.shell}")
    if cfg.run_args:
        print(f"Default args: {' '.join(cfg.run_args)}")
    else:
        print("Default args: (none)")

    if cfg.state:
        print("State:")
        for k, v in sorted(cfg.state.items()):
            print(f"  {k} = {v}")
    else:
        print("State:        (none)")

    if cfg.env:
        print("Env:")
        for k, v in sorted(cfg.env.items()):
            print(f"  {k} = {v}")
    else:
        print("Env:          (none)")

    if cfg.shared_caches:
        print("Shared:")
        for k, v in sorted(cfg.shared_caches.items()):
            print(f"  {k} = {v}")
    else:
        print("Shared:       (none)")

    return 0


def run_config(args: argparse.Namespace) -> int:
    """View or modify crab configuration.

    Maps config keys to crab TOML sections:
      model, start_mode, etc. -> [state]
      env.X                   -> [env]
      shared.X                -> [shared]
      shell, run_args         -> [crab]
    """
    from kanibako.crabs import load_crab_config, write_crab_config

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    crab_id = args.crab_id
    path = std.crabs / f"{crab_id}.toml"
    if not path.exists():
        print(f"Error: crab '{crab_id}' not found ({path})", file=sys.stderr)
        return 1

    cfg = load_crab_config(path)
    key_value = getattr(args, "key_value", None)

    # Handle --reset
    if args.reset is not None:
        if args.all_keys:
            if not args.force:
                from kanibako.utils import confirm_prompt
                from kanibako.errors import UserCancelled

                try:
                    confirm_prompt(
                        "Reset all crab config overrides? Type 'yes' to proceed: "
                    )
                except UserCancelled:
                    print("Aborted.")
                    return 0
            # Reset to defaults
            cfg.state.clear()
            cfg.env.clear()
            cfg.shared_caches.clear()
            cfg.run_args.clear()
            write_crab_config(path, cfg)
            print("Reset all crab config overrides.")
            return 0

        # Key can come from --reset VALUE or from positional key_value.
        reset_key = args.reset if args.reset != "__RESET__" else key_value
        if not reset_key:
            print("Error: --reset requires a key name (or --all)", file=sys.stderr)
            return 1

        key = reset_key.strip()
        changed = _reset_crab_key(cfg, key)
        if changed:
            write_crab_config(path, cfg)
            print(f"Reset {key}")
        else:
            print(f"No override for {key}")
        return 0

    # Parse key/value argument
    if key_value is None:
        # Show mode
        return _show_crab_config(cfg, args.crab_id, effective=args.effective)

    if "=" in key_value:
        key, _, value = key_value.partition("=")
        key = key.strip()
        value = value.strip()
        _set_crab_key(cfg, key, value)
        write_crab_config(path, cfg)
        print(f"Set {key}={value}")
        return 0

    # Get mode
    key = key_value.strip()
    val = _get_crab_key(cfg, key)
    if val is not None:
        print(val)
    else:
        print("(not set)", file=sys.stderr)
    return 0


def _get_crab_key(cfg: CrabConfig, key: str) -> str | None:
    """Read a single key from crab config."""
    if key.startswith("env."):
        env_name = key[4:]
        return cfg.env.get(env_name)
    if key.startswith("shared."):
        cache_name = key[7:]
        return cfg.shared_caches.get(cache_name)
    if key == "shell":
        return cfg.shell
    if key == "name":
        return cfg.name or None
    if key == "run_args":
        return " ".join(cfg.run_args) if cfg.run_args else None
    # Everything else goes to state
    return cfg.state.get(key)


def _set_crab_key(cfg: CrabConfig, key: str, value: str) -> None:
    """Set a single key in crab config."""
    if key.startswith("env."):
        env_name = key[4:]
        cfg.env[env_name] = value
    elif key.startswith("shared."):
        cache_name = key[7:]
        cfg.shared_caches[cache_name] = value
    elif key == "shell":
        cfg.shell = value
    elif key == "name":
        cfg.name = value
    elif key == "run_args":
        cfg.run_args = value.split()
    else:
        # State section (model, start_mode, autonomous, etc.)
        cfg.state[key] = value


def _reset_crab_key(cfg: CrabConfig, key: str) -> bool:
    """Remove a single key from crab config.  Returns True if found."""
    if key.startswith("env."):
        env_name = key[4:]
        if env_name in cfg.env:
            del cfg.env[env_name]
            return True
        return False
    if key.startswith("shared."):
        cache_name = key[7:]
        if cache_name in cfg.shared_caches:
            del cfg.shared_caches[cache_name]
            return True
        return False
    if key == "shell":
        cfg.shell = "standard"
        return True
    if key == "name":
        cfg.name = ""
        return True
    if key == "run_args":
        if cfg.run_args:
            cfg.run_args.clear()
            return True
        return False
    if key in cfg.state:
        del cfg.state[key]
        return True
    return False


def _show_crab_config(
    cfg: CrabConfig, crab_id: str, *, effective: bool = False,
) -> int:
    """Display crab config."""
    has_output = False

    # [crab] section
    print(f"  name = {cfg.name or crab_id}")
    print(f"  shell = {cfg.shell}")
    if cfg.run_args:
        print(f"  run_args = {cfg.run_args}")
    has_output = True

    # [state] section
    if cfg.state:
        for k, v in sorted(cfg.state.items()):
            print(f"  {k} = {v}")
        has_output = True
    elif effective:
        print("  # (no state overrides)")

    # [env] section
    if cfg.env:
        for k, v in sorted(cfg.env.items()):
            print(f"  env.{k} = {v}")
        has_output = True

    # [shared] section
    if cfg.shared_caches:
        for k, v in sorted(cfg.shared_caches.items()):
            print(f"  shared.{k} = {v}")
        has_output = True

    if not has_output:
        print("  (no overrides)")

    return 0


def run_reauth(args: argparse.Namespace) -> int:
    """Check authentication and login if needed."""
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import xdg, load_std_paths, resolve_any_project
    from kanibako.targets import resolve_target

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    # Resolve project to check auth mode.
    std = load_std_paths(config)
    proj = resolve_any_project(std, config, getattr(args, "project", None))

    try:
        target = resolve_target(config.box_crab or None)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not target.has_binary:
        print("No agent target configured.", file=sys.stderr)
        return 1

    if not proj.group_auth:
        # Check project's own credentials instead of host.
        creds_path = target.credential_check_path(proj.shell_path)
        if creds_path and creds_path.is_file():
            print(
                f"{target.display_name}: distinct auth (project credentials exist).",
                file=sys.stderr,
            )
            return 0
        else:
            print(
                f"{target.display_name}: distinct auth -- no credentials found. "
                "Launch the container to authenticate.",
                file=sys.stderr,
            )
            return 1

    if target.check_auth():
        # Sync refreshed credentials to the project shell directory
        if proj.group_auth:
            target.refresh_credentials(proj.shell_path)
        print(f"{target.display_name}: authenticated.", file=sys.stderr)
        return 0
    else:
        print(f"{target.display_name}: authentication failed.", file=sys.stderr)
        return 1

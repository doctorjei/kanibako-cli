"""kanibako agent: agent management, authentication, and coordination."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kanibako.agents import AgentConfig


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "agent",
        help="Agent management, authentication, and helpers",
        description="Manage agent configurations, authentication, and helper instances.",
    )
    agent_sub = p.add_subparsers(dest="agent_command", metavar="COMMAND")

    # agent list (default)
    list_p = agent_sub.add_parser(
        "list",
        aliases=["ls"],
        help="List configured agents",
    )
    list_p.add_argument("-q", "--quiet", action="store_true", help="Names only")
    list_p.set_defaults(func=run_list)

    # agent info <agent>
    info_p = agent_sub.add_parser(
        "info",
        aliases=["inspect"],
        help="Show agent configuration details",
    )
    info_p.add_argument("agent_id", help="Agent identifier")
    info_p.set_defaults(func=run_info)

    # agent config <agent> [key[=value]] [--effective] [--reset] [--all] [--force]
    config_p = agent_sub.add_parser(
        "config",
        help="View or modify agent configuration",
        description=(
            "Unified config interface for agent settings.\n\n"
            "  agent config myagent                 show all settings\n"
            "  agent config myagent model            get the value of 'model'\n"
            "  agent config myagent model=sonnet     set 'model' to 'sonnet'\n"
            "  agent config myagent env.FOO=bar      set env var FOO\n"
            "  agent config myagent --reset model    reset one key\n"
            "  agent config myagent --reset --all    reset all overrides\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_p.add_argument("agent_id", help="Agent identifier")
    config_p.add_argument(
        "key_value", nargs="?", default=None,
        help="Config key or key=value pair",
    )
    config_p.add_argument(
        "--effective", action="store_true",
        help="Show resolved values including defaults",
    )
    config_p.add_argument(
        "--reset", action="store_true",
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
    reauth_p = agent_sub.add_parser(
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

    # agent helper -- delegate to helper_cmd
    from kanibako.commands.helper_cmd import add_helper_subparsers

    helper_p = agent_sub.add_parser(
        "helper",
        help="Manage helper instances",
        description="Spawn, list, stop, cleanup, and respawn helper instances.",
    )
    add_helper_subparsers(helper_p)

    # agent fork <name> -- delegate to fork_cmd
    from kanibako.commands.fork_cmd import run_fork

    fork_p = agent_sub.add_parser(
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
    fork_p.set_defaults(func=run_fork, command="agent")

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list, quiet=False)


# ---------------------------------------------------------------------------
# Agent list / info / config / reauth handlers
# ---------------------------------------------------------------------------


def _load_data_path() -> Path:
    """Load config and return the data_path."""
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import xdg, load_std_paths

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)
    return std.data_path


def run_list(args: argparse.Namespace) -> int:
    """List configured agents."""
    from kanibako.agents import agents_dir, load_agent_config

    try:
        data_path = _load_data_path()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    adir = agents_dir(data_path)
    if not adir.is_dir():
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print("No agents configured.")
        return 0

    toml_files = sorted(adir.glob("*.toml"))
    if not toml_files:
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print("No agents configured.")
        return 0

    quiet = getattr(args, "quiet", False)
    if quiet:
        for f in toml_files:
            print(f.stem)
        return 0

    print(f"{'NAME':<20} {'SHELL':<12} {'MODEL'}")
    for f in toml_files:
        cfg = load_agent_config(f)
        name = f.stem
        shell = cfg.shell or "standard"
        model = cfg.state.get("model", "-")
        print(f"{name:<20} {shell:<12} {model}")
    return 0


def run_info(args: argparse.Namespace) -> int:
    """Show agent configuration details."""
    from kanibako.agents import agent_toml_path, load_agent_config

    try:
        data_path = _load_data_path()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    agent_id = args.agent_id
    path = agent_toml_path(data_path, agent_id)
    if not path.exists():
        print(f"Error: agent '{agent_id}' not found ({path})", file=sys.stderr)
        return 1

    cfg = load_agent_config(path)
    print(f"Name:         {cfg.name or agent_id}")
    print(f"Shell:        {cfg.shell}")
    if cfg.default_args:
        print(f"Default args: {' '.join(cfg.default_args)}")
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
    """View or modify agent configuration.

    Maps config keys to agent TOML sections:
      model, start_mode, etc. -> [state]
      env.X                   -> [env]
      shared.X                -> [shared]
      shell, default_args     -> [agent]
    """
    from kanibako.agents import agent_toml_path, load_agent_config, write_agent_config

    try:
        data_path = _load_data_path()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    agent_id = args.agent_id
    path = agent_toml_path(data_path, agent_id)
    if not path.exists():
        print(f"Error: agent '{agent_id}' not found ({path})", file=sys.stderr)
        return 1

    cfg = load_agent_config(path)
    key_value = getattr(args, "key_value", None)

    # Handle --reset
    if args.reset:
        if args.all_keys:
            if not args.force:
                from kanibako.utils import confirm_prompt
                from kanibako.errors import UserCancelled

                try:
                    confirm_prompt(
                        "Reset all agent config overrides? Type 'yes' to proceed: "
                    )
                except UserCancelled:
                    print("Aborted.")
                    return 0
            # Reset to defaults
            cfg.state.clear()
            cfg.env.clear()
            cfg.shared_caches.clear()
            cfg.default_args.clear()
            write_agent_config(path, cfg)
            print("Reset all agent config overrides.")
            return 0

        if not key_value:
            print("Error: --reset requires a key name (or --all)", file=sys.stderr)
            return 1

        key = key_value.strip()
        changed = _reset_agent_key(cfg, key)
        if changed:
            write_agent_config(path, cfg)
            print(f"Reset {key}")
        else:
            print(f"No override for {key}")
        return 0

    # Parse key/value argument
    if key_value is None:
        # Show mode
        return _show_agent_config(cfg, args.agent_id, effective=args.effective)

    if "=" in key_value:
        key, _, value = key_value.partition("=")
        key = key.strip()
        value = value.strip()
        _set_agent_key(cfg, key, value)
        write_agent_config(path, cfg)
        print(f"Set {key}={value}")
        return 0

    # Get mode
    key = key_value.strip()
    val = _get_agent_key(cfg, key)
    if val is not None:
        print(val)
    else:
        print("(not set)", file=sys.stderr)
    return 0


def _get_agent_key(cfg: AgentConfig, key: str) -> str | None:
    """Read a single key from agent config."""
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
    if key == "default_args":
        return " ".join(cfg.default_args) if cfg.default_args else None
    # Everything else goes to state
    return cfg.state.get(key)


def _set_agent_key(cfg: AgentConfig, key: str, value: str) -> None:
    """Set a single key in agent config."""
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
    elif key == "default_args":
        cfg.default_args = value.split()
    else:
        # State section (model, start_mode, autonomous, etc.)
        cfg.state[key] = value


def _reset_agent_key(cfg: AgentConfig, key: str) -> bool:
    """Remove a single key from agent config.  Returns True if found."""
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
    if key == "default_args":
        if cfg.default_args:
            cfg.default_args.clear()
            return True
        return False
    if key in cfg.state:
        del cfg.state[key]
        return True
    return False


def _show_agent_config(
    cfg: AgentConfig, agent_id: str, *, effective: bool = False,
) -> int:
    """Display agent config."""
    has_output = False

    # [agent] section
    print(f"  name = {cfg.name or agent_id}")
    print(f"  shell = {cfg.shell}")
    if cfg.default_args:
        print(f"  default_args = {cfg.default_args}")
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
        target = resolve_target(config.target_name or None)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not target.has_binary:
        print("No agent target configured.", file=sys.stderr)
        return 1

    if proj.auth == "distinct":
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
        print(f"{target.display_name}: authenticated.", file=sys.stderr)
        return 0
    else:
        print(f"{target.display_name}: authentication failed.", file=sys.stderr)
        return 1

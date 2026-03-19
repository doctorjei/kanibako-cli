"""kanibako system: global configuration, self-update, and system info."""

from __future__ import annotations

import argparse
import sys

from kanibako import __version__
from kanibako.config import config_file_path, load_config
from kanibako.paths import xdg


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "system",
        help="Global configuration, upgrades, and system information",
        description="Manage global kanibako configuration and perform system tasks.",
    )
    sys_sub = p.add_subparsers(dest="system_command", metavar="COMMAND")

    # system info (default)
    info_p = sys_sub.add_parser(
        "info",
        aliases=["inspect"],
        help="Show system information",
    )
    info_p.set_defaults(func=run_info)

    # system config [key[=value]] [--effective] [--reset] [--all] [--force]
    config_p = sys_sub.add_parser(
        "config",
        help="View or modify global configuration",
    )
    config_p.add_argument(
        "key_value", nargs="?", default=None,
        help="key or key=value",
    )
    config_p.add_argument(
        "--effective", action="store_true",
        help="Show all resolved values including defaults",
    )
    config_p.add_argument(
        "--reset", action="store_true",
        help="Remove an override (revert to default)",
    )
    config_p.add_argument(
        "--all", action="store_true", dest="all_keys",
        help="With --reset: remove all overrides",
    )
    config_p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompts",
    )
    config_p.set_defaults(func=run_config)

    # system upgrade [--check]
    from kanibako.commands.upgrade import run as run_upgrade_fn

    upgrade_p = sys_sub.add_parser(
        "upgrade",
        help="Upgrade kanibako to the latest version",
    )
    upgrade_p.add_argument(
        "--check", action="store_true",
        help="Check for updates without installing",
    )
    upgrade_p.set_defaults(func=run_upgrade_fn)

    # system diagnose
    from kanibako.commands.diagnose import run_system_diagnose

    diagnose_p = sys_sub.add_parser(
        "diagnose",
        help="Check system health (runtime, images, agents, storage)",
    )
    diagnose_p.set_defaults(func=run_system_diagnose)

    # Default to info when 'system' is run without a subcommand
    p.set_defaults(func=run_info)


def run_info(args: argparse.Namespace) -> int:
    """Show system information: version, paths, runtime."""
    import platform

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)

    print(f"Kanibako v{__version__}")
    print(f"Python:    {platform.python_version()}")

    if cf.exists():
        print(f"Config:    {cf}")
        config = load_config(cf)
        data_home = xdg("XDG_DATA_HOME", ".local/share")
        data_path = data_home / (config.paths_data_path or "kanibako")
        print(f"Data:      {data_path}")
    else:
        print(
            "Config:    (not initialized — run 'kanibako setup' or just 'kanibako start')"
        )

    # Container runtime
    try:
        import subprocess

        from kanibako.container import ContainerRuntime

        runtime = ContainerRuntime()
        result = subprocess.run(
            [runtime.cmd, "--version"], capture_output=True, text=True,
        )
        version = result.stdout.strip() if result.returncode == 0 else "unknown"
        print(f"Runtime:   {runtime.cmd} ({version})")
    except Exception:
        print(
            "Runtime:   not found — install podman (https://podman.io/) or Docker"
        )

    # Install method
    try:
        from kanibako.commands.upgrade import _get_repo_dir

        repo = _get_repo_dir()
        if repo is not None:
            print(f"Install:   git ({repo})")
        else:
            print("Install:   pip")
    except Exception:
        print("Install:   pip")

    # Agent count
    try:
        from kanibako.targets import discover_targets

        targets = discover_targets()
        count = len(targets)
        if count > 0:
            print(
                f"Agents:    {count} detected (use 'kanibako crab list' for details)"
            )
        else:
            print(
                "Agents:    none (install a plugin: pip install kanibako-agent-claude)"
            )
    except Exception:
        pass

    print()
    print("Tip: Run 'kanibako system diagnose' for a full health check.")

    return 0


def run_config(args: argparse.Namespace) -> int:
    """View or modify global configuration."""
    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)

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

    key_value = getattr(args, "key_value", None)
    action, key, value = parse_config_arg(key_value)

    # --reset --all
    if args.reset and getattr(args, "all_keys", False):
        msg = reset_all(config_path=cf, force=args.force)
        print(msg)
        return 0

    # --reset <key>
    if args.reset:
        if not key:
            print(
                "Error: --reset requires a key (or use --reset --all).",
                file=sys.stderr,
            )
            return 1
        msg = reset_config_value(key, config_path=cf)
        print(msg)
        return 0

    # show (no args)
    if action == ConfigAction.show:
        show_config(
            global_config_path=cf,
            config_path=cf,
            effective=args.effective,
        )
        return 0

    # get
    if action == ConfigAction.get:
        if not is_known_key(key):
            print(f"Error: unknown config key: {key}", file=sys.stderr)
            return 1
        val = get_config_value(key, global_config_path=cf)
        if val is None:
            print(f"{key}: (not set)")
        else:
            print(f"{key}={val}")
        return 0

    # set
    if action == ConfigAction.set:
        msg = set_config_value(key, value, config_path=cf, is_system=True)
        print(msg)
        return 0

    return 0

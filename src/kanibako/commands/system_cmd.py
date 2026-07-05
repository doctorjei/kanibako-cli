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

    # system set <key>=<value> [--force]
    set_p = sys_sub.add_parser(
        "set",
        help="Set a global configuration value",
        description=(
            "Set a global setting (key=value).\n\n"
            "  system set model=opus              set the global default model\n"
            "  system set env.EDITOR=nano         set a global env var\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_p.add_argument("key_value", help="key=value pair")
    set_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts",
    )
    set_p.set_defaults(func=run_set)

    # system reset <key> | --all  [--force]
    reset_p = sys_sub.add_parser(
        "reset",
        help="Reset (remove) a global configuration override",
        description=(
            "Remove a global override, reverting to the default.\n\n"
            "  system reset model                 reset one key\n"
            "  system reset --all                 reset all overrides\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reset_p.add_argument("key", nargs="?", default=None, help="Config key to reset")
    reset_p.add_argument(
        "--all", action="store_true", dest="all_keys",
        help="Remove all overrides",
    )
    reset_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts",
    )
    reset_p.set_defaults(func=run_reset)

    # system get <key>
    get_p = sys_sub.add_parser(
        "get",
        help="Get a global configuration value",
        description="Read one global setting.",
    )
    get_p.add_argument("key", help="Config key to read")
    get_p.set_defaults(func=run_get)

    # system show [--effective]
    show_p = sys_sub.add_parser(
        "show",
        help="Show global configuration",
        description=(
            "Show global settings.\n\n"
            "  system show                        show overrides\n"
            "  system show --effective            show resolved values\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    show_p.add_argument(
        "--effective", action="store_true",
        help="Show all resolved values including defaults",
    )
    show_p.set_defaults(func=run_show)

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
        from pathlib import Path

        from kanibako.paths import resolve_system_paths
        data_home = xdg("XDG_DATA_HOME", ".local/share")
        data_path = resolve_system_paths(
            config.config_paths, data_home=data_home, home=Path.home(),
        )["config.data"]
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
                f"Agents:    {count} detected (use 'kanibako agent list' for details)"
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


def run_set(args: argparse.Namespace) -> int:
    """``system set <key>=<value>``."""
    args.reset = False
    args.all_keys = False
    args.effective = False
    return _run_system_config(args)


def run_reset(args: argparse.Namespace) -> int:
    """``system reset <key>`` / ``system reset --all``."""
    args.reset = True
    args.key_value = getattr(args, "key", None)
    args.effective = False
    return _run_system_config(args)


def run_get(args: argparse.Namespace) -> int:
    """``system get <key>``."""
    args.key_value = args.key
    args.reset = False
    args.all_keys = False
    args.effective = False
    args.force = False
    return _run_system_config(args)


def run_show(args: argparse.Namespace) -> int:
    """``system show [--effective]``."""
    args.key_value = None
    args.reset = False
    args.all_keys = False
    args.force = False
    return _run_system_config(args)


def _run_system_config(args: argparse.Namespace) -> int:
    """Shared global-config engine dispatch.

    The SYSTEM scope keeps STRUCTURAL CONFIG (the ``system.*`` path-tier
    family) in the ``~/.config/kanibako_config.yaml`` CONFIG file (``cf``) and
    routes SETTINGS (``system.default_agent``, the ``system.auth.*`` chain,
    agent settings, downward scope defaults) to ``@config.settings`` =
    ``global/settings.yaml`` (``ssp``), via the ``system_settings_path`` arg —
    the same file the launch cascade's system tier reads (F2/F3).

    The system-tier ENV file is ``@config.data/env`` (``env_sys``) — the exact
    file the launch env layering reads as its system tier (start.py
    ``global_env_path = std.data_path / "env"``; precedence system < agent <
    workset < box), threaded into every verb so ``system set env.X`` lands
    where the launch reads it.
    """
    from kanibako.paths import load_std_paths

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)
    # The system SETTINGS file (separate from the kanibako_config.yaml CONFIG file).
    std = load_std_paths(load_config(cf))
    ssp = std.settings
    # The system-tier env file (mirrors the launch's system env source).
    env_sys = std.data_path / "env"

    from kanibako.agent_config import agent_settings_path
    from kanibako.agent_ref import canonicalize_agent_ref
    from kanibako.agent_representation import agent_default_bind_keys
    from kanibako.config_interface import (
        ConfigAction,
        ConfigLevel,
        _parse_agent_node_bind_key,
        get_config_value,
        is_known_key,
        parse_config_arg,
        reset_all,
        reset_config_value,
        set_config_value,
        show_config,
    )
    from kanibako.errors import ConfigError

    key_value = getattr(args, "key_value", None)
    action, key, value = parse_config_arg(key_value)

    # --reset --all
    if args.reset and getattr(args, "all_keys", False):
        msg = reset_all(
            config_path=cf, env_path=env_sys, force=args.force,
            system_settings_path=ssp,
            command_scope=ConfigLevel.system,
        )
        print(msg)
        return 0

    # --reset <key>
    if args.reset:
        if not key:
            print(
                "Error: reset requires a key (or --all).",
                file=sys.stderr,
            )
            return 1
        # Ensure the system settings dir exists for SETTINGS removals.
        ssp.parent.mkdir(parents=True, exist_ok=True)
        # item-0 (per-node DESCRIPTOR bind reset, item 3): a system-scope
        # ``agent.<node>.bindings.{ro,rw}.<name>`` reset removes the repoint from
        # the node's OWN settings file, reverting the bind to the descriptor FLOOR.
        # Thread that detect-free per-node floor registry (``agent_default_bind_
        # keys``) so the honest cleared-message names the reverted-to floor value
        # (symmetric with the set handler's ``default_categories`` build).
        reset_default_categories = None
        reset_bind_parse = _parse_agent_node_bind_key(key)
        if reset_bind_parse is not None:
            node_raw, _rcat, _rname = reset_bind_parse
            try:
                reset_node = canonicalize_agent_ref(node_raw)
            except ConfigError:
                reset_node = None
            if reset_node is not None:
                reset_default_categories = agent_default_bind_keys(reset_node)
        # Thread the system SETTINGS file as the cascade's system tier so the
        # honest cleared-message can name the now-effective value + source tier
        # (item 1). A system-scope regular settings key was removed FROM ssp, so
        # ssp is the tier the post-reset snapshot must read.
        msg = reset_config_value(
            key, config_path=cf, env_path=env_sys, system_settings_path=ssp,
            command_scope=ConfigLevel.system,
            cascade_system_path=ssp,
            agents_root=std.agents,
            default_categories=reset_default_categories,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    # show (no args)
    if action == ConfigAction.show:
        show_config(
            global_config_path=cf,
            config_path=cf,
            # The system env file IS this level's own env tier: env_project is
            # the "this level's overrides" slot (shown by the plain view too).
            env_project=env_sys,
            effective=args.effective,
            system_settings_path=ssp,
        )
        return 0

    # get
    if action == ConfigAction.get:
        if not is_known_key(key):
            # Residuals item 4: a STRUCTURAL file-only key (system.setup_completed,
            # system.channels.*) is not in the settable known-key set, so the plain
            # is_known_key gate rejected it as "unknown config key" — while `set`
            # gives the truthful structural refusal (naming the config file). Make
            # get's message MATCH set's truth for these keys instead of pretending
            # they do not exist.
            from kanibako.config_interface import (
                _is_system_path_key,
                _system_key_refusal,
            )
            if _is_system_path_key(key):
                print(_system_key_refusal(key), file=sys.stderr)
                return 1
            print(f"Error: unknown config key: {key}", file=sys.stderr)
            return 1
        val = get_config_value(
            key, global_config_path=cf, env_global=env_sys,
            system_settings_path=ssp,
            agents_root=std.agents,
        )
        if val is None:
            print(f"{key}: (not set)")
        else:
            print(f"{key}={val}")
        return 0

    # set
    if action == ConfigAction.set:
        # Ensure the system settings dir exists for SETTINGS writes.
        ssp.parent.mkdir(parents=True, exist_ok=True)

        # item-0 (per-node DESCRIPTOR bind repoint): a system-scope
        # ``agent.<node>.bindings.{ro,rw}.<name>`` set SOURCE-ONLY repoints the
        # descriptor delivery bind (claude launcher/share) on the node's OWN settings
        # file. The write target is ``agents/<node>/settings.yaml`` (NOT the
        # kanibako_config.yaml CONFIG file), the SAME file the per-persona agent keys
        # write to; and the DESCRIPTOR floor registry (detect-free, per-node) is
        # threaded as ``default_categories`` so the must-exist gate sees the
        # launch-only descriptor floor. The node is resolved by HARNESS with NO
        # detect(), so this validates even for an uninstalled agent (Fork 3).
        set_config_path = cf
        set_default_categories = None
        set_cascade_agent_path = None
        set_cascade_agent_name = ""
        bind_parse = _parse_agent_node_bind_key(key)
        if bind_parse is not None:
            node_raw, _cat, _name = bind_parse
            try:
                node = canonicalize_agent_ref(node_raw)
            except ConfigError:
                node = None
            if node is not None:
                node_file = agent_settings_path(std.agents, node)
                node_file.parent.mkdir(parents=True, exist_ok=True)
                set_config_path = node_file
                set_default_categories = agent_default_bind_keys(node)
                set_cascade_agent_path = node_file
                set_cascade_agent_name = node

        # Full launch cascade for a CATEGORY set's set-time E3 probe (Jei (b),
        # 2026-06-29): the system is the command scope. A system-scope category set
        # writes to the CONFIG file (cf — see set_config_value's category branch),
        # so cf goes in the system slot for sibling @-refs; the resolved system.*
        # config tier is folded in as the FLOOR regardless.
        msg = set_config_value(
            key, value, config_path=set_config_path, env_path=env_sys,
            is_system=True,
            system_settings_path=ssp,
            cascade_system_path=cf,
            cascade_agent_path=set_cascade_agent_path,
            cascade_agent_name=set_cascade_agent_name,
            command_scope=ConfigLevel.system,
            agents_root=std.agents,
            default_categories=set_default_categories,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    return 0

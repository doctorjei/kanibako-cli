"""kanibako system: global configuration, self-update, and system info."""

from __future__ import annotations

import argparse
import sys

from kanibako import __version__
from kanibako.commands.flags import add_null_flag
from kanibako.settings.config import config_file_path, load_config
from kanibako.settings.paths import xdg


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
            "  system set system.env.EDITOR=nano  set a global env var\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_p.add_argument("key_value", nargs="?", help="key=value pair")
    add_null_flag(set_p, undo="system reset <key>")
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

    # system defaults
    defaults_p = sys_sub.add_parser(
        "defaults",
        help="List every default kanibako ships, with its scope and source",
        description=(
            "List the values kanibako declares out of the box.\n\n"
            "Install-wide and STATIC — it takes no box and resolves nothing, so it\n"
            "answers what 'show --effective' cannot: which of the values in force\n"
            "are kanibako's own defaults, and which file declares each one.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    defaults_p.set_defaults(func=run_defaults)

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
        from pathlib import Path

        from kanibako.settings.config import bootstrap_config_paths
        from kanibako.settings.paths import resolve_system_paths
        data_home = xdg("XDG_DATA_HOME", ".local/share")
        # ⚑ ``bootstrap_config_paths``, not ``load_config(cf).config_paths``: the raw
        # capture also carries whatever ``system:`` table the file happens to hold, and
        # the Layer-1 file may not supply settings (Jei, 2026-08-26).
        data_path = resolve_system_paths(
            bootstrap_config_paths(cf), data_home=data_home, home=Path.home(),
        )["config.data"]
        print(f"Data:      {data_path}")
    else:
        print(
            "Config:    (not initialized — run 'kanibako setup' or just 'kanibako start')"
        )

    # Container runtime
    try:
        import subprocess

        from kanibako.runtime.container import ContainerRuntime

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


def run_defaults(args: argparse.Namespace) -> int:
    """``system defaults`` — the shipped defaults, each beside the artefact declaring it.

    Deliberately NOT routed through ``_run_system_config``: that engine reads and writes
    the user's own files, and this verb reads neither. It consults only PACKAGED data
    (the keyspace manifest, ``core-defaults.yaml``, each installed plugin's defaults
    file), so it works with no config, no project and no box — which is exactly when a
    user needs to know what the defaults are.
    """
    from kanibako.settings.defaults_inventory import print_defaults

    print_defaults(sys.stdout)
    return 0


def _run_system_config(args: argparse.Namespace) -> int:
    """Shared global-config engine dispatch.

    The SYSTEM scope keeps STRUCTURAL CONFIG (the ``system.*`` path-tier
    family) in the ``~/.config/kanibako_config.yaml`` CONFIG file (``cf``) and
    routes SETTINGS (``system.agent``, the ``system.auth.*`` chain,
    agent settings, downward scope defaults) to ``@config.settings`` =
    ``global/settings.yaml`` (``ssp``), via the ``system_settings_path`` arg —
    the same file the launch cascade's system tier reads (F2/F3).

    ⚑ There is NO system-tier ENV FILE any more. ``@config.data/env`` was the
    docker ``.env`` the launch layered as its system tier; R-39 retired the bare
    ``env.<VAR>`` spelling that wrote it and Jei's RQ-1 re-ruling retired the
    launch READ, so no verb threads it. The system env family is the settings key
    ``system.env.<VAR>``, stored in ``ssp`` like every other system setting.
    """
    from kanibako.settings.paths import load_std_paths

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)
    # The system SETTINGS file (separate from the kanibako_config.yaml CONFIG file).
    std = load_std_paths(load_config(cf))
    ssp = std.settings

    from kanibako.settings.config_keys import (
        ConfigLevel,
        bare_env_retired_error,
        scope_read_key_error,
    )
    from kanibako.settings.config_interface import (
        ConfigAction,
        get_config_value,
        parse_config_arg,
        reset_all,
        reset_config_value,
        set_config_value,
        show_config,
    )

    key_value = getattr(args, "key_value", None)
    action, key, value = parse_config_arg(
        key_value, set_null=getattr(args, "null", False),
    )

    # --reset --all
    if args.reset and getattr(args, "all_keys", False):
        msg = reset_all(
            config_path=cf, force=args.force,
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
        # ⚑ NO per-node bind floor registry is threaded any more. It existed so a
        # reset of ``agent.<node>.bindings.{ro,rw}.<name>`` could name the descriptor
        # FLOOR it reverted to; R-9 retired that reset route (the engine refuses the
        # key by name), so there is nothing left for the registry to describe.
        # Thread the system SETTINGS file as the cascade's system tier so the
        # honest cleared-message can name the now-effective value + source tier
        # (item 1). A system-scope regular settings key was removed FROM ssp, so
        # ssp is the tier the post-reset snapshot must read.
        msg = reset_config_value(
            key, config_path=cf, system_settings_path=ssp,
            command_scope=ConfigLevel.system,
            cascade_system_path=ssp,
            agents_root=std.agents,
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
            effective=args.effective,
            system_settings_path=ssp,
        )
        return 0

    # get
    if action == ConfigAction.get:
        # Bare env.* — RETIRED (R-39): refused with the cure BEFORE the closed-keyspace
        # gate, for the reason ``scope_read_key_error`` states — a generic "not a key"
        # must not overwrite a specific cure.
        _env_err = bare_env_retired_error(
            key, verb="read", command_scope=ConfigLevel.system,
        )
        if _env_err is not None:
            print(_env_err, file=sys.stderr)
            return 1
        # ⚑ THE CLOSED-KEYSPACE READ GATE (spec §0) — the SAME function ``box get`` and
        # ``workset get`` call, so the three nouns cannot drift into three answers for one
        # key.  Without it a declared name this scope cannot serve was read anyway, found
        # nothing, and printed "(not set)" at rc 0 — the fabricated answer §0 forbids in
        # the same breath as the write.
        # ⚑⚑ IT IS THE ONLY VOCABULARY GATE ON THIS READ, and that is the point.  An
        # ``is_known_key`` PRE-GATE stood here until 2026-08-28 printing "unknown config
        # key" — a SECOND vocabulary, disagreeing with this one on SEVEN DECLARED KEYS
        # (the six bind-shaped category terminals
        # ``system.{bindings.ro,bindings.rw,caches,common,seeded,synced}`` and
        # ``system.masks``), which it refused while ``get_config_value`` reads every one
        # of them and its two sibling nouns already served them.  ``is_known_key`` answers
        # "is this key-SHAPED, as opposed to a project name" — the question ``box``'s
        # POSITIONAL PARSER asks, which is where it still lives; §0 asks "is this a
        # DECLARED key", and only ``key_validity`` (reached here through
        # ``scope_key_reason``) answers that.
        # ⚑ NO ``active_agent``: a bare table leaf here means the ANY-AGENT tier, so naming
        # one agent in the cure would answer for a tier the user did not ask about.
        _key_err = scope_read_key_error(key, ConfigLevel.system)
        if _key_err is not None:
            print(_key_err, file=sys.stderr)
            return 1
        val = get_config_value(
            key, global_config_path=cf,
            system_settings_path=ssp,
            agents_root=std.agents,
            # ⚑ The scope is threaded even though every other handler threads it
            # for a REASON this one does not have: ``get`` consumes command_scope
            # only for the box-scope redirect, so at the system scope it changes
            # nothing today (pinned per key family in
            # tests/test_settings/test_config_dest_parity.py). It is passed because
            # the destination rule now takes the scope explicitly, and a handler
            # that withholds it leaves the rule inferring the scope from a path
            # being non-None -- which is exactly how get and set drifted apart.
            command_scope=ConfigLevel.system,
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

        # ⚑ THE PER-NODE BIND ROUTING BLOCK IS GONE (R-9). A system-scope
        # ``agent.<node>.bindings.{ro,rw}.<name>`` set used to be re-aimed at the
        # node's OWN ``agents/<node>/agent.yaml`` (mkdir included) and handed the
        # detect-free descriptor floor registry so the must-exist gate would pass.
        # That write route is retired: the engine refuses the key BY NAME in its
        # preamble, before any destination is resolved. Removing the block here is
        # part of the retirement, not an oversight — leaving it would have mkdir'd an
        # ``agents/<node>/`` directory for a write that is then refused.
        #
        # Full launch cascade for a CATEGORY set's set-time E3 probe (Jei (b),
        # 2026-06-29): the system is the command scope, whose settings file is ssp —
        # the SAME file the launch cascade's system tier reads (start.py) and the same
        # file this command's RESET handler threads. It passed cf (the CONFIG file)
        # here while a system category set wrote to cf, so the must-exist probe agreed
        # with neither the launch nor reset; both halves now name ssp.
        msg = set_config_value(
            key, value, config_path=cf,
            is_system=True,
            system_settings_path=ssp,
            cascade_system_path=ssp,
            command_scope=ConfigLevel.system,
            agents_root=std.agents,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    return 0

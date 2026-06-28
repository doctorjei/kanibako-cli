"""Full argparse tree with subparsers, dispatcher, and main() entry point."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from kanibako import __version__
from kanibako.errors import KanibakoError, UserCancelled


class _Formatter(argparse.RawDescriptionHelpFormatter):
    """Wider action column so subcommand help text stays on one line."""

    def __init__(self, prog: str, **kwargs: Any) -> None:
        kwargs.setdefault("max_help_position", 30)
        super().__init__(prog, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kanibako",
        description="Safe, persistent workspaces for AI coding agents.",
        epilog=(
            "COMMANDS\n"
            "    rig         Manage box rigs (images)\n"
            "    box         Project lifecycle commands for boxes (containers)\n"
            "    agent       Agent management, authentication, and settings\n"
            "    workset     Project grouping\n"
            "    system      Global configuration, upgrades, and system information\n"
            "\n"
            "SHORTCUTS (equivalent to 'box <command>'):\n"
            "    create      Create a new project box\n"
            "    list        List active and/or inactive boxes\n"
            "    ps          List active (running) boxes\n"
            "    rm          Remove a box\n"
            "\n"
            "    start       Start a box session (default)\n"
            "    stop        Stop a running box session\n"
            "    shell       Open a shell in a box\n"
            "\n"
            "common switches (for 'start' command):\n"
            "  -N, --new           start a new conversation\n"
            "  -C, --continue      continue the most recent conversation (default)\n"
            "  -R, --resume        resume with conversation picker\n"
            "  -A, --autonomous    run with full permissions (default)\n"
            "  -S, --secure        run without --dangerously-skip-permissions\n"
            "  -M, --model MODEL   override the agent model for this run\n"
            "  -v, --verbose       show debug output (target detection, container cmd)\n"
            "\n"
            "run 'kanibako COMMAND --help' for subcommand-specific options"
        ),
        formatter_class=_Formatter,
        add_help=False,
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # Import and register all subcommand parsers.
    from kanibako.commands.start import (
        add_shell_parser,
        add_start_parser,
    )
    from kanibako.commands.image import add_parser as add_rig_parser
    from kanibako.commands.box import add_parser as add_box_parser
    from kanibako.commands.box._parser import run_create, run_list as run_list_fn, run_ps, run_rm
    from kanibako.commands.stop import add_parser as add_stop_parser
    from kanibako.commands.workset_cmd import add_parser as add_workset_parser
    from kanibako.commands.agent_cmd import add_parser as add_agent_parser
    from kanibako.commands.system_cmd import add_parser as add_system_parser
    from kanibako.commands.baseline_cmd import add_parser as add_baseline_parser

    # Setup wizard (before management commands, works pre-init).
    from kanibako.commands.setup_cmd import add_arguments as add_setup_arguments
    from kanibako.commands.setup_cmd import run_setup
    setup_p = subparsers.add_parser("setup", help="Run the setup wizard")
    add_setup_arguments(setup_p)
    setup_p.set_defaults(func=run_setup)

    # Top-level aliases (start, shell, stop already have their own parsers).
    add_start_parser(subparsers)
    add_shell_parser(subparsers)
    add_stop_parser(subparsers)

    # list — top-level shortcut for box list
    list_p = subparsers.add_parser("list", help="List active and/or inactive boxes")
    list_p.add_argument(
        "--active", action="store_true",
        help="Show only active (running) boxes",
    )
    list_p.add_argument(
        "--all", "-a", action="store_true", dest="show_all",
        help="Include orphaned boxes",
    )
    list_p.add_argument(
        "-q", "--quiet", action="store_true",
        help="Output box names only, one per line",
    )
    list_p.set_defaults(func=run_list_fn)

    # ps — top-level shortcut for box list --active
    ps_p = subparsers.add_parser("ps", help="List active (running) boxes")
    ps_p.add_argument(
        "--all", "-a", action="store_true", dest="show_all",
        help="Show all boxes (active and inactive)",
    )
    ps_p.add_argument(
        "-q", "--quiet", action="store_true",
        help="Output box names only, one per line",
    )
    ps_p.set_defaults(func=run_ps)

    # create — top-level alias for box create
    create_p = subparsers.add_parser("create", help="Create a new project")
    create_p.add_argument(
        "path", nargs="?", default=None,
        help="Project directory (default: cwd). Created if it doesn't exist.",
    )
    create_p.add_argument(
        "--standalone", action="store_true",
        help="Use standalone mode (all state inside the project directory)",
    )
    create_p.add_argument(
        "--name", default=None,
        help="Project name override (default: auto-assigned from directory name)",
    )
    create_p.add_argument(
        "-i", "--image", default=None,
        help="Container image to use for this project",
    )
    create_p.add_argument(
        "--no-vault", action="store_true",
        help="Disable vault directories",
    )
    create_p.add_argument(
        "--distinct-auth", action="store_true",
        help="Use distinct credentials (no sync from host)",
    )
    create_p.add_argument(
        "--allow-home", action="store_true",
        help="Permit a standalone project rooted at $HOME (mounts your entire "
             "home directory; required to create one there)",
    )
    create_p.set_defaults(func=run_create)

    # rm — top-level alias for box rm
    rm_p = subparsers.add_parser("rm", help="Remove a project")
    rm_p.add_argument("target", help="Project name or workspace path to remove")
    rm_p.add_argument(
        "--purge", action="store_true",
        help="Also delete kanibako metadata for this project",
    )
    rm_p.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompt (only relevant with --purge)",
    )
    rm_p.set_defaults(func=run_rm)

    # Management commands.
    add_rig_parser(subparsers)
    add_box_parser(subparsers)
    add_workset_parser(subparsers)
    add_agent_parser(subparsers)
    add_system_parser(subparsers)
    add_baseline_parser(subparsers)

    # Blanket --agent / --box flags (W1 Phase D): add them to every leaf
    # subcommand AFTER the whole tree is built, so they PARSE everywhere.
    # Relevance is enforced post-parse in main() via check_flag_relevance.
    from kanibako.commands.flags import inject_blanket_flags
    inject_blanket_flags(parser)

    return parser


_SUBCOMMANDS = {
    # Top-level aliases (delegate to box subcommands).
    "start", "stop", "shell", "ps", "list", "create", "rm",
    # Management commands.
    "box", "rig", "workset", "agent", "system", "baseline",
    # Setup wizard.
    "setup",
}


def _normalize_command(effective: list[str]) -> list[str]:
    """Reorder argv so a leading global-style flag doesn't swallow a subcommand.

    The dispatcher's fallback rule ``effective[0] not in _SUBCOMMANDS -> prepend
    "start"`` treats a LEADING flag (e.g. ``--agent goose shell``) as a bare
    ``start`` with that flag — turning ``--agent goose shell`` into ``start
    shell`` with ``project="shell"``, which both launches the wrong thing and
    fires the Gate-1 setup nudge.

    To honour the blanket-flag design (``kanibako --agent X <subcommand>`` ==
    ``kanibako <subcommand> --agent X``), when ``effective[0]`` is an option
    (starts with ``-``) and a KNOWN subcommand appears later, move the FIRST such
    subcommand token to the front, preserving the relative order of everything
    else.  ``--agent goose shell`` -> ``["shell", "--agent", "goose"]``.

    Heuristic (kept simple, matches the documented design where flags normally
    follow the subcommand): scan for the first token that is a ``_SUBCOMMANDS``
    member.  A flag VALUE that happens to equal a subcommand name (e.g.
    ``--box shell start`` where a box is literally named "shell") would be
    matched as the subcommand; this is an accepted edge case.

    The genuinely-no-subcommand cases are left untouched here so the caller's
    existing ``prepend "start"`` rule still handles them: ``kanibako myproject``
    (bare positional) and ``kanibako -A`` / ``kanibako -N`` (leading flags, no
    subcommand) both fall through unchanged.
    """
    if not effective or not effective[0].startswith("-"):
        return effective
    sub_idx = next(
        (i for i, tok in enumerate(effective) if tok in _SUBCOMMANDS),
        None,
    )
    if sub_idx is None:
        return effective
    sub = effective[sub_idx]
    return [sub] + effective[:sub_idx] + effective[sub_idx + 1:]


def _ensure_initialized() -> None:
    """Ensure kanibako is initialized (create config + data dirs on first run)."""
    from kanibako.config import (
        KanibakoConfig,
        config_file_path,
        write_global_config,
    )
    from pathlib import Path

    from kanibako.paths import resolve_system_paths, xdg

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)

    if cf.exists():
        return  # Already initialized

    # First run: create config and data dirs
    config = KanibakoConfig()
    write_global_config(cf, config)

    # Create data directories
    data_home = xdg("XDG_DATA_HOME", ".local/share")
    sys_paths = resolve_system_paths(
        config.system_paths, data_home=data_home, home=Path.home(),
    )
    data_path = sys_paths["system.data"]
    (data_path / "containers").mkdir(parents=True, exist_ok=True)
    sys_paths["system._boxes"].mkdir(parents=True, exist_ok=True)

    # Channels type-root skeleton (per-workset mailbox/share partitions + chat
    # logs are guarantee-created on the launch path; see commands/install.py).
    channels_dir = sys_paths["system.channelroot"]
    (channels_dir / "commons").mkdir(parents=True, exist_ok=True)
    (channels_dir / "share").mkdir(parents=True, exist_ok=True)
    (channels_dir / "mailboxes").mkdir(parents=True, exist_ok=True)
    chat_dir = channels_dir / "chat"
    chat_dir.mkdir(parents=True, exist_ok=True)
    (chat_dir / "general.md").touch(exist_ok=True)
    (chat_dir / "broadcast.md").touch(exist_ok=True)

    # Create agents directory and generate default per-agent settings files.
    # Each agent's settings live INSIDE its store dir as
    # agents/<agent>/settings.yaml (the per-agent store dir is created on
    # demand by write_agent_config).
    from kanibako.agent_config import (
        AgentConfig,
        agent_settings_path,
        write_agent_config,
    )
    from kanibako.targets import discover_targets

    agents_path = sys_paths["system.agents"]
    agents_path.mkdir(parents=True, exist_ok=True)

    general_toml = agent_settings_path(agents_path, "general")
    if not general_toml.exists():
        write_agent_config(general_toml, AgentConfig(name="Shell"))

    target_names = list(discover_targets())
    for target_name, cls in discover_targets().items():
        target_toml = agent_settings_path(agents_path, target_name)
        if not target_toml.exists():
            write_agent_config(target_toml, cls().generate_agent_config())

    # Curated base + per-agent template content (Phase 9c).  The host
    # agent-config IMPORT was removed in 1.6.0, so a box gets its base guidance
    # (~/INSTRUCTIONS.md) and per-agent config (claude .claude.json onboarding
    # stub + settings.json, goose config.yaml, codex config.toml) from these
    # CURATED templates instead.  The content ships as static package data and
    # is COPIED here into the runtime template dirs (@system.base_template +
    # @system.agents/<agent>/template), create-if-absent so user edits survive
    # an upgrade.  The layered seed-once stage+seed
    # (templates.stage_and_seed_templates) then copies them into each new box
    # home at creation.
    from kanibako.paths import load_std_paths
    from kanibako.templates import install_packaged_templates

    install_packaged_templates(load_std_paths(config), target_names)

    # Seed default global environment variables (don't overwrite existing).
    from kanibako.shellenv import read_env_file, write_env_file

    global_env_path = data_path / "env"
    global_env = read_env_file(global_env_path)
    for key, value in {"COLORTERM": "truecolor"}.items():
        global_env.setdefault(key, value)
    write_env_file(global_env_path, global_env)

    # Try shell completion
    try:
        from kanibako.commands.install import _install_completion

        _install_completion()
    except Exception:
        pass


def _setup_nudge(args: argparse.Namespace) -> None:
    """Gate-1 NON-BLOCKING setup-completion nudge (§Design 3/4).

    Fires only for the agent-requiring commands (those in
    :data:`~kanibako.commands.flags.AGENT_FLAG_COMMANDS` — ``start``,
    ``box start``, ``agent reauth``), i.e. the ones that run the unified agent
    resolver.  ``shell`` and ``setup`` itself, plus pure config/list commands,
    are intentionally excluded.

    If the ``system.setup_completed`` marker is absent or stale, prints an
    advisory to stderr and RETURNS — the command then proceeds to normal agent
    resolution.  This never blocks (a fresh user with exactly one installed
    agent still launches), and any failure reading the marker is swallowed (the
    nudge must never break a command).
    """
    from kanibako.commands.flags import AGENT_FLAG_COMMANDS, command_key

    if command_key(args) not in AGENT_FLAG_COMMANDS:
        return
    try:
        from kanibako.config import config_file_path, setup_nudge_message
        from kanibako.paths import xdg

        cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        message = setup_nudge_message(cf)
    except Exception:  # pragma: no cover - defensive; never break a command
        return
    if message:
        print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()

    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    effective = list(argv if argv is not None else sys.argv[1:])

    # Extract -v/--verbose before subcommand dispatch.
    verbose = "-v" in effective or "--verbose" in effective
    effective = [a for a in effective if a not in ("-v", "--verbose")]

    from kanibako.log import setup_logging
    setup_logging(verbose=verbose)

    # Handle top-level --help and --version before argparse dispatch
    # (kept off the parser so they don't appear in tab-completion).
    if effective and effective[0] in ("-h", "--help"):
        parser.print_help()
        sys.exit(0)
    elif effective and effective[0] == "--version":
        print(f"kanibako {__version__}")
        sys.exit(0)
    else:
        # Reorder a leading global-style flag ahead of a later subcommand so
        # `kanibako --agent goose shell` dispatches as `shell` (not `start` with
        # project="shell").  Must run BEFORE the prepend-"start" fallback and the
        # `--` split below.
        effective = _normalize_command(effective)

        # If the first arg isn't a known subcommand, default to "start".
        if not effective or effective[0] not in _SUBCOMMANDS:
            effective = ["start"] + effective

        # For start/shell, split args at '--' so flags after the project
        # positional still work (REMAINDER would otherwise swallow them).
        # Everything before '--' goes to argparse; everything after becomes
        # args passed to the agent/shell.
        post_dash: list[str] | None = None
        if (
            len(effective) >= 2
            and effective[0] in ("start", "shell")
            and "--" in effective[1:]
        ):
            idx = effective.index("--", 1)
            post_dash = effective[idx + 1:]
            effective = effective[:idx]

        args = parser.parse_args(effective)

        # Relevance check for the blanket --agent/--box flags: they parse on
        # every command, but passing one to an UNRELATED command is an error
        # (not a silent no-op).
        from kanibako.commands.flags import FlagRelevanceError, check_flag_relevance
        try:
            check_flag_relevance(args)
        except FlagRelevanceError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)

        # Gate 1 (§Design 3/4): a NON-BLOCKING setup-completion nudge for the
        # agent-requiring commands (those that run the unified resolver).  Prints
        # to stderr and CONTINUES — never blocks, so a fresh user with exactly
        # one installed agent still launches (just with a nudge).
        _setup_nudge(args)

        if args.command == "start":
            args.agent_args = post_dash or []
        elif args.command == "shell":
            args.shell_args = post_dash or []

        # Lazy init: create config + data dirs on first run.
        # Skip for agent (config-facing) and setup, and for the runtime
        # box subcommands helper/fork (which run inside containers).
        skip_init = args.command in ("agent", "setup") or (
            args.command == "box"
            and getattr(args, "box_command", None) in ("helper", "fork")
        )
        if not skip_init:
            _ensure_initialized()

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        sys.exit(0)

    try:
        rc = func(args)
    except UserCancelled:
        print("Aborted.")
        rc = 2
    except KanibakoError as e:
        print(f"Error: {e}", file=sys.stderr)
        rc = 1
    except KeyboardInterrupt:
        print()
        rc = 130

    sys.exit(rc)

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
        description="Run AI coding agents in rootless containers with per-project isolation.",
        epilog=(
            "top-level aliases (delegate to 'box' subcommands):\n"
            "  start [project]     start a container session (default command)\n"
            "  stop [project]      stop a running container\n"
            "  shell [project]     open a shell in a container\n"
            "  ps                  list running containers\n"
            "  create [path]       create a new project\n"
            "  rm <project>        remove a project\n"
            "\n"
            "management commands:\n"
            "  box                 project lifecycle (create, list, start, stop, ...)\n"
            "  image               container image management\n"
            "  workset             project grouping\n"
            "  agent               agent operations (reauth, helper, fork)\n"
            "  system              global config + self-update\n"
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
    from kanibako.commands.image import add_parser as add_image_parser
    from kanibako.commands.box import add_parser as add_box_parser
    from kanibako.commands.box._parser import run_create, run_ps, run_rm
    from kanibako.commands.stop import add_parser as add_stop_parser
    from kanibako.commands.workset_cmd import add_parser as add_workset_parser
    from kanibako.commands.agent_cmd import add_parser as add_agent_parser
    from kanibako.commands.system_cmd import add_parser as add_system_parser

    # Top-level aliases (start, shell, stop already have their own parsers).
    add_start_parser(subparsers)
    add_shell_parser(subparsers)
    add_stop_parser(subparsers)

    # ps — top-level alias for box ps
    ps_p = subparsers.add_parser("ps", help="List running containers")
    ps_p.add_argument(
        "--all", "-a", action="store_true", dest="show_all",
        help="Include stopped containers",
    )
    ps_p.add_argument(
        "-q", "--quiet", action="store_true",
        help="Output container names only, one per line",
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
    add_image_parser(subparsers)
    add_box_parser(subparsers)
    add_workset_parser(subparsers)
    add_agent_parser(subparsers)
    add_system_parser(subparsers)

    return parser


_COMMAND_ALIASES: dict[str, str] = {
    "crab": "agent",
    "rig": "image",
    "container": "box",
}

_SUBCOMMANDS = {
    # Top-level aliases (delegate to box subcommands).
    "start", "stop", "shell", "ps", "create", "rm",
    # Management commands.
    "box", "image", "workset", "agent", "system",
    # Command aliases (#62).
    "crab", "rig", "container",
}


def _ensure_initialized() -> None:
    """Ensure kanibako is initialized (create config + data dirs on first run)."""
    from kanibako.config import (
        KanibakoConfig,
        config_file_path,
        write_global_config,
    )
    from kanibako.paths import xdg

    config_home = xdg("XDG_CONFIG_HOME", ".config")
    cf = config_file_path(config_home)

    if cf.exists():
        return  # Already initialized

    # First run: create config and data dirs
    config = KanibakoConfig()
    write_global_config(cf, config)

    # Create data directories
    data_home = xdg("XDG_DATA_HOME", ".local/share")
    data_path = data_home / (config.paths_data_path or "kanibako")
    (data_path / "containers").mkdir(parents=True, exist_ok=True)
    (data_path / "boxes").mkdir(parents=True, exist_ok=True)

    templates_dir = data_path / (config.paths_templates or "templates")
    (templates_dir / "general" / "base").mkdir(parents=True, exist_ok=True)
    (templates_dir / "general" / "standard").mkdir(parents=True, exist_ok=True)

    comms_dir = data_path / (config.paths_comms or "comms")
    (comms_dir / "mailbox").mkdir(parents=True, exist_ok=True)
    (comms_dir / "broadcast.log").touch(exist_ok=True)

    # Create agents directory and generate default agent TOMLs.
    from kanibako.agents import AgentConfig, write_agent_config
    from kanibako.targets import discover_targets

    agents_path = data_path / (config.paths_agents or "agents")
    agents_path.mkdir(parents=True, exist_ok=True)

    general_toml = agents_path / "general.toml"
    if not general_toml.exists():
        write_agent_config(general_toml, AgentConfig(name="Shell"))

    for target_name, cls in discover_targets().items():
        target_toml = agents_path / f"{target_name}.toml"
        if not target_toml.exists():
            agent_cfg = cls().generate_agent_config()
            write_agent_config(target_toml, agent_cfg)
        else:
            agent_cfg = AgentConfig()
        (templates_dir / target_name / agent_cfg.shell).mkdir(
            parents=True, exist_ok=True,
        )

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
        # If the first arg isn't a known subcommand, default to "start".
        if not effective or effective[0] not in _SUBCOMMANDS:
            effective = ["start"] + effective
        # Translate command aliases (e.g. crab→agent).
        if effective and effective[0] in _COMMAND_ALIASES:
            effective[0] = _COMMAND_ALIASES[effective[0]]
        args = parser.parse_args(effective)

        # Lazy init: create config + data dirs on first run.
        # Skip for agent (helper/fork run inside containers).
        if args.command not in ("agent",):
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

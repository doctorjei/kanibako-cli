"""Full argparse tree with subparsers, dispatcher, and main() entry point."""

from __future__ import annotations

import argparse
import sys

from kanibako import __version__
from kanibako.errors import KanibakoError, UserCancelled


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kanibako",
        description="Run Claude Code in rootless containers with per-project isolation.",
        epilog=(
            "common switches (for default 'start' command):\n"
            "  -p, --project DIR   use DIR as the project directory (default: cwd)\n"
            "  -i, --image IMAGE   use IMAGE as the container image for this run\n"
            "  -N, --new           start a new conversation (skip default --continue)\n"
            "  -S, --safe          run without --dangerously-skip-permissions\n"
            "  -c, --command CMD   use CMD as the container entrypoint\n"
            "  -v, --verbose       show debug output (target detection, container cmd)\n"
            "\n"
            "run 'kanibako COMMAND --help' for subcommand-specific options"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # Import and register all subcommand parsers.
    from kanibako.commands.start import (
        add_resume_parser,
        add_shell_parser,
        add_start_parser,
    )
    from kanibako.commands.config_cmd import add_parser as add_config_parser
    from kanibako.commands.image import add_parser as add_image_parser
    from kanibako.commands.box import add_parser as add_box_parser
    from kanibako.commands.install import add_parser as add_setup_parser
    from kanibako.commands.remove import add_parser as add_remove_parser
    from kanibako.commands.stop import add_parser as add_stop_parser
    from kanibako.commands.upgrade import add_parser as add_upgrade_parser
    from kanibako.commands.refresh_credentials import (
        add_parser as add_reauth_parser,
    )
    from kanibako.commands.init import add_init_parser, add_new_parser
    from kanibako.commands.workset_cmd import add_parser as add_workset_parser
    from kanibako.commands.status import add_parser as add_status_parser
    from kanibako.commands.vault_cmd import add_parser as add_vault_parser
    from kanibako.commands.env_cmd import add_parser as add_env_parser
    from kanibako.commands.shared_cmd import add_parser as add_shared_parser
    from kanibako.commands.helper_cmd import add_parser as add_helper_parser

    add_start_parser(subparsers)
    add_shell_parser(subparsers)
    add_resume_parser(subparsers)
    add_stop_parser(subparsers)
    add_config_parser(subparsers)
    add_image_parser(subparsers)
    add_box_parser(subparsers)
    add_workset_parser(subparsers)
    add_setup_parser(subparsers)
    add_remove_parser(subparsers)
    add_upgrade_parser(subparsers)
    add_reauth_parser(subparsers)
    add_status_parser(subparsers)
    add_init_parser(subparsers)
    add_new_parser(subparsers)
    add_vault_parser(subparsers)
    add_env_parser(subparsers)
    add_shared_parser(subparsers)
    add_helper_parser(subparsers)

    return parser


_SUBCOMMANDS = {
    "start", "shell", "resume", "stop", "config", "image",
    "box", "workset", "setup", "remove", "upgrade", "reauth",
    "status", "init", "new", "vault", "env", "shared", "helper",
}


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
        args = parser.parse_args(effective)

        if args.command not in ("setup", "helper"):
            from kanibako.paths import xdg
            from kanibako.config import config_file_path
            _cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
            if not _cf.exists():
                print(
                    f"kanibako is not set up yet ({_cf} not found).\n"
                    f"Run 'kanibako setup' first.",
                    file=sys.stderr,
                )
                sys.exit(1)

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

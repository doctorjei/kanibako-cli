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

    # ``parser_class`` (B-5): every subcommand — and, since add_subparsers
    # defaults the class to its own parser's type, every NESTED subcommand —
    # accepts its flags in ANY position, including between two positionals.
    # Inert for parsers that argparse already interleaves correctly; see
    # kanibako.commands.flags.hoist_optionals.
    from kanibako.commands.flags import OptionsAnywhereParser

    subparsers = parser.add_subparsers(
        dest="command", metavar="COMMAND", parser_class=OptionsAnywhereParser,
    )

    # Import and register all subcommand parsers.
    from kanibako.commands.start import (
        add_shell_parser,
        add_start_parser,
    )
    from kanibako.commands.code_cmd import add_code_parser
    from kanibako.commands.image import add_parser as add_rig_parser
    from kanibako.commands.box import add_parser as add_box_parser
    from kanibako.commands.box._parser import (
        run_create,
        run_list as run_list_fn,
        run_ps,
        run_register,
        run_rm,
    )
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
    add_code_parser(subparsers)

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
        "--allow-home", action="store_true",
        help="Permit a standalone project rooted at $HOME (mounts your entire "
             "home directory; required to create one there)",
    )
    create_p.add_argument(
        "--private", action="store_true",
        help="Create a PRIVATE box: disable global and workset credential "
             "sharing so the host's OAuth token is never seeded into it.",
    )
    create_p.add_argument(
        "--force", action="store_true",
        help="Create even if --name is already used by a workset (the box "
             "shadows that workset in bare-name resolution)",
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

    # register — top-level alias for box register
    register_p = subparsers.add_parser(
        "register",
        help="Re-register a deregistered box, or register a standalone box on disk",
    )
    register_p.add_argument(
        "target",
        help="Deregistered box name, or path to a standalone box on disk",
    )
    register_p.add_argument(
        "--force", action="store_true",
        help="Re-register even if the name is used by a workset",
    )
    register_p.set_defaults(func=run_register)

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
    "start", "stop", "shell", "code", "ps", "list", "create", "rm", "register",
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
    from kanibako.settings.config import (
        KanibakoConfig,
        config_file_path,
        write_global_config,
    )
    from pathlib import Path

    from kanibako.settings.paths import resolve_system_paths, xdg

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
        config.config_paths, data_home=data_home, home=Path.home(),
    )
    data_path = sys_paths["config.data"]
    (data_path / "containers").mkdir(parents=True, exist_ok=True)
    sys_paths["system._boxes"].mkdir(parents=True, exist_ok=True)

    # NOTE (block #3a, JC-3): the channel type-root skeleton is NO LONGER
    # pre-created here.  ``channelroot`` moved to Layer 2 (a ``system.*`` settings
    # key), and the launch path already creates the full skeleton — the L7
    # guarantee-create for the type-root bind sources + ``_seed_channel_files``
    # for the chat logs (start.py).  No host-side pre-launch consumer of the
    # skeleton exists (audit: every reader is on the box-launch path), so the
    # setup/init pre-creation was redundant and is dropped.

    # Create agents directory and generate default per-agent settings files.
    # Each agent's settings live INSIDE its store dir as
    # agents/<agent>/settings.yaml (the per-agent store dir is created on
    # demand by write_agent_config).
    from kanibako.settings.agent_config import (
        AgentConfig,
        agent_settings_path,
        write_agent_config,
    )
    from kanibako.targets import discover_targets

    agents_path = sys_paths["config.agents"]
    agents_path.mkdir(parents=True, exist_ok=True)

    general_toml = agent_settings_path(agents_path, "general")
    if not general_toml.exists():
        write_agent_config(general_toml, AgentConfig(name="Shell"))

    target_names = list(discover_targets())
    for target_name, cls in discover_targets().items():
        target_toml = agent_settings_path(agents_path, target_name)
        if not target_toml.exists():
            write_agent_config(target_toml, cls().generate_agent_config())

    # Packaged content → the host stores.  The content ships as static package data
    # and is installed here into its ENUMERATED destinations (@system.template's box
    # + workset moulds, @system.canon/handbook, and every agent store under
    # @config.agents), create-if-absent so user edits survive an upgrade.  The
    # layered seed-once apply (the three ``seeded.template`` keystore keys, staged by
    # ``commands.start._apply_init_seeds`` via ``templates.stage_layers``) then copies
    # the box HOME moulds into each new box store at creation; the box handbook chapter
    # is a SEPARATE host-side copy (``templates.install_box_handbook_template``) and is
    # not a ``seeded`` entry.
    #
    # ⚑ THIS IS THE LAZY BACKSTOP of J-6's agent-store A-action (the "two paths, one
    # action" pair), and it runs the SAME full per-file mould stamp the deliberate
    # SETUP trigger does — ``install_packaged_templates`` calls
    # ``ensure_agent_stores``, which is the one implementation.  The bare per-agent
    # mkdir this used to be is gone.
    #
    # ⚑ It fires on FIRST RUN ONLY (this whole function returns early once the config
    # file exists), and since R-38 retired the template-staleness stamp NOTHING
    # detects packaged-content drift on an already-initialized host automatically.
    # A template change that RIDES A RELEASE is announced by the setup bands
    # (``SETUP_FCV`` nudge / ``SETUP_BCV`` hard block in ``setup_compat_gate``); a
    # plugin pip-installed LATER at the SAME kanibako version is the ruled ACCEPTED
    # LOSS — its store materialises at the next ``kanibako setup``, the deliberate
    # trigger.  Verified 2026-08-02: ``install_packaged_templates`` has exactly two
    # callers, this first-run backstop and ``setup_cmd._run_template_refresh``.
    # Recorded as migrations M-18 (superseded in part) and M-23.
    from kanibako.settings.paths import load_std_paths
    from kanibako.launch.templates import install_packaged_templates

    std_paths = load_std_paths(config)
    install_packaged_templates(std_paths, target_names)

    # Seed the default box environment (don't overwrite existing).
    #
    # ``COLORTERM=truecolor`` is declared at BOX scope — the scope that actually
    # describes it (it is a property of the terminal a box runs, not of the host
    # install) — and written DOWNWARD into the system SETTINGS file, which the
    # directional rule allows (a system file may set keys of the scopes it
    # contains). ``settings_launch._emit_scope_node`` delivers it as a box-scope
    # ``env`` category entry, so a box's own ``box.env.COLORTERM`` overrides it.
    #
    # ⚑ This is still a WRITTEN VALUE, not a default. The proper fix — no write at
    # all, and a declared box-scope DEFAULT that populates with nothing stored —
    # is tracked as MBR-2 and is deliberately NOT done here (it is HELD pending a
    # decision on where defaults live).
    #
    # setdefault semantics: first run only (this function returns early once the
    # config file exists) AND create-if-absent, so a user value is never clobbered.
    from kanibako.settings.config_io import read_stored_leaf, write_nested_key

    if read_stored_leaf(std_paths.settings, ("box", "env"), "COLORTERM") is None:
        std_paths.settings.parent.mkdir(parents=True, exist_ok=True)
        write_nested_key(
            std_paths.settings, ("box", "env"), "COLORTERM", "truecolor",
        )

    # Try shell completion
    try:
        from kanibako.commands.install import _install_completion

        _install_completion()
    except Exception:
        pass


def _setup_nudge(args: argparse.Namespace) -> None:
    """Gate-1 setup/config compatibility gate (§Design 3/4; 5-band).

    Fires only for the agent-requiring commands (those in
    :data:`~kanibako.commands.flags.AGENT_FLAG_COMMANDS` — ``start``,
    ``box start``, ``agent reauth``), i.e. the ones that run the unified agent
    resolver.  ``shell`` and ``setup`` itself, plus pure config/list commands,
    are intentionally excluded.

    Delegates the band logic to :func:`~kanibako.settings.config.setup_compat_gate`:

    * NUDGE bands (absent/stale marker) → print the advisory to stderr and RETURN
      (non-blocking — the command then proceeds to normal agent resolution).
    * SILENT-BUMP / no-op bands → nothing printed, RETURN.
    * ERROR bands (config from a newer build, or too old to auto-fill) → the gate
      raises :class:`~kanibako.errors.ConfigError` (a
      :class:`~kanibako.errors.KanibakoError`), which PROPAGATES so the CLI
      converts it to the standard clean rc1.

    A deliberate :class:`ConfigError` (``KanibakoError``) band error is allowed
    through; any other UNEXPECTED failure (a marker-read bug, missing file, a
    failed silent-bump write) is swallowed so the gate never breaks a command.
    """
    from kanibako.commands.flags import AGENT_FLAG_COMMANDS, command_key

    if command_key(args) not in AGENT_FLAG_COMMANDS:
        return

    try:
        from kanibako.settings.config import config_file_path, setup_compat_gate
        from kanibako.settings.paths import xdg

        cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        # ⚑ The separate HARD template-staleness gate that used to run here is
        # RETIRED (R-38): packaged-template drift is now announced by the bands
        # above — a content change bumps ``SETUP_FCV`` (nudge), a structural one
        # ``SETUP_BCV`` (hard block).  ``setup_compat_gate`` is the ONE gate.
        message = setup_compat_gate(cf)
    except KanibakoError:
        # Deliberate ERROR band — propagate so the CLI surfaces rc1.
        raise
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

        # Gate 1 (§Design 3/4): the 5-band setup/config compatibility gate for
        # the agent-requiring commands (those that run the unified resolver).
        # NUDGE bands print to stderr and CONTINUE (never block); the two ERROR
        # bands raise ConfigError (a KanibakoError), which we convert to the same
        # clean rc1 every other KanibakoError path produces (mirrors the func()
        # handler below — this call is OUTSIDE that try block).
        try:
            _setup_nudge(args)
        except KanibakoError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

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

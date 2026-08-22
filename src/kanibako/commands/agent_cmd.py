"""kanibako agent: agent configuration, authentication, and settings."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from kanibako.agent_ref import canonicalize_agent_ref, display_agent_ref
from kanibako.commands.flags import add_null_flag

if TYPE_CHECKING:
    from pathlib import Path

    from kanibako.settings.agent_config import AgentConfig
    from kanibako.settings.paths import StandardPaths


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "agent",
        help="Agent management, authentication, and settings",
        description="Manage agent configurations, authentication, and settings.",
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

    # agent set <agent> <key>=<value>
    set_p = agent_sub.add_parser(
        "set",
        help="Set an agent configuration value",
        description=(
            "Set an agent setting (key=value).\n\n"
            "  agent set myagent model=sonnet     set 'model'\n"
            "  agent set myagent env.FOO=bar      set env var FOO\n"
            "  agent set myagent secret_path.TOKEN=~/.config/claude/foo/token\n"
            "                                     TOKEN from a host secret file\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_p.add_argument("agent_id", help="Agent identifier")
    set_p.add_argument("key_value", nargs="?", help="key=value pair")
    add_null_flag(set_p, undo="agent reset <agent> <key>")
    set_p.set_defaults(func=run_set)

    # agent reset <agent> <key> | --all  [--force]
    reset_p = agent_sub.add_parser(
        "reset",
        help="Reset (remove) an agent configuration override",
        description=(
            "Remove an agent override, reverting to the default.\n\n"
            "  agent reset myagent model          reset one key\n"
            "  agent reset myagent --all          reset all overrides\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reset_p.add_argument("agent_id", help="Agent identifier")
    reset_p.add_argument("key", nargs="?", default=None, help="Config key to reset")
    reset_p.add_argument(
        "--all", action="store_true", dest="all_keys",
        help="Reset all overrides",
    )
    reset_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts",
    )
    reset_p.set_defaults(func=run_reset)

    # agent get <agent> <key>
    get_p = agent_sub.add_parser(
        "get",
        help="Get an agent configuration value",
        description="Read one agent setting.",
    )
    get_p.add_argument("agent_id", help="Agent identifier")
    get_p.add_argument("key", help="Config key to read")
    get_p.set_defaults(func=run_get)

    # agent show <agent> [--effective]
    show_p = agent_sub.add_parser(
        "show",
        help="Show agent configuration",
        description=(
            "Show agent settings.\n\n"
            "  agent show myagent                 show all settings\n"
            "  agent show myagent --effective     show resolved values\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    show_p.add_argument("agent_id", help="Agent identifier")
    show_p.add_argument(
        "--effective", action="store_true",
        help="Show resolved values including defaults",
    )
    show_p.set_defaults(func=run_show)

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

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list, quiet=False)


# ---------------------------------------------------------------------------
# Agent list / info / config + reauth handlers
# ---------------------------------------------------------------------------


def _load_std() -> StandardPaths:
    """Load config and return the resolved standard paths."""
    from kanibako.settings.config import config_file_path, load_config
    from kanibako.settings.paths import xdg, load_std_paths

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    return load_std_paths(config)


def run_list(args: argparse.Namespace) -> int:
    """List configured agents."""
    from kanibako.settings.agent_file import load

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    adir = std.agents
    if not adir.is_dir():
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print("No agents configured.")
        return 0

    # Each agent's settings live inside its store dir: agents/<agent>/agent.yaml.
    settings_files = sorted(
        p for p in adir.glob("*/agent.yaml") if p.is_file()
    )
    if not settings_files:
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print("No agents configured.")
        return 0

    quiet = getattr(args, "quiet", False)
    if quiet:
        for f in settings_files:
            # The on-disk dir name is the canonical NODE (``℘``); show ``+`` to the user.
            print(display_agent_ref(f.parent.name))
        return 0

    print(f"{'NAME':<20} {'MODEL'}")
    for f in settings_files:
        cfg = load(f)
        name = display_agent_ref(f.parent.name)
        model = cfg.state.get("model", "-")
        print(f"{name:<20} {model}")
    return 0


def run_info(args: argparse.Namespace) -> int:
    """Show agent configuration details."""
    from kanibako.settings.agent_config import agent_settings_path
    from kanibako.settings.agent_file import load

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # The positional may be a persona ref with the typable ``+``; canonicalise to
    # the ``℘`` node-name to locate the on-disk dir, but DISPLAY the ``+`` form.
    agent_id = canonicalize_agent_ref(args.agent_id)
    agent_display = display_agent_ref(agent_id)
    path = agent_settings_path(std.agents, agent_id)
    if not path.exists():
        print(
            f"Error: agent '{agent_display}' not found ({path})", file=sys.stderr
        )
        return 1

    cfg = load(path)
    print(f"Name:         {cfg.name or agent_display}")
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

    # SECRET category POINTERS (VAR -> host path). The token file contents (the
    # secret) are never read here — only the path is shown.
    if cfg.secret_path:
        print("Secret paths:")
        for k, v in sorted(cfg.secret_path.items()):
            print(f"  {k} = {v}")

    return 0


def run_set(args: argparse.Namespace) -> int:
    """``agent set <agent> <key>=<value>``."""
    args.reset = None
    args.all_keys = False
    args.effective = False
    args.force = False
    return _run_agent_config(args)


def run_reset(args: argparse.Namespace) -> int:
    """``agent reset <agent> <key>`` / ``agent reset <agent> --all``."""
    key = getattr(args, "key", None)
    all_keys = getattr(args, "all_keys", False)
    if not all_keys and not key:
        print("Error: reset requires a key (or --all)", file=sys.stderr)
        return 1
    # The shared body uses ``reset`` as a presence sentinel and reads the key
    # from ``reset`` (or, with the const fallback, from ``key_value``).
    args.reset = key if key else "__RESET__"
    args.key_value = key
    args.effective = False
    return _run_agent_config(args)


def run_get(args: argparse.Namespace) -> int:
    """``agent get <agent> <key>``."""
    args.key_value = args.key
    args.reset = None
    args.all_keys = False
    args.effective = False
    args.force = False
    return _run_agent_config(args)


def run_show(args: argparse.Namespace) -> int:
    """``agent show <agent> [--effective]``."""
    args.key_value = None
    args.reset = None
    args.all_keys = False
    args.force = False
    return _run_agent_config(args)


def _run_agent_config(args: argparse.Namespace) -> int:
    """Shared agent-config engine dispatch.

    Maps config keys to agent config sections:
      model, continue_mode, etc. -> state keys
      env.X                   -> [env]
      shell, run_args, name   -> identity keys
    """
    from kanibako.settings.agent_config import agent_settings_path
    from kanibako.settings.agent_file import (
        clear_overrides,
        load,
        read_leaf,
        remove_leaf,
        slot_for,
        write_leaf,
    )
    from kanibako.settings.config_keys import (
        access_value_error,
        agent_read_key_error,
        is_access_key,
    )

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Canonicalise the (possibly ``+``) persona ref to the ``℘`` node to locate the
    # on-disk dir; DISPLAY the ``+`` form.
    agent_id = canonicalize_agent_ref(args.agent_id)
    agent_display = display_agent_ref(agent_id)
    path = agent_settings_path(std.agents, agent_id)
    if not path.exists():
        print(
            f"Error: agent '{agent_display}' not found ({path})", file=sys.stderr
        )
        return 1

    key_value = getattr(args, "key_value", None)

    # Handle --reset
    if args.reset is not None:
        if args.all_keys:
            if not args.force:
                from kanibako.utils import confirm_prompt
                from kanibako.errors import UserCancelled

                try:
                    confirm_prompt(
                        "Remove all config overrides? Type 'yes' to proceed: "
                    )
                except UserCancelled:
                    print("Aborted.")
                    return 0
            # ⚑ THROUGH THE BOUNDARY, not by hand on the raw document. This was the
            # sixth site that spelled the per-agent file's shape — a read-modify-write
            # on the root table, in a command module. It PRESERVES ``name`` and reports
            # a COUNT of the overrides actually removed, in the SAME wording the other
            # scopes' ``reset_all`` (config_interface.py) prints; both facts now live
            # with the shape, in :func:`agent_file.clear_overrides`.
            count = clear_overrides(path)
            print(
                f"Reset {count} override(s)." if count else "No overrides to reset."
            )
            return 0

        # Key can come from --reset VALUE or from positional key_value.
        reset_key = args.reset if args.reset != "__RESET__" else key_value
        if not reset_key:
            print("Error: reset requires a key (or --all)", file=sys.stderr)
            return 1

        key = reset_key.strip()
        # ⚑ THE SAME GATE ``set`` TAKES, and the symmetry is the point (spec §0): a
        # reset is a WRITE, so "No override for …" on a spelling that is not a key —
        # or on one whose write route is RETIRED — is a lie in both directions.  This
        # is the order ``config_interface.reset_config_value`` uses.
        gate_err = _agent_key_gate(agent_id, key, path=path, verb="reset")
        if gate_err is not None:
            print(gate_err, file=sys.stderr)
            return 1
        changed = remove_leaf(slot_for(std.agents, agent_id, key))
        if changed:
            # Honest cleared-form (F7), same contract as every other noun's
            # reset. This engine edits the sparse settings file directly, NOT
            # the assemble/merge cascade, so it has no resolved cascade inputs
            # to thread — the cleared-only fallback (effective=None) is the
            # correct honest form here (no new plumbing for this micro-block).
            from kanibako.settings.config_keys import ConfigLevel
            from kanibako.settings.config_interface import _honest_reset_message

            print(_honest_reset_message(key, ConfigLevel.agent))
        else:
            print(f"No override for {key}")
        return 0

    # ``--null`` at AGENT scope — REFUSED, honestly and by name.
    #
    # ⚑ Handled BEFORE the ``=`` split and the get fallback, because a bare key
    # with no ``=`` falls through to GET: the flag PARSED (it is on this parser)
    # and the command then READ a value and printed it, exit 0, writing nothing.
    # An accepted-and-ignored flag is the worse failure — the user is told the
    # write happened by the absence of any error.
    #
    # ⚑ Why REFUSE rather than write the null. This file's reader coerces every
    # value it loads: ``agent_file.load`` builds ``cfg.state`` / ``cfg.env``
    # with ``str(v)``, so a YAML ``null`` here comes back as the TEXT ``"None"``
    # — not a suppression, a four-character string. For ``access`` that string
    # is not a legal tier at all, so a flag whose whole promise is "suppress
    # this" would leave the box REFUSING to launch (and, before R-41 made the
    # resolver exact, it would have run PERMISSIVE instead). The other three scopes route
    # through ``set_config_value``, which owns the closed-keyspace check and the
    # per-route null refusals (the retired bare ``env.<VAR>``; every bind-shaped
    # CATEGORY, whose write route DS-BL1 = (a) retired outright — so ``--null`` on one
    # gets that refusal, not a null-mechanism one); this verb has its own writer and
    # none of that, so writing here
    # would also put two disagreeing spellings of one idea in the tree. Agent-file
    # null semantics need the READER to change with them — a separate change that
    # owns launch consumption, not a CLI polish.
    if getattr(args, "null", False):
        if key_value is None:
            print("Error: --null requires a key", file=sys.stderr)
            return 1
        # ``partition`` so a mistaken ``--null key=value`` still names the KEY in
        # the cure rather than echoing the whole token back.
        null_key = key_value.partition("=")[0].strip()
        print(
            f"Error: --null is not supported at agent scope. To clear the "
            f"agent's OWN value use 'agent reset {agent_display} {null_key}'; "
            f"to suppress what this agent declares, request it from a box or "
            f"workset with '--null pref.agent.{agent_id}.{null_key}' (spec §2h).",
            file=sys.stderr,
        )
        return 1

    # Parse key/value argument
    if key_value is None:
        # Show mode — read the config only where the READ paths need it.
        cfg = load(path)
        return _show_agent_config(cfg, agent_display, effective=args.effective)

    if "=" in key_value:
        key, _, value = key_value.partition("=")
        key = key.strip()
        value = value.strip()
        # Write-time validation for the auth-critical ``access`` permission key
        # (parity with the ``config set`` path in config_interface.py; R-41
        # respelled the key and the guard followed it). This sibling ``agent set``
        # verb writes the SAME keyspace slot but through a different setter, so
        # without this guard a typo (``access=fll``) would land verbatim as the
        # string ``"fll"`` and be re-read at LAUNCH. Reject an off-enum value NOW
        # through the SAME validator (``access_value_error``) the ``config set``
        # path uses — one message, one truth table; only ``access`` is guarded,
        # never ``model`` / ``allow_helpers`` / ``run_args`` / ``env.*`` /
        # ``secret_path.*``.
        if is_access_key(key):
            access_err = access_value_error(key, value)
            if access_err is not None:
                print(access_err, file=sys.stderr)
                return 1
        gate_err = _agent_key_gate(agent_id, key, path=path, verb="set")
        if gate_err is not None:
            print(gate_err, file=sys.stderr)
            return 1
        # run_args is stored as a LIST (space-split); everything else is the
        # raw string. Sparse write — only the touched key is materialized.
        stored: object = value.split() if key == "run_args" else value
        write_leaf(slot_for(std.agents, agent_id, key), stored)
        print(f"Set {key}={value}")
        return 0

    # Get mode
    key = key_value.strip()
    read_err = agent_read_key_error(agent_id, key)
    if read_err is not None:
        print(read_err, file=sys.stderr)
        return 1
    val = _get_agent_key(load(path), key)
    if val is None:
        # ⚑ D-6: THE RECORD FIRST, THE FILE SECOND.  ``AgentConfig`` models a
        # SUBSET of what the file may hold — no field answers for the CATEGORY
        # tables — so a record-only read printed "(not set)" over values the file
        # carries, including ones ``agent set`` had just written.  The fallback
        # goes through the SAME boundary slot ``config get agent.<node>.<tail>``
        # uses, so the two verbs render ONE string
        # (``config_io.render_stored_scalar``) rather than two renderings of one
        # value.  ⚑ A declared key MUST be readable (spec §0); the record's shape
        # is not a reason for a key to have no answer.
        val = read_leaf(slot_for(std.agents, agent_id, key))
    if val is not None:
        print(val)
    else:
        print("(not set)", file=sys.stderr)
    return 0


def _agent_key_gate(
    agent_id: str, key: str, *, path: "Path", verb: str
) -> str | None:
    """The WRITE gate for ``agent set`` / ``agent reset``: refusal text, or ``None``.

    ⚑ THE ORDER MIRRORS ``set_config_value``'s PREAMBLE, and each step is there because the next
    one would say the wrong thing about it:

    1. a RETIRED bind-shaped route is refused BY NAME with its own cure — never degraded to "not
       a declared key" (spec §0).  It covers all five bind-shaped categories, from the same
       derived recogniser the other verbs use, so there is no bindings-only rule to widen later;
    2. the CLOSED KEYSPACE (D-5) — this verb had no vocabulary at all and stored whatever it was
       handed, including ``self.model`` (ruling 55);
    3. the VALUE SHAPE (D-7) — a declared key whose value is a TABLE takes no scalar.
    """
    from kanibako.settings.agent_file import table_value_error
    from kanibako.settings.config_keys import (
        agent_node_bind_retired_error,
        agent_write_key_error,
    )

    retired = agent_node_bind_retired_error(f"agent.{agent_id}.{key}", verb=verb)
    if retired is not None:
        return retired
    key_err = agent_write_key_error(agent_id, key, verb=verb)
    if key_err is not None:
        return key_err
    return table_value_error(key, path=path, verb=verb)


def _get_agent_key(cfg: AgentConfig, key: str) -> str | None:
    """Read a single key from agent config."""
    # secret_path.<VAR> — the SECRET category POINTER (host path). Checked before the
    # ``env.`` prefix. Returns the stored PATH, never the (secret) file contents.
    if key.startswith("secret_path."):
        var = key[len("secret_path."):]
        return cfg.secret_path.get(var)
    if key.startswith("env."):
        env_name = key[4:]
        return cfg.env.get(env_name)
    if key == "name":
        return cfg.name or None
    if key == "run_args":
        return " ".join(cfg.run_args) if cfg.run_args else None
    # Everything else goes to state
    return cfg.state.get(key)


# ⚑ ``_agent_key_route`` IS GONE (S1) and its absence is deliberate. It was a thin
# delegation to the file-shape route, which is now reached the way every other caller
# reaches it: ``agent_file.slot_for(...)`` then read/write/remove through the slot. A
# second name for one hop is a second place for the rule to be described.


def _show_agent_config(
    cfg: AgentConfig, agent_id: str, *, effective: bool = False,
) -> int:
    """Display agent config."""
    has_output = False

    # Identity keys
    print(f"  name = {cfg.name or agent_id}")
    if cfg.run_args:
        print(f"  run_args = {cfg.run_args}")
    has_output = True

    # agent-state keys
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

    # secret_path POINTERS (VAR -> host path). Only the PATH is shown; the token
    # file contents (the secret) are never read here.
    if cfg.secret_path:
        for k, v in sorted(cfg.secret_path.items()):
            print(f"  secret_path.{k} = {v}")
        has_output = True

    if not has_output:
        print("  (no overrides)")

    return 0


def run_reauth(args: argparse.Namespace) -> int:
    """Check authentication and login if needed."""
    from kanibako.agent_ref import harness_of
    from kanibako.settings.agent_select import select_agent
    from kanibako.settings.config import config_file_path, load_config
    from kanibako.targets import resolve_target
    from kanibako.settings.paths import xdg, load_std_paths

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    # Resolve project to check auth mode.  Reconcile the positional subject with
    # the blanket --box flag (same → warn / differ → error), then route through
    # the path-or-name resolver.
    from kanibako.commands.flags import resolve_subject_value
    from kanibako.settings.paths import resolve_box_target
    std = load_std_paths(config)
    subject = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    proj = resolve_box_target(std, config, subject)

    # Resolve the agent UP FRONT through the ONE selection seam (--agent > box
    # pref > workset pref > system.agent → installed-count rule).  reauth is an
    # agent-requiring command, so a resolution failure raises a typed
    # AgentResolutionError that the top-level cli.py handler surfaces verbatim
    # (Gate-2a/2b) with a non-zero exit — never a silent fall-through.
    selection = select_agent(
        std=std, proj=proj,
        explicit_agent=getattr(args, "agent", None),  # Phase D seam (--agent)
    )
    agent_name = selection.node
    # ``agent_name`` is the NODE-name (persona identity); the target/plugin is keyed
    # by the HARNESS. ⚑ Routed through the ONE translator: a SUPPRESSED box
    # (``pref.system.agent: null``) has no node, and handing ``""`` to
    # ``resolve_target`` would AUTO-DETECT an agent and reauth it — credentials for
    # an agent this box deliberately does not run (the same two-vocabulary
    # collision the launch seam guards; bifrost E-NULL).
    target = (
        resolve_target(harness_of(selection.node), proj.project_path)
        if selection.has_agent
        else None
    )
    if target is None:
        print(
            "This box runs no agent (pref.system.agent is null), so there are no "
            "credentials to refresh. Give it an agent with "
            "'kanibako box set pref.system.agent=<name>', or pass '--agent <name>' "
            "to reauth one explicitly.",
            file=sys.stderr,
        )
        return 1

    if not target.has_binary:
        print("No agent target configured.", file=sys.stderr)
        return 1

    # Auth 3-tier SHARING + persona endpoint: resolve BOTH per-box decisions off ONE
    # launch snapshot (single-route — the same pipeline ``start`` uses), for the
    # resolved agent. The auth display below gates on whether the box receives shared creds; the
    # endpoint drives the OAuth-suppress cred fork (block B) so a reauth on a
    # custom-endpoint box never syncs the Anthropic token into a box pointed at a
    # third-party endpoint.
    from kanibako.settings.agent_config import agent_settings_path
    from kanibako.settings.agent_file import load
    from kanibako.commands.start import (
        _persona_values_for,
        _resolve_box_launch_decisions,
    )
    agent_cfg_path = agent_settings_path(std.agents, agent_name)
    reauth_agent_cfg = load(agent_cfg_path) if agent_cfg_path.exists() else None
    auth_src, active_endpoint, _active_model = _resolve_box_launch_decisions(
        std=std,
        proj=proj,
        target=target,
        agent_name=agent_name,
        agent_cfg=reauth_agent_cfg,
        system_settings_path=std.settings,
        agent_cfg_path=agent_cfg_path,
        # The persona store's LIVE tier — the SAME one a launch resolves against.
        # ``suppress_oauth`` below is ``active_endpoint is not None``, so this is
        # the reauth path's half of the cred fork: a persona endpoint that did not
        # reach this resolve would let a reauth sync the host Anthropic token into
        # a box pointed at a third-party endpoint. ``None`` for a bare agent.
        persona_values=_persona_values_for(agent_name, target),
        # ⚑ REQUIRED (P7): reauth must resolve the SAME per-agent credential dir the
        # launch delivers from (``@workset.auth.path/@system.agent``); without the
        # §1A selection level it would collapse to the workset auth ROOT.
        selection_level=selection.selection_level,
    )
    suppress_oauth = active_endpoint is not None

    if not auth_src.creds_shared:
        # Private box: check project's own credentials instead of the source.
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
        # Sync refreshed credentials to the project shell directory.  Mirror the
        # start.py gate exactly: descriptor-bearing targets route their cred
        # refresh through the credsync engine (descriptor.cred_files); only legacy
        # (desc is None) targets fall back to the per-plugin refresh hook.  An
        # ungated target.refresh_credentials here would push a descriptor agent
        # (e.g. goose) down its legacy path / bespoke copy.
        if auth_src.creds_shared:
            from pathlib import Path

            from kanibako.targets import credsync

            desc = target.descriptor
            if desc is not None:
                credsync.refresh_box_credentials(
                    desc, target, auth=auth_src, host_home=Path.home(),
                    project_home=proj.shell_path,
                    suppress_oauth=suppress_oauth,
                )
            else:
                target.refresh_credentials(proj.shell_path)
        print(f"{target.display_name}: authenticated.", file=sys.stderr)
        return 0

    # Auth failed.  If the agent declares an interactive in-box setup command
    # (goose ``configure`` / codex ``login``), run it in the box so the user can
    # configure / log in there (host-side reauth can't do it — the credential
    # lives in box-state), then re-check.  ``_run_container(setup_only=True)``
    # assembles the box, hits the same FIX-2 in-box-setup path, and returns
    # WITHOUT launching a full agent session.  Agents with no setup command
    # (claude by default) fall through to the existing failure message.
    if target.setup_entrypoint is not None:
        from kanibako.commands.start import _run_container
        return _run_container(
            project_dir=subject,
            entrypoint=None,
            image_override=None,
            new_session=False,
            safe_mode=False,
            resume_mode=False,
            extra_args=[],
            persistent=False,
            explicit_agent=getattr(args, "agent", None),
            setup_only=True,
        )

    print(f"{target.display_name}: authentication failed.", file=sys.stderr)
    return 1

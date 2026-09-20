"""kanibako agent: agent configuration, authentication, and settings."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import TYPE_CHECKING

from kanibako.agent_ref import canonicalize_agent_ref, display_agent_ref
from kanibako.commands.flags import add_null_flag

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Mapping

    from kanibako.settings.agent_config import AgentConfig
    from kanibako.settings.paths import StandardPaths

_log = logging.getLogger(__name__)


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


def _config_file() -> Path:
    """The Layer-1 CONFIG file's path — this module's ONE recipe for locating it."""
    from kanibako.settings.config import config_file_path
    from kanibako.settings.paths import xdg

    return config_file_path(xdg("XDG_CONFIG_HOME", ".config"))


def _load_std() -> StandardPaths:
    """Load config and return the resolved standard paths."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    return load_std_paths(load_config(_config_file()))


def _agent_node(raw: str) -> str:
    """The node the ``<agent>`` positional names, canonicalised to the ``℘`` KEY form.

    ⚑⚑ THE ANY-AGENT TIER TOKEN IS NOT A REF, and passes through untouched. This is
    the order ``settings.config_dest.check_agent_node`` already uses and it is the same
    reason: ``default`` ADDRESSES the reserved tier rather than NAMING an agent, while
    ``agent_ref.parse_agent_ref`` is the true-agent grammar and refuses a reserved
    pseudo-agent name (keyspec §2d). Canonicalising it would raise here and replace the
    engine's refusal — which names the CURE, the bare-key spelling — with a bare
    reservation notice. The other reserved name has no tier a verb can address, so it
    takes the ``canonicalize_agent_ref`` road and surfaces as an ordinary ``ConfigError``
    — which ``cli.py`` flattens to one ``Error: …`` line at rc 1, like any other.
    """
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    return raw if raw == AGENT_DEFAULT_SUB else canonicalize_agent_ref(raw)


def run_list(args: argparse.Namespace) -> int:
    """List configured agents."""
    from kanibako.settings.agent_file import load
    from kanibako.settings.config import AGENT_META_FILE

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
        p for p in adir.glob(f"*/{AGENT_META_FILE}") if p.is_file()
    )
    if not settings_files:
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print("No agents configured.")
        return 0

    quiet = getattr(args, "quiet", False)
    if quiet:
        for f in settings_files:
            # ``store_dirname`` already wrote the ``+`` form; printing through the
            # display boundary anyway keeps ONE rule for what a user is shown.
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
    from kanibako.settings.agent_file import load, stored_leaf_display

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # The positional may arrive in either spelling; canonicalise to the ``℘`` node —
    # the form every KEY takes.  ``agent_settings_path`` maps it back to the ``+``
    # store dirname, so nothing here composes a path from the node itself.
    agent_id = _agent_node(args.agent_id)
    agent_display = display_agent_ref(agent_id)
    path = agent_settings_path(std.agents, agent_id)
    if not path.exists():
        print(
            f"Error: agent '{agent_display}' not found ({path})", file=sys.stderr
        )
        return 1

    cfg = load(path)
    # ⚑ THE §2d KEY, RESOLVED — not a field of the file. The line is spelled for the key it
    # prints, so a reader can reach it: `kanibako agent set <agent> label=…`.
    print(f"Label:        {_agent_label(std, agent_id)}")
    if cfg.run_args:
        # ⚑ THE FILE'S OWN JOIN, not a second one: ``agent_file`` owns the argv
        # translation, and a hand-rolled ``' '.join`` here was a second answer to
        # "how does a stored argv list read back" (P10).
        print(f"Default args: {stored_leaf_display('run_args', cfg.run_args)}")
    else:
        print("Default args: (none)")

    # ``label`` is an ordinary declared leaf, so a stored one rides ``cfg.state``
    # like any other — and the line above already carries it, RESOLVED. Listing
    # both prints one key twice, with the stored value second, which reads as two
    # keys of the same name (the same cut ``_show_agent_config`` makes).
    state_rows = {k: v for k, v in cfg.state.items() if k != "label"}
    if state_rows:
        print("State:")
        for k, text in _stored_rows(state_rows):
            print(f"  {k} = {text}")
    else:
        print("State:        (none)")

    if cfg.env:
        print("Env:")
        for k, text in _stored_rows(cfg.env, "env."):
            print(f"  {k} = {text}")
    else:
        print("Env:          (none)")

    # SECRET category POINTERS (VAR -> host path). The token file contents (the
    # secret) are never read here — only the path is shown.
    if cfg.secret_path:
        print("Secret paths:")
        for k, text in _stored_rows(cfg.secret_path, "secret_path."):
            print(f"  {k} = {text}")

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
    """Shared agent-config engine dispatch — the get / set / show / reset bodies.

    Each verb addresses ONE agent's settings file by a bare TAIL (``model``, ``env.<VAR>``,
    ``secret_path.<VAR>``); the canonical key it names is ``agent.<node>.<tail>``.

    ⚑ ``set`` AND ``reset`` ROUTE THEIR WRITES to ``config_interface``'s ``set_config_value`` /
    ``reset_config_value``, the pair every other noun shares — this verb is not a second writer
    of that slot.  ⚑ THERE IS NO EXCEPTION TO THAT ANY MORE: ``name`` was the one tail written
    by hand, because it was not a key and the shared setter had no slot to route it to.  D8b
    retired it (2026-09-15), so every tail this verb accepts is a declared key and takes the one
    route.  ``get`` still addresses the file directly through ``agent_file``'s slot boundary.
    """
    from kanibako.settings.agent_config import agent_settings_path
    from kanibako.settings.agent_file import (
        clear_overrides,
        load,
        read_leaf,
        slot_for,
    )
    from kanibako.settings.config_keys import agent_read_key_error

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Canonicalise the (possibly ``+``) persona ref to the ``℘`` node — the KEY form.
    # ``agent_settings_path`` maps it back to the ``+`` store dirname.
    agent_id = _agent_node(args.agent_id)
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
            # on the root table, in a command module. Nothing in the file is exempt
            # now (MIGRATION § 2.73); it reports a COUNT of the overrides actually
            # removed, in the SAME wording the other scopes' ``reset_all``
            # (config_interface.py) prints. Both facts live with the shape, in
            # :func:`agent_file.clear_overrides`.
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
        # ⚑⚑ THE ONE SETTER'S RESET HALF, AND THERE IS NO LONGER A SECOND ARM BESIDE IT.
        # This verb removed the leaf with its own hand for ``name``; two writers of one
        # keyspace slot is the defect class, and a removal is a WRITE. ``name`` was never a
        # key, so it had no slot to route to — retiring it (D8b) deletes the branch, and
        # every tail this verb accepts now goes through the shared resetter. The threading
        # MIRRORS the set call exactly (system command scope, because the per-node agent
        # store is global and the engine's per-node routes are reachable only there; the
        # NODE named so a per-node route resolves).
        from kanibako.settings.config_interface import reset_config_value
        from kanibako.settings.config_keys import ConfigLevel

        msg = reset_config_value(
            f"agent.{agent_id}.{key}",
            config_path=_config_file(),
            system_settings_path=std.settings,
            cascade_system_path=std.settings,
            cascade_agent_name=agent_id,
            command_scope=ConfigLevel.system,
            agents_root=std.agents,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        # ⚑ The ENGINE'S OWN ANSWER to "did anything change", not a second read of the
        # file: ``reset_config_value`` reports a no-op with exactly this prefix at every
        # one of its branches (pinned by
        # ``TestAgentResetRoutesThroughTheOneSetter.test_the_no_op_prefix_is_the_engines``).
        changed = not msg.startswith("No override for ")
        if changed:
            # Honest cleared-form (F7), same contract as every other noun's reset.
            # ⚑ THE KEY AS THE USER TYPED IT and the AGENT scope, never the canonical
            # ``agent.<node>.<tail>`` at the system scope the engine echoes: this noun takes
            # a BARE tail on the command line and its ``set`` twin answers in that spelling,
            # so the confirmation teaches the form this verb accepts. The cleared-only
            # fallback (effective=None) is the honest form — the agent file is not a cascade
            # tier a reset can resolve through.
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
    # 🛑 THE REFUSAL IS KEPT; BOTH REASONS IT USED TO REST ON ARE GONE, and saying so is
    # the point — an obsolete justification left standing is how a rule gets a NEW one
    # invented for it later.
    #
    # It read: (1) this file's reader coerces with ``str(v)``, so a YAML ``null`` would
    # come back as the TEXT ``"None"`` — for ``access``, not a legal tier at all, so a
    # flag promising "suppress this" would leave the box REFUSING to launch; and (2) this
    # verb has its own writer, so writing here would put two disagreeing spellings of one
    # idea in the tree. Neither holds now. ``agent_file.load`` KEEPS a present-``None`` in
    # ``cfg.state``, ``cfg.env`` and ``cfg.secret_path`` alike, and the ``=`` arm below
    # routes through ``set_config_value`` — the one setter, with the closed-keyspace check
    # and the per-route null refusals (the retired bare ``env.<VAR>``; every bind-shaped
    # CATEGORY, whose write route DS-BL1 = (a) retired outright, so ``--null`` on one gets
    # THAT refusal, not a null-mechanism one).
    #
    # ⚑ WHETHER AGENT SCOPE SHOULD NOW ACCEPT ``--null`` IS A PRODUCT QUESTION, not a
    # leftover to tidy: it asks what an agent writing a present-``None`` at its OWN level
    # means to the launch, which is the consumer's side of §2h and nobody's to settle in a
    # rendering pass. Until it is settled the message below is the honest answer: the
    # ``agent reset`` cure it names is measured working, and the pref cure is §2h's own.
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
        # ⚑ RESOLVED HERE, where ``std`` is in scope; the formatter stays a formatter.
        return _show_agent_config(
            cfg, _agent_label(std, agent_id), effective=args.effective,
        )

    if "=" in key_value:
        key, _, value = key_value.partition("=")
        key = key.strip()
        value = value.strip()
        # ⚑ THE NOUN'S OWN §0 GATE, ahead of the shared setter and NOT a duplicate of it: it
        # judges the tail against the KNOWN-GOOD node (the canonical ``℘`` node), which is the one
        # thing ``set_config_value`` cannot do — that engine reads the node OUT of the key, so
        # ``self.model`` parses as a node ``claude.self`` and ruling 55's spelling would be
        # refused for the wrong reason, blaming the agent name rather than naming the key.
        gate_err = _agent_key_gate(agent_id, key, path=path, verb="set")
        if gate_err is not None:
            print(gate_err, file=sys.stderr)
            return 1
        # ⚑⚑ THE ONE SETTER, AND THERE IS NO LONGER A SECOND ARM BESIDE IT. This verb had its
        # OWN writer straight to ``write_leaf``, so none of the set-time validation ran:
        # measured, ``agent set claude canon=@bogus.ref`` stored the dangling reference at rc 0
        # while the same value through ``system set`` was refused by name. Two writers of one
        # keyspace slot is the defect — so the checks are NOT copied here; the write is routed
        # to where they already live (the E3 resolution probe, the typed-scalar check and the
        # auth-critical ``access`` enum guard among them). ``name`` was the last tail still
        # written by hand, because it was not a key and the shared setter had nothing to route
        # it to; retiring it (D8b) leaves ONE route for everything this verb accepts.
        # ⚑ The command scope is SYSTEM because the per-node agent store is global (under
        # ``config.agents``) and the engine's per-node routes are reachable only there —
        # see ``config_dest._agent_node_route``. The threading otherwise mirrors
        # ``system set``'s, with ONE datum that verb does not have (P7): the NODE being
        # written, which anchors ``@meta.agent.<node>.path`` in the set-time snapshot. A
        # legal value spelled against the agent's own store root dangles without it.
        from kanibako.settings.config_interface import set_config_value
        from kanibako.settings.config_keys import ConfigLevel

        msg = set_config_value(
            f"agent.{agent_id}.{key}", value,
            config_path=_config_file(),
            system_settings_path=std.settings,
            cascade_system_path=std.settings,
            cascade_agent_name=agent_id,
            command_scope=ConfigLevel.system,
            agents_root=std.agents,
        )
        if msg.startswith("Error:"):
            print(msg, file=sys.stderr)
            return 1
        # ⚑ THE KEY AS THE USER TYPED IT, never the canonical ``agent.<node>.<tail>`` the
        # setter echoes: this noun takes a BARE tail on the command line, and its ``reset``
        # twin already answers in that spelling (``_honest_reset_message(key, …)``). A
        # confirmation is a lesson, and the form it teaches must be the form this verb accepts.
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
        # uses.  ⚑ A declared key MUST be readable (spec §0); the record's shape
        # is not a reason for a key to have no answer.
        # ⚑⚑ THE TWO ARMS SPELL A VALUE THE SAME WAY BECAUSE BOTH RENDER, NOT
        # BECAUSE ONLY ONE DOES.  Routing the fallback through the shared renderer
        # was never enough on its own: every key the record MODELS is answered
        # above and never reaches here, so while that arm handed its value back
        # raw this verb had two vocabularies — ``agent get <node> label`` over a
        # stored ``""`` printed a blank line where ``system get`` printed ``""``.
        # ``_get_agent_key`` renders too now, through the same pair.
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


def _declared_label(agent_id: str) -> str:
    """The §2d FLOOR for *agent_id*'s ``label``: its plugin's declaration, else core's backstop.

    ⚑ THE SAME TWO SOURCES THE LAUNCH FLOORS ON, IN THE SAME ORDER — ``start.py`` builds
    ``{**core_defaults.behavior_defaults(), **{d.key: d.default for d in descriptors}}``, so a
    plugin's declared value wins the core backstop and the merged floor then lands at
    ``agent.default.<key>`` for a core-declared leaf (``settings_launch``'s OS1 rule; ``label``
    is one).  Reading it the same way here is what stops ``agent info`` and the box from
    disagreeing about an agent's description.

    ⚑ A PLUGIN THAT CANNOT BE READ IS CONCEDED, NOT FATAL — the same treatment
    ``settings_prefs.default_valid_agents`` gives a raising ``setting_descriptors()``: a broken
    or absent plugin costs the user a description, never the command.  ``no_agent`` declares no
    ``label`` at all and correctly reads the core backstop (the spec's ``agent.shell.label`` has
    no node to live on yet — D2).
    """
    from kanibako.agent_ref import harness_of
    from kanibako.settings import core_defaults
    from kanibako.targets import get_target

    try:
        descriptors = get_target(harness_of(agent_id))().setting_descriptors()
    except Exception:  # pragma: no cover - a plugin must not break a display verb
        _log.debug("setting_descriptors() failed for a target", exc_info=True)
        descriptors = []
    for descriptor in descriptors:
        if descriptor.key == "label":
            return str(descriptor.default)
    return core_defaults.behavior_default("label")


def _agent_label(std: "StandardPaths", agent_id: str) -> str:
    """*agent_id*'s human-readable DESCRIPTION — ``agent.<node>.label`` RESOLVED (spec §2d).

    THE ONE RESOLVE BOTH DISPLAY VERBS SHARE.  ``agent info`` and ``agent show`` used to print
    the agent file's ``name`` field, which was not a key; ``label`` is, so reading it means
    consulting the cascade rather than a field.

    ⚑⚑ THREE DOORS, IN CASCADE ORDER, AND THEY ARE NOT INTERCHANGEABLE:

    1. the agent FILE's own flat ``label`` — read through the file boundary's slot, because
       ``assemble_levels`` carries only this file's CATEGORY tables into the agent rung; its
       flat behaviour scalars reach the launch through ``agent_file.state_level`` instead.
       This is the same SLOT ``agent get <node> label`` reads, but NOT the same read: that
       verb renders and this one must not (below).
    2. ``agent.<node>.label`` through the cascade — a per-agent value in the SYSTEM file.
    3. ``agent.default.label`` through the cascade, FLOORED — the all-agents tier, whose base
       rung is :func:`_declared_label`.

    Steps 2 and 3 are §2d's active-over-default pick, cascade first, exactly as
    ``settings_launch.effective_behavior`` makes it.

    ⚑ THE FLOOR IS PASSED IN, never baked into ``effective_value`` — its other caller (``reset``)
    must name NO built-in default, and that function's docstring says why.

    ⚑ NO WORKSET OR BOX TIER, and that is honest rather than missing: this verb names an agent,
    not a box, so there is no workset or box file to read.  A value set at either scope shows
    where it applies — in ``box show``.

    ALWAYS RETURNS A STRING: ``core-defaults.yaml``'s ``agent_default.label`` is declared, so
    there is always a floor to fall to.  The final ``declared`` return covers only the arms
    where ``effective_value`` declines to name a value at all — an unreadable path tier, or a
    ``label`` explicitly set to the empty string.
    """
    from kanibako.settings.agent_config import agent_settings_path
    from kanibako.settings.agent_file import (
        slot_for,
        stored_leaf_display,
        stored_leaf_value,
    )
    from kanibako.settings.config_interface import effective_value
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    # ⚑ EMPTY IS NOT A VALUE AT THIS DOOR EITHER. Doors 2 and 3 already decline an
    # empty render, and the contract below is that this function always returns
    # something printable; a bare ``is not None`` here would let a stored ``""``
    # through and print a blank ``Label:`` line.
    # ⚑⚑ IT IS THE STORED VALUE THAT IS TESTED, NEVER THE RENDERING, and that is not a
    # style choice: the read verbs spell a stored ``""`` as ``""`` (spec §2h), which is
    # also what a user who stored the two-character label ``""`` gets back, so the two are
    # INDISTINGUISHABLE once rendered and a door comparing text discards one of them. This
    # is the same test ``effective_value`` makes for the same reason, on the same key.
    # ⚑ A present-``None`` answers ``None`` here too, together with absent, and both mean
    # the file names no label — which is exactly the fall-through this door wants.
    stored = stored_leaf_value(slot_for(std.agents, agent_id, "label"))
    if stored is not None and stored != "":
        return stored_leaf_display("label", stored)

    declared = _declared_label(agent_id)
    floor: dict[str, object] = {f"agent.{AGENT_DEFAULT_SUB}.label": declared}
    for sections in (("agent", agent_id), ("agent", AGENT_DEFAULT_SUB)):
        resolved = effective_value(
            ".".join((*sections, "label")), sections, "label",
            agent_name=agent_id,
            system_path=std.settings,
            agent_path=agent_settings_path(std.agents, agent_id),
            workset_path=None,
            box_path=None,
            floor=floor,
        )
        if resolved is not None:
            return resolved[0]
    return declared


def _stored_rows(
    table: "Mapping[str, object]", prefix: str = "",
) -> list[tuple[str, str]]:
    """*table*'s entries as sorted ``(name, rendered text)`` pairs.

    ⚑⚑ THE ONE RENDERING RULE THE NOUN'S DISPLAY DOORS SHARE (P10).  ``info`` and ``show``
    print the same three tables under different headings, and each used to coerce with a
    bare ``f"{v}"`` — so a stored null printed Python's ``None`` at both, a spelling the CLI
    refuses back, while the ``system`` noun's view of the very same file said ``null``.
    ⚑ *prefix* IS THE FILE TAIL'S HEAD (``env.``, ``secret_path.``), not decoration:
    ``stored_leaf_display`` keys its shape rule on the tail, which is what keeps an env var
    NAMED ``run_args`` a scalar.
    """
    from kanibako.settings.agent_file import stored_leaf_display

    return [
        (k, stored_leaf_display(f"{prefix}{k}", v)) for k, v in sorted(table.items())
    ]


def _get_agent_key(cfg: AgentConfig, key: str) -> str | None:
    """The RENDERED value the RECORD holds at *key*, or ``None`` when it holds none.

    ⚑⚑ IT RENDERS, AND ITS CALLER'S FILE FALLBACK IS WHY IT MUST.  That fallback reads the
    same leaf through ``read_leaf`` and so answers in the ``get`` conventions (spec §2h);
    handing the record's value back raw made ONE verb answer in two vocabularies — a stored
    ``""`` printed a blank line where the file route prints ``""``, and a stored null that
    reached the record printed ``None``.
    ⚑ ``None`` STILL MEANS "THE RECORD HAS NO ANSWER", never a rendered one: the record
    models a SUBSET of what the file may hold, and a present-``None`` it does hold is
    indistinguishable from a key it never saw, so both fall through to the file — which can
    tell them apart and is the only reader that can.
    """
    # secret_path.<VAR> — the SECRET category POINTER (host path). Checked before the
    # ``env.`` prefix. Returns the stored PATH, never the (secret) file contents.
    if key.startswith("secret_path."):
        v: object = cfg.secret_path.get(key[len("secret_path."):])
    elif key.startswith("env."):
        v = cfg.env.get(key[4:])
    # ⚑ NO ``name`` ARM, AND ITS ABSENCE IS THE POINT (D8b): this is the agent-FILE read shim,
    # and ``label`` — the key that replaced it — is an ordinary declared leaf that reaches
    # ``cfg.state`` below like every other one. ``get`` reads the STORED tier by contract, so
    # an unset ``label`` answers "(not set)" here while ``info``/``show`` resolve the cascade.
    elif key == "run_args":
        # ⚑ THE FILE'S OWN JOIN (through ``agent_file.stored_leaf_display``), never a
        # second one here: it is the read half of the split ``write_leaf`` applies, and
        # the two must not be able to drift. An empty list falls through to the file read
        # in the caller, which tells a present ``run_args: []`` apart from an absent key.
        v = cfg.run_args or None
    else:
        # Everything else goes to state
        v = cfg.state.get(key)
    if v is None:
        return None
    from kanibako.settings.agent_file import stored_leaf_display

    return stored_leaf_display(key, v)


# ⚑ ``_agent_key_route`` IS GONE (S1) and its absence is deliberate. It was a thin
# delegation to the file-shape route, which is now reached the way every other caller
# reaches it: ``agent_file.slot_for(...)`` then read/write/remove through the slot. A
# second name for one hop is a second place for the rule to be described.


def _show_agent_config(
    cfg: AgentConfig, label: str, *, effective: bool = False,
) -> int:
    """Display agent config.

    ⚑ A PURE FORMATTER, AND *label* IS WHY IT TAKES A STRING.  It receives a FILE object and no
    cascade handle, so the one key this verb resolves (:func:`_agent_label`) is resolved at the
    CALLER and handed in: two reads of one key in one verb is the shape to avoid.
    ⚑ The parameter used to be ``agent_id`` and used to be handed the DISPLAY ref — a name that
    had stopped describing what it received.
    """
    from kanibako.settings.agent_file import stored_leaf_display

    has_output = False

    # The §2d description + ``run_args``, the one launch-invocation value the file
    # models as a field of its own.
    print(f"  label = {label}")
    if cfg.run_args:
        # ⚑ THE COMMAND-LINE SPELLING, not the list's Python repr: this line used to
        # print ``run_args = ['--a', '--b']`` at the user — a shape they cannot type
        # back in.
        print(f"  run_args = {stored_leaf_display('run_args', cfg.run_args)}")
    has_output = True

    # agent-state keys
    # ⚑ ``label`` IS EXCLUDED HERE BECAUSE IT ALREADY HAS ITS LINE. It is an ordinary
    # declared leaf, so a stored one rides ``cfg.state`` like any other — and the line
    # above already carries it, RESOLVED. Listing both prints one key twice, with the
    # stored value second, which reads as two keys of the same name.
    state_rows = {k: v for k, v in cfg.state.items() if k != "label"}
    if state_rows:
        for k, text in _stored_rows(state_rows):
            print(f"  {k} = {text}")
        has_output = True
    elif effective:
        print("  # (no state overrides)")

    # [env] section
    if cfg.env:
        for k, text in _stored_rows(cfg.env, "env."):
            print(f"  env.{k} = {text}")
        has_output = True

    # secret_path POINTERS (VAR -> host path). Only the PATH is shown; the token
    # file contents (the secret) are never read here.
    if cfg.secret_path:
        for k, text in _stored_rows(cfg.secret_path, "secret_path."):
            print(f"  secret_path.{k} = {text}")
        has_output = True

    if not has_output:
        print("  (no overrides)")

    return 0


def run_reauth(args: argparse.Namespace) -> int:
    """Check authentication and login if needed."""
    from kanibako.agent_ref import harness_of
    from kanibako.settings.agent_select import select_agent
    from kanibako.settings.config import load_config
    from kanibako.targets import resolve_target
    from kanibako.settings.paths import load_std_paths

    config = load_config(_config_file())

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
    # pref > workset pref > system.agent).  reauth is an agent-requiring command,
    # so a resolution failure raises a typed AgentResolutionError that the
    # top-level cli.py handler surfaces verbatim with a non-zero exit — never a
    # silent fall-through, and never an implicit pick.
    selection = select_agent(
        std=std, proj=proj,
        explicit_agent=getattr(args, "agent", None),  # Phase D seam (--agent)
    )
    agent_name = selection.node
    # ``agent_name`` is the NODE-name (persona identity); the target/plugin is keyed
    # by the HARNESS. ⚑ Routed through the ONE translator: a NO-AGENT box has no
    # node, and handing ``""`` to ``resolve_target`` would AUTO-DETECT an agent and
    # reauth it — credentials for an agent this box deliberately does not run (the
    # same two-vocabulary collision the launch seam guards; bifrost E-NULL).
    # 🛑 ``pref.system.agent: null`` reaches neither line since the 2026-09-19
    # ruling: ``select_agent`` REFUSES it above, saying no default agent is set, so
    # no production path produces a node-less selection and the ``target is None``
    # arm below is unreachable today.
    # ⚑ KEPT anyway, and NOT as a reservation for ``shell``: keyspec §2b makes the
    # plain-shell box an effective ``@system.agent`` of ``shell``, a NAMED node, so
    # a D2 selection has ``has_agent`` TRUE and resolves a target like any other.
    # The guard stays because the incident is on record (bifrost E-NULL) and the
    # shape costs one conjunct — a floor under the two-vocabulary seam, not a plan.
    target = (
        resolve_target(harness_of(selection.node), proj.project_path)
        if selection.has_agent
        else None
    )
    if target is None:
        print(
            "This box runs no agent, so there are no credentials to refresh. "
            "Give it an agent with 'kanibako box set pref.system.agent=<name>', "
            "or pass '--agent <name>' to reauth one explicitly.",
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

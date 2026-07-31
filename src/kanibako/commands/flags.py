"""Blanket ``--agent`` / ``--box`` flags + per-command relevance (W1 Phase D).

Two cross-cutting CLI flags are added to (nearly) every subcommand via a
post-construction walk of the argparse tree:

* ``--agent NAME`` — the top-precedence, ephemeral (this-invocation-only) input
  to the unified agent resolver (``config.resolve_agent`` cascade).  It feeds
  the C2 resolver seam (``getattr(args, "agent", None)``) so a command resolves
  as if NAME were the configured agent.  Relevant only to agent-touching
  commands.
* ``--box VALUE`` — the SUBJECT selector: which box/project the command acts on
  (path OR registered box name; name precedence), even when not cwd.  Routed
  through :func:`kanibako.paths.resolve_box_target`.  Relevant only to commands
  that act on a subject box/project.

**Parse everywhere, relevance per-command.**  The flags PARSE on every command
(so ``kanibako <cmd> --agent X --box Y`` never argparse-errors on an unknown
flag), but each is only MEANINGFUL for a declared set of commands.  Passing a
flag to an UNRELATED command is a hard error (not a silent no-op), surfaced by
:func:`check_flag_relevance` in the dispatcher.

Relevance is keyed by the command's dotted path (e.g. ``"start"``,
``"agent reauth"``, ``"box convert"``).  ``setup`` is intentionally EXCLUDED
from the blanket ``--agent`` injection: it owns its own ``--agent`` flag with
PERSISTENT (writes ``system.agent``) semantics, distinct from the blanket
flag's ephemeral override — so the two never collide (see Phase B / Phase D
reconcile).
"""

from __future__ import annotations

import argparse
import sys

from kanibako.errors import SubjectConflictError

# ---------------------------------------------------------------------------
# Relevance declarations (dotted command paths).
# ---------------------------------------------------------------------------

# ``--agent`` is the ephemeral agent-resolver override.  Relevant ONLY to the
# commands that run the unified cascade with the explicit-agent seam: the launch
# path (start + its box alias), reauth (top-level + the ``agent`` subcommand),
# and create (top-level + its box alias) — ``run_create`` threads the explicit
# agent to the persona verdict + the home seed, and a persona ref whose
# persona-grata store entry exists drives the initial store import.
# ``shell`` is NOT here — it bypasses agent resolution entirely (the no-agent
# recovery hatch).  ``setup`` is NOT here — it has its own persistent --agent.
AGENT_FLAG_COMMANDS: frozenset[str] = frozenset({
    "start",
    "box start",
    "create",
    "box create",
    "reauth",
    "agent reauth",
})

# ``--box`` is the subject/anchor selector.  Relevant to every command that acts
# on a subject box/project (vs. cwd): the launch/shell paths, the box lifecycle
# + inspection commands, stop, reauth, and ``workset disconnect``.  NOT relevant
# to commands with no single-box subject (list/ps/create, workset/agent/system/
# rig groups, setup).
BOX_FLAG_COMMANDS: frozenset[str] = frozenset({
    # launch / shell
    "start",
    "shell",
    "box start",
    "box shell",
    # stop
    "stop",
    "box stop",
    # box lifecycle / inspection (subject anchor)
    "box convert",
    "box move",
    "box duplicate",
    "box info",
    "box set",
    "box reset",
    "box get",
    "box show",
    "box diagnose",
    "box rm",
    "box register",
    "box remap",
    # auth
    "reauth",
    "agent reauth",
    # workset member
    "workset disconnect",
})

# Commands that already own a local ``--agent`` flag with different semantics
# and must be skipped by the blanket injection.
_AGENT_FLAG_EXCLUDE: frozenset[str] = frozenset({"setup"})


def _add_agent_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent",
        metavar="NAME",
        default=None,
        help=(
            "Use agent NAME for this invocation (ephemeral; top of the "
            "resolution cascade, not persisted)."
        ),
    )


def _add_box_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--box",
        metavar="VALUE",
        default=None,
        help=(
            "Act on box VALUE (a registered box name or a path), even when not "
            "the current directory."
        ),
    )


def _has_option(parser: argparse.ArgumentParser, option: str) -> bool:
    """True if *parser* already defines *option* (e.g. setup's local --agent)."""
    for action in parser._actions:
        if option in action.option_strings:
            return True
    return False


def inject_blanket_flags(parser: argparse.ArgumentParser) -> None:
    """Walk the whole argparse tree and add ``--agent``/``--box`` to leaves.

    A *leaf* is any (sub)parser that does NOT itself hold further subparsers.
    Group parsers that only dispatch to subcommands (``box``, ``agent``,
    ``workset``, ``rig``, ``system``, ``baseline``, ``workset share``) are not
    runnable on their own, so they get nothing.

    Flags are added unconditionally to every leaf so ``--agent``/``--box`` PARSE
    everywhere (relevance is checked post-parse).  Two exceptions:

    * ``setup`` (and any command in :data:`_AGENT_FLAG_EXCLUDE`) keeps its own
      ``--agent``; only the blanket ``--box`` is added.
    * a parser that already defines an option string is left alone for that
      option (defensive — no duplicate-flag crash).
    """
    _walk(parser, prefix=())


def _walk(parser: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
    subparsers_action = _find_subparsers_action(parser)
    if subparsers_action is None:
        # Leaf parser → inject the blanket flags.
        cmd_key = " ".join(prefix)
        if cmd_key not in _AGENT_FLAG_EXCLUDE and not _has_option(parser, "--agent"):
            _add_agent_flag(parser)
        if not _has_option(parser, "--box"):
            _add_box_flag(parser)
        return
    # Group parser → recurse into each named subparser.  (A group parser is
    # never runnable standalone, so it gets no flags.)  Note: a parser can both
    # have subparsers AND be runnable via set_defaults(func=...) as a fallback
    # (e.g. ``box`` defaults to list); those fallbacks take no subject, so not
    # injecting on the group is correct.
    for name, child in subparsers_action.choices.items():
        _walk(child, prefix=(*prefix, name))


def _find_subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def command_key(args: argparse.Namespace) -> str:
    """Compute the dotted command path for *args* (e.g. ``"box convert"``).

    Mirrors the keys in :data:`AGENT_FLAG_COMMANDS` / :data:`BOX_FLAG_COMMANDS`.
    Walks the known nested-subparser dest chain so a command and its subcommand
    are joined by a single space.
    """
    command = getattr(args, "command", None)
    if not isinstance(command, str) or not command:
        return ""
    parts = [command]
    # Known nested-subparser dests, in walk order.  Only one level is needed for
    # the relevance sets above, but include the full chain for completeness.
    nested_dests = (
        "box_command",
        "agent_command",
        "workset_command",
        "rig_command",
        "system_command",
        "baseline_command",
        "vault_command",
        "helper_command",
        "share_command",
    )
    for dest in nested_dests:
        sub = getattr(args, dest, None)
        if isinstance(sub, str) and sub:
            parts.append(sub)
    return " ".join(parts)


def resolve_subject_value(
    positional: str | None, box_flag: str | None,
) -> str | None:
    """Reconcile a command's positional subject with its ``--box`` flag.

    The general rule wherever a positional and ``--box`` coexist (§Design 8):

    * only one supplied → use it;
    * both supplied, SAME string → warn + continue (return the value);
    * both supplied, DIFFERENT strings → :class:`SubjectConflictError`.

    Returns the effective subject string (or ``None`` for cwd).  Equality is a
    plain string compare — callers resolve the winner through their own
    path-or-name resolver, so two spellings of the same box (a name and its
    path) are treated as DIFFERENT here and rejected, which is the safe choice
    (the user gave conflicting selectors).
    """
    if positional is not None and box_flag is not None:
        if positional == box_flag:
            print(
                f"Warning: both a positional target and --box name '{box_flag}'; "
                "they match, continuing.",
                file=sys.stderr,
            )
            return box_flag
        raise SubjectConflictError(
            f"conflicting targets: positional '{positional}' and --box "
            f"'{box_flag}'. Pass only one."
        )
    return box_flag if box_flag is not None else positional


class FlagRelevanceError(Exception):
    """Raised when ``--agent``/``--box`` is passed to an unrelated command."""


def check_flag_relevance(args: argparse.Namespace) -> None:
    """Error if ``--agent``/``--box`` is set for a command it is irrelevant to.

    A flag PARSES on every command, but is only MEANINGFUL for its declared set.
    Setting it elsewhere is a user error (not a silent no-op), so raise an
    actionable :class:`FlagRelevanceError` the dispatcher turns into a non-zero
    exit.  ``setup``'s own ``--agent`` is never seen here (it is excluded from
    the blanket injection and routed through setup's own handling).
    """
    key = command_key(args)
    agent = getattr(args, "agent", None)
    box = getattr(args, "box", None)
    # ``--agent``: only check when the blanket flag is the one in play.  setup's
    # local --agent lives on a command (``setup``) that is not in
    # AGENT_FLAG_COMMANDS, but it is legitimate there — so skip the check for
    # the excluded commands entirely.  Only a real string value counts as "set"
    # (a non-str sentinel from a MagicMock test stub is ignored).
    if isinstance(agent, str) and key not in _AGENT_FLAG_EXCLUDE:
        # `shell` (and `box shell`) is a shell — it never launches an agent, so
        # `--agent` is meaningless rather than wrong.  Product decision: IGNORE
        # it with a clear note (don't hard-error), then proceed to open a plain
        # shell.  run_shell never reads args.agent, so the value is dropped here.
        if key in ("shell", "box shell"):
            print(
                "Note: --agent is ignored for 'shell' "
                "(a shell never launches an agent).",
                file=sys.stderr,
            )
            return
        if key not in AGENT_FLAG_COMMANDS:
            raise FlagRelevanceError(
                f"--agent is not valid for '{key}'. It applies to agent "
                f"commands ({', '.join(sorted(AGENT_FLAG_COMMANDS))}); for those "
                f"it picks the agent for this invocation. To set a persistent "
                f"default, run 'kanibako setup'."
            )
    if isinstance(box, str):
        if key not in BOX_FLAG_COMMANDS:
            raise FlagRelevanceError(
                f"--box is not valid for '{key}'. It selects the subject box "
                f"for commands that act on one "
                f"({', '.join(sorted(BOX_FLAG_COMMANDS))})."
            )

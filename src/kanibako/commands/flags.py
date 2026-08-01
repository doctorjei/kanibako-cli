"""Cross-cutting CLI flag plumbing: the blanket flags, the shared ``--null``
suppression flag, and option-anywhere parsing.

Three things live here, all of them argparse plumbing that no single command
owns:

1. the blanket ``--agent`` / ``--box`` flags + their per-command relevance
   (W1 Phase D), described below;
2. :data:`NULL_FLAG_HELP` / :func:`add_null_flag` — the ONE spelling of the
   ``--null`` suppression flag every scope's ``set`` verb wires (B-6);
3. :class:`OptionsAnywhereParser` / :func:`hoist_optionals` — the parser class
   that lets a flag be written in ANY position, including between two
   positionals (B-5).

Two cross-cutting CLI flags are added to (nearly) every subcommand via a
post-construction walk of the argparse tree:

* ``--agent NAME`` — the top-precedence, ephemeral (this-invocation-only) input
  to the unified agent resolver (``config.resolve_agent`` cascade).  It feeds
  the C2 resolver seam (``getattr(args, "agent", None)``) so a command resolves
  as if NAME were the configured agent.  Relevant only to agent-touching
  commands.
* ``--box VALUE`` — the SUBJECT selector: which box/project the command acts on
  (path OR registered box name; name precedence), even when not cwd.  Routed
  through :func:`kanibako.settings.paths.resolve_box_target`.  Relevant only to commands
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


# ---------------------------------------------------------------------------
# The shared ``--null`` suppression flag (B-6).
# ---------------------------------------------------------------------------

# ONE help string for every scope's ``set`` verb (box / workset / system /
# agent).  It has to TEACH the feature, because suppression has no verb of its
# own: Jei rejected a dedicated ``suppress`` command as "unnecessary and
# redundant" (2026-07-31), which makes this help text the whole discoverability
# surface.  So it says both halves — what ``--null`` DOES (suppresses the
# inherited value) and how to UNDO it (the sibling ``reset`` verb).
#
# ⚑ ``reset``, not ``--reset``: there is no ``--reset`` FLAG on any parser.
# ``reset`` is a sibling SUBCOMMAND (``box reset <key>``, ``system reset <key>``
# …); ``args.reset`` is only the internal namespace attribute the shared engine
# reads.  Naming a flag the user cannot type is the failure this text avoids.
#
# ⚑ The undo example is PER-VERB, not shared: each scope's ``reset`` takes a
# different positional shape (``workset reset <workset> <key>`` needs its noun,
# ``system reset <key>`` takes none).  One literal example would be untypeable
# for three of the four verbs, and an example the user cannot type teaches the
# opposite of what it is for.
NULL_FLAG_HELP_TEMPLATE = (
    "SUPPRESS the value this key would otherwise inherit: writes an explicit "
    "null (present-None) at this scope, so the scopes above it stop supplying "
    "the key and the consumer sees it as dropped (spec section 2h). This WRITES "
    "an override rather than removing one - to undo it and get the inherited "
    "value back, use the sibling 'reset' verb ('{undo}')."
)


def add_null_flag(parser: argparse.ArgumentParser, *, undo: str) -> None:
    """Wire the shared ``--null`` flag onto a scope's ``set`` parser.

    Single source for the sentence, so the four ``set`` verbs cannot drift apart
    (they were four copies of one string before).  *undo* is the verb's OWN
    ``reset`` spelling — keyword-only and required, so a new scope's ``set``
    cannot inherit another scope's untypeable example by accident.
    """
    parser.add_argument(
        "--null", action="store_true",
        help=NULL_FLAG_HELP_TEMPLATE.format(undo=undo),
    )


# ---------------------------------------------------------------------------
# Option-anywhere parsing (B-5).
# ---------------------------------------------------------------------------

# Sentinel for "an option whose arity this rewriter will not reason about".
_BAIL = -1


def _take_for(nargs: object) -> int:
    """Extra argv tokens an option with *nargs* consumes (or :data:`_BAIL`)."""
    if nargs == 0:
        return 0
    if nargs is None or nargs == 1:
        return 1
    return _BAIL


def _option_take(
    option_nargs: dict[str, object], parser: argparse.ArgumentParser, token: str,
) -> int | None:
    """How many EXTRA argv tokens *token* consumes, or ``None`` if positional.

    ``0`` is a toggle (``store_true`` and friends) or the self-contained
    ``--opt=value`` form; ``1`` is a single-value option (``--box NAME``);
    :data:`_BAIL` is any other arity, which makes the caller leave argv alone.
    """
    if token in option_nargs:
        return _take_for(option_nargs[token])
    head = token.split("=", 1)[0]
    if head != token and head in option_nargs:
        return 0  # ``--opt=value`` carries its own value
    # An unambiguous long-option ABBREVIATION (argparse's own allow_abbrev
    # behaviour).  Without this, ``--nul`` would parse before the positionals
    # and not after them — one flag with two behaviours, which is worse than
    # either.
    prefix = parser.prefix_chars
    if (
        getattr(parser, "allow_abbrev", True)
        and len(token) > 2
        and token[0] in prefix
        and token[1] in prefix
    ):
        matches = [opt for opt in option_nargs if opt.startswith(token)]
        if len(matches) == 1:
            return _take_for(option_nargs[matches[0]])
    return None


def _splits_positionals(positionals: "list[argparse.Action]") -> bool:
    """True if an interleaved optional could SPLIT this parser's positionals.

    argparse matches positionals in GROUPS divided by the optionals between
    them, so a group boundary can only fall BETWEEN two positional slots — which
    needs either two or more positional actions, or a single action that
    consumes more than one token (``nargs`` ``"*"`` / ``"+"`` / an int > 1).

    A parser with one ``None`` / ``"?"`` / ``1`` positional can never be split
    and is left strictly alone.
    """
    if len(positionals) >= 2:
        return True
    if len(positionals) == 1:
        nargs = positionals[0].nargs
        return nargs in ("*", "+") or (isinstance(nargs, int) and nargs > 1)
    return False


def hoist_optionals(
    parser: argparse.ArgumentParser, argv: list[str],
) -> list[str]:
    """Reorder *argv* so optionals may be written in ANY position (B-5).

    argparse matches positionals in GROUPS split by the optionals between them,
    and a flag written BETWEEN two positionals strands what follows it.  There
    are TWO ways that bites, and the second is version-dependent:

    1. a VARIADIC positional (``nargs="*"``) swallows its whole group, leaving
       nothing for the tail — every Python version::

           box set mybox --null pref.system.agent
           → error: unrecognized arguments: pref.system.agent

    2. ⚑ **Python < 3.13 only.** ``_match_arguments_partial`` matched a
       ZERO-WIDTH trailing positional into the FIRST group and then dropped it
       from the pending list, so the tail had no positional left to bind::

           workset set myws --null model        # 3.11: unrecognized: model

       For ``[workset (nargs=None), key_value (nargs="?")]`` against the pattern
       ``'AOA'``, 3.11 returns ``[1, 0]`` (both actions consumed, ``key_value``
       matched empty) where 3.13 returns ``[1]``.  CPython 3.13 added the
       trailing-zero strip — *"if the next pattern char is an optional, defer the
       empty positionals"* — which is exactly this repair, upstream.  We support
       ``>=3.11`` (``requires-python``; CI pins 3.11), so the rewrite must not
       lean on it: 3.13 masked the defect locally while CI reddened.

    This moves the optionals (each with its value) to the FRONT, preserving
    their relative order, and leaves the positionals in theirs.  Nothing is
    dropped and nothing changes meaning — argparse binds an optional by NAME,
    never by position — so the only observable difference is that the
    previously-fatal orderings now parse, identically on every supported
    version.

    The rewrite is INERT unless the parser actually has the shape that breaks:

    * a ``REMAINDER`` or subparsers positional → argv returned untouched,
      because those deliberately capture it verbatim and reordering would
      corrupt the very thing they exist to preserve;
    * positionals that cannot be split (:func:`_splits_positionals`) → argv
      returned untouched, since no group boundary can fall between them.

    A ``--`` ends the rewrite: everything from it on is passed through verbatim,
    which is how a user writes a positional that looks like a flag.
    """
    positionals = [a for a in parser._actions if not a.option_strings]
    # Checked FIRST: a raw-argv positional vetoes the rewrite outright, whatever
    # else the parser's shape looks like.
    if any(
        isinstance(a, argparse._SubParsersAction) or a.nargs == argparse.REMAINDER
        for a in positionals
    ):
        return argv
    if not _splits_positionals(positionals):
        return argv

    option_nargs: dict[str, object] = {}
    for action in parser._actions:
        for opt in action.option_strings:
            option_nargs[opt] = action.nargs

    hoisted: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            rest.extend(argv[i:])
            break
        take = _option_take(option_nargs, parser, token)
        if take is None:
            rest.append(token)
            i += 1
            continue
        if take == _BAIL:
            return argv
        values = argv[i + 1: i + 1 + take]
        # A value-taking option with nothing to take — it sits at the END of
        # argv, or its value slot holds ``--``.  Hoisting the bare flag would
        # move it in front of the positionals, where the FIRST positional
        # becomes adjacent to it and is silently bound as its value
        # (``box show mybox --box`` → ``--box mybox``, exit 0, wrong subject).
        # Bail so argparse raises the error it owns: "expected one argument".
        if take > 0 and (len(values) < take or "--" in values):
            return argv
        hoisted.append(token)
        hoisted.extend(values)
        i += 1 + take
    return hoisted + rest


class OptionsAnywhereParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose optionals parse in any position (B-5).

    Installed as the ``parser_class`` of the TOP-LEVEL subparsers action, so
    every subcommand inherits it — and every NESTED subcommand too, since
    ``add_subparsers`` defaults ``parser_class`` to the type of the parser it is
    called on.  One rule for the whole CLI ("a flag may go anywhere") beats
    per-verb patching, and :func:`hoist_optionals` is inert for the parsers that
    never had the problem.

    The hook is ``parse_known_args`` rather than ``parse_args`` because that is
    the entry point ``_SubParsersAction.__call__`` uses for a subcommand.
    """

    def parse_known_args(  # type: ignore[override]
        self,
        args: "list[str] | None" = None,
        namespace: argparse.Namespace | None = None,
    ) -> "tuple[argparse.Namespace, list[str]]":
        if args is not None:
            args = hoist_optionals(self, list(args))
        return super().parse_known_args(args, namespace)


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

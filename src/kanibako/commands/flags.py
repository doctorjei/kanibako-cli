"""Cross-cutting CLI flag plumbing: the blanket flags, the shared ``--null``
suppression flag, and option-anywhere parsing.

Three things live here, all of them argparse plumbing that no single command
owns: the blanket ``--agent`` / ``--box`` flags + their per-command relevance
(W1 Phase D); :data:`NULL_FLAG_HELP_TEMPLATE` / :func:`add_null_flag`, the ONE
spelling of the ``--null`` suppression flag every scope's ``set`` verb wires
(B-6); and :class:`OptionsAnywhereParser` / :func:`hoist_optionals`, the parser
class that lets a flag be written in ANY position, including between two
positionals (B-5).

⚑ **Parse everywhere, advertise and mean it only where declared.**  Both blanket
flags PARSE on every command, but each is only MEANINGFUL for a declared set;
passing one to an UNRELATED command is a hard error, never a silent no-op
(:func:`check_flag_relevance`).  Because it is an error there, it is also
``help=argparse.SUPPRESS`` there — ``--help`` never offers a flag the command
would refuse.

What each flag selects, the full case enumeration and the provenance:
``llm-docs/kanibako/commands/flags.py.md``.
"""

from __future__ import annotations

import argparse
import sys

from kanibako.errors import SubjectConflictError

# ---------------------------------------------------------------------------
# Relevance declarations (dotted command paths).
# ---------------------------------------------------------------------------

# ``--agent`` is the ephemeral agent-resolver override: the commands that run
# the unified cascade with the explicit-agent seam.
# ⚑ ``shell`` is NOT here — it bypasses agent resolution entirely (the no-agent
# recovery hatch).  ``setup`` is NOT here — it has its own persistent --agent.
AGENT_FLAG_COMMANDS: frozenset[str] = frozenset({
    "start",
    "box start",
    "create",
    "box create",
    "reauth",
    "agent reauth",
})

# ``--box`` is the subject/anchor selector: every command that acts on a subject
# box/project rather than on cwd.
#
# ⚑ Declared in CANONICAL spellings only — an argparse ALIAS is never listed.
# ``add_parser(aliases=[...])`` registers ONE parser under several names, and
# :func:`command_key` reports that parser's canonical name whichever the user
# typed, so ``box mv`` inherits ``box move``'s membership.  Listing the alias
# too would be a second carrier of one shape.
#
# ⚑ The top-level shortcuts are NOT argparse aliases — ``rm``/``register`` (and
# ``start``/``stop``/``shell``) are SEPARATE parser objects in ``cli.py`` that
# merely share the box handler, so there is no alias relation for the code to
# derive and each spelling is its own key.  Both spellings of a shortcut pair
# must therefore be declared together.
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
    # top-level shortcuts for the two box verbs above (separate parsers, same
    # handler: ``run_rm`` / ``run_register`` both read ``--box`` through
    # ``resolve_subject_value``).
    "rm",
    "register",
    # auth
    "reauth",
    "agent reauth",
    # workset member
    "workset disconnect",
})

# Commands that already own a local ``--agent`` flag with different semantics
# and must be skipped by the blanket injection.
_AGENT_FLAG_EXCLUDE: frozenset[str] = frozenset({"setup"})

# Namespace attribute carrying the CANONICAL dotted path of the leaf parser that
# actually ran, stamped by :func:`_walk` via ``set_defaults``.  It exists because
# argparse records the name the user TYPED (``args.box_command == "mv"``), which
# would make relevance an accident of spelling.
_COMMAND_PATH_DEST = "_command_path"


# ⚑ Both adders below pass ``default=None``: an un-given flag installs NOTHING —
# no default agent, no default subject.  The CLI is its own cascade level, above
# ``box``, and it supplies a value only when the user actually typed one.
#
# ⚑⚑ *advertise* separates PARSING from ADVERTISING.  The flag is added to every
# leaf either way, so it always parses and relevance stays a post-parse judgement
# with an enumerating message; ``advertise=False`` only withholds the help text,
# so a command that would REFUSE the flag does not offer it first.
def _add_agent_flag(
    parser: argparse.ArgumentParser, *, advertise: bool,
) -> None:
    parser.add_argument(
        "--agent",
        metavar="NAME",
        default=None,
        help=(
            "Use agent NAME for this invocation (ephemeral; top of the "
            "resolution cascade, not persisted)."
        ) if advertise else argparse.SUPPRESS,
    )


def _add_box_flag(parser: argparse.ArgumentParser, *, advertise: bool) -> None:
    parser.add_argument(
        "--box",
        metavar="VALUE",
        default=None,
        help=(
            "Act on box VALUE (a registered box name or a path), even when not "
            "the current directory."
        ) if advertise else argparse.SUPPRESS,
    )


# ---------------------------------------------------------------------------
# The shared ``--null`` suppression flag (B-6).
# ---------------------------------------------------------------------------

# ONE help string for every scope's ``set`` verb.  It has to TEACH the feature,
# because suppression has no verb of its own — this help text is the whole
# discoverability surface.
#
# ⚑ ``reset``, not ``--reset``: there is no ``--reset`` FLAG on any parser.
# ``reset`` is a sibling SUBCOMMAND; ``args.reset`` is only the internal
# namespace attribute.  Naming a flag the user cannot type is the failure this
# text avoids.
#
# ⚑ The undo example is PER-VERB, not shared: each scope's ``reset`` takes a
# different positional shape, so one literal example would be untypeable for
# three of the four verbs.
NULL_FLAG_HELP_TEMPLATE = (
    "SUPPRESS the value this key would otherwise inherit: writes an explicit "
    "null (present-None) at this scope, so the scopes above it stop supplying "
    "the key and the consumer sees it as dropped (spec section 2h). This WRITES "
    "an override rather than removing one - to undo it and get the inherited "
    "value back, use the sibling 'reset' verb ('{undo}')."
)


def add_null_flag(parser: argparse.ArgumentParser, *, undo: str) -> None:
    """Wire the shared ``--null`` flag onto a scope's ``set`` parser.

    *undo* is the verb's OWN ``reset`` spelling — keyword-only and required, so a
    new scope's ``set`` cannot inherit another scope's untypeable example by
    accident.
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

    ``0`` is a toggle or the self-contained ``--opt=value`` form; ``1`` is a
    single-value option; :data:`_BAIL` is any other arity, which makes the caller
    leave argv alone.
    """
    if token in option_nargs:
        return _take_for(option_nargs[token])
    head = token.split("=", 1)[0]
    if head != token and head in option_nargs:
        return 0  # ``--opt=value`` carries its own value
    # An unambiguous long-option ABBREVIATION (argparse's own allow_abbrev
    # behaviour).  ⚑ Without this, ``--nul`` would parse before the positionals
    # and not after them — one flag with two behaviours.
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

    ⚑ A parser with one ``None`` / ``"?"`` / ``1`` positional can never be split
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

    The optionals (each with its value) move to the FRONT, preserving their
    relative order; the positionals keep theirs.  Nothing is dropped and nothing
    changes meaning — argparse binds an optional by NAME, never by position.  A
    ``--`` ends the rewrite: everything from it on is passed through verbatim.
    The rewrite is INERT unless the parser has the shape that breaks.

    ⚑ **This must not lean on CPython 3.13's trailing-zero strip.**  We support
    ``>=3.11`` and CI pins 3.11, where a ZERO-WIDTH trailing positional is
    matched into the FIRST group and dropped — 3.13 masked that defect locally
    while CI reddened.  The two failing shapes, the measured
    ``_match_arguments_partial`` behaviour and the inertness rules are in the
    llm-doc.
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
        # argv, or its value slot holds ``--``.  ⚑ Hoisting the bare flag would
        # put the FIRST positional adjacent to it, silently bound as its value
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
    every subcommand inherits it — and every NESTED subcommand too.

    ⚑ The hook is ``parse_known_args`` rather than ``parse_args`` because that
    is the entry point ``_SubParsersAction.__call__`` uses for a subcommand.
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

    A *leaf* is any (sub)parser that does NOT itself hold further subparsers;
    group parsers only dispatch, so they get nothing.  Every leaf gets the flags
    unconditionally, so they PARSE everywhere and relevance is checked
    post-parse.  Two exceptions: ``setup`` (anything in
    :data:`_AGENT_FLAG_EXCLUDE`) keeps its own ``--agent`` and gets only the
    blanket ``--box``; a parser that already defines an option string is left
    alone for that option (defensive — no duplicate-flag crash).

    ⚑ A leaf outside a flag's declared set gets the flag with
    ``help=argparse.SUPPRESS``: it still parses, so passing it still reaches
    :func:`check_flag_relevance` and its enumerating error rather than argparse's
    bare "unrecognized arguments" — but ``--help`` no longer offers a flag the
    command would refuse.
    """
    _walk(parser, prefix=())


def _walk(parser: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
    subparsers_action = _find_subparsers_action(parser)
    if subparsers_action is None:
        # Leaf parser → stamp its canonical path, then inject the blanket flags.
        cmd_key = " ".join(prefix)
        parser.set_defaults(**{_COMMAND_PATH_DEST: cmd_key})
        if cmd_key not in _AGENT_FLAG_EXCLUDE and not _has_option(parser, "--agent"):
            _add_agent_flag(parser, advertise=cmd_key in AGENT_FLAG_COMMANDS)
        if not _has_option(parser, "--box"):
            _add_box_flag(parser, advertise=cmd_key in BOX_FLAG_COMMANDS)
        return
    # Group parser → recurse into each DISTINCT subparser, under the first name
    # it was registered with.  ⚑ ``add_parser(name, aliases=[...])`` puts the
    # canonical name into ``choices`` first and each alias after it, all mapped
    # to the SAME parser object; skipping the repeats is what makes the stamped
    # path canonical, so ``box mv`` is judged as ``box move``.
    # ⚑ A parser can BOTH have subparsers AND be runnable via
    # set_defaults(func=...) as a fallback (e.g. ``box`` defaults to list); those
    # fallbacks take no subject, so not injecting on the group is correct.
    seen: set[int] = set()
    for name, child in subparsers_action.choices.items():
        if id(child) in seen:
            continue
        seen.add(id(child))
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

    Mirrors the keys in :data:`AGENT_FLAG_COMMANDS` / :data:`BOX_FLAG_COMMANDS`,
    joining a command and its subcommand with a single space.

    ⚑ The CANONICAL path stamped by :func:`_walk` wins when present, so an
    argparse ALIAS is judged as the command it aliases: argparse stores the name
    the user TYPED in the subcommand dest, which would otherwise make ``box mv``
    a different command from ``box move`` and let one spelling of one parser
    accept a flag the other refuses.  The typed-name reconstruction below stays
    as the fallback for a namespace built by hand (tests) or by a parser tree
    that never went through :func:`inject_blanket_flags`.
    """
    stamped = getattr(args, _COMMAND_PATH_DEST, None)
    if isinstance(stamped, str) and stamped:
        return stamped
    command = getattr(args, "command", None)
    if not isinstance(command, str) or not command:
        return ""
    parts = [command]
    # Known nested-subparser dests, in walk order (the full chain, though only
    # one level is needed for the relevance sets above).
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
    only one supplied → use it; both supplied, SAME string → warn + continue;
    both supplied, DIFFERENT strings → :class:`SubjectConflictError`.  Returns
    the effective subject string, or ``None`` for cwd.

    ⚑ Equality is a plain string compare, so two spellings of the SAME box (a
    name and its path) are DIFFERENT here and rejected — the safe choice.
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

    Setting a flag outside its declared set is a user error, not a silent no-op,
    so raise an actionable :class:`FlagRelevanceError` the dispatcher turns into
    a non-zero exit.
    """
    key = command_key(args)
    agent = getattr(args, "agent", None)
    box = getattr(args, "box", None)
    # ``--agent``: only check when the blanket flag is the one in play.  setup's
    # local --agent lives on a command (``setup``) that is not in
    # AGENT_FLAG_COMMANDS, but it is legitimate there — so skip the check for
    # the excluded commands entirely.
    # ⚑ ``None`` means ABSENT: an un-given flag installs nothing, so it must not
    # be treated as a value.  Any ``str`` counts as "set"; a non-str sentinel
    # from a MagicMock test stub is ignored.
    if isinstance(agent, str) and key not in _AGENT_FLAG_EXCLUDE:
        # `shell` (and `box shell`) never launches an agent, so `--agent` is
        # meaningless rather than wrong.  Product decision: IGNORE it with a
        # clear note (don't hard-error), then open a plain shell.  run_shell
        # never reads args.agent, so the value is dropped here.
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

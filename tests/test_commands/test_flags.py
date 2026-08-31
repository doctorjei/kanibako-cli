"""Tests for the blanket --agent/--box flags + relevance (W1 Phase D)."""

from __future__ import annotations

import argparse
import inspect
import re
import sys

import pytest

from kanibako import cli
from kanibako.commands.flags import (
    AGENT_FLAG_COMMANDS,
    AGENT_FLAG_HELP_PER_RUN,
    AGENT_FLAG_HELP_PERSISTED,
    AGENT_FLAG_PERSISTS,
    BOX_FLAG_COMMANDS,
    FlagRelevanceError,
    _AGENT_FLAG_EXCLUDE,
    check_flag_relevance,
    command_key,
    resolve_subject_value,
)
from kanibako.errors import SubjectConflictError


@pytest.fixture
def parser():
    return cli.build_parser()


def _parse(parser, argv):
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Parsing: flags parse everywhere
# ---------------------------------------------------------------------------

class TestBlanketFlagsParse:
    def test_agent_and_box_parse_on_start(self, parser):
        a = _parse(parser, ["start", "--agent", "claude", "--box", "foo"])
        assert a.agent == "claude"
        assert a.box == "foo"

    def test_box_parses_on_box_convert(self, parser):
        a = _parse(parser, ["box", "convert", "--box", "mybox", "--standalone"])
        assert a.box == "mybox"
        assert a.to_standalone is True  # destination axis still works

    def test_box_parses_on_unrelated_command(self, parser):
        # PARSES everywhere (no argparse error); relevance is a post-parse check.
        a = _parse(parser, ["list", "--box", "foo"])
        assert a.box == "foo"

    def test_agent_parses_on_unrelated_command(self, parser):
        a = _parse(parser, ["list", "--agent", "claude"])
        assert a.agent == "claude"

    def test_setup_keeps_local_agent(self, parser):
        # setup is excluded from the blanket --agent; its own flag still parses.
        a = _parse(parser, ["setup", "--agent", "claude"])
        assert a.agent == "claude"


# ---------------------------------------------------------------------------
# command_key
# ---------------------------------------------------------------------------

class TestCommandKey:
    def test_top_level(self, parser):
        assert command_key(_parse(parser, ["start"])) == "start"

    def test_nested(self, parser):
        assert command_key(
            _parse(parser, ["box", "convert", "--standalone"])
        ) == "box convert"

    def test_agent_reauth(self, parser):
        assert command_key(_parse(parser, ["agent", "reauth"])) == "agent reauth"


# ---------------------------------------------------------------------------
# Relevance enforcement
# ---------------------------------------------------------------------------

class TestRelevance:
    def test_agent_relevant_on_start(self, parser):
        check_flag_relevance(_parse(parser, ["start", "--agent", "claude"]))

    def test_agent_relevant_on_agent_reauth(self, parser):
        check_flag_relevance(_parse(parser, ["agent", "reauth", "--agent", "x"]))

    def test_agent_relevant_on_create(self, parser):
        # The `create --agent` fix (persona-grata trigger): no longer rejected.
        check_flag_relevance(
            _parse(parser, ["create", "--agent", "navigator+codex"])
        )

    def test_agent_relevant_on_box_create(self, parser):
        check_flag_relevance(
            _parse(parser, ["box", "create", "--agent", "navigator+codex"])
        )

    def test_agent_irrelevant_on_list_errors(self, parser):
        with pytest.raises(FlagRelevanceError):
            check_flag_relevance(_parse(parser, ["list", "--agent", "claude"]))

    def test_agent_ignored_on_shell_with_note(self, parser, capsys):
        # shell never launches an agent: --agent is IGNORED with a note to
        # stderr (product decision), NOT a hard FlagRelevanceError.
        check_flag_relevance(_parse(parser, ["shell", "--agent", "claude"]))
        assert "--agent is ignored for 'shell'" in capsys.readouterr().err

    def test_agent_ignored_on_box_shell_with_note(self, parser, capsys):
        check_flag_relevance(
            _parse(parser, ["box", "shell", "--agent", "claude"])
        )
        assert "--agent is ignored for 'shell'" in capsys.readouterr().err

    def test_box_relevant_on_start(self, parser):
        check_flag_relevance(_parse(parser, ["start", "--box", "foo"]))

    def test_box_relevant_on_shell(self, parser):
        check_flag_relevance(_parse(parser, ["shell", "--box", "foo"]))

    def test_box_relevant_on_box_convert(self, parser):
        check_flag_relevance(
            _parse(parser, ["box", "convert", "--box", "m", "--standalone"])
        )

    def test_box_relevant_on_workset_disconnect(self, parser):
        check_flag_relevance(
            _parse(parser, ["workset", "disconnect", "ws", "proj", "--box", "proj"])
        )

    def test_box_irrelevant_on_list_errors(self, parser):
        with pytest.raises(FlagRelevanceError):
            check_flag_relevance(_parse(parser, ["list", "--box", "foo"]))

    def test_box_irrelevant_on_create_errors(self, parser):
        with pytest.raises(FlagRelevanceError):
            check_flag_relevance(_parse(parser, ["create", "--box", "foo"]))

    def test_setup_agent_not_flagged(self, parser):
        # setup's own --agent is legitimate and must NOT raise.
        check_flag_relevance(_parse(parser, ["setup", "--agent", "claude"]))

    def test_no_flags_no_error(self, parser):
        check_flag_relevance(_parse(parser, ["list"]))

    def test_alias_relevant_where_its_canonical_spelling_is(self, parser):
        # ``inspect`` is an ALIAS of ``box info``, which is declared.
        check_flag_relevance(_parse(parser, ["box", "inspect", "--box", "foo"]))

    def test_alias_still_refused_where_its_canonical_spelling_is(self, parser):
        # The inverse: canonicalising must not make an alias MORE permissive
        # than the command it aliases.  ``box ls`` → ``box list``, undeclared.
        with pytest.raises(FlagRelevanceError):
            check_flag_relevance(_parse(parser, ["box", "ls", "--box", "foo"]))

    def test_box_relevant_on_top_level_rm(self, parser):
        check_flag_relevance(_parse(parser, ["rm", "--box", "foo", "foo"]))

    def test_box_relevant_on_top_level_register(self, parser):
        check_flag_relevance(_parse(parser, ["register", "--box", "foo", "foo"]))

    def test_box_relevant_on_code(self, parser):
        # ``run_code`` reads --box through ``resolve_subject_value``; the table
        # refused it anyway, so ``kanibako code --box foo`` exited 2.
        check_flag_relevance(_parse(parser, ["code", "--box", "foo"]))

    def test_box_relevant_on_code_remote_leg_too(self, parser):
        # Relevance runs before dispatch, so it gates BOTH legs of run_code —
        # and ``_run_code_remote`` reads --box as well.
        check_flag_relevance(_parse(parser, ["code", "--box", "foo", "--remote", "h"]))

    def test_box_still_irrelevant_on_rig_list(self, parser):
        # Negative control: a neighbouring leaf whose handler never reads --box
        # must still refuse it.  Declaring ``code`` must not loosen anything.
        with pytest.raises(FlagRelevanceError):
            check_flag_relevance(_parse(parser, ["rig", "list", "--box", "foo"]))

    def test_the_declared_sets_carry_the_commands_they_are_for(self):
        # Spot check that the sets are POPULATED as documented.  What makes every
        # entry a real command is derived, not listed:
        # TestEveryDeclaredKeyNamesAReachableCommand.
        assert "start" in AGENT_FLAG_COMMANDS
        assert "agent reauth" in AGENT_FLAG_COMMANDS
        assert "create" in AGENT_FLAG_COMMANDS
        assert "box create" in AGENT_FLAG_COMMANDS
        assert "start" in BOX_FLAG_COMMANDS
        assert "workset disconnect" in BOX_FLAG_COMMANDS


# ---------------------------------------------------------------------------
# Help honesty: a leaf advertises a blanket flag only where it takes one
# ---------------------------------------------------------------------------

def _leaves(parser):
    """Map every dotted command key to its leaf parser (walk order)."""
    found: dict[str, argparse.ArgumentParser] = {}

    def sub(p):
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action
        return None

    def walk(p, prefix):
        action = sub(p)
        if action is None:
            found[" ".join(prefix)] = p
            return
        for name, child in action.choices.items():
            walk(child, (*prefix, name))

    walk(parser, ())
    return found


def _action_for(parser, option):
    for action in parser._actions:
        if option in action.option_strings:
            return action
    return None


def _alias_closure(leaves, keys):
    """*keys* plus every ALIAS spelling of the same leaf.

    ⚑ ``add_parser(aliases=[...])`` registers ONE parser object under several
    names, so an alias cannot carry different help from its canonical spelling —
    the advertisement is a property of the parser, not of the key.  The RELEVANCE
    check now agrees, because :func:`command_key` reports the canonical path
    (:class:`TestAnAliasIsJudgedAsTheCommandItAliases`); before that fix
    ``box mv --box`` was refused where ``box move --box`` was accepted, while
    ``box mv --help`` advertised the flag anyway.
    """
    by_object: dict[int, set[str]] = {}
    for key, leaf in leaves.items():
        by_object.setdefault(id(leaf), set()).add(key)
    out: set[str] = set()
    for key in keys:
        leaf = leaves.get(key)
        if leaf is not None:
            out |= by_object[id(leaf)]
    return out


def _alias_keys(parser):
    """Map every ALIAS leaf key in the tree to its canonical key.

    Derived from the tree itself: ``add_parser(name, aliases=[...])`` registers
    ONE parser object under several names, canonical FIRST, so the first name a
    given object appears under at each level is that level's canonical spelling.
    """
    out: dict[str, str] = {}

    def sub(p):
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action
        return None

    def walk(p, typed, canon):
        action = sub(p)
        if action is None:
            if " ".join(typed) != " ".join(canon):
                out[" ".join(typed)] = " ".join(canon)
            return
        first: dict[int, str] = {}
        for name, child in action.choices.items():
            canon_name = first.setdefault(id(child), name)
            walk(child, (*typed, name), (*canon, canon_name))

    walk(parser, (), ())
    return out


def _shortcut_twins(parser):
    """Top-level leaves that dispatch to the SAME handler as a ``box`` leaf.

    ⚑ Derived by HANDLER IDENTITY, not from a hand-kept list: ``kanibako rm``
    and ``kanibako box rm`` are two separate parser objects (not argparse
    aliases) that share ``run_rm``, and that shared handler is the whole reason
    they must agree about a flag.  Each twin is reported under its CANONICAL
    spelling so an alias of the box verb is not counted separately.
    """
    leaves = _leaves(parser)
    canonical = _alias_keys(parser)
    pairs: set[tuple[str, str]] = set()
    for key, leaf in leaves.items():
        if " " in key:
            continue
        func = leaf.get_default("func")
        if func is None:
            continue
        for other, other_leaf in leaves.items():
            if other.startswith("box ") and other_leaf.get_default("func") is func:
                pairs.add((key, canonical.get(other, other)))
    return sorted(pairs)


class TestAnAliasIsJudgedAsTheCommandItAliases:
    """``box mv --box`` was refused where ``box move --box`` was accepted.

    argparse records the name the user TYPED (``args.box_command == "mv"``), so
    relevance was an accident of spelling on ONE parser object registered under
    two names — and ``--help`` advertised the flag under both, promising what one
    spelling then refused.  :func:`command_key` now reports the path stamped on
    the leaf that actually ran.  INVERT: drop the ``set_defaults`` stamp in
    ``_walk`` (or the ``_COMMAND_PATH_DEST`` branch in ``command_key``) and the
    alias rows redden.
    """

    def test_every_alias_in_the_tree_carries_its_canonical_path(self, parser):
        from kanibako.commands.flags import _COMMAND_PATH_DEST

        leaves = _leaves(parser)
        aliases = _alias_keys(parser)
        # Guard against a vacuous pass: the aliases are really there.
        assert "box mv" in aliases and aliases["box mv"] == "box move"
        for alias, canon in aliases.items():
            assert leaves[alias].get_default(_COMMAND_PATH_DEST) == canon, alias

    @pytest.mark.parametrize("argv, canonical", [
        (["box", "inspect"], "box info"),
        (["box", "mv", "old", "new"], "box move"),
        (["box", "delete", "x"], "box rm"),
        (["box", "ls"], "box list"),
        (["workset", "inspect", "ws"], "workset info"),
        (["agent", "ls"], "agent list"),
        (["rig", "delete", "img"], "rig rm"),
        (["system", "inspect"], "system info"),
    ])
    def test_command_key_reports_the_canonical_spelling(
        self, parser, argv, canonical,
    ):
        assert command_key(_parse(parser, argv)) == canonical

    def test_the_typed_spelling_is_still_recorded(self, parser):
        """Canonicalising is a RELEVANCE judgement, not a rewrite of the
        namespace — the subcommand dest still says what the user typed."""
        assert _parse(parser, ["box", "inspect"]).box_command == "inspect"


class TestATopLevelShortcutAgreesWithItsBoxVerb:
    """``kanibako rm --box`` was refused where ``kanibako box rm --box`` was not.

    These are NOT argparse aliases — ``cli.py`` registers them as separate
    parsers — so no canonicalisation can relate them and each spelling is its own
    key.  What relates them is the HANDLER: ``run_rm`` reads ``--box`` through
    ``resolve_subject_value`` whichever parser dispatched to it, so a shortcut
    that refuses the flag refuses it for a handler that would have used it.
    INVERT: drop ``"rm"``/``"register"`` from ``BOX_FLAG_COMMANDS`` and the
    ``--box`` rows redden.
    """

    def test_the_twins_are_found(self, parser):
        # Vacuity guard + the inventory this rule is asserted over.
        twins = dict(_shortcut_twins(parser))
        assert twins["rm"] == "box rm"
        assert twins["register"] == "box register"
        assert twins["start"] == "box start"

    @pytest.mark.parametrize("declared", [AGENT_FLAG_COMMANDS, BOX_FLAG_COMMANDS])
    def test_both_spellings_are_declared_together(self, parser, declared):
        split = [
            (short, full) for short, full in _shortcut_twins(parser)
            if (short in declared) != (full in declared)
        ]
        assert split == []


def _handler_reads(func, option, _depth=1, _seen=None):
    """True if *func* — or a helper it calls in its OWN module — reads ``args.<option>``.

    ⚑ Derived from the handler's SOURCE, because that is where the disagreement
    lives: a declared set is a CLAIM about what handlers do, and only a handler
    can settle it.  One level of intra-module following covers a handler that
    splits a leg out (``run_code`` → ``_run_code_remote``); every consumer today
    spells the read ``args.<option>`` or ``getattr(args, "<option>")``, and
    ``test_the_oracle_is_not_vacuous`` pins a true AND a false case for each
    flag, so a new spelling reddens there rather than passing silently here.
    """
    if func is None or _depth < 0:
        return False
    _seen = _seen if _seen is not None else set()
    key = (getattr(func, "__module__", None), getattr(func, "__qualname__", None))
    if key in _seen:
        return False
    _seen.add(key)
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        return False
    pattern = rf'args\.{option}\b|getattr\(\s*args\s*,\s*[\'"]{option}[\'"]'
    if re.search(pattern, source):
        return True
    module = sys.modules.get(getattr(func, "__module__", ""), None)
    if module is None:
        return False
    for name in set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", source)):
        callee = getattr(module, name, None)
        if inspect.isfunction(callee) and _handler_reads(
            callee, option, _depth - 1, _seen
        ):
            return True
    return False


class TestATableEntryAgreesWithWhatTheHandlerReads:
    """``kanibako code --box mybox`` exited 2 while ``run_code`` read ``--box``.

    ``code`` is neither an argparse alias nor a shortcut twin — there is no
    ``box code`` — so NEITHER derivation above reaches it, and its refusal came
    from the relevance table alone.  The property that does reach it is the
    general one: a declared set is a claim about handlers, so every leaf whose
    handler reads a blanket flag must be declared for that flag.  INVERT: drop
    ``"code"`` from ``BOX_FLAG_COMMANDS`` and the ``--box`` row reddens, naming
    ``code``.

    ⚑ ONE direction only, and deliberately so.  The converse of THIS property —
    declared ⇒ some handler reads it — stays unasserted: a command may legitimately
    be declared for a flag its handler does not itself read (it may pass the
    namespace on, or read it under a spelling the source scan does not model), and
    an over-declaration costs a user nothing but a flag that is accepted and
    ignored.  The converse that IS asserted is the weaker, exact one — declared ⇒
    the key names a REACHABLE command — in
    :class:`TestEveryDeclaredKeyNamesAReachableCommand`.
    """

    def test_the_oracle_is_not_vacuous(self, parser):
        # A true and a false case per flag: without these the sweep below would
        # pass just as happily if the source scan matched nothing at all.
        leaves = _leaves(parser)
        assert _handler_reads(leaves["box stop"].get_default("func"), "box")
        assert not _handler_reads(leaves["box list"].get_default("func"), "box")
        assert _handler_reads(leaves["box create"].get_default("func"), "agent")
        assert not _handler_reads(leaves["rig list"].get_default("func"), "agent")

    @pytest.mark.parametrize(
        "option,declared",
        [("box", BOX_FLAG_COMMANDS), ("agent", AGENT_FLAG_COMMANDS)],
    )
    def test_every_handler_that_reads_a_flag_is_declared(
        self, parser, option, declared,
    ):
        canonical = _alias_keys(parser)
        undeclared = set()
        for key, leaf in _leaves(parser).items():
            canon = canonical.get(key, key)
            if canon in declared:
                continue
            # NOT an exemption: ``check_flag_relevance`` skips the ``--agent``
            # check outright for these (setup owns a LOCAL --agent with
            # persistent semantics), so there is no refusal to disagree with.
            if option == "agent" and canon in _AGENT_FLAG_EXCLUDE:
                continue
            if _handler_reads(leaf.get_default("func"), option):
                undeclared.add(canon)
        assert sorted(undeclared) == []


def _canonical_leaf_keys(parser):
    """Every dotted path :func:`command_key` can report for a REAL invocation.

    ⚑ Derived from the tree by composing the two walks above, not re-walked: a
    leaf key that is not an ALIAS spelling is the canonical path ``flags._walk``
    stamps, and a stamped path is the only thing ``command_key`` returns for a
    namespace argparse built.  A GROUP parser is absent by construction and
    correctly so — ``_walk`` injects the blanket flags on leaves only, so a group
    with a ``set_defaults(func=...)`` fallback (``kanibako box`` → list) has no
    ``--box`` action at all, ``args.box`` never exists, and there is no refusal
    for a declaration to disagree with.  If a group ever gains the flags, this
    oracle has to widen with them.
    """
    return set(_leaves(parser)) - set(_alias_keys(parser))


class TestEveryDeclaredKeyNamesAReachableCommand:
    """``--box is not valid for 'rig list'`` named ``reauth`` among the commands
    that DO take it, and ``kanibako reauth --box x`` then failed.

    ``"reauth"`` was declared in BOTH sets, but there is no top-level ``reauth``
    parser — ``kanibako reauth`` parses as ``start reauth``, consuming the word as
    a box name, so ``kanibako reauth --help`` printed ``start``'s usage and
    ``kanibako reauth`` died with "no box at reauth".  ``command_key`` could
    therefore never report ``"reauth"`` and the entry changed no behaviour; what
    it did do was reach the user, because both sets are joined verbatim into the
    refusal message.  The real command was carried the whole time by the separate
    ``"agent reauth"`` entry.

    The property is the exact one the defect violated: a declared key must be a
    command someone can type.  It is deliberately WEAKER than "declared ⇒ a
    handler reads it" (see the class above) — over-declaring a live command is a
    judgement call, naming a command that does not exist is not.
    INVERT: put ``"reauth"`` back in either set and the matching rows redden.
    """

    def test_the_oracle_is_not_vacuous(self, parser):
        # Without these, an oracle that returned every string would pass below.
        reachable = _canonical_leaf_keys(parser)
        assert "agent reauth" in reachable
        assert "box convert" in reachable
        assert "reauth" not in reachable      # the defect's key
        assert "agent inspect" not in reachable  # an ALIAS is not a canonical key
        assert "box" not in reachable         # a GROUP is not a leaf

    @pytest.mark.parametrize("option, declared", [
        ("--agent", AGENT_FLAG_COMMANDS),
        ("--box", BOX_FLAG_COMMANDS),
    ])
    def test_no_declared_key_is_unreachable(self, parser, option, declared):
        unreachable = sorted(set(declared) - _canonical_leaf_keys(parser))
        assert unreachable == [], (
            f"{option} is declared for commands that cannot be typed: "
            f"{unreachable}"
        )

    @pytest.mark.parametrize("option", ["--agent", "--box"])
    def test_the_refusal_a_user_reads_names_only_typeable_commands(
        self, parser, option,
    ):
        # The rendered surface, not the set: ``rig list`` takes neither flag, so
        # both messages enumerate their whole set in parentheses.
        with pytest.raises(FlagRelevanceError) as excinfo:
            check_flag_relevance(_parse(parser, ["rig", "list", option, "foo"]))
        message = str(excinfo.value)
        listed = re.search(r"\(([^()]*)\)", message).group(1).split(", ")
        assert len(listed) > 1  # vacuity: the enumeration was actually found
        assert sorted(set(listed) - _canonical_leaf_keys(parser)) == []


class TestBlanketFlagsAreAdvertisedOnlyWhereTheyApply:
    """A leaf that would REFUSE a blanket flag must not offer it in --help.

    ⚑ Derived, never hand-listed: the property is asserted over the WHOLE parser
    tree against the declared sets, so a new command cannot reintroduce the
    defect silently — it either joins the declared set or is born suppressed.
    """

    @pytest.mark.parametrize("option, declared, extra", [
        ("--agent", AGENT_FLAG_COMMANDS, _AGENT_FLAG_EXCLUDE),
        ("--box", BOX_FLAG_COMMANDS, frozenset()),
    ])
    def test_no_leaf_advertises_a_flag_it_would_refuse(
        self, parser, option, declared, extra,
    ):
        leaves = _leaves(parser)
        allowed = _alias_closure(leaves, set(declared) | set(extra))
        advertised = {
            key for key, leaf in leaves.items()
            if (act := _action_for(leaf, option)) is not None
            and act.help != argparse.SUPPRESS
        }
        assert advertised <= allowed, (
            f"{option} is advertised by leaves that refuse it: "
            f"{sorted(advertised - allowed)}"
        )

    @pytest.mark.parametrize("option", ["--agent", "--box"])
    def test_every_leaf_still_parses_the_flag(self, parser, option):
        # Suppressing the HELP must not stop the flag PARSING: that is what
        # keeps check_flag_relevance's enumerating message reachable instead of
        # argparse's bare "unrecognized arguments".
        leaves = _leaves(parser)
        missing = [k for k, p in leaves.items() if _action_for(p, option) is None]
        assert missing == []

    @pytest.mark.parametrize("option, declared", [
        ("--agent", AGENT_FLAG_COMMANDS),
        ("--box", BOX_FLAG_COMMANDS),
    ])
    def test_the_commands_that_take_it_still_advertise_it(
        self, parser, option, declared,
    ):
        # The other direction: suppressing everything would also pass the test
        # above.  ⚑ A declared key with no leaf is skipped rather than failed —
        # the sets are the relevance authority, not a command inventory.
        leaves = _leaves(parser)
        silent = [
            key for key in sorted(declared)
            if (leaf := leaves.get(key)) is not None
            and (act := _action_for(leaf, option)) is not None
            and act.help == argparse.SUPPRESS
        ]
        assert silent == []

    @pytest.mark.parametrize("argv, option", [
        (["box", "get", "--help"], "--agent"),
        (["system", "get", "--help"], "--agent"),
        (["rig", "list", "--help"], "--agent"),
        (["box", "set", "--help"], "--agent"),
        (["system", "get", "--help"], "--box"),
        (["setup", "--help"], "--box"),
    ])
    def test_help_text_does_not_name_the_flag(
        self, parser, capsys, argv, option,
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
        assert option not in capsys.readouterr().out

    @pytest.mark.parametrize("argv, option", [
        (["start", "--help"], "--agent"),
        (["box", "start", "--help"], "--box"),
        (["setup", "--help"], "--agent"),
    ])
    def test_help_text_still_names_the_flag_where_it_applies(
        self, parser, capsys, argv, option,
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
        assert option in capsys.readouterr().out

    @pytest.mark.parametrize("argv, option", [
        (["box", "get", "--agent", "claude", "model"], "--agent"),
        (["system", "get", "--agent", "claude", "model"], "--agent"),
        (["rig", "list", "--box", "foo"], "--box"),
    ])
    def test_a_suppressed_flag_still_reaches_the_relevance_error(
        self, parser, argv, option,
    ):
        # Not argparse's "unrecognized arguments": the enumerating message that
        # tells the user where the flag DOES apply.
        with pytest.raises(FlagRelevanceError) as excinfo:
            check_flag_relevance(_parse(parser, argv))
        assert option in str(excinfo.value)


# ---------------------------------------------------------------------------
# Help honesty: --agent says whether the choice is SAVED
# ---------------------------------------------------------------------------

def _handler_persists_the_agent(func):
    """True if *func* WRITES the box's agent setting through the settings seam.

    ⚑ The CALL, not the key name: ``run_reauth`` names ``pref.system.agent`` in a
    message it prints, and a substring match would read that as a write.  Only
    the leaf handler's own source is scanned — the write is the verb's own act,
    and one that moved into a helper should re-state its claim here deliberately
    rather than be followed to silently.
    """
    if func is None:
        return False
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        return False
    return re.search(
        r'set_config_value\(\s*["\']pref\.system\.agent["\']', source,
    ) is not None


class TestTheAgentFlagHelpSaysWhetherItSticks:
    """``kanibako create --help`` told the user the agent was "not persisted".

    It is: ``run_create`` writes the §2h request ``pref.system.agent`` into the
    new box's own settings, so the box keeps that agent and a later bare
    ``kanibako start`` launches it.  ONE help string served all five members of
    ``AGENT_FLAG_COMMANDS``, so the launch pair's truth was printed as create's —
    and a reader built instructions on the false half.  The claim now splits on
    ``AGENT_FLAG_PERSISTS``.

    ⚑ Swept over the whole tree against the two constants, never hand-listed, so
    re-merging the strings reddens every row rather than one.  INVERT: put
    ``"start"`` into ``AGENT_FLAG_PERSISTS`` (or drop ``"create"``) and the
    wiring row names it; make the two constants say the same thing and the
    contradiction row reddens.
    """

    def test_the_persisting_set_is_part_of_the_declared_set(self):
        # A command that refuses --agent cannot persist one; and neither an
        # empty nor an all-of-it subset would test anything below.
        assert AGENT_FLAG_PERSISTS <= AGENT_FLAG_COMMANDS
        assert AGENT_FLAG_PERSISTS
        assert AGENT_FLAG_PERSISTS != AGENT_FLAG_COMMANDS

    def test_the_two_claims_contradict_each_other(self):
        per_run = AGENT_FLAG_HELP_PER_RUN.lower()
        persisted = AGENT_FLAG_HELP_PERSISTED.lower()
        assert "not saved" in per_run
        assert "not saved" not in persisted
        assert "save" in persisted

    def test_the_persisting_set_is_what_the_handlers_actually_do(self, parser):
        """The set is a CLAIM about handlers, so the handlers settle it."""
        leaves = _leaves(parser)
        canonical = _alias_keys(parser)
        # Vacuity: a true AND a false case, so a scan that matched nothing (or
        # everything) cannot pass the sweep below.
        assert _handler_persists_the_agent(
            leaves["box create"].get_default("func"))
        assert not _handler_persists_the_agent(
            leaves["start"].get_default("func"))
        disagree = set()
        for key in AGENT_FLAG_COMMANDS:
            leaf = leaves.get(key)
            if leaf is None:
                continue
            canon = canonical.get(key, key)
            persists = _handler_persists_the_agent(leaf.get_default("func"))
            if persists != (canon in AGENT_FLAG_PERSISTS):
                disagree.add(canon)
        assert sorted(disagree) == []

    def test_every_advertised_leaf_carries_the_claim_that_fits_it(self, parser):
        leaves = _leaves(parser)
        canonical = _alias_keys(parser)
        persisting = _alias_closure(leaves, AGENT_FLAG_PERSISTS)
        wrong: list[str] = []
        saw_persisted: set[str] = set()
        saw_per_run: set[str] = set()
        for key, leaf in leaves.items():
            # setup owns a LOCAL --agent with its own help; the blanket flag is
            # never injected there, so there is no claim of ours to check.
            if canonical.get(key, key) in _AGENT_FLAG_EXCLUDE:
                continue
            act = _action_for(leaf, "--agent")
            if act is None or act.help == argparse.SUPPRESS:
                continue
            if key in persisting:
                saw_persisted.add(key)
                expected = AGENT_FLAG_HELP_PERSISTED
            else:
                saw_per_run.add(key)
                expected = AGENT_FLAG_HELP_PER_RUN
            if act.help != expected:
                wrong.append(key)
        assert sorted(wrong) == []
        # Vacuity: both claims were really exercised by the sweep.
        assert saw_persisted and saw_per_run

    @pytest.mark.parametrize("argv", [
        ["create", "--help"],
        ["box", "create", "--help"],
    ])
    def test_create_help_does_not_deny_that_it_saves(self, parser, capsys, argv):
        # The rendered surface a user reads, unwrapped: this is the exact text
        # that misled one.
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
        text = " ".join(capsys.readouterr().out.split()).lower()
        assert "not saved" not in text
        assert "not persisted" not in text
        assert "save" in text

    @pytest.mark.parametrize("argv", [
        ["start", "--help"],
        ["box", "start", "--help"],
        ["agent", "reauth", "--help"],
    ])
    def test_the_per_run_verbs_still_say_the_choice_is_not_saved(
        self, parser, capsys, argv,
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
        text = " ".join(capsys.readouterr().out.split()).lower()
        assert "this run only" in text
        assert "not saved" in text


# ---------------------------------------------------------------------------
# resolve_subject_value (positional + --box reconcile)
# ---------------------------------------------------------------------------

class TestResolveSubjectValue:
    def test_only_positional(self):
        assert resolve_subject_value("a", None) == "a"

    def test_only_box(self):
        assert resolve_subject_value(None, "b") == "b"

    def test_neither(self):
        assert resolve_subject_value(None, None) is None

    def test_same_warns_and_continues(self, capsys):
        assert resolve_subject_value("x", "x") == "x"
        assert "match" in capsys.readouterr().err.lower()

    def test_differ_errors(self):
        with pytest.raises(SubjectConflictError):
            resolve_subject_value("a", "b")

    def test_conflict_is_kanibako_error(self):
        from kanibako.errors import KanibakoError
        with pytest.raises(KanibakoError):
            resolve_subject_value("a", "b")


class TestBothLegsOfCodeReconcileTheSameWay:
    """Declaring ``code`` made a second leg reachable; it must reconcile alike.

    ``run_code`` returns early for ``--remote``, and that leg picked its subject
    with a plain ``or`` — the positional silently winning over a DIFFERENT
    ``--box``.  Nothing could reach it while the relevance table refused
    ``--box`` for ``code``, so declaring the command would have turned a hard
    error into a silent drop on exactly one sub-path.  Both legs now route
    through :func:`resolve_subject_value`.  INVERT: restore the ``or`` in
    ``_run_code_remote`` and the conflict row reddens.
    """

    def _args(self, **kw):
        base = {"project": None, "box": None, "remote": None}
        return argparse.Namespace(**{**base, **kw})

    def test_remote_leg_errors_on_a_conflicting_subject(self):
        from kanibako.commands.code_cmd import run_code
        with pytest.raises(SubjectConflictError):
            run_code(self._args(project="mybox", box="otherbox", remote="h"))

    def test_local_leg_errors_on_a_conflicting_subject(self):
        from kanibako.commands.code_cmd import run_code
        with pytest.raises(SubjectConflictError):
            run_code(self._args(project="mybox", box="otherbox"))

    def test_remote_leg_still_requires_a_subject(self, capsys):
        # Unchanged: --remote has no cwd to fall back on.
        from kanibako.commands.code_cmd import run_code
        assert run_code(self._args(remote="h")) == 1
        assert "requires a box" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# B-5 — a flag parses in ANY position (option-anywhere parsing)
# ---------------------------------------------------------------------------

# Each row is (verb argv-prefix, the positional payload, the namespace attrs
# that must be identical whatever the flag's position).  ``box set`` is the one
# with the variadic positional — the shape that used to break — but the rule is
# asserted for EVERY verb that carries --null so no verb can drift.
_SET_VERBS = [
    pytest.param(
        ["box", "set"], ["mybox", "pref.system.agent"], {"args": ["mybox", "pref.system.agent"]},
        id="box-set",
    ),
    pytest.param(
        ["workset", "set"], ["myws", "model"], {"workset": "myws", "key_value": "model"},
        id="workset-set",
    ),
    pytest.param(
        ["system", "set"], ["model"], {"key_value": "model"},
        id="system-set",
    ),
    pytest.param(
        ["agent", "set"], ["myagent", "model"], {"agent_id": "myagent", "key_value": "model"},
        id="agent-set",
    ),
]


def _orderings(payload: list[str], flag: str) -> list[list[str]]:
    """Every position *flag* can occupy relative to *payload* (leading, each
    interior slot, trailing)."""
    return [payload[:i] + [flag] + payload[i:] for i in range(len(payload) + 1)]


class TestNullFlagParsesInEveryPosition:
    """B-5: ``--null`` used to be positional-sensitive.

    ``box set <box> --null <key>`` died with "unrecognized arguments: <key>"
    because argparse matches positionals in GROUPS split by the optionals
    between them, and the variadic ``args`` positional swallowed the first
    group.  INVERT: drop the ``parser_class=OptionsAnywhereParser`` in
    ``build_parser`` and the interior orderings redden.
    """

    @pytest.mark.parametrize("verb,payload,expected", _SET_VERBS)
    def test_null_in_every_position(self, parser, verb, payload, expected):
        for argv in _orderings(payload, "--null"):
            args = _parse(parser, verb + argv)
            assert args.null is True, argv
            for attr, value in expected.items():
                assert getattr(args, attr) == value, argv

    def test_the_bifrost_invocation(self, parser):
        """The exact spelling the P7 e2e report recorded as broken."""
        args = _parse(parser, ["box", "set", "mybox", "--null", "pref.system.agent"])
        assert args.null is True
        assert args.args == ["mybox", "pref.system.agent"]

    @pytest.mark.parametrize(
        "verb,payload,expected",
        # ``agent set`` is the one set verb with no --force; the others carry it.
        [p for p in _SET_VERBS if p.id != "agent-set"],
    )
    def test_force_in_every_position(self, parser, verb, payload, expected):
        """The rule is about OPTIONALS, not about ``--null`` specifically."""
        for argv in _orderings(payload, "--force"):
            args = _parse(parser, verb + argv)
            assert args.force is True, argv
            for attr, value in expected.items():
                assert getattr(args, attr) == value, argv


class TestOptionsAnywhereOnTheOtherBoxConfigVerbs:
    """``get``/``reset``/``show`` share ``box set``'s variadic positional, so
    they had the same defect and get the same guarantee."""

    def test_reset_takes_force_between_positionals(self, parser):
        args = _parse(parser, ["box", "reset", "mybox", "--force", "pref.x"])
        assert args.force is True
        assert args.args == ["mybox", "pref.x"]

    def test_get_takes_box_flag_between_positionals(self, parser):
        args = _parse(parser, ["box", "get", "mybox", "--box", "other", "pref.x"])
        assert args.box == "other"
        assert args.args == ["mybox", "pref.x"]

    def test_show_effective_after_the_positional(self, parser):
        args = _parse(parser, ["box", "show", "mybox", "--effective"])
        assert args.effective is True
        assert args.args == ["mybox"]


class TestValueTakingFlagsKeepTheirValues:
    """A hoisted optional must carry its value with it — reordering that split
    ``--box other`` would bind the wrong string."""

    def test_agent_flag_between_positionals(self, parser):
        args = _parse(parser, ["box", "set", "mybox", "--agent", "claude", "pref.x=1"])
        assert args.agent == "claude"
        assert args.args == ["mybox", "pref.x=1"]

    def test_attached_value_form(self, parser):
        args = _parse(parser, ["box", "set", "mybox", "--box=other", "pref.x=1"])
        assert args.box == "other"
        assert args.args == ["mybox", "pref.x=1"]

    def test_long_option_abbreviation(self, parser):
        """argparse honours unambiguous abbreviations, so the rewrite must too —
        otherwise ``--nul`` would work before the positionals and not after."""
        args = _parse(parser, ["box", "set", "mybox", "--nul", "pref.x"])
        assert args.null is True
        assert args.args == ["mybox", "pref.x"]

    def test_double_dash_ends_the_rewrite(self, parser):
        """After ``--`` a flag-looking token is DATA and must stay a positional."""
        args = _parse(parser, ["box", "set", "--", "--null", "pref.x"])
        assert args.null is False
        assert args.args == ["--null", "pref.x"]


class TestMissingOptionValueStillErrors:
    """The rewrite must not turn argparse's errors into silent misparses.

    A value-taking option with nothing after it would, if hoisted bare, land in
    front of the positionals and swallow the FIRST one as its value — exit 0
    with the wrong subject instead of "expected one argument".  Both cases are
    the Editor's fuzz repros (463/30k differential hits).
    """

    def test_trailing_value_flag_on_show(self, parser):
        with pytest.raises(SystemExit) as exc:
            _parse(parser, ["box", "show", "mybox", "--box"])
        assert exc.value.code == 2

    def test_trailing_value_flag_on_set(self, parser):
        with pytest.raises(SystemExit) as exc:
            _parse(parser, ["box", "set", "mybox", "pref.x", "--agent"])
        assert exc.value.code == 2

    def test_the_subject_is_not_silently_stolen(self, parser, capsys):
        """The specific wrong outcome: ``mybox`` bound to --box, args empty."""
        with pytest.raises(SystemExit):
            _parse(parser, ["box", "show", "mybox", "--box"])
        assert "expected one argument" in capsys.readouterr().err

    def test_a_double_dash_in_the_value_slot_bails(self):
        """``--val --`` has no real value; leave argv to argparse rather than
        binding the separator as data."""
        import argparse as ap

        from kanibako.commands.flags import hoist_optionals

        p = ap.ArgumentParser()
        p.add_argument("args", nargs="*")
        p.add_argument("--val")
        argv = ["a", "--val", "--", "b"]
        assert hoist_optionals(p, argv) == argv


class TestSplittableParsersThatWereNeverBroken:
    """The guard is deliberately WIDER than the set of broken shapes.

    ⚑ These two parsers were always fine, on both versions: their positionals
    are all REQUIRED single-token, so no zero-width match can land in the first
    group and 3.11 defers the tail correctly by itself.  They are rewritten now
    only because :func:`_splits_positionals` asks the simple, version-independent
    question "could an optional split these?" rather than trying to predict
    which shapes which CPython mishandles.  That makes them the regression test
    FOR THE WIDENING — the rewrite must leave already-correct parsers alone.
    """

    def test_box_move_with_a_flag_between_its_two_positionals(self, parser):
        args = _parse(parser, ["box", "move", "old", "--force", "new"])
        assert args.force is True
        assert (args.old, args.new) == ("old", "new")

    def test_workset_disconnect_with_a_flag_between(self, parser):
        args = _parse(parser, ["workset", "disconnect", "myws", "--force", "proj"])
        assert args.force is True
        assert (args.workset, args.project) == ("myws", "proj")


@pytest.fixture
def argparse_311_matching(monkeypatch):
    """Force Python 3.11's ``_match_arguments_partial`` semantics.

    CPython 3.13 strips a ZERO-WIDTH trailing positional out of the first group
    when the next pattern token is an optional; 3.11 does not.  That one
    difference is why three of these orderings passed locally on 3.13 and failed
    in CI on 3.11.  Restoring the OLD behaviour here exercises the declared
    support floor (``requires-python = ">=3.11"``) on whatever interpreter
    happens to run the suite, so the gap cannot reopen unnoticed.
    """
    import re

    def _match_arguments_partial_311(self, actions, arg_strings_pattern):
        result = []
        for i in range(len(actions), 0, -1):
            actions_slice = actions[:i]
            pattern = "".join(
                self._get_nargs_pattern(action) for action in actions_slice
            )
            match = re.match(pattern, arg_strings_pattern)
            if match is not None:
                result.extend([len(string) for string in match.groups()])
                break
        return result

    monkeypatch.setattr(
        argparse.ArgumentParser,
        "_match_arguments_partial",
        _match_arguments_partial_311,
    )


class TestSupportFloorIsPython311:
    """Pin the FLOOR, not the dev box.  ``requires-python = ">=3.11"`` and CI
    pins 3.11; 3.13 repaired this in argparse itself, so a green 3.13 run proves
    nothing about the version we actually ship against."""

    def test_the_simulation_is_faithful(self, argparse_311_matching):
        """A BARE argparse parser with ``workset set``'s exact shape FAILS under
        floor semantics.  This is the defect itself — and it proves the fixture
        bites rather than silently agreeing with 3.13."""
        bare = argparse.ArgumentParser(prog="bare")
        bare.add_argument("workset")
        bare.add_argument("key_value", nargs="?")
        bare.add_argument("--null", action="store_true")
        with pytest.raises(SystemExit) as exc:
            bare.parse_args(["myws", "--null", "model"])
        assert exc.value.code == 2

    @pytest.mark.parametrize("verb,payload,expected", _SET_VERBS)
    def test_null_in_every_position_on_the_floor(
        self, parser, argparse_311_matching, verb, payload, expected,
    ):
        """The three CI failures, pinned: workset-set and agent-set are the rows
        that reddened on 3.11."""
        for argv in _orderings(payload, "--null"):
            args = _parse(parser, verb + argv)
            assert args.null is True, argv
            for attr, value in expected.items():
                assert getattr(args, attr) == value, argv

    def test_an_all_required_shape_is_correct_on_the_floor_too(
        self, parser, argparse_311_matching,
    ):
        """``box move`` never had the defect (no zero-width positional to strand)
        — asserted here so the floor row records which shapes were and were not
        affected, rather than implying every split parser was broken."""
        args = _parse(parser, ["box", "move", "old", "--force", "new"])
        assert (args.old, args.new) == ("old", "new")


class TestHoistOptionalsIsInertWhereItShouldBe:
    def test_no_variadic_positional_returns_argv_untouched(self):
        import argparse

        from kanibako.commands.flags import hoist_optionals

        p = argparse.ArgumentParser()
        p.add_argument("name")
        p.add_argument("--flag", action="store_true")
        argv = ["a", "--flag"]
        assert hoist_optionals(p, argv) == argv

    def test_remainder_positional_returns_argv_untouched(self):
        """REMAINDER deliberately captures raw argv; reordering would corrupt it."""
        import argparse

        from kanibako.commands.flags import hoist_optionals

        p = argparse.ArgumentParser()
        p.add_argument("args", nargs="*")
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.add_argument("--flag", action="store_true")
        argv = ["a", "--flag", "b"]
        assert hoist_optionals(p, argv) == argv

    def test_nothing_is_dropped_or_duplicated(self):
        import argparse

        from kanibako.commands.flags import hoist_optionals

        p = argparse.ArgumentParser()
        p.add_argument("args", nargs="*")
        p.add_argument("--flag", action="store_true")
        p.add_argument("--val")
        argv = ["a", "--flag", "b", "--val", "v", "c"]
        out = hoist_optionals(p, argv)
        assert sorted(out) == sorted(argv)
        assert out == ["--flag", "--val", "v", "a", "b", "c"]


# ---------------------------------------------------------------------------
# B-6 — the --null help text is the discoverability surface for suppression
# ---------------------------------------------------------------------------

def _null_help(parser, verb):
    sub = parser
    for name in verb:
        action = next(
            a for a in sub._actions if isinstance(a, argparse._SubParsersAction)
        )
        sub = action.choices[name]
    return next(a for a in sub._actions if "--null" in a.option_strings).help


# (verb, its own reset spelling, a CONCRETE argv for that spelling).  The third
# column is what makes the second honest: an undo example the user cannot type
# teaches the opposite of what it is for.
_UNDO_EXAMPLES = [
    pytest.param(
        ["box", "set"], "box reset [project] <key>",
        ["box", "reset", "myproj", "model"], id="box",
    ),
    pytest.param(
        ["workset", "set"], "workset reset <workset> <key>",
        ["workset", "reset", "myws", "model"], id="workset",
    ),
    pytest.param(
        ["system", "set"], "system reset <key>",
        ["system", "reset", "model"], id="system",
    ),
    pytest.param(
        ["agent", "set"], "agent reset <agent> <key>",
        ["agent", "reset", "claude", "model"], id="agent",
    ),
]


class TestNullFlagHelpTeachesSuppression:
    """Jei rejected a dedicated suppress verb as "unnecessary and redundant"
    (2026-07-31), which makes this help string the ONLY place the feature is
    taught.  It must say what --null does AND what undoes it."""

    def test_help_says_it_suppresses(self):
        from kanibako.commands.flags import NULL_FLAG_HELP_TEMPLATE

        assert "SUPPRESS" in NULL_FLAG_HELP_TEMPLATE
        assert "inherit" in NULL_FLAG_HELP_TEMPLATE

    def test_help_names_reset_as_the_undo(self):
        from kanibako.commands.flags import NULL_FLAG_HELP_TEMPLATE

        assert "'reset' verb" in NULL_FLAG_HELP_TEMPLATE
        # ⚑ The VERB, never a flag: no parser defines ``--reset``.
        assert "--reset" not in NULL_FLAG_HELP_TEMPLATE

    @pytest.mark.parametrize("verb,undo,_argv", _UNDO_EXAMPLES)
    def test_every_verb_uses_the_one_sentence(self, parser, verb, undo, _argv):
        """Single source: four copies of one sentence are four chances to drift.
        Only the undo EXAMPLE varies."""
        from kanibako.commands.flags import NULL_FLAG_HELP_TEMPLATE

        assert _null_help(parser, verb) == NULL_FLAG_HELP_TEMPLATE.format(undo=undo)

    @pytest.mark.parametrize("verb,undo,argv", _UNDO_EXAMPLES)
    def test_the_undo_example_is_a_command_the_user_can_type(
        self, parser, verb, undo, argv,
    ):
        """The example's shape must match the verb's real positionals — a shared
        'reset <key>' would be untypeable for workset/agent (they require their
        noun) and wrong for box."""
        assert undo in _null_help(parser, verb)
        args = _parse(parser, argv)          # the concrete form parses
        assert args.func.__name__ == "run_reset"
        # The placeholders in the example line up with the concrete argv.
        assert undo.split()[:2] == argv[:2]
        assert len(undo.split()) == len(argv)

    def test_no_parser_defines_a_reset_flag(self, parser):
        """The claim the help text makes about ``reset`` being a VERB, pinned."""
        seen: list[str] = []

        def walk(p, prefix):
            for a in p._actions:
                if "--reset" in a.option_strings:
                    seen.append(" ".join(prefix))
                if isinstance(a, argparse._SubParsersAction):
                    for name, child in a.choices.items():
                        walk(child, (*prefix, name))

        walk(parser, ())
        assert seen == []

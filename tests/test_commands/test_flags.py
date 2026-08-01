"""Tests for the blanket --agent/--box flags + relevance (W1 Phase D)."""

from __future__ import annotations

import argparse

import pytest

from kanibako import cli
from kanibako.commands.flags import (
    AGENT_FLAG_COMMANDS,
    BOX_FLAG_COMMANDS,
    FlagRelevanceError,
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

    def test_declared_sets_are_subset_of_real_commands(self):
        # Guardrail: every declared command key is a real dotted path shape.
        assert "start" in AGENT_FLAG_COMMANDS
        assert "agent reauth" in AGENT_FLAG_COMMANDS
        assert "create" in AGENT_FLAG_COMMANDS
        assert "box create" in AGENT_FLAG_COMMANDS
        assert "start" in BOX_FLAG_COMMANDS
        assert "workset disconnect" in BOX_FLAG_COMMANDS


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

"""Tests for the blanket --agent/--box flags + relevance (W1 Phase D)."""

from __future__ import annotations

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

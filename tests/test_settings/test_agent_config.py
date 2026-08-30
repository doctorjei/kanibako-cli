"""Tests for kanibako.settings.agent_config: the AgentConfig record and the store's paths.

⚑ The FILE-SHAPE tests moved with their code to ``test_agent_file.py`` (S1): every load /
write / route case belongs with :mod:`kanibako.settings.agent_file`, which owns the shape.
What is left here touches no file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.agent_config import (
    AgentConfig,
    agent_config_path,
    agent_settings_path,
    agents_dir,
    ambiguous_path_value_error,
    is_self_resolving,
    is_unambiguous_path_value,
)


class TestAgentConfigDefaults:
    def test_defaults(self):
        cfg = AgentConfig()
        assert cfg.name == ""
        assert cfg.run_args == []
        assert cfg.state == {}
        assert cfg.env == {}
        assert cfg.secret_path == {}
        assert cfg.category_tables == {}

    def test_custom_values(self):
        cfg = AgentConfig(
            name="Claude Code",
            run_args=["--verbose"],
            state={"access": "permissive"},
            env={"FOO": "bar"},
        )
        assert cfg.name == "Claude Code"
        assert cfg.run_args == ["--verbose"]
        assert cfg.state == {"access": "permissive"}
        assert cfg.env == {"FOO": "bar"}

    def test_secret_path_custom_value(self):
        cfg = AgentConfig(
            secret_path={"ANTHROPIC_AUTH_TOKEN": "~/.config/claude/nav/token"}
        )
        assert cfg.secret_path == {
            "ANTHROPIC_AUTH_TOKEN": "~/.config/claude/nav/token"
        }


class TestAgentsDir:
    def test_default(self, tmp_path):
        result = agents_dir(tmp_path)
        assert result == tmp_path / "agents"

    def test_custom(self, tmp_path):
        result = agents_dir(tmp_path, "my-crabs")
        assert result == tmp_path / "my-crabs"

    def test_empty_fallback(self, tmp_path):
        result = agents_dir(tmp_path, "")
        assert result == tmp_path / "agents"


class TestAgentConfigPath:
    def test_path(self, tmp_path):
        # Per-agent settings live INSIDE the store dir: agents/<agent>/agent.yaml
        result = agent_config_path(tmp_path, "claude")
        assert result == tmp_path / "agents" / "claude" / "agent.yaml"

    def test_custom_agents_dir(self, tmp_path):
        result = agent_config_path(tmp_path, "claude", "my-crabs")
        assert result == tmp_path / "my-crabs" / "claude" / "agent.yaml"

    def test_general_agent(self, tmp_path):
        result = agent_config_path(tmp_path, "general")
        assert result == tmp_path / "agents" / "general" / "agent.yaml"


class TestAgentSettingsPath:
    def test_path(self, tmp_path):
        agents_root = tmp_path / "agents"
        result = agent_settings_path(agents_root, "claude")
        assert result == agents_root / "claude" / "agent.yaml"

    def test_general(self, tmp_path):
        agents_root = tmp_path / "agents"
        result = agent_settings_path(agents_root, "general")
        assert result == agents_root / "general" / "agent.yaml"


class TestUnambiguousPathValue:
    """[R147]'s predicate, and the ONE way it differs from ``is_self_resolving``.

    ⚑ THE DIFFERENCE IS THE POINT, so it is asserted as a CONTRAST rather than as two
    independent tables: ``is_self_resolving`` rules on a bind SOURCE, where a
    declaration may name any variable the launch namespace supplies; this one rules on
    a path a USER typed, where a non-XDG variable expands to a bare NAME and leaves the
    value exactly as relative as it started.
    """

    @pytest.mark.parametrize("value", [
        "/abs", "\\/abs", "~/x", "~", "$XDG_DATA_HOME/x", "${XDG_CACHE_HOME}/x",
        "@meta.workset.path/x", "@{config.data}/x",
    ])
    def test_legal_shapes(self, value):
        assert is_unambiguous_path_value(value) is True

    @pytest.mark.parametrize("value", [
        "leaf", "sub/dir", "./here", "../sibling", "\\~foo", "",
    ])
    def test_ambiguous_shapes(self, value):
        assert is_unambiguous_path_value(value) is False

    @pytest.mark.parametrize("value", ["$AGENT/logs", "$WORKSET/x", "$HOME/x"])
    def test_a_non_xdg_variable_is_a_source_root_but_not_a_path_value(self, value):
        # ⚑ The contrast that justifies a second predicate existing at all.
        assert is_self_resolving(value) is True
        assert is_unambiguous_path_value(value) is False


class TestAmbiguousPathValueError:
    def test_it_names_both_readings_and_the_file(self):
        message = ambiguous_path_value_error(
            "workset.channelroot", "comms",
            anchor="/ws", anchor_ref="@meta.workset.path", where="/ws/workset.yaml",
        )
        assert "workset.channelroot" in message
        assert "'comms'" in message
        assert "/ws/comms" in message
        assert str(Path.cwd() / "comms") in message
        assert "@meta.workset.path/comms" in message
        assert "/ws/workset.yaml" in message

    def test_the_optional_halves_are_optional(self):
        message = ambiguous_path_value_error("system.cache", "c", anchor="/data")
        assert "/data/c" in message
        assert str(Path.cwd() / "c") in message

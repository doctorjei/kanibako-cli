"""Tests for kanibako.settings.agent_config: the AgentConfig record and the store's paths.

⚑ The FILE-SHAPE tests moved with their code to ``test_agent_file.py`` (S1): every load /
write / route case belongs with :mod:`kanibako.settings.agent_file`, which owns the shape.
What is left here touches no file.
"""

from __future__ import annotations

from kanibako.settings.agent_config import (
    AgentConfig,
    agent_config_path,
    agent_settings_path,
    agents_dir,
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

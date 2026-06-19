"""Tests for kanibako.agent_config: AgentConfig, load/write agent YAML."""

from __future__ import annotations

from kanibako.agent_config import (
    AgentConfig,
    agent_config_path,
    agents_dir,
    load_agent_config,
    write_agent_config,
)


class TestAgentConfigDefaults:
    def test_defaults(self):
        cfg = AgentConfig()
        assert cfg.name == ""
        assert cfg.shell == "standard"
        assert cfg.run_args == []
        assert cfg.state == {}
        assert cfg.env == {}
        assert cfg.shared_caches == {}

    def test_custom_values(self):
        cfg = AgentConfig(
            name="Claude Code",
            shell="minimal",
            run_args=["--verbose"],
            state={"access": "permissive"},
            env={"FOO": "bar"},
            shared_caches={"plugins": ".claude/plugins"},
        )
        assert cfg.name == "Claude Code"
        assert cfg.shell == "minimal"
        assert cfg.run_args == ["--verbose"]
        assert cfg.state == {"access": "permissive"}
        assert cfg.env == {"FOO": "bar"}
        assert cfg.shared_caches == {"plugins": ".claude/plugins"}


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
        result = agent_config_path(tmp_path, "claude")
        assert result == tmp_path / "agents" / "claude.yaml"

    def test_custom_agents_dir(self, tmp_path):
        result = agent_config_path(tmp_path, "claude", "my-crabs")
        assert result == tmp_path / "my-crabs" / "claude.yaml"

    def test_general_agent(self, tmp_path):
        result = agent_config_path(tmp_path, "general")
        assert result == tmp_path / "agents" / "general.yaml"


class TestLoadAgentConfig:
    def test_nonexistent_file_returns_defaults(self, tmp_path):
        cfg = load_agent_config(tmp_path / "missing.yaml")
        assert cfg.name == ""
        assert cfg.shell == "standard"
        assert cfg.run_args == []

    def test_load_all_sections(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'crab:\n'
            '  name: "Claude Code"\n'
            '  shell: "minimal"\n'
            '  run_args: ["--verbose", "--debug"]\n'
            '  model: "opus"\n'
            '  access: "permissive"\n'
            'env:\n'
            '  MY_VAR: "hello"\n'
            'shared:\n'
            '  plugins: ".claude/plugins"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.name == "Claude Code"
        assert cfg.shell == "minimal"
        assert cfg.run_args == ["--verbose", "--debug"]
        assert cfg.state == {"model": "opus", "access": "permissive"}
        assert cfg.env == {"MY_VAR": "hello"}
        assert cfg.shared_caches == {"plugins": ".claude/plugins"}

    def test_load_crab_section_only(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'crab:\n'
            '  name: "Shell"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.name == "Shell"
        assert cfg.shell == "standard"
        assert cfg.run_args == []
        assert cfg.state == {}
        assert cfg.env == {}
        assert cfg.shared_caches == {}

    def test_load_state_keys_without_identity(self, tmp_path):
        # [crab] with only state keys (no identity keys) → all land in state.
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'crab:\n'
            '  access: "safe"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.name == ""
        assert cfg.state == {"access": "safe"}

    def test_load_missing_crab_section(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'env:\n'
            '  FOO: "bar"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.name == ""
        assert cfg.state == {}
        assert cfg.env == {"FOO": "bar"}

    def test_load_empty_file(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text("")
        cfg = load_agent_config(cfg_path)
        assert cfg.name == ""
        assert cfg.shell == "standard"

    def test_run_args_must_be_list(self, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        cfg_path.write_text(
            'crab:\n'
            '  run_args: "not-a-list"\n'
        )
        cfg = load_agent_config(cfg_path)
        assert cfg.run_args == []


class TestWriteAgentConfig:
    def test_write_defaults(self, tmp_path):
        path = tmp_path / "agents" / "test.yaml"
        cfg = AgentConfig()
        write_agent_config(path, cfg)

        assert path.exists()
        content = path.read_text()
        assert 'crab:' in content
        assert 'state:' not in content
        assert 'env:' in content
        assert 'shared:' in content

    def test_write_with_values(self, tmp_path):
        path = tmp_path / "test.yaml"
        cfg = AgentConfig(
            name="Claude Code",
            shell="standard",
            run_args=["--verbose"],
            state={"access": "permissive"},
            env={"FOO": "bar"},
            shared_caches={"plugins": ".claude/plugins"},
        )
        write_agent_config(path, cfg)

        loaded = load_agent_config(path)
        assert loaded.name == "Claude Code"
        assert loaded.shell == "standard"
        assert loaded.run_args == ["--verbose"]
        assert loaded.state == {"access": "permissive"}
        assert loaded.env == {"FOO": "bar"}
        assert loaded.shared_caches == {"plugins": ".claude/plugins"}

    def test_state_folded_into_crab_section(self, tmp_path):
        path = tmp_path / "test.yaml"
        cfg = AgentConfig(state={"access": "permissive"})
        write_agent_config(path, cfg)

        content = path.read_text()
        # No separate state section; state knobs live under crab.
        assert 'state:' not in content
        loaded = load_agent_config(path)
        assert loaded.state == {"access": "permissive"}

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "agent.yaml"
        write_agent_config(path, AgentConfig())
        assert path.exists()


class TestRoundTrip:
    def test_write_then_load(self, tmp_path):
        path = tmp_path / "test.yaml"
        original = AgentConfig(
            name="Claude Code",
            shell="minimal",
            run_args=["--verbose", "--debug"],
            state={"model": "opus", "access": "permissive"},
            env={"MY_VAR": "hello"},
            shared_caches={"plugins": ".claude/plugins"},
        )
        write_agent_config(path, original)
        loaded = load_agent_config(path)

        assert loaded.name == original.name
        assert loaded.shell == original.shell
        assert loaded.run_args == original.run_args
        assert loaded.state == original.state
        assert loaded.env == original.env
        assert loaded.shared_caches == original.shared_caches

    def test_round_trip_empty_config(self, tmp_path):
        path = tmp_path / "test.yaml"
        original = AgentConfig()
        write_agent_config(path, original)
        loaded = load_agent_config(path)

        assert loaded.name == ""
        assert loaded.shell == "standard"
        assert loaded.run_args == []
        assert loaded.state == {}
        assert loaded.env == {}
        assert loaded.shared_caches == {}

    def test_state_folded_into_single_crab_section(self, tmp_path):
        # Writing state must produce ONE crab section (identity + state),
        # with no separate state section, and load back intact.
        path = tmp_path / "test.yaml"
        original = AgentConfig(
            name="Claude Code",
            shell="minimal",
            run_args=["--verbose"],
            state={"model": "sonnet"},
        )
        write_agent_config(path, original)
        content = path.read_text()
        assert 'state:' not in content
        assert content.count("crab:") == 1
        assert 'name: Claude Code' in content
        assert 'shell: minimal' in content
        assert 'model: sonnet' in content

        loaded = load_agent_config(path)
        assert loaded.state == {"model": "sonnet"}
        assert loaded.name == "Claude Code"
        assert loaded.shell == "minimal"
        assert loaded.run_args == ["--verbose"]

    def test_round_trip_multiple_run_args(self, tmp_path):
        path = tmp_path / "test.yaml"
        original = AgentConfig(run_args=["--foo", "--bar", "baz"])
        write_agent_config(path, original)
        loaded = load_agent_config(path)
        assert loaded.run_args == ["--foo", "--bar", "baz"]

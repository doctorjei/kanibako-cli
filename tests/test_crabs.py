"""Tests for kanibako.crabs: CrabConfig, load/write crab TOML."""

from __future__ import annotations

from kanibako.crabs import (
    CrabConfig,
    crab_toml_path,
    crabs_dir,
    load_crab_config,
    write_crab_config,
)


class TestCrabConfigDefaults:
    def test_defaults(self):
        cfg = CrabConfig()
        assert cfg.name == ""
        assert cfg.shell == "standard"
        assert cfg.run_args == []
        assert cfg.state == {}
        assert cfg.env == {}
        assert cfg.shared_caches == {}

    def test_custom_values(self):
        cfg = CrabConfig(
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


class TestCrabsDir:
    def test_default(self, tmp_path):
        result = crabs_dir(tmp_path)
        assert result == tmp_path / "crabs"

    def test_custom(self, tmp_path):
        result = crabs_dir(tmp_path, "my-crabs")
        assert result == tmp_path / "my-crabs"

    def test_empty_fallback(self, tmp_path):
        result = crabs_dir(tmp_path, "")
        assert result == tmp_path / "crabs"


class TestCrabTomlPath:
    def test_path(self, tmp_path):
        result = crab_toml_path(tmp_path, "claude")
        assert result == tmp_path / "crabs" / "claude.toml"

    def test_custom_crabs_dir(self, tmp_path):
        result = crab_toml_path(tmp_path, "claude", "my-crabs")
        assert result == tmp_path / "my-crabs" / "claude.toml"

    def test_general_agent(self, tmp_path):
        result = crab_toml_path(tmp_path, "general")
        assert result == tmp_path / "crabs" / "general.toml"


class TestLoadCrabConfig:
    def test_nonexistent_file_returns_defaults(self, tmp_path):
        cfg = load_crab_config(tmp_path / "missing.toml")
        assert cfg.name == ""
        assert cfg.shell == "standard"
        assert cfg.run_args == []

    def test_load_all_sections(self, tmp_path):
        toml_path = tmp_path / "test.toml"
        toml_path.write_text(
            '[crab]\n'
            'name = "Claude Code"\n'
            'shell = "minimal"\n'
            'run_args = ["--verbose", "--debug"]\n'
            '\n'
            '[state]\n'
            'model = "opus"\n'
            'access = "permissive"\n'
            '\n'
            '[env]\n'
            'MY_VAR = "hello"\n'
            '\n'
            '[shared]\n'
            'plugins = ".claude/plugins"\n'
        )
        cfg = load_crab_config(toml_path)
        assert cfg.name == "Claude Code"
        assert cfg.shell == "minimal"
        assert cfg.run_args == ["--verbose", "--debug"]
        assert cfg.state == {"model": "opus", "access": "permissive"}
        assert cfg.env == {"MY_VAR": "hello"}
        assert cfg.shared_caches == {"plugins": ".claude/plugins"}

    def test_load_crab_section_only(self, tmp_path):
        toml_path = tmp_path / "test.toml"
        toml_path.write_text(
            '[crab]\n'
            'name = "Shell"\n'
        )
        cfg = load_crab_config(toml_path)
        assert cfg.name == "Shell"
        assert cfg.shell == "standard"
        assert cfg.run_args == []
        assert cfg.state == {}
        assert cfg.env == {}
        assert cfg.shared_caches == {}

    def test_load_missing_crab_section(self, tmp_path):
        toml_path = tmp_path / "test.toml"
        toml_path.write_text(
            '[state]\n'
            'access = "safe"\n'
        )
        cfg = load_crab_config(toml_path)
        assert cfg.name == ""
        assert cfg.state == {"access": "safe"}

    def test_load_empty_file(self, tmp_path):
        toml_path = tmp_path / "test.toml"
        toml_path.write_text("")
        cfg = load_crab_config(toml_path)
        assert cfg.name == ""
        assert cfg.shell == "standard"

    def test_run_args_must_be_list(self, tmp_path):
        toml_path = tmp_path / "test.toml"
        toml_path.write_text(
            '[crab]\n'
            'run_args = "not-a-list"\n'
        )
        cfg = load_crab_config(toml_path)
        assert cfg.run_args == []


class TestWriteCrabConfig:
    def test_write_defaults(self, tmp_path):
        path = tmp_path / "crabs" / "test.toml"
        cfg = CrabConfig()
        write_crab_config(path, cfg)

        assert path.exists()
        content = path.read_text()
        assert '[crab]' in content
        assert '[state]' in content
        assert '[env]' in content
        assert '[shared]' in content
        assert '# model = "opus"' in content

    def test_write_with_values(self, tmp_path):
        path = tmp_path / "test.toml"
        cfg = CrabConfig(
            name="Claude Code",
            shell="standard",
            run_args=["--verbose"],
            state={"access": "permissive"},
            env={"FOO": "bar"},
            shared_caches={"plugins": ".claude/plugins"},
        )
        write_crab_config(path, cfg)

        content = path.read_text()
        assert 'name = "Claude Code"' in content
        assert 'shell = "standard"' in content
        assert 'run_args = ["--verbose"]' in content
        assert 'access = "permissive"' in content
        assert 'FOO = "bar"' in content
        assert 'plugins = ".claude/plugins"' in content

    def test_model_commented_when_not_in_state(self, tmp_path):
        path = tmp_path / "test.toml"
        cfg = CrabConfig(state={"access": "permissive"})
        write_crab_config(path, cfg)

        content = path.read_text()
        assert '# model = "opus"' in content
        assert 'access = "permissive"' in content

    def test_model_not_commented_when_in_state(self, tmp_path):
        path = tmp_path / "test.toml"
        cfg = CrabConfig(state={"model": "sonnet"})
        write_crab_config(path, cfg)

        content = path.read_text()
        assert 'model = "sonnet"' in content
        # Should NOT have the comment line
        lines = content.split("\n")
        comment_lines = [line for line in lines if line.strip() == '# model = "opus"']
        assert len(comment_lines) == 0

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "agent.toml"
        write_crab_config(path, CrabConfig())
        assert path.exists()


class TestRoundTrip:
    def test_write_then_load(self, tmp_path):
        path = tmp_path / "test.toml"
        original = CrabConfig(
            name="Claude Code",
            shell="minimal",
            run_args=["--verbose", "--debug"],
            state={"model": "opus", "access": "permissive"},
            env={"MY_VAR": "hello"},
            shared_caches={"plugins": ".claude/plugins"},
        )
        write_crab_config(path, original)
        loaded = load_crab_config(path)

        assert loaded.name == original.name
        assert loaded.shell == original.shell
        assert loaded.run_args == original.run_args
        assert loaded.state == original.state
        assert loaded.env == original.env
        assert loaded.shared_caches == original.shared_caches

    def test_round_trip_empty_config(self, tmp_path):
        path = tmp_path / "test.toml"
        original = CrabConfig()
        write_crab_config(path, original)
        loaded = load_crab_config(path)

        assert loaded.name == ""
        assert loaded.shell == "standard"
        assert loaded.run_args == []
        assert loaded.state == {}
        assert loaded.env == {}
        assert loaded.shared_caches == {}

    def test_round_trip_multiple_run_args(self, tmp_path):
        path = tmp_path / "test.toml"
        original = CrabConfig(run_args=["--foo", "--bar", "baz"])
        write_crab_config(path, original)
        loaded = load_crab_config(path)
        assert loaded.run_args == ["--foo", "--bar", "baz"]

"""Tests for kanibako.commands.shared_cmd (init, list)."""

from __future__ import annotations

import argparse

from kanibako.agents import AgentConfig, write_agent_config
from kanibako.config import load_config
from kanibako.paths import load_std_paths


def _write_config_with_shared(config_file, shared_entries: dict[str, str]) -> None:
    """Rewrite kanibako.toml replacing the [shared] section with real entries."""
    lines = []
    in_shared = False
    for line in config_file.read_text().splitlines():
        if line.strip() == "[shared]":
            in_shared = True
            lines.append("[shared]")
            for k, v in shared_entries.items():
                lines.append(f'{k} = "{v}"')
            continue
        if in_shared:
            if line.startswith("[") and line.strip() != "[shared]":
                in_shared = False
                lines.append(line)
            # Skip old [shared] content (comments, blank lines).
            continue
        lines.append(line)
    config_file.write_text("\n".join(lines) + "\n")


class TestSharedInit:
    def test_init_creates_global_cache(self, config_file, tmp_home, capsys):
        from kanibako.commands.shared_cmd import run_init

        config = load_config(config_file)
        std = load_std_paths(config)
        expected = std.data_path / "shared" / "global" / "pip"

        args = argparse.Namespace(name="pip", agent=None)
        rc = run_init(args)
        assert rc == 0
        assert expected.is_dir()
        assert "Created" in capsys.readouterr().out

    def test_init_creates_agent_cache(self, config_file, tmp_home, capsys):
        from kanibako.commands.shared_cmd import run_init

        config = load_config(config_file)
        std = load_std_paths(config)
        expected = std.data_path / "shared" / "claude" / "plugins"

        args = argparse.Namespace(name="plugins", agent="claude")
        rc = run_init(args)
        assert rc == 0
        assert expected.is_dir()
        assert "Created" in capsys.readouterr().out

    def test_init_already_exists(self, config_file, tmp_home, capsys):
        from kanibako.commands.shared_cmd import run_init

        config = load_config(config_file)
        std = load_std_paths(config)
        cache_dir = std.data_path / "shared" / "global" / "npm"
        cache_dir.mkdir(parents=True)

        args = argparse.Namespace(name="npm", agent=None)
        rc = run_init(args)
        assert rc == 0
        assert "Already exists" in capsys.readouterr().out

    def test_init_creates_parent_dirs(self, config_file, tmp_home):
        from kanibako.commands.shared_cmd import run_init

        args = argparse.Namespace(name="deep-cache", agent="custom-agent")
        rc = run_init(args)
        assert rc == 0

        config = load_config(config_file)
        std = load_std_paths(config)
        assert (std.data_path / "shared" / "custom-agent" / "deep-cache").is_dir()


class TestSharedList:
    def test_list_no_caches(self, config_file, tmp_home, capsys):
        from kanibako.commands.shared_cmd import run_list

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        assert "No shared caches configured" in capsys.readouterr().out

    def test_list_shows_global_caches(self, config_file, tmp_home, capsys):
        from kanibako.commands.shared_cmd import run_list

        _write_config_with_shared(config_file, {"pip": ".cache/pip"})

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "pip" in out
        assert "missing" in out
        assert ".cache/pip" in out

    def test_list_shows_ready_status(self, config_file, tmp_home, capsys):
        from kanibako.commands.shared_cmd import run_list

        _write_config_with_shared(config_file, {"pip": ".cache/pip"})

        config = load_config(config_file)
        std = load_std_paths(config)
        (std.data_path / "shared" / "global" / "pip").mkdir(parents=True)

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "ready" in out

    def test_list_shows_agent_caches(self, config_file, tmp_home, capsys):
        from kanibako.commands.shared_cmd import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        agent_path = std.data_path / "agents" / "claude.toml"
        write_agent_config(agent_path, AgentConfig(
            name="Claude Code",
            shared_caches={"plugins": ".claude/plugins"},
        ))

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "claude" in out
        assert "plugins" in out
        assert ".claude/plugins" in out

    def test_list_shows_mixed_global_and_agent(self, config_file, tmp_home, capsys):
        from kanibako.commands.shared_cmd import run_list

        _write_config_with_shared(config_file, {"npm": ".cache/npm"})

        config = load_config(config_file)
        std = load_std_paths(config)

        agent_path = std.data_path / "agents" / "claude.toml"
        write_agent_config(agent_path, AgentConfig(
            name="Claude Code",
            shared_caches={"plugins": ".claude/plugins"},
        ))

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "npm" in out
        assert "claude" in out
        assert "plugins" in out

    def test_list_shows_init_hint(self, config_file, tmp_home, capsys):
        from kanibako.commands.shared_cmd import run_list

        _write_config_with_shared(config_file, {"pip": ".cache/pip"})

        args = argparse.Namespace()
        run_list(args)
        out = capsys.readouterr().out
        assert "kanibako shared init" in out

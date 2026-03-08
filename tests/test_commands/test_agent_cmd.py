"""Tests for kanibako.commands.agent_cmd."""

from __future__ import annotations

import argparse
from unittest.mock import patch, MagicMock

import pytest

from kanibako.agents import AgentConfig, write_agent_config, agents_dir


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_env(config_file, tmp_home):
    """Set up an agent environment with one agent defined."""
    from kanibako.config import load_config
    from kanibako.paths import load_std_paths

    config = load_config(config_file)
    std = load_std_paths(config)

    adir = agents_dir(std.data_path)
    adir.mkdir(parents=True, exist_ok=True)

    # Create a sample agent
    cfg = AgentConfig(
        name="claude",
        shell="standard",
        default_args=["--no-helpers"],
        state={"model": "opus"},
        env={"EDITOR": "vim"},
        shared_caches={"npm": "/home/agent/.npm"},
    )
    write_agent_config(adir / "claude.toml", cfg)

    return std.data_path


@pytest.fixture
def empty_agent_env(config_file, tmp_home):
    """Set up an agent environment with no agents defined."""
    from kanibako.config import load_config
    from kanibako.paths import load_std_paths

    config = load_config(config_file)
    std = load_std_paths(config)
    return std.data_path


# ---------------------------------------------------------------------------
# agent list
# ---------------------------------------------------------------------------


class TestRunList:
    def test_list_with_agents(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_list

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "claude" in out
        assert "opus" in out
        assert "standard" in out

    def test_list_quiet(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_list

        args = argparse.Namespace(quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out.strip() == "claude"

    def test_list_no_agents(self, empty_agent_env, capsys):
        from kanibako.commands.agent_cmd import run_list

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No agents" in out

    def test_list_no_agents_quiet(self, empty_agent_env, capsys):
        from kanibako.commands.agent_cmd import run_list

        args = argparse.Namespace(quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_list_multiple_agents(self, agent_env, capsys):
        """List shows multiple agents sorted by name."""
        from kanibako.commands.agent_cmd import run_list

        adir = agents_dir(agent_env)
        cfg2 = AgentConfig(name="aider", shell="bash", state={"model": "sonnet"})
        write_agent_config(adir / "aider.toml", cfg2)

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "aider" in out
        assert "claude" in out


# ---------------------------------------------------------------------------
# agent info
# ---------------------------------------------------------------------------


class TestRunInfo:
    def test_info_valid_agent(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_info

        args = argparse.Namespace(agent_id="claude")
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "claude" in out
        assert "standard" in out
        assert "opus" in out
        assert "EDITOR" in out
        assert "npm" in out
        assert "--no-helpers" in out

    def test_info_missing_agent(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_info

        args = argparse.Namespace(agent_id="nonexistent")
        rc = run_info(args)
        assert rc == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# agent config
# ---------------------------------------------------------------------------


class TestRunConfig:
    def test_config_show(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config

        args = argparse.Namespace(
            agent_id="claude", key_value=None,
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "shell = standard" in out
        assert "model = opus" in out

    def test_config_get_state_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config

        args = argparse.Namespace(
            agent_id="claude", key_value="model",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "opus" in capsys.readouterr().out

    def test_config_get_env_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config

        args = argparse.Namespace(
            agent_id="claude", key_value="env.EDITOR",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "vim" in capsys.readouterr().out

    def test_config_get_shared_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config

        args = argparse.Namespace(
            agent_id="claude", key_value="shared.npm",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "/home/agent/.npm" in capsys.readouterr().out

    def test_config_get_missing_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config

        args = argparse.Namespace(
            agent_id="claude", key_value="nonexistent",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "not set" in capsys.readouterr().err

    def test_config_set_state_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agents import agent_toml_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="model=sonnet",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Set model=sonnet" in capsys.readouterr().out

        # Verify the file was updated
        path = agent_toml_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.state["model"] == "sonnet"

    def test_config_set_env_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agents import agent_toml_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="env.PAGER=less",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Set env.PAGER=less" in capsys.readouterr().out

        path = agent_toml_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.env["PAGER"] == "less"

    def test_config_set_shell(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agents import agent_toml_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="shell=bash",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0

        path = agent_toml_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.shell == "bash"

    def test_config_reset_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agents import agent_toml_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="model",
            effective=False, reset="__RESET__", all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Reset model" in capsys.readouterr().out

        path = agent_toml_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert "model" not in cfg.state

    def test_config_reset_env_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agents import agent_toml_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="env.EDITOR",
            effective=False, reset="__RESET__", all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Reset env.EDITOR" in capsys.readouterr().out

        path = agent_toml_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert "EDITOR" not in cfg.env

    def test_config_reset_missing_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config

        args = argparse.Namespace(
            agent_id="claude", key_value="nonexistent",
            effective=False, reset="__RESET__", all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "No override" in capsys.readouterr().out

    def test_config_reset_all_forced(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agents import agent_toml_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value=None,
            effective=False, reset="__RESET__", all_keys=True, force=True,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Reset all" in capsys.readouterr().out

        path = agent_toml_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.state == {}
        assert cfg.env == {}
        assert cfg.shared_caches == {}
        assert cfg.default_args == []

    def test_config_reset_requires_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config

        args = argparse.Namespace(
            agent_id="claude", key_value=None,
            effective=False, reset="__RESET__", all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 1
        assert "requires a key" in capsys.readouterr().err

    def test_config_missing_agent(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config

        args = argparse.Namespace(
            agent_id="nonexistent", key_value=None,
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# agent reauth
# ---------------------------------------------------------------------------


class TestRunReauth:
    def test_reauth_no_binary(self, config_file, tmp_home, capsys):
        """Reauth errors if no agent binary is found."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        with patch("kanibako.targets.resolve_target") as mock_target:
            target = MagicMock()
            target.has_binary = False
            mock_target.return_value = target
            rc = run_reauth(args)
        assert rc == 1
        assert "No agent target" in capsys.readouterr().err

    def test_reauth_target_error(self, config_file, tmp_home, capsys):
        """Reauth errors gracefully when target resolution fails."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        with patch("kanibako.targets.resolve_target") as mock_target:
            mock_target.side_effect = KeyError("no target")
            rc = run_reauth(args)
        assert rc == 1
        assert "Error" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Parser / alias tests
# ---------------------------------------------------------------------------


class TestAgentParser:
    def test_agent_is_subcommand(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "agent" in _SUBCOMMANDS

    def test_fork_no_longer_top_level(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "fork" not in _SUBCOMMANDS

    def test_helper_no_longer_top_level(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "helper" not in _SUBCOMMANDS

    def test_reauth_no_longer_top_level(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "reauth" not in _SUBCOMMANDS

    def test_agent_default_is_list(self):
        """Running 'agent' with no subcommand defaults to list."""
        from kanibako.cli import build_parser
        from kanibako.commands.agent_cmd import run_list

        parser = build_parser()
        args = parser.parse_args(["agent"])
        assert args.func == run_list

    def test_helper_list_alias_ls(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["agent", "helper", "ls"])
        assert args.command == "agent"
        assert hasattr(args, "func")

    def test_helper_send(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["agent", "helper", "send", "3", "hello"])
        assert args.number == 3
        assert args.message == "hello"

    def test_helper_broadcast(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["agent", "helper", "broadcast", "all hands"])
        assert args.message == "all hands"

    def test_helper_log(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["agent", "helper", "log", "-f", "--from", "1", "--last", "5"])
        assert args.follow is True
        assert args.from_helper == 1
        assert args.last == 5

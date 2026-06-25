"""Tests for kanibako.commands.agent_cmd."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from kanibako.agent_config import (
    AgentConfig,
    agent_settings_path,
    agents_dir,
    write_agent_config,
)


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
        run_args=["--no-helpers"],
        state={"model": "opus"},
        env={"EDITOR": "vim"},
    )
    write_agent_config(agent_settings_path(adir, "claude"), cfg)

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
        cfg2 = AgentConfig(name="aider", state={"model": "sonnet"})
        write_agent_config(agent_settings_path(adir, "aider"), cfg2)

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
        assert "opus" in out
        assert "EDITOR" in out
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
        from kanibako.agent_config import agent_config_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="model=sonnet",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Set model=sonnet" in capsys.readouterr().out

        # Verify the file was updated
        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.state["model"] == "sonnet"

    def test_config_set_env_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agent_config import agent_config_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="env.PAGER=less",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Set env.PAGER=less" in capsys.readouterr().out

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.env["PAGER"] == "less"

    def test_config_shell_is_no_longer_an_identity_key(self, agent_env, capsys):
        # The template-variant ``shell`` axis was removed; ``shell`` is no longer
        # an AgentConfig identity field, so a ``shell=`` set now lands in generic
        # state (not as a dedicated identity knob).
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agent_config import agent_config_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="shell=bash",
            effective=False, reset=None, all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert not hasattr(cfg, "shell")
        assert cfg.state["shell"] == "bash"

    def test_config_reset_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agent_config import agent_config_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="model",
            effective=False, reset="__RESET__", all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Reset model" in capsys.readouterr().out

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert "model" not in cfg.state

    def test_config_reset_env_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_config
        from kanibako.agent_config import agent_config_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value="env.EDITOR",
            effective=False, reset="__RESET__", all_keys=False, force=False,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Reset env.EDITOR" in capsys.readouterr().out

        path = agent_config_path(agent_env, "claude")
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
        from kanibako.agent_config import agent_config_path, load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key_value=None,
            effective=False, reset="__RESET__", all_keys=True, force=True,
        )
        rc = run_config(args)
        assert rc == 0
        assert "Reset all" in capsys.readouterr().out

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.state == {}
        assert cfg.env == {}
        assert cfg.run_args == []

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
    # W1: reauth resolves the agent via the unified config.resolve_agent cascade
    # before resolve_target.  We patch resolve_agent to a fixed name so these
    # tests don't depend on the host's installed-agent set, and patch
    # resolve_target for the resulting target object.

    def test_reauth_no_binary(self, config_file, tmp_home, capsys):
        """Reauth errors if no agent binary is found."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
        ):
            target = MagicMock()
            target.has_binary = False
            mock_target.return_value = target
            rc = run_reauth(args)
        assert rc == 1
        assert "No agent target" in capsys.readouterr().err

    def test_reauth_resolution_error_propagates(self, config_file, tmp_home):
        """When the cascade resolves nothing (Gate-2a/2b), the typed
        AgentResolutionError propagates to the top-level cli.py handler —
        reauth does NOT swallow it into a rc-1 with an ad-hoc message."""
        from kanibako.commands.agent_cmd import run_reauth
        from kanibako.errors import NoAgentSelectedError

        args = argparse.Namespace(project=None)
        with patch(
            "kanibako.config.resolve_agent",
            side_effect=NoAgentSelectedError("no agent selected"),
        ):
            with pytest.raises(NoAgentSelectedError):
                run_reauth(args)

    def test_reauth_refreshes_credentials_legacy(self, config_file, tmp_home, capsys):
        """Legacy target (descriptor is None): refresh via the per-plugin hook.

        The gate mirrors start.py exactly — only a ``desc is None`` target uses
        ``target.refresh_credentials``; descriptor-bearing targets route through
        credsync (covered separately below)."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            target.descriptor = None
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                proj.group_auth = True
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 0
        target.refresh_credentials.assert_called_once_with(proj.shell_path)

    def test_reauth_refreshes_credentials_descriptor(self, config_file, tmp_home, capsys):
        """Descriptor-bearing target: refresh routes through the credsync engine.

        B2 fix: ``run_reauth`` previously called ``target.refresh_credentials``
        UNGATED, sending a descriptor agent (e.g. goose) down its bypassed legacy
        path.  It now mirrors the three start.py sites: descriptor present ->
        ``credsync.refresh_cred_files`` with the same arg shape."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch("kanibako.targets.credsync.refresh_cred_files") as mock_refresh,
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            desc = MagicMock()
            target.descriptor = desc
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                proj.group_auth = True
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 0
        target.refresh_credentials.assert_not_called()
        mock_refresh.assert_called_once_with(
            desc, target, host_home=Path.home(),
            project_home=proj.shell_path, group_auth=proj.group_auth,
        )

    def test_reauth_skips_refresh_for_distinct(self, config_file, tmp_home, capsys):
        """Distinct auth does not trigger credential refresh."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
        ):
            target = MagicMock()
            target.has_binary = True
            target.display_name = "Claude Code"
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                proj.group_auth = False
                # Distinct auth with credentials present returns 0 before check_auth
                creds_path = MagicMock()
                creds_path.is_file.return_value = True
                target.credential_check_path.return_value = creds_path
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 0
        target.refresh_credentials.assert_not_called()

    def test_reauth_runs_inbox_setup_when_auth_fails_and_setup_declared(
        self, config_file, tmp_home, capsys,
    ):
        """FIX 2: check_auth fails + target declares setup_entrypoint -> reauth
        delegates to _run_container(setup_only=True) (the in-box setup path)
        instead of just printing 'authentication failed'."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        with (
            patch("kanibako.config.resolve_agent", return_value="goose"),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.commands.start._run_container", return_value=0,
            ) as m_run,
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = False
            target.setup_entrypoint = "goose"
            target.display_name = "Goose"
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                proj.group_auth = True
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 0
        m_run.assert_called_once()
        assert m_run.call_args.kwargs.get("setup_only") is True

    def test_reauth_no_setup_when_auth_fails_and_no_setup_cmd(
        self, config_file, tmp_home, capsys,
    ):
        """check_auth fails + no setup command (claude) -> standard failure, rc 1."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.commands.start._run_container",
            ) as m_run,
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = False
            target.setup_entrypoint = None
            target.display_name = "Claude Code"
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                proj.group_auth = True
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 1
        m_run.assert_not_called()
        err = capsys.readouterr().err
        assert "authentication failed" in err.lower()


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
        args = parser.parse_args(["box", "helper", "ls"])
        assert args.command == "box"
        assert hasattr(args, "func")

    def test_helper_send(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "helper", "send", "3", "hello"])
        assert args.number == 3
        assert args.message == "hello"

    def test_helper_broadcast(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "helper", "broadcast", "all hands"])
        assert args.message == "all hands"

    def test_helper_log(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "helper", "log", "-f", "--from", "1", "--last", "5"])
        assert args.follow is True
        assert args.from_helper == 1
        assert args.last == 5

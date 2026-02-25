"""Tests for ClaudeTarget."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


from kanibako.targets.base import AgentInstall, ResourceMapping, ResourceScope, TargetSetting
from kanibako.targets.claude import ClaudeTarget


class TestClaudeTargetProperties:
    def test_name(self):
        t = ClaudeTarget()
        assert t.name == "claude"

    def test_display_name(self):
        t = ClaudeTarget()
        assert t.display_name == "Claude Code"


class TestDetect:
    def test_found(self, tmp_path):
        """Detect returns AgentInstall when claude binary exists."""
        # Create a fake claude installation.
        install_dir = tmp_path / "claude"
        install_dir.mkdir()
        versions = install_dir / "versions" / "1.0"
        versions.mkdir(parents=True)
        binary = versions / "claude-bin"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        symlink = tmp_path / "claude-link"
        symlink.symlink_to(binary)

        t = ClaudeTarget()
        with patch("shutil.which", return_value=str(symlink)):
            result = t.detect()

        assert result is not None
        assert isinstance(result, AgentInstall)
        assert result.name == "claude"
        assert result.binary == symlink
        assert result.install_dir == install_dir

    def test_not_found(self):
        """Detect returns None when claude is not installed."""
        t = ClaudeTarget()
        with patch("shutil.which", return_value=None):
            result = t.detect()
        assert result is None

    def test_fallback_when_no_claude_dir(self, tmp_path):
        """When no 'claude' directory is found walking up, falls back to parent."""
        binary = tmp_path / "some" / "path" / "binary"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        t = ClaudeTarget()
        with patch("shutil.which", return_value=str(binary)):
            result = t.detect()

        assert result is not None
        # Falls back to binary's parent (resolved)
        assert result.install_dir == binary.resolve().parent


class TestBinaryMounts:
    def test_mounts(self, tmp_path):
        t = ClaudeTarget()
        install_dir = tmp_path / "share" / "claude"
        install_dir.mkdir(parents=True)
        binary = tmp_path / "bin" / "claude"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"fake-binary")
        install = AgentInstall(
            name="claude",
            binary=binary,
            install_dir=install_dir,
        )
        mounts = t.binary_mounts(install)
        assert len(mounts) == 2
        assert mounts[0].source == install_dir
        assert mounts[0].destination == "/home/agent/.local/share/claude"
        assert mounts[0].options == "ro"
        assert mounts[1].source == binary
        assert mounts[1].destination == "/home/agent/.local/bin/claude"
        assert mounts[1].options == "ro"

    def test_missing_source_skipped(self, tmp_path):
        """Mounts with non-existent sources are not added."""
        t = ClaudeTarget()
        install = AgentInstall(
            name="claude",
            binary=tmp_path / "nonexistent" / "claude",
            install_dir=tmp_path / "nonexistent" / "share",
        )
        mounts = t.binary_mounts(install)
        assert len(mounts) == 0


class TestInitHome:
    def test_creates_claude_dir(self, tmp_path, monkeypatch):
        """init_home creates .claude/ directory in home."""
        home = tmp_path / "home"
        home.mkdir()

        # No host files to copy.
        fake_home = tmp_path / "fake_user_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        t = ClaudeTarget()
        t.init_home(home)

        assert (home / ".claude").is_dir()
        assert (home / ".claude.json").exists()

    def test_copies_host_credentials(self, tmp_path, monkeypatch):
        """init_home copies .credentials.json from host."""
        home = tmp_path / "home"
        home.mkdir()

        fake_home = tmp_path / "fake_user_home"
        (fake_home / ".claude").mkdir(parents=True)
        creds = {"claudeAiOauth": {"token": "test"}}
        (fake_home / ".claude" / ".credentials.json").write_text(json.dumps(creds))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        t = ClaudeTarget()
        t.init_home(home)

        copied = home / ".claude" / ".credentials.json"
        assert copied.is_file()
        assert json.loads(copied.read_text())["claudeAiOauth"]["token"] == "test"

    def test_copies_filtered_settings(self, tmp_path, monkeypatch):
        """init_home copies filtered .claude.json from host."""
        home = tmp_path / "home"
        home.mkdir()

        fake_home = tmp_path / "fake_user_home"
        fake_home.mkdir()
        settings = {
            "oauthAccount": "user@example.com",
            "hasCompletedOnboarding": True,
            "installMethod": "npm",
            "dangerousKey": "should-be-removed",
        }
        (fake_home / ".claude.json").write_text(json.dumps(settings))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        t = ClaudeTarget()
        t.init_home(home)

        result = json.loads((home / ".claude.json").read_text())
        assert result["oauthAccount"] == "user@example.com"
        assert result["hasCompletedOnboarding"] is True
        assert "dangerousKey" not in result


class TestInitHomeDistinctAuth:
    def test_distinct_auth_skips_credential_copy(self, tmp_path, monkeypatch):
        """init_home with auth='distinct' skips credential copy."""
        home = tmp_path / "home"
        home.mkdir()

        fake_home = tmp_path / "fake_user_home"
        (fake_home / ".claude").mkdir(parents=True)
        creds = {"claudeAiOauth": {"token": "test"}}
        (fake_home / ".claude" / ".credentials.json").write_text(json.dumps(creds))
        (fake_home / ".claude.json").write_text(json.dumps({"oauthAccount": "x"}))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        t = ClaudeTarget()
        t.init_home(home, auth="distinct")

        assert (home / ".claude").is_dir()
        assert not (home / ".claude" / ".credentials.json").exists()
        # Empty .claude.json is created
        assert (home / ".claude.json").exists()
        assert (home / ".claude.json").read_text() == ""

    def test_shared_auth_copies_credentials(self, tmp_path, monkeypatch):
        """init_home with auth='shared' (default) copies credentials."""
        home = tmp_path / "home"
        home.mkdir()

        fake_home = tmp_path / "fake_user_home"
        (fake_home / ".claude").mkdir(parents=True)
        creds = {"claudeAiOauth": {"token": "test"}}
        (fake_home / ".claude" / ".credentials.json").write_text(json.dumps(creds))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        t = ClaudeTarget()
        t.init_home(home, auth="shared")

        copied = home / ".claude" / ".credentials.json"
        assert copied.is_file()
        assert json.loads(copied.read_text())["claudeAiOauth"]["token"] == "test"


class TestBuildCliArgs:
    def test_default(self):
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False, resume_mode=False,
            new_session=False, is_new_project=False,
            extra_args=[],
        )
        assert "--dangerously-skip-permissions" in args
        assert "--continue" in args

    def test_safe_mode(self):
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=True, resume_mode=False,
            new_session=False, is_new_project=False,
            extra_args=[],
        )
        assert "--dangerously-skip-permissions" not in args
        assert "--continue" in args

    def test_resume_mode(self):
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False, resume_mode=True,
            new_session=False, is_new_project=False,
            extra_args=[],
        )
        assert "--resume" in args
        assert "--continue" not in args

    def test_new_session(self):
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False, resume_mode=False,
            new_session=True, is_new_project=False,
            extra_args=[],
        )
        assert "--continue" not in args

    def test_new_project(self):
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False, resume_mode=False,
            new_session=False, is_new_project=True,
            extra_args=[],
        )
        assert "--continue" not in args

    def test_extra_args_resume_flag(self):
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False, resume_mode=False,
            new_session=False, is_new_project=False,
            extra_args=["--resume"],
        )
        assert "--continue" not in args
        assert "--resume" in args

    def test_extra_args_passed_through(self):
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False, resume_mode=False,
            new_session=False, is_new_project=False,
            extra_args=["--foo", "bar"],
        )
        assert "--foo" in args
        assert "bar" in args

    def test_extra_args_r_flag(self):
        t = ClaudeTarget()
        args = t.build_cli_args(
            safe_mode=False, resume_mode=False,
            new_session=False, is_new_project=False,
            extra_args=["-r"],
        )
        assert "--continue" not in args


class TestCheckAuth:
    def test_logged_in_returns_true(self):
        """check_auth returns True when status shows loggedIn."""
        t = ClaudeTarget()
        status_result = MagicMock(
            returncode=0,
            stdout=json.dumps({"loggedIn": True}),
        )
        with patch("kanibako.targets.claude.shutil.which", return_value="/usr/bin/claude"):
            with patch("kanibako.targets.claude.subprocess.run", return_value=status_result):
                assert t.check_auth() is True

    def test_not_logged_in_triggers_login(self):
        """check_auth runs login when status shows not loggedIn."""
        t = ClaudeTarget()
        status_not_logged = MagicMock(
            returncode=0,
            stdout=json.dumps({"loggedIn": False}),
        )
        login_result = MagicMock(returncode=0)
        status_after_login = MagicMock(
            returncode=0,
            stdout=json.dumps({"loggedIn": True}),
        )
        with patch("kanibako.targets.claude.shutil.which", return_value="/usr/bin/claude"):
            with patch("kanibako.targets.claude.subprocess.run",
                       side_effect=[status_not_logged, login_result, status_after_login]):
                assert t.check_auth() is True

    def test_login_fails_returns_false(self):
        """check_auth returns False when login fails."""
        t = ClaudeTarget()
        status_not_logged = MagicMock(
            returncode=0,
            stdout=json.dumps({"loggedIn": False}),
        )
        login_result = MagicMock(returncode=1)
        with patch("kanibako.targets.claude.shutil.which", return_value="/usr/bin/claude"):
            with patch("kanibako.targets.claude.subprocess.run",
                       side_effect=[status_not_logged, login_result]):
                assert t.check_auth() is False

    def test_binary_not_found_returns_true(self):
        """check_auth returns True when claude binary is not found."""
        t = ClaudeTarget()
        with patch("kanibako.targets.claude.shutil.which", return_value=None):
            assert t.check_auth() is True

    def test_status_command_fails_returns_true(self):
        """check_auth returns True when auth status command fails."""
        t = ClaudeTarget()
        status_result = MagicMock(returncode=1, stdout="")
        with patch("kanibako.targets.claude.shutil.which", return_value="/usr/bin/claude"):
            with patch("kanibako.targets.claude.subprocess.run", return_value=status_result):
                assert t.check_auth() is True


class TestRefreshCredentials:
    def test_calls_credential_function(self, tmp_path, monkeypatch):
        """refresh_credentials delegates to refresh_host_to_project."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        fake_home = tmp_path / "fake_user_home"
        (fake_home / ".claude").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        t = ClaudeTarget()
        with patch("kanibako.credentials.refresh_host_to_project") as m_h2p:
            t.refresh_credentials(home)

        m_h2p.assert_called_once()
        host_creds = m_h2p.call_args[0][0]
        project_creds = m_h2p.call_args[0][1]
        assert host_creds == fake_home / ".claude" / ".credentials.json"
        assert project_creds == home / ".claude" / ".credentials.json"


class TestResourceMappings:
    def test_returns_list(self):
        t = ClaudeTarget()
        mappings = t.resource_mappings()
        assert isinstance(mappings, list)
        assert len(mappings) > 0

    def test_all_entries_are_resource_mappings(self):
        t = ClaudeTarget()
        for m in t.resource_mappings():
            assert isinstance(m, ResourceMapping)

    def test_shared_resources(self):
        """Only plugin binaries are shared."""
        t = ClaudeTarget()
        mappings = {m.path: m.scope for m in t.resource_mappings()}
        assert mappings["plugins/"] == ResourceScope.SHARED
        shared = [p for p, s in mappings.items() if s == ResourceScope.SHARED]
        assert shared == ["plugins/"]

    def test_seeded_resources(self):
        """settings.json and CLAUDE.md are seeded from workset."""
        t = ClaudeTarget()
        mappings = {m.path: m.scope for m in t.resource_mappings()}
        assert mappings["settings.json"] == ResourceScope.SEEDED
        assert mappings["CLAUDE.md"] == ResourceScope.SEEDED

    def test_project_resources(self):
        """Session data, history, tasks, etc. are project-scoped."""
        t = ClaudeTarget()
        mappings = {m.path: m.scope for m in t.resource_mappings()}
        project_paths = [
            "projects/", "session-env/", "history.jsonl", "tasks/",
            "todos/", "plans/", "file-history/", "backups/",
            "debug/", "paste-cache/", "shell-snapshots/",
        ]
        for path in project_paths:
            assert mappings[path] == ResourceScope.PROJECT, f"{path} should be PROJECT"


class TestSettingDescriptors:
    def test_returns_list_of_target_settings(self):
        t = ClaudeTarget()
        descriptors = t.setting_descriptors()
        assert isinstance(descriptors, list)
        assert all(isinstance(d, TargetSetting) for d in descriptors)

    def test_model_setting(self):
        t = ClaudeTarget()
        descriptors = {d.key: d for d in t.setting_descriptors()}
        assert "model" in descriptors
        assert descriptors["model"].default == "opus"
        assert descriptors["model"].choices == ()  # freeform

    def test_access_setting(self):
        t = ClaudeTarget()
        descriptors = {d.key: d for d in t.setting_descriptors()}
        assert "access" in descriptors
        assert descriptors["access"].default == "permissive"
        assert descriptors["access"].choices == ("permissive", "default")


class TestGenerateAgentConfig:
    def test_returns_claude_defaults(self):
        t = ClaudeTarget()
        cfg = t.generate_agent_config()
        assert cfg.name == "Claude Code"
        assert cfg.shell == "standard"
        assert cfg.state == {"model": "opus", "access": "permissive"}
        assert cfg.shared_caches == {"plugins": ".claude/plugins"}
        assert cfg.default_args == []
        assert cfg.env == {}

    def test_is_agent_config_instance(self):
        from kanibako.agents import AgentConfig
        t = ClaudeTarget()
        cfg = t.generate_agent_config()
        assert isinstance(cfg, AgentConfig)


class TestApplyState:
    def test_model_translated_to_cli_arg(self):
        t = ClaudeTarget()
        cli_args, env_vars = t.apply_state({"model": "opus"})
        assert cli_args == ["--model", "opus"]
        assert env_vars == {}

    def test_unknown_keys_ignored(self):
        t = ClaudeTarget()
        cli_args, env_vars = t.apply_state({"unknown_key": "value"})
        assert cli_args == []
        assert env_vars == {}

    def test_empty_state(self):
        t = ClaudeTarget()
        cli_args, env_vars = t.apply_state({})
        assert cli_args == []
        assert env_vars == {}

    def test_model_with_other_keys(self):
        t = ClaudeTarget()
        cli_args, env_vars = t.apply_state({"model": "sonnet", "access": "permissive"})
        assert cli_args == ["--model", "sonnet"]
        assert env_vars == {}

    def test_empty_model_not_added(self):
        t = ClaudeTarget()
        cli_args, env_vars = t.apply_state({"model": ""})
        assert cli_args == []


class TestWritebackCredentials:
    def test_calls_writeback(self, tmp_path):
        """writeback_credentials delegates to writeback_project_to_host."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        t = ClaudeTarget()
        with patch("kanibako.credentials.writeback_project_to_host") as m_wb:
            t.writeback_credentials(home)

        m_wb.assert_called_once()
        project_creds = m_wb.call_args[0][0]
        assert project_creds == home / ".claude" / ".credentials.json"

"""Tests for GooseTarget."""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import patch

import yaml

from kanibako.plugins.goose import GooseTarget
from kanibako.targets.base import AgentInstall


class TestProperties:
    def test_name(self):
        assert GooseTarget().name == "goose"

    def test_display_name(self):
        assert GooseTarget().display_name == "Goose"

    def test_config_dir_name(self):
        assert GooseTarget().config_dir_name == ".config/goose"


class TestDetect:
    def test_found(self, tmp_path: Path):
        binary = tmp_path / "goose"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        with patch(
            "kanibako.plugins.goose.target.shutil.which",
            return_value=str(binary),
        ):
            result = GooseTarget().detect()

        assert result is not None
        assert result.name == "goose"
        assert result.binary == binary.resolve()
        assert result.install_dir == binary.resolve().parent

    def test_not_found(self):
        with patch(
            "kanibako.plugins.goose.target.shutil.which",
            return_value=None,
        ):
            assert GooseTarget().detect() is None


class TestBinaryMounts:
    def test_single_mount(self, tmp_path: Path):
        binary = tmp_path / "goose"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        install = AgentInstall(name="goose", binary=binary, install_dir=tmp_path)
        mounts = GooseTarget().binary_mounts(install)

        assert len(mounts) == 1
        assert mounts[0].source == binary
        assert mounts[0].destination == "/home/agent/.local/bin/goose"
        assert mounts[0].options == "ro"

    def test_no_mount_when_binary_missing(self, tmp_path: Path):
        binary = tmp_path / "goose"  # does not exist

        install = AgentInstall(name="goose", binary=binary, install_dir=tmp_path)
        mounts = GooseTarget().binary_mounts(install)

        assert mounts == []


class TestInitHome:
    def test_creates_config_and_data_dir(self, project_home: Path, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        GooseTarget().init_home(project_home)

        assert (project_home / ".config" / "goose").is_dir()
        assert (project_home / ".local" / "share" / "Block" / "goose").is_dir()

    def test_copies_filtered_config(self, project_home: Path, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))

        host_config = fake_host / ".config" / "goose" / "config.yaml"
        data = {
            "provider": "anthropic",
            "model": "claude-4",
            "extensions": ["web"],
            "SECRET_KEY": "should-be-dropped",
            "unknown_field": "also-dropped",
        }
        host_config.write_text(yaml.safe_dump(data))

        GooseTarget().init_home(project_home)

        result = yaml.safe_load(
            (project_home / ".config" / "goose" / "config.yaml").read_text()
        )
        assert set(result.keys()) == {"provider", "model", "extensions"}
        assert "SECRET_KEY" not in result
        assert "unknown_field" not in result

    def test_idempotent(self, project_home: Path, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))

        config_dir = project_home / ".config" / "goose"
        config_dir.mkdir(parents=True)
        existing = {"provider": "existing"}
        (config_dir / "config.yaml").write_text(yaml.safe_dump(existing))

        host_config = fake_host / ".config" / "goose" / "config.yaml"
        host_config.write_text(yaml.safe_dump({"provider": "new-value"}))

        GooseTarget().init_home(project_home)

        result = yaml.safe_load(
            (project_home / ".config" / "goose" / "config.yaml").read_text()
        )
        assert result["provider"] == "existing"  # Not overwritten

    def test_copies_secrets_with_perms(self, project_home: Path, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))

        host_secrets = fake_host / ".config" / "goose" / "secrets.yaml"
        host_secrets.write_text("api_key: secret123\n")

        GooseTarget().init_home(project_home)

        project_secrets = project_home / ".config" / "goose" / "secrets.yaml"
        assert project_secrets.is_file()
        assert project_secrets.read_text() == "api_key: secret123\n"
        mode = project_secrets.stat().st_mode & 0o777
        assert mode == 0o600

    def test_distinct_auth_creates_empty_config_no_secrets(
        self, project_home: Path, fake_host: Path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))

        host_config = fake_host / ".config" / "goose" / "config.yaml"
        host_config.write_text(yaml.safe_dump({"provider": "anthropic"}))
        host_secrets = fake_host / ".config" / "goose" / "secrets.yaml"
        host_secrets.write_text("api_key: secret\n")

        GooseTarget().init_home(project_home, auth="distinct")

        project_config = project_home / ".config" / "goose" / "config.yaml"
        assert project_config.is_file()
        assert project_config.read_text() == ""  # empty

        project_secrets = project_home / ".config" / "goose" / "secrets.yaml"
        assert not project_secrets.exists()


class TestCredentialCheckPath:
    def test_returns_correct_path(self, tmp_path: Path):
        result = GooseTarget().credential_check_path(tmp_path)
        assert result == tmp_path / ".config" / "goose" / "secrets.yaml"


class TestInvalidateCredentials:
    def test_deletes_secrets(self, tmp_path: Path):
        secrets = tmp_path / ".config" / "goose" / "secrets.yaml"
        secrets.parent.mkdir(parents=True)
        secrets.write_text("data\n")

        GooseTarget().invalidate_credentials(tmp_path)

        assert not secrets.exists()

    def test_noop_when_missing(self, tmp_path: Path):
        # Should not raise
        GooseTarget().invalidate_credentials(tmp_path)


class TestRefreshCredentials:
    def test_delegates_to_refresh_secrets(self, project_home: Path, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))

        host_secrets = fake_host / ".config" / "goose" / "secrets.yaml"
        host_secrets.write_text("key: val\n")

        config_dir = project_home / ".config" / "goose"
        config_dir.mkdir(parents=True)

        calls = []
        monkeypatch.setattr(
            "kanibako.plugins.goose.target.refresh_secrets",
            lambda h, p: calls.append((h, p)) or True,
        )

        GooseTarget().refresh_credentials(project_home)

        assert len(calls) == 1
        assert calls[0][0] == fake_host / ".config" / "goose" / "secrets.yaml"
        assert calls[0][1] == project_home / ".config" / "goose" / "secrets.yaml"


class TestWritebackCredentials:
    def test_delegates_to_writeback_secrets(self, project_home: Path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "kanibako.plugins.goose.target.writeback_secrets",
            lambda p: calls.append(p),
        )

        GooseTarget().writeback_credentials(project_home)

        assert len(calls) == 1
        assert calls[0] == project_home / ".config" / "goose" / "secrets.yaml"


class TestCheckAuth:
    def test_returns_true_when_both_exist(self, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        monkeypatch.setattr(
            "kanibako.plugins.goose.target.shutil.which",
            lambda _: "/usr/bin/goose",
        )

        config = fake_host / ".config" / "goose" / "config.yaml"
        config.write_text("provider: anthropic\n")
        secrets = fake_host / ".config" / "goose" / "secrets.yaml"
        secrets.write_text("key: secret\n")

        assert GooseTarget().check_auth() is True

    def test_returns_false_when_secrets_missing(self, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        monkeypatch.setattr(
            "kanibako.plugins.goose.target.shutil.which",
            lambda _: "/usr/bin/goose",
        )

        config = fake_host / ".config" / "goose" / "config.yaml"
        config.write_text("provider: anthropic\n")
        # No secrets file

        assert GooseTarget().check_auth() is False

    def test_returns_true_when_binary_not_found(self, monkeypatch):
        monkeypatch.setattr(
            "kanibako.plugins.goose.target.shutil.which",
            lambda _: None,
        )
        assert GooseTarget().check_auth() is True


class TestGenerateAgentConfig:
    def test_returns_correct_defaults(self):
        config = GooseTarget().generate_agent_config()
        assert config.name == "Goose"
        assert config.shell == "standard"
        assert config.state["provider"] == "anthropic"
        assert "model" in config.state


class TestApplyState:
    def test_provider_env_var(self):
        cli_args, env_vars = GooseTarget().apply_state({"provider": "openai"})
        assert cli_args == []
        assert env_vars["GOOSE_PROVIDER"] == "openai"

    def test_model_env_var(self):
        cli_args, env_vars = GooseTarget().apply_state({"model": "gpt-4"})
        assert cli_args == []
        assert env_vars["GOOSE_MODEL"] == "gpt-4"

    def test_empty_state_no_vars(self):
        cli_args, env_vars = GooseTarget().apply_state({})
        assert cli_args == []
        assert env_vars == {}


class TestSettingDescriptors:
    def test_returns_provider_and_model(self):
        settings = GooseTarget().setting_descriptors()
        keys = [s.key for s in settings]
        assert "provider" in keys
        assert "model" in keys
        assert len(settings) == 2


class TestResourceMappings:
    def test_returns_expected_entries(self):
        mappings = GooseTarget().resource_mappings()
        names = [m.path for m in mappings]
        assert "config.yaml" in names
        assert "secrets.yaml" in names
        assert "sessions.db" in names
        assert len(mappings) == 3


class TestBuildCliArgs:
    def _build(self, **overrides):
        defaults = dict(
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
        )
        defaults.update(overrides)
        return GooseTarget().build_cli_args(**defaults)

    def test_default_session_start_approve(self):
        args = self._build()
        assert args[:2] == ["session", "start"]
        assert "--approve-all" in args

    def test_safe_mode_no_approve(self):
        args = self._build(safe_mode=True)
        assert args[:2] == ["session", "start"]
        assert "--approve-all" not in args

    def test_resume_mode(self):
        args = self._build(resume_mode=True)
        assert args[:2] == ["session", "resume"]
        assert "--approve-all" in args

    def test_resume_safe(self):
        args = self._build(resume_mode=True, safe_mode=True)
        assert args[:2] == ["session", "resume"]
        assert "--approve-all" not in args

    def test_extra_args_passed_through(self):
        args = self._build(extra_args=["--verbose", "--no-color"])
        assert "--verbose" in args
        assert "--no-color" in args

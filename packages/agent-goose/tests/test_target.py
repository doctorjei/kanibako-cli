"""Tests for GooseTarget."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from kanibako.plugins.goose import GooseTarget
from kanibako.targets import assembly
from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    BindScope,
    Cadence,
    Channel,
    CredFileSpec,
    HostSrcOrigin,
    PluginDescriptor,
)


class TestProperties:
    def test_name(self):
        assert GooseTarget().name == "goose"

    def test_display_name(self):
        assert GooseTarget().display_name == "Goose"

    def test_config_dir_name(self):
        assert GooseTarget().config_dir_name == ".config/goose"


def _anchor_contract(monkeypatch, binary):
    """Point the goose plugin's contract constant at a tmp binary.

    detect / check_auth anchor to ``_BINARY`` (the contract path
    ~/.local/bin/goose) instead of ``shutil.which`` — tests override that module
    constant rather than mocking PATH.
    """
    import kanibako.plugins.goose.target as goose_mod
    monkeypatch.setattr(goose_mod, "_BINARY", Path(binary))


class TestDetect:
    def test_found(self, tmp_path: Path, monkeypatch):
        """Detect anchors to the contract path (no shutil.which)."""
        binary = tmp_path / "goose"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        _anchor_contract(monkeypatch, binary)

        with patch("kanibako.plugins.goose.target.shutil.which") as m_which:
            result = GooseTarget().detect()
        m_which.assert_not_called()

        assert result is not None
        assert result.name == "goose"
        assert result.binary == binary.resolve()
        assert result.install_dir == binary.resolve().parent
        assert result.launcher is None

    def test_not_found(self, tmp_path: Path, monkeypatch):
        """Detect returns None when the contract path is absent."""
        _anchor_contract(monkeypatch, tmp_path / "goose")  # never created
        with patch("kanibako.plugins.goose.target.shutil.which") as m_which:
            assert GooseTarget().detect() is None
        m_which.assert_not_called()

    def test_dangling_symlink_still_installed(self, tmp_path: Path, monkeypatch):
        """A present-but-dangling symlink still counts as installed."""
        target_path = tmp_path / "gone"  # does not exist
        link = tmp_path / "goose"
        link.symlink_to(target_path)  # dangling
        _anchor_contract(monkeypatch, link)

        result = GooseTarget().detect()
        assert result is not None
        assert result.name == "goose"


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

        GooseTarget().init_home(project_home, group_auth=False)

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


def _real_binary(tmp_path):
    """Create a real (existing) contract binary file under tmp_path."""
    binary = tmp_path / "goose"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


class TestCheckAuth:
    def test_returns_true_when_both_exist(self, tmp_path: Path, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        _anchor_contract(monkeypatch, _real_binary(tmp_path))

        config = fake_host / ".config" / "goose" / "config.yaml"
        config.write_text("provider: anthropic\n")
        secrets = fake_host / ".config" / "goose" / "secrets.yaml"
        secrets.write_text("key: secret\n")

        with patch("kanibako.plugins.goose.target.shutil.which") as m_which:
            assert GooseTarget().check_auth() is True
        m_which.assert_not_called()

    def test_returns_false_when_secrets_missing(self, tmp_path: Path, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        _anchor_contract(monkeypatch, _real_binary(tmp_path))

        config = fake_host / ".config" / "goose" / "config.yaml"
        config.write_text("provider: anthropic\n")
        # No secrets file

        assert GooseTarget().check_auth() is False

    def test_returns_true_when_binary_not_found(self, tmp_path: Path, monkeypatch):
        """No contract binary -> True (defers to later warnings), no which."""
        _anchor_contract(monkeypatch, tmp_path / "goose")  # never created
        with patch("kanibako.plugins.goose.target.shutil.which") as m_which:
            assert GooseTarget().check_auth() is True
        m_which.assert_not_called()


class TestGenerateCrabConfig:
    def test_returns_correct_defaults(self):
        config = GooseTarget().generate_crab_config()
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

    def test_sessions_db_anchored_to_data_dir(self):
        """sessions.db lives under the data dir, anchored via `base`."""
        mappings = {m.path: m for m in GooseTarget().resource_mappings()}
        assert mappings["sessions.db"].base == ".local/share/goose/sessions"
        # config/secrets stay relative to the config dir (no base).
        assert mappings["config.yaml"].base == ""
        assert mappings["secrets.yaml"].base == ""


class TestBuildCliArgs:
    """Legacy build_cli_args is RETAINED (abstract method) but BYPASSED at launch.

    Goose now launches via the descriptor (bare ``session`` / ``session
    --resume``).  The legacy method still emits the old ``session start`` /
    ``session resume`` text, but it is no longer on the launch path — these
    tests just pin that the legacy method is still callable + unchanged so
    nothing crashes if invoked.  The real grammar is asserted in
    ``TestDescriptor`` / ``TestDescriptorAssembly``.
    """

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

    def test_legacy_method_still_callable(self):
        args = self._build()
        assert isinstance(args, list)

    def test_extra_args_passed_through(self):
        args = self._build(extra_args=["--verbose", "--no-color"])
        assert "--verbose" in args
        assert "--no-color" in args


class TestDescriptor:
    """The declarative PluginDescriptor that puts goose on the generic launch path."""

    def test_is_plugin_descriptor(self):
        assert isinstance(GooseTarget().descriptor, PluginDescriptor)

    def test_command(self):
        assert GooseTarget().descriptor.command == ("goose",)

    def test_binary_binding(self):
        d = GooseTarget().descriptor
        bindings = {b.key: b for b in d.bindings}
        assert set(bindings) == {"binary"}
        binary = bindings["binary"]
        assert binary.origin == HostSrcOrigin.BINARY
        assert binary.box_dest == "/home/agent/.local/bin/goose"
        assert binary.kind == BindKind.FILE
        assert binary.scope == BindScope.AGENT_CRITICAL
        assert binary.ro is True

    def test_mode_uses_bare_session(self):
        """goose 1.37.0: bare `session` / `session --resume` (NO start/resume subcmds)."""
        d = GooseTarget().descriptor
        assert d.mode["start"] == ("session",)
        assert d.mode["continue"] == ("session", "--resume")
        # The removed-subcommand grammar must NOT appear anywhere.
        assert "resume" not in d.mode

    def test_operations_exec(self):
        d = GooseTarget().descriptor
        assert "exec" in d.operations
        assert d.operations["exec"].fragment == ("run", "--no-session", "-t")

    def test_safe_bypass_env_goose_mode(self):
        """Safe-bypass is symmetric ENV GOOSE_MODE (NO --approve-all flag in 1.37.0).

        safe-OFF/-A -> ``auto``; safe-ON/-S -> ``approve``.  The secure value is
        MANDATORY because goose's unset GOOSE_MODE default is itself ``auto``
        (the A1 fix).
        """
        sb = GooseTarget().descriptor.safe_bypass
        assert sb is not None
        assert sb.channel == Channel.ENV
        assert sb.env_var == "GOOSE_MODE"
        assert sb.env_value == "auto"
        assert sb.secure_env_value == "approve"
        assert sb.flag == ()
        assert sb.secure_flag == ()
        assert sb.setting_key == ""

    def test_settings_model_and_provider_env(self):
        d = GooseTarget().descriptor
        settings = {s.setting_key: s for s in d.settings}
        assert set(settings) == {"model", "provider"}
        assert settings["model"].channel == Channel.ENV
        assert settings["model"].env_var == "GOOSE_MODEL"
        assert settings["provider"].channel == Channel.ENV
        assert settings["provider"].env_var == "GOOSE_PROVIDER"

    def test_container_env_empty(self):
        assert GooseTarget().descriptor.container_env == {}

    def test_cred_files(self):
        d = GooseTarget().descriptor
        specs = {s.home_rel: s for s in d.cred_files}
        assert set(specs) == {
            ".config/goose/secrets.yaml",
            ".config/goose/config.yaml",
        }

        secrets = specs[".config/goose/secrets.yaml"]
        assert secrets.host_rel == ".config/goose/secrets.yaml"
        assert secrets.cadence == Cadence.SYNC
        assert secrets.mtime_gate is True
        assert secrets.filtered is False

        config = specs[".config/goose/config.yaml"]
        assert config.host_rel == ".config/goose/config.yaml"
        assert config.cadence == Cadence.SEED_ONCE
        assert config.filtered is True

    def test_host_prep_and_init_dirs(self):
        d = GooseTarget().descriptor
        assert d.host_prep is False
        assert d.init_dirs == (".config/goose", ".local/share/goose/sessions")


class TestTransformCred:
    """transform_cred — PURE content op (engine owns gating)."""

    _CONFIG_SPEC = CredFileSpec(
        ".config/goose/config.yaml", ".config/goose/config.yaml",
        cadence=Cadence.SEED_ONCE, filtered=True,
    )
    _SECRETS_SPEC = CredFileSpec(
        ".config/goose/secrets.yaml", ".config/goose/secrets.yaml",
        cadence=Cadence.SYNC, mtime_gate=True, filtered=False,
    )

    def test_config_with_source_filtered(self, tmp_path):
        """config.yaml with a host source -> allowlist-filtered write."""
        src = tmp_path / "config.yaml"
        src.write_text(yaml.safe_dump({
            "provider": "anthropic",
            "model": "claude-4",
            "extensions": ["web"],
            "SECRET_KEY": "should-be-dropped",
            "unknown_field": "also-dropped",
        }))
        dst = tmp_path / "home" / ".config" / "goose" / "config.yaml"

        GooseTarget().transform_cred(self._CONFIG_SPEC, src, dst, "in")

        result = yaml.safe_load(dst.read_text())
        assert set(result.keys()) == {"provider", "model", "extensions"}
        assert "SECRET_KEY" not in result
        assert "unknown_field" not in result

    def test_config_without_source_is_noop(self, tmp_path):
        """config.yaml with src=None -> no file written (no empty-config rule)."""
        dst = tmp_path / "home" / ".config" / "goose" / "config.yaml"

        GooseTarget().transform_cred(self._CONFIG_SPEC, None, dst, "in")

        assert not dst.exists()

    def test_secrets_falls_back_to_base_copy(self, tmp_path):
        """A non-config spec routes to the base plain-copy fallback."""
        src = tmp_path / "secrets.yaml"
        src.write_text("api_key: secret123\n")
        dst = tmp_path / "home" / ".config" / "goose" / "secrets.yaml"

        GooseTarget().transform_cred(self._SECRETS_SPEC, src, dst, "in")

        assert dst.is_file()
        assert dst.read_text() == "api_key: secret123\n"


class TestDescriptorAssembly:
    """Integration: goose's argv/env assembled from the descriptor via assembly.*."""

    def _argv(self, *, resume_mode=False, safe_off=True, state=None, extra_args=None):
        d = GooseTarget().descriptor
        mode_key = assembly.resolve_mode(
            resume_mode=resume_mode,
            new_session=False,
            is_new_project=False,
            extra_args=extra_args or [],
            available_modes=d.mode.keys(),
        )
        return assembly.assemble_argv(
            d,
            mode_key=mode_key,
            safe_mode_off=safe_off,
            setting_values=state or {},
            op=None,
            extra_args=extra_args or [],
        )

    def test_default_argv_is_continue(self):
        """Default launch (no new-session forcing) resolves to continue-last.

        With ``continue`` available and nothing forcing a fresh session,
        resolve_mode -> 'continue' -> ['session', '--resume'].  GOOSE_MODE /
        --approve-all never appear in argv (the bypass is an env var).
        """
        argv = self._argv()
        assert argv == ["session", "--resume"]
        assert "GOOSE_MODE" not in argv
        assert "--approve-all" not in argv

    def test_start_mode_is_bare_session(self):
        """Explicit start mode -> bare ['session']."""
        d = GooseTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="start", safe_mode_off=True,
            setting_values={}, op=None, extra_args=[],
        )
        assert argv == ["session"]

    def test_continue_argv(self):
        d = GooseTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="continue", safe_mode_off=True,
            setting_values={}, op=None, extra_args=[],
        )
        assert argv == ["session", "--resume"]

    def test_new_session_forced_is_bare_session(self):
        d = GooseTarget().descriptor
        mode_key = assembly.resolve_mode(
            resume_mode=False, new_session=True, is_new_project=False,
            extra_args=[], available_modes=d.mode.keys(),
        )
        argv = assembly.assemble_argv(
            d, mode_key=mode_key, safe_mode_off=True,
            setting_values={}, op=None, extra_args=[],
        )
        assert argv == ["session"]

    def test_exec_op_argv(self):
        d = GooseTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="start", safe_mode_off=True,
            setting_values={}, op="exec", extra_args=["do the thing"],
        )
        assert argv == ["run", "--no-session", "-t", "do the thing"]

    def test_env_safe_off_sets_goose_mode_auto(self):
        d = GooseTarget().descriptor
        env = assembly.assemble_env(d, safe_mode_off=True, setting_values={})
        assert env["GOOSE_MODE"] == "auto"

    def test_env_safe_on_sets_goose_mode_approve(self):
        """The A1 fix: -S/secure emits GOOSE_MODE=approve (NOT nothing).

        goose's unset GOOSE_MODE default is ``auto`` (tools auto-run), so -S must
        emit a restrictive value or it would not actually be safe.
        """
        d = GooseTarget().descriptor
        env = assembly.assemble_env(d, safe_mode_off=False, setting_values={})
        assert env["GOOSE_MODE"] == "approve"

    def test_env_model_and_provider_from_settings(self):
        d = GooseTarget().descriptor
        env = assembly.assemble_env(
            d, safe_mode_off=True,
            setting_values={"model": "claude-4", "provider": "anthropic"},
        )
        assert env["GOOSE_MODEL"] == "claude-4"
        assert env["GOOSE_PROVIDER"] == "anthropic"

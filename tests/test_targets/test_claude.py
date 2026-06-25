"""Tests for ClaudeTarget."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    BindScope,
    Cadence,
    Channel,
    CredFileSpec,
    HostSrcOrigin,
    PluginDescriptor,
    TargetSetting,
)
from kanibako.plugins.claude import ClaudeTarget


class TestClaudeTargetProperties:
    def test_name(self):
        t = ClaudeTarget()
        assert t.name == "claude"

    def test_display_name(self):
        t = ClaudeTarget()
        assert t.display_name == "Claude Code"


class TestCredentialCheckPath:
    def test_returns_credentials_json_path(self, tmp_path):
        t = ClaudeTarget()
        result = t.credential_check_path(tmp_path)
        assert result == tmp_path / ".claude" / ".credentials.json"

    def test_config_dir_name(self):
        t = ClaudeTarget()
        assert t.config_dir_name == ".claude"


def _anchor_contract(monkeypatch, launcher, install_dir):
    """Point the claude plugin's contract constants at a tmp install.

    detect / check_auth / prepare_host all anchor to ``_LAUNCHER`` /
    ``_INSTALL_DIR`` instead of ``shutil.which`` — tests override those module
    constants rather than mocking PATH.
    """
    import kanibako.plugins.claude.target as claude_mod
    monkeypatch.setattr(claude_mod, "_LAUNCHER", Path(launcher))
    monkeypatch.setattr(claude_mod, "_INSTALL_DIR", Path(install_dir))


class TestDetect:
    def test_found(self, tmp_path, monkeypatch):
        """Detect anchors to the contract paths (no shutil.which)."""
        # Real install rooted at the contract install_dir.
        install_dir = tmp_path / "share" / "claude"
        versions = install_dir / "versions" / "1.0"
        versions.mkdir(parents=True)
        binary = versions / "claude-bin"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        launcher = tmp_path / "bin" / "claude"
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to(binary)

        _anchor_contract(monkeypatch, launcher, install_dir)

        t = ClaudeTarget()
        # No shutil.which involved: assert it is never consulted.
        with patch("kanibako.plugins.claude.target.shutil.which") as m_which:
            result = t.detect()
        m_which.assert_not_called()

        assert result is not None
        assert isinstance(result, AgentInstall)
        assert result.name == "claude"
        # binary is the resolved (symlink-free) launcher target.
        assert result.binary == binary.resolve()
        # install_dir + launcher anchor to the contract paths.
        assert result.install_dir == install_dir
        assert result.launcher == launcher

    def test_not_found(self, tmp_path, monkeypatch):
        """Detect returns None when the contract launcher is absent."""
        launcher = tmp_path / "bin" / "claude"  # never created
        install_dir = tmp_path / "share" / "claude"
        _anchor_contract(monkeypatch, launcher, install_dir)

        t = ClaudeTarget()
        with patch("kanibako.plugins.claude.target.shutil.which") as m_which:
            result = t.detect()
        m_which.assert_not_called()
        assert result is None

    def test_dangling_launcher_still_installed(self, tmp_path, monkeypatch):
        """A present-but-dangling launcher symlink still counts as installed.

        The update gate / binary validation handles a broken install
        downstream; detect must not silently drop the agent.
        """
        target_path = tmp_path / "versions" / "gone"  # does not exist
        launcher = tmp_path / "bin" / "claude"
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to(target_path)  # dangling
        install_dir = tmp_path / "share" / "claude"
        _anchor_contract(monkeypatch, launcher, install_dir)

        t = ClaudeTarget()
        result = t.detect()
        assert result is not None
        assert result.launcher == launcher
        assert result.install_dir == install_dir


# Claude's delivery binds + launch argv come from its descriptor (the legacy
# ``binary_mounts`` / ``build_cli_args`` hooks were removed for the
# descriptor-only public release).  The exhaustive descriptor-assembly behavior
# is pinned in ``test_start_descriptor_assembly.py``; these tests assert the
# claude plugin's own descriptor exposes the expected delivery binds.


class TestDescriptorDeliveryMounts:
    def test_mounts(self, tmp_path):
        """The descriptor delivers the two AS-IS host binds: share dir + launcher."""
        from kanibako.targets.assembly import descriptor_mounts

        t = ClaudeTarget()
        install_dir = tmp_path / "share" / "claude"
        install_dir.mkdir(parents=True)
        # The launcher (~/.local/bin/claude) is the bin bind source, bound
        # as-is from the recorded contract path on the install.
        launcher = tmp_path / "bin" / "claude"
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(b"fake-binary")
        install = AgentInstall(
            name="claude",
            binary=tmp_path / "versions" / "1.0" / "claude-bin",
            install_dir=install_dir,
            launcher=launcher,
        )
        mounts = descriptor_mounts(t.descriptor, install)

        assert len(mounts) == 2
        assert mounts[0].source == install_dir
        assert mounts[0].destination == "/home/agent/.local/share/claude"
        assert mounts[0].options == "ro"
        assert mounts[1].source == launcher
        assert mounts[1].destination == "/home/agent/.local/bin/claude"
        assert mounts[1].options == "ro"

    def test_missing_source_safe_fails(self, tmp_path):
        """A missing AGENT_CRITICAL source raises BindingSourceError (clean safe-fail)."""
        from kanibako.targets.assembly import BindingSourceError, descriptor_mounts

        t = ClaudeTarget()
        install = AgentInstall(
            name="claude",
            binary=tmp_path / "nonexistent" / "claude",
            install_dir=tmp_path / "nonexistent" / "share",
            launcher=tmp_path / "nonexistent" / "bin" / "claude",
        )
        with pytest.raises(BindingSourceError):
            descriptor_mounts(t.descriptor, install)


def _real_launcher(tmp_path):
    """Create a real (existing) contract launcher file under tmp_path."""
    launcher = tmp_path / "bin" / "claude"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_bytes(b"#!/bin/sh\n")
    launcher.chmod(0o755)
    return launcher


class TestCheckAuth:
    def test_logged_in_returns_true(self, tmp_path, monkeypatch):
        """check_auth returns True when status shows loggedIn."""
        _anchor_contract(monkeypatch, _real_launcher(tmp_path), tmp_path / "share")
        t = ClaudeTarget()
        status_result = MagicMock(
            returncode=0,
            stdout=json.dumps({"loggedIn": True}),
        )
        with patch("kanibako.plugins.claude.target.subprocess.run", return_value=status_result):
            assert t.check_auth() is True

    def test_not_logged_in_triggers_login(self, tmp_path, monkeypatch):
        """check_auth runs login when status shows not loggedIn."""
        _anchor_contract(monkeypatch, _real_launcher(tmp_path), tmp_path / "share")
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
        with patch("kanibako.plugins.claude.target.subprocess.run",
                   side_effect=[status_not_logged, login_result, status_after_login]):
            assert t.check_auth() is True

    def test_login_fails_returns_false(self, tmp_path, monkeypatch):
        """check_auth returns False when login fails."""
        _anchor_contract(monkeypatch, _real_launcher(tmp_path), tmp_path / "share")
        t = ClaudeTarget()
        status_not_logged = MagicMock(
            returncode=0,
            stdout=json.dumps({"loggedIn": False}),
        )
        login_result = MagicMock(returncode=1)
        with patch("kanibako.plugins.claude.target.subprocess.run",
                   side_effect=[status_not_logged, login_result]):
            assert t.check_auth() is False

    def test_binary_not_found_returns_true(self, tmp_path, monkeypatch):
        """check_auth returns True when the contract launcher is absent."""
        _anchor_contract(monkeypatch, tmp_path / "bin" / "claude", tmp_path / "share")
        t = ClaudeTarget()
        with patch("kanibako.plugins.claude.target.shutil.which") as m_which:
            assert t.check_auth() is True
        m_which.assert_not_called()

    def test_status_command_fails_returns_true(self, tmp_path, monkeypatch):
        """check_auth returns True when auth status command fails."""
        _anchor_contract(monkeypatch, _real_launcher(tmp_path), tmp_path / "share")
        t = ClaudeTarget()
        status_result = MagicMock(returncode=1, stdout="")
        with patch("kanibako.plugins.claude.target.subprocess.run", return_value=status_result):
            assert t.check_auth() is True

    def test_exec_format_error_returns_true(self, tmp_path, monkeypatch):
        """A corrupt/0-byte binary raising OSError must not crash check_auth.

        Defense-in-depth: even if the launch-path guard ever fails to run
        first, the auth probe must never bubble an OSError (Exec format error)
        as an uncaught traceback -- treat it as auth-unknown and return True.
        """
        _anchor_contract(monkeypatch, _real_launcher(tmp_path), tmp_path / "share")
        t = ClaudeTarget()
        with patch(
            "kanibako.plugins.claude.target.subprocess.run",
            side_effect=OSError(8, "Exec format error"),
        ):
            assert t.check_auth() is True

    def test_anchors_to_contract_launcher(self, tmp_path, monkeypatch):
        """check_auth execs the contract launcher path, never shutil.which."""
        launcher = _real_launcher(tmp_path)
        _anchor_contract(monkeypatch, launcher, tmp_path / "share")
        t = ClaudeTarget()
        status_result = MagicMock(returncode=0, stdout=json.dumps({"loggedIn": True}))
        with patch("kanibako.plugins.claude.target.shutil.which") as m_which:
            with patch("kanibako.plugins.claude.target.subprocess.run",
                       return_value=status_result) as m_run:
                t.check_auth()
        m_which.assert_not_called()
        assert m_run.call_args.args[0][0] == str(launcher)

    def test_host_execs_disable_autoupdater(self, tmp_path, monkeypatch):
        """check_auth's host execs run with DISABLE_AUTOUPDATER=1 in env.

        Probing host auth must not wake Claude's async background updater
        mid-launch, which would prune/repoint the version we are about to bind.
        """
        _anchor_contract(monkeypatch, _real_launcher(tmp_path), tmp_path / "share")
        t = ClaudeTarget()
        status_result = MagicMock(
            returncode=0,
            stdout=json.dumps({"loggedIn": True}),
        )
        with patch("kanibako.plugins.claude.target.subprocess.run",
                   return_value=status_result) as m_run:
            t.check_auth()
        env = m_run.call_args.kwargs.get("env")
        assert env is not None
        assert env.get("DISABLE_AUTOUPDATER") == "1"


class TestPrepareHost:
    """prepare_host runs the synchronous update gate + host auth, safely."""

    def _install(self, tmp_path, *, anchor=True, monkeypatch=None):
        """Build an AgentInstall and (by default) anchor the contract launcher
        to a real file so prepare_host runs (it gates on _LAUNCHER, not which)."""
        launcher = tmp_path / "bin" / "claude"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_bytes(b"#!/bin/sh\n")
        launcher.chmod(0o755)
        install_dir = tmp_path / "share" / "claude"
        if anchor and monkeypatch is not None:
            _anchor_contract(monkeypatch, launcher, install_dir)
        return AgentInstall(
            name="claude", binary=launcher, install_dir=install_dir, launcher=launcher,
        )

    def test_runs_synchronous_update_gate(self, tmp_path, monkeypatch):
        """prepare_host runs `claude update` synchronously, env-disabled updater."""
        t = ClaudeTarget()
        install = self._install(tmp_path, monkeypatch=monkeypatch)
        update_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("kanibako.plugins.claude.target.shutil.which") as m_which:
            with patch("kanibako.plugins.claude.target.subprocess.run",
                       return_value=update_result) as m_run:
                t.prepare_host(install, auto_auth=False, data_path=tmp_path)
        # No PATH resolution: which is never consulted.
        m_which.assert_not_called()
        # The update gate must have been invoked synchronously.
        update_calls = [
            c for c in m_run.call_args_list if c.args and c.args[0][1:] == ["update"]
        ]
        assert len(update_calls) == 1
        # Execs the contract launcher path.
        assert update_calls[0].args[0][0] == str(install.launcher)
        # And with the auto-updater disabled in the exec environment.
        env = update_calls[0].kwargs.get("env")
        assert env is not None and env.get("DISABLE_AUTOUPDATER") == "1"

    def test_no_binary_is_noop(self, tmp_path, monkeypatch):
        """No contract launcher -> prepare_host is a no-op (never crashes)."""
        t = ClaudeTarget()
        install = self._install(tmp_path, anchor=False)
        # Anchor to a path that does NOT exist.
        _anchor_contract(monkeypatch, tmp_path / "absent" / "claude", tmp_path / "share")
        with patch("kanibako.plugins.claude.target.shutil.which") as m_which:
            with patch("kanibako.plugins.claude.target.subprocess.run") as m_run:
                t.prepare_host(install, auto_auth=True, data_path=tmp_path)
        m_which.assert_not_called()
        m_run.assert_not_called()

    def test_update_failure_does_not_raise(self, tmp_path, monkeypatch):
        """A failing/erroring `claude update` must not crash the launch."""
        t = ClaudeTarget()
        install = self._install(tmp_path, monkeypatch=monkeypatch)
        with patch("kanibako.plugins.claude.target.subprocess.run",
                   side_effect=OSError(8, "Exec format error")):
            # Must return cleanly, not raise.
            t.prepare_host(install, auto_auth=False, data_path=tmp_path)

    def test_update_timeout_does_not_raise(self, tmp_path, monkeypatch):
        """A `claude update` that times out must not crash the launch."""
        import subprocess as _sp
        t = ClaudeTarget()
        install = self._install(tmp_path, monkeypatch=monkeypatch)
        with patch("kanibako.plugins.claude.target.subprocess.run",
                   side_effect=_sp.TimeoutExpired("claude", 300)):
            t.prepare_host(install, auto_auth=False, data_path=tmp_path)

    def test_auto_auth_invokes_refresh_with_disabled_updater(self, tmp_path, monkeypatch):
        """When auto_auth is set, auto_refresh_auth is called with disabled env."""
        t = ClaudeTarget()
        install = self._install(tmp_path, monkeypatch=monkeypatch)
        update_result = MagicMock(returncode=0, stdout="", stderr="")
        fake_auth = MagicMock(success=True, error=None)
        with patch("kanibako.plugins.claude.target.subprocess.run",
                   return_value=update_result):
            with patch("kanibako.auth_browser.auto_refresh_auth",
                       return_value=fake_auth) as m_auth:
                t.prepare_host(install, auto_auth=True, data_path=tmp_path)
        m_auth.assert_called_once()
        env = m_auth.call_args.kwargs.get("env")
        assert env is not None and env.get("DISABLE_AUTOUPDATER") == "1"

    def test_auto_auth_skipped_when_disabled(self, tmp_path, monkeypatch):
        """When auto_auth is False, auto_refresh_auth is not called."""
        t = ClaudeTarget()
        install = self._install(tmp_path, monkeypatch=monkeypatch)
        update_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("kanibako.plugins.claude.target.subprocess.run",
                   return_value=update_result):
            with patch("kanibako.auth_browser.auto_refresh_auth") as m_auth:
                t.prepare_host(install, auto_auth=False, data_path=tmp_path)
        m_auth.assert_not_called()


class TestRefreshCredentials:
    def test_calls_credential_function(self, tmp_path, monkeypatch):
        """refresh_credentials delegates to refresh_host_to_project."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)

        fake_home = tmp_path / "fake_user_home"
        (fake_home / ".claude").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        t = ClaudeTarget()
        with patch("kanibako.plugins.claude.target.refresh_host_to_project") as m_h2p:
            t.refresh_credentials(home)

        m_h2p.assert_called_once()
        host_creds = m_h2p.call_args[0][0]
        project_creds = m_h2p.call_args[0][1]
        assert host_creds == fake_home / ".claude" / ".credentials.json"
        assert project_creds == home / ".claude" / ".credentials.json"


class TestDefaultShares:
    """Part 3a: claude declares plugins + cache as AGENT-scope ``shared`` entries.

    The old PROJECT ``resource_mappings`` abstraction was deleted (those dirs live
    in the box home bind, fresh per box); plugins + cache are now category
    ``agent.shared.*`` defaults rooted at ``@system.agents/claude``.
    """

    def test_declares_plugins_and_cache(self):
        t = ClaudeTarget()
        shares = t.default_shares()
        # STRUCTURED form (spec §2a): each value is a (host_src, box_dest) tuple,
        # NOT a colon-joined string.
        assert shares == {
            "agent.shared.plugins": ("plugins", "/home/agent/.claude/plugins"),
            "agent.shared.cache": ("cache", "/home/agent/.claude/cache"),
        }

    def test_share_values_are_relative_host_src(self):
        """host_src is the relative key name (joined under the agent store root)."""
        t = ClaudeTarget()
        for value in t.default_shares().values():
            host_src, box_dest = value
            assert not host_src.startswith("/")
            assert box_dest.startswith("/home/agent/.claude/")


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
        assert descriptors["access"].choices == ("permissive", "restricted")


class TestGenerateAgentConfig:
    def test_returns_claude_defaults(self):
        t = ClaudeTarget()
        cfg = t.generate_agent_config()
        assert cfg.name == "Claude Code"
        assert cfg.state == {"model": "opus", "access": "permissive"}
        assert cfg.run_args == []
        assert cfg.env == {}

    def test_is_crab_config_instance(self):
        from kanibako.agent_config import AgentConfig
        t = ClaudeTarget()
        cfg = t.generate_agent_config()
        assert isinstance(cfg, AgentConfig)


class TestApplyState:
    # Every Claude container invocation gets DISABLE_AUTOUPDATER=1 so the
    # in-container agent cannot self-update mid-session and repoint its
    # writable launcher to a version the read-only host bind cannot have.
    _BASE_ENV = {"DISABLE_AUTOUPDATER": "1"}

    def test_model_translated_to_cli_arg(self):
        t = ClaudeTarget()
        cli_args, env_vars = t.apply_state({"model": "opus"})
        assert cli_args == ["--model", "opus"]
        assert env_vars == self._BASE_ENV

    def test_unknown_keys_ignored(self):
        t = ClaudeTarget()
        cli_args, env_vars = t.apply_state({"unknown_key": "value"})
        assert cli_args == []
        assert env_vars == self._BASE_ENV

    def test_empty_state(self):
        t = ClaudeTarget()
        cli_args, env_vars = t.apply_state({})
        assert cli_args == []
        assert env_vars == self._BASE_ENV

    def test_disable_autoupdater_always_present(self):
        """DISABLE_AUTOUPDATER=1 is set regardless of state contents."""
        t = ClaudeTarget()
        for state in ({}, {"model": "opus"}, {"unknown": "x"}):
            _, env_vars = t.apply_state(state)
            assert env_vars.get("DISABLE_AUTOUPDATER") == "1"

    def test_model_with_other_keys(self):
        t = ClaudeTarget()
        cli_args, env_vars = t.apply_state({"model": "sonnet", "access": "permissive"})
        assert cli_args == ["--model", "sonnet"]
        assert env_vars == self._BASE_ENV

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
        with patch("kanibako.plugins.claude.target.writeback_project_to_host") as m_wb:
            t.writeback_credentials(home)

        m_wb.assert_called_once()
        project_creds = m_wb.call_args[0][0]
        assert project_creds == home / ".claude" / ".credentials.json"


class TestDescriptor:
    """The declarative PluginDescriptor (DORMANT this phase — not yet consumed)."""

    def test_is_plugin_descriptor(self):
        d = ClaudeTarget().descriptor
        assert isinstance(d, PluginDescriptor)

    def test_command(self):
        assert ClaudeTarget().descriptor.command == ("claude",)

    def test_bindings(self):
        d = ClaudeTarget().descriptor
        bindings = {b.key: b for b in d.bindings}
        # Part 3a: the ``plugins`` SHARED_STORE binding was removed; plugins (and
        # cache) are now AGENT-scope ``shared`` category entries (default_shares),
        # so only the two AGENT_CRITICAL delivery binds remain.
        assert set(bindings) == {"share", "launcher"}

        share = bindings["share"]
        assert share.origin == HostSrcOrigin.INSTALL_DIR
        assert share.box_dest == "/home/agent/.local/share/claude"
        assert share.kind == BindKind.DIR
        assert share.scope == BindScope.AGENT_CRITICAL
        assert share.ro is True

        launcher = bindings["launcher"]
        assert launcher.origin == HostSrcOrigin.LAUNCHER
        assert launcher.box_dest == "/home/agent/.local/bin/claude"
        assert launcher.kind == BindKind.FILE
        assert launcher.scope == BindScope.AGENT_CRITICAL
        assert launcher.ro is True

    def test_mode(self):
        d = ClaudeTarget().descriptor
        assert d.mode["start"] == ()
        assert d.mode["continue"] == ("--continue",)
        # resume intentionally cut (user 2026-06-17): {start, continue} only.
        assert "resume" not in d.mode

    def test_operations_exec(self):
        d = ClaudeTarget().descriptor
        assert "exec" in d.operations
        assert d.operations["exec"].fragment == ("-p",)

    def test_safe_bypass(self):
        sb = ClaudeTarget().descriptor.safe_bypass
        assert sb is not None
        assert sb.channel == Channel.FLAG
        assert sb.flag == ("--dangerously-skip-permissions",)
        assert sb.setting_key == "access"

    def test_settings_model(self):
        d = ClaudeTarget().descriptor
        assert len(d.settings) == 1
        model = d.settings[0]
        assert model.setting_key == "model"
        assert model.channel == Channel.FLAG
        assert model.flag == ("--model",)

    def test_container_env(self):
        env = ClaudeTarget().descriptor.container_env
        assert env["DISABLE_AUTOUPDATER"] == "1"
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"

    def test_cred_files(self):
        # The host .claude.json config IMPORT (SEED_ONCE) was removed in 1.6.0;
        # only the synced .credentials.json remains.
        d = ClaudeTarget().descriptor
        specs = {s.home_rel: s for s in d.cred_files}
        assert set(specs) == {".claude/.credentials.json"}

        creds = specs[".claude/.credentials.json"]
        assert creds.host_rel == ".claude/.credentials.json"
        assert creds.cadence == Cadence.SYNC
        assert creds.mtime_gate is True
        assert creds.filtered is True

    def test_host_prep_and_init_dirs(self):
        d = ClaudeTarget().descriptor
        assert d.host_prep is True
        assert d.init_dirs == (".claude",)


class TestTransformCred:
    """transform_cred — PURE content op (engine owns gating)."""

    _CREDS_SPEC = CredFileSpec(
        ".claude/.credentials.json", ".claude/.credentials.json",
        cadence=Cadence.SYNC, mtime_gate=True, filtered=True,
    )

    def test_credentials_in_dst_absent_wholesale_copy(self, tmp_path):
        """.credentials.json "in" with project absent -> wholesale copy."""
        src = tmp_path / "host" / ".credentials.json"
        src.parent.mkdir(parents=True)
        src.write_text(json.dumps({"claudeAiOauth": {"token": "x"}, "extra": True}))
        dst = tmp_path / "home" / ".claude" / ".credentials.json"

        ClaudeTarget().transform_cred(self._CREDS_SPEC, src, dst, "in")

        data = json.loads(dst.read_text())
        assert data["claudeAiOauth"]["token"] == "x"
        assert data["extra"] is True

    def test_credentials_in_dst_present_merges_oauth(self, tmp_path):
        """.credentials.json "in" with project present -> oauth merged, other keys kept."""
        src = tmp_path / "host" / ".credentials.json"
        src.parent.mkdir(parents=True)
        src.write_text(json.dumps({"claudeAiOauth": {"token": "new"}}))
        dst = tmp_path / "home" / ".claude" / ".credentials.json"
        dst.parent.mkdir(parents=True)
        dst.write_text(json.dumps({
            "claudeAiOauth": {"token": "old"},
            "projectOnlyKey": "keep-me",
        }))

        ClaudeTarget().transform_cred(self._CREDS_SPEC, src, dst, "in")

        data = json.loads(dst.read_text())
        assert data["claudeAiOauth"]["token"] == "new"
        assert data["projectOnlyKey"] == "keep-me"

    def test_credentials_out_wholesale_copy(self, tmp_path):
        """.credentials.json "out" -> wholesale project->host copy."""
        src = tmp_path / "home" / ".claude" / ".credentials.json"
        src.parent.mkdir(parents=True)
        src.write_text(json.dumps({"claudeAiOauth": {"token": "wb"}}))
        dst = tmp_path / "host" / ".claude" / ".credentials.json"

        ClaudeTarget().transform_cred(self._CREDS_SPEC, src, dst, "out")

        data = json.loads(dst.read_text())
        assert data["claudeAiOauth"]["token"] == "wb"

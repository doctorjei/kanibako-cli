"""Tests for kanibako.commands.start."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kanibako.commands.start import _apply_tweakcc, _run_container


class TestTargetWarnings:
    """Verify warnings when target detection fails."""

    def test_detect_returns_none_warns(self, start_mocks, capsys):
        """When detect() returns None, a warning should be printed."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )

        captured = capsys.readouterr()
        assert "Warning:" in captured.err
        assert "binary not found" in captured.err

    def test_no_agent_target_suppresses_warning(self, start_mocks, capsys):
        """When target has_binary=False and detect() returns None, no warning is printed."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            m.target.has_binary = False
            m.target.name = "no_agent"
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )

        captured = capsys.readouterr()
        assert "Warning:" not in captured.err

    def test_detect_returns_none_still_launches(self, start_mocks):
        """Container should still launch even when detection fails."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()

    def test_no_agent_target_still_launches(self, start_mocks):
        """Container should still launch with no_agent target."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            m.target.has_binary = False
            m.target.name = "no_agent"
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()

    def test_shell_mode_skips_target(self, start_mocks, capsys):
        """When entrypoint is set, target detection is skipped entirely."""
        with start_mocks() as m:
            m.resolve_target.side_effect = KeyError("should not be called")
            _run_container(
                project_dir=None,
                entrypoint="/bin/bash",
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )

        captured = capsys.readouterr()
        assert "Warning:" not in captured.err


class TestCheckAuth:
    """Verify pre-launch auth check behavior."""

    def test_auth_failure_returns_1(self, start_mocks, capsys):
        """When check_auth() returns False, start returns 1."""
        with start_mocks() as m:
            m.target.check_auth.return_value = False
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 1
            m.runtime.run.assert_not_called()

        captured = capsys.readouterr()
        assert "Authentication failed" in captured.err

    def test_auth_success_proceeds(self, start_mocks):
        """When check_auth() returns True, container launches normally."""
        with start_mocks() as m:
            m.target.check_auth.return_value = True
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()

    def test_auth_skipped_without_install(self, start_mocks):
        """When detect() returns None, check_auth is not called."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.check_auth.assert_not_called()

    def test_auth_skipped_in_shell_mode(self, start_mocks):
        """In shell mode (entrypoint set), check_auth is not called."""
        with start_mocks() as m:
            rc = _run_container(
                project_dir=None,
                entrypoint="/bin/bash",
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.check_auth.assert_not_called()


class TestDistinctAuth:
    """Verify distinct auth skips host credential sync."""

    def test_distinct_auth_skips_refresh(self, start_mocks):
        """When proj.auth == 'distinct', refresh_credentials is not called."""
        with start_mocks() as m:
            m.proj.auth = "distinct"
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.refresh_credentials.assert_not_called()
            m.target.writeback_credentials.assert_not_called()

    def test_distinct_auth_skips_check_auth(self, start_mocks):
        """When proj.auth == 'distinct', check_auth is not called."""
        with start_mocks() as m:
            m.proj.auth = "distinct"
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.check_auth.assert_not_called()

    def test_shared_auth_calls_refresh(self, start_mocks):
        """When proj.auth == 'shared', refresh_credentials is called."""
        with start_mocks() as m:
            m.proj.auth = "shared"
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.refresh_credentials.assert_called_once()


class TestStartArgs:
    """Verify CLI args are correctly passed through to container."""

    def test_claude_mode_adds_skip_permissions(self, start_mocks):
        """Default (no entrypoint) should inject --dangerously-skip-permissions."""
        with start_mocks() as m:
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )

            call_kwargs = m.runtime.run.call_args
            cli_args = call_kwargs.kwargs.get("cli_args", [])
            assert "--dangerously-skip-permissions" in cli_args
            assert "--continue" in cli_args

    def test_safe_mode_skips_permissions(self, start_mocks):
        with start_mocks() as m:
            m.proj.is_new = True
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=True,
                resume_mode=False,
                extra_args=[],
            )

            call_kwargs = m.runtime.run.call_args
            cli_args = call_kwargs.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args


class TestAgentConfigIntegration:
    """Verify agent config integration in _run_container."""

    def test_default_args_merged_into_cli(self, start_mocks):
        """Agent default_args are prepended to extra_args."""
        with start_mocks() as m:
            m.agent_cfg.default_args = ["--verbose"]
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=["--foo"],
            )
            m.target.build_cli_args.assert_called_once()
            call_kwargs = m.target.build_cli_args.call_args.kwargs
            assert call_kwargs["extra_args"] == ["--verbose", "--foo"]

    def test_apply_state_called(self, start_mocks):
        """target.apply_state() is called with agent_cfg.state."""
        with start_mocks() as m:
            m.agent_cfg.state = {"model": "opus"}
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            m.target.apply_state.assert_called_once_with({"model": "opus"})

    def test_state_args_appended_to_cli(self, start_mocks):
        """CLI args from apply_state() are appended to the final cli_args."""
        with start_mocks() as m:
            m.target.apply_state.return_value = (["--model", "opus"], {})
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--model" in cli_args
            assert "opus" in cli_args

    def test_agent_env_merged_into_container_env(self, start_mocks):
        """Agent [env] section values are included in container env."""
        with start_mocks() as m:
            m.agent_cfg.env = {"MY_VAR": "hello"}
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert env.get("MY_VAR") == "hello"

    def test_state_env_merged_into_container_env(self, start_mocks):
        """Env vars from apply_state() are included in container env."""
        with start_mocks() as m:
            m.target.apply_state.return_value = ([], {"STATE_VAR": "value"})
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert env.get("STATE_VAR") == "value"

    def test_shell_mode_uses_general_agent(self, start_mocks):
        """Shell mode (entrypoint set) loads 'general' agent config."""
        with start_mocks() as m:
            m.resolve_target.side_effect = KeyError("skip")
            _run_container(
                project_dir=None, entrypoint="/bin/bash", image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # agent_toml_path should have been called with agent_id="general"
            call_args = m.agent_toml_path.call_args
            assert call_args[0][1] == "general"


class TestTweakccIntegration:
    """Verify tweakcc patching in the container launch flow."""

    def test_disabled_by_default(self, start_mocks):
        """Empty tweakcc config → no patching, normal flow."""
        with start_mocks() as m:
            assert m.agent_cfg.tweakcc == {}
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()

    def test_enabled_calls_apply_tweakcc(self, start_mocks):
        """When tweakcc is enabled in agent config, _apply_tweakcc is called."""
        with start_mocks() as m:
            m.agent_cfg.tweakcc = {"enabled": True}
            m.load_agent_config.return_value = m.agent_cfg

            with patch("kanibako.commands.start._apply_tweakcc") as mock_apply:
                mock_apply.return_value = None  # disabled/failed
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
                mock_apply.assert_called_once()

    def test_patched_binary_used_in_mounts(self, start_mocks, tmp_path):
        """When tweakcc returns a patched install, binary_mounts uses it."""
        with start_mocks() as m:
            m.agent_cfg.tweakcc = {"enabled": True}
            m.load_agent_config.return_value = m.agent_cfg

            from kanibako.targets.base import AgentInstall, Mount
            from kanibako.tweakcc_cache import CacheEntry

            patched_binary = tmp_path / "patched"
            patched_binary.write_bytes(b"\x7fELF" + b"\x00" * 50)
            patched_install = AgentInstall(
                name="claude",
                binary=patched_binary,
                install_dir=tmp_path / "install",
            )
            fake_entry = CacheEntry(path=patched_binary, fd=-1)
            fake_cache = MagicMock()

            with patch("kanibako.commands.start._apply_tweakcc") as mock_apply:
                mock_apply.return_value = (patched_install, fake_entry, fake_cache)
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
                # binary_mounts should be called with the patched install
                m.target.binary_mounts.assert_called_once_with(patched_install)
                # cache should be released after container exits
                fake_cache.release.assert_called_once_with(fake_entry)

    def test_failure_falls_back(self, start_mocks):
        """When tweakcc fails, original binary is used (graceful fallback)."""
        with start_mocks() as m:
            m.agent_cfg.tweakcc = {"enabled": True}
            m.load_agent_config.return_value = m.agent_cfg

            with patch("kanibako.commands.start._apply_tweakcc") as mock_apply:
                mock_apply.return_value = None  # signals failure
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
                assert rc == 0
                # Original install used (binary_mounts called with mock install)
                m.target.binary_mounts.assert_called_once()

    def test_telemetry_disabled_for_claude(self, start_mocks):
        """CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 is set for Claude target."""
        with start_mocks() as m:
            m.target.name = "claude"
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1"

    def test_telemetry_not_overridden_by_user(self, start_mocks):
        """User can override telemetry setting via -e flag."""
        with start_mocks() as m:
            m.target.name = "claude"
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
                cli_env=["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=0"],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            # User's -e override takes priority (set after setdefault)
            assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "0"


class TestApplyTweakcc:
    """Unit tests for the _apply_tweakcc helper."""

    def test_disabled_returns_none(self, tmp_path):
        """When tweakcc is not enabled, returns None."""
        from kanibako.agents import AgentConfig

        install = MagicMock()
        agent_cfg = AgentConfig(tweakcc={})
        result = _apply_tweakcc(install, agent_cfg, tmp_path, MagicMock())
        assert result is None

    def test_enabled_but_empty_returns_none(self, tmp_path):
        """Enabled=False explicitly → returns None."""
        from kanibako.agents import AgentConfig

        install = MagicMock()
        agent_cfg = AgentConfig(tweakcc={"enabled": False})
        result = _apply_tweakcc(install, agent_cfg, tmp_path, MagicMock())
        assert result is None

    def test_bun_sea_error_returns_none(self, tmp_path):
        """BunSEAError during hash → returns None (graceful fallback)."""
        from kanibako.agents import AgentConfig
        from kanibako.bun_sea import BunSEAError

        install = MagicMock()
        agent_cfg = AgentConfig(tweakcc={"enabled": True})
        logger = MagicMock()

        with patch("kanibako.bun_sea.cli_js_hash") as mock_hash:
            mock_hash.side_effect = BunSEAError("bad binary")
            result = _apply_tweakcc(install, agent_cfg, tmp_path, logger)
            assert result is None
            logger.warning.assert_called_once()

    def test_cache_hit(self, tmp_path):
        """Cache hit → returns patched install without calling put."""
        from kanibako.agents import AgentConfig

        install = MagicMock()
        install.name = "claude"
        install.install_dir = tmp_path / "install"
        agent_cfg = AgentConfig(tweakcc={"enabled": True})
        logger = MagicMock()

        fake_entry = MagicMock()
        fake_entry.path = tmp_path / "cached_binary"

        with (
            patch("kanibako.bun_sea.cli_js_hash", return_value="abc123"),
            patch("kanibako.tweakcc_cache.TweakccCache") as MockCache,
        ):
            cache_instance = MockCache.return_value
            cache_instance.cache_key.return_value = "testkey"
            cache_instance.get.return_value = fake_entry

            result = _apply_tweakcc(install, agent_cfg, tmp_path, logger)

            assert result is not None
            patched_install, entry, cache = result
            assert patched_install.binary == fake_entry.path
            assert patched_install.install_dir == install.install_dir
            assert entry is fake_entry
            cache_instance.put.assert_not_called()

    def test_cache_miss_calls_put(self, tmp_path):
        """Cache miss → calls put with tweakcc command."""
        from kanibako.agents import AgentConfig

        install = MagicMock()
        install.name = "claude"
        install.binary = tmp_path / "binary"
        install.install_dir = tmp_path / "install"
        agent_cfg = AgentConfig(tweakcc={"enabled": True})
        logger = MagicMock()

        fake_entry = MagicMock()
        fake_entry.path = tmp_path / "cached"

        with (
            patch("kanibako.bun_sea.cli_js_hash", return_value="abc123"),
            patch("kanibako.tweakcc_cache.TweakccCache") as MockCache,
        ):
            cache_instance = MockCache.return_value
            cache_instance.cache_key.return_value = "testkey"
            cache_instance.get.return_value = None  # miss
            cache_instance.put.return_value = fake_entry

            result = _apply_tweakcc(install, agent_cfg, tmp_path, logger)

            assert result is not None
            cache_instance.put.assert_called_once()

    def test_returns_cache_object(self, tmp_path):
        """Returned tuple includes the cache object for later release."""
        from kanibako.agents import AgentConfig

        install = MagicMock()
        install.name = "claude"
        install.install_dir = tmp_path / "install"
        agent_cfg = AgentConfig(tweakcc={"enabled": True})
        logger = MagicMock()

        fake_entry = MagicMock()
        fake_entry.path = tmp_path / "cached"

        with (
            patch("kanibako.bun_sea.cli_js_hash", return_value="abc"),
            patch("kanibako.tweakcc_cache.TweakccCache") as MockCache,
        ):
            cache_instance = MockCache.return_value
            cache_instance.cache_key.return_value = "k"
            cache_instance.get.return_value = fake_entry

            result = _apply_tweakcc(install, agent_cfg, tmp_path, logger)
            _, _, cache_obj = result
            assert cache_obj is cache_instance

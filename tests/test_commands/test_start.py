"""Tests for kanibako.commands.start."""

from __future__ import annotations

from kanibako.commands.start import _run_container


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

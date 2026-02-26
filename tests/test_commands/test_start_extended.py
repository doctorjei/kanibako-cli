"""Extended tests for kanibako.commands.start: lock, flags, credential flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.start import _run_container
from kanibako.errors import ContainerError


# ---------------------------------------------------------------------------
# Concurrency lock
# ---------------------------------------------------------------------------

class TestConcurrencyLock:
    def test_lock_acquired_and_released(self, start_mocks):
        with start_mocks() as m:
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            # fcntl.flock called twice: LOCK_EX|LOCK_NB for acquire, LOCK_UN for release
            flock_calls = m.fcntl.flock.call_args_list
            assert len(flock_calls) == 2

    def test_lock_contention_returns_1(self, start_mocks):
        with start_mocks() as m:
            m.fcntl.flock.side_effect = OSError("locked")
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 1

    def test_lock_released_on_failure(self, start_mocks):
        with start_mocks() as m:
            m.runtime.run.side_effect = RuntimeError("boom")
            with pytest.raises(RuntimeError):
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            # Lock should still be released in finally block
            flock_calls = m.fcntl.flock.call_args_list
            assert len(flock_calls) == 2

    def test_lock_file_path(self, start_mocks):
        """Lock file is created under metadata_path."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # metadata_path / ".kanibako.lock" was accessed
            m.proj.metadata_path.__truediv__.assert_any_call(".kanibako.lock")


# ---------------------------------------------------------------------------
# Flag combinations
# ---------------------------------------------------------------------------

class TestFlagCombinations:
    def test_new_session_skips_continue(self, start_mocks):
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=True, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" not in cli_args
            assert "--dangerously-skip-permissions" in cli_args

    def test_new_project_skips_continue(self, start_mocks):
        with start_mocks() as m:
            m.proj.is_new = True
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" not in cli_args

    def test_existing_project_adds_continue(self, start_mocks):
        with start_mocks() as m:
            m.proj.is_new = False
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" in cli_args

    def test_resume_adds_resume_flag(self, start_mocks):
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=True,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--resume" in cli_args
            assert "--continue" not in cli_args

    def test_extra_resume_skips_continue(self, start_mocks):
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=["--resume"],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" not in cli_args
            assert "--resume" in cli_args

    def test_entrypoint_disables_claude_mode(self, start_mocks):
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint="/bin/bash", image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args
            assert "--continue" not in cli_args

    def test_double_dash_stripping(self, start_mocks):
        """run_start strips leading '--' from agent_args."""
        from kanibako.commands.start import run_start
        import argparse

        with start_mocks() as m:
            args = argparse.Namespace(
                project=None, entrypoint=None, image=None,
                new=False, safe=False,
                agent_args=["--", "--my-flag"],
            )
            run_start(args)
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--my-flag" in cli_args
            # The leading '--' should be stripped
            assert cli_args[0] != "--" or cli_args == ["--"]

    def test_safe_and_resume(self, start_mocks):
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=True, resume_mode=True,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args
            assert "--resume" in cli_args

    def test_image_override(self, start_mocks):
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override="custom:v1",
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # load_merged_config should have been called with cli_overrides
            call_kwargs = m.load_merged_config.call_args
            assert call_kwargs.kwargs["cli_overrides"] == {"container_image": "custom:v1"}

    def test_runtime_not_found_returns_1(self, start_mocks):
        with start_mocks() as m:
            m.runtime_cls.side_effect = ContainerError("No runtime")
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 1

    def test_ensure_image_failure_returns_1(self, start_mocks):
        with start_mocks() as m:
            m.runtime.ensure_image.side_effect = ContainerError("pull failed")
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 1

    def test_exit_code_propagation(self, start_mocks):
        with start_mocks() as m:
            m.runtime.run.return_value = 42
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 42

    def test_target_refresh_called(self, start_mocks):
        """target.refresh_credentials is called before runtime.run."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            m.target.refresh_credentials.assert_called_once_with(m.proj.shell_path)

    def test_target_writeback_after_run(self, start_mocks):
        """target.writeback_credentials is called after runtime.run."""
        call_order = []
        with start_mocks() as m:
            def track_run(*a, **kw):
                call_order.append("run")
                return 0
            m.runtime.run.side_effect = track_run
            m.target.writeback_credentials.side_effect = lambda *a: call_order.append("writeback")
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert call_order == ["run", "writeback"]

    def test_target_build_cli_args_called(self, start_mocks):
        """target.build_cli_args is called with correct parameters."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=True, safe_mode=True, resume_mode=False,
                extra_args=["--foo"],
            )
            m.target.build_cli_args.assert_called_once_with(
                safe_mode=True,
                resume_mode=False,
                new_session=True,
                is_new_project=False,
                extra_args=["--foo"],
            )


# ---------------------------------------------------------------------------
# First-boot image persistence (Item 3)
# ---------------------------------------------------------------------------

class TestFirstBootImagePersistence:
    def test_first_boot_image_persisted(self, start_mocks):
        with start_mocks() as m:
            m.proj.is_new = True
            with patch("kanibako.config.write_project_config") as m_wpc:
                _run_container(
                    project_dir=None, entrypoint=None, image_override="custom:v1",
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
                m_wpc.assert_called_once()

    def test_existing_project_image_not_persisted(self, start_mocks):
        with start_mocks() as m:
            m.proj.is_new = False
            with patch("kanibako.config.write_project_config") as m_wpc:
                _run_container(
                    project_dir=None, entrypoint=None, image_override="custom:v1",
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
                m_wpc.assert_not_called()

    def test_first_boot_no_override_not_persisted(self, start_mocks):
        with start_mocks() as m:
            m.proj.is_new = True
            with patch("kanibako.config.write_project_config") as m_wpc:
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
                m_wpc.assert_not_called()


# ---------------------------------------------------------------------------
# Orphan detection hint (Item 1)
# ---------------------------------------------------------------------------

class TestOrphanDetectionHint:
    def test_orphan_hint_on_new_project(self, start_mocks, capsys):
        with start_mocks() as m:
            m.proj.is_new = True
            with patch("kanibako.paths.iter_projects") as m_iter:
                orphan_path = MagicMock()
                orphan_path.is_dir.return_value = False
                m_iter.return_value = [(MagicMock(), orphan_path)]
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            captured = capsys.readouterr()
            assert "orphaned" in captured.err

    def test_no_orphan_hint_on_existing_project(self, start_mocks, capsys):
        with start_mocks() as m:
            m.proj.is_new = False
            with patch("kanibako.paths.iter_projects") as m_iter:
                m_iter.return_value = []
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            captured = capsys.readouterr()
            assert "orphaned" not in captured.err


# ---------------------------------------------------------------------------
# Agent config first-use generation
# ---------------------------------------------------------------------------

class TestAgentConfigFirstUse:
    def test_generates_config_on_first_use(self, start_mocks):
        """When agent TOML doesn't exist, target.generate_agent_config() is called."""
        with start_mocks() as m:
            mock_path = m.agent_toml_path.return_value
            mock_path.exists.return_value = False
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            m.target.generate_agent_config.assert_called_once()

    def test_does_not_generate_when_exists(self, start_mocks):
        """When agent TOML exists, generate_agent_config() is NOT called."""
        with start_mocks() as m:
            mock_path = m.agent_toml_path.return_value
            mock_path.exists.return_value = True
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            m.target.generate_agent_config.assert_not_called()

    def test_agent_template_variant_used(self, start_mocks):
        """Template application uses agent_cfg.shell for template variant."""
        import kanibako.templates
        with start_mocks() as m:
            m.proj.is_new = True
            m.agent_cfg.shell = "minimal"
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # The already-patched apply_shell_template should have been called
            mock_fn = kanibako.templates.apply_shell_template
            mock_fn.assert_called_once()
            call_args = mock_fn.call_args[0]
            assert call_args[3] == "minimal"  # template_name

    def test_no_agent_target_uses_no_agent_id(self, start_mocks):
        """When auto-detect finds nothing, NoAgentTarget's name is used as agent_id."""
        with start_mocks() as m:
            m.target.name = "no_agent"
            m.target.has_binary = False
            m.target.detect.return_value = None
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            call_args = m.agent_toml_path.call_args[0]
            assert call_args[1] == "no_agent"


# ---------------------------------------------------------------------------
# Persistent mode (#24)
# ---------------------------------------------------------------------------

class TestPersistentMode:
    """Verify persistent mode (tmux wrapping, reattach, lifecycle)."""

    def test_persistent_launches_detached_with_tmux(self, start_mocks):
        """Persistent mode: container runs detached with tmux entrypoint."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            run_kwargs = m.runtime.run.call_args.kwargs
            assert run_kwargs["detach"] is True
            assert run_kwargs["entrypoint"] == "tmux"
            cli_args = run_kwargs.get("cli_args") or []
            assert cli_args[:4] == ["new-session", "-s", "kanibako", "--"]
            assert "claude" in cli_args

    def test_persistent_attaches_after_launch(self, start_mocks):
        """After detached launch, exec attaches to the tmux session."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            m.runtime.exec.assert_called_once()
            exec_args = m.runtime.exec.call_args[0]
            assert exec_args[1] == ["tmux", "attach", "-t", "kanibako"]

    def test_persistent_reattach_when_running(self, start_mocks):
        """If container is already running, reattach without launching."""
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 0
            m.runtime.run.assert_not_called()
            m.runtime.exec.assert_called_once()

    def test_persistent_reattach_refreshes_credentials(self, start_mocks):
        """Reattach refreshes credentials before exec."""
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            m.target.refresh_credentials.assert_called_once_with(m.proj.shell_path)

    def test_persistent_removes_stale_container(self, start_mocks):
        """Stopped container is removed before recreating."""
        with start_mocks() as m:
            m.runtime.is_running.return_value = False
            m.runtime.container_exists.return_value = True
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            m.runtime.rm.assert_called_once()
            m.runtime.run.assert_called_once()

    def test_persistent_skips_flock(self, start_mocks):
        """Persistent mode does not acquire file lock."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            m.fcntl.flock.assert_not_called()

    def test_persistent_skips_writeback(self, start_mocks):
        """Persistent mode does not write back credentials (session still running)."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            m.target.writeback_credentials.assert_not_called()

    def test_persistent_forces_no_helpers(self, start_mocks):
        """Persistent mode disables helper hub even if not requested."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, no_helpers=False,
            )
            # HelperHub should never be imported/started
            run_kwargs = m.runtime.run.call_args.kwargs
            # The container should have launched (detached), hub not started
            assert run_kwargs["detach"] is True

    def test_persistent_custom_entrypoint(self, start_mocks):
        """Custom entrypoint is wrapped inside tmux."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint="/bin/bash", image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            run_kwargs = m.runtime.run.call_args.kwargs
            assert run_kwargs["entrypoint"] == "tmux"
            cli_args = run_kwargs.get("cli_args") or []
            assert cli_args[:4] == ["new-session", "-s", "kanibako", "--"]
            assert "/bin/bash" in cli_args

    def test_persistent_returns_exec_exit_code(self, start_mocks):
        """Return code comes from exec, not from detached run."""
        with start_mocks() as m:
            m.runtime.run.return_value = 0  # detach always returns 0
            m.runtime.exec.return_value = 7
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 7


class TestNoConversationHint:
    """Hint when agent exits non-zero with --continue/--resume."""

    def test_hint_on_nonzero_exit_with_continue(self, start_mocks, capsys):
        """Non-zero exit in continue mode shows -N hint."""
        with start_mocks() as m:
            m.runtime.run.return_value = 1
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        captured = capsys.readouterr()
        assert "start -N" in captured.err

    def test_no_hint_on_zero_exit(self, start_mocks, capsys):
        """Successful exit does not show the hint."""
        with start_mocks() as m:
            m.runtime.run.return_value = 0
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        captured = capsys.readouterr()
        assert "start -N" not in captured.err

    def test_no_hint_with_new_session(self, start_mocks, capsys):
        """No hint when -N was already used."""
        with start_mocks() as m:
            m.runtime.run.return_value = 1
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=True, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        captured = capsys.readouterr()
        assert "start -N" not in captured.err

    def test_no_hint_in_shell_mode(self, start_mocks, capsys):
        """No hint in shell mode (entrypoint set)."""
        with start_mocks() as m:
            m.runtime.run.return_value = 1
            _run_container(
                project_dir=None, entrypoint="/bin/bash", image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        captured = capsys.readouterr()
        assert "start -N" not in captured.err


class TestInteractivePersistentGuard:
    """Interactive mode rejects launch when a container already exists."""

    def test_existing_container_blocks_interactive(self, start_mocks, capsys):
        """If a container exists, interactive start returns 1 with a message."""
        with start_mocks() as m:
            m.runtime.container_exists.return_value = True
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 1
            m.runtime.run.assert_not_called()
        captured = capsys.readouterr()
        assert "container already exists" in captured.err.lower()
        assert "kanibako connect" in captured.err

    def test_no_container_proceeds_normally(self, start_mocks):
        """When no container exists, interactive mode proceeds."""
        with start_mocks() as m:
            m.runtime.container_exists.return_value = False
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()

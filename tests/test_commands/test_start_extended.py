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

    def test_resume_falls_through_to_continue(self, start_mocks):
        """Resume was cut from claude's descriptor (user 2026-06-17).

        ``-R``/resume_mode has no ``"resume"`` mode key to select, so
        assembly.resolve_mode falls through to ``--continue`` (continue-last) and
        never emits ``--resume`` for claude.
        """
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=True,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--resume" not in cli_args
            assert "--continue" in cli_args

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

    def test_safe_and_resume(self, start_mocks):
        """Secure (-S) drops the bypass flag; resume_mode falls through to
        --continue (claude's descriptor declares no "resume" mode key)."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=True, resume_mode=True,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args
            assert "--resume" not in cli_args
            assert "--continue" in cli_args

    def test_image_override(self, start_mocks):
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override="custom:v1",
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # load_merged_config should have been called with cli_overrides
            call_kwargs = m.load_merged_config.call_args
            assert call_kwargs.kwargs["cli_overrides"] == {"box_image": "custom:v1"}

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
        """Legacy (descriptor-less) target: refresh_credentials runs pre-launch.

        A descriptor-bearing target routes refresh through the credsync engine
        (TestCredsyncRouting), so this pins the legacy hook path explicitly.
        """
        with start_mocks() as m:
            m.target.descriptor = None
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            m.target.refresh_credentials.assert_called_once_with(m.proj.shell_path)

    def test_target_writeback_after_run(self, start_mocks):
        """Legacy (descriptor-less) target: writeback_credentials runs post-run.

        A descriptor-bearing target routes writeback through the credsync engine
        (TestCredsyncRouting), so this pins the legacy hook path explicitly.
        """
        call_order = []
        with start_mocks() as m:
            m.target.descriptor = None
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

    def test_argv_assembled_from_descriptor(self, start_mocks):
        """The agent argv is assembled from the descriptor (build_cli_args is gone).

        With new_session=True (no --continue), secure safe_mode=True (no
        --dangerously-skip-permissions), the only argv tail is the passed
        extra_args; assert the observable cli_args that reach runtime.run.
        """
        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=True, safe_mode=True, resume_mode=False,
                extra_args=["--foo"],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" not in cli_args            # new session
            assert "--dangerously-skip-permissions" not in cli_args  # secure
            assert "--foo" in cli_args                      # extra args appended


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
        """When agent config doesn't exist, target.generate_agent_config() is called."""
        with start_mocks() as m:
            m.agent_config_path.exists.return_value = False
            # Return a real AgentConfig so the (now YAML) write path can
            # serialize it — a bare MagicMock is not representable.
            from kanibako.agent_config import AgentConfig
            m.target.generate_agent_config.return_value = AgentConfig(name="claude")
            # The derived agent-config path (std.agents / "<id>.yaml") is a
            # MagicMock here; stub the writer so it never coerces that mock to a
            # literal "MagicMock" path and mkdir's it into the CWD.
            with patch("kanibako.commands.start.write_agent_config"):
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            m.target.generate_agent_config.assert_called_once()

    def test_does_not_generate_when_exists(self, start_mocks):
        """When agent config exists, generate_agent_config() is NOT called."""
        with start_mocks() as m:
            m.agent_config_path.exists.return_value = True
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            m.target.generate_agent_config.assert_not_called()

    def test_template_layers_applied_for_new_box(self, start_mocks):
        """A new box stages+seeds the ordered template layers once."""
        import kanibako.templates
        with start_mocks() as m:
            # B7 seed-at-create / membership model: the one-time home seed is
            # gated SOLELY on ``proj.is_new`` (a box that already exists in the
            # registry was already seeded).  ``_box_already_seeded`` was deleted,
            # so ``is_new = True`` alone drives the first-start seed path.
            m.proj.is_new = True
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # The already-patched stage_and_seed_templates should have been
            # called once, seeding the box home from the resolved layer specs.
            mock_fn = kanibako.templates.stage_and_seed_templates
            mock_fn.assert_called_once()
            # First positional arg = the box home (proj.shell_path).
            assert mock_fn.call_args[0][0] is m.proj.shell_path

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
            # The agent config path is std.agents / "no_agent" / "settings.yaml"
            # (settings live inside the per-agent store dir); std.agents also gets
            # a / "no_agent" / "share" call from the scoped-share resolver, so
            # check the full call list.
            div_args = [
                c[0][0]
                for c in m.load_std_paths.return_value.agents.__truediv__.call_args_list
            ]
            assert "no_agent" in div_args
            sub_args = [
                c[0][0]
                for c in m.load_std_paths.return_value.agents.__truediv__
                .return_value.__truediv__.call_args_list
            ]
            assert "settings.yaml" in sub_args


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
        """Reattach refreshes credentials before exec (legacy hook path).

        A descriptor-bearing target routes the reattach refresh through the
        credsync engine's tier orchestrator (refresh_box_credentials); this pins
        the legacy hook explicitly.
        """
        with start_mocks() as m:
            m.target.descriptor = None
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
            # Container dies after exec so we don't retry.
            _exec_calls = [0]
            def _exec_side(*a, **kw):
                _exec_calls[0] += 1
                m.runtime.is_running.return_value = False
                return 7
            m.runtime.exec.side_effect = _exec_side
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 7
            assert _exec_calls[0] == 1  # no retry when container died

    def test_persistent_exec_retries_on_transient_failure(self, start_mocks, capsys):
        """Exec retries when it fails but container is still running."""
        with start_mocks() as m:
            # First two execs fail (transient), third succeeds.
            m.runtime.exec.side_effect = [1, 1, 0]
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 0
            assert m.runtime.exec.call_count == 3
            captured = capsys.readouterr()
            assert "attempt 1/5" in captured.err
            assert "attempt 2/5" in captured.err

    def test_persistent_exec_no_retry_when_container_dies(self, start_mocks):
        """No retry when exec fails and container is no longer running."""
        with start_mocks() as m:
            def _exec_then_die(*a, **kw):
                m.runtime.is_running.return_value = False
                return 1
            m.runtime.exec.side_effect = _exec_then_die
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 1
            m.runtime.exec.assert_called_once()

    def test_persistent_exec_exhausts_retries(self, start_mocks, capsys):
        """After exhausting retries, returns last non-zero exit code."""
        with start_mocks() as m:
            m.runtime.exec.return_value = 1
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 1
            assert m.runtime.exec.call_count == 5
            captured = capsys.readouterr()
            # Warnings printed for attempts 1-4, not for the last one.
            assert "attempt 4/5" in captured.err
            assert "attempt 5/5" not in captured.err

    @staticmethod
    def _drive_fresh_exit(m, exit_rc: int = 0):
        """Model a fresh persistent launch that EXITS after attach.

        Fresh start => no stale container at the top (``container_exists`` False,
        so the pre-launch cleanup never fires).  ``run`` creates it (is_running
        True, exists True).  The attach exec returns and the box has exited
        (is_running False) but the container record still exists until removed,
        so the teardown can remove it.
        """
        exists = {"v": False}
        m.runtime.container_exists.side_effect = lambda *a, **kw: exists["v"]
        _orig_run = m.runtime.run.side_effect

        def _run_side(*a, **kw):
            exists["v"] = True
            return _orig_run(*a, **kw) if _orig_run else 0
        m.runtime.run.side_effect = _run_side

        def _exec_then_exit(*a, **kw):
            m.runtime.is_running.return_value = False  # tmux session ended
            return exit_rc
        m.runtime.exec.side_effect = _exec_then_exit

        def _rm_side(*a, **kw):
            exists["v"] = False
            return True
        m.runtime.rm.side_effect = _rm_side

    def test_persistent_removes_box_on_clean_exit(self, start_mocks):
        """Two-state lifecycle: an exited box is torn down after writeback.

        The in-box shell/agent exited -> tmux session ended -> container is not
        running after attach returns.  Writeback runs, then the container is
        removed so the next start/shell is fresh.
        """
        with start_mocks() as m:
            self._drive_fresh_exit(m)
            wb = MagicMock()
            with patch(
                "kanibako.commands.start.writeback_session_credentials", wb
            ):
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], persistent=True,
                )
            assert rc == 0
            m.runtime.rm.assert_called_once()
            assert wb.called

    def test_persistent_writeback_before_remove(self, start_mocks):
        """On clean exit, writeback is invoked before the container is removed."""
        with start_mocks() as m:
            self._drive_fresh_exit(m)
            events: list[str] = []
            _rm_state = m.runtime.rm.side_effect

            def _rm_side(*a, **kw):
                events.append("rm")
                return _rm_state(*a, **kw)
            m.runtime.rm.side_effect = _rm_side
            with patch(
                "kanibako.commands.start.writeback_session_credentials",
                side_effect=lambda *a, **kw: events.append("writeback"),
            ):
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], persistent=True,
                )
            assert events == ["writeback", "rm"]

    def test_persistent_keeps_box_on_detach(self, start_mocks):
        """A detached box (still running after attach) is kept, not removed."""
        with start_mocks() as m:
            # After the detached launch + attach, the container is STILL running
            # (Ctrl-b d / dropped client): is_running stays True (run side-effect
            # sets it True and exec doesn't clear it).
            m.runtime.exec.return_value = 0  # attach returns, container alive
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 0
            m.runtime.rm.assert_not_called()

    def test_persistent_removes_box_on_nonzero_exit(self, start_mocks):
        """Crisp model: a non-zero in-box exit still tears down the box."""
        with start_mocks() as m:
            self._drive_fresh_exit(m, exit_rc=3)
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 3
            m.runtime.rm.assert_called_once()

    def test_persistent_remove_failure_does_not_crash(self, start_mocks, capsys):
        """A removal failure logs a warning, never crashes or changes exit code."""
        with start_mocks() as m:
            self._drive_fresh_exit(m)
            m.runtime.rm.side_effect = RuntimeError("boom")
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            # Exit code preserved; no exception propagated.
            assert rc == 0

    def test_persistent_reattach_removes_box_on_exit(self, start_mocks):
        """Reattach path also tears down an exited box after writeback."""
        with start_mocks() as m:
            # Container already running -> reattach branch.
            m.runtime.is_running.return_value = True

            def _exec_then_exit(*a, **kw):
                m.runtime.is_running.return_value = False
                return 0
            m.runtime.exec.side_effect = _exec_then_exit
            m.runtime.container_exists.return_value = True
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 0
            m.runtime.run.assert_not_called()  # reattach, no new launch
            m.runtime.rm.assert_called_once()

    def test_persistent_reattach_keeps_box_on_detach(self, start_mocks):
        """Reattach + detach (still running) keeps the box."""
        with start_mocks() as m:
            m.runtime.is_running.return_value = True  # stays running (detach)
            m.runtime.exec.return_value = 0
            m.runtime.container_exists.return_value = True
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert rc == 0
            m.runtime.rm.assert_not_called()


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
        assert "already running" in captured.err.lower()
        assert "kanibako start" in captured.err

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


# ---------------------------------------------------------------------------
# New Phase 6 features: model override, CLI env, project extraction
# ---------------------------------------------------------------------------

class TestModelOverride:
    """Verify -M/--model override is applied to effective state."""

    def test_model_override_applied(self, start_mocks):
        """-M/--model overrides effective state -> --model <value> in the argv.

        The model value flows through effective_state into assembly's SettingArg
        emission; assert the observable --model flag on the launched argv.
        """
        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], model_override="opus",
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert cli_args[cli_args.index("--model") + 1] == "opus"

    def test_no_model_override(self, start_mocks):
        """Without a model override (and no crab model state), no --model flag."""
        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], model_override=None,
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--model" not in cli_args


class TestCliEnv:
    """Verify -e/--env KEY=VALUE vars are merged into container env."""

    def test_cli_env_merged(self, start_mocks):
        """Per-run env vars from -e are included in container env."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], cli_env=["MY_KEY=my_val", "OTHER=123"],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert env.get("MY_KEY") == "my_val"
            assert env.get("OTHER") == "123"

    def test_cli_env_overrides_agent_env(self, start_mocks):
        """Per-run env vars have highest priority over agent env."""
        with start_mocks() as m:
            m.agent_cfg.env = {"MY_KEY": "agent_val"}
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], cli_env=["MY_KEY=cli_val"],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert env.get("MY_KEY") == "cli_val"

    def test_no_cli_env(self, start_mocks):
        """No error when cli_env is None."""
        with start_mocks():
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], cli_env=None,
            )
            assert rc == 0

    def test_cli_env_passed_to_exec_into_running(self, start_mocks):
        """Per-run -e vars reach the exec'd process when the box is running.

        Shell mode (entrypoint set) against an already-running container execs
        in instead of launching; the per-run -e vars must still be applied
        (previously they were silently dropped on this path).
        """
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            rc = _run_container(
                project_dir=None, entrypoint="/bin/sh", image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=["-c", "printenv"], cli_env=["MY_KEY=my_val"],
            )
            assert rc == 0
            m.runtime.run.assert_not_called()
            m.runtime.exec.assert_called_once()
            env = m.runtime.exec.call_args.kwargs.get("env") or {}
            assert env.get("MY_KEY") == "my_val"


class TestProjectPositional:
    """Verify args.project is read directly by run_start."""

    def test_project_passed_through(self, start_mocks):
        """args.project is forwarded to resolve_any_project."""
        from kanibako.commands.start import run_start
        import argparse

        with start_mocks() as m:
            args = argparse.Namespace(
                entrypoint=None, image=None,
                new_session=False, continue_session=False,
                resume_session=False, autonomous=False, secure=False,
                model=None, env=None, persistent=False, ephemeral=False,
                no_helpers=False,
                project="/tmp/myproject",
                agent_args=[],
            )
            run_start(args)
            m.resolve_any_project.assert_called_once()
            call_args = m.resolve_any_project.call_args
            assert call_args[0][2] == "/tmp/myproject"

    def test_project_none_uses_cwd(self, start_mocks):
        """args.project=None lets resolve_any_project default to cwd."""
        from kanibako.commands.start import run_start
        import argparse

        with start_mocks() as m:
            args = argparse.Namespace(
                entrypoint=None, image=None,
                new_session=False, continue_session=False,
                resume_session=False, autonomous=False, secure=False,
                model=None, env=None, persistent=False, ephemeral=False,
                no_helpers=False,
                project=None,
                agent_args=[],
            )
            run_start(args)
            m.resolve_any_project.assert_called_once()
            call_args = m.resolve_any_project.call_args
            assert call_args[0][2] is None


class TestSecureAutonomousFlags:
    """Verify -A/--autonomous and -S/--secure flag mapping."""

    def test_secure_maps_to_safe_mode(self, start_mocks):
        """-S/--secure should enable safe_mode (no --dangerously-skip-permissions)."""
        from kanibako.commands.start import run_start
        import argparse

        with start_mocks() as m:
            m.proj.is_new = True
            args = argparse.Namespace(
                entrypoint=None, image=None,
                new_session=False, continue_session=False,
                resume_session=False, autonomous=False, secure=True,
                model=None, env=None, persistent=False, ephemeral=False,
                no_helpers=False,
                agent_args=[],
            )
            run_start(args)
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args

    def test_autonomous_maps_to_unsafe_mode(self, start_mocks):
        """-A/--autonomous should disable safe_mode (adds --dangerously-skip-permissions)."""
        from kanibako.commands.start import run_start
        import argparse

        with start_mocks() as m:
            args = argparse.Namespace(
                entrypoint=None, image=None,
                new_session=False, continue_session=False,
                resume_session=False, autonomous=True, secure=False,
                model=None, env=None, persistent=False, ephemeral=False,
                no_helpers=False,
                agent_args=[],
            )
            run_start(args)
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" in cli_args

    def test_default_is_autonomous(self, start_mocks):
        """Without -A or -S, default behavior is autonomous."""
        from kanibako.commands.start import run_start
        import argparse

        with start_mocks() as m:
            args = argparse.Namespace(
                entrypoint=None, image=None,
                new_session=False, continue_session=False,
                resume_session=False, autonomous=False, secure=False,
                model=None, env=None, persistent=False, ephemeral=False,
                no_helpers=False,
                agent_args=[],
            )
            run_start(args)
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" in cli_args


# ---------------------------------------------------------------------------
# Characterization: vault_tmpfs is mode-specific (pins behavior for #71 B0)
# ---------------------------------------------------------------------------

class TestVaultTmpfsMode:
    """Pin that there is NO default tmpfs mask in any box mode.

    The old unconditional ``~/workspace/vault`` mask default was DROPPED in
    1.6.0 — the vault was relocated out of the workspace, so there is nothing in
    ``~/workspace`` to mask.  start.py resolves masks through the ``box.masks``
    category model with no default, so with no config the mask list is empty in
    every box mode (default / workset / standalone).  A box (or any scope) may
    still declare masks via ``box.masks`` / ``<scope>.masks``.
    """

    def test_default_mode_has_no_mask(self, start_mocks):
        from pathlib import Path

        from kanibako.paths import ProjectGroup, BoxMode

        with start_mocks() as m:
            m.proj.mode = BoxMode.primary
            m.proj.group = ProjectGroup(
                name="default", root=Path("/data"),
                is_default=True, local_shared_base=Path("/data"),
            )
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # Empty mask list -> runtime.run is passed tmpfs_masks=None
            # (``tmpfs_masks or None`` at the call site), so no tmpfs mount
            # args are emitted.
            masks = m.runtime.run.call_args.kwargs.get("tmpfs_masks")
            assert masks is None

    def test_workset_mode_has_no_mask(self, start_mocks):
        from pathlib import Path

        from kanibako.paths import ProjectGroup, BoxMode

        with start_mocks() as m:
            m.proj.mode = BoxMode.named
            m.proj.group = ProjectGroup(
                name="ws", root=Path("/ws"),
                is_default=False, local_shared_base=Path("/ws"),
            )
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # Empty mask list -> runtime.run is passed tmpfs_masks=None
            # (``tmpfs_masks or None`` at the call site), so no tmpfs mount
            # args are emitted.
            masks = m.runtime.run.call_args.kwargs.get("tmpfs_masks")
            assert masks is None

    def test_standalone_mode_has_no_mask(self, start_mocks):
        from kanibako.paths import BoxMode

        with start_mocks() as m:
            m.proj.mode = BoxMode.standalone
            m.proj.group = None
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # Empty mask list -> runtime.run is passed tmpfs_masks=None
            # (``tmpfs_masks or None`` at the call site), so no tmpfs mount
            # args are emitted.
            masks = m.runtime.run.call_args.kwargs.get("tmpfs_masks")
            assert masks is None


class TestConfigurableBootstrap:
    """PART C: the persistent bootstrap program is configurable (default tmux)."""

    def test_default_bootstrap_is_tmux(self, start_mocks):
        with start_mocks() as m:
            # merged.box_bootstrap_program defaults to "tmux" in the fixture.
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            run_kwargs = m.runtime.run.call_args.kwargs
            assert run_kwargs["entrypoint"] == "tmux"
            assert run_kwargs["cli_args"][:4] == [
                "new-session", "-s", "kanibako", "--",
            ]
            exec_args = m.runtime.exec.call_args[0]
            assert exec_args[1] == ["tmux", "attach", "-t", "kanibako"]

    def test_non_tmux_bootstrap_execs_program_directly(self, start_mocks):
        with start_mocks() as m:
            m.merged.box_bootstrap_program = "zellij"
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            run_kwargs = m.runtime.run.call_args.kwargs
            # Non-tmux: program is exec'd with the inner command + args (no
            # tmux new-session shape).
            assert run_kwargs["entrypoint"] == "zellij"
            cli_args = run_kwargs["cli_args"]
            assert "new-session" not in cli_args
            assert cli_args[0] == "claude"  # inner command (default entrypoint)
            # Reattach uses the program bare (no -t kanibako session contract).
            exec_args = m.runtime.exec.call_args[0]
            assert exec_args[1] == ["zellij"]

    def test_non_tmux_reattach_when_running(self, start_mocks):
        with start_mocks() as m:
            m.merged.box_bootstrap_program = "zellij"
            m.runtime.is_running.return_value = True
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            exec_args = m.runtime.exec.call_args[0]
            assert exec_args[1] == ["zellij"]


class TestLaunchBaselineCheckIntegration:
    """PART D: the two-tier launch check gates / warns in _run_container."""

    def test_bootstrap_missing_hard_stops(self, start_mocks, capsys):
        from kanibako.commands.start import _BOOTSTRAP_MISSING

        with start_mocks() as m:
            m.launch_check.return_value = _BOOTSTRAP_MISSING
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
        assert rc == 1
        m.runtime.run.assert_not_called()

    def test_baseline_missing_proceeds(self, start_mocks):
        """Tier-2 missing baseline tools never block the launch."""
        with start_mocks() as m:
            m.launch_check.return_value = [("ripgrep", "rg")]
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            # Launch proceeds despite the tier-2 warning.
            m.runtime.run.assert_called_once()
        assert rc == 0

    def test_check_skipped_when_ephemeral(self, start_mocks):
        """Ephemeral launches don't use the bootstrap program, so the launch
        check (and its hard stop) is skipped entirely — even if the image lacks
        the bootstrap program."""
        from kanibako.commands.start import _BOOTSTRAP_MISSING

        with start_mocks() as m:
            m.launch_check.return_value = _BOOTSTRAP_MISSING
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=False,
            )
            # Check never runs; launch proceeds despite the bootstrap sentinel.
            m.launch_check.assert_not_called()
            m.runtime.run.assert_called_once()
        assert rc == 0


class TestCheckLaunchBaselineUnit:
    """PART D: _check_launch_baseline tiers + state-file surfacing (probe mocked)."""

    def _std(self, tmp_path):
        from types import SimpleNamespace
        return SimpleNamespace()  # only used for the state path via xdg

    def test_tier1_missing_returns_sentinel_with_shell_reminder(
        self, tmp_path, monkeypatch, capsys
    ):
        from kanibako.commands import start as start_mod

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        runtime = MagicMock()
        runtime.cmd = "podman"
        # Probe: bootstrap program 'tmux' is among the missing.
        with patch.object(
            start_mod, "probe_missing_executables", return_value=["tmux", "rg"]
        ):
            result = start_mod._check_launch_baseline(
                runtime, "img:latest", "tmux", "box1", self._std(tmp_path),
            )
        assert result is start_mod._BOOTSTRAP_MISSING
        err = capsys.readouterr().err
        assert "bootstrap program 'tmux'" in err
        # Shell-availability reminder so the user can investigate.
        assert "shell IS still available" in err
        assert "bash" in err

    def test_tier2_missing_warns_and_persists(
        self, tmp_path, monkeypatch, capsys
    ):
        from kanibako.commands import start as start_mod
        from kanibako import baseline as baseline_mod

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        runtime = MagicMock()
        runtime.cmd = "podman"

        # Use a known baseline so we can assert the missing exe maps to a pkg.
        monkeypatch.setattr(
            baseline_mod, "load_baseline",
            lambda: {"tmux": ["tmux"], "ripgrep": ["rg"]},
        )
        # Bootstrap present (tmux), but 'rg' missing.
        with patch.object(
            start_mod, "probe_missing_executables", return_value=["rg"]
        ):
            result = start_mod._check_launch_baseline(
                runtime, "img:latest", "tmux", "box1", self._std(tmp_path),
            )
        assert result == [("ripgrep", "rg")]
        # Persisted to the state file.
        issues = start_mod._launch_issues_path(self._std(tmp_path), "box1")
        assert issues.is_file()
        assert "ripgrep: rg" in issues.read_text()
        # No pre-launch print: the warning is surfaced once, post-session.
        assert capsys.readouterr().err == ""
        # Reprint surfaces the missing tools.
        start_mod._print_launch_issues(self._std(tmp_path), "box1")
        err = capsys.readouterr().err
        assert "missing baseline tools" in err
        assert "ripgrep: rg" in err

    def test_tier2_clean_clears_stale_state(self, tmp_path, monkeypatch):
        from kanibako.commands import start as start_mod
        from kanibako import baseline as baseline_mod

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        runtime = MagicMock()
        runtime.cmd = "podman"
        monkeypatch.setattr(
            baseline_mod, "load_baseline", lambda: {"tmux": ["tmux"]},
        )
        # Pre-seed a stale issues file.
        issues = start_mod._launch_issues_path(self._std(tmp_path), "box1")
        issues.parent.mkdir(parents=True, exist_ok=True)
        issues.write_text("ripgrep: rg\n")
        with patch.object(
            start_mod, "probe_missing_executables", return_value=[]
        ):
            result = start_mod._check_launch_baseline(
                runtime, "img:latest", "tmux", "box1", self._std(tmp_path),
            )
        assert result == []
        assert not issues.exists()

    def test_print_launch_issues_reprints(self, tmp_path, monkeypatch, capsys):
        from kanibako.commands import start as start_mod

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        issues = start_mod._launch_issues_path(self._std(tmp_path), "box1")
        issues.parent.mkdir(parents=True, exist_ok=True)
        issues.write_text("ripgrep: rg\n")
        start_mod._print_launch_issues(self._std(tmp_path), "box1")
        err = capsys.readouterr().err
        assert "missing baseline tools" in err
        assert "ripgrep: rg" in err

    def test_shadow_issues_persist_and_reprint(self, tmp_path, monkeypatch, capsys):
        from kanibako.commands import start as start_mod

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        std = self._std(tmp_path)
        dests = ["/home/agent/vault/rw", "/home/agent/.local/bin/foo"]
        start_mod._persist_shadow_issues(std, "box1", dests)
        # Persisted one-per-line.
        issues = start_mod._shadow_issues_path(std, "box1")
        assert issues.is_file()
        assert issues.read_text() == "/home/agent/vault/rw\n/home/agent/.local/bin/foo\n"
        # No pre-launch print: the warning is surfaced once, post-session.
        assert capsys.readouterr().err == ""
        # Reprint surfaces the dests.
        start_mod._print_shadow_issues(std, "box1")
        err = capsys.readouterr().err
        assert "shadow pre-existing files" in err
        assert "/home/agent/vault/rw" in err
        assert "/home/agent/.local/bin/foo" in err

    def test_shadow_issues_empty_clears_state(self, tmp_path, monkeypatch):
        from kanibako.commands import start as start_mod

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        std = self._std(tmp_path)
        issues = start_mod._shadow_issues_path(std, "box1")
        issues.parent.mkdir(parents=True, exist_ok=True)
        issues.write_text("/home/agent/vault/rw\n")
        start_mod._persist_shadow_issues(std, "box1", [])
        assert not issues.exists()

    def test_single_probe_covers_bootstrap_and_baseline(
        self, tmp_path, monkeypatch
    ):
        """Only ONE ephemeral probe runs, checking bootstrap + all baseline."""
        from kanibako.commands import start as start_mod
        from kanibako import baseline as baseline_mod

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        runtime = MagicMock()
        runtime.cmd = "podman"
        monkeypatch.setattr(
            baseline_mod, "load_baseline",
            lambda: {"tmux": ["tmux"], "ripgrep": ["rg"]},
        )
        with patch.object(
            start_mod, "probe_missing_executables", return_value=[]
        ) as mock_probe:
            start_mod._check_launch_baseline(
                runtime, "img:latest", "tmux", "box1", self._std(tmp_path),
            )
        assert mock_probe.call_count == 1
        probed = mock_probe.call_args[0][2]
        # tmux appears once (bootstrap + baseline dedup), rg included.
        assert probed[0] == "tmux"
        assert probed.count("tmux") == 1
        assert "rg" in probed


# ---------------------------------------------------------------------------
# Reattach: source the agent from the running container (no Gate-2a)
# ---------------------------------------------------------------------------

class TestReattachAgentSourcing:
    """A persistent box that is ALREADY RUNNING reattaches by sourcing its
    agent from the container's KANIBAKO_AGENT stamp, bypassing resolve_agent's
    Gate-2a (which would otherwise fire with 2+ agents and no default)."""

    def _gate2a_unless_explicit(self):
        """resolve_agent stand-in: raises Gate-2a unless an explicit agent is
        supplied — i.e. only the container-sourced injection can satisfy it."""
        from kanibako.errors import NoAgentSelectedError

        def _fn(*, explicit_agent, **kw):
            if explicit_agent:
                return explicit_agent
            raise NoAgentSelectedError("pick an agent")
        return _fn

    def test_reattach_sources_stored_agent_no_gate2a(self, start_mocks, capsys):
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            m.runtime.inspect_env.return_value = "claude"
            m.resolve_agent.side_effect = self._gate2a_unless_explicit()
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, explicit_agent=None,
            )
            assert rc == 0
            # Reattach exec'd (not a fresh run).
            assert m.runtime.exec.called
            assert not m.runtime.run.called
            # Cred refresh ran for the sourced agent (descriptor path).
            assert m.credsync.refresh_box_credentials.called
            # Heads-up went to STDERR, names the box + agent.
            err = capsys.readouterr().err
            assert "Reattaching to running box 'testproject'" in err
            assert "agent: claude" in err

    def test_reattach_matching_explicit_agent_ok(self, start_mocks):
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            m.runtime.inspect_env.return_value = "claude"
            m.resolve_agent.side_effect = self._gate2a_unless_explicit()
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, explicit_agent="claude",
            )
            assert rc == 0
            assert m.runtime.exec.called

    def test_reattach_mismatched_explicit_agent_errors(self, start_mocks):
        from kanibako.errors import KanibakoError
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            m.runtime.inspect_env.return_value = "claude"
            with pytest.raises(KanibakoError) as exc:
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], persistent=True, explicit_agent="goose",
                )
            msg = str(exc.value)
            assert "already running agent 'claude'" in msg
            assert "--agent 'goose'" in msg
            assert "kanibako stop testproject" in msg

    def test_reattach_differing_default_superseded_silently(
        self, start_mocks, capsys
    ):
        """A differing system DEFAULT (not an explicit --agent) does NOT error;
        the running box's stored agent wins."""
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            m.runtime.inspect_env.return_value = "claude"
            # explicit_agent is None (default would resolve to something else),
            # so the stored agent is injected and used — no error.
            m.resolve_agent.side_effect = self._gate2a_unless_explicit()
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, explicit_agent=None,
            )
            assert rc == 0
            # resolve_agent saw the injected stored agent.
            assert m.resolve_agent.call_args.kwargs["explicit_agent"] == "claude"

    def test_preexisting_running_box_no_stamp_falls_back(self, start_mocks):
        """A box running before this change has no KANIBAKO_AGENT (inspect_env
        -> None): no injection, normal resolution applies (Gate-2a if no
        default — unchanged behavior)."""
        from kanibako.errors import NoAgentSelectedError
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            m.runtime.inspect_env.return_value = None
            m.resolve_agent.side_effect = self._gate2a_unless_explicit()
            with pytest.raises(NoAgentSelectedError):
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], persistent=True, explicit_agent=None,
                )


# ---------------------------------------------------------------------------
# Fresh launch stamps KANIBAKO_AGENT on the container
# ---------------------------------------------------------------------------

class TestAgentStamp:
    def test_fresh_launch_stamps_agent_env(self, start_mocks):
        """A real agent launch sets KANIBAKO_AGENT=<agent> in the built env."""
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            env = m.runtime.run.call_args.kwargs["env"]
            assert env["KANIBAKO_AGENT"] == "claude"

    def test_shell_launch_does_not_stamp_agent_env(self, start_mocks):
        """A no-agent / shell launch (target None) carries no KANIBAKO_AGENT."""
        with start_mocks() as m:
            m.resolve_target.return_value.descriptor = None
            _run_container(
                project_dir=None, entrypoint="bash", image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, box_shell_mode=True,
            )
            env = m.runtime.run.call_args.kwargs["env"]
            assert "KANIBAKO_AGENT" not in env

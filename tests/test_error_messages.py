"""Tests for user-facing error messages: clarity, terminology, and suggested actions."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.start import _run_container
from kanibako.commands.stop import run
from kanibako.errors import ContainerError


class TestRuntimeNotFoundMessages:
    """Verify helpful message when no container runtime is installed."""

    def test_start_runtime_missing_shows_install_hint(self, start_mocks, capsys):
        """Missing runtime suggests installing podman with URL."""
        with start_mocks() as m:
            m.runtime_cls.side_effect = ContainerError("No runtime")
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        assert rc == 1
        err = capsys.readouterr().err
        assert "podman" in err
        assert "https://podman.io/" in err or "Docker" in err

    def test_stop_runtime_missing_shows_install_hint(self, capsys):
        """Stop command also shows a helpful message when runtime is missing."""
        with patch(
            "kanibako.commands.stop.ContainerRuntime",
            side_effect=ContainerError("No runtime"),
        ):
            args = argparse.Namespace(
                all_containers=False, project=None, force=False,
            )
            rc = run(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "podman" in err

    def test_container_error_includes_url(self):
        """ContainerError._detect() message includes podman URL."""
        from kanibako.container import ContainerRuntime

        with patch("shutil.which", return_value=None), \
             patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ContainerError) as exc_info:
                ContainerRuntime()
            assert "https://podman.io/" in str(exc_info.value)


class TestImagePullFailureMessages:
    """Verify that image pull failure suggests rig rebuild."""

    def test_ensure_image_failure_suggests_rig_rebuild(self):
        """When image can't be pulled or built, error mentions rig rebuild."""
        from kanibako.container import ContainerRuntime

        rt = MagicMock(spec=ContainerRuntime)
        rt.cmd = "podman"
        real_rt = ContainerRuntime.__new__(ContainerRuntime)
        real_rt.cmd = "podman"

        with patch.object(ContainerRuntime, "image_exists", return_value=False), \
             patch.object(ContainerRuntime, "pull", return_value=False), \
             patch.object(ContainerRuntime, "_guess_containerfile", return_value=None):
            with pytest.raises(ContainerError) as exc_info:
                real_rt.ensure_image("kanibako-custom:v1", MagicMock())
            msg = str(exc_info.value)
            assert "rig" in msg.lower()
            assert "kanibako rig rebuild" in msg

    def test_ensure_image_no_containerfile_suggests_rebuild(self):
        """When Containerfile is missing, error suggests rig rebuild."""
        from kanibako.container import ContainerRuntime

        real_rt = ContainerRuntime.__new__(ContainerRuntime)
        real_rt.cmd = "podman"

        with patch.object(ContainerRuntime, "image_exists", return_value=False), \
             patch.object(ContainerRuntime, "pull", return_value=False), \
             patch.object(ContainerRuntime, "_guess_containerfile", return_value="kanibako"), \
             patch("kanibako.container.get_containerfile", return_value=None):
            with pytest.raises(ContainerError) as exc_info:
                real_rt.ensure_image("kanibako-oci:latest", MagicMock())
            msg = str(exc_info.value)
            assert "kanibako rig rebuild" in msg


class TestLockConflictMessages:
    """Verify lock conflict message suggests kanibako stop."""

    def test_lock_conflict_suggests_stop(self, start_mocks, capsys):
        """Lock contention error mentions kanibako stop."""
        with start_mocks() as m:
            m.fcntl.flock.side_effect = OSError("locked")
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        assert rc == 1
        err = capsys.readouterr().err
        assert "kanibako stop" in err

    def test_lock_conflict_mentions_shell_alternative(self, start_mocks, capsys):
        """Lock contention error also mentions kanibako shell as alternative."""
        with start_mocks() as m:
            m.fcntl.flock.side_effect = OSError("locked")
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        err = capsys.readouterr().err
        assert "kanibako shell" in err


class TestAuthFailureMessages:
    """Verify auth failure suggests reauth."""

    def test_auth_failure_suggests_reauth(self, start_mocks, capsys):
        """Auth failure mentions kanibako crab reauth."""
        with start_mocks() as m:
            m.target.check_auth.return_value = False
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        assert rc == 1
        err = capsys.readouterr().err
        assert "kanibako crab reauth" in err

    def test_auth_failure_suggests_shell(self, start_mocks, capsys):
        """Auth failure also suggests kanibako shell as escape hatch."""
        with start_mocks() as m:
            m.target.check_auth.return_value = False
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        err = capsys.readouterr().err
        assert "kanibako shell" in err


class TestContainerExistsMessage:
    """Verify container-exists error suggests stop."""

    def test_container_exists_suggests_stop(self, start_mocks, capsys):
        """Container-exists error mentions both start and stop."""
        with start_mocks() as m:
            m.runtime.container_exists.return_value = True
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
        assert rc == 1
        err = capsys.readouterr().err
        assert "kanibako start" in err
        assert "kanibako stop" in err


class TestContainerStartFailureMessage:
    """Verify persistent container failure suggests diagnose."""

    def test_persistent_failure_suggests_diagnose(self, start_mocks, capsys):
        """When persistent container dies, suggests system diagnose."""
        with start_mocks() as m:
            # Container never becomes running after detached launch.
            m.runtime.is_running.return_value = False
            m.runtime.run.side_effect = lambda *a, **kw: 0
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
        assert rc == 1
        err = capsys.readouterr().err
        assert "kanibako system diagnose" in err


class TestFreshnessTerminology:
    """Verify freshness warning uses new rig terminology."""

    def test_freshness_uses_rig_rebuild(self, capsys):
        """Freshness warning says 'kanibako rig rebuild'."""
        from kanibako.freshness import _check

        mock_runtime = MagicMock()
        mock_runtime.get_local_digest.return_value = "sha256:old"
        cache_path = MagicMock()

        with patch("kanibako.freshness._cached_remote_digest", return_value="sha256:new"):
            _check(mock_runtime, "kanibako-oci:latest", cache_path)

        err = capsys.readouterr().err
        assert "kanibako rig rebuild" in err
        assert "kanibako image rebuild" not in err


# ---------------------------------------------------------------------------
# System info output (Part 2: newbie pass)
# ---------------------------------------------------------------------------


class TestSystemInfoOutput:
    """Verify system info output improvements."""

    def test_info_capitalizes_kanibako(self, capsys):
        """System info header shows 'Kanibako v...' with capital K and v prefix."""
        from kanibako.commands.system_cmd import run_info

        with (
            patch("kanibako.commands.system_cmd.config_file_path") as m_cf,
            patch("kanibako.commands.system_cmd.load_config"),
            patch("kanibako.commands.system_cmd.xdg", return_value=MagicMock()),
            patch("kanibako.container.ContainerRuntime") as m_rt,
            patch("subprocess.run") as m_run,
            patch("kanibako.targets.discover_targets", return_value={}),
        ):
            m_cf.return_value = MagicMock(exists=MagicMock(return_value=True))
            m_rt.return_value = MagicMock(cmd="podman")
            m_run.return_value = MagicMock(returncode=0, stdout="podman 5.0")
            rc = run_info(argparse.Namespace())

        assert rc == 0
        out = capsys.readouterr().out
        assert out.startswith("Kanibako v")

    def test_info_shows_agent_count(self, capsys):
        """System info shows number of detected agents."""
        from kanibako.commands.system_cmd import run_info

        mock_cls = MagicMock()
        with (
            patch("kanibako.commands.system_cmd.config_file_path") as m_cf,
            patch("kanibako.commands.system_cmd.load_config"),
            patch("kanibako.commands.system_cmd.xdg", return_value=MagicMock()),
            patch("kanibako.container.ContainerRuntime") as m_rt,
            patch("subprocess.run") as m_run,
            patch(
                "kanibako.targets.discover_targets",
                return_value={"claude": mock_cls},
            ),
        ):
            m_cf.return_value = MagicMock(exists=MagicMock(return_value=True))
            m_rt.return_value = MagicMock(cmd="podman")
            m_run.return_value = MagicMock(returncode=0, stdout="podman 5.0")
            run_info(argparse.Namespace())

        out = capsys.readouterr().out
        assert "1 detected" in out
        assert "kanibako crab list" in out

    def test_info_no_agents_shows_install_hint(self, capsys):
        """System info shows install hint when no agents are found."""
        from kanibako.commands.system_cmd import run_info

        with (
            patch("kanibako.commands.system_cmd.config_file_path") as m_cf,
            patch("kanibako.commands.system_cmd.load_config"),
            patch("kanibako.commands.system_cmd.xdg", return_value=MagicMock()),
            patch("kanibako.container.ContainerRuntime") as m_rt,
            patch("subprocess.run") as m_run,
            patch("kanibako.targets.discover_targets", return_value={}),
        ):
            m_cf.return_value = MagicMock(exists=MagicMock(return_value=True))
            m_rt.return_value = MagicMock(cmd="podman")
            m_run.return_value = MagicMock(returncode=0, stdout="podman 5.0")
            run_info(argparse.Namespace())

        out = capsys.readouterr().out
        assert "none" in out.lower()
        assert "kanibako-agent-claude" in out

    def test_info_config_not_initialized(self, capsys):
        """When config doesn't exist, shows setup hint."""
        from kanibako.commands.system_cmd import run_info

        with (
            patch("kanibako.commands.system_cmd.config_file_path") as m_cf,
            patch("kanibako.commands.system_cmd.xdg", return_value=MagicMock()),
            patch("kanibako.container.ContainerRuntime") as m_rt,
            patch("subprocess.run") as m_run,
            patch("kanibako.targets.discover_targets", return_value={}),
        ):
            m_cf.return_value = MagicMock(exists=MagicMock(return_value=False))
            m_rt.return_value = MagicMock(cmd="podman")
            m_run.return_value = MagicMock(returncode=0, stdout="podman 5.0")
            run_info(argparse.Namespace())

        out = capsys.readouterr().out
        assert "not initialized" in out
        assert "kanibako setup" in out or "kanibako start" in out

    def test_info_runtime_not_found(self, capsys):
        """When runtime is missing, shows install suggestion."""
        from kanibako.commands.system_cmd import run_info

        with (
            patch("kanibako.commands.system_cmd.config_file_path") as m_cf,
            patch("kanibako.commands.system_cmd.xdg", return_value=MagicMock()),
            patch(
                "kanibako.container.ContainerRuntime",
                side_effect=Exception("no runtime"),
            ),
            patch("kanibako.targets.discover_targets", return_value={}),
        ):
            m_cf.return_value = MagicMock(exists=MagicMock(return_value=False))
            run_info(argparse.Namespace())

        out = capsys.readouterr().out
        assert "not found" in out
        assert "podman" in out

    def test_info_shows_diagnose_tip(self, capsys):
        """System info always shows diagnose tip at the end."""
        from kanibako.commands.system_cmd import run_info

        with (
            patch("kanibako.commands.system_cmd.config_file_path") as m_cf,
            patch("kanibako.commands.system_cmd.load_config"),
            patch("kanibako.commands.system_cmd.xdg", return_value=MagicMock()),
            patch("kanibako.container.ContainerRuntime") as m_rt,
            patch("subprocess.run") as m_run,
            patch("kanibako.targets.discover_targets", return_value={}),
        ):
            m_cf.return_value = MagicMock(exists=MagicMock(return_value=True))
            m_rt.return_value = MagicMock(cmd="podman")
            m_run.return_value = MagicMock(returncode=0, stdout="podman 5.0")
            run_info(argparse.Namespace())

        out = capsys.readouterr().out
        assert "kanibako system diagnose" in out

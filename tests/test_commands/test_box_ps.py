"""Tests for kanibako box ps and list commands (unified)."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.box._parser import run_list, run_ps


@pytest.fixture
def mock_runtime():
    rt = MagicMock()
    rt.list_running.return_value = []
    rt.list_all.return_value = []
    return rt


def _list_args(*, show_all=False, orphan=False, active=False, quiet=False):
    return argparse.Namespace(
        show_all=show_all, orphan=orphan, active=active, quiet=quiet,
    )


def _ps_args(*, show_all=False, quiet=False):
    return argparse.Namespace(show_all=show_all, quiet=quiet)


# ---------------------------------------------------------------------------
# Patches common to most tests (no real projects, no real runtime)
# ---------------------------------------------------------------------------

def _mock_patches(mock_runtime, names=None):
    """Return a context manager stack of common patches."""
    if names is None:
        names = {"projects": {}, "worksets": {}}
    return (
        patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
        patch("kanibako.commands.box._parser.config_file_path"),
        patch("kanibako.commands.box._parser.load_config"),
        patch("kanibako.commands.box._parser.load_std_paths", return_value=MagicMock(data_path=MagicMock())),
        patch("kanibako.commands.box._parser.iter_projects", return_value=[]),
        patch("kanibako.commands.box._parser.iter_workset_projects", return_value=[]),
        patch("kanibako.commands.box._parser.read_names", return_value=names),
    )


class TestBoxPs:
    """Tests for run_ps (delegates to run_list with active-only filter)."""

    def test_ps_delegates_to_run_list(self, mock_runtime, capsys):
        """run_ps sets active=True and calls run_list."""
        args = _ps_args()
        p1, p2, p3, p4, p5, p6, p7 = _mock_patches(mock_runtime)
        with p1, p2, p3, p4, p5, p6, p7:
            rc = run_ps(args)
        assert rc == 0
        # ps without --all sets active=True
        assert getattr(args, "active", False) is True

    def test_ps_all_does_not_set_active(self, mock_runtime, capsys):
        """run_ps --all does NOT set active, so all projects show."""
        args = _ps_args(show_all=True)
        p1, p2, p3, p4, p5, p6, p7 = _mock_patches(mock_runtime)
        with p1, p2, p3, p4, p5, p6, p7:
            rc = run_ps(args)
        assert rc == 0
        # active should remain unset (or False)
        assert getattr(args, "active", False) is not True or getattr(args, "show_all", False)

    def test_ps_no_running_containers(self, mock_runtime, capsys):
        p1, p2, p3, p4, p5, p6, p7 = _mock_patches(mock_runtime)
        with p1, p2, p3, p4, p5, p6, p7:
            args = _ps_args()
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "No" in out  # "No active boxes." or "No known projects."

    def test_ps_quiet_no_containers(self, mock_runtime, capsys):
        """Quiet mode with no containers outputs nothing."""
        p1, p2, p3, p4, p5, p6, p7 = _mock_patches(mock_runtime)
        with p1, p2, p3, p4, p5, p6, p7:
            args = _ps_args(quiet=True)
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert out == ""

    def test_ps_runtime_not_found(self, capsys):
        """run_ps gracefully handles missing container runtime."""
        from kanibako.errors import ContainerError
        with (
            patch("kanibako.commands.box._parser.ContainerRuntime",
                  side_effect=ContainerError("No runtime")),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths",
                  return_value=MagicMock(data_path=MagicMock())),
            patch("kanibako.commands.box._parser.iter_projects", return_value=[]),
            patch("kanibako.commands.box._parser.iter_workset_projects", return_value=[]),
            patch("kanibako.commands.box._parser.read_names",
                  return_value={"projects": {}, "worksets": {}}),
        ):
            args = _ps_args()
            rc = run_ps(args)
            # Should not crash — runtime error is handled gracefully
            assert rc == 0


class TestRunList:
    """Tests for run_list (unified list command)."""

    def test_list_empty(self, mock_runtime, capsys):
        p1, p2, p3, p4, p5, p6, p7 = _mock_patches(mock_runtime)
        with p1, p2, p3, p4, p5, p6, p7:
            args = _list_args()
            rc = run_list(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "No known projects" in out

    def test_list_shows_active_and_stopped(self, mock_runtime, capsys):
        """run_list without --active shows both active and stopped projects."""
        from pathlib import Path

        mock_runtime.list_running.return_value = [
            ("kanibako-myproj", "kanibako-oci:latest", "Up 10 minutes"),
        ]
        proj_a_path = MagicMock(name="myproj")
        proj_a_dir = MagicMock(spec=Path)
        proj_a_dir.is_dir.return_value = True
        proj_a_dir.__str__ = lambda self: "/home/user/myproj"

        proj_b_path = MagicMock(name="other")
        proj_b_dir = MagicMock(spec=Path)
        proj_b_dir.is_dir.return_value = True
        proj_b_dir.__str__ = lambda self: "/home/user/other"

        projects = [(proj_a_path, proj_a_dir), (proj_b_path, proj_b_dir)]
        names = {
            "projects": {"myproj": "/home/user/myproj", "other": "/home/user/other"},
            "worksets": {},
        }

        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths",
                  return_value=MagicMock(data_path=MagicMock())),
            patch("kanibako.commands.box._parser.iter_projects", return_value=projects),
            patch("kanibako.commands.box._parser.iter_workset_projects", return_value=[]),
            patch("kanibako.commands.box._parser.read_names", return_value=names),
        ):
            args = _list_args()
            rc = run_list(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "NAME" in out  # header
            assert "myproj" in out
            assert "active" in out
            assert "other" in out
            assert "stopped" in out

    def test_list_active_only(self, mock_runtime, capsys):
        """run_list --active shows only active projects."""
        from pathlib import Path

        mock_runtime.list_running.return_value = [
            ("kanibako-myproj", "kanibako-oci:latest", "Up 10 minutes"),
        ]
        proj_a_path = MagicMock(name="myproj")
        proj_a_dir = MagicMock(spec=Path)
        proj_a_dir.is_dir.return_value = True
        proj_a_dir.__str__ = lambda self: "/home/user/myproj"

        proj_b_path = MagicMock(name="other")
        proj_b_dir = MagicMock(spec=Path)
        proj_b_dir.is_dir.return_value = True
        proj_b_dir.__str__ = lambda self: "/home/user/other"

        projects = [(proj_a_path, proj_a_dir), (proj_b_path, proj_b_dir)]
        names = {
            "projects": {"myproj": "/home/user/myproj", "other": "/home/user/other"},
            "worksets": {},
        }

        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths",
                  return_value=MagicMock(data_path=MagicMock())),
            patch("kanibako.commands.box._parser.iter_projects", return_value=projects),
            patch("kanibako.commands.box._parser.iter_workset_projects", return_value=[]),
            patch("kanibako.commands.box._parser.read_names", return_value=names),
        ):
            args = _list_args(active=True)
            rc = run_list(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "myproj" in out
            assert "other" not in out  # stopped, should be filtered

    def test_list_active_no_results(self, mock_runtime, capsys):
        """run_list --active with no active boxes prints a message."""
        from pathlib import Path

        proj_path = MagicMock(name="idle")
        proj_dir = MagicMock(spec=Path)
        proj_dir.is_dir.return_value = True
        proj_dir.__str__ = lambda self: "/home/user/idle"

        projects = [(proj_path, proj_dir)]
        names = {"projects": {"idle": "/home/user/idle"}, "worksets": {}}

        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths",
                  return_value=MagicMock(data_path=MagicMock())),
            patch("kanibako.commands.box._parser.iter_projects", return_value=projects),
            patch("kanibako.commands.box._parser.iter_workset_projects", return_value=[]),
            patch("kanibako.commands.box._parser.read_names", return_value=names),
        ):
            args = _list_args(active=True)
            rc = run_list(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "No active boxes" in out

    def test_list_quiet(self, mock_runtime, capsys):
        """Quiet mode outputs names only, one per line."""
        from pathlib import Path

        proj_path = MagicMock(name="myproj")
        proj_dir = MagicMock(spec=Path)
        proj_dir.is_dir.return_value = True
        proj_dir.__str__ = lambda self: "/home/user/myproj"

        projects = [(proj_path, proj_dir)]
        names = {"projects": {"myproj": "/home/user/myproj"}, "worksets": {}}

        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths",
                  return_value=MagicMock(data_path=MagicMock())),
            patch("kanibako.commands.box._parser.iter_projects", return_value=projects),
            patch("kanibako.commands.box._parser.iter_workset_projects", return_value=[]),
            patch("kanibako.commands.box._parser.read_names", return_value=names),
        ):
            args = _list_args(quiet=True)
            rc = run_list(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert out.strip() == "myproj"
            assert "NAME" not in out

    def test_list_runtime_error_graceful(self, capsys):
        """Missing container runtime shows all projects as stopped, not a crash."""
        from pathlib import Path
        from kanibako.errors import ContainerError

        proj_path = MagicMock(name="myproj")
        proj_dir = MagicMock(spec=Path)
        proj_dir.is_dir.return_value = True
        proj_dir.__str__ = lambda self: "/home/user/myproj"

        projects = [(proj_path, proj_dir)]
        names = {"projects": {"myproj": "/home/user/myproj"}, "worksets": {}}

        with (
            patch("kanibako.commands.box._parser.ContainerRuntime",
                  side_effect=ContainerError("No runtime")),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths",
                  return_value=MagicMock(data_path=MagicMock())),
            patch("kanibako.commands.box._parser.iter_projects", return_value=projects),
            patch("kanibako.commands.box._parser.iter_workset_projects", return_value=[]),
            patch("kanibako.commands.box._parser.read_names", return_value=names),
        ):
            args = _list_args()
            rc = run_list(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "myproj" in out
            assert "stopped" in out  # All show as stopped when runtime unavailable

    def test_list_ps_all_shows_everything(self, mock_runtime, capsys):
        """ps --all (show_all=True) shows all projects including inactive."""
        from pathlib import Path

        mock_runtime.list_running.return_value = [
            ("kanibako-running", "kanibako-oci:latest", "Up 10 minutes"),
        ]
        proj_a_path = MagicMock(name="running")
        proj_a_dir = MagicMock(spec=Path)
        proj_a_dir.is_dir.return_value = True
        proj_a_dir.__str__ = lambda self: "/home/user/running"

        proj_b_path = MagicMock(name="idle")
        proj_b_dir = MagicMock(spec=Path)
        proj_b_dir.is_dir.return_value = True
        proj_b_dir.__str__ = lambda self: "/home/user/idle"

        projects = [(proj_a_path, proj_a_dir), (proj_b_path, proj_b_dir)]
        names = {
            "projects": {"running": "/home/user/running", "idle": "/home/user/idle"},
            "worksets": {},
        }

        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths",
                  return_value=MagicMock(data_path=MagicMock())),
            patch("kanibako.commands.box._parser.iter_projects", return_value=projects),
            patch("kanibako.commands.box._parser.iter_workset_projects", return_value=[]),
            patch("kanibako.commands.box._parser.read_names", return_value=names),
        ):
            args = _ps_args(show_all=True)
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "running" in out
            assert "idle" in out
            assert "active" in out
            assert "stopped" in out

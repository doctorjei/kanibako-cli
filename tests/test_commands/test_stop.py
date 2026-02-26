"""Tests for kanibako.commands.stop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.stop import run, _stop_one, _stop_all


@pytest.fixture
def mock_runtime():
    rt = MagicMock()
    rt.stop.return_value = True
    rt.list_running.return_value = []
    rt.container_exists.return_value = False
    rt.rm.return_value = True
    return rt


class TestStopOne:
    def test_running_container_stopped(self, mock_runtime, capsys):
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_any_project") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            rc = _stop_one(mock_runtime, project_dir=None)
            assert rc == 0
            mock_runtime.stop.assert_called_once()
            out = capsys.readouterr().out
            assert "Stopped" in out

    def test_no_running_container(self, mock_runtime, capsys):
        mock_runtime.stop.return_value = False
        mock_runtime.container_exists.return_value = False
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_any_project") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            proj.metadata_path = MagicMock()
            lock_path = MagicMock()
            lock_path.__str__ = lambda self: "/fake/path/.kanibako.lock"
            proj.metadata_path.__truediv__ = MagicMock(return_value=lock_path)
            m_resolve.return_value = proj

            rc = _stop_one(mock_runtime, project_dir=None)
            assert rc == 0
            out = capsys.readouterr().out
            assert "No running container" in out
            assert "rm " in out
            assert ".kanibako.lock" in out

    def test_stop_removes_persistent_container(self, mock_runtime, capsys):
        """After stopping a running container, rm is called to clean up."""
        mock_runtime.container_exists.return_value = True  # exists after stop
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_any_project") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            rc = _stop_one(mock_runtime, project_dir=None)
            assert rc == 0
            mock_runtime.rm.assert_called_once()

    def test_stop_cleans_stale_persistent_container(self, mock_runtime, capsys):
        """A stopped persistent container (not running) is removed."""
        mock_runtime.stop.return_value = False  # not running
        mock_runtime.container_exists.return_value = True  # but exists (stopped)
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_any_project") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            rc = _stop_one(mock_runtime, project_dir=None)
            assert rc == 0
            mock_runtime.rm.assert_called_once()
            out = capsys.readouterr().out
            assert "Removed stopped container" in out

    def test_stop_with_project_dir(self, mock_runtime):
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_any_project") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            _stop_one(mock_runtime, project_dir="/some/path")
            # resolve_any_project called with the given path (positional)
            call_args = m_resolve.call_args
            assert call_args[0][2] == "/some/path"
            assert call_args[1]["initialize"] is False


class TestStopAll:
    def test_stops_multiple_containers(self, mock_runtime, capsys):
        mock_runtime.list_running.return_value = [
            ("kanibako-aabbccdd", "img:latest", "Up 5 minutes"),
            ("kanibako-11223344", "img:latest", "Up 10 minutes"),
        ]
        rc = _stop_all(mock_runtime)
        assert rc == 0
        assert mock_runtime.stop.call_count == 2
        out = capsys.readouterr().out
        assert "Stopped 2 container(s)" in out

    def test_nothing_running(self, mock_runtime, capsys):
        mock_runtime.list_running.return_value = []
        rc = _stop_all(mock_runtime)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No running kanibako containers" in out
        mock_runtime.stop.assert_not_called()

    def test_partial_failure(self, mock_runtime, capsys):
        mock_runtime.list_running.return_value = [
            ("kanibako-aabbccdd", "img:latest", "Up 5 minutes"),
            ("kanibako-11223344", "img:latest", "Up 10 minutes"),
        ]
        mock_runtime.stop.side_effect = [True, False]
        rc = _stop_all(mock_runtime)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Stopped 1 container(s)" in out
        capsys.readouterr()  # drain stderr


class TestRunDispatch:
    def test_dispatches_to_stop_all(self, capsys):
        with patch("kanibako.commands.stop.ContainerRuntime") as m_cls:
            rt = MagicMock()
            rt.list_running.return_value = []
            m_cls.return_value = rt
            import argparse
            args = argparse.Namespace(all_containers=True, path=None)
            rc = run(args)
            assert rc == 0
            rt.list_running.assert_called_once()

    def test_dispatches_to_stop_one(self):
        with (
            patch("kanibako.commands.stop.ContainerRuntime") as m_cls,
            patch("kanibako.commands.stop._stop_one", return_value=0) as m_stop_one,
        ):
            rt = MagicMock()
            m_cls.return_value = rt
            import argparse
            args = argparse.Namespace(all_containers=False, path=None)
            rc = run(args)
            assert rc == 0
            m_stop_one.assert_called_once_with(rt, project_dir=None)

    def test_runtime_not_found(self, capsys):
        from kanibako.errors import ContainerError
        with patch("kanibako.commands.stop.ContainerRuntime", side_effect=ContainerError("No runtime")):
            import argparse
            args = argparse.Namespace(all_containers=False, path=None)
            rc = run(args)
            assert rc == 1
            err = capsys.readouterr().err
            assert "No runtime" in err

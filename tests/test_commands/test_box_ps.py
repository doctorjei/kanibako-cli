"""Tests for kanibako box ps command."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.box._parser import run_ps


@pytest.fixture
def mock_runtime():
    rt = MagicMock()
    rt.list_running.return_value = []
    rt.list_all.return_value = []
    return rt


class TestBoxPs:
    def test_no_running_containers(self, mock_runtime, capsys):
        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths") as m_std,
        ):
            m_std.return_value = MagicMock(data_path=MagicMock())
            args = argparse.Namespace(show_all=False, quiet=False)
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "No running kanibako containers" in out

    def test_no_containers_all(self, mock_runtime, capsys):
        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths") as m_std,
        ):
            m_std.return_value = MagicMock(data_path=MagicMock())
            args = argparse.Namespace(show_all=True, quiet=False)
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "No kanibako containers" in out

    def test_running_containers_shown(self, mock_runtime, capsys):
        mock_runtime.list_running.return_value = [
            ("kanibako-myproj", "kanibako-oci:latest", "Up 10 minutes"),
            ("kanibako-other", "kanibako-oci:latest", "Up 5 minutes"),
        ]
        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths") as m_std,
            patch("kanibako.commands.box._parser.read_names") as m_names,
        ):
            m_std.return_value = MagicMock(data_path=MagicMock())
            m_names.return_value = {
                "projects": {"myproj": "/home/user/myproj"},
                "worksets": {},
            }
            args = argparse.Namespace(show_all=False, quiet=False)
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "PROJECT" in out  # header
            assert "myproj" in out
            assert "kanibako-other" in out  # no name mapping, shows container name
            assert "Up 10 minutes" in out

    def test_quiet_mode(self, mock_runtime, capsys):
        mock_runtime.list_running.return_value = [
            ("kanibako-myproj", "kanibako-oci:latest", "Up 10 minutes"),
        ]
        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths") as m_std,
            patch("kanibako.commands.box._parser.read_names") as m_names,
        ):
            m_std.return_value = MagicMock(data_path=MagicMock())
            m_names.return_value = {
                "projects": {"myproj": "/home/user/myproj"},
                "worksets": {},
            }
            args = argparse.Namespace(show_all=False, quiet=True)
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert out.strip() == "myproj"
            assert "PROJECT" not in out  # no header in quiet mode

    def test_all_includes_stopped(self, mock_runtime, capsys):
        mock_runtime.list_all.return_value = [
            ("kanibako-active", "kanibako-oci:latest", "Up 10 minutes"),
            ("kanibako-stopped", "kanibako-oci:latest", "Exited (0) 2 hours ago"),
        ]
        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths") as m_std,
            patch("kanibako.commands.box._parser.read_names") as m_names,
        ):
            m_std.return_value = MagicMock(data_path=MagicMock())
            m_names.return_value = {"projects": {}, "worksets": {}}
            args = argparse.Namespace(show_all=True, quiet=False)
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "kanibako-active" in out
            assert "kanibako-stopped" in out
            assert "Exited" in out
            # Verify list_all was called (not list_running)
            mock_runtime.list_all.assert_called_once()
            mock_runtime.list_running.assert_not_called()

    def test_runtime_not_found(self, capsys):
        from kanibako.errors import ContainerError
        with patch(
            "kanibako.commands.box._parser.ContainerRuntime",
            side_effect=ContainerError("No runtime"),
        ):
            args = argparse.Namespace(show_all=False, quiet=False)
            rc = run_ps(args)
            assert rc == 1
            err = capsys.readouterr().err
            assert "No runtime" in err

    def test_quiet_no_containers(self, mock_runtime, capsys):
        """Quiet mode with no containers outputs nothing."""
        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths") as m_std,
        ):
            m_std.return_value = MagicMock(data_path=MagicMock())
            args = argparse.Namespace(show_all=False, quiet=True)
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert out == ""

    def test_name_cross_reference(self, mock_runtime, capsys):
        """Container names matching 'kanibako-{name}' are resolved to project names."""
        mock_runtime.list_running.return_value = [
            ("kanibako-webapp", "kanibako-oci:latest", "Up 1 hour"),
        ]
        with (
            patch("kanibako.commands.box._parser.ContainerRuntime", return_value=mock_runtime),
            patch("kanibako.commands.box._parser.config_file_path"),
            patch("kanibako.commands.box._parser.load_config"),
            patch("kanibako.commands.box._parser.load_std_paths") as m_std,
            patch("kanibako.commands.box._parser.read_names") as m_names,
        ):
            m_std.return_value = MagicMock(data_path=MagicMock())
            m_names.return_value = {
                "projects": {"webapp": "/home/user/webapp"},
                "worksets": {},
            }
            args = argparse.Namespace(show_all=False, quiet=True)
            rc = run_ps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "webapp" in out
            assert "kanibako-webapp" not in out  # resolved to project name

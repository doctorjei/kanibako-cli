"""Tests for kanibako.commands.connect."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.connect import (
    _list_projects,
    _resolve_project_arg,
    run_connect,
)


# ---------------------------------------------------------------------------
# _resolve_project_arg
# ---------------------------------------------------------------------------


class TestResolveProjectArg:
    """Resolution logic: qualified name, bare name, path, and fallback."""

    def _patch_infra(self, **kwargs):
        """Return a context manager patching config/paths/names for resolution."""
        from contextlib import contextmanager
        from types import SimpleNamespace

        @contextmanager
        def _ctx():
            data_path = kwargs.get("data_path", Path("/fake/data"))
            std = SimpleNamespace(data_path=data_path)
            config = MagicMock()

            with (
                patch("kanibako.commands.connect.config_file_path"),
                patch("kanibako.commands.connect.load_config", return_value=config),
                patch("kanibako.commands.connect.load_std_paths", return_value=std),
                patch("kanibako.commands.connect.resolve_qualified_name") as m_qual,
                patch("kanibako.commands.connect.resolve_name") as m_name,
            ):
                yield SimpleNamespace(
                    resolve_qualified_name=m_qual,
                    resolve_name=m_name,
                    data_path=data_path,
                )

        return _ctx()

    def test_qualified_name_resolved(self):
        """workset/project syntax resolves via resolve_qualified_name."""
        with self._patch_infra() as m:
            m.resolve_qualified_name.return_value = ("/home/user/ws/workspaces/app", "myws")
            result = _resolve_project_arg("myws/app")
        assert result == "/home/user/ws/workspaces/app"
        m.resolve_qualified_name.assert_called_once()

    def test_bare_name_resolved(self):
        """A simple name resolves via resolve_name."""
        with self._patch_infra() as m:
            from kanibako.errors import ProjectError
            m.resolve_qualified_name.side_effect = ProjectError("not qualified")
            m.resolve_name.return_value = ("/home/user/myapp", "project")
            result = _resolve_project_arg("myapp")
        assert result == "/home/user/myapp"

    def test_filesystem_path_fallback(self, tmp_path):
        """Existing directory path is accepted as fallback."""
        with self._patch_infra() as m:
            from kanibako.errors import ProjectError
            m.resolve_name.side_effect = ProjectError("unknown")
            result = _resolve_project_arg(str(tmp_path))
        assert result == str(tmp_path)

    def test_unknown_name_returns_none(self, capsys):
        """Non-existent name and non-existent path returns None."""
        with self._patch_infra() as m:
            from kanibako.errors import ProjectError
            m.resolve_name.side_effect = ProjectError("unknown")
            result = _resolve_project_arg("/nonexistent/bogus/path")
        assert result is None
        assert "not a known project name" in capsys.readouterr().err

    def test_qualified_fallback_to_bare_name(self):
        """If qualified name fails, fall through to bare name."""
        with self._patch_infra() as m:
            from kanibako.errors import ProjectError
            m.resolve_qualified_name.side_effect = ProjectError("not found")
            m.resolve_name.return_value = ("/home/user/foo-bar", "project")
            # "foo/bar" contains "/" but isn't a path — tries qualified first, then bare
            result = _resolve_project_arg("foo/bar")
        assert result == "/home/user/foo-bar"

    def test_absolute_path_skips_qualified(self):
        """Absolute paths skip qualified name parsing."""
        with self._patch_infra() as m:
            from kanibako.errors import ProjectError
            m.resolve_name.side_effect = ProjectError("unknown")
            # /some/path starts with / — should NOT try qualified name
            result = _resolve_project_arg("/tmp")
        assert result == "/tmp"
        m.resolve_qualified_name.assert_not_called()

    def test_dotslash_path_skips_qualified(self, tmp_path):
        """Relative ./paths skip qualified name parsing."""
        with self._patch_infra() as m:
            from kanibako.errors import ProjectError
            m.resolve_name.side_effect = ProjectError("unknown")
            result = _resolve_project_arg(f"./{tmp_path.name}")
        # May or may not resolve — the point is qualified was skipped
        m.resolve_qualified_name.assert_not_called()


# ---------------------------------------------------------------------------
# run_connect
# ---------------------------------------------------------------------------


class TestRunConnect:
    """Top-level dispatch: --list, missing arg, normal connect."""

    def test_missing_project_arg_errors(self, capsys):
        """No project argument prints error and returns 1."""
        args = MagicMock()
        args.list_projects = False
        args.project = None
        rc = run_connect(args)
        assert rc == 1
        assert "project name or path is required" in capsys.readouterr().err

    def test_list_flag_dispatches(self):
        """--list calls _list_projects."""
        args = MagicMock()
        args.list_projects = True
        with patch("kanibako.commands.connect._list_projects", return_value=0) as m:
            rc = run_connect(args)
        assert rc == 0
        m.assert_called_once()

    def test_connect_calls_run_container(self):
        """Successful resolution calls _run_container with persistent=True."""
        args = MagicMock()
        args.list_projects = False
        args.project = "myapp"
        args.image = None
        args.new = False
        args.safe = False

        with (
            patch(
                "kanibako.commands.connect._resolve_project_arg",
                return_value="/home/user/myapp",
            ),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
        ):
            rc = run_connect(args)

        assert rc == 0
        m_run.assert_called_once()
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["persistent"] is True
        assert call_kwargs["project_dir"] == "/home/user/myapp"
        assert call_kwargs["extra_args"] == []

    def test_connect_passes_flags(self):
        """Flags are forwarded to _run_container."""
        args = MagicMock()
        args.list_projects = False
        args.project = "myapp"
        args.image = "custom:latest"
        args.new = True
        args.safe = True

        with (
            patch(
                "kanibako.commands.connect._resolve_project_arg",
                return_value="/resolved",
            ),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
        ):
            rc = run_connect(args)

        assert rc == 0
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["image_override"] == "custom:latest"
        assert call_kwargs["new_session"] is True
        assert call_kwargs["safe_mode"] is True

    def test_connect_failed_resolution_returns_1(self):
        """Failed resolution returns 1 without calling _run_container."""
        args = MagicMock()
        args.list_projects = False
        args.project = "bogus"

        with (
            patch("kanibako.commands.connect._resolve_project_arg", return_value=None),
            patch("kanibako.commands.start._run_container") as m_run,
        ):
            rc = run_connect(args)

        assert rc == 1
        m_run.assert_not_called()


# ---------------------------------------------------------------------------
# _list_projects
# ---------------------------------------------------------------------------


class TestListProjects:
    """Project listing with running status."""

    def _patch_list(self, projects=None, worksets=None, running=None):
        """Return a context manager patching list dependencies."""
        from contextlib import contextmanager
        from types import SimpleNamespace

        if projects is None:
            projects = {}
        if worksets is None:
            worksets = {}
        if running is None:
            running = []

        @contextmanager
        def _ctx():
            std = SimpleNamespace(data_path=Path("/fake/data"))
            with (
                patch("kanibako.commands.connect.config_file_path"),
                patch("kanibako.commands.connect.load_config"),
                patch("kanibako.commands.connect.load_std_paths", return_value=std),
                patch(
                    "kanibako.commands.connect.read_names",
                    return_value={"projects": projects, "worksets": worksets},
                ),
                patch("kanibako.commands.connect.ContainerRuntime") as m_rt,
            ):
                rt = MagicMock()
                rt.list_running.return_value = running
                m_rt.return_value = rt
                yield

        return _ctx()

    def test_no_projects(self, capsys):
        """Empty registry prints message."""
        with self._patch_list():
            rc = _list_projects()
        assert rc == 0
        assert "No projects registered" in capsys.readouterr().out

    def test_lists_projects(self, capsys):
        """Projects appear in output with names and paths."""
        with self._patch_list(projects={"myapp": "/home/user/myapp"}):
            rc = _list_projects()
        assert rc == 0
        out = capsys.readouterr().out
        assert "myapp" in out
        assert "/home/user/myapp" in out

    def test_running_status_shown(self, capsys):
        """Running containers show 'running' status."""
        with self._patch_list(
            projects={"myapp": "/home/user/myapp"},
            running=[("kanibako-myapp", "kanibako-base:latest", "running")],
        ):
            rc = _list_projects()
        assert rc == 0
        out = capsys.readouterr().out
        assert "running" in out

    def test_not_running_no_status(self, capsys):
        """Non-running projects have empty status column."""
        with self._patch_list(projects={"myapp": "/home/user/myapp"}):
            rc = _list_projects()
        assert rc == 0
        out = capsys.readouterr().out
        lines = [l for l in out.strip().split("\n") if "myapp" in l and "NAME" not in l]
        assert len(lines) == 1
        # Status column should be empty (no "running" text)
        assert "running" not in lines[0]

    def test_worksets_shown(self, capsys):
        """Worksets appear with (workset) suffix."""
        with self._patch_list(worksets={"clientwork": "/home/user/clients"}):
            rc = _list_projects()
        assert rc == 0
        out = capsys.readouterr().out
        assert "clientwork (workset)" in out

    def test_header_present(self, capsys):
        """Output includes column headers."""
        with self._patch_list(projects={"app": "/home/user/app"}):
            _list_projects()
        out = capsys.readouterr().out
        assert "NAME" in out
        assert "PATH" in out
        assert "STATUS" in out


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


class TestCLIRegistration:
    """Verify connect is wired into the CLI parser."""

    def test_connect_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "connect" in _SUBCOMMANDS

    def test_connect_parser_registered(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        # Verify parsing works without error
        args = parser.parse_args(["connect", "--list"])
        assert args.command == "connect"
        assert args.list_projects is True

    def test_connect_parser_with_project_and_flags(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["connect", "-N", "myproject"])
        assert args.command == "connect"
        assert args.project == "myproject"
        assert args.new is True

    def test_connect_parser_with_image(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["connect", "-i", "custom:latest", "myproject"])
        assert args.command == "connect"
        assert args.project == "myproject"
        assert args.image == "custom:latest"

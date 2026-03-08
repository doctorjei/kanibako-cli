"""Tests for kanibako box info command (replaces top-level status)."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kanibako.cli import _SUBCOMMANDS, build_parser
from kanibako.commands.box._parser import (
    _check_container_running,
    _format_credential_age,
    run_info,
)
from kanibako.config import load_config
from kanibako.errors import ContainerError
from kanibako.paths import load_std_paths, resolve_project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def initialized_project(config_file, credentials_dir, tmp_home):
    """Create a fully initialized local project."""
    config = load_config(config_file)
    std = load_std_paths(config)
    project_dir = str(tmp_home / "project")
    proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
    return SimpleNamespace(
        config=config, std=std, proj=proj, project_dir=project_dir,
        config_file=config_file, tmp_home=tmp_home,
    )


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestBoxInfoParser:
    def test_status_not_in_subcommands(self):
        """Top-level 'status' command has been removed."""
        assert "status" not in _SUBCOMMANDS

    def test_box_info_parser_default(self):
        parser = build_parser()
        args = parser.parse_args(["box", "info"])
        assert args.command == "box"
        assert args.box_command == "info"
        assert args.path is None

    def test_box_info_parser_with_path(self):
        parser = build_parser()
        args = parser.parse_args(["box", "info", "/tmp/mydir"])
        assert args.command == "box"
        assert args.path == "/tmp/mydir"

    def test_box_inspect_alias(self):
        parser = build_parser()
        args = parser.parse_args(["box", "inspect"])
        assert args.command == "box"
        assert hasattr(args, "func")
        assert args.func is run_info

    def test_box_info_has_func(self):
        parser = build_parser()
        args = parser.parse_args(["box", "info"])
        assert hasattr(args, "func")
        assert args.func is run_info


# ---------------------------------------------------------------------------
# _format_credential_age tests
# ---------------------------------------------------------------------------

class TestFormatCredentialAge:
    def test_nonexistent_file(self, tmp_path):
        result = _format_credential_age(tmp_path / "nonexistent.json")
        assert "n/a" in result
        assert "no credentials file" in result

    def test_recent_file(self, tmp_path):
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        result = _format_credential_age(creds)
        # Should show seconds or minutes since just created.
        assert "ago" in result
        assert "UTC" in result

    def test_old_file(self, tmp_path):
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        # Set mtime to 2 days ago.
        old_time = time.time() - 2 * 86400
        import os
        os.utime(creds, (old_time, old_time))
        result = _format_credential_age(creds)
        assert "2d ago" in result

    def test_hours_ago(self, tmp_path):
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        old_time = time.time() - 3 * 3600
        import os
        os.utime(creds, (old_time, old_time))
        result = _format_credential_age(creds)
        assert "3h ago" in result

    def test_minutes_ago(self, tmp_path):
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        old_time = time.time() - 5 * 60
        import os
        os.utime(creds, (old_time, old_time))
        result = _format_credential_age(creds)
        assert "5m ago" in result


# ---------------------------------------------------------------------------
# _check_container_running tests
# ---------------------------------------------------------------------------

def _mock_proj(*, name="", project_hash="a" * 64, mode="local",
               project_path="/home/user/proj"):
    """Duck-typed ProjectPaths for _check_container_running tests."""
    return SimpleNamespace(
        name=name,
        project_hash=project_hash,
        mode=SimpleNamespace(value=mode),
        project_path=Path(project_path),
    )


class TestCheckContainerRunning:
    def test_no_runtime(self):
        with patch(
            "kanibako.commands.box._parser.ContainerRuntime",
            side_effect=ContainerError("no runtime"),
        ):
            proj = _mock_proj()
            running, detail = _check_container_running(proj)
            assert running is False
            assert "no container runtime" in detail

    def test_no_containers(self):
        mock_rt = MagicMock()
        mock_rt.list_running.return_value = []
        mock_rt.container_exists.return_value = False
        with patch(
            "kanibako.commands.box._parser.ContainerRuntime",
            return_value=mock_rt,
        ):
            proj = _mock_proj()
            running, detail = _check_container_running(proj)
            assert running is False
            assert "not running" in detail

    def test_container_found_by_name(self):
        container_name = "kanibako-myapp"
        mock_rt = MagicMock()
        mock_rt.list_running.return_value = [
            (container_name, "test:latest", "Up 5 minutes"),
        ]
        with patch(
            "kanibako.commands.box._parser.ContainerRuntime",
            return_value=mock_rt,
        ):
            proj = _mock_proj(name="myapp")
            running, detail = _check_container_running(proj)
            assert running is True
            assert "running" in detail
            assert container_name in detail

    def test_container_found_by_hash_fallback(self):
        """Unnamed project falls back to hash-based container name."""
        mock_rt = MagicMock()
        mock_rt.list_running.return_value = [
            ("kanibako-aaaaaaaa", "test:latest", "Up 5 minutes"),
        ]
        with patch(
            "kanibako.commands.box._parser.ContainerRuntime",
            return_value=mock_rt,
        ):
            proj = _mock_proj(name="")
            running, detail = _check_container_running(proj)
            assert running is True
            assert "running" in detail

    def test_different_container(self):
        mock_rt = MagicMock()
        mock_rt.list_running.return_value = [
            ("kanibako-bbbbbbbb", "test:latest", "Up 5 minutes"),
        ]
        mock_rt.container_exists.return_value = False
        with patch(
            "kanibako.commands.box._parser.ContainerRuntime",
            return_value=mock_rt,
        ):
            proj = _mock_proj(name="myapp")
            running, detail = _check_container_running(proj)
            assert running is False
            assert "not running" in detail

    def test_stopped_persistent_container(self):
        """Stopped persistent container is detected and reported."""
        mock_rt = MagicMock()
        mock_rt.list_running.return_value = []
        mock_rt.container_exists.return_value = True  # stopped but exists
        with patch(
            "kanibako.commands.box._parser.ContainerRuntime",
            return_value=mock_rt,
        ):
            proj = _mock_proj(name="myapp")
            running, detail = _check_container_running(proj)
            assert running is False
            assert "stopped persistent" in detail


# ---------------------------------------------------------------------------
# run_info integration tests (with real filesystem, mocked container)
# ---------------------------------------------------------------------------

class TestRunInfo:
    def test_no_project_data(self, config_file, tmp_home, capsys):
        """Info for a directory with no kanibako data."""
        args = argparse.Namespace(path=None)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-abcdef12)"),
        ):
            rc = run_info(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "No project data found" in out

    def test_initialized_project(self, initialized_project, capsys):
        """Info for an initialized local project."""
        args = argparse.Namespace(path=initialized_project.project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-abcdef12)"),
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Name:" in out
        assert "local" in out
        assert "Hash:" in out
        assert "Metadata:" in out
        assert "Shell:" in out
        assert "Lock:" in out
        assert "none" in out
        assert "Container:" in out
        assert "Image:" in out
        assert "Credentials:" in out

    def test_lock_active(self, initialized_project, capsys):
        """Info shows ACTIVE lock when lock file exists."""
        lock_file = initialized_project.proj.metadata_path / ".kanibako.lock"
        lock_file.write_text("kanibako-test\n")
        args = argparse.Namespace(path=initialized_project.project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-test)"),
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "ACTIVE" in out

    def test_with_path_arg(self, initialized_project, capsys):
        """Info with path argument pointing to a project directory."""
        args = argparse.Namespace(path=initialized_project.project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-abcdef12)"),
        ):
            rc = run_info(args)
        assert rc == 0

    def test_nonexistent_directory(self, config_file, tmp_home, capsys):
        """Info for a directory that doesn't exist."""
        args = argparse.Namespace(path="/nonexistent/path")
        rc = run_info(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "does not exist" in err

    def test_shows_image(self, initialized_project, capsys):
        """Info shows the configured container image."""
        args = argparse.Namespace(path=initialized_project.project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-abcdef12)"),
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        # Default image from KanibakoConfig
        assert "ghcr.io/doctorjei/kanibako-oci:latest" in out

    def test_shows_project_image_override(self, initialized_project, capsys):
        """Info shows project-specific image when project.toml is set."""
        project_toml = initialized_project.proj.metadata_path / "project.toml"
        project_toml.write_text('[container]\nimage = "custom:v2"\n')
        args = argparse.Namespace(path=initialized_project.project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-abcdef12)"),
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "custom:v2" in out

    def test_credential_age_displayed(self, initialized_project, capsys):
        """Info shows credential file age when credentials exist."""
        creds_dir = initialized_project.proj.shell_path / ".claude"
        creds_dir.mkdir(parents=True, exist_ok=True)
        creds = creds_dir / ".credentials.json"
        creds.write_text('{"claudeAiOauth": {"token": "test"}}')
        # Mock target so credential_check_path returns the Claude path
        # regardless of whether the Claude plugin is installed.
        mock_target = MagicMock()
        mock_target.credential_check_path.return_value = creds
        args = argparse.Namespace(path=initialized_project.project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-abcdef12)"),
        ), patch(
            "kanibako.commands.box._parser.resolve_target",
            return_value=mock_target,
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        # Should show "ago" for a recently-created file.
        assert "ago" in out

    def test_no_credentials_shows_na(self, initialized_project, capsys):
        """Info shows n/a when no credentials file exists."""
        creds = initialized_project.proj.shell_path / ".claude" / ".credentials.json"
        # Mock target so credential_check_path returns a path that doesn't exist.
        mock_target = MagicMock()
        mock_target.credential_check_path.return_value = creds
        args = argparse.Namespace(path=initialized_project.project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-abcdef12)"),
        ), patch(
            "kanibako.commands.box._parser.resolve_target",
            return_value=mock_target,
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "n/a" in out


class TestRunInfoStandalone:
    def test_standalone_project(self, config_file, tmp_home, credentials_dir, capsys):
        """Info for a standalone project."""
        from kanibako.paths import resolve_standalone_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        resolve_standalone_project(
            std, config, project_dir=project_dir, initialize=True,
        )
        args = argparse.Namespace(path=project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-abcdef12)"),
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "standalone" in out
        assert "kanibako" in out

"""Tests for kanibako.commands.setup_cmd."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.setup_cmd import run_setup


@pytest.fixture
def setup_args():
    """Minimal argparse.Namespace for run_setup."""
    return argparse.Namespace()


class TestSetupRuntime:
    """Step 1: container runtime detection."""

    def test_setup_detects_runtime(self, setup_args, capsys):
        """When a runtime is available, Step 1 shows [ok]."""
        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman (podman version 5.0.0)"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={},
            ),
        ):
            rc = run_setup(setup_args)

        captured = capsys.readouterr()
        assert "[ok]" in captured.out
        assert "podman" in captured.out
        # No runtime → no early exit, but no agents either → returns 0
        assert rc == 0

    def test_setup_no_runtime_exits_1(self, setup_args, capsys):
        """When no runtime is found, setup returns 1."""
        with patch(
            "kanibako.commands.diagnose._check_runtime",
            return_value=("!!", "not found"),
        ):
            rc = run_setup(setup_args)

        assert rc == 1
        captured = capsys.readouterr()
        assert "No container runtime found" in captured.out


class TestSetupAgents:
    """Step 2: agent detection."""

    def test_setup_detects_agents(self, setup_args, capsys):
        """When an agent plugin is installed and detected, it shows [ok]."""
        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.detect.return_value = MagicMock()  # non-None = detected

        mock_cls = MagicMock(return_value=mock_target)

        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={"claude": mock_cls},
            ),
            patch(
                "kanibako.commands.diagnose._check_image",
                return_value=("ok", "test:latest (available locally)"),
            ),
            patch("kanibako.config.config_file_path"),
            patch("kanibako.config.load_merged_config"),
            patch("kanibako.paths.xdg"),
        ):
            rc = run_setup(setup_args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "[ok] Claude Code detected" in captured.out
        assert "You're ready to go" in captured.out

    def test_setup_no_agents(self, setup_args, capsys):
        """When no agent plugins are installed, it shows [!!]."""
        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={},
            ),
        ):
            rc = run_setup(setup_args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "No agent plugins installed" in captured.out

    def test_setup_agent_not_detected(self, setup_args, capsys):
        """When a plugin exists but agent binary is not found, it shows [--]."""
        mock_target = MagicMock()
        mock_target.display_name = "Claude Code"
        mock_target.detect.return_value = None  # not found on system

        mock_cls = MagicMock(return_value=mock_target)

        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={"claude": mock_cls},
            ),
            patch(
                "kanibako.commands.diagnose._check_image",
                return_value=("--", "not found"),
            ),
            patch("kanibako.config.config_file_path"),
            patch("kanibako.config.load_merged_config"),
            patch("kanibako.paths.xdg"),
        ):
            rc = run_setup(setup_args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "[--] Claude Code not found" in captured.out
        assert "Install an agent plugin" in captured.out


class TestSetupParser:
    """Verify setup is properly wired into the CLI."""

    def test_setup_parser(self):
        """'setup' is parseable from build_parser."""
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"
        assert hasattr(args, "func")

    def test_setup_in_subcommands(self):
        """'setup' is in _SUBCOMMANDS so it's recognized at the top level."""
        from kanibako.cli import _SUBCOMMANDS

        assert "setup" in _SUBCOMMANDS

    def test_setup_skips_ensure_initialized(self):
        """'setup' should work even before kanibako is initialized."""
        # Verify the skip list includes 'setup' by checking main() behavior.
        # We test the condition directly rather than running main().
        # The condition in cli.py is: args.command not in ("crab", "setup")
        from kanibako.cli import _ensure_initialized

        with (
            patch(
                "kanibako.commands.diagnose._check_runtime",
                return_value=("ok", "podman"),
            ),
            patch(
                "kanibako.targets.discover_targets",
                return_value={},
            ),
            patch.object(
                type(_ensure_initialized),
                "__call__",
                side_effect=AssertionError("should not be called"),
            ) if False else patch("kanibako.cli._ensure_initialized") as mock_init,
        ):
            from kanibako.cli import main

            with pytest.raises(SystemExit) as exc_info:
                main(["setup"])
            # main calls sys.exit(0) on success
            assert exc_info.value.code == 0
            mock_init.assert_not_called()

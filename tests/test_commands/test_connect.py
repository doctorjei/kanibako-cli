"""Tests for session persistence (tmux helpers) in kanibako.commands.start.

These tests replace the old connect-command tests.  The ``connect``
command was merged into ``start --persistent`` in Phase 7 of the CLI
audit.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kanibako.commands.start import (
    _tmux_available,
    _tmux_has_session,
    _tmux_session_name,
)


# ---------------------------------------------------------------------------
# _tmux_available
# ---------------------------------------------------------------------------


class TestTmuxAvailable:
    """Check tmux installation detection."""

    def test_returns_true_when_tmux_found(self):
        with patch("kanibako.commands.start.shutil.which", return_value="/usr/bin/tmux"):
            assert _tmux_available() is True

    def test_returns_false_when_tmux_missing(self):
        with patch("kanibako.commands.start.shutil.which", return_value=None):
            assert _tmux_available() is False


# ---------------------------------------------------------------------------
# _tmux_session_name
# ---------------------------------------------------------------------------


class TestTmuxSessionName:
    """Deterministic session naming."""

    def test_returns_prefixed_name(self):
        assert _tmux_session_name("myapp") == "kanibako-myapp"

    def test_handles_hyphens(self):
        assert _tmux_session_name("my-app") == "kanibako-my-app"

    def test_handles_underscores(self):
        assert _tmux_session_name("my_app") == "kanibako-my_app"


# ---------------------------------------------------------------------------
# _tmux_has_session
# ---------------------------------------------------------------------------


class TestTmuxHasSession:
    """Session existence detection."""

    def test_returns_true_on_zero_exit(self):
        mock_result = MagicMock(returncode=0)
        with patch("kanibako.commands.start.subprocess.run", return_value=mock_result) as m:
            assert _tmux_has_session("kanibako-myapp") is True
        m.assert_called_once_with(
            ["tmux", "has-session", "-t", "kanibako-myapp"],
            capture_output=True,
        )

    def test_returns_false_on_nonzero_exit(self):
        mock_result = MagicMock(returncode=1)
        with patch("kanibako.commands.start.subprocess.run", return_value=mock_result):
            assert _tmux_has_session("kanibako-myapp") is False


# ---------------------------------------------------------------------------
# Default persistence in run_start
# ---------------------------------------------------------------------------


class TestDefaultPersistence:
    """``run_start`` defaults to persistent when tmux is available."""

    def _make_args(self, persistent=False, ephemeral=False):
        args = MagicMock()
        args.persistent = persistent
        args.ephemeral = ephemeral
        args.new_session = False
        args.continue_session = False
        args.resume_session = False
        args.secure = False
        args.autonomous = False
        args.model = None
        args.no_helpers = False
        args.env = None
        args.agent_args = []
        args.project = None
        args.image = None
        args.entrypoint = None
        return args

    def test_persistent_by_default_when_tmux_available(self):
        """When neither --persistent nor --ephemeral is given and tmux is
        installed, _run_container receives persistent=True."""
        args = self._make_args()
        with (
            patch("kanibako.commands.start._tmux_available", return_value=True),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["persistent"] is True

    def test_ephemeral_by_default_when_tmux_missing(self):
        """When tmux is not installed, default to ephemeral."""
        args = self._make_args()
        with (
            patch("kanibako.commands.start._tmux_available", return_value=False),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["persistent"] is False

    def test_explicit_persistent_overrides_default(self):
        """--persistent forces persistent=True even without tmux."""
        args = self._make_args(persistent=True)
        with (
            patch("kanibako.commands.start._tmux_available", return_value=False),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["persistent"] is True

    def test_explicit_ephemeral_overrides_default(self):
        """--ephemeral forces persistent=False even with tmux."""
        args = self._make_args(ephemeral=True)
        with (
            patch("kanibako.commands.start._tmux_available", return_value=True),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["persistent"] is False


# ---------------------------------------------------------------------------
# CLI registration — connect removed
# ---------------------------------------------------------------------------


class TestConnectRemoved:
    """Verify the connect command is no longer registered."""

    def test_connect_not_in_subcommands(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "connect" not in _SUBCOMMANDS

    def test_connect_not_a_valid_subcommand(self):
        import pytest
        from kanibako.cli import build_parser
        parser = build_parser()
        # "connect" is no longer a registered subcommand — argparse rejects it
        with pytest.raises(SystemExit, match="2"):
            parser.parse_args(["connect"])

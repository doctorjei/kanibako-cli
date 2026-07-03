"""Tests for session persistence (tmux helpers) in kanibako.commands.start.

These tests replace the old connect-command tests.  The ``connect``
command was merged into ``start --persistent`` in Phase 7 of the CLI
audit.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kanibako.commands.start import (
    _bootstrap_available,
    _tmux_has_session,
    _tmux_session_name,
)


# ---------------------------------------------------------------------------
# _bootstrap_available
# ---------------------------------------------------------------------------


class TestTmuxAvailable:
    """Check tmux installation detection."""

    def test_returns_true_when_tmux_found(self):
        with patch("kanibako.commands.start.shutil.which", return_value="/usr/bin/tmux"):
            assert _bootstrap_available() is True

    def test_returns_false_when_tmux_missing(self):
        with patch("kanibako.commands.start.shutil.which", return_value=None):
            assert _bootstrap_available() is False


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

    def test_persistent_by_default_when_bootstrap_available(self):
        """When neither --persistent nor --ephemeral is given and tmux is
        installed, _run_container receives persistent=True."""
        args = self._make_args()
        with (
            patch("kanibako.commands.start._bootstrap_available", return_value=True),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
            patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["persistent"] is True

    def test_ephemeral_by_default_when_tmux_missing(self):
        """When tmux is not installed, default to ephemeral."""
        args = self._make_args()
        with (
            patch("kanibako.commands.start._bootstrap_available", return_value=False),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
            patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["persistent"] is False

    def test_explicit_persistent_when_bootstrap_present(self):
        """--persistent forces persistent=True when the program IS on the host."""
        args = self._make_args(persistent=True)
        with (
            patch(
                "kanibako.commands.start._resolve_bootstrap_program",
                return_value="tmux",
            ),
            patch("kanibako.commands.start._bootstrap_available", return_value=True),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
            patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["persistent"] is True

    def test_explicit_ephemeral_overrides_default(self):
        """--ephemeral forces persistent=False even with tmux."""
        args = self._make_args(ephemeral=True)
        with (
            patch("kanibako.commands.start._bootstrap_available", return_value=True),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
            patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        call_kwargs = m_run.call_args[1]
        assert call_kwargs["persistent"] is False


# ---------------------------------------------------------------------------
# `none` sentinel + host-absent note + explicit --persistent host-absent error
# ---------------------------------------------------------------------------


class TestBootstrapNoneAndHostNote:
    """box.bootstrap_program=none opt-out, the host-absent clue-in note, and the
    clean --persistent-with-absent-program error (run_start default path)."""

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

    def test_none_is_non_persistent_no_note_no_probe(self, capsys):
        """`none` opt-out: foreground (non-persistent), NO note, and
        _bootstrap_available is NEVER consulted (the user chose foreground)."""
        args = self._make_args()
        with (
            patch(
                "kanibako.commands.start._resolve_bootstrap_program",
                return_value="none",
            ),
            patch(
                "kanibako.commands.start._bootstrap_available"
            ) as m_avail,
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
            patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        assert m_run.call_args[1]["persistent"] is False
        m_avail.assert_not_called()
        assert capsys.readouterr().err == ""

    def test_host_note_fires_when_program_absent(self, capsys):
        """Configured program absent on host (default path): non-persistent AND
        one clue-in note naming the program, the consequence, and both remedies."""
        args = self._make_args()
        with (
            patch(
                "kanibako.commands.start._resolve_bootstrap_program",
                return_value="tmux",
            ),
            patch("kanibako.commands.start._bootstrap_available", return_value=False),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
            patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        assert m_run.call_args[1]["persistent"] is False
        err = capsys.readouterr().err
        assert "'tmux' not found on this host" in err  # names the program
        assert "foreground" in err                      # names the consequence
        assert "Install 'tmux'" in err                  # remedy 1: install
        assert "box.bootstrap_program=none" in err      # remedy 2: explicit opt-out

    def test_no_note_when_program_present(self, capsys):
        """Program present on host: persistent default, and NO note at all.

        Mutation guard: this is the negative half of the warning condition —
        flipping _bootstrap_available's return (the guarding condition) makes
        test_host_note_fires_when_program_absent go red, proving the note is
        gated on absence, not printed unconditionally."""
        args = self._make_args()
        with (
            patch(
                "kanibako.commands.start._resolve_bootstrap_program",
                return_value="tmux",
            ),
            patch("kanibako.commands.start._bootstrap_available", return_value=True),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
            patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
        ):
            from kanibako.commands.start import run_start
            run_start(args)
        assert m_run.call_args[1]["persistent"] is True
        assert capsys.readouterr().err == ""

    def test_explicit_persistent_absent_program_is_clean_error(self, capsys):
        """--persistent with the program absent on host: clean error (rc=1),
        NOT a silent force-through, and _run_container is never reached."""
        args = self._make_args(persistent=True)
        with (
            patch(
                "kanibako.commands.start._resolve_bootstrap_program",
                return_value="tmux",
            ),
            patch("kanibako.commands.start._bootstrap_available", return_value=False),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
            patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
        ):
            from kanibako.commands.start import run_start
            rc = run_start(args)
        assert rc == 1
        m_run.assert_not_called()
        err = capsys.readouterr().err
        assert "--persistent needs 'tmux' on this host" in err
        assert "not installed" in err

    def test_explicit_persistent_with_none_is_clean_error(self, capsys):
        """--persistent with box.bootstrap_program=none is a contradiction:
        clean error (rc=1), _run_container never reached."""
        args = self._make_args(persistent=True)
        with (
            patch(
                "kanibako.commands.start._resolve_bootstrap_program",
                return_value="none",
            ),
            patch("kanibako.commands.start._run_container", return_value=0) as m_run,
            patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
        ):
            from kanibako.commands.start import run_start
            rc = run_start(args)
        assert rc == 1
        m_run.assert_not_called()
        err = capsys.readouterr().err
        assert "--persistent requires a bootstrap program" in err
        assert "box.bootstrap_program=none" in err


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

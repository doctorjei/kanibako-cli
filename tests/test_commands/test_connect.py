"""Tests for session persistence (tmux helpers) in kanibako.commands.start.

These tests replace the old connect-command tests.  The ``connect``
command was merged into ``start --persistent`` in Phase 7 of the CLI
audit.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import kanibako.commands.start as start_mod
from kanibako.commands.start import BootstrapChoice


def _make_args(persistent=False, ephemeral=False, detach=False):
  args = MagicMock()
  args.persistent = persistent
  args.ephemeral = ephemeral
  args.detach = detach
  args.warm_only = False
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


def _run_without_host_program(args, program="tmux"):
  """Run ``run_start`` on a host where *program* is not on PATH.

  Returns ``(rc, _run_container mock, shutil.which mock)``.
  """
  with (
    patch(
      "kanibako.commands.start._resolve_bootstrap_program",
      return_value=BootstrapChoice(program, "claude"),
    ),
    patch("kanibako.commands.start.shutil.which", return_value=None) as m_which,
    patch("kanibako.commands.start._run_container", return_value=0) as m_run,
    patch("kanibako.commands.start.resolve_target", return_value=MagicMock()),
  ):
    rc = start_mod.run_start(args)
  return rc, m_run, m_which


# ---------------------------------------------------------------------------
# Persistence mode needs no host bootstrap program
# ---------------------------------------------------------------------------


class TestNoHostProbe:
  """The bootstrap program runs in the box; ``start`` never looks for it on
  the host."""

  def test_no_host_probe_helper(self):
    assert not hasattr(start_mod, "_bootstrap_available")

  def test_persistent_by_default_without_host_program(self, capsys):
    """No mode flag, no tmux on the host: persistent, and no note."""
    rc, m_run, m_which = _run_without_host_program(_make_args())
    assert rc == 0
    assert m_run.call_args[1]["persistent"] is True
    assert capsys.readouterr().err == ""
    assert "tmux" not in [c.args[0] for c in m_which.call_args_list if c.args]

  def test_explicit_persistent_without_host_program(self, capsys):
    """--persistent with no tmux on the host starts persistent; no refusal."""
    rc, m_run, m_which = _run_without_host_program(_make_args(persistent=True))
    assert rc == 0
    assert m_run.call_args[1]["persistent"] is True
    assert capsys.readouterr().err == ""
    assert "tmux" not in [c.args[0] for c in m_which.call_args_list if c.args]

  def test_explicit_ephemeral_overrides_default(self):
    """--ephemeral forces persistent=False."""
    rc, m_run, _ = _run_without_host_program(_make_args(ephemeral=True))
    assert rc == 0
    assert m_run.call_args[1]["persistent"] is False


# ---------------------------------------------------------------------------
# `none` sentinel
# ---------------------------------------------------------------------------


class TestBootstrapNone:
  """agent.default.bootstrap=none opt-out (run_start default path)."""

  _make_args = staticmethod(_make_args)

  def test_none_is_non_persistent_no_note(self, capsys):
    """`none` opt-out: foreground (non-persistent), and NO note."""
    rc, m_run, _ = _run_without_host_program(_make_args(), program="none")
    assert rc == 0
    assert m_run.call_args[1]["persistent"] is False
    assert capsys.readouterr().err == ""

  def test_explicit_persistent_with_none_is_clean_error(self, capsys):
    """--persistent with agent.claude.bootstrap=none is a contradiction:
    clean error (rc=1), _run_container never reached."""
    args = self._make_args(persistent=True)
    with (
      patch(
        "kanibako.commands.start._resolve_bootstrap_program",
        return_value=BootstrapChoice("none", "claude"),
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
    # Names the ACTUAL cause, on the resolved node's key, and the command.
    assert "agent.claude.bootstrap is 'none'" in err
    assert "kanibako system set agent.claude.bootstrap=tmux" in err


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

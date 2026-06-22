"""Tests for `kanibako setup` agent selection + completion marker (W1 Phase B)."""

from __future__ import annotations

import argparse

from kanibako import __version__
from kanibako.commands import setup_cmd
from kanibako.config import (
    config_file_path,
    load_config,
    read_default_agent,
    read_setup_completed,
)
from kanibako.paths import load_std_paths, xdg


class _FakeTarget:
    """Minimal stand-in for a kanibako Target plugin."""

    display_name = "Fake"
    _detects = True

    def detect(self):
        return object() if self._detects else None


def _make_target(name: str, detects: bool = True):
    return type(
        f"Target_{name}",
        (_FakeTarget,),
        {"display_name": name.title(), "_detects": detects},
    )


def _patch_targets(monkeypatch, targets: dict):
    """Patch discover_targets everywhere setup_cmd reaches for it."""
    monkeypatch.setattr(
        "kanibako.targets.discover_targets", lambda *a, **k: dict(targets)
    )


def _ns(**kw) -> argparse.Namespace:
    base = {"agent": None}
    base.update(kw)
    return argparse.Namespace(**base)


def _config_paths(tmp_home):
    cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    ssp = load_std_paths(load_config(cf)).settings
    return cf, ssp


# --- _run_agent_selection: --agent flag (non-interactive) -----------------


def test_agent_flag_valid_writes_default(tmp_home, config_file, monkeypatch):
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    selected = setup_cmd._run_agent_selection(_ns(agent="claude"))
    assert selected == "claude"
    _, ssp = _config_paths(tmp_home)
    assert read_default_agent(ssp) == "claude"


def test_agent_flag_bogus_errors_no_write(tmp_home, config_file, monkeypatch):
    import pytest

    from kanibako.errors import ConfigError

    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    # An unknown --agent is a HARD error, not a graceful skip: raises so that
    # run_setup aborts BEFORE writing the default or the completion marker.
    with pytest.raises(ConfigError) as exc_info:
        setup_cmd._run_agent_selection(_ns(agent="bogus"))
    assert "Unknown agent 'bogus'" in str(exc_info.value)
    _, ssp = _config_paths(tmp_home)
    assert read_default_agent(ssp) is None


def test_full_setup_bogus_agent_nonzero_no_marker_no_default(
    tmp_home, config_file, monkeypatch, capsys
):
    """`setup --agent bogus` → nonzero rc, NO marker, NO default written."""
    import pytest

    from kanibako.errors import ConfigError

    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_runtime", lambda: ("ok", "podman")
    )
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_image", lambda cfg: ("ok", "rig")
    )
    # run_setup lets the ConfigError propagate (cli.py turns it into rc=1);
    # critically, the marker write at the end of run_setup is never reached.
    with pytest.raises(ConfigError):
        setup_cmd.run_setup(_ns(agent="bogus"))
    cf, ssp = _config_paths(tmp_home)
    assert read_setup_completed(cf) is None
    assert read_default_agent(ssp) is None


# --- _run_agent_selection: interactive menu -------------------------------


def test_interactive_pick_writes_default(tmp_home, config_file, monkeypatch):
    _patch_targets(
        monkeypatch,
        {"claude": _make_target("claude"), "goose": _make_target("goose")},
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # detected sorted: claude, goose → "2" picks goose.
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    selected = setup_cmd._run_agent_selection(_ns())
    assert selected == "goose"
    _, ssp = _config_paths(tmp_home)
    assert read_default_agent(ssp) == "goose"


def test_interactive_single_agent_skip_silent(tmp_home, config_file, monkeypatch):
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # 1 agent → skip option is index 2; accepted silently (no confirm prompt).
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    selected = setup_cmd._run_agent_selection(_ns())
    assert selected is None
    _, ssp = _config_paths(tmp_home)
    assert read_default_agent(ssp) is None


def test_interactive_skip_two_agents_reprompts_then_confirms(
    tmp_home, config_file, monkeypatch
):
    _patch_targets(
        monkeypatch,
        {"claude": _make_target("claude"), "goose": _make_target("goose")},
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # Sequence: pick skip(3) → confirm "n" (re-prompt) → skip(3) → confirm "y".
    answers = iter(["3", "n", "3", "y"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    selected = setup_cmd._run_agent_selection(_ns())
    assert selected is None
    _, ssp = _config_paths(tmp_home)
    assert read_default_agent(ssp) is None


def test_interactive_skip_two_agents_unconfirmed_reprompts_to_pick(
    tmp_home, config_file, monkeypatch, capsys
):
    _patch_targets(
        monkeypatch,
        {"claude": _make_target("claude"), "goose": _make_target("goose")},
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # skip(3) → "n" (unconfirmed) re-prompts → then pick claude(1).
    answers = iter(["3", "n", "1"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    selected = setup_cmd._run_agent_selection(_ns())
    assert selected == "claude"
    out = capsys.readouterr().out
    assert "will FAIL" in out  # the gating warning was shown
    _, ssp = _config_paths(tmp_home)
    assert read_default_agent(ssp) == "claude"


# --- _run_agent_selection: non-TTY ----------------------------------------


def test_non_tty_no_flag_no_input_graceful(tmp_home, config_file, monkeypatch, capsys):
    _patch_targets(
        monkeypatch,
        {"claude": _make_target("claude"), "goose": _make_target("goose")},
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("input() must not be called in non-TTY mode")

    monkeypatch.setattr("builtins.input", _boom)
    selected = setup_cmd._run_agent_selection(_ns())
    assert selected is None
    out = capsys.readouterr().out
    assert "non-interactive" in out
    _, ssp = _config_paths(tmp_home)
    assert read_default_agent(ssp) is None


# --- marker write ---------------------------------------------------------


def test_marker_written_equals_version(tmp_home, config_file):
    setup_cmd._write_setup_marker()
    cf, _ = _config_paths(tmp_home)
    assert read_setup_completed(cf) == __version__


def test_full_setup_flag_writes_marker_and_default(
    tmp_home, config_file, monkeypatch
):
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_runtime", lambda: ("ok", "podman")
    )
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_image", lambda cfg: ("ok", "rig")
    )
    rc = setup_cmd.run_setup(_ns(agent="claude"))
    assert rc == 0
    cf, ssp = _config_paths(tmp_home)
    assert read_default_agent(ssp) == "claude"
    assert read_setup_completed(cf) == __version__


def test_full_setup_non_tty_writes_marker_no_default(
    tmp_home, config_file, monkeypatch
):
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_runtime", lambda: ("ok", "podman")
    )
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_image", lambda cfg: ("ok", "rig")
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = setup_cmd.run_setup(_ns())
    assert rc == 0
    cf, ssp = _config_paths(tmp_home)
    assert read_default_agent(ssp) is None
    assert read_setup_completed(cf) == __version__

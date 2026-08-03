"""Tests for `kanibako setup` agent selection + completion marker (W1 Phase B)."""

from __future__ import annotations

import argparse

from kanibako import __version__
from kanibako.commands import setup_cmd
from kanibako.settings.config import (
    config_file_path,
    load_config,
    read_system_agent,
    read_setup_completed,
)
from kanibako.settings.paths import load_std_paths, xdg


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
    assert read_system_agent(ssp) == "claude"


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
    assert read_system_agent(ssp) is None


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
    assert read_system_agent(ssp) is None


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
    assert read_system_agent(ssp) == "goose"


def test_interactive_single_agent_skip_silent(tmp_home, config_file, monkeypatch):
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # 1 agent → skip option is index 2; accepted silently (no confirm prompt).
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    selected = setup_cmd._run_agent_selection(_ns())
    assert selected is None
    _, ssp = _config_paths(tmp_home)
    assert read_system_agent(ssp) is None


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
    assert read_system_agent(ssp) is None


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
    assert read_system_agent(ssp) == "claude"


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
    assert read_system_agent(ssp) is None


# --- marker write ---------------------------------------------------------


def test_marker_written_equals_version(tmp_home, config_file):
    setup_cmd._write_setup_marker()
    cf, _ = _config_paths(tmp_home)
    assert read_setup_completed(cf) == __version__


def _stub_probes(monkeypatch):
    """Make Steps 1 and 3 succeed without touching a real runtime."""
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_runtime", lambda: ("ok", "podman")
    )
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_image", lambda cfg: ("ok", "rig")
    )


def _stage_templates():
    """Install the packaged templates so Step 5 has nothing to do (``CURRENT``)."""
    from kanibako.launch.templates import install_packaged_templates

    install_packaged_templates(_resolve_std(), setup_cmd._known_target_names())


def test_full_setup_flag_writes_marker_and_default(
    tmp_home, config_file, monkeypatch
):
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    _stub_probes(monkeypatch)
    # Templates already current → Step 5 is a no-op, isolating this to the
    # --agent flag.  A run with NOTHING TO DO still records completion.
    _stage_templates()
    rc = setup_cmd.run_setup(_ns(agent="claude"))
    assert rc == 0
    cf, ssp = _config_paths(tmp_home)
    assert read_system_agent(ssp) == "claude"
    assert read_setup_completed(cf) == __version__


def test_full_setup_non_tty_current_templates_writes_marker_no_default(
    tmp_home, config_file, monkeypatch
):
    """Non-TTY agent skip is graceful and STILL completes — Step 5 had nothing to do."""
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    _stub_probes(monkeypatch)
    _stage_templates()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = setup_cmd.run_setup(_ns())
    assert rc == 0
    cf, ssp = _config_paths(tmp_home)
    assert read_system_agent(ssp) is None
    assert read_setup_completed(cf) == __version__


def test_full_setup_non_tty_stale_templates_records_no_marker(
    tmp_home, config_file, monkeypatch, capsys
):
    """⚑ REGRESSION (F-1): a non-TTY run that SKIPPED Step 5 records NOTHING.

    The defect this pins: R-38 removed the per-branch template stamp but left the
    setup-completion marker unconditional, so a headless ``kanibako setup`` on an
    unmigrated store printed "cannot be updated non-interactively", refreshed
    nothing, and then recorded ``setup_completed = <this build>`` anyway.  That
    cleared ``setup_compat_gate``'s BCV hard block against a store setup never
    touched, and the next ``box create`` seeded an empty home, silently.

    The existing step-level test exercises ``_run_template_refresh`` in isolation
    and never reaches the marker write — which is exactly why the defect shipped.
    This one runs the WHOLE wizard.
    """
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    _stub_probes(monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # No _stage_templates(): the store is stale, exactly like a 1.7.x upgrade.
    rc = setup_cmd.run_setup(_ns())
    # ⚑ The EXIT STATUS carries the same claim the banner does: a run that
    # recorded nothing is a failure for anything scripting it (`kanibako setup &&
    # kanibako start`, `kanibako setup || exit 1`).
    assert rc == 1
    cf, _ = _config_paths(tmp_home)
    assert read_setup_completed(cf) is None
    out = capsys.readouterr().out
    # The banner must not claim a completion that was not recorded, and must
    # name the cure.
    assert "Setup Complete" not in out
    assert "Setup Incomplete" in out
    assert "--refresh-templates" in out


def test_full_setup_non_tty_stale_templates_refresh_flag_cures(
    tmp_home, config_file, monkeypatch, capsys
):
    """⚑ The refusal above has a REACHABLE cure: the documented headless path.

    ``kanibako setup --refresh-templates`` (with ``--agent`` to skip the menu) is
    the same stale store, non-interactive, and it both refreshes and completes.
    """
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    _stub_probes(monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = setup_cmd.run_setup(_ns(agent="claude", refresh_templates=True))
    assert rc == 0
    cf, ssp = _config_paths(tmp_home)
    assert read_setup_completed(cf) == __version__
    assert read_system_agent(ssp) == "claude"
    assert _staged_marker(_resolve_std()).is_file()  # the refresh really ran
    assert "Setup Complete" in capsys.readouterr().out


def test_full_setup_tty_decline_completes_and_exits_zero(
    tmp_home, config_file, monkeypatch, capsys
):
    """An INFORMED decline is a settled state: marker recorded, rc 0.

    The rc follows the MARKER, not the refresh.  Declining at the prompt leaves
    the store out of date by the user's own knowing choice, so the run is
    complete — the withholding rc is reserved for the run that could not ask.
    """
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    _stub_probes(monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    # No _stage_templates(): the store is stale, so Step 5 reaches the prompt.
    rc = setup_cmd.run_setup(_ns(agent="claude"))
    assert rc == 0
    cf, _ = _config_paths(tmp_home)
    assert read_setup_completed(cf) == __version__
    assert "Setup Complete" in capsys.readouterr().out


def test_full_setup_marker_write_failure_is_incomplete_and_nonzero(
    tmp_home, config_file, monkeypatch, capsys
):
    """⚑ A marker write that RAISES is a failed setup — banner and rc alike.

    The realistic shape is a read-only ``~/.config`` on an upgraded 1.7.x host:
    ``kanibako setup --refresh-templates`` refreshes the store, the marker write
    then raises, and the ``except`` arm used to leave ``recorded`` TRUE.  stdout
    printed "Setup Complete" and the command exited 0 while stderr said the
    completion could not be recorded — and the next ``kanibako start`` hard-
    blocked on the marker that was never written.
    """
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    _stub_probes(monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def _boom():
        raise OSError("Read-only file system")

    monkeypatch.setattr(setup_cmd, "_write_setup_marker", _boom)
    rc = setup_cmd.run_setup(_ns(agent="claude", refresh_templates=True))
    assert rc == 1
    cap = capsys.readouterr()
    assert "Could not record setup completion" in cap.err
    assert "Setup Complete" not in cap.out
    assert "Setup Incomplete" in cap.out
    # The cure must match the CAUSE: Step 5 settled here, so the "needs a
    # terminal / pass --refresh-templates" advice would be simply wrong.
    assert "writing the marker failed" in cap.out
    assert "needs an informed choice" not in cap.out
    cf, _ = _config_paths(tmp_home)
    assert read_setup_completed(cf) is None


# --- Step 2: binary-less shell agent ---------------------------------------


def test_step2_reports_binary_less_shell_as_ok(tmp_home, config_file, monkeypatch, capsys):
    """The binary-less 'Shell' (no_agent) target has no host binary; Step 2 must
    report it as available (image default), not '... not found on this system'.
    """
    from kanibako.targets.no_agent import NoAgentTarget

    _patch_targets(monkeypatch, {"no_agent": NoAgentTarget})
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_runtime", lambda: ("ok", "podman")
    )
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_image", lambda cfg: ("ok", "rig")
    )
    # Step 5 is not this test's subject, and its outcome now gates both the marker
    # and the RC — stage the templates so it reports CURRENT deterministically.
    _stage_templates()
    rc = setup_cmd.run_setup(_ns(agent="no_agent"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Shell" in out
    assert "not found on this system" not in out
    assert "image default" in out


# --- Step 5: template-refresh step (informed-consent flow) -----------------


def _system_table(tmp_home):
    """The raw ``[system]`` table of the global config file (or ``{}``)."""
    from kanibako.settings.config_io import load_doc

    cf, _ = _config_paths(tmp_home)
    if not cf.exists():
        return {}
    return load_doc(cf).get("system", {}) or {}


def _resolve_std():
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    cf, _ = setup_cmd._settings_paths()
    return load_std_paths(load_config(cf))


def _staged_marker(std):
    """One packaged file whose STAGED host path proves the install ran.

    ``@system.template/box/home/canon/notebook/MY_CONTENTS.md`` — the box mould's
    notebook entry point, chosen because it is in the SYSTEM-OWNED staging (the only
    tier ``refresh=True`` may rewrite), not in a user-owned store.
    """
    return (
        std.template / "box" / "home" / "canon" / "notebook" / "MY_CONTENTS.md"
    )


def test_only_skipped_outcome_withholds_completion():
    """⚑ The marker gate: SKIPPED is the ONLY outcome that withholds completion.

    Pinned as a set equality in BOTH directions over the whole enum, which is what
    makes "a NEW outcome cannot inherit either answer silently" true rather than
    merely intended.  A single ``withholding == {SKIPPED}`` assertion does NOT
    catch a new member: paired with the old negative property
    (``self is not SKIPPED``) a new member landed in the RECORDING set, which that
    assertion never looked at, so it passed — an unnoticed member would have
    cleared the ``setup_compat_gate`` BCV block with a green suite.

    With the property now a POSITIVE membership test, a new member fails here
    whichever way it is introduced: left alone it falls into ``withholding``;
    added to the property's recording set it falls into ``recording``.  Either
    way it must be named below, which is the explicit decision this test forces.
    """
    recording = {step for step in setup_cmd.TemplateStep if step.records_completion}
    withholding = set(setup_cmd.TemplateStep) - recording

    assert withholding == {setup_cmd.TemplateStep.SKIPPED}
    assert recording == {
        setup_cmd.TemplateStep.REFRESHED,
        setup_cmd.TemplateStep.CURRENT,
        setup_cmd.TemplateStep.DECLINED,
    }


def test_template_step_forced_flag_applies(tmp_home, config_file):
    """--refresh-templates: apply the refresh non-interactively (no prompt).

    ⚑ R-38: the step no longer writes ``system.templates_stamp`` — pinned here so a
    reintroduced writer fails rather than silently resurrecting the retired key.
    """
    outcome = setup_cmd._run_template_refresh(_ns(refresh_templates=True))
    assert outcome is setup_cmd.TemplateStep.REFRESHED
    std = _resolve_std()
    assert _staged_marker(std).is_file()  # applied
    assert (
        std.agents / "claude" / "template" / "box" / "home" / ".claude.json"
    ).is_file()
    assert "templates_stamp" not in _system_table(tmp_home)


def test_template_step_nothing_to_do_reports_up_to_date(tmp_home, config_file, capsys):
    """Already current → say so, no prompt, nothing recorded."""
    from kanibako.launch.templates import install_packaged_templates

    std = _resolve_std()
    install_packaged_templates(std, setup_cmd._known_target_names())
    outcome = setup_cmd._run_template_refresh(_ns())
    assert outcome is setup_cmd.TemplateStep.CURRENT
    assert "up to date" in capsys.readouterr().out
    assert "templates_stamp" not in _system_table(tmp_home)


def test_template_step_tty_accept_applies(tmp_home, config_file, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    outcome = setup_cmd._run_template_refresh(_ns())
    assert outcome is setup_cmd.TemplateStep.REFRESHED
    std = _resolve_std()
    assert _staged_marker(std).is_file()  # applied
    assert "templates_stamp" not in _system_table(tmp_home)


def test_template_step_tty_decline_does_not_apply(
    tmp_home, config_file, monkeypatch, capsys
):
    """Decline: do NOT apply, and (R-38) record no template stamp either.

    ⚑ The decline is an INFORMED choice, so it still reports ``DECLINED`` — an
    outcome that DOES record setup completion (pre-R-38 this branch stamped too).
    Only the un-asked non-TTY skip withholds the marker.
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    outcome = setup_cmd._run_template_refresh(_ns())
    assert outcome is setup_cmd.TemplateStep.DECLINED
    assert outcome.records_completion
    std = _resolve_std()
    # Not applied — the shipped files were NOT written.
    assert not _staged_marker(std).exists()
    assert "templates_stamp" not in _system_table(tmp_home)
    assert "unblessed state" in capsys.readouterr().out


def test_template_step_non_tty_no_flag_skips(
    tmp_home, config_file, monkeypatch, capsys
):
    """Non-tty, no forced flag → SKIP; the store is left exactly as it was."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("input() must not be called in non-TTY mode")

    monkeypatch.setattr("builtins.input", _boom)
    outcome = setup_cmd._run_template_refresh(_ns())
    assert outcome is setup_cmd.TemplateStep.SKIPPED
    assert not outcome.records_completion
    std = _resolve_std()
    assert not _staged_marker(std).exists()  # not applied
    assert "cannot be updated non-interactively" in capsys.readouterr().out
    assert "templates_stamp" not in _system_table(tmp_home)


def test_setup_refresh_templates_flag_parsed(monkeypatch):
    """`setup --refresh-templates` parses to args.refresh_templates = True."""
    import argparse

    parser = argparse.ArgumentParser()
    setup_cmd.add_arguments(parser)
    args = parser.parse_args(["--refresh-templates"])
    assert args.refresh_templates is True
    args = parser.parse_args([])
    assert args.refresh_templates is False


def test_full_setup_with_refresh_flag_writes_marker_only(
    tmp_home, config_file, monkeypatch
):
    """`setup --refresh-templates` (full run) writes the setup MARKER and nothing else.

    ⚑ R-38: the run used to write ``system.templates_stamp`` alongside the marker.
    The marker is now the whole of what setup records.
    """
    _patch_targets(monkeypatch, {"claude": _make_target("claude")})
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_runtime", lambda: ("ok", "podman")
    )
    monkeypatch.setattr(
        "kanibako.commands.diagnose._check_image", lambda cfg: ("ok", "rig")
    )
    rc = setup_cmd.run_setup(_ns(agent="claude", refresh_templates=True))
    assert rc == 0
    cf, _ = _config_paths(tmp_home)
    assert read_setup_completed(cf) == __version__
    assert "templates_stamp" not in _system_table(tmp_home)

"""Explicit box creation — no auto-create on launch (Jei 2026-07-11g, v1.7.0).

Creating a box is a deliberate act: it must go through ``kanibako create``.  A
launch (``start`` / bare ``kanibako`` / ``code`` / ``shell``) NEVER materialises a
new box — it ERRORS if the target box does not exist.  Auto-START of an EXISTING
box is unchanged.  These are REAL-path tests (real ``std``/resolver/journal); the
gate errors BEFORE any container work, so no runtime stubbing is needed for the
absent-box cases.
"""

from __future__ import annotations

import argparse

from kanibako.commands.start import _no_box_error, _resolve_existing_box, _run_container


def _launch(project_dir, **over):
    """Minimal ``_run_container`` launch (foreground start) for a target."""
    kwargs = dict(
        project_dir=project_dir, entrypoint=None, image_override=None,
        new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
    )
    kwargs.update(over)
    return _run_container(**kwargs)


def _create_args(path, **over):
    ns = argparse.Namespace(
        path=str(path), standalone=False, no_vault=True,
        name=None, image=None, agent=None, allow_home=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _std(config_file):
    from kanibako.config import load_config
    from kanibako.paths import load_std_paths

    config = load_config(config_file)
    return config, load_std_paths(config)


# ---------------------------------------------------------------------------
# Launch on an ABSENT box → exact error + non-zero exit (no box materialised)
# ---------------------------------------------------------------------------

class TestLaunchAbsentBoxErrors:
    def test_bare_kanibako_cwd_errors(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """Bare ``kanibako`` (project_dir=None → cwd) on a dir with no box errors
        and materialises NOTHING; the suggestion is a bare ``kanibako create``."""
        _config, std = _std(config_file)
        rc = _launch(None)
        assert rc == 1
        err = capsys.readouterr().err
        assert "no box at" in err
        assert "run 'kanibako create'" in err
        # No box was invented for the wrong cwd.
        assert not std.boxes.exists() or not any(std.boxes.iterdir())

    def test_start_named_box_errors_with_copy_pasteable_spec(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """A bare NAME that resolves to no registered box errors, and the suggested
        ``create`` carries that spec so it is copy-pasteable."""
        _config, std = _std(config_file)
        rc = _launch("ghostbox")
        assert rc == 1
        err = capsys.readouterr().err
        assert "no box at ghostbox" in err
        assert "run 'kanibako create ghostbox'" in err
        assert not std.boxes.exists() or not any(std.boxes.iterdir())

    def test_shell_absent_box_errors(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """``kanibako shell`` (box_shell_mode) routes through the same gate."""
        _config, std = _std(config_file)
        rc = _launch(None, box_shell_mode=True)
        assert rc == 1
        assert "no box at" in capsys.readouterr().err
        assert not std.boxes.exists() or not any(std.boxes.iterdir())


# ---------------------------------------------------------------------------
# `create` materialises + prints the start-hint; create-then-launch passes gate
# ---------------------------------------------------------------------------

class TestCreateAndThenLaunch:
    def test_create_prints_start_hint(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.commands.box._parser import run_create

        rc = run_create(_create_args(tmp_home / "project"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Created default project in" in out
        # The copy-pasteable start hint (Jei's exact wording).
        assert "Start the box by executing 'kanibako'" in out
        assert "shortcuts to 'kanibako start'" in out

    def test_create_then_launch_passes_gate(
        self, config_file, tmp_home, credentials_dir
    ):
        """After ``create`` the box EXISTS, so the launch gate resolves it (no
        "no box" error) — create-then-start works."""
        from kanibako.commands.box._parser import run_create

        config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        # cwd is tmp_home/project (fixture chdir); the launch resolves the box.
        proj = _resolve_existing_box(std, config, None)
        assert proj is not None
        assert proj.name == "project"

    def test_existing_box_still_resolves_for_autostart(
        self, config_file, tmp_home, credentials_dir
    ):
        """An EXISTING (stopped) box passes the gate — auto-START is unchanged.

        (The full stopped→start launch flow is covered by the ``start_mocks``
        suite; here we assert the gate itself does not block an existing box.)"""
        from kanibako.paths import resolve_box_target

        config, std = _std(config_file)
        # Materialise + register a bare box the way `create` would.
        resolve_box_target(
            std, config, None, initialize=True, register=True, warn=False,
        )
        proj = _resolve_existing_box(std, config, None)
        assert proj is not None
        assert proj.name == "project"


# ---------------------------------------------------------------------------
# Crash-recovery boundary: launch errors on a half-created box; create completes
# ---------------------------------------------------------------------------

class TestInterruptedCreateBoundary:
    def test_launch_does_not_resurrect_half_created_box(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """An INTERRUPTED create (box dir + pending journal entry, but NOT yet
        registered) reads as "no box" on a launch — the launch errors rather than
        silently completing someone's half-finished create.  Re-running ``create``
        is what completes it (forward-recovery belongs to create)."""
        from kanibako.commands.box._parser import run_create
        from kanibako.commands.start import _pending_create_entry, _write_create_entry
        from kanibako.paths import load_primary_boxes, resolve_project

        config, std = _std(config_file)
        project_dir = str(tmp_home / "project")

        # Simulate a crash mid-create: the deferred resolve created boxes/project
        # + meta (register=False → NOT registered) and the write-ahead journal
        # entry was written, but the box was never registered / the entry cleared.
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True, register=False,
        )
        _write_create_entry(std, proj)
        assert (std.boxes / "project").is_dir()
        assert _pending_create_entry(std, proj) is not None
        assert load_primary_boxes(std.primary_workset) == {}  # unregistered

        # LAUNCH must treat the not-yet-registered box as "no box" → error, NOT
        # resurrect/complete it.
        assert _resolve_existing_box(std, config, None) is None
        rc = _launch(None)
        assert rc == 1
        assert "no box at" in capsys.readouterr().err

        # Re-running `create` COMPLETES the interrupted create (forward-recovery),
        # after which the box is registered and the launch gate resolves it.
        rc_create = run_create(_create_args(tmp_home / "project"))
        assert rc_create == 0
        assert _pending_create_entry(std, proj) is None
        assert load_primary_boxes(std.primary_workset).get("project") == project_dir
        assert _resolve_existing_box(std, config, None) is not None


# ---------------------------------------------------------------------------
# `_no_box_error` message shape (unit)
# ---------------------------------------------------------------------------

class TestNoBoxErrorMessage:
    def test_named_spec_is_copy_pasteable(self):
        msg = _no_box_error("myproj")
        assert msg == (
            "Error: no box at myproj. To create a new box, "
            "run 'kanibako create myproj'"
        )

    def test_no_spec_suggests_bare_create(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        msg = _no_box_error(None)
        assert msg == (
            f"Error: no box at {tmp_path}. To create a new box, "
            "run 'kanibako create'"
        )

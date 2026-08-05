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

from kanibako.commands.start import (
    _no_box_error,
    _resolve_existing_box,
    _run_container,
    _unbuilt_box_error,
)


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
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

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
        from kanibako.settings.paths import resolve_box_target

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
        from kanibako.settings.paths import load_primary_boxes, resolve_project

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
# MBR-6: a launch REFUSES a registered box whose directory is gone
# ---------------------------------------------------------------------------

class TestLaunchRefusesUnbuiltBox:
    """Jei 2026-08-02f: *"no, a launch should not silently rebuild anything."*

    A registered box whose directory has been deleted used to be re-materialised
    by the launch resolve — a REPAIR, not a creation.  It now refuses, names the
    box, and leaves the filesystem untouched.
    """

    def test_registered_box_with_missing_dir_refuses_and_builds_nothing(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        import shutil

        from kanibako.commands.box._parser import run_create
        from kanibako.settings.paths import load_primary_boxes

        config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        box_dir = std.boxes / "project"
        assert box_dir.is_dir()
        capsys.readouterr()

        # The box dir goes; the registration survives.  That IS the case.
        shutil.rmtree(box_dir)
        assert load_primary_boxes(std.primary_workset).get("project") == str(
            tmp_home / "project"
        )

        rc = _launch(None)
        assert rc == 1
        err = capsys.readouterr().err
        assert "is registered, but its box directory is gone" in err
        assert "box 'project'" in err
        assert "will not rebuild it" in err
        # NOTHING was materialised — not the box dir, not a home tree.
        assert not box_dir.exists()
        # ...and the registration is left exactly as it was, so the cure below
        # has something to work with.
        assert load_primary_boxes(std.primary_workset).get("project") == str(
            tmp_home / "project"
        )

    def test_the_cure_the_refusal_names_actually_works(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """The B9 standard: a cure that does not work is worse than no cure.

        The message names ``kanibako create <workspace>``; run exactly that and
        the box must come back and pass the launch gate.
        """
        import shutil

        from kanibako.commands.box._parser import run_create

        config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        shutil.rmtree(std.boxes / "project")
        capsys.readouterr()

        assert _launch(None) == 1
        err = capsys.readouterr().err
        assert f"Rebuild it:  kanibako create {tmp_home / 'project'}" in err

        # The cure, run as printed.
        assert run_create(_create_args(tmp_home / "project")) == 0
        assert (std.boxes / "project" / "home").is_dir()
        assert _resolve_existing_box(std, config, None) is not None

    def test_shell_and_named_target_route_through_the_same_gate(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """``kanibako shell`` and a bare-NAME target hit the one chokepoint too."""
        import shutil

        from kanibako.commands.box._parser import run_create

        _config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        shutil.rmtree(std.boxes / "project")
        capsys.readouterr()

        assert _launch(None, box_shell_mode=True) == 1
        assert "its box directory is gone" in capsys.readouterr().err
        assert _launch("project") == 1
        assert "its box directory is gone" in capsys.readouterr().err
        assert not (std.boxes / "project").exists()


class TestUnbuiltBoxErrorMessage:
    """``_unbuilt_box_error`` unit: which boxes it refuses, and the cure it names."""

    def test_intact_box_is_not_refused(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.commands.box._parser import run_create

        config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        proj = _resolve_existing_box(std, config, None)
        assert proj is not None
        assert _unbuilt_box_error(proj) is None

    def test_connected_workset_box_is_not_refused_before_its_first_launch(
        self, config_file, tmp_home, credentials_dir
    ):
        """⚑ The reason the test is the BOX DIR and not the home tree.

        ``workset connect`` registers the box and creates its dir but NEVER
        seeds — the home is materialised by the FIRST launch.  Gating on
        ``shell_path`` would refuse that sanctioned flow.
        """
        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.paths import resolve_box_target

        config, std = _std(config_file)
        ws = create_workset("wsa", tmp_home / "wsa", std)
        src = tmp_home / "member"
        src.mkdir()
        add_project(ws, "member", src, std)

        proj = resolve_box_target(
            std, config, str(src), initialize=False, register=True, warn=False,
        )
        assert proj.name == "member"
        assert not proj.shell_path.exists()  # never seeded by connect
        assert _unbuilt_box_error(proj) is None

    def test_named_box_with_missing_dir_names_the_workset_cure(
        self, config_file, tmp_home, credentials_dir
    ):
        """``create`` refuses inside a workset member ("already initialized"), so
        the named-box cure is ``workset disconnect`` + ``workset connect``."""
        import shutil

        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.paths import resolve_box_target

        config, std = _std(config_file)
        ws = create_workset("wsa", tmp_home / "wsa", std)
        src = tmp_home / "member"
        src.mkdir()
        add_project(ws, "member", src, std)

        proj = resolve_box_target(
            std, config, str(src), initialize=False, register=True, warn=False,
        )
        shutil.rmtree(proj.metadata_path)

        msg = _unbuilt_box_error(proj)
        assert msg is not None
        assert "box 'member' is registered" in msg
        assert (
            f"Rebuild it:  kanibako workset disconnect wsa member "
            f"&& kanibako workset connect wsa {src}"
        ) in msg


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

"""Explicit box creation — no auto-create on launch (Jei 2026-07-11g, v1.7.0).

Creating a box is a deliberate act: it must go through ``kanibako create``.  A
launch (``start`` / bare ``kanibako`` / ``code`` / ``shell``) NEVER materializes a
new box — it ERRORS if the target box does not exist.  Auto-START of an EXISTING
box is unchanged.  These are REAL-path tests (real ``std``/resolver/journal); the
gate errors BEFORE any container work, so no runtime stubbing is needed for the
absent-box cases.
"""

from __future__ import annotations

import argparse
import re
import shlex

import pytest

from kanibako.commands.start import (
    _no_box_error,
    _resolve_existing_box,
    _run_container,
    _unbuilt_box_error,
)

# ⚑ NEVER ``shutil.rmtree`` A BOX TREE FROM A TEST BODY — not even to set up a case.
# These tests drive the REAL ``run_create``, which materializes the J-7 canon skeleton
# and then makes it root-owned + 555 via ``podman unshare``.  Where that works (CI
# runners, bifrost) a bare ``rmtree`` of the box dir dies with EACCES partway through;
# where it does not (this project's dev box, broken ``newuidmap``) the protect pass
# short-circuits and the same line passes.  That asymmetry is the whole bug — it is why
# the three tests below were green here and red in CI, and it is the SAME failure
# ``test_commands/test_box_register.py`` already carries a note about.  ``remove_box_tree``
# is the sanctioned escalating deleter the product's own lifecycle verbs use, so the
# test tree carries no second, driftable copy of the escalation.
from kanibako.runtime.container import remove_box_tree
from kanibako.settings.paths import BoxMode, _early_scope


def _launch(project_dir, **over):
    """Minimal ``_run_container`` launch (foreground start) for a target."""
    kwargs = dict(
        project_dir=project_dir, entrypoint=None, image_override=None,
        new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
    )
    kwargs.update(over)
    return _run_container(**kwargs)


def _create_args(path, **over):
    # ⚑ ``no_vault=True`` is this suite's vault SUPPRESSION shorthand, and it is
    # indistinguishable from a typed ``--no-vault`` — a SHAPING flag the recovery
    # refusal must refuse.  A recovery re-run passes ``no_vault=False`` to spell
    # the flag the way the CLI spells it when absent.
    ns = argparse.Namespace(
        path=str(path), standalone=False, no_vault=True,
        name=None, image=None, agent=None, allow_home=False,
        recover=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    config = load_config(config_file)
    return config, load_std_paths(config)


def _primary_boxes(std):
    """The PRIMARY membership, read with *std*'s early record."""
    from kanibako.settings.paths import load_primary_boxes

    return load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))


# ---------------------------------------------------------------------------
# `create` at a path a NAMED box already holds → refused, nothing written
# ---------------------------------------------------------------------------

def _connected_member(tmp_home, std, *, name="extbox", dirname="ext"):
    """A workset with ONE externally-connected member at ``tmp_home/<dirname>``."""
    from kanibako.project.workset import add_project, create_workset

    ws = create_workset("wsa", tmp_home / "wsa", std)
    member = tmp_home / dirname
    member.mkdir()
    add_project(ws, name, member, std)
    return member


class TestCreateRefusesNamedBoxWorkspace:
    """The PATH half of "one record per project" at the ``create`` door.

    ``create`` picked a PRIMARY box name from the path, so a path a NAMED box
    already holds collided with nothing the NAME guard could see — and the box
    it made was then unreachable, because the ancestor-walk detection resolves
    that directory to the named box.
    """

    def test_create_at_a_connected_path_is_refused(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.commands.box._parser import run_create

        _config, std = _std(config_file)
        member = _connected_member(tmp_home, std)

        assert run_create(_create_args(member)) == 1
        err = capsys.readouterr().err
        assert "already the workspace of named box 'extbox'" in err
        assert "in workset 'wsa'" in err
        # ⚑ Qualified: a bare name tries the PRIMARY workset first.
        assert "kanibako box show wsa/extbox" in err
        # The WRITE is what the refusal is for: no membership row, no box dir.
        assert _primary_boxes(std) == {}
        assert not any(std.boxes.iterdir()) if std.boxes.exists() else True

    def test_create_inside_a_connected_path_is_refused(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """The resolver matches the DEEPEST registered ANCESTOR, as ``connect`` does."""
        from kanibako.commands.box._parser import run_create

        _config, std = _std(config_file)
        member = _connected_member(tmp_home, std)
        inner = member / "deep" / "inner"
        inner.mkdir(parents=True)

        assert run_create(_create_args(inner)) == 1
        assert "already the workspace of named box 'extbox'" in capsys.readouterr().err
        assert _primary_boxes(std) == {}

    def test_a_neighbour_path_is_not_refused(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """The guard is the OWNING PATH, not the containing directory."""
        from kanibako.commands.box._parser import run_create

        _config, std = _std(config_file)
        _connected_member(tmp_home, std)
        neighbour = tmp_home / "ext2"
        neighbour.mkdir()

        assert run_create(_create_args(neighbour)) == 0
        assert "already the workspace of named box" not in capsys.readouterr().err

    def test_an_in_tree_member_is_not_refused(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """A member under the workset's own ``workset.workspaces`` is not this guard's.

        The resolver skips in-tree members, so this guard never claims them; that
        path is in the workset's path space, whose own rule decides the create.
        Only this guard's message is pinned here, whatever the exit code.
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.project.workset import add_project, create_workset

        _config, std = _std(config_file)
        ws = create_workset("wsa", tmp_home / "wsa", std)
        member = ws.root / "workspaces" / "m1"
        member.mkdir(parents=True)
        add_project(ws, "m1", member, std)

        run_create(_create_args(member))
        assert "already the workspace of named box" not in capsys.readouterr().err

    def test_standalone_at_a_connected_path_is_refused(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """The rule is mode-free: detection finds the connected box before a
        standalone marker, so a standalone box there would be unreachable too."""
        from kanibako.commands.box._parser import run_create

        _config, std = _std(config_file)
        member = _connected_member(tmp_home, std)
        before = sorted(p.name for p in member.iterdir())

        assert run_create(_create_args(member, standalone=True)) == 1
        err = capsys.readouterr().err
        assert "already the workspace of named box 'extbox'" in err
        assert sorted(p.name for p in member.iterdir()) == before

    def test_the_cure_the_refusal_names_actually_works(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """The named cure is the verb the refusal prints, run as printed."""
        from kanibako.commands.box._parser import run_create

        _config, std = _std(config_file)
        member = _connected_member(tmp_home, std)

        assert run_create(_create_args(member)) == 1
        cure = "kanibako workset disconnect wsa extbox --force"
        assert cure in capsys.readouterr().err

        from kanibako.commands.workset_cmd import run_disconnect
        import argparse

        assert run_disconnect(argparse.Namespace(
            workset="wsa", project="extbox", force=True, remove_files=False,
        )) == 0
        capsys.readouterr()
        # The path is free now, so the same create that was refused succeeds.
        assert run_create(_create_args(member)) == 0
        assert list(_primary_boxes(std)) == ["ext"]


# ---------------------------------------------------------------------------
# Launch on an ABSENT box → exact error + non-zero exit (no box materialized)
# ---------------------------------------------------------------------------

class TestLaunchAbsentBoxErrors:
    def test_bare_kanibako_cwd_errors(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """Bare ``kanibako`` (project_dir=None → cwd) on a dir with no box errors
        and materializes NOTHING; the suggestion is a bare ``kanibako create``.

        ⚑ The suggestion is no longer wrapped in single quotes: the printed
        command is the tail of the line, so a reader copies it and it pastes.
        That is why this pin reads ``run:  `` and not ``run '...'``.
        """
        _config, std = _std(config_file)
        rc = _launch(None)
        assert rc == 1
        err = capsys.readouterr().err
        assert "no box at" in err
        assert "run:  kanibako create" in err
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
        assert "kanibako create ghostbox" in err
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
# `create` materializes + prints the start-hint; create-then-launch passes gate
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
        # Materialize + register a bare box the way `create` would.
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
        silently completing someone's half-finished create.  ``create --recover``
        is what completes it (forward-recovery belongs to create)."""
        from kanibako.commands.box._parser import run_create
        from kanibako.commands.start import _pending_create_entry, _write_create_entry
        from kanibako.settings.paths import resolve_project

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
        assert _primary_boxes(std) == {}  # unregistered

        # LAUNCH must treat the not-yet-registered box as "no box" → error, NOT
        # resurrect/complete it.
        assert _resolve_existing_box(std, config, None) is None
        rc = _launch(None)
        assert rc == 1
        assert "no box at" in capsys.readouterr().err

        # Re-running `create --recover` COMPLETES the interrupted create
        # (forward-recovery), after which the box is registered and the launch
        # gate resolves it.
        rc_create = run_create(
            _create_args(tmp_home / "project", recover=True, no_vault=False)
        )
        assert rc_create == 0
        assert _pending_create_entry(std, proj) is None
        assert _primary_boxes(std).get("project") == project_dir
        assert _resolve_existing_box(std, config, None) is not None

    @staticmethod
    def _half_created_standalone(std, config, root):
        """A standalone create stopped after its resolve and journal entry."""
        from kanibako.commands.start import _write_create_entry
        from kanibako.settings.paths import resolve_standalone_project

        root.mkdir()
        proj = resolve_standalone_project(
            std, config, str(root), initialize=True, register=False,
        )
        _write_create_entry(std, proj)
        return proj

    def test_launch_refuses_half_created_standalone_and_keeps_its_entry(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """The import pass must not adopt a standalone box whose create is pending:
        the launch refuses naming ``create --recover``, the entry survives, and
        ``create --recover`` then finishes the box."""
        from kanibako.commands.box._parser import run_create
        from kanibako.commands.start import _pending_create_entry
        from kanibako.project import registry_store

        config, std = _std(config_file)
        root = tmp_home / "sa"
        proj = self._half_created_standalone(std, config, root)

        assert _launch(str(root)) == 1
        err = capsys.readouterr().err
        assert "Imported" not in err
        assert (f"Finish it:  kanibako create --standalone --recover {root}"
                in [ln.strip() for ln in err.splitlines()])
        assert _pending_create_entry(std, proj) is not None
        assert registry_store.load_standalone(std.registry) == {}

        assert run_create(
            _create_args(root, standalone=True, recover=True, no_vault=False)
        ) == 0
        assert _pending_create_entry(std, proj) is None
        assert _resolve_existing_box(std, config, str(root)) is not None

    def test_register_refuses_half_created_standalone_and_keeps_its_entry(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.commands.box._parser import run_register
        from kanibako.commands.start import _pending_create_entry
        from kanibako.project import registry_store

        config, std = _std(config_file)
        root = tmp_home / "sa"
        proj = self._half_created_standalone(std, config, root)

        assert run_register(
            argparse.Namespace(target=str(root), box=None)
        ) == 1
        err = capsys.readouterr().err
        assert (f"kanibako create --standalone --recover --register {root}"
                in [ln.strip() for ln in err.splitlines()])
        assert _pending_create_entry(std, proj) is not None
        assert registry_store.load_standalone(std.registry) == {}


# ---------------------------------------------------------------------------
# MBR-6: a launch REFUSES a registered box whose directory is gone
# ---------------------------------------------------------------------------

class TestLaunchRefusesUnbuiltBox:
    """Jei 2026-08-02f: *"no, a launch should not silently rebuild anything."*

    A registered box whose directory has been deleted used to be re-materialized
    by the launch resolve — a REPAIR, not a creation.  It now refuses, names the
    box, and leaves the filesystem untouched.

    ⚑ EVERY test in this class takes ``protected_canon``.  Removing the box dir is
    the SETUP for all three, and on an unprotected host that removal is trivial —
    so without the fixture the local run cannot see the failure CI sees.  The
    fixture reproduces the MODE half (555/444) deterministically; ownership still
    only exists on CI/bifrost, so this makes the local run meaningful, not an
    oracle.
    """

    def test_registered_box_with_missing_dir_refuses_and_builds_nothing(
        self, config_file, tmp_home, credentials_dir, capsys, protected_canon
    ):
        from kanibako.commands.box._parser import run_create

        config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        box_dir = std.boxes / "project"
        assert box_dir.is_dir()
        capsys.readouterr()

        # The box dir goes; the registration survives.  That IS the case.
        assert remove_box_tree(box_dir), "the box tree must actually be gone"
        assert _primary_boxes(std).get("project") == str(
            tmp_home / "project"
        )

        rc = _launch(None)
        assert rc == 1
        err = capsys.readouterr().err
        assert "is registered, but its box directory is gone" in err
        assert "box 'project'" in err
        assert "will not rebuild it" in err
        # NOTHING was materialized — not the box dir, not a home tree.
        assert not box_dir.exists()
        # ...and the registration is left exactly as it was, so the cure below
        # has something to work with.
        assert _primary_boxes(std).get("project") == str(
            tmp_home / "project"
        )

    def test_the_cure_the_refusal_names_actually_works(
        self, config_file, tmp_home, credentials_dir, capsys, protected_canon
    ):
        """The B9 standard: a cure that does not work is worse than no cure.

        The message names ``kanibako create <workspace>``; run exactly that and
        the box must come back and pass the launch gate.
        """
        from kanibako.commands.box._parser import run_create

        config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        assert remove_box_tree(std.boxes / "project")
        capsys.readouterr()

        assert _launch(None) == 1
        err = capsys.readouterr().err
        assert f"Rebuild it:  kanibako create {tmp_home / 'project'}" in err

        # The cure, run as printed.
        assert run_create(_create_args(tmp_home / "project")) == 0
        assert (std.boxes / "project" / "home").is_dir()
        assert _resolve_existing_box(std, config, None) is not None

    def test_shell_and_named_target_route_through_the_same_gate(
        self, config_file, tmp_home, credentials_dir, capsys, protected_canon
    ):
        """``kanibako shell`` and a bare-NAME target hit the one chokepoint too."""
        from kanibako.commands.box._parser import run_create

        _config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        assert remove_box_tree(std.boxes / "project")
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
        assert _unbuilt_box_error(proj, std) is None

    def test_connected_workset_box_is_not_refused_before_its_first_launch(
        self, config_file, tmp_home, credentials_dir
    ):
        """⚑ The reason the test is the BOX DIR and not the home tree.

        ``workset connect`` registers the box and creates its dir but NEVER
        seeds — the home is materialized by the FIRST launch.  Gating on
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
        assert _unbuilt_box_error(proj, std) is None

    def test_named_box_with_missing_dir_names_the_workset_cure(
        self, config_file, tmp_home, credentials_dir
    ):
        """``create`` refuses inside a workset member ("already initialized"), so
        the named-box cure is ``workset disconnect`` + ``workset connect``."""
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
        # A box METADATA tree: ``connect`` never seeds, so there is no skeleton here
        # today — but it is still a box tree, and the same rule applies to all of them.
        assert remove_box_tree(proj.metadata_path)

        msg = _unbuilt_box_error(proj, std)
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
        """A NAME-shaped miss offers BOTH branches, register first (I3/§D4a)."""
        msg = _no_box_error("myproj")
        assert msg == (
            "Error: no box at myproj.\n"
            "  A bare name is resolved through the registry, and nothing "
            "is registered under it.\n"
            "  If this is an unregistered standalone box, register it "
            "first:  kanibako box register <path-to-its-box-root>\n"
            "  Otherwise create a new box:  kanibako create myproj"
        )

    def test_name_shaped_miss_leads_with_the_register_cure(self):
        """⚑ ``create <name>`` must never be the ONLY path offered for a name.

        Since I3/§D4a an unregistered standalone box is real, on disk and
        launchable from its own directory, yet invisible to a bare name — and
        following ``create <name>`` from anywhere else mkdirs ``./<name>`` and
        puts a PRIMARY box in it, which is the harm this function's own contract
        names.  Both cures present, register FIRST.
        """
        msg = _no_box_error("ghostbox")
        assert "kanibako box register <path-to-its-box-root>" in msg
        assert "kanibako create ghostbox" in msg
        assert msg.index("box register") < msg.index("kanibako create ghostbox")

    def test_path_shaped_miss_keeps_the_one_liner(self, tmp_path, monkeypatch):
        """⚑ Only the NAME shape changed.  A spec that IS a path on disk has no
        registry story — ``create <path>`` is the right and only cure there.

        ⚑ The cure is now ``shlex.quote``d and the line carries no wrapping
        quotes.  This pin exists because BOTH halves are load-bearing: without
        the quoting a spec with a space pastes as two arguments, and with the
        wrapping quotes the tail a reader copies is not a command at all.  A
        plain path needs neither, so the text here is unchanged apart from the
        ``run:  `` separator.
        """
        monkeypatch.chdir(tmp_path)
        real = tmp_path / "realdir"
        real.mkdir()
        msg = _no_box_error(str(real))
        assert msg == (
            f"Error: no box at {real.resolve()}. To create a new box, "
            f"run:  kanibako create {shlex.quote(str(real))}"
        )
        assert "box register" not in msg

    @pytest.mark.parametrize("spec", [".gone", "gone.", "a b", "./gone", "a/b/c"])
    def test_missing_path_designation_gets_the_path_message(
        self, tmp_path, monkeypatch, spec,
    ):
        """A designation that cannot be a box name is a PATH, so a miss has no
        registry story, whether or not it exists on disk.

        ⚑ The spec is ``shlex.quote``d and unquoted around, so the expectation
        goes through ``shlex.quote`` too — the ``a b`` case is the one that
        distinguishes the quoting from a pass-through, and it is asserted as
        text here and as a real command paste in the pasteability tests below.
        """
        monkeypatch.chdir(tmp_path)
        msg = _no_box_error(spec)
        assert msg == (
            f"Error: no box at {(tmp_path / spec).resolve()}. To create a new box, "
            f"run:  kanibako create {shlex.quote(spec)}"
        )

    def test_missing_qualified_designation_gets_the_name_message(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        assert "A bare name is resolved through the registry" in _no_box_error("ws/gone")

    def test_no_spec_suggests_bare_create(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        msg = _no_box_error(None)
        assert msg == (
            f"Error: no box at {tmp_path}. To create a new box, "
            "run:  kanibako create"
        )

    def test_spec_with_a_space_is_one_argument_when_pasted(self, tmp_path, monkeypatch):
        """⚑ The SPACE case.  The defect is not the wording but the command a
        reader copies out of the line, so the assertion is on that command: the
        tail after ``run:  `` must split into exactly THREE tokens whose third
        is the spec verbatim, or the paste creates a box in a directory the user
        never named.
        """
        monkeypatch.chdir(tmp_path)
        spec = "a b"
        printed = _no_box_error(spec).rsplit("run:  ", 1)[1]
        assert printed == f"kanibako create {shlex.quote(spec)}"
        assert shlex.split(printed) == ["kanibako", "create", spec]

    def test_spec_with_a_quote_is_one_argument_when_pasted(self, tmp_path, monkeypatch):
        """⚑ The QUOTE case, and it is the one that catches a fix which only
        handles spaces.

        No space here, so a spaces-only cure leaves the embedded ``'`` opening a
        shell string that never closes: the paste does not create a box at the
        named directory, it runs whatever the shell recovers from the rest of
        the line.  ``shlex.quote`` closes and reopens the quote around it, so
        the spec survives as one argument.
        """
        monkeypatch.chdir(tmp_path)
        spec = "o'brien"
        printed = _no_box_error(spec).rsplit("run:  ", 1)[1]
        assert printed == f"kanibako create {shlex.quote(spec)}"
        assert shlex.split(printed) == ["kanibako", "create", spec]


# ---------------------------------------------------------------------------
# BROKEN STANDALONE: registered, but its ``box_data/`` is gone
# ---------------------------------------------------------------------------

class TestBrokenStandaloneNoBoxError:
    """A standalone box whose ``box_data/`` was deleted resolves NAMELESS, so the
    explicit-create gate answers it — and the generic message's copy-pasteable
    suggestion is built from the user's own spec.  For a bare standalone NAME that
    yields ``kanibako create <name>``, and running THAT mkdirs a directory
    literally named ``<name>`` in the CWD and puts a PRIMARY box in it.  These pin
    the replacement (Director ruling D-1/A2, 2026-08-06) and, negatively, the
    floor: the damaging form must never appear.
    """

    def _broken(self, config_file, tmp_home):
        """Create a REAL standalone box, then delete its ``box_data/``.

        Returns ``(std, box_name, root)``.  Deletion goes through
        ``remove_box_tree`` — the canon skeleton under ``box_data/home`` is
        root-owned + 555 where ``podman unshare`` works.
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.project import registry_store

        root = (tmp_home / "sa-proj").resolve()
        root.mkdir()
        # ⚑ ``--register`` (I3/§D4a): the branch under test is the message for a
        # REGISTERED standalone box, and registration at create is opt-in now.
        ns = argparse.Namespace(
            path=str(root), standalone=True, no_vault=True,
            name=None, image=None, agent=None, allow_home=False, register=True,
        )
        assert run_create(ns) == 0
        _config, std = _std(config_file)
        name = registry_store.standalone_name_for_root(std.registry, root)
        assert name, "standalone create must have registered a name"
        assert remove_box_tree(root / "box_data")
        return std, name, root

    def test_by_name_names_the_ruled_cure(
        self, config_file, tmp_home, credentials_dir,
    ):
        std, name, root = self._broken(config_file, tmp_home)
        msg = _no_box_error(name, std)
        # THE FLOOR, asserted negatively: the suggestion that mkdirs ./<name>.
        assert f"kanibako create {name}" not in msg
        assert "no box at" not in msg
        assert f"box '{name}' is registered as a standalone box at {root}" in msg
        assert f"its box data ({root / 'box_data'}) is gone" in msg
        assert "A launch will not rebuild it" in msg

    def test_cure_is_one_line_and_keeps_the_name(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``--name`` is load-bearing (it preserves the kuid / channel address)
        and both commands must be on ONE line so the pair cannot be
        half-followed — the ``rm`` is what FREES the name the ``create`` asks
        for.

        ⚑ ``--register`` is what makes ``--name`` load-bearing (I3/§D4a): create
        drops the name outright when it is not registering, so without the flag
        this cure would rebuild the box under a FRESH kuid and leave it out of
        the registry that named it."""
        std, name, root = self._broken(config_file, tmp_home)
        msg = _no_box_error(name, std)
        (cure,) = [ln for ln in msg.splitlines() if "Rebuild it:" in ln]
        assert (
            f"kanibako box rm {name} && kanibako create --standalone "
            f"--register --name {name} {root}"
        ) in cure
        assert "your workspace/ and vault/ are not touched" in msg

    def test_rule_breaking_standalone_name_is_not_looked_up(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        """A designation that breaks the box-name rule is a PATH, so a standalone
        box REGISTERED under such a name is not found by it."""
        from kanibako.project import registry_store

        _config, std = _std(config_file)
        root = (tmp_home / "legacy").resolve()
        root.mkdir()
        registry_store.register_standalone(std.registry, "bad name", root)
        monkeypatch.chdir(tmp_home)
        msg = _no_box_error("bad name", std)
        assert "registered as a standalone box" not in msg
        # ``bad name`` is a PATH, so its spec is quoted in the cure.
        assert msg == (
            f"Error: no box at {tmp_home.resolve() / 'bad name'}. To create a new "
            f"box, run:  kanibako create {shlex.quote('bad name')}"
        )

    def test_by_path_names_the_ruled_cure(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The ROOT-PATH form (``kanibako start <root>``) gets the SAME message.
        Its own milder defect was suggesting ``create <root>`` with no
        ``--standalone``, which would have made a PRIMARY box at that root."""
        std, name, root = self._broken(config_file, tmp_home)
        assert _no_box_error(str(root), std) == _no_box_error(name, std)
        assert "--standalone" in _no_box_error(str(root), std)

    def test_intact_standalone_never_takes_the_branch(
        self, config_file, tmp_home, credentials_dir,
    ):
        """⚑ The ``box_data/`` clause is load-bearing.  With a LIVE box tree on
        disk, ``box rm`` DOES park a deregistered entry and delete things — so the
        branch must not fire, and the suggestion must never be reachable in that
        state."""
        from kanibako.commands.box._parser import run_create
        from kanibako.project import registry_store

        root = (tmp_home / "sa-proj").resolve()
        root.mkdir()
        ns = argparse.Namespace(
            path=str(root), standalone=True, no_vault=True,
            name=None, image=None, agent=None, allow_home=False, register=True,
        )
        assert run_create(ns) == 0
        _config, std = _std(config_file)
        name = registry_store.standalone_name_for_root(std.registry, root)
        assert (root / "box_data").is_dir()

        msg = _no_box_error(name, std)
        assert "is registered as a standalone box" not in msg
        assert "kanibako box rm" not in msg
        assert msg == _no_box_error(name)

    def test_unregistered_name_is_unchanged(
        self, config_file, tmp_home, credentials_dir,
    ):
        """D-2 regression pin: a token naming NO standalone box does not take the
        registry-keyed branch — it gets the ordinary NAME-shaped message, ``std``
        or not.

        ⚑ That message is no longer the one-line ``create`` suggestion: the
        bare-name question this test once called "still-boarded" was decided by
        I3/§D4a, and the register cure now leads it (see
        ``TestNoBoxErrorMessage``).  What this pin still owns is the BRANCH — the
        ``_broken_standalone_error`` text must not appear for a token that names
        no registered box."""
        _config, std = _std(config_file)
        assert _no_box_error("ghostbox", std) == _no_box_error("ghostbox")
        assert "is registered as a standalone box" not in _no_box_error(
            "ghostbox", std,
        )
        assert _no_box_error(None, std) == _no_box_error(None)

    def test_launch_at_broken_standalone_prints_the_cure(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """End-to-end through the real launch gate — the seam the fix has to
        reach, not just the helper."""
        std, name, root = self._broken(config_file, tmp_home)
        capsys.readouterr()
        assert _launch(name) == 1
        err = capsys.readouterr().err
        assert f"kanibako create {name}" not in err
        assert (
            f"kanibako box rm {name} && kanibako create --standalone --register"
        ) in err


# ---------------------------------------------------------------------------
# Q106: a launch REFUSES a box whose workspace resolves through a null
# ``workset.workspaces`` — the workspace bind is mounted at every launch
# ---------------------------------------------------------------------------

def _null_workspaces(root):
    """Set ``workset.workspaces: null`` in *root*'s workset.yaml, keeping what is there."""
    from kanibako.settings.config_io import dump_doc, load_doc

    path = root / "workset.yaml"
    doc = dict(load_doc(path)) if path.is_file() else {}
    doc["workset"] = {**doc.get("workset", {}), "workspaces": None}
    dump_doc(path, doc)
    return path


def _tree(root):
    return sorted(root.rglob("*"))


def _assert_launch_passes_the_gate(target, monkeypatch, capsys):
    """Launch *target* with no container runtime: reaching that error means the gate passed."""
    from kanibako.commands import start
    from kanibako.errors import ContainerError

    def _no_runtime():
        raise ContainerError("no runtime (test)")

    monkeypatch.setattr(start, "ContainerRuntime", _no_runtime)
    capsys.readouterr()
    assert _launch(target) == 1
    assert "No container runtime found" in capsys.readouterr().err


class TestLaunchRefusesNullWorkspaceBind:
    """His Q106 answer: *"if a critical bind is <None>, launch should fail"*.

    Named in-tree and standalone boxes refuse on the probe, before anything is created;
    primary and an external named member resolve their workspace through no workset key.
    """

    def test_standalone_refuses_naming_the_key_and_file_and_creates_nothing(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.commands.box._parser import run_create
        from kanibako.errors import WorksetError

        root = (tmp_home / "sa-null").resolve()
        root.mkdir()
        ns = argparse.Namespace(
            path=str(root), standalone=True, no_vault=True,
            name=None, image=None, agent=None, allow_home=False, register=True,
        )
        assert run_create(ns) == 0
        settings = _null_workspaces(root)
        before = _tree(tmp_home)

        with pytest.raises(WorksetError) as exc:
            _launch(str(root))
        message = str(exc.value)
        assert "workset.workspaces" in message
        assert str(settings) in message
        assert "~/workspace" in message
        assert "Delete that line" in message
        assert _tree(tmp_home) == before
        assert not any(p.name == "None" for p in before)

    def test_named_in_tree_member_refuses_and_creates_nothing(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.errors import WorksetError
        from kanibako.project.workset import add_project, create_workset

        _config, std = _std(config_file)
        root = (tmp_home / "worksets" / "nullws").resolve()
        ws = create_workset("nullws", root, std)
        add_project(ws, "app", root / "workspaces" / "app", std)
        settings = _null_workspaces(root)
        before = _tree(tmp_home)

        # By path and by qualified name: the one chokepoint either way.
        for target in (str(root / "workspaces" / "app"), "nullws/app"):
            with pytest.raises(WorksetError) as exc:
                _launch(target)
            assert "Cannot launch box 'app'" in str(exc.value)
            assert str(settings) in str(exc.value)
        assert _tree(tmp_home) == before

    def test_named_external_member_is_untouched(
        self, config_file, tmp_home, credentials_dir, monkeypatch, capsys,
    ):
        from kanibako.commands.start import _refuse_null_workspace_bind
        from kanibako.project.workset import add_project, create_workset

        config, std = _std(config_file)
        root = (tmp_home / "worksets" / "extws").resolve()
        ws = create_workset("extws", root, std)
        source = (tmp_home / "ext-src").resolve()
        source.mkdir()
        add_project(ws, "ext", source, std)
        _null_workspaces(root)

        proj = _resolve_existing_box(std, config, str(source))
        assert proj is not None and proj.name == "ext"
        assert proj.project_path == source
        _refuse_null_workspace_bind(std, proj)  # no raise
        _assert_launch_passes_the_gate(str(source), monkeypatch, capsys)

    def test_primary_is_untouched(
        self, config_file, tmp_home, credentials_dir, monkeypatch, capsys,
    ):
        from kanibako.commands.box._parser import run_create
        from kanibako.commands.start import _refuse_null_workspace_bind

        config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        # Primary's ``workset.workspaces`` IS <None> (spec §2c); a file value changes nothing.
        _null_workspaces(std.primary_workset)

        proj = _resolve_existing_box(std, config, str(tmp_home / "project"))
        assert proj is not None and proj.name == "project"
        _refuse_null_workspace_bind(std, proj)  # no raise
        _assert_launch_passes_the_gate(str(tmp_home / "project"), monkeypatch, capsys)


class TestStandaloneNullWorkspaceHasNoPath:
    """Q106 review: under a null ``workset.workspaces`` a standalone box has NO workspace.

    ``meta.box.workspace`` is ``<None>``; no display names the default ``<root>/workspace``
    and nothing named ``None`` is created.  The launch refusal above stays the one refusal.
    """

    def _box(self, tmp_home):
        import shutil

        from kanibako.commands.box._parser import run_create

        root = (tmp_home / "sa-null").resolve()
        root.mkdir()
        ns = argparse.Namespace(
            path=str(root), standalone=True, no_vault=True,
            name=None, image=None, agent=None, allow_home=False, register=True,
        )
        assert run_create(ns) == 0
        _null_workspaces(root)
        # The create made the default folder before the null; without it, any path to it
        # in the output below can only be a fabrication.
        shutil.rmtree(root / "workspace")
        return root

    def _cli(self, argv, capsys):
        from kanibako import cli

        capsys.readouterr()
        try:
            cli.main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, capsys.readouterr()

    def test_resolve_and_floor_carry_none(self, config_file, tmp_home, credentials_dir):
        from kanibako.settings.paths import resolve_standalone_project
        from kanibako.settings.settings_launch import (
            _box_inputs, _workset_workspaces_floor_value,
        )

        root = self._box(tmp_home)
        config, std = _std(config_file)
        proj = resolve_standalone_project(std, config, str(root), initialize=True)
        assert proj.project_path is None
        assert _workset_workspaces_floor_value(
            "standalone", str(root), early=_early_scope(std, BoxMode.standalone),
        ) is None
        inputs = _box_inputs(std=std, proj=proj, agent_name="", system_path=None)
        assert inputs.meta_identity is not None
        assert inputs.meta_identity["meta.box.workspace"] is None
        assert not (root / "workspace").exists()
        assert not any(p.name == "None" for p in _tree(tmp_home))

    def test_displays_show_none_never_the_default_folder(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = self._box(tmp_home)
        fabricated = str(root / "workspace")

        code, out = self._cli(["box", "show", str(root), "--effective"], capsys)
        assert code == 0, out.err
        assert "box.bindings" in out.out  # the resolve ran
        assert fabricated not in out.out + out.err
        assert "= None" not in out.out

        code, out = self._cli(["box", "info", str(root)], capsys)
        assert code == 0, out.err
        assert re.search(r"^\s*Project:\s+<None>$", out.out, re.MULTILINE), out.out
        assert fabricated not in out.out + out.err

        # ``meta.*`` is derived per launch and ``box get`` refuses it for EVERY box.
        code, out = self._cli(["box", "get", str(root), "meta.box.workspace"], capsys)
        assert code == 1
        assert "cannot be read here" in out.err
        assert fabricated not in out.out + out.err

        # The display verbs may seed their own state; none of it is the default folder.
        assert not (root / "workspace").exists()
        assert not any(p.name == "None" for p in _tree(tmp_home))

    def test_lifecycle_ops_refuse_naming_the_key(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.commands.box._lifecycle import resolve_lifecycle_target
        from kanibako.errors import WorksetError

        root = self._box(tmp_home)
        config, std = _std(config_file)
        with pytest.raises(WorksetError) as exc:
            resolve_lifecycle_target(str(root), std, config)
        assert "workset.workspaces" in str(exc.value)
        assert str(root / "workset.yaml") in str(exc.value)


class TestNamedInTreeNullWorkspaceHasNoPath:
    """A named IN-TREE member under a null ``workset.workspaces`` has no workspace either.

    ``@workset.workspaces/<name>`` is a whole-value ``<None>`` (§0), so the launch refuses
    and the two display faces must not name the registered folder: it is the RECORD, not
    the resolution.  An external member and a primary box resolve through no workset key
    and keep their own path — the boundary this must not cross.
    """

    def _member(self, tmp_home, config_file):
        from kanibako.project.workset import add_project, create_workset

        config, std = _std(config_file)
        root = (tmp_home / "worksets" / "nullws").resolve()
        ws = create_workset("nullws", root, std)
        add_project(ws, "app", root / "workspaces" / "app", std)
        settings = _null_workspaces(root)
        return config, std, root, settings

    def _cli(self, argv, capsys):
        from kanibako import cli

        capsys.readouterr()
        try:
            cli.main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, capsys.readouterr()

    def test_resolve_and_floor_carry_none(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.settings.settings_launch import _box_inputs

        config, std, _root, _settings = self._member(tmp_home, config_file)
        proj = _resolve_existing_box(std, config, "nullws/app")
        assert proj is not None and proj.name == "app"
        assert proj.project_path is None
        inputs = _box_inputs(std=std, proj=proj, agent_name="", system_path=None)
        assert inputs.meta_identity is not None
        assert inputs.meta_identity["meta.box.workspace"] is None
        # The RECORD of where the files are survives the null: the hash is the box's
        # identity, and nulling must not rename its container or drop its membership.
        assert proj.project_hash
        assert not any(p.name == "None" for p in _tree(tmp_home))

    def test_displays_show_none_never_the_registered_folder(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        self._member(tmp_home, config_file)
        recorded = str(tmp_home / "worksets" / "nullws" / "workspaces" / "app")

        code, out = self._cli(["box", "show", "nullws/app", "--effective"], capsys)
        assert code == 0, out.err
        assert "box.bindings" in out.out  # the resolve ran
        assert recorded not in out.out + out.err
        assert "= None" not in out.out

        code, out = self._cli(["box", "info", "nullws/app"], capsys)
        assert code == 0, out.err
        assert re.search(r"^\s*Project:\s+<None>$", out.out, re.MULTILINE), out.out
        assert recorded not in out.out + out.err
        assert not any(p.name == "None" for p in _tree(tmp_home))

    def test_the_displays_agree_with_the_launch_refusal(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Both faces and the launch answer from the one resolved value."""
        from kanibako.errors import WorksetError

        self._member(tmp_home, config_file)

        with pytest.raises(WorksetError) as exc:
            _launch("nullws/app")
        message = str(exc.value)
        assert "workset.workspaces" in message

        code, out = self._cli(["box", "info", "nullws/app"], capsys)
        assert code == 0, out.err
        # The launch says the box has no workspace to mount; the display must not contradict.
        assert re.search(r"^\s*Project:\s+<None>$", out.out, re.MULTILINE), out.out

        code, out = self._cli(["box", "show", "nullws/app", "--effective"], capsys)
        assert code == 0, out.err
        assert "~/workspace]" not in out.out

    def test_lifecycle_state_carries_the_recorded_workspace(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A lifecycle op works on the RECORD — where the files are — so it still resolves.

        ⚑ The null governs the RESOLVED ``meta.box.workspace``, which is what the launch
        mounts; a move or a re-point needs the leaf on disk, and refusing at resolve time
        would strand a box that can legitimately move OUT to a directory outside the workset.
        The op-level refusal is what stops a move that would land a new in-tree leaf.
        """
        from kanibako.commands.box._lifecycle import resolve_lifecycle_target
        from kanibako.project.workset import load_workset

        config, std, root, _settings = self._member(tmp_home, config_file)
        recorded = root / "workspaces" / "app"
        state = resolve_lifecycle_target("nullws/app", std, config)
        assert state.workspace_path == recorded
        assert state.mode.value == "named"
        # The box is still a member, so the op can re-point or release it.
        assert [p.name for p in load_workset(root, "nullws", early_system=std.early_system).projects] == ["app"]

    def test_an_external_member_keeps_its_own_workspace(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The boundary: a recorded workspace OUTSIDE the root resolves through no key."""
        from kanibako.project.workset import add_project, create_workset

        config, std = _std(config_file)
        root = (tmp_home / "worksets" / "extws").resolve()
        ws = create_workset("extws", root, std)
        source = (tmp_home / "ext-src").resolve()
        source.mkdir()
        add_project(ws, "ext", source, std)
        _null_workspaces(root)

        proj = _resolve_existing_box(std, config, str(source))
        assert proj is not None and proj.name == "ext"
        assert proj.project_path == source

    def test_a_primary_box_keeps_its_project_dir(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The boundary: primary's workspace is the project dir, not a workset key."""
        from kanibako.commands.box._parser import run_create

        config, std = _std(config_file)
        assert run_create(_create_args(tmp_home / "project")) == 0
        _null_workspaces(std.primary_workset)

        proj = _resolve_existing_box(std, config, str(tmp_home / "project"))
        assert proj is not None and proj.name == "project"
        assert proj.project_path == (tmp_home / "project").resolve()


class TestUnregisteredStandaloneRegisterHint:
    """The hint an unregistered standalone ``create`` prints is a RUNNABLE cure.

    Registration at create is opt-in for a standalone box (§D4a), so this line is
    the only thing that tells the user how to adopt the box they just made.  Its
    operand is the box ROOT, and a root is not one shell word whenever it holds a
    space — pasted unquoted, ``register`` rejects the words after the space as
    extra arguments (rc 2, "unrecognized arguments") and adopts nothing.
    """

    @staticmethod
    def _spaced_root(tmp_home):
        # ⚑ The space sits in the PARENT so what breaks is the cure line's quoting
        # alone; a spaced LEAF would make the box NAME illegal and fail for a
        # second, unrelated reason.
        root = tmp_home / "sa box" / "sa"
        root.mkdir(parents=True)
        return root

    def test_the_hint_at_a_spaced_root_registers_that_root(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Pinned as a RUN, not a string match: only the run says the box at the
        SPACED root is the one that got adopted."""
        from kanibako.cli import build_parser
        from kanibako.commands.box._parser import run_create
        from kanibako.project import registry_store

        root = self._spaced_root(tmp_home)
        ns = argparse.Namespace(
            path=str(root), standalone=True, no_vault=True,
            name=None, image=None, agent=None, allow_home=False, register=False,
        )
        capsys.readouterr()
        assert run_create(ns) == 0
        out = capsys.readouterr().out

        _config, std = _std(config_file)
        assert registry_store.standalone_name_for_root(std.registry, root) is None

        found = re.search(r"run '(.*)' to address it by name from elsewhere\.", out, re.S)
        assert found, f"no registration hint in {out!r}"
        argv = shlex.split(found.group(1))
        assert argv[:3] == ["kanibako", "box", "register"]
        assert argv[3:] == [str(root)], "the root must arrive as ONE operand"

        parsed = build_parser().parse_args(argv[1:])
        assert parsed.func(parsed) == 0
        assert registry_store.standalone_name_for_root(std.registry, root)


class TestCreateRefusesPerOwnerBeforeTheDir:
    """A primary ``create <path>`` refused by an inherited per-owner key leaves no ``<path>``."""

    @pytest.mark.parametrize("key", ["registry", "canon", "channels.chat"])
    def test_refused_and_path_absent(self, config_file, tmp_home, credentials_dir, capsys, key):
        from kanibako.cli import main
        from kanibako.settings.config_io import dump_doc

        _config, std = _std(config_file)
        head, _, leaf = key.partition(".")
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        dump_doc(std.settings, {"workset": {head: {leaf: "/srv/x"} if leaf else "/srv/x"}})
        target = tmp_home / "new"
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc:
            main(["create", str(target)])
        assert exc.value.code == 1
        assert f"workset.{key}" in capsys.readouterr().err
        assert not target.exists()


# ⚑ ``workset.registry`` and ``workset.template`` are the per-owner keys a standalone
# ``create`` does NOT resolve — ``registry`` indexes boxes at the SYSTEM registry, and the
# standalone canon stamp reads ``workset.canon`` alone.  A pre-mkdir guard that refused
# EVERY per-owner key would refuse both of these valid creates.
_STANDALONE_ACCEPTED_KEYS = ["registry", "template"]


class TestStandaloneCreatePerOwnerBeforeTheDir:
    """A standalone ``create <path>`` refuses its own per-owner keys, and only those."""

    @staticmethod
    def _system_workset_key(config_file, key, value="/srv/x"):
        _config, std = _std(config_file)
        head, _, leaf = key.partition(".")
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        from kanibako.settings.config_io import dump_doc
        dump_doc(std.settings, {"workset": {head: {leaf: value} if leaf else value}})
        return std

    @pytest.mark.parametrize(
        "key", ["canon", "workspaces", "channelroot", "channels.chat"],
    )
    def test_refused_and_path_absent(self, config_file, tmp_home, credentials_dir, capsys, key):
        """The refusal lands before the mkdir, so no ``<path>`` stands behind it."""
        from kanibako.cli import main

        self._system_workset_key(config_file, key)
        target = tmp_home / "new"
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc:
            main(["create", "--standalone", str(target)])
        assert exc.value.code == 1
        assert f"workset.{key}" in capsys.readouterr().err
        assert not target.exists(), f"{key} refused the create but left {target} behind"

    @pytest.mark.parametrize("key", _STANDALONE_ACCEPTED_KEYS)
    def test_inherited_value_the_standalone_arm_accepts_still_creates(
        self, config_file, tmp_home, credentials_dir, capsys, key,
    ):
        """An inherited value the standalone arm accepts must not block the create."""
        from kanibako.cli import main

        self._system_workset_key(config_file, key)
        target = tmp_home / "new"
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc:
            main(["create", "--standalone", str(target)])
        assert exc.value.code == 0, capsys.readouterr().err
        assert (target / "box_data").is_dir(), f"{key} blocked a standalone create"

    @pytest.mark.parametrize("key", ["channelroot", "channels.chat", "channels.mailboxes"])
    def test_refused_a_pre_existing_target_is_left_as_the_user_left_it(
        self, config_file, tmp_home, credentials_dir, capsys, key,
    ):
        """The refusal lands before ANY write, so a target that ALREADY exists is untouched.

        ⛔ A guard that runs only under ``if not target.exists()`` never reaches this case:
        the create then materializes a whole box beside ``user.txt`` and refuses afterwards.
        """
        from kanibako.cli import main

        self._system_workset_key(config_file, key)
        target = tmp_home / "mine"
        target.mkdir()
        (target / "user.txt").write_text("the user's own file\n")
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc:
            main(["create", "--standalone", str(target)])
        assert exc.value.code == 1
        assert f"workset.{key}" in capsys.readouterr().err
        assert sorted(p.name for p in target.iterdir()) == ["user.txt"], (
            f"{key} refused the create but wrote into {target}"
        )

"""Interrupted-create recovery via the lifecycle journal (J1).

A create is write-ahead journaled: ``write-entry -> seed -> register ->
clear-entry``.  A crash before the entry is cleared leaves a pending ``create``
journal entry; the next ``create`` (or auto-create-at-launch) DETECTS it and
COMPLETES the create by replay (seed create-if-absent -> register-if-absent ->
clear-entry).  The HARD INVARIANT — ``registered ==> no pending entry`` at rest —
holds for PRIMARY and STANDALONE, both for an unregistered interrupted box and a
register->clear-window crash (registered + stale entry).

This SUPERSEDES the B3 ``.seeding`` file-marker suite.  The journal create-entry
helpers (``_write_create_entry`` / ``_clear_create_entry`` / ``_pending_create_
entry``) replace the marker helpers; the recovery tests are NON-VACUOUS —
``rc == 0`` and the post-recovery state are asserted UNCONDITIONALLY.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from kanibako.launch import journal
from kanibako.commands.start import (
    _box_journal_key,
    _clear_create_entry,
    _pending_create_entry,
    _register_new_box,
    _write_create_entry,
)
from kanibako.settings.paths import BoxMode, load_primary_boxes


def _primary_names(std):
    """Return the PRIMARY box membership (the sole store since projects retired)."""
    return load_primary_boxes(std.primary_workset)


# ---------------------------------------------------------------------------
# Journal create-entry helpers (real paths) — the marker-helper replacements
# ---------------------------------------------------------------------------

class TestCreateEntryHelpers:
    def _proj_primary(self, std, box_dir: Path):
        from types import SimpleNamespace
        # shell_path always ends in home/; its parent is the box dir (the key).
        return SimpleNamespace(
            shell_path=box_dir / "home", mode=BoxMode.primary,
            name="myapp", project_path=Path("/ws/myapp"), group=None,
        )

    def test_journal_key_is_shell_parent(self, tmp_path: Path) -> None:
        box = tmp_path / "boxes" / "myapp"
        proj = self._proj_primary(None, box)
        assert _box_journal_key(proj) == str(box)

    def test_write_then_pending_then_clear(self, tmp_path: Path) -> None:
        from types import SimpleNamespace
        std = SimpleNamespace(journal=tmp_path / "journal.yaml")
        box = tmp_path / "boxes" / "myapp"
        proj = self._proj_primary(std, box)

        assert _pending_create_entry(std, proj) is None
        _write_create_entry(std, proj)
        entry = _pending_create_entry(std, proj)
        assert entry is not None
        assert entry["op"] == "create"
        assert entry["name"] == "myapp"
        assert entry["mode"] == "primary"
        _clear_create_entry(std, proj)
        assert _pending_create_entry(std, proj) is None

    def test_write_records_workset_for_named(self, tmp_path: Path) -> None:
        from types import SimpleNamespace
        std = SimpleNamespace(journal=tmp_path / "journal.yaml")
        box = tmp_path / "boxes" / "myapp"
        proj = SimpleNamespace(
            shell_path=box / "home", mode=BoxMode.named, name="myapp",
            project_path=Path("/ws/myapp"),
            group=SimpleNamespace(name="myws"),
        )
        _write_create_entry(std, proj)
        assert _pending_create_entry(std, proj)["workset"] == "myws"


# ---------------------------------------------------------------------------
# _register_new_box (mode-aware, idempotent)
# ---------------------------------------------------------------------------

class TestRegisterNewBox:
    def test_primary_registers_name_path(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        registry = tmp_path / "registry.yaml"
        primary = tmp_path / "primary_workset"
        std = SimpleNamespace(registry=registry, primary_workset=primary)
        proj = SimpleNamespace(
            mode=BoxMode.primary, name="myapp",
            project_path=tmp_path / "ws" / "myapp",
        )
        _register_new_box(std, proj)
        assert load_primary_boxes(primary)["myapp"] == str(
            tmp_path / "ws" / "myapp"
        )

    def test_primary_idempotent_same_mapping(self, tmp_path: Path) -> None:
        """Recovery re-entry on an already-registered box is a no-op (no raise)."""
        from types import SimpleNamespace
        registry = tmp_path / "registry.yaml"
        primary = tmp_path / "primary_workset"
        std = SimpleNamespace(registry=registry, primary_workset=primary)
        proj = SimpleNamespace(
            mode=BoxMode.primary, name="myapp",
            project_path=tmp_path / "ws" / "myapp",
        )
        _register_new_box(std, proj)
        _register_new_box(std, proj)  # must not raise.

    def test_standalone_registers_root_idempotent(self, tmp_path: Path) -> None:
        from types import SimpleNamespace
        from kanibako import registry_store

        registry = tmp_path / "registry.yaml"
        std = SimpleNamespace(registry=registry)
        root = tmp_path / "standalone"
        root.mkdir()
        proj = SimpleNamespace(
            mode=BoxMode.standalone, name="ab12_proj", metadata_path=root,
        )
        _register_new_box(std, proj)
        assert registry_store.load_standalone(registry)["ab12_proj"] == str(root)
        _register_new_box(std, proj)  # idempotent.
        assert registry_store.load_standalone(registry)["ab12_proj"] == str(root)

    def test_named_is_noop(self, tmp_path: Path) -> None:
        """NAMED boxes carry no deferred registration on create."""
        from types import SimpleNamespace

        registry = tmp_path / "registry.yaml"
        primary = tmp_path / "primary_workset"
        std = SimpleNamespace(registry=registry, primary_workset=primary)
        proj = SimpleNamespace(
            mode=BoxMode.named, name="proj",
            project_path=tmp_path / "ws" / "workspaces" / "proj",
        )
        _register_new_box(std, proj)  # no-op.
        assert load_primary_boxes(primary) == {}


# ---------------------------------------------------------------------------
# Resolver register=False (deferred registration)
# ---------------------------------------------------------------------------

class TestResolverRegisterFalse:
    def test_primary_register_false_leaves_registry_untouched(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
            register=False,
        )
        assert proj.is_new
        assert proj.name == "project"
        assert _primary_names(std) == {}
        assert proj.shell_path.is_dir()

    def test_primary_register_true_is_default(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )
        assert _primary_names(std)[proj.name] == project_dir

    def test_standalone_register_false_not_in_standalone_section(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.settings.config import load_config
        from kanibako import registry_store
        from kanibako.settings.paths import load_std_paths, resolve_standalone_project

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa"
        root.mkdir()
        proj = resolve_standalone_project(
            std, config, str(root), initialize=True, register=False,
        )
        assert proj.is_new
        assert proj.name
        assert registry_store.load_standalone(std.registry) == {}


class TestDeferredCreateReservesDir:
    def test_second_create_does_not_grab_half_built_dir(
        self, config_file, tmp_home, credentials_dir
    ):
        """A first create deferred-registration leaves boxes/<name>/ on disk but
        unregistered; a SECOND create for a different workspace with the same
        leaf must pick a DIFFERENT name (not seed over the half-built box)."""
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)

        first = tmp_home / "project"
        proj1 = resolve_project(
            std, config, project_dir=str(first), initialize=True,
            register=False,
        )
        assert proj1.name == "project"
        assert (std.boxes / "project").is_dir()

        second = tmp_home / "elsewhere" / "project"
        second.mkdir(parents=True)
        proj2 = resolve_project(
            std, config, project_dir=str(second), initialize=True,
            register=False,
        )
        assert proj2.name == "project2"


# ---------------------------------------------------------------------------
# run_create lifecycle: clean create writes-then-clears the entry (invariant)
# ---------------------------------------------------------------------------

def _create_args(path, **over):
    ns = argparse.Namespace(
        path=str(path), standalone=False, no_vault=True,
        name=None, image=None, agent=None, allow_home=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


class TestRunCreatePersonaGate:
    """`box create` for an UNLOADABLE persona: a TRUE PRE-FLIGHT (F5, Director
    ruling 2026-07-03).  The load-or-error gate runs on a NON-materialising probe
    BEFORE the box dir is created (and before the write-ahead journal entry, ruling
    #3), so a failed create leaves NOTHING behind: no box dir / settings.yaml, no
    journal entry, no seed, and the registry untouched.  Real filesystem — these
    are fs-level, not mock-level, assertions.

    UNMASKED: nothing patches ``_resolve_box_launch_decisions``.  The verdict runs
    the REAL launch-decision resolve on the pick_name()'d probe; reverting the
    probe naming (``_name_new_box_probe``) makes ``box_channel_addresses`` raise
    "box has no name" BEFORE the verdict → this test would ERROR instead of rc==1
    (the F5/F7 mutation proof)."""

    def test_unloadable_persona_create_no_box_no_entry_no_seed(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        from unittest.mock import MagicMock

        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths
        from kanibako.launch import journal

        # No persona host dir under XDG_CONFIG_HOME (tmp_home/config) → the
        # explicit persona 'navigator+claude' is unrecognised AND unadoptable, so
        # the create verdict is a hard error.  claude IS an installed harness in
        # the test env, so the gate is genuinely reached (not skipped as no-agent).
        seed_called = {"v": False}

        def spy_seed(std, config, proj, **kw):  # must NEVER run.
            seed_called["v"] = True

        monkeypatch.setattr("kanibako.commands.start.seed_new_box", spy_seed)
        # The journal write-entry must be UNREACHED (guard precedes it, ruling #3).
        m_write_entry = MagicMock()
        monkeypatch.setattr(
            "kanibako.commands.start._write_create_entry", m_write_entry
        )

        rc = run_create(
            _create_args(tmp_home / "project", agent="navigator+claude")
        )
        assert rc == 1

        config = load_config(config_file)
        std = load_std_paths(config)
        # TRUE PRE-FLIGHT: the box was NEVER materialised — no box dir /
        # settings.yaml (the workspace dir tmp_home/project the user asked to
        # create in is theirs; the BOX under std.boxes is what must be absent).
        assert not std.boxes.exists() or not any(std.boxes.iterdir())
        # Guard ran BEFORE the journal entry: no entry written, nothing seeded,
        # registry untouched (fs-level).
        m_write_entry.assert_not_called()
        assert seed_called["v"] is False
        assert journal.read_journal(std.journal) == {}
        assert _primary_names(std) == {}
        # No agent-store artifact was materialised for the persona node.
        assert not (std.agents / "navigator℘claude").exists()


class TestRunCreateJournalLifecycle:
    def test_clean_create_leaves_no_entry_and_registers(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """A clean create ends registered AND with no pending entry (invariant
        registered ==> no pending entry).  The entry is present DURING the seed
        (write-ahead ordering)."""
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        seen = {}

        def fake_seed(std, config, proj, **kw):
            seen["pending_during_seed"] = (
                _pending_create_entry(std, proj) is not None
            )

        monkeypatch.setattr("kanibako.commands.start.seed_new_box", fake_seed)

        rc = run_create(_create_args(tmp_home / "project"))
        assert rc == 0

        config = load_config(config_file)
        std = load_std_paths(config)
        # Write-ahead: entry present during seed, gone at rest.
        assert seen["pending_during_seed"] is True
        box_key = str(std.boxes / "project")
        assert journal.pending_create(std.journal, box_key) is None
        assert "project" in _primary_names(std)

    def test_genuine_collision_in_register_leaves_entry(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """If the deferred register raises a GENUINE collision, the entry is LEFT
        (box incomplete) and the error propagates — run_create does NOT swallow
        it or clear the entry."""
        from kanibako.commands.box import _parser
        from kanibako.settings.config import load_config
        from kanibako.errors import ProjectError
        from kanibako.settings.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)

        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )

        def boom(std, proj, **kw):
            raise ProjectError("simulated registry collision")

        monkeypatch.setattr("kanibako.commands.start._register_new_box", boom)

        with pytest.raises(ProjectError):
            _parser.run_create(_create_args(tmp_home / "project"))

        # Entry LEFT (box incomplete) — recovery will resume it.
        box_key = str(std.boxes / "project")
        assert journal.pending_create(std.journal, box_key) is not None

    def test_already_initialized_without_entry_errors(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """A genuinely complete box (registered, NO pending entry) re-created
        errors 'already initialized' (rc=1) — recovery only triggers on an
        actual pending entry, not on every existing box."""
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )
        rc = run_create(_create_args(tmp_home / "project"))
        assert rc == 0

        # Sanity: no pending entry at rest.
        config = load_config(config_file)
        std = load_std_paths(config)
        assert journal.read_journal(std.journal) == {}

        rc2 = run_create(_create_args(tmp_home / "project"))
        assert rc2 == 1


class TestRunCreateCrossKindName:
    """`box create --name <workset-name>` (per-kind name policy, Jei 2026-07-08).

    Box and workset names are SEPARATE namespaces, but a bare name shared across
    kinds resolves to the box (shadowing the workset).  An explicit --name that
    collides with a WORKSET name refuses UNLESS --force; the refusal is an
    up-front CLI check (clean rc=1) BEFORE the box dir + seed materialize.
    """

    def test_name_collides_with_workset_refuses_cleanly(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.names import register_name
        from kanibako.settings.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        register_name(std.registry, "common", str(tmp_home / "ws"), section="worksets")

        seed_called = {"v": False}
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: seed_called.__setitem__("v", True),
        )

        rc = run_create(_create_args(tmp_home / "project", name="common"))
        assert rc == 1
        # Refused up front: nothing materialized or seeded.
        assert seed_called["v"] is False
        assert not std.boxes.exists() or not any(std.boxes.iterdir())
        assert _primary_names(std) == {}

    def test_name_collides_with_workset_force_creates(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.names import register_name
        from kanibako.settings.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        register_name(std.registry, "common", str(tmp_home / "ws"), section="worksets")

        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )

        rc = run_create(_create_args(tmp_home / "project", name="common", force=True))
        assert rc == 0
        # --force let the box take the shadowed name → registered in membership.
        assert "common" in _primary_names(std)

    def test_name_collides_with_primary_box_refuses_even_with_force(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """SAME-KIND: a --name already owned by another PRIMARY box refuses even
        with --force (per-kind uniqueness is unconditional)."""
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, register_primary_box_name

        config = load_config(config_file)
        std = load_std_paths(config)
        register_primary_box_name(
            std.primary_workset, std.registry, "common", str(tmp_home / "other"),
        )

        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )

        rc = run_create(_create_args(tmp_home / "project", name="common", force=True))
        assert rc == 1


# ---------------------------------------------------------------------------
# IMPORT/CONNECT never write a create entry (they register-only; do NOT seed)
# ---------------------------------------------------------------------------

class TestImportConnectNoCreateEntry:
    """The create journal entry is EXCLUSIVELY a create/seed-path signal.
    IMPORT/CONNECT and the convert/duplicate/move lifecycle flows register-only
    (the box was seeded where it was created) — a create entry on them would
    wrongly trigger re-seed.  Structural guard: those modules must never
    reference the create-entry or seed helpers."""

    @pytest.mark.parametrize(
        "module",
        [
            "kanibako.commands.box._lifecycle",
            "kanibako.import_reconcile",
            "kanibako.commands.workset_cmd",
        ],
    )
    def test_module_has_no_create_entry_or_seed_calls(self, module: str) -> None:
        import importlib
        import inspect

        src = inspect.getsource(importlib.import_module(module))
        for forbidden in (
            "_write_create_entry",
            "_pending_create_entry",
            "seed_new_box",
            "_seed_box_home",
        ):
            assert forbidden not in src, (
                f"{module} must not reference {forbidden}: import/connect/"
                f"lifecycle flows register-only and must NEVER seed or journal a "
                f"create."
            )


# ---------------------------------------------------------------------------
# RECOVERY (NON-VACUOUS) — re-create completes an interrupted create
# ---------------------------------------------------------------------------

def _simulate_interrupted_create(
    std, config, *, standalone, path, register_box
):
    """Faithfully simulate a create crash: resolve register=False, write the
    create entry, seed (create-if-absent), optionally register (the
    register->clear-window crash), then STOP before clearing the entry.

    Returns the resolved ``proj`` (the on-disk half-built box) so the test can
    plant a user edit before recovery.  ``register_box`` toggles the two crash
    points: False = crash before register (unregistered + entry); True = crash
    after register, before clear (registered + stale entry).
    """
    from kanibako.settings.paths import resolve_project, resolve_standalone_project

    if standalone:
        proj = resolve_standalone_project(
            std, config, str(path), initialize=True, register=False,
        )
    else:
        proj = resolve_project(
            std, config, project_dir=str(path), initialize=True,
            register=False,
        )
    _write_create_entry(std, proj)
    # "seed" — minimal create-if-absent: ensure the home exists (the resolver
    # already made it) so a user edit can land there.
    Path(proj.shell_path).mkdir(parents=True, exist_ok=True)
    if register_box:
        _register_new_box(std, proj)
    # CRASH: entry left, NOT cleared.
    return proj


class TestRecoveryPrimary:
    @pytest.mark.parametrize("register_box", [False, True])
    def test_recovery_completes_and_clears_entry(
        self, config_file, tmp_home, credentials_dir, monkeypatch, register_box
    ):
        """PRIMARY interrupted create (unregistered AND registered+stale-entry):
        a re-run completes — registered exactly once, entry GONE, USER HOME EDIT
        SURVIVES.  Asserted UNCONDITIONALLY (no rc-gated skip)."""
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        path = tmp_home / "project"

        proj = _simulate_interrupted_create(
            std, config, standalone=False, path=path, register_box=register_box,
        )
        box_key = str(std.boxes / "project")
        # A user edit lands in the home AFTER the interrupted seed.
        user_file = Path(proj.shell_path) / "USER_EDIT.txt"
        user_file.write_text("precious")
        # Crash state confirmed: entry present.
        assert journal.pending_create(std.journal, box_key) is not None
        if not register_box:
            assert _primary_names(std) == {}

        # Recovery: re-run create (seed neutralized — assert the
        # register+entry-clear completion + home untouched).
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )
        rc = run_create(_create_args(path))

        config = load_config(config_file)
        std = load_std_paths(config)
        # UNCONDITIONAL recovery asserts.
        assert rc == 0
        assert "project" in _primary_names(std)
        # Registered EXACTLY once.
        assert list(_primary_names(std)).count("project") == 1
        assert journal.pending_create(std.journal, box_key) is None
        # Invariant restored: registered ==> no pending entry.
        assert journal.read_journal(std.journal) == {}
        # User edit SURVIVED.
        assert user_file.read_text() == "precious"


class TestRecoveryStandalone:
    @pytest.mark.parametrize("register_box", [False, True])
    def test_recovery_completes_and_clears_entry(
        self, config_file, tmp_home, credentials_dir, monkeypatch, register_box
    ):
        """STANDALONE interrupted create (unregistered AND registered+stale-entry):
        a re-run completes — registered exactly once, entry GONE, USER HOME EDIT
        SURVIVES.  Asserted UNCONDITIONALLY."""
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako import registry_store
        from kanibako.settings.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa"
        root.mkdir()

        proj = _simulate_interrupted_create(
            std, config, standalone=True, path=root, register_box=register_box,
        )
        box_key = _box_journal_key(proj)
        box_name = proj.name
        user_file = Path(proj.shell_path) / "USER_EDIT.txt"
        user_file.write_text("precious")
        assert journal.pending_create(std.journal, box_key) is not None
        if not register_box:
            assert registry_store.load_standalone(std.registry) == {}

        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )
        rc = run_create(_create_args(root, standalone=True))

        config = load_config(config_file)
        std = load_std_paths(config)
        assert rc == 0
        registered = registry_store.load_standalone(std.registry)
        assert box_name in registered
        assert registered[box_name] == str(root)
        # Registered exactly once (one standalone entry for this box).
        assert list(registered).count(box_name) == 1
        assert journal.pending_create(std.journal, box_key) is None
        assert journal.read_journal(std.journal) == {}
        assert user_file.read_text() == "precious"


# ---------------------------------------------------------------------------
# box lifecycle I4: conflict-safe create (DATA-LOSS guard on `create --name X`)
# ---------------------------------------------------------------------------

class TestConflictSafeCreate:
    """`create --name X` REFUSES to reuse an existing box home (``std.boxes/X``).

    Closes the DATA-LOSS window (box-lifecycle I4): before this guard a
    ``create --name dup`` after ``rm dup`` merged into the deregistered box's
    retained home, so a later ``rm dup --purge`` deleted the live box's data.
    """

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        # The home seed runs AFTER the guard (and never for a refused create) — stub
        # it so these are fast + fs-deterministic, matching the sibling suites.
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )

    def _std(self, config_file):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths
        return load_std_paths(load_config(config_file))

    def test_repro_create_over_deregistered_refused_no_data_loss(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """⚑ THE HAZARD REPRO: rm dup (deregister) -> create --name dup <NEW path>
        is REFUSED; the deregistered box's home + secrets are intact (no merge)."""
        from kanibako import registry_store
        from kanibako.commands.box._parser import run_create, run_rm

        orig = tmp_home / "orig"
        orig.mkdir()
        assert run_create(_create_args(orig, name="dup")) == 0
        std = self._std(config_file)
        home_dir = std.boxes / "dup"
        (home_dir / "home").mkdir(parents=True, exist_ok=True)
        sentinel = home_dir / "home" / "SECRET.txt"
        sentinel.write_text("old-box-credentials")

        assert run_rm(
            argparse.Namespace(target="dup", purge=False, force=False)
        ) == 0
        assert registry_store.lookup_deregistered(std.registry, "dup") is not None
        capsys.readouterr()

        # create --name dup at a NEW path -> REFUSED (the reuse hole is closed).
        newp = tmp_home / "newp"
        newp.mkdir()
        rc = run_create(_create_args(newp, name="dup"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "deregistered" in err
        assert "box register dup" in err
        assert "box rm dup --purge" in err

        # NO data loss: the deregistered home + sentinel untouched, entry stands,
        # and no active "dup" was minted over it.
        assert sentinel.read_text() == "old-box-credentials"
        assert registry_store.lookup_deregistered(std.registry, "dup") is not None
        assert "dup" not in _primary_names(std)

    def test_create_over_active_name_refused(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.commands.box._parser import run_create

        orig = tmp_home / "orig"
        orig.mkdir()
        assert run_create(_create_args(orig, name="act")) == 0
        std = self._std(config_file)
        assert "act" in _primary_names(std)
        capsys.readouterr()

        newp = tmp_home / "newp"
        newp.mkdir()
        rc = run_create(_create_args(newp, name="act"))
        assert rc == 1
        # Active-name collision refused by check_primary_box_name_free.
        assert "already registered" in capsys.readouterr().err
        assert "act" in _primary_names(std)

    def test_create_over_orphaned_metadata_refused(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.commands.box._parser import run_create

        std = self._std(config_file)
        # An ORPHANED home: a std.boxes/<name> dir with NO membership, NO
        # deregistered entry, NO pending journal entry.
        orphan = std.boxes / "orphan"
        (orphan / "home").mkdir(parents=True, exist_ok=True)
        (orphan / "home" / "KEEP.txt").write_text("orphaned")

        newp = tmp_home / "newp"
        newp.mkdir()
        rc = run_create(_create_args(newp, name="orphan"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "orphaned metadata" in err
        assert (orphan / "home" / "KEEP.txt").read_text() == "orphaned"
        assert "orphan" not in _primary_names(std)

    def test_normal_create_fresh_name_unaffected(
        self, config_file, tmp_home, credentials_dir
    ):
        """A --name create of a genuinely-new name at a fresh path is UNAFFECTED —
        the guard is a no-op when the home is free."""
        from kanibako.commands.box._parser import run_create

        fresh = tmp_home / "fresh"
        fresh.mkdir()
        rc = run_create(_create_args(fresh, name="brandnew"))
        assert rc == 0
        std = self._std(config_file)
        assert "brandnew" in _primary_names(std)
        assert (std.boxes / "brandnew").is_dir()

    def test_stale_journal_entry_does_not_reopen_hazard(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """A STALE `create` journal entry (register->clear-window crash that `rm`
        never clears) must NOT false-allow `create --name X <new path>` to merge
        into a deregistered box's retained home.  The deregistered refusal
        precedes the pending-create allow, so the reuse hole stays closed even
        with a lingering journal crumb."""
        from types import SimpleNamespace

        from kanibako import registry_store
        from kanibako.launch import journal
        from kanibako.commands.box._parser import run_create, run_rm
        from kanibako.commands.start import _write_create_entry
        from kanibako.settings.paths import BoxMode

        orig = tmp_home / "orig"
        orig.mkdir()
        assert run_create(_create_args(orig, name="dup")) == 0
        std = self._std(config_file)
        home_dir = std.boxes / "dup"
        (home_dir / "home").mkdir(parents=True, exist_ok=True)
        sentinel = home_dir / "home" / "SECRET.txt"
        sentinel.write_text("old-box-credentials")

        # Plant a stale pending `create` entry for the box home (the
        # register->clear crash window), then `rm dup` (which does NOT clear it).
        proj = SimpleNamespace(
            shell_path=home_dir / "home", mode=BoxMode.primary,
            name="dup", project_path=orig, group=None,
        )
        _write_create_entry(std, proj)
        assert run_rm(
            argparse.Namespace(target="dup", purge=False, force=False)
        ) == 0
        assert journal.pending_create(std.journal, str(home_dir)) is not None
        assert registry_store.lookup_deregistered(std.registry, "dup") is not None
        capsys.readouterr()

        # create --name dup at a NEW path is REFUSED despite the stale entry.
        newp = tmp_home / "newp"
        newp.mkdir()
        rc = run_create(_create_args(newp, name="dup"))
        assert rc == 1
        assert "deregistered" in capsys.readouterr().err
        # No active "dup" minted over the deregistered home; sentinel intact.
        assert "dup" not in _primary_names(std)
        assert sentinel.read_text() == "old-box-credentials"

    def test_named_half_create_recovery_still_works(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """A --name box interrupted mid-create (pending journal entry) is RESUMED
        by re-running `create --name <same>` — the I4 guard must NOT refuse it."""
        from kanibako.commands import start as start_mod
        from kanibako.commands.box._parser import run_create
        from kanibako.errors import ProjectError

        std = self._std(config_file)
        path = tmp_home / "halfbox"
        path.mkdir()

        real_register = start_mod._register_new_box
        calls = {"n": 0}

        def flaky(std, proj, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ProjectError("simulated registry collision")
            return real_register(std, proj, **kw)

        monkeypatch.setattr(
            "kanibako.commands.start._register_new_box", flaky
        )

        # First create: interrupted at register -> box dir + pending entry LEFT.
        with pytest.raises(ProjectError):
            run_create(_create_args(path, name="halfbox"))
        box_key = str(std.boxes / "halfbox")
        assert journal.pending_create(std.journal, box_key) is not None
        assert (std.boxes / "halfbox").is_dir()

        # Re-run: the guard sees the pending entry and ALLOWS the recovery re-entry.
        rc = run_create(_create_args(path, name="halfbox"))
        assert rc == 0
        assert "halfbox" in _primary_names(std)
        assert journal.pending_create(std.journal, box_key) is None

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

from kanibako import journal
from kanibako.commands.start import (
    _box_journal_key,
    _clear_create_entry,
    _pending_create_entry,
    _register_new_box,
    _write_create_entry,
)
from kanibako.paths import BoxMode


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
        from kanibako.names import read_names

        registry = tmp_path / "registry.yaml"
        std = SimpleNamespace(registry=registry)
        proj = SimpleNamespace(
            mode=BoxMode.primary, name="myapp",
            project_path=tmp_path / "ws" / "myapp",
        )
        _register_new_box(std, proj)
        assert read_names(registry)["projects"]["myapp"] == str(
            tmp_path / "ws" / "myapp"
        )

    def test_primary_idempotent_same_mapping(self, tmp_path: Path) -> None:
        """Recovery re-entry on an already-registered box is a no-op (no raise)."""
        from types import SimpleNamespace
        registry = tmp_path / "registry.yaml"
        std = SimpleNamespace(registry=registry)
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
        """NAMED boxes carry no name-registry entry on create."""
        from types import SimpleNamespace
        from kanibako.names import read_names

        registry = tmp_path / "registry.yaml"
        std = SimpleNamespace(registry=registry)
        proj = SimpleNamespace(
            mode=BoxMode.named, name="proj",
            project_path=tmp_path / "ws" / "workspaces" / "proj",
        )
        _register_new_box(std, proj)  # no-op.
        assert read_names(registry)["projects"] == {}


# ---------------------------------------------------------------------------
# Resolver register=False (deferred registration)
# ---------------------------------------------------------------------------

class TestResolverRegisterFalse:
    def test_primary_register_false_leaves_registry_untouched(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.config import load_config
        from kanibako.names import read_names
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
            register=False,
        )
        assert proj.is_new
        assert proj.name == "project"
        assert read_names(std.registry)["projects"] == {}
        assert proj.shell_path.is_dir()

    def test_primary_register_true_is_default(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.config import load_config
        from kanibako.names import read_names
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )
        assert read_names(std.registry)["projects"][proj.name] == project_dir

    def test_standalone_register_false_not_in_standalone_section(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.config import load_config
        from kanibako import registry_store
        from kanibako.paths import load_std_paths, resolve_standalone_project

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
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

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
        path=str(path), standalone=False, no_vault=True, distinct_auth=False,
        name=None, image=None, agent=None, allow_home=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


class TestRunCreateJournalLifecycle:
    def test_clean_create_leaves_no_entry_and_registers(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """A clean create ends registered AND with no pending entry (invariant
        registered ==> no pending entry).  The entry is present DURING the seed
        (write-ahead ordering)."""
        from kanibako.commands.box._parser import run_create
        from kanibako.config import load_config
        from kanibako.names import read_names
        from kanibako.paths import load_std_paths

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
        assert "project" in read_names(std.registry)["projects"]

    def test_genuine_collision_in_register_leaves_entry(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """If the deferred register raises a GENUINE collision, the entry is LEFT
        (box incomplete) and the error propagates — run_create does NOT swallow
        it or clear the entry."""
        from kanibako.commands.box import _parser
        from kanibako.config import load_config
        from kanibako.errors import ProjectError
        from kanibako.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)

        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )

        def boom(std, proj):
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
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths

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
    from kanibako.paths import resolve_project, resolve_standalone_project

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
        from kanibako.config import load_config
        from kanibako.names import read_names
        from kanibako.paths import load_std_paths

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
            assert read_names(std.registry)["projects"] == {}

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
        assert "project" in read_names(std.registry)["projects"]
        # Registered EXACTLY once.
        assert list(read_names(std.registry)["projects"]).count("project") == 1
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
        from kanibako.config import load_config
        from kanibako import registry_store
        from kanibako.paths import load_std_paths

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

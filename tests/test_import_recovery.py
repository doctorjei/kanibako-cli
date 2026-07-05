"""Import/connect atomicity + recovery via the lifecycle journal (J2).

Import/connect REGISTER an externally-seeded box and NEVER seed (CONVENTIONS
"Seed model" B7).  J2 brackets the register-only seam with a write-ahead
``op: import`` / ``op: connect`` journal entry: write entry -> register-if-absent
-> clear, with NO seed.  A crash before the entry clears leaves it; the next
resolve re-enters the (idempotent) import and replays it — register-if-absent ->
clear, NEVER a seed.  The op TYPE (import/connect, not create) is exactly what
keeps "import never seeds" true: the register-only replay table has no seed step.

These tests are NON-VACUOUS: rc / registry / journal state and the "seed NOT
called" guard are asserted UNCONDITIONALLY (the J1 lesson — no ``if rc == 0:``
skips).  Behavioral-equivalence: a normal (non-interrupted) import/connect is
byte-identical to the pre-J2 register-only path (entry written + cleared WITHIN
the op, journal empty at rest).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako import import_reconcile, journal, registry_store
from kanibako.paths import (
    BoxMode,
    detect_project_mode,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.workset import add_project, create_workset


# ---------------------------------------------------------------------------
# journal.pending_import — the register-only recovery signal (op-typed)
# ---------------------------------------------------------------------------

class TestPendingImport:
    def test_pending_import_matches_import_and_connect_only(self, tmp_path: Path):
        jp = tmp_path / "journal.yaml"
        box = tmp_path / "boxes" / "b"
        # An import entry IS a pending import.
        journal.write_entry(jp, box, op="import", name="b", mode="primary")
        assert journal.pending_import(jp, box) is not None
        assert journal.pending_import(jp, box)["op"] == "import"
        # A connect entry IS a pending import.
        journal.write_entry(jp, box, op="connect", name="b", mode="named")
        assert journal.pending_import(jp, box) is not None
        # A create entry is NOT a pending import (op-typed separation).
        journal.write_entry(jp, box, op="create", name="b", mode="primary")
        assert journal.pending_import(jp, box) is None
        # ... and pending_create does NOT match an import entry.
        journal.write_entry(jp, box, op="import", name="b", mode="primary")
        assert journal.pending_create(jp, box) is None

    def test_pending_import_absent_when_empty(self, tmp_path: Path):
        jp = tmp_path / "journal.yaml"
        assert journal.pending_import(jp, tmp_path / "nope") is None


# ---------------------------------------------------------------------------
# Behavioral equivalence: a normal import leaves the journal EMPTY at rest
# (entry written + cleared WITHIN the op).
# ---------------------------------------------------------------------------

class TestImportBehavioralEquivalence:
    def test_standalone_import_empty_journal_at_rest(
        self, std, config, project_dir, credentials_dir, capsys,
    ):
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        name = proj.name
        registry_store.save_section(std.registry, "standalone", {})
        capsys.readouterr()

        # Resolve re-discovers + imports; journal must be EMPTY at rest.
        result = detect_project_mode(project_dir, std, config)
        assert result.mode is BoxMode.standalone
        assert registry_store.load_standalone(std.registry).get(name) == str(
            project_dir.resolve()
        )
        assert journal.read_journal(std.journal) == {}

    def test_primary_resolve_no_rediscovery_option_a(
        self, std, config, project_dir, credentials_dir, capsys,
    ):
        """P8b/Option A: a register=True resolve does NOT auto-rediscover an
        unregistered on-disk PRIMARY box (retired live import) — and it touches no
        journal.  The registry is the sole identity authority."""
        proj = resolve_project(
            std, config, project_dir=str(project_dir), initialize=True,
        )
        assert proj.name
        registry_store.save_section(std.registry, "projects", {})
        capsys.readouterr()

        proj2 = resolve_project(
            std, config, project_dir=str(project_dir), initialize=False,
        )
        assert proj2.name == ""  # not recovered from disk
        assert registry_store.load_section(std.registry, "projects") == {}
        assert journal.read_journal(std.journal) == {}

    def test_named_workset_import_empty_journal_at_rest(
        self, std, config, tmp_home, capsys,
    ):
        ws_root = tmp_home / "worksets" / "imp"
        create_workset("imp", ws_root, std)
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        name = import_reconcile.import_named_workset(
            std.registry, ws_root, journal=std.journal,
        )
        assert name == "imp"
        assert registry_store.load_section(std.registry, "worksets").get(
            "imp"
        ) == str(ws_root.resolve())
        assert journal.read_journal(std.journal) == {}

    def test_import_without_journal_arg_is_plain_register(
        self, std, config, project_dir, credentials_dir, capsys,
    ):
        """A std-less caller (journal=None) registers exactly as before J2 —
        no journal file is touched."""
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        name = proj.name
        registry_store.save_section(std.registry, "standalone", {})
        capsys.readouterr()

        # Direct call WITHOUT journal= → plain register, no journal write.
        got = import_reconcile.import_standalone(std.registry, project_dir)
        assert got == name
        assert registry_store.load_standalone(std.registry).get(name) == str(
            project_dir.resolve()
        )
        # The journal file was never created/written.
        assert journal.read_journal(std.journal) == {}


# ---------------------------------------------------------------------------
# Atomicity: the entry is PRESENT during the register window (write-ahead).
# ---------------------------------------------------------------------------

class TestImportWriteAheadOrdering:
    def test_entry_present_during_register_then_cleared(
        self, std, config, project_dir, credentials_dir, capsys, monkeypatch,
    ):
        """The journal entry exists DURING the registry write (write-ahead) and
        is gone after — proven by spying the registry write."""
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        name = proj.name
        registry_store.save_section(std.registry, "standalone", {})
        capsys.readouterr()

        box_key = str((project_dir / "box_data").resolve())
        seen = {}
        real_register = registry_store.register_standalone

        def spy_register(registry, n, root):
            seen["pending_during_register"] = (
                journal.pending_import(std.journal, box_key) is not None
            )
            return real_register(registry, n, root)

        monkeypatch.setattr(
            registry_store, "register_standalone", spy_register,
        )
        import_reconcile.import_standalone(
            std.registry, project_dir, journal=std.journal,
        )

        # Write-ahead: entry was present during the register, gone at rest.
        assert seen["pending_during_register"] is True
        assert journal.pending_import(std.journal, box_key) is None
        assert journal.read_journal(std.journal) == {}
        assert registry_store.load_standalone(std.registry).get(name)

    def test_register_crash_leaves_entry(
        self, std, config, project_dir, credentials_dir, capsys, monkeypatch,
    ):
        """If the register body raises, the entry is LEFT (import incomplete) —
        the wrapper does NOT swallow the error or clear the entry."""
        resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        registry_store.save_section(std.registry, "standalone", {})
        capsys.readouterr()

        box_key = str((project_dir / "box_data").resolve())

        def boom(registry, n, root):
            raise RuntimeError("simulated registry write crash")

        monkeypatch.setattr(registry_store, "register_standalone", boom)
        with pytest.raises(RuntimeError, match="simulated"):
            import_reconcile.import_standalone(
                std.registry, project_dir, journal=std.journal,
            )
        # Entry LEFT — recovery resumes it on the next resolve.
        assert journal.pending_import(std.journal, box_key) is not None


# ---------------------------------------------------------------------------
# RECOVERY (NON-VACUOUS): an interrupted import is completed on re-resolve,
# the entry cleared, and NO SEED is ever called.
# ---------------------------------------------------------------------------

# The seed entry points an import must NEVER call.  Patched to a sentinel-raising
# fail so any accidental seed during an import recovery is a hard test failure.
_SEED_FUNCS = (
    "kanibako.commands.start.seed_new_box",
    "kanibako.commands.start._seed_box_home",
)


@pytest.fixture
def seed_tripwire(monkeypatch):
    """Make ANY seed call during the test an immediate failure.

    Import/connect register-only flows must never seed (B7); recovery replays
    register-only.  Tripping this proves the recovery path took no seed step.
    """
    called = {"seed": False}

    def trip(*a, **k):
        called["seed"] = True
        raise AssertionError("seed must NOT be called on an import/connect path")

    for fq in _SEED_FUNCS:
        monkeypatch.setattr(fq, trip)
    return called


class TestStandaloneImportRecovery:
    def test_interrupted_import_completes_and_clears_no_seed(
        self, std, config, project_dir, credentials_dir, capsys, seed_tripwire,
    ):
        """STANDALONE import interrupted (entry left, NOT registered):
        re-resolve registers + clears the entry; seed NEVER called.  All asserts
        UNCONDITIONAL."""
        # Build a real standalone box, then wipe the registry → unregistered.
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        name = proj.name
        registry_store.save_section(std.registry, "standalone", {})

        box_key = str((project_dir / "box_data").resolve())
        # Simulate the crash-before-register state: write the import entry, then
        # STOP (registration not done).
        journal.write_entry(
            std.journal, box_key, op="import", name=name, mode="standalone",
        )
        # Plant a user file in the box home to prove recovery never re-seeds it.
        home = project_dir / "box_data" / "home"
        home.mkdir(parents=True, exist_ok=True)
        (home / "USER.txt").write_text("precious")
        capsys.readouterr()

        assert journal.pending_import(std.journal, box_key) is not None
        assert registry_store.load_standalone(std.registry) == {}

        # Recovery: re-resolve re-enters the idempotent import → register + clear.
        result = detect_project_mode(project_dir, std, config)

        assert result.mode is BoxMode.standalone
        # Registered exactly once.
        registered = registry_store.load_standalone(std.registry)
        assert registered.get(name) == str(project_dir.resolve())
        assert list(registered).count(name) == 1
        # Entry GONE; journal empty (invariant restored).
        assert journal.pending_import(std.journal, box_key) is None
        assert journal.read_journal(std.journal) == {}
        # NO seed (tripwire never fired) + user file untouched.
        assert seed_tripwire["seed"] is False
        assert (home / "USER.txt").read_text() == "precious"

    def test_register_clear_window_stale_entry_cleared(
        self, std, config, project_dir, credentials_dir, capsys, seed_tripwire,
    ):
        """STANDALONE register->clear window (REGISTERED + stale entry): a
        re-resolve over the already-complete box clears the stale entry, no seed.
        """
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        name = proj.name
        box_key = str((project_dir / "box_data").resolve())
        # Box is registered (from the resolve above); plant a stale import entry.
        journal.write_entry(
            std.journal, box_key, op="import", name=name, mode="standalone",
        )
        capsys.readouterr()
        assert registry_store.load_standalone(std.registry).get(name)
        assert journal.pending_import(std.journal, box_key) is not None

        # Re-resolve hits the import's idempotent no-op branch → clears stale.
        detect_project_mode(project_dir, std, config)

        assert journal.pending_import(std.journal, box_key) is None
        assert journal.read_journal(std.journal) == {}
        assert seed_tripwire["seed"] is False


# NOTE (P8c): the PRIMARY import-recovery tests were removed — they exercised
# ``reconcile_primary_boxes``, which was sequestered out of the live package into
# ``salvage/primary_reconcile.py`` (a non-shipping frozen reference).  Under
# Option A primary import recovery is not wired into any live resolve path; the
# STANDALONE/CONNECT recovery paths above/below remain live and covered.


# ---------------------------------------------------------------------------
# CONNECT recovery + atomicity (the journal entry lives in run_connect, the
# ACTUAL connect command — NOT in add_project, which the deferred
# move/convert/duplicate pipelines also call; FIX 2).
# ---------------------------------------------------------------------------

def _connect_args(workset, source, project_name=None):
    import argparse as _ap
    return _ap.Namespace(
        workset=workset, source=str(source), project_name=project_name,
    )


class TestConnectJournal:
    def test_clean_connect_empty_journal_at_rest(
        self, config_file, tmp_home, capsys,
    ):
        """A normal connect (via run_connect) writes + clears the entry within
        the op → journal empty at rest (behavioral equivalence)."""
        from kanibako.commands.workset_cmd import run_connect
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths
        from kanibako.workset import load_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("cjws", tmp_home / "cj_ws", std)
        src = tmp_home / "cj_src"
        src.mkdir()

        rc = run_connect(_connect_args("cjws", src, "cjproj"))
        assert rc == 0
        assert journal.read_journal(std.journal) == {}
        reloaded = load_workset(ws.root)
        assert any(p.name == "cjproj" for p in reloaded.projects)

    def test_connect_entry_present_during_membership_write(
        self, config_file, tmp_home, capsys, monkeypatch,
    ):
        """Write-ahead: the connect entry exists DURING the workset-membership
        write (inside add_project) and is gone after run_connect returns."""
        from kanibako.commands.workset_cmd import run_connect
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths
        from kanibako import workset as workset_mod

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("cjws2", tmp_home / "cj_ws2", std)
        src = tmp_home / "cj_src2"
        src.mkdir()

        box_key = str((tmp_home / "cj_ws2" / "boxes" / "cjproj2"))
        seen = {}
        real_write = workset_mod._write_workset_toml

        def spy_write(w):
            seen["pending_during_write"] = (
                journal.pending_import(std.journal, box_key) is not None
            )
            return real_write(w)

        monkeypatch.setattr(workset_mod, "_write_workset_toml", spy_write)
        rc = run_connect(_connect_args("cjws2", src, "cjproj2"))

        assert rc == 0
        assert seen["pending_during_write"] is True
        assert journal.pending_import(std.journal, box_key) is None
        assert journal.read_journal(std.journal) == {}

    def test_connect_membership_crash_leaves_entry(
        self, config_file, tmp_home, capsys, monkeypatch,
    ):
        """A crash in the membership write leaves the connect entry (incomplete);
        the in-process unwind still runs but the journal entry persists for the
        cross-process recovery signal."""
        from kanibako.commands.workset_cmd import run_connect
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths
        from kanibako import workset as workset_mod

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("cjws3", tmp_home / "cj_ws3", std)
        src = tmp_home / "cj_src3"
        src.mkdir()

        box_key = str((tmp_home / "cj_ws3" / "boxes" / "cjproj3"))

        def boom(w):
            raise RuntimeError("simulated membership write crash")

        monkeypatch.setattr(workset_mod, "_write_workset_toml", boom)
        with pytest.raises(RuntimeError, match="simulated"):
            run_connect(_connect_args("cjws3", src, "cjproj3"))

        # Entry LEFT (the membership write never completed).
        assert journal.pending_import(std.journal, box_key) is not None


class TestConnectSelfHealOnResolve:
    """FIX 1: a stale ``connect`` entry on an already-MEMBER box self-heals when
    ``resolve_workset_project`` next resolves it — symmetric with the import
    path's ``_clear_stale_import``.  Restores 'registered ==> no pending entry'."""

    def test_resolve_clears_stale_connect_entry(
        self, config_file, tmp_home, credentials_dir, capsys, seed_tripwire,
    ):
        from kanibako.commands.workset_cmd import run_connect
        from kanibako.config import load_config
        from kanibako.paths import (
            load_std_paths,
            resolve_workset_project,
        )
        from kanibako.paths import WorksetSpec
        from kanibako.workset import load_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("healws", tmp_home / "heal_ws", std)
        src = tmp_home / "heal_src"
        src.mkdir()

        # Connect (registers the member), then plant a stale connect entry to
        # simulate a crash in the register->clear window (box IS a member).
        rc = run_connect(_connect_args("healws", src, "healproj"))
        assert rc == 0
        box_key = str((tmp_home / "heal_ws" / "boxes" / "healproj"))
        journal.write_entry(
            std.journal, box_key, op="connect", name="healproj", mode="named",
            workset="healws",
        )
        capsys.readouterr()
        assert journal.pending_import(std.journal, box_key) is not None

        # Resolve the already-member box → self-heal clears the stale entry.
        ws = load_workset(tmp_home / "heal_ws")
        proj = resolve_workset_project(
            WorksetSpec.from_workset(ws), "healproj", std, config,
            initialize=False,
        )

        # UNCONDITIONAL: the box's host-side dir matches the entry key, cleared.
        assert str(Path(proj.shell_path).parent) == box_key
        assert journal.pending_import(std.journal, box_key) is None
        assert journal.read_journal(std.journal) == {}
        # No seed on the resolve path.
        assert seed_tripwire["seed"] is False

    def test_resolve_does_not_touch_create_entry(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Guard: the self-heal clears ONLY register-only (import/connect)
        entries — a J1 ``create`` entry for the same box is LEFT untouched (it is
        the create-recovery path's signal)."""
        from kanibako.commands.workset_cmd import run_connect
        from kanibako.config import load_config
        from kanibako.paths import (
            load_std_paths,
            resolve_workset_project,
        )
        from kanibako.paths import WorksetSpec
        from kanibako.workset import load_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("crws", tmp_home / "cr_ws", std)
        src = tmp_home / "cr_src"
        src.mkdir()
        run_connect(_connect_args("crws", src, "crproj"))

        box_key = str((tmp_home / "cr_ws" / "boxes" / "crproj"))
        journal.write_entry(
            std.journal, box_key, op="create", name="crproj", mode="named",
        )
        capsys.readouterr()

        ws = load_workset(tmp_home / "cr_ws")
        resolve_workset_project(
            WorksetSpec.from_workset(ws), "crproj", std, config,
            initialize=False,
        )
        # The create entry is NOT cleared by the connect self-heal.
        assert journal.pending_create(std.journal, box_key) is not None


class TestDeferredPipelinesDoNotJournalConnect:
    """FIX 2: add_project is also the membership-write seam for the DEFERRED
    move/convert/duplicate pipelines.  Those must produce NO ``connect`` journal
    entry — only the actual connect command (run_connect) journals."""

    def test_bare_add_project_writes_no_journal_entry(
        self, config_file, tmp_home, capsys,
    ):
        """A direct add_project call (the move/convert/duplicate seam) writes NO
        journal entry, even though std (with a journal) is passed."""
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths
        from kanibako.workset import load_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("dpws", tmp_home / "dp_ws", std)
        # Internal source (the duplicate/move target is an in-tree workspace dir).
        src = ws.workspaces_dir / "dpproj"
        src.mkdir(parents=True)

        add_project(ws, "dpproj", src, std)  # std passed — but NO journal entry.

        assert journal.read_journal(std.journal) == {}
        reloaded = load_workset(ws.root)
        assert any(p.name == "dpproj" for p in reloaded.projects)

    def test_copy_into_workset_writes_no_journal_entry(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """``copy_into_workset`` (the duplicate-into-workset pipeline helper)
        registers via add_project but writes NO connect journal entry."""
        from kanibako.commands.box._lifecycle import copy_into_workset
        from kanibako.config import load_config
        from kanibako.paths import BoxMode, load_std_paths
        from kanibako.workset import load_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("ciws", tmp_home / "ci_ws", std)

        # A source box to copy from: metadata + shell dirs + a workspace.
        src_meta = tmp_home / "src_box"
        src_shell = src_meta / "home"
        src_shell.mkdir(parents=True)
        (src_shell / "marker.txt").write_text("x")
        src_workspace = tmp_home / "src_ws"
        src_workspace.mkdir()

        copy_into_workset(
            ws, "ciproj", src_meta, src_shell, src_workspace, BoxMode.primary,
            copy_workspace=True, std=std,
        )

        # The pipeline registered the project but journaled NO connect op.
        assert journal.read_journal(std.journal) == {}
        reloaded = load_workset(ws.root)
        assert any(p.name == "ciproj" for p in reloaded.projects)

    def test_no_journal_connect_call_in_lifecycle_module(self) -> None:
        """Structural guard: the move/convert/duplicate module must NOT call the
        connect journal bracket — only run_connect journals a connect op."""
        import importlib
        import inspect

        src = inspect.getsource(
            importlib.import_module("kanibako.commands.box._lifecycle")
        )
        assert "_journal_connect" not in src, (
            "move/convert/duplicate (_lifecycle) must NOT journal a connect op; "
            "only run_connect does."
        )


# ---------------------------------------------------------------------------
# Structural guard: the import/connect journal helpers must NEVER reference a
# seed entry point (the register-only replay has no seed step — B7).
# ---------------------------------------------------------------------------

class TestNoSeedInImportConnectModules:
    @pytest.mark.parametrize(
        "module", ["kanibako.import_reconcile", "kanibako.workset"],
    )
    def test_no_seed_calls(self, module: str) -> None:
        import importlib
        import inspect

        src = inspect.getsource(importlib.import_module(module))
        for forbidden in ("seed_new_box", "_seed_box_home"):
            assert forbidden not in src, (
                f"{module} must not reference {forbidden}: import/connect "
                f"register-only flows must NEVER seed (B7)."
            )

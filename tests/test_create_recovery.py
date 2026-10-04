"""Interrupted-create recovery via the lifecycle journal (J1).

A create is write-ahead journaled: ``write-entry -> seed -> register ->
clear-entry``.  A crash before the entry is cleared leaves a pending ``create``
journal entry; a ``create`` on such a path REFUSES and names what it found, and
``create --recover`` is what COMPLETES it by replay (seed create-if-absent ->
register-if-absent -> clear-entry).  The HARD INVARIANT — ``registered ==> no
pending entry`` at rest — holds for PRIMARY and STANDALONE, both for an
unregistered interrupted box and a register->clear-window crash (registered +
stale entry).

This SUPERSEDES the B3 ``.seeding`` file-marker suite.  The journal create-entry
helpers (``_write_create_entry`` / ``_clear_create_entry`` / ``_pending_create_
entry``) replace the marker helpers; the recovery tests are NON-VACUOUS —
``rc == 0`` and the post-recovery state are asserted UNCONDITIONALLY.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

import pytest

from kanibako.agent_ref import parse_agent_ref
from kanibako.launch import journal
from kanibako.commands.start import (
    _box_journal_key,
    _clear_create_entry,
    _pending_create_entry,
    _register_new_box,
    _write_create_entry,
)
from kanibako.settings.paths import BoxMode, load_primary_boxes

# ⚑ NEVER ``shutil.rmtree`` A BOX TREE FROM A TEST BODY — ``run_create`` materializes
# the J-7 canon skeleton root-owned + 555, so a bare ``rmtree`` dies with EACCES where
# ``podman unshare`` works and silently passes where it does not.  That asymmetry is
# what makes a local green vacuous; ``remove_box_tree`` is the sanctioned deleter.
from kanibako.runtime.container import remove_box_tree


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
        from kanibako.project import registry_store

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
        from kanibako.project import registry_store
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
    # ⚑ ``no_vault=True`` is this suite's vault SUPPRESSION shorthand, and it is
    # indistinguishable from a typed ``--no-vault`` — which is a SHAPING flag the
    # recovery refusal must refuse.  A recovery or bare-refusal re-run therefore
    # passes ``no_vault=False`` to spell the flag the way the CLI spells it when
    # absent; that also matches ``_simulate_interrupted_create``, which resolves
    # with the resolver's own (enabled) default.
    ns = argparse.Namespace(
        path=str(path), standalone=False, no_vault=True,
        name=None, image=None, agent=None, allow_home=False,
        register=False, recover=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _cure_lines(err: str, verb: str) -> "list[str]":
    """Every ``kanibako <verb> …`` line in *err*, stripped.

    Membership in this list is an END-OF-LINE anchor; a bare ``in err`` is a
    substring test, and a standalone root's ``... {path}`` IS a substring of the
    ``... {path}/workspace`` that is not the box.
    """
    return [ln.strip() for ln in err.splitlines()
            if ln.strip().startswith(f"kanibako {verb} ")]


def _printed_cure(err: str, verb: str) -> "list[str]":
    """The argv of the ONE ``kanibako <verb> …`` line *err* printed.

    Read off the refusal and whitespace-split with the leading ``kanibako``
    dropped, so a caller can hand the result to the SHIPPED parser and RUN the
    command the user was told to type.  A cure the CLI would reject must raise here
    rather than at their terminal, which no string match can do.
    """
    import shlex

    lines = _cure_lines(err, verb)
    assert len(lines) == 1, f"expected exactly one 'kanibako {verb}' line, got {lines}"
    return shlex.split(lines[0])[1:]


def _run_printed_cure(err: str, verb: str) -> int:
    """RUN the printed *verb* cure through the shipped parser; return its rc."""
    from kanibako.cli import build_parser

    parsed = build_parser().parse_args(_printed_cure(err, verb))
    return parsed.func(parsed)


class TestRunCreatePersonaGate:
    """`box create` for an UNLOADABLE persona: a TRUE PRE-FLIGHT (F5, Director
    ruling 2026-07-03).  The load-or-error gate runs on a NON-materializing probe
    BEFORE the box dir is created (and before the write-ahead journal entry, ruling
    #3), so a failed create leaves NOTHING behind: no box dir / box.yaml, no
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
        from kanibako.settings.agent_config import agent_settings_path
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths
        from kanibako.launch import journal

        # Nothing configures the explicit persona 'navigator+claude' — no keyspace
        # endpoint, no persona-store entry — so the create verdict is a hard error.
        # claude IS an installed harness in the test env, so the gate is genuinely
        # reached (not skipped as no-agent).
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
        # TRUE PRE-FLIGHT: the box was NEVER materialized — no box dir /
        # box.yaml (the workspace dir tmp_home/project the user asked to
        # create in is theirs; the BOX under std.boxes is what must be absent).
        assert not std.boxes.exists() or not any(std.boxes.iterdir())
        # Guard ran BEFORE the journal entry: no entry written, nothing seeded,
        # registry untouched (fs-level).
        m_write_entry.assert_not_called()
        assert seed_called["v"] is False
        assert journal.read_journal(std.journal) == {}
        assert _primary_names(std) == {}
        # No persona store DIRECTORY was materialized for the node — the whole
        # ``agents/<node>/`` dir, not just its ``agent.yaml``, since a node dir can
        # exist without that file.
        assert not agent_settings_path(std.agents, "navigator+claude").parent.exists()


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
        self, config_file, tmp_home, credentials_dir, monkeypatch, capsys
    ):
        """A genuinely complete box (registered, NO pending entry) re-created
        errors 'already initialized' (rc=1) — recovery only triggers on an
        actual pending entry, not on every existing box.

        ⚑ AND IT LEAVES THE DISK ALONE.  This is the PRIMARY twin of the
        standalone defect: ``resolve_project``'s ``if initialize:`` arm
        re-bootstraps a missing ``home/`` before returning, so the refusal used to
        print "already initialized" about a box whose home it had just re-created.
        The home is deleted between the two calls exactly so that shows up.
        """
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

        home = std.boxes / "project" / "home"
        assert home.is_dir()
        assert remove_box_tree(home)

        capsys.readouterr()
        rc2 = run_create(_create_args(tmp_home / "project"))
        assert rc2 == 1
        # ⚑ FULL message: the path now comes off the PROBE, and must still be the
        # resolved workspace the materializing resolve would have reported.
        assert capsys.readouterr().err.strip() == (
            f"Error: project already initialized in {(tmp_home / 'project').resolve()}"
        )
        assert not home.exists()

    def test_create_standalone_refusal_leaves_disk_untouched(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """FULL-TREE snapshot equality across a refused standalone re-create.

        The strongest form of "nothing happened": every path under the root is
        enumerated before and after the refused ``create`` and the two lists must
        match.  Home and workspace are deleted first so the resolver's recovery
        arms (``_bootstrap_shell`` + the ``workspace/`` mkdir) have something to
        re-create if the refusal is still downstream of them.
        """
        from kanibako.commands.box._parser import run_create

        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )
        root = tmp_home / "sa-proj"
        root.mkdir()
        assert run_create(_create_args(root, standalone=True)) == 0

        assert remove_box_tree(root / "box_data" / "home")
        assert remove_box_tree(root / "workspace")
        before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))

        assert run_create(_create_args(root, standalone=True)) == 1
        after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        assert after == before

    def test_unregistered_placeholder_dir_does_not_block_create(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """A brand-new PRIMARY create still succeeds — the predicate the refusal
        now reads must be FALSE for a box that does not exist yet.

        ⚑ Pins the ``std.boxes/__unregistered__`` coupling.  A brand-new primary
        box has no registered name, so ``_resolve_local_dir`` returns the
        ``__unregistered__`` PLACEHOLDER as ``metadata_path`` — and
        ``box_tree_materialized`` asks whether ``metadata_path`` is a dir.  The
        placeholder is never materialized, which is why the create proceeds; this
        asserts both halves (rc 0, and the placeholder is not on disk).  ⚑ Note
        the coupling is INHERITED, not introduced: ``resolve_project``'s own
        ``is_new`` gate reads the same path, so a placeholder dir that DID exist
        refused the create before this change too (pinned below).
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )
        std = load_std_paths(load_config(config_file))
        assert run_create(_create_args(tmp_home / "project")) == 0
        assert not (std.boxes / "__unregistered__").exists()

    def test_unregistered_placeholder_coupling_is_unchanged(
        self, config_file, tmp_home, credentials_dir, monkeypatch, capsys
    ):
        """CHARACTERIZATION, not an endorsement: a pre-existing
        ``std.boxes/__unregistered__`` dir makes a brand-new PRIMARY create refuse
        "already initialized".

        That was true BEFORE the refusal was hoisted (``resolve_project`` gates
        ``is_new`` on the same placeholder path) and is true after — the hoist
        moves WHEN the refusal prints, not WHAT it decides.  Pinned so the
        coupling cannot silently change under a future edit to either gate.  It is
        a latent defect in its own right and is NOT fixed here.
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )
        std = load_std_paths(load_config(config_file))
        (std.boxes / "__unregistered__").mkdir(parents=True)

        capsys.readouterr()
        assert run_create(_create_args(tmp_home / "project")) == 1
        assert "already initialized" in capsys.readouterr().err


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
        from kanibako.project.names import register_name
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
        from kanibako.project.names import register_name
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
            "kanibako.project.import_reconcile",
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
        ``create --recover`` completes — registered exactly once, entry GONE, USER
        HOME EDIT SURVIVES.  Asserted UNCONDITIONALLY (no rc-gated skip)."""
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

        # Recovery: re-run create --recover (seed neutralized — assert the
        # register+entry-clear completion + home untouched).
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )
        rc = run_create(_create_args(path, recover=True, no_vault=False))

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
        ``create --recover`` completes — registered exactly once, entry GONE, USER
        HOME EDIT SURVIVES.  Asserted UNCONDITIONALLY.

        ⚑ The re-run passes ``--register`` (I3/§D4a): registration at create is
        opt-in now, and this suite is about the JOURNAL, so it keeps asking for
        the registering create it always exercised."""
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.project import registry_store
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
        rc = run_create(
            _create_args(root, standalone=True, register=True,
                         recover=True, no_vault=False)
        )

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

    def test_recovery_of_a_registered_box_never_claims_unregistered(
        self, config_file, tmp_home, credentials_dir, monkeypatch, capsys
    ):
        """⚑ The create hint reads the REGISTRY, not the flag (I3/§D4a).

        A create interrupted AFTER its registry write leaves a registered box
        with a stale journal entry.  The recovery re-run carries no
        ``--register`` — nobody re-types the flag to finish an interrupted
        create — so a flag-gated hint would tell the user their registered box
        is "Not registered" and hand them a cure for a state it is not in.
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.project import registry_store
        from kanibako.settings.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa"
        root.mkdir()

        proj = _simulate_interrupted_create(
            std, config, standalone=True, path=root, register_box=True,
        )
        assert proj.name in registry_store.load_standalone(std.registry)
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )
        capsys.readouterr()

        assert run_create(
            _create_args(root, standalone=True, recover=True, no_vault=False)
        ) == 0

        out = capsys.readouterr().out
        assert "Not registered" not in out
        assert "box register" not in out


class TestRecoveryAgentIdentity:
    """A recovery may not seed one agent while the box is configured for another.

    ``--agent`` persists ``pref.system.agent`` under ``if proj.is_new:``, so a
    recovery — which is by definition NOT new — cannot adopt a new one.  The seed
    call sits OUTSIDE that guard, so it must resolve the agent the box's own
    settings resolve to: seeded for one, configured for the other, has no way
    back (the home bind owns the content after create; nothing re-seeds).  A
    ``--agent`` typed on the recovery is REFUSED by name rather than dropped.
    """

    def _interrupt_create_with_agent(self, path, agent):
        """Run a REAL ``create --agent`` that dies at the deferred register.

        Leaves the half-built box exactly as a crash would: box dir on disk,
        ``pref.system.agent`` already persisted, journal entry still pending.
        ⚑ Its own patch scope, so the caller's spy is not shadowed by the stub.
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.errors import ProjectError

        def boom(std, proj, **kw):
            raise ProjectError("simulated registry collision")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "kanibako.commands.start.seed_new_box",
                lambda std, config, proj, **kw: None,
            )
            mp.setattr("kanibako.commands.start._register_new_box", boom)
            with pytest.raises(ProjectError):
                run_create(_create_args(path, name="halfbox", agent=agent))

    def test_recovery_seeds_the_agent_the_box_is_configured_for(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """``create --recover`` seeds the agent the box's own settings resolve to.

        ⚑ Asserted as the RULE — "what the seed resolves == what the box
        resolves" — not as "``explicit_agent`` is None", so the pin survives a
        change in HOW the two are kept together.

        INVERT: hand ``seed_new_box`` the recovery's ``--agent`` again and the
        seed resolves goose while the box stays claude.
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.agent_select import select_agent
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        path = tmp_home / "halfbox"
        path.mkdir()

        self._interrupt_create_with_agent(path, "claude")

        box_key = str(std.boxes / "halfbox")
        assert journal.pending_create(std.journal, box_key) is not None
        # The persist DID happen on attempt one — it precedes the journal window,
        # so a pending entry means the box already has its agent.
        box_yaml = load_doc(std.boxes / "halfbox" / "box.yaml")
        assert box_yaml["pref"]["system"]["agent"] == "claude"

        seen: dict[str, str] = {}

        def spy_seed(std, config, proj, **kw):
            seen["seeded"] = select_agent(
                std=std, proj=proj, explicit_agent=kw.get("explicit_agent"),
            ).node
            seen["configured"] = select_agent(
                std=std, proj=proj, explicit_agent=None,
            ).node

        monkeypatch.setattr("kanibako.commands.start.seed_new_box", spy_seed)
        assert run_create(
            _create_args(path, recover=True, no_vault=False)
        ) == 0

        assert seen["configured"] == "claude"
        assert seen["seeded"] == seen["configured"]
        # The re-run's flag changed nothing on disk either (the ``is_new`` guard).
        assert load_doc(std.boxes / "halfbox" / "box.yaml") == box_yaml
        assert journal.pending_create(std.journal, box_key) is None

    def test_a_recovery_refuses_a_second_agent_rather_than_dropping_it(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """The refusal half: a second ``--agent`` on a recovery is REFUSED by name.

        ``--agent`` and ``--name`` are SHAPING, so attempt one already wrote the
        box's agent and the journal records no argument to compare the new one
        against.  Dropping the flag silently is what this refuses; a refusal that
        did not NAME it would leave the user with no way to know what was ignored.
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        path = tmp_home / "halfbox"
        path.mkdir()

        self._interrupt_create_with_agent(path, "claude")
        box_key = str(std.boxes / "halfbox")
        before = load_doc(std.boxes / "halfbox" / "box.yaml")
        capsys.readouterr()

        rc = run_create(
            _create_args(path, name="halfbox", agent="goose", no_vault=False)
        )

        assert rc == 1
        err = capsys.readouterr().err
        assert "--agent" in err and "--name" in err
        assert "goose" not in err  # never echo the value; name the flag
        # Refused BEFORE any write: settings untouched, entry still pending.
        assert load_doc(std.boxes / "halfbox" / "box.yaml") == before
        assert journal.pending_create(std.journal, box_key) is not None
        assert "halfbox" not in _primary_names(std)

    def test_fresh_create_still_seeds_the_agent_the_flag_names(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """The other half — a FRESH ``create --agent`` is unchanged: the flag
        both persists and steers the seed.  Without this, "ignore the flag"
        would pass the test above by breaking every ordinary create."""
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.agent_select import select_agent
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        path = tmp_home / "fresh"
        path.mkdir()

        seen: dict[str, str] = {}

        def spy_seed(std, config, proj, **kw):
            seen["seeded"] = select_agent(
                std=std, proj=proj, explicit_agent=kw.get("explicit_agent"),
            ).node

        monkeypatch.setattr("kanibako.commands.start.seed_new_box", spy_seed)
        assert run_create(_create_args(path, name="fresh", agent="goose")) == 0

        assert seen["seeded"] == "goose"
        assert load_doc(
            std.boxes / "fresh" / "box.yaml"
        )["pref"]["system"]["agent"] == "goose"


def _agent_flag_reads(node: ast.AST) -> list[ast.AST]:
    """Every read of the RAW ``--agent`` flag off ``args`` under *node*."""
    def is_read(n: ast.AST) -> bool:
        if isinstance(n, ast.Attribute):
            return (
                n.attr == "agent"
                and isinstance(n.value, ast.Name) and n.value.id == "args"
            )
        return (
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "getattr" and len(n.args) >= 2
            and isinstance(n.args[0], ast.Name) and n.args[0].id == "args"
            and isinstance(n.args[1], ast.Constant) and n.args[1].value == "agent"
        )

    return [n for n in ast.walk(node) if is_read(n)]


class TestAgentFlagIsReadOnce:
    """STRUCTURAL twin of ``TestRecoveryAgentIdentity``: ``run_create`` reads the
    raw ``--agent`` flag EXACTLY ONCE, and that read IS the ``_agent_arg``
    normalization every consumer downstream of it goes through.

    The behavioral pins above exercise the four consumers that exist today; a
    FIFTH one spelling the flag again would route around all of them in silence.
    Asserted over the AST, so the prose that names the spelling is not counted.
    """

    def test_run_create_reads_the_agent_flag_exactly_once(self) -> None:
        import inspect

        from kanibako.commands.box._parser import run_create

        fn = ast.parse(inspect.getsource(run_create)).body[0]
        # ⚑ Both assignment shapes — an added type annotation is the SAME rule,
        # and a pin that reds on it would be pinning the spelling, not the rule.
        norm = [
            n for n in ast.walk(fn)
            if isinstance(n, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(t, ast.Name) and t.id == "_agent_arg"
                for t in (
                    n.targets if isinstance(n, ast.Assign) else [n.target]
                )
            )
        ]
        assert norm, (
            "run_create must fold --agent into _agent_arg: one answer to "
            "'which agent is this create steering' IS the mechanism."
        )
        reads = _agent_flag_reads(fn)
        norm_reads = [r for n in norm for r in _agent_flag_reads(n)]
        assert len(reads) == 1 and reads == norm_reads, (
            f"run_create reads the raw --agent flag at {len(reads)} site(s); "
            "exactly one is allowed and it must BE the _agent_arg "
            "normalization. A recovery re-run folds the flag to None because "
            "the pref.system.agent persist is is_new-only while the seed is "
            "not: a second raw read seeds the box's home for the flag's agent "
            "while the box's settings still name the one the interrupted "
            "attempt persisted, and nothing ever re-seeds. Read _agent_arg "
            "instead; llm-docs/kanibako/commands/box/_parser.py.md carries the "
            "full reasoning."
        )


class TestBlankAgentFlagIsGivenAtBothDoors:
    """``--agent`` is GIVEN whenever argparse hands over a string — the persona
    store check and the ``pref.system.agent`` persist ask that one question.

    They once asked two: the store check tested ``_agent_arg`` truthiness, the
    persist tested ``.strip()`` truthiness, and ``--agent "  "`` fell between
    them.  A blank ref is a value the user TYPED, so it is refused by the ref
    grammar rather than read as "resolve from settings" — which would create the
    box steering an agent nobody asked for and say nothing about it.
    """

    @pytest.mark.parametrize("blank", ["", "  ", "\t\n", "   "])
    def test_blank_agent_refuses_before_either_door_acts(
        self, blank, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """Every blank spelling refuses identically, and nothing is created.

        INVERT: restore either door's old guard and ``""`` walks past both into a
        materialized box whose agent came from the cascade.
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.errors import ConfigError, KanibakoError
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        store_checked = {"v": False}

        def spy_store(*a, **kw):  # door one must never be reached with a blank
            store_checked["v"] = True
            return None

        monkeypatch.setattr(
            "kanibako.commands.box._parser._check_persona_store_for_create",
            spy_store,
        )
        seed_called = {"v": False}
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: seed_called.__setitem__("v", True),
        )

        with pytest.raises(ConfigError) as excinfo:
            run_create(_create_args(tmp_home / "project", agent=blank))

        # The refusal is the GRAMMAR's, on a KanibakoError that cli.py flattens to
        # a single ``Error: …`` line — not a second spelling of the rule here.
        assert isinstance(excinfo.value, KanibakoError)
        assert str(excinfo.value) == "agent ref is empty"
        assert store_checked["v"] is False
        assert seed_called["v"] is False
        std = load_std_paths(load_config(config_file))
        assert not std.boxes.exists() or not any(std.boxes.iterdir())
        assert journal.read_journal(std.journal) == {}

    def test_both_doors_read_the_same_normalized_ref(
        self, config_file, tmp_home, credentials_dir, monkeypatch
    ):
        """A ref with surrounding whitespace reaches BOTH doors as ONE value.

        The persist used to strip on its own while the store check saw the raw
        flag — two normalizations, so the box could be checked under one spelling
        and configured under another.  Asserted as the RULE (what door one saw ==
        what door two wrote), never against a literal, so a change in HOW the ref
        is normalized keeps the pin meaningful.
        """
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import load_std_paths

        seen: dict[str, str] = {}

        def spy_store(agent_ref, project_path):
            seen["checked"] = agent_ref
            return None

        monkeypatch.setattr(
            "kanibako.commands.box._parser._check_persona_store_for_create",
            spy_store,
        )
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: seen.__setitem__(
                "seeded", kw.get("explicit_agent")
            ),
        )

        assert run_create(
            _create_args(tmp_home / "project", name="spaced", agent="  goose  ")
        ) == 0

        std = load_std_paths(load_config(config_file))
        persisted = load_doc(
            std.boxes / "spaced" / "box.yaml"
        )["pref"]["system"]["agent"]
        assert seen["checked"] == persisted == seen["seeded"]
        # And it is a ref the grammar accepts, so the box is configured for a name
        # the cascade can actually resolve.
        assert parse_agent_ref(persisted)[0] == "goose"

    def test_the_two_doors_spell_given_the_same_way(self) -> None:
        """STRUCTURAL twin: every ``_agent_arg`` guard in ``run_create`` is the
        SAME expression.

        The behavioral pins above catch today's disagreement; this one catches the
        NEXT one, whatever value it happens to disagree about.  Asserted as mutual
        agreement rather than against an expected spelling — the rule is "one
        question", not "this question" — and it reds on emptiness (P15): a
        ``run_create`` that guards on ``_agent_arg`` nowhere fails here.
        """
        import inspect

        from kanibako.commands.box._parser import run_create

        fn = ast.parse(inspect.getsource(run_create)).body[0]
        guards = [
            n.test for n in ast.walk(fn)
            if isinstance(n, ast.If)
            and any(
                isinstance(s, ast.Name) and s.id == "_agent_arg"
                for s in ast.walk(n.test)
            )
        ]
        assert len(guards) >= 2, (
            "run_create must guard BOTH the persona store check and the "
            "pref.system.agent persist on _agent_arg; found "
            f"{len(guards)} such guard(s)."
        )
        shapes = {ast.dump(g) for g in guards}
        assert len(shapes) == 1, (
            "The _agent_arg guards in run_create ask "
            f"{len(shapes)} different questions: {sorted(shapes)}. They must ask "
            "ONE — whether --agent was GIVEN. Truthiness and .strip() truthiness "
            "are different predicates, and an all-whitespace --agent used to "
            "clear the first and be dropped by the second."
        )


# ---------------------------------------------------------------------------
# The create flag classes: every create flag is shaping or subject
# ---------------------------------------------------------------------------

def _create_parsers() -> "dict[str, argparse.ArgumentParser]":
    """The two parsers a user can spell ``create`` with, off the REAL CLI tree.

    Built through :func:`build_parser` on purpose: the blanket ``--agent`` /
    ``--box`` flags are injected only once the whole tree exists, so a parser
    reached any other way is missing part of the create surface.
    """
    from kanibako.cli import build_parser

    def sub(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action
        raise AssertionError(f"no subparsers under {parser.prog!r}")

    root = sub(build_parser())
    return {
        "create": root.choices["create"],
        "box create": sub(root.choices["box"]).choices["create"],
    }


def _advertised_flags(
    parser: argparse.ArgumentParser,
) -> "tuple[set[str], set[str], set[str]]":
    """A create parser's advertised (dests, option strings), plus the dests it
    SUPPRESSES — its user surface and exactly what was held out of it.

    One thing is dropped outright: argparse's own ``-h`` is machinery rather
    than a create flag, so it goes by TYPE.  A ``help=argparse.SUPPRESS`` action
    is NOT dropped silently — it is REPORTED in the third set, because
    suppression on its own says nothing about whether a flag reaches the
    command (``commands/helper_cmd.py`` hides a live, functional surface that
    way).  The caller pins that set to a named list; this walk decides nothing.
    """
    dests: set[str] = set()
    options: set[str] = set()
    suppressed: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if action.help is argparse.SUPPRESS:
            suppressed.add(action.dest)
            continue
        dests.add(action.dest)
        options.update(action.option_strings)
    return dests, options, suppressed


class TestCreateFlagsAreClassified:
    """Every flag ``create`` advertises is SHAPING or SUBJECT, on BOTH spellings.

    The recovery refusal turns on that partition — shaping flags cannot be
    honored on a replay because attempt one already wrote the box's state and
    the journal records the intent, not the arguments — so a create flag nobody
    classified is a flag the refusal silently ignores.  Asserted against the
    parser, never against an inventory: adding a flag to ``create`` without
    filing it reds here.

    Together the two tests pin the chain ``dests(box create) == dests(create) ==
    shaping | subject | {"recover"}``.
    """

    def test_every_advertised_create_flag_is_classified(self) -> None:
        from kanibako.commands.box._parser import (
            _CREATE_SHAPING_FLAGS,
            _CREATE_SUBJECT_FLAGS,
        )

        shaping, subject = set(_CREATE_SHAPING_FLAGS), set(_CREATE_SUBJECT_FLAGS)
        assert not shaping & subject, (
            f"a create flag is in BOTH classes: {sorted(shaping & subject)}. "
            "The refusal reads the two as a partition; a member of both makes "
            "'is this flag refused on a recovery' unanswerable."
        )
        assert "recover" not in shaping | subject, (
            "--recover selects the behavior the classes are consulted FOR; "
            "it is not itself a create flag to be classified."
        )

        dests, _, suppressed = _advertised_flags(_create_parsers()["box create"])
        # ⚑ P15: the parser lookup supplying nothing must RED, not compare two
        # empty sets and report a partition that was never checked.
        assert dests, (
            "no advertised flags found on 'box create' — the parser walk is "
            "broken, so this pin proves nothing about the classification."
        )
        # ⚑ The ONE exclusion from the partition below, bounded and named. It is
        # not a carve-out for "hidden": see the message.
        assert suppressed == {"box"}, (
            "the help=SUPPRESS flags on 'box create' are not the reviewed set.\n"
            f"  unreviewed suppressed flag(s): {sorted(suppressed - {'box'})}\n"
            f"  expected but absent:           {sorted({'box'} - suppressed)}\n"
            "'box' is exempt from the classification NOT because it is hidden "
            "but because it can never reach run_create: 'create' and 'box "
            "create' are absent from BOX_FLAG_COMMANDS, so a typed --box makes "
            "check_flag_relevance raise FlagRelevanceError, which main() prints "
            "and turns into sys.exit(2) BEFORE any func() dispatch "
            "(commands/flags.py, cli.py). Suppression alone proves nothing — "
            "commands/helper_cmd.py hides a live, functional 'helper register' "
            "the same way. A new suppressed flag must be SHOWN to be refused "
            "before dispatch to be added here; if it does reach run_create, "
            "file it in _CREATE_SHAPING_FLAGS or _CREATE_SUBJECT_FLAGS instead."
        )
        assert dests == shaping | subject | {"recover"}, (
            "'box create' flags and the create flag classes disagree.\n"
            f"  unclassified on the parser: {sorted(dests - (shaping | subject | {'recover'}))}\n"
            f"  classified but not a flag:  {sorted((shaping | subject) - dests)}\n"
            "File a new flag in _CREATE_SHAPING_FLAGS if it initializes stored "
            "box state, else in _CREATE_SUBJECT_FLAGS (commands/box/_parser.py)."
        )

    def test_both_create_spellings_declare_the_same_flags(self) -> None:
        """``create`` and ``box create`` are separate parsers; keep them equal.

        ``--rig`` was on ``box create`` alone, so the preferred spelling of
        ``--image`` did not exist on the shortcut most users type.
        """
        parsers = _create_parsers()
        top_dests, top_options, _ = _advertised_flags(parsers["create"])
        box_dests, box_options, _ = _advertised_flags(parsers["box create"])
        assert top_options and box_options, (
            "one create parser advertises no options at all — the walk is "
            "broken and the parity below is vacuous."
        )
        assert top_dests == box_dests, (
            f"only on 'create': {sorted(top_dests - box_dests)}; "
            f"only on 'box create': {sorted(box_dests - top_dests)}"
        )
        assert top_options == box_options, (
            f"only on 'create': {sorted(top_options - box_options)}; "
            f"only on 'box create': {sorted(box_options - top_options)}. "
            "The two parsers declare their flags separately (cli.py and "
            "commands/box/_parser.py); every create flag needs both."
        )


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
        from kanibako.project import registry_store
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

        from kanibako.project import registry_store
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

    def test_named_half_create_refuses_then_recover_completes(
        self, config_file, tmp_home, credentials_dir, monkeypatch, capsys
    ):
        """A ``--name`` box interrupted mid-create: the I4 guard must NOT call it
        orphaned metadata, and ``create --recover`` must finish it.

        Pinned in two halves because they answer different questions.  The
        refusal must name the pending attempt rather than the box home (which is
        exactly what the I4 orphan wording would claim); the recovery must then
        complete under the name attempt one chose, which is the whole reason
        ``--name`` is refused on a replay.
        """
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
        capsys.readouterr()

        # Re-typing `create --name <same>` refuses and NAMES THE PENDING ATTEMPT,
        # never the I4 orphan wording that would send the user to `rm -rf` their
        # own half-built box.
        assert run_create(_create_args(path, name="halfbox")) == 1
        err = capsys.readouterr().err
        assert "an interrupted 'create' is pending" in err
        assert "orphaned metadata" not in err
        assert "--name" in err
        assert journal.pending_create(std.journal, box_key) is not None

        # `create --recover` — no shaping flag — completes under that same name.
        assert run_create(
            _create_args(path, recover=True, no_vault=False)
        ) == 0
        assert "halfbox" in _primary_names(std)
        assert journal.pending_create(std.journal, box_key) is None


# ---------------------------------------------------------------------------
# The recovery REFUSALS (A-E), PRIMARY and STANDALONE
# ---------------------------------------------------------------------------

class TestPendingCreateRefusal:
    """A ``create`` on a path carrying a pending create entry REFUSES, and so
    does a ``--recover`` with nothing to recover.

    Each refusal is pinned three ways, because a refusal that changed the disk or
    consumed the journal entry would be a refusal that destroyed the evidence it
    names — the very thing the user is being told to go and inspect:

    * ``rc == 1`` and the refusal reaches stderr,
    * the box tree is byte-for-byte what it was (``rglob`` snapshot),
    * the journal entry is still exactly what it was — pending for A/B/C, and
      never minted for D/E.

    The five cases, both modes:

    ======  ======================================================  =================
    case    invocation                                              refusal
    ======  ======================================================  =================
    A       ``create <path>``                                       pending, named
    B       ``create <path> --image X``                             pending + the flag
    C       ``create --recover <path> --image X``                   re-run without it
    D       ``create --recover <path>`` on a COMPLETE box           nothing to recover
    E       ``create --recover <path>`` with no box at all          no interrupted create
    ======  ======================================================  =================
    """

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        # The home seed runs AFTER the refusal (and never for a refused create) —
        # stub it so these are fast + fs-deterministic, matching the sibling suite.
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )

    def _std(self, config_file):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths
        return load_std_paths(load_config(config_file))

    @staticmethod
    def _disk(*roots: Path) -> "dict[str, int]":
        """Every path under each of *roots* → its size; the "nothing was touched" snapshot.

        Both roots always: a STANDALONE box keeps its tree under the box ROOT
        (``<root>/box_data``) and a PRIMARY one under ``std.boxes``, so a snapshot
        of ``std.boxes`` alone would be vacuous for half the parametrization.
        """
        snapshot: dict[str, int] = {}
        for root in roots:
            if not root.exists():
                continue
            snapshot.update({
                f"{root}::{p.relative_to(root)}": p.stat().st_size
                for p in sorted(root.rglob("*"))
            })
        return snapshot

    def _interrupted(self, config_file, tmp_home, standalone):
        """A half-built box + pending entry, and the box key it hangs off."""
        from kanibako.settings.config import load_config

        config = load_config(config_file)
        std = self._std(config_file)
        path = tmp_home / ("sa" if standalone else "project")
        if standalone:
            path.mkdir()
        proj = _simulate_interrupted_create(
            std, config, standalone=standalone, path=path, register_box=False,
        )
        assert journal.pending_create(std.journal, _box_journal_key(proj))
        return std, path, _box_journal_key(proj)

    def _complete_box(self, config_file, tmp_home, standalone):
        """A box that finished its create: no pending entry anywhere."""
        from kanibako.commands.box._parser import run_create

        std = self._std(config_file)
        path = tmp_home / ("sa" if standalone else "project")
        if standalone:
            path.mkdir()
        assert run_create(
            _create_args(
                path, standalone=standalone, no_vault=False,
                register=standalone,
            )
        ) == 0
        assert journal.read_journal(std.journal) == {}
        return std, path

    @pytest.mark.parametrize("standalone", [False, True], ids=["primary", "standalone"])
    def test_bare_create_refuses_and_names_the_pending_attempt(
        self, standalone, config_file, tmp_home, credentials_dir, capsys
    ):
        """Case A — the DEFAULT case: a bare re-run is what the ratification turned
        from rc 0 to rc 1.  It names the box, the workspace and when the attempt
        started, and hands back the one command that does finish it."""
        from kanibako.commands.box._parser import run_create

        std, path, box_key = self._interrupted(config_file, tmp_home, standalone)
        before = self._disk(std.boxes, path)
        capsys.readouterr()

        assert run_create(
            _create_args(path, standalone=standalone, no_vault=False)
        ) == 1

        err = capsys.readouterr().err
        assert err.startswith("Error: ")
        assert "an interrupted 'create' is pending" in err
        assert "kanibako box diagnose" in err
        # END-OF-LINE anchored — see the note in case C.
        sa = " --standalone" if standalone else ""
        assert f"kanibako create{sa} --recover {path}" in _cure_lines(err, "create")
        # Untouched: disk identical, entry still pending for the user to finish.
        assert self._disk(std.boxes, path) == before
        assert journal.pending_create(std.journal, box_key) is not None

    @pytest.mark.parametrize("standalone", [False, True], ids=["primary", "standalone"])
    def test_shaping_flags_refused_by_name_with_an_after_the_fact_cure(
        self, standalone, config_file, tmp_home, credentials_dir, capsys
    ):
        """Case B — a SHAPING flag on the re-run is refused BY NAME: attempt one
        already wrote this box's settings, so the flag can be neither honored nor
        compared.  The refusal names the flags AND the ``box set`` key that changes
        each one afterwards."""
        from kanibako.commands.box._parser import run_create

        std, path, box_key = self._interrupted(config_file, tmp_home, standalone)
        before = self._disk(std.boxes, path)
        capsys.readouterr()

        assert run_create(
            _create_args(
                path, standalone=standalone, no_vault=False,
                image="ubuntu:24.04", agent="goose",
            )
        ) == 1

        err = capsys.readouterr().err
        assert "an interrupted 'create' is pending" in err
        # The specific flags, in _CREATE_SHAPING_FLAGS order — never the value.
        assert "--image, --agent" in err
        assert "ubuntu:24.04" not in err and "goose" not in err
        # Both have a `box set` spelling, so both get an after-the-fact cure.
        assert "Or change them afterwards:" in err
        assert "kanibako box set --box" in err
        assert "box.image=<value>" in err
        assert "pref.system.agent=<value>" in err
        assert self._disk(std.boxes, path) == before
        assert journal.pending_create(std.journal, box_key) is not None

    @pytest.mark.parametrize("standalone", [False, True], ids=["primary", "standalone"])
    def test_recover_with_a_shaping_flag_refused_as_re_run_without_it(
        self, standalone, config_file, tmp_home, credentials_dir, capsys
    ):
        """Case C — ``--recover`` does not buy a SHAPING flag its way past the
        refusal.  ``--recover`` is not a create flag to be classified (it selects
        the behavior the classes are consulted FOR), so the refusal collapses to
        the one command that does work."""
        from kanibako.commands.box._parser import run_create

        std, path, box_key = self._interrupted(config_file, tmp_home, standalone)
        before = self._disk(std.boxes, path)
        capsys.readouterr()

        assert run_create(
            _create_args(
                path, standalone=standalone, recover=True, no_vault=False,
                image="ubuntu:24.04",
            )
        ) == 1

        err = capsys.readouterr().err
        assert "--image" in err
        assert "Re-run without them:" in err
        # The cure is the one command, and it carries no flag.  END-OF-LINE
        # anchored: a bare ``... --recover {path}`` match is a SUBSTRING of the
        # standalone root's ``... --recover {path}/workspace``, which is a cure that
        # does not run.
        sa = " --standalone" if standalone else ""
        assert f"kanibako create{sa} --recover {path}" in _cure_lines(err, "create")
        assert self._disk(std.boxes, path) == before
        assert journal.pending_create(std.journal, box_key) is not None

    @pytest.mark.parametrize("standalone", [False, True], ids=["primary", "standalone"])
    def test_recover_on_a_complete_box_refuses_as_nothing_to_recover(
        self, standalone, config_file, tmp_home, credentials_dir, capsys
    ):
        """Case D — ``--recover`` on a box that is already complete refuses and
        points at ``start``: there is no half-finished attempt, and the user asked
        to finish one.  Nothing is minted, so the journal stays empty."""
        from kanibako.commands.box._parser import run_create

        std, path = self._complete_box(config_file, tmp_home, standalone)
        before = self._disk(std.boxes, path)
        capsys.readouterr()

        assert run_create(
            _create_args(path, standalone=standalone, recover=True, no_vault=False)
        ) == 1

        err = capsys.readouterr().err
        assert "--recover found nothing to recover" in err
        assert str(path) in err
        # END-OF-LINE anchored — see the note in case C.
        assert f"kanibako start {path}" in _cure_lines(err, "start")
        assert self._disk(std.boxes, path) == before
        assert journal.read_journal(std.journal) == {}

    @pytest.mark.parametrize("standalone", [False, True], ids=["primary", "standalone"])
    def test_recover_with_no_interrupted_create_refuses_as_no_interrupted_create(
        self, standalone, config_file, tmp_home, credentials_dir, capsys
    ):
        """Case E — ``--recover`` where nothing was ever started refuses with the
        inspect-first line and a plain ``create``: the flag is a request to finish
        a specific attempt, and there is no attempt and no box."""
        from kanibako.commands.box._parser import run_create

        std = self._std(config_file)
        path = tmp_home / ("sa" if standalone else "project")
        if standalone:
            path.mkdir()
        before = self._disk(std.boxes, path)
        capsys.readouterr()

        assert run_create(
            _create_args(path, standalone=standalone, recover=True, no_vault=False)
        ) == 1

        err = capsys.readouterr().err
        assert "--recover found no interrupted 'create'" in err
        assert str(path) in err
        assert "kanibako box diagnose" in err
        # END-OF-LINE anchored, and it carries the mode: without ``--standalone``
        # this line builds a PRIMARY box named after the workspace subdirectory.
        sa = " --standalone" if standalone else ""
        assert f"kanibako create{sa} {path}" in _cure_lines(err, "create")
        # ⚑ THE PRE-FLIGHT HALF: the refusal ran before the materialize, so no box
        # tree was built for the very invocation that refused to build one.
        assert self._disk(std.boxes, path) == before
        assert journal.read_journal(std.journal) == {}
        assert (std.boxes / "project").exists() is False
        assert (std.boxes / "project2").exists() is False


class TestPreJournalForkRefused:
    """A crash BEFORE the write-ahead entry leaves a ``std.boxes/<basename>`` dir
    that no membership and no journal entry claims — and there is then nothing for
    ``--recover`` to find.  The picker steps over it and mints ``<basename>2``,
    splitting one workspace across two boxes and leaving the first half-written,
    so the bare PRIMARY create refuses instead.

    ``create --name`` reaches the same condition and refuses it with the same
    wording (see ``test_create_over_orphaned_metadata_refused``); this pins the
    spelling the user cannot type a ``--name`` for.
    """

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )

    def test_bare_create_over_an_unclaimed_box_home_refuses(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        path = tmp_home / "project"  # the tmp_home fixture made it and chdir'd in

        # THE CRASH WINDOW: the deferred resolve materialized boxes/project, and
        # the process died BEFORE `_write_create_entry` — so no journal entry.
        proj = resolve_project(
            std, config, project_dir=str(path), initialize=True, register=False,
        )
        assert (std.boxes / "project").is_dir()
        assert journal.pending_create(
            std.journal, str(Path(proj.shell_path).parent)
        ) is None
        before = {
            str(p.relative_to(std.boxes)): p.stat().st_size
            for p in sorted(std.boxes.rglob("*"))
        }
        capsys.readouterr()

        assert run_create(_create_args(path, no_vault=False)) == 1

        err = capsys.readouterr().err
        assert "already has orphaned metadata" in err
        assert str(std.boxes / "project") in err
        # NO second tree: the picker did not get to mint 'project2', and the
        # first one is byte-for-byte what the crash left.
        assert sorted(p.name for p in std.boxes.iterdir()) == ["project"]
        assert {
            str(p.relative_to(std.boxes)): p.stat().st_size
            for p in sorted(std.boxes.rglob("*"))
        } == before
        assert _primary_names(std) == {}


class TestCuresAreRunnable:
    """Every cure a ``create`` refusal prints is a command that RUNS.

    A cure is the whole point of a refusal, and a string match cannot tell a
    runnable one from an unreadable one: a STANDALONE box's journal records its
    resolved ``<root>/workspace``, so naming that as the re-entry path reads like
    a plausible cure while ``create`` treats the subdirectory as a fresh PRIMARY
    workspace.  So each case is pinned twice — the FULL line, END-OF-LINE
    anchored, and the line handed to the SHIPPED parser and executed.
    """

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )

    def _std(self, config_file):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths
        return load_std_paths(load_config(config_file))

    def _config(self, config_file):
        from kanibako.settings.config import load_config
        return load_config(config_file)

    def _root(self, tmp_home, standalone):
        path = tmp_home / ("sa" if standalone else "project")
        if standalone:
            path.mkdir()
        return path

    def _interrupted(self, config_file, tmp_home, standalone):
        std = self._std(config_file)
        path = self._root(tmp_home, standalone)
        proj = _simulate_interrupted_create(
            std, self._config(config_file), standalone=standalone, path=path,
            register_box=False,
        )
        return std, path, _box_journal_key(proj)

    @pytest.mark.parametrize("standalone", [False, True], ids=["primary", "standalone"])
    @pytest.mark.parametrize(
        "given", [{"image": "ubuntu:24.04"}, {}], ids=["flag_refused", "bare_rerun"],
    )
    def test_the_pending_create_cure_completes_the_box(
        self, standalone, given, config_file, tmp_home, credentials_dir, capsys
    ):
        """A/B and C: the cure is the re-entry at the path the user named, and
        RUNNING it finishes the interrupted attempt — entry cleared, and the box
        registered exactly where that mode registers at all."""
        from kanibako.commands.box._parser import run_create
        from kanibako.project import registry_store

        std, path, box_key = self._interrupted(config_file, tmp_home, standalone)
        capsys.readouterr()

        assert run_create(_create_args(
            path, standalone=standalone, no_vault=False, **given,
        )) == 1
        err = capsys.readouterr().err
        sa = " --standalone" if standalone else ""
        assert f"kanibako create{sa} --recover {path}" in _cure_lines(err, "create")

        assert _run_printed_cure(err, "create") == 0
        assert journal.pending_create(std.journal, box_key) is None
        assert journal.read_journal(std.journal) == {}
        if standalone:
            assert (path / "box_data").is_dir()
            assert _primary_names(std) == {}
            assert registry_store.load_standalone(std.registry) == {}
        else:
            assert path.name in _primary_names(std)

    def test_the_nothing_to_recover_cure_is_a_legal_start_of_this_box(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """D: ``--recover`` on a COMPLETE standalone box points ``start`` at the box
        ROOT.  Pinned as a PARSE, because running ``start`` would launch a
        container instead of exercising the spelling: the line must be a legal
        command naming that box, which is what the user is being sent to."""
        from kanibako.cli import build_parser
        from kanibako.commands.box._parser import run_create
        from kanibako.commands.start import _clear_create_entry, _register_new_box

        std = self._std(config_file)
        root = self._root(tmp_home, standalone=True)
        proj = _simulate_interrupted_create(
            std, self._config(config_file), standalone=True, path=root,
            register_box=False,
        )
        _register_new_box(std, proj)
        _clear_create_entry(std, proj)
        assert journal.read_journal(std.journal) == {}
        capsys.readouterr()

        assert run_create(
            _create_args(root, standalone=True, recover=True, no_vault=False)
        ) == 1
        err = capsys.readouterr().err
        assert f"kanibako start {root}" in _cure_lines(err, "start")
        parsed = build_parser().parse_args(_printed_cure(err, "start"))
        assert parsed.project == str(root)
        assert (root / "box_data").is_dir()

    def test_the_no_vault_cure_turns_the_vault_off(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """B with ``--no-vault``: the cure is a ``box set`` that RUNS and leaves the
        box's ``enable_vault`` FALSE — the value ``--no-vault`` itself asked for.

        Asserting the printed string is what pinned the constant backwards once:
        ``box.enable_vault=true`` reads as a plausible cure and sets the exact
        opposite of the flag.  So the pin is the box's own resolved value after
        the cure has been executed, read back through the shipped ``box get``
        door rather than off the settings file the cure happened to write.

        Built on the REGISTER->CLEAR crash window (registered + stale entry)
        because ``box set`` resolves its subject through the registry, and an
        unregistered half-create has no entry to resolve yet.
        """
        from kanibako.cli import build_parser
        from kanibako.commands.box._parser import run_create
        from kanibako.commands.start import _box_journal_key

        def _door(*argv):
            """RUN one argv through the shipped parser; return its rc."""
            return build_parser().parse_args(list(argv)).func(
                build_parser().parse_args(list(argv))
            )

        std = self._std(config_file)
        path = self._root(tmp_home, standalone=False)
        proj = _simulate_interrupted_create(
            std, self._config(config_file), standalone=False, path=path,
            register_box=True,
        )
        assert journal.pending_create(std.journal, _box_journal_key(proj))
        capsys.readouterr()

        # The sparse default: not set, which resolves to a vault that is ENABLED.
        get = ("box", "get", "--box", "project", "box.enable_vault")
        assert _door(*get) == 0
        assert "(not set)" in capsys.readouterr().err

        assert run_create(
            _create_args(path, standalone=False, no_vault=True)
        ) == 1
        err = capsys.readouterr().err
        assert "--no-vault" in err
        assert "kanibako box set --box project box.enable_vault=false" in err

        # ⚑ RUN THE PRINTED CURE, then read the box's own value back.  ``box get``
        # answers a SET value on stdout and an absent one on stderr, so each read
        # is taken from the stream the door actually used.
        cure = _printed_cure(err, "box set")
        assert cure == ["box", "set", "--box", "project", "box.enable_vault=false"]
        assert _door(*cure) == 0
        capsys.readouterr()
        assert _door(*get) == 0
        assert capsys.readouterr().out.strip() == "false"

    def test_the_no_interrupted_create_cure_builds_the_standalone_box(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """E: with no attempt on record the cure is a plain standalone create at the
        ROOT.  RUN, it produces that box's ``box_data`` in place and mints no
        primary box — where the same path spelled without ``--standalone`` builds a
        PRIMARY box named after the workspace subdirectory instead."""
        from kanibako.commands.box._parser import run_create

        std = self._std(config_file)
        root = self._root(tmp_home, standalone=True)
        capsys.readouterr()

        assert run_create(
            _create_args(root, standalone=True, recover=True, no_vault=False)
        ) == 1
        err = capsys.readouterr().err
        assert f"kanibako create --standalone {root}" in _cure_lines(err, "create")

        assert _run_printed_cure(err, "create") == 0
        assert (root / "box_data").is_dir()
        assert _primary_names(std) == {}
        assert journal.read_journal(std.journal) == {}


class TestBareCreateOverADeregisteredHome:
    """A bare PRIMARY ``create`` steps OVER a ``std.boxes/<basename>`` that a
    DEREGISTERED entry still claims — ``rm`` without ``--purge`` dropped the
    membership and kept the data — and mints the next free name beside it.

    The refusal D4 owes is owed to a home that NOTHING claims: no membership, no
    journal entry, and no deregistered entry either.  A deregistered entry IS a
    claim, and the box it names is one the user can readopt, so a plain re-run
    must not become a deletion decision about it.  Pinned on the SAME workspace
    and on ANOTHER one with the same basename, because in the second the name is
    the only thing the two share.
    """

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(
            "kanibako.commands.start.seed_new_box",
            lambda std, config, proj, **kw: None,
        )

    def _deregistered(self, config_file, where):
        """A created box, then ``rm`` without ``--purge`` — a retained home."""
        from kanibako.commands.box._parser import run_create, run_rm
        from kanibako.project import registry_store
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        where.mkdir(parents=True)
        assert run_create(_create_args(where)) == 0
        std = load_std_paths(load_config(config_file))
        sentinel = std.boxes / where.name / "home" / "KEEP.txt"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("retained-box-data")
        assert run_rm(
            argparse.Namespace(target=where.name, purge=False, force=False)
        ) == 0
        assert registry_store.lookup_deregistered(std.registry, where.name) is not None
        return std, sentinel

    @pytest.mark.parametrize("same_workspace", [True, False], ids=["same", "other"])
    def test_bare_create_steps_over_a_deregistered_home_and_mints_the_next_name(
        self, same_workspace, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.commands.box._parser import run_create
        from kanibako.project import registry_store

        # SAME: the re-create runs where the box was.  OTHER: a different workspace
        # of the same basename, so the name is all the two share.
        first = tmp_home / "wsA" / "dup"
        std, sentinel = self._deregistered(config_file, first)
        second = first if same_workspace else tmp_home / "wsB" / "dup"
        second.mkdir(parents=True, exist_ok=True)
        capsys.readouterr()

        # rc 0, and the name it mints is the NEXT free one beside the claimed home.
        assert run_create(_create_args(second)) == 0
        assert "Error:" not in capsys.readouterr().err

        assert _primary_names(std) == {"dup2": str(second)}
        assert (std.boxes / "dup2").is_dir()

        # ⚑ THE DATA-LOSS HALF: the create neither refused nor merged into the
        # retained home, and the deregistered entry is untouched — the user can
        # still readopt 'dup' and get their data back.
        assert sentinel.read_text() == "retained-box-data"
        assert registry_store.lookup_deregistered(std.registry, "dup") is not None
        assert sorted(p.name for p in std.boxes.iterdir()) == ["dup", "dup2"]

    def test_a_genuine_orphan_is_still_refused_and_never_offered_for_deletion(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """The picker's OTHER branch is untouched by sharing the carrier: a home no
        membership, no deregistered entry and no journal entry claims is the
        orphan, and that is the one the bare create refuses — a silent ``<name>2``
        fork is what the refusal is for.

        Its CURE is the subject, though.  The registry is a derived, rebuildable
        index, so an unclaimed home is not established to be junk: it may be a
        complete box whose registration was lost.  So the message names the
        directory, says so, and offers no way to delete it — every verb it prints
        is RUN here, and the one that gets the box back is restoring the index.
        """
        import shlex
        import subprocess

        from kanibako.cli import build_parser
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        # A home no membership, no deregistered entry and no journal entry claims.
        # The bare create meets it by BASENAME, so the workspace is named to match.
        elsewhere = tmp_home / "orphan"
        elsewhere.mkdir()
        orphan = std.boxes / elsewhere.name
        (orphan / "home").mkdir(parents=True)
        (orphan / "home" / "KEEP.txt").write_text("orphaned")
        capsys.readouterr()

        assert run_create(_create_args(elsewhere)) == 1
        err = capsys.readouterr().err
        assert "orphaned metadata" in err
        assert str(orphan) in err

        # ⚑ NO DELETION IS EVER SUGGESTED.  A ``rm -rf`` or ``--purge`` spelled here
        # is advice to destroy a box the user may still get back.
        assert "rm -rf" not in err
        assert "--purge" not in err
        assert not [ln for ln in err.splitlines() if ln.strip().startswith("rm ")]
        # It says what the directory may be, and where the work would be.
        assert "COMPLETE box whose registration was lost" in err
        assert f"{orphan / 'home'}" in err

        # ⚑ EVERY PRINTED COMMAND RUNS ON THIS STATE.  ``box list`` is the index
        # that lost the entry, so it is the diagnosis; ``ls`` is the only way to
        # see what the unclaimed directory holds.  The restore-the-index cure is
        # prose, so these two are the WHOLE command set.
        cures = [ln.strip() for ln in err.splitlines() if ln.startswith("  ")]
        assert cures == ["kanibako box list", f"ls {orphan}"]

        parsed = build_parser().parse_args(shlex.split("box list"))
        assert parsed.func(parsed) == 0
        assert subprocess.run(  # noqa: S603 - the message's own line, verbatim
            shlex.split(f"ls {orphan}"), capture_output=True,
        ).returncode == 0

        # Untouched throughout: still one unclaimed home, data intact.
        assert (orphan / "home" / "KEEP.txt").read_text() == "orphaned"
        assert sorted(p.name for p in std.boxes.iterdir()) == ["orphan"]
        assert _primary_names(std) == {}

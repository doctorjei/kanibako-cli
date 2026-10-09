"""CLI-level tests for the lifecycle entry points (remap / move / convert).

These exercise the thin ``run_remap`` / ``run_move`` / ``run_convert`` wrappers
(arg parsing + friendly errors) on top of the Phase-1 engine.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path

import pytest

from kanibako.cli import build_parser
from kanibako.commands.box import _lifecycle
from kanibako.commands.box._lifecycle import (
    _BARE_MOVE,
    resolve_lifecycle_target,
    run_convert,
    run_move,
    run_remap,
)
from kanibako.settings.config import load_config
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.paths import _early_scope, load_primary_boxes
from kanibako.settings.paths import (
    BoxMode,
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.utils import project_hash
from kanibako.project.workset import add_project, create_workset, load_workset


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _connected_index(std):
    """Reconstruct the ``{external_path: {workset, project}}`` connection view.

    D10 replacement for the retired global ``connected:`` index: a connected box
    is a NAMED workset's per-workset ``boxes:`` entry whose path is EXTERNAL
    (outside that workset root).  Mirrors the old ``_load_connected`` shape.
    """
    from pathlib import Path

    from kanibako.project import registry_store, workset_registry
    from kanibako.settings.config_io import load_doc

    out = {}
    for name, root_str in registry_store.load_section(
        std.registry, "worksets"
    ).items():
        root = Path(root_str)
        registry_path = workset_registry.resolve_workset_registry_path(
            root, load_doc(root / "workset.yaml"), early=_early_scope(std, BoxMode.named, name),
        )
        for box_name, box_path in workset_registry.load_workset_boxes(
            registry_path
        ).items():
            resolved = Path(box_path).resolve()
            try:
                resolved.relative_to(root.resolve())
                continue
            except ValueError:
                out[str(resolved)] = {"workset": name, "project": box_name}
    return out

@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    config = load_config(config_file)
    std = load_std_paths(config)
    return config, std, tmp_home


def _default(env, name="proj", contents="hi"):
    config, std, tmp_home = env
    pdir = tmp_home / name
    pdir.mkdir()
    (pdir / "file.txt").write_text(contents)
    resolve_project(std, config, project_dir=str(pdir), initialize=True)
    return pdir


def _standalone(env, name="sa"):
    config, std, tmp_home = env
    pdir = tmp_home / name
    pdir.mkdir()
    (pdir / "file.txt").write_text("x")
    resolve_standalone_project(std, config, project_dir=str(pdir), initialize=True)
    return pdir


def _remap_args(old, new=None, force=True):
    return argparse.Namespace(
        old=str(old) if old is not None else None,
        new=str(new) if new is not None else None,
        force=force,
    )


def _move_args(old, new, *, force=True, to_default=False, to_standalone=False,
               to_workset=None, name=None):
    return argparse.Namespace(
        old=str(old) if old is not None else None,
        new=str(new) if new is not None else None,
        force=force, to_default=to_default, to_standalone=to_standalone,
        to_workset=to_workset, name=name,
    )


def _convert_args(old=None, *, force=True, to_default=False, to_standalone=False,
                  to_workset=None, move=None, name=None):
    return argparse.Namespace(
        old=str(old) if old is not None else None,
        force=force, to_default=to_default, to_standalone=to_standalone,
        to_workset=to_workset, move=move, name=name,
    )


# ---------------------------------------------------------------------------
# remap
# ---------------------------------------------------------------------------

class TestRemap:
    def test_remap_internal_default(self, env):
        """remap rewrites records to a new (already-present) path; no move."""
        config, std, tmp_home = env
        pdir = _default(env, contents="keep")
        # Simulate the user having moved the folder themselves.
        new = tmp_home / "moved_here"
        pdir.rename(new)

        # Reference the project by its (stale) old path; remap records the new.
        rc = run_remap(_remap_args(str(pdir), str(new)))
        # Resolve from the new path: old records should be gone, new present.
        assert rc == 0
        # File untouched (records-only).
        assert (new / "file.txt").read_text() == "keep"
        names = load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))
        assert str(new) in names.values()

        # P8b/Option A: the remapped workspace resolves from the registry, not an
        # on-disk ``resolved.workspace`` (no ``project:`` on disk).
        proj = resolve_project(std, config, project_dir=str(new), initialize=False)
        assert proj.project_path == new.resolve()
        assert proj.project_hash == project_hash(str(new.resolve()))
        assert "project" not in load_doc(proj.metadata_path / "box.yaml")

    def test_remap_external_repoint(self, env):
        """remap on an EXTERNAL-connected project repoints records, no move."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        external = tmp_home / "ext_repo"
        external.mkdir()
        (external / "f.txt").write_text("ext")
        add_project(ws, "ext", external, std)

        # User moved the external dir on disk.
        new_ext = tmp_home / "ext_repo_moved"
        external.rename(new_ext)

        # Reference by the (stale) old external path; remap repoints to new.
        rc = run_remap(_remap_args(str(external), str(new_ext)))
        assert rc == 0
        # Files preserved (never moved/deleted by remap).
        assert (new_ext / "f.txt").read_text() == "ext"
        # connected.yaml now points at the new external path.
        connected = _connected_index(std)
        assert str(new_ext.resolve()) in connected

    def test_remap_missing_project(self, env):
        config, std, tmp_home = env
        plain = tmp_home / "plain"
        plain.mkdir()
        rc = run_remap(_remap_args(str(plain), str(plain)))
        assert rc == 1


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------

class TestMove:
    def test_move_internal_relocate(self, env):
        config, std, tmp_home = env
        pdir = _default(env, contents="movedata")
        dest = tmp_home / "dest"
        rc = run_move(_move_args(pdir, dest))
        assert rc == 0
        assert dest.is_dir() and (dest / "file.txt").read_text() == "movedata"
        assert not pdir.exists()

    def test_move_plus_workset(self, env):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env)
        dest = tmp_home / "dest_ext"
        rc = run_move(_move_args(pdir, dest, to_workset="ws"))
        assert rc == 0
        ws2 = load_workset(ws.root, ws.name, early_system=std.early_system)
        assert any(p.name == "proj" for p in ws2.projects)

    def test_move_external_refused(self, env):
        """move refuses an external-connected project with a clear message."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        external = tmp_home / "ext"
        external.mkdir()
        add_project(ws, "ext", external, std)
        dest = tmp_home / "somewhere"
        rc = run_move(_move_args(str(external), dest))
        assert rc == 1
        # external dir untouched.
        assert external.is_dir()

    def test_move_requires_both(self, env):
        config, std, tmp_home = env
        rc = run_move(_move_args(str(tmp_home / "x"), None))
        assert rc == 1

    def test_move_into_foreign_workset_refusal_names_the_workset(self, env, capsys):
        """The membership refusal renders the workset's name, not a literal ``{ws_name}``."""
        config, std, tmp_home = env
        create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env)
        rc = run_move(_move_args(pdir, tmp_home / "ws_root" / "inside"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "Refusing to land the project inside workset 'ws'" in err
        assert "Use `--workset ws` to make it a member" in err
        assert "{ws_name}" not in err
        assert pdir.is_dir()

    def test_mv_alias_parses(self):
        parser = build_parser()
        args = parser.parse_args(["box", "mv", "/a", "/b"])
        assert args.box_command == "mv"
        assert args.func is run_move
        assert args.old == "/a" and args.new == "/b"


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------

class TestConvert:
    def test_convert_to_standalone_inplace(self, env):
        config, std, tmp_home = env
        pdir = _default(env)
        rc = run_convert(_convert_args(pdir, to_standalone=True))
        assert rc == 0
        # P8b/Option A: Drift I marker workset.yaml at the ROOT (materialized by
        # the sparse kuid write), but no on-disk ``project:`` identity — the
        # standalone box is registered in registry.standalone.
        from kanibako.project.registry_store import load_standalone
        assert "project" not in load_doc(pdir / "workset.yaml")
        assert (pdir / "workset.yaml").is_file()
        assert (pdir / "box_data").is_dir()
        assert any(root == str(pdir) for root in load_standalone(std.registry).values())

    def test_convert_to_default_inplace(self, env):
        config, std, tmp_home = env
        pdir = _standalone(env)
        rc = run_convert(_convert_args(pdir, to_default=True))
        assert rc == 0
        proj = resolve_project(std, config, project_dir=str(pdir), initialize=False)
        assert proj.metadata_path.parent == std.boxes
        # P8b/Option A: primary identity is the primary membership, not disk.
        assert proj.mode == BoxMode.primary
        assert "project" not in load_doc(proj.metadata_path / "box.yaml")
        assert str(pdir) in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary)).values()

    def test_convert_to_workset_inplace_external(self, env):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env)
        rc = run_convert(_convert_args(pdir, to_workset="ws"))
        assert rc == 0
        ws2 = load_workset(ws.root, ws.name, early_system=std.early_system)
        assert any(p.name == "proj" for p in ws2.projects)
        # P8b/Option A: the external workspace is recorded in the workset's
        # per-workset ``boxes:`` registry, not an on-disk ``resolved.workspace``.
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc
        reg = workset_registry.load_workset_boxes(
            workset_registry.resolve_workset_registry_path(
                ws.root, load_doc(ws.root / "workset.yaml"),
                early=_early_scope(std, BoxMode.named, ws.name),
            )
        )
        assert reg.get("proj") == str(pdir.resolve())

    def test_convert_move_path(self, env):
        config, std, tmp_home = env
        pdir = _default(env, contents="cm")
        dest = tmp_home / "convdest"
        rc = run_convert(_convert_args(pdir, to_standalone=True, move=str(dest)))
        assert rc == 0
        assert dest.is_dir()
        # Drift I: workset.yaml at the root; drift H: files in workspace/ subdir.
        assert (dest / "workset.yaml").is_file()
        assert (dest / "box_data").is_dir()
        assert (dest / "workspace" / "file.txt").read_text() == "cm"
        assert not pdir.exists()

    def test_convert_bare_move_into_workset(self, env):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env, contents="bare")
        rc = run_convert(_convert_args(pdir, to_workset="ws", move=_BARE_MOVE))
        assert rc == 0
        landed = ws.workspaces_dir / "proj"
        assert landed.is_dir() and (landed / "file.txt").read_text() == "bare"
        assert not pdir.exists()

    def test_convert_bare_move_requires_workset(self, env):
        config, std, tmp_home = env
        pdir = _default(env)
        rc = run_convert(_convert_args(pdir, to_standalone=True, move=_BARE_MOVE))
        assert rc == 1
        # nothing changed: still a primary box, no standalone marker created.
        assert resolve_project(
            std, config, project_dir=str(pdir), initialize=False,
        ).mode == BoxMode.primary
        assert not (pdir / "box_data").exists()

    def test_convert_requires_target(self, env):
        config, std, tmp_home = env
        pdir = _default(env)
        rc = run_convert(_convert_args(pdir))
        assert rc == 1

    def test_convert_missing_project(self, env):
        config, std, tmp_home = env
        plain = tmp_home / "plain"
        plain.mkdir()
        rc = run_convert(_convert_args(plain, to_standalone=True))
        assert rc == 1


# ---------------------------------------------------------------------------
# lock pre-flight: refuse to move/convert (copy+rmtree) a running box (H3)
# ---------------------------------------------------------------------------

class TestLockGuard:
    def _lock(self, env, pdir):
        """Plant a .kanibako.lock in the project's metadata dir."""
        config, std, _ = env
        proj = resolve_project(std, config, project_dir=str(pdir), initialize=False)
        lock = proj.metadata_path / ".kanibako.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("box-container\n")
        return lock

    def test_move_locked_aborts_and_keeps_source(self, env):
        config, std, tmp_home = env
        pdir = _default(env, contents="live")
        self._lock(env, pdir)

        dest = tmp_home / "moved_locked"
        rc = run_move(_move_args(pdir, dest, force=False))
        assert rc == 2
        # Source workspace NOT deleted; dest NOT created.
        assert pdir.is_dir() and (pdir / "file.txt").read_text() == "live"
        assert not dest.exists()

    def test_move_locked_force_proceeds(self, env):
        config, std, tmp_home = env
        pdir = _default(env, contents="live")
        self._lock(env, pdir)

        dest = tmp_home / "moved_forced"
        rc = run_move(_move_args(pdir, dest, force=True))
        assert rc == 0
        assert dest.is_dir() and (dest / "file.txt").read_text() == "live"
        assert not pdir.exists()

    def test_convert_locked_aborts_and_keeps_source(self, env):
        config, std, tmp_home = env
        pdir = _default(env, contents="live")
        self._lock(env, pdir)

        dest = tmp_home / "convdest_locked"
        rc = run_convert(_convert_args(pdir, to_standalone=True, move=str(dest), force=False))
        assert rc == 2
        assert pdir.is_dir() and (pdir / "file.txt").read_text() == "live"
        assert not dest.exists()
        # Ownership unchanged (still default): resolves as primary, no marker.
        assert resolve_project(
            std, config, project_dir=str(pdir), initialize=False,
        ).mode == BoxMode.primary
        assert not (pdir / "box_data").exists()

    def test_convert_locked_force_proceeds(self, env):
        config, std, tmp_home = env
        pdir = _default(env, contents="live")
        self._lock(env, pdir)

        dest = tmp_home / "convdest_forced"
        rc = run_convert(_convert_args(pdir, to_standalone=True, move=str(dest), force=True))
        assert rc == 0
        assert dest.is_dir()
        # Drift I: workset.yaml at the root; drift H: files in workspace/ subdir.
        assert (dest / "workset.yaml").is_file()
        assert (dest / "box_data").is_dir()
        assert (dest / "workspace" / "file.txt").read_text() == "live"
        assert not pdir.exists()


# ---------------------------------------------------------------------------
# F-7: per-kind (box-vs-workset) name policy on DEFAULT-mode rename edges
# ---------------------------------------------------------------------------

class TestConvertMoveCrossKindName:
    """``box convert/move --default --name <X>`` follows the SAME per-kind name
    policy as ``create`` (``system-design-1.8.0.md`` § "Detection & import"):
    box and workset names are separate namespaces, so a WORKSET's name is
    taken freely, while a SAME-KIND (another primary box) collision refuses.
    """

    def test_convert_default_name_shared_with_a_workset_succeeds(
        self, env, caplog, monkeypatch,
    ):
        """t1: convert --default --name <workset> needs no --force (the confirmation
        is answered, not forced); both names coexist, a bare resolve hits the BOX,
        and nothing warns."""
        config, std, tmp_home = env
        create_workset("common", tmp_home / "ws_root", std)
        pdir = _standalone(env)  # standalone source → true mint path
        monkeypatch.setattr("kanibako.utils.confirm_prompt", lambda _msg: None)

        with caplog.at_level("WARNING"):
            rc = run_convert(
                _convert_args(pdir, to_default=True, name="common", force=False)
            )
        assert rc == 0
        assert "common" in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary))
        from kanibako.project import registry_store
        assert "common" in registry_store.load_section(std.registry, "worksets")
        from pathlib import Path

        from kanibako.settings.paths import resolve_name
        with caplog.at_level("WARNING"):
            _resolved, kind = resolve_name(
                std.registry, "common", cwd=Path(tmp_home),
                primary_workset=std.primary_workset,
                early_system=std.early_system,
            )
        assert kind == "project"
        assert [r.getMessage() for r in caplog.records
                if r.levelname == "WARNING" and "shadow" in r.getMessage()] == []

    def test_convert_default_name_collides_primary_box_force_still_refuses(
        self, env,
    ):
        """t3: --force NEVER bypasses SAME-KIND uniqueness — a --name already
        owned by another PRIMARY box refuses even with --force."""
        config, std, tmp_home = env
        # An existing primary box owns the name "taken".
        taken_dir = _default(env, name="taken")
        pdir = _standalone(env, name="src")

        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = run_convert(
                _convert_args(pdir, to_default=True, name="taken", force=True)
            )
        assert rc == 1
        # Pre-existing "taken" box unchanged (still maps to its own workspace);
        # source still standalone.
        assert load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary),
        ).get("taken") == str(taken_dir)
        assert (pdir / "box_data").is_dir()

    def test_move_default_name_shared_with_a_workset_succeeds(self, env, monkeypatch):
        """t4: box move --default --name <workset> mirror of t1 — the move lands
        under the workset's name (the confirmation is answered, not forced)."""
        config, std, tmp_home = env
        create_workset("common", tmp_home / "ws_root", std)
        pdir = _default(env, name="mvsrc")
        dest = tmp_home / "mv_dest"
        monkeypatch.setattr("kanibako.utils.confirm_prompt", lambda _msg: None)

        rc = run_move(
            _move_args(pdir, dest, to_default=True, name="common", force=False)
        )
        assert rc == 0
        assert dest.is_dir()
        assert "common" in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary))

    def test_convert_named_workset_name_equals_global_workset_succeeds(self, env):
        """t5: a NAMED-workset target named the same as a global workset
        still converts."""
        config, std, tmp_home = env
        create_workset("tw", tmp_home / "tw_root", std)
        create_workset("gname", tmp_home / "g_root", std)
        pdir = _default(env, name="cbox")

        rc = run_convert(
            _convert_args(pdir, to_workset="tw", name="gname")
        )
        assert rc == 0
        ws2 = load_workset(tmp_home / "tw_root", "tw", early_system=std.early_system)
        assert any(p.name == "gname" for p in ws2.projects)

    def test_move_default_same_name_relocates_registration(self, env):
        """t-a (FIX1): move --default --name <current-name> to a NEW dir succeeds —
        the source's OWN registration is a self-reuse, not a collision.

        The old path is unregistered, the SAME name is registered at the new path,
        the files move, and the ``boxes/<name>`` metadata dir is preserved intact.
        Pre-fix this refused ("already registered") because the source's own entry
        tripped the same-kind guard at BOTH the validate gate and the register.
        """
        config, std, tmp_home = env
        pdir = _default(env, name="movesame")
        boxes0 = load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))
        name = next(n for n, p in boxes0.items() if p == str(pdir))
        meta_before = std.boxes / name
        assert meta_before.is_dir()

        dest = tmp_home / "moved_here"
        rc = run_move(
            _move_args(pdir, dest, to_default=True, name=name, force=True)
        )
        assert rc == 0

        boxes1 = load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))
        # Same name, now at the new path; old path fully unregistered.
        assert boxes1.get(name) == str(dest)
        assert str(pdir) not in boxes1.values()
        # Exactly one entry points at the new workspace (no strand).
        assert sum(1 for v in boxes1.values() if v == str(dest)) == 1
        # Files relocated; old workspace removed.
        assert (dest / "file.txt").read_text() == "hi"
        assert not pdir.exists()
        # Metadata dir preserved (reused in place, never deleted).
        assert (std.boxes / name).is_dir()

    def test_move_default_same_name_register_failure_restores_old(self, env):
        """t-a (FIX1 failure window): if the re-register fails AFTER the source's
        old entry was unregistered, the unwind restores name -> OLD path.

        Mocks ``register_primary_box_name`` (as bound in ``_lifecycle``) to raise
        after the pre-register unregister; the op must roll back to the source's
        original registration, leave nothing at the dest, and keep the original
        workspace dir (STEP 5 rmtree never reached).
        """
        from unittest.mock import patch

        from kanibako.errors import ProjectError

        config, std, tmp_home = env
        pdir = _default(env, name="movefail")
        boxes0 = load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))
        name = next(n for n, p in boxes0.items() if p == str(pdir))

        dest = tmp_home / "fail_dest"
        with patch(
            "kanibako.commands.box._lifecycle.register_primary_box_name",
            side_effect=ProjectError("boom"),
        ):
            rc = run_move(
                _move_args(pdir, dest, to_default=True, name=name, force=True)
            )
        assert rc == 1

        boxes1 = load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))
        # Source registration restored to its OLD path; dest not left registered.
        assert boxes1.get(name) == str(pdir)
        assert str(dest) not in boxes1.values()
        # Original workspace preserved; the rolled-back dest copy removed.
        assert pdir.is_dir()
        assert not dest.exists()

    def test_convert_inplace_different_name_refuses(self, env):
        """t-b (FIX2): in-place primary->default convert with a DIFFERENT --name
        REFUSES (rc=1) rather than silently dropping the name.

        The friendly message teaches the supported route (move to rename / drop
        --name); the registry is unchanged (original name -> path, no new name)."""
        import io
        from contextlib import redirect_stderr

        config, std, tmp_home = env
        pdir = _default(env, name="renbox")
        boxes0 = load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))
        name = next(n for n, p in boxes0.items() if p == str(pdir))

        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = run_convert(
                _convert_args(pdir, to_default=True, name="somethingelse")
            )
        assert rc == 1
        err = buf.getvalue().lower()
        assert "rename" in err
        assert "not supported" in err
        # Registry unchanged: original name still maps to the path; no new name.
        boxes1 = load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))
        assert boxes1.get(name) == str(pdir)
        assert "somethingelse" not in boxes1


# ---------------------------------------------------------------------------
# parser: migrate is gone
# ---------------------------------------------------------------------------

class TestMigrateGone:
    def test_migrate_subcommand_errors(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["box", "migrate", "/old", "/new"])

    def test_box_lists_new_verbs(self):
        parser = build_parser()
        argv = {
            "remap": ["box", "remap", "/x"],
            "move": ["box", "move", "/x", "/y"],
            "convert": ["box", "convert", "/x", "--standalone"],
        }
        for verb, args_list in argv.items():
            args = parser.parse_args(args_list)
            assert args.box_command == verb


# ---------------------------------------------------------------------------
# P2 / M-8 — lifecycle ops must CARRY the source box's box-scope settings
# ---------------------------------------------------------------------------

class TestLifecycleCarriesBoxSettings:
    """``convert`` / ``move`` make a NEW box that INHERITS the source's box-scope
    settings.  Post-P2 a standalone box keeps those in ``box_data/box.yaml``,
    one level below the ROOT file — so an op that copies ``metadata_path`` "as if it
    were box metadata" delivers the source's WORKSET tier to the destination's BOX
    tier and the real settings are never read again.

    ⚑ These pin the CARRIED VALUE end-to-end (set → convert/move → read back), not a
    file location, because the loss they guard is silent: every existing test passed
    green over it."""

    @staticmethod
    def _set_box_image(pdir, value):
        """Set a box-scope value through the REAL ``box set`` path."""
        from kanibako.commands.box._parser import run_set
        rc = run_set(argparse.Namespace(
            args=[str(pdir), f"box.image={value}"], box=None, force=False,
        ))
        assert rc == 0

    @staticmethod
    def _effective_image(config, box_tier, ws_tier):
        from kanibako.settings.settings_launch import load_merged_config
        return load_merged_config(box_tier, workset_path=ws_tier).box_image

    def test_convert_standalone_to_default_carries_box_settings(self, env, capsys):
        config, std, tmp_home = env
        pdir = _standalone(env)
        self._set_box_image(pdir, "carry/img:7")
        capsys.readouterr()

        assert run_convert(_convert_args(pdir, to_default=True)) == 0
        capsys.readouterr()

        proj = resolve_project(std, config, project_dir=str(pdir), initialize=False)
        from kanibako.settings.paths import box_workset_settings_paths
        box_tier, ws_tier = box_workset_settings_paths(proj)
        assert self._effective_image(config, box_tier, ws_tier) == "carry/img:7"
        # ...and it is the destination's BOX TIER that holds it.
        assert load_doc(box_tier)["box"]["image"] == "carry/img:7"

    def test_convert_standalone_to_workset_carries_box_settings(self, env, capsys):
        config, std, tmp_home = env
        create_workset("ws", tmp_home / "ws_root", std)
        pdir = _standalone(env)
        self._set_box_image(pdir, "carry/img:8")
        capsys.readouterr()

        assert run_convert(_convert_args(pdir, to_workset="ws")) == 0
        capsys.readouterr()

        from kanibako.settings.paths import WorksetSpec, box_workset_settings_paths
        ws = load_workset(tmp_home / "ws_root", "ws", early_system=std.early_system)
        names = list(ws.project_names) if hasattr(ws, "project_names") else [
            p.name for p in ws.projects
        ]
        assert len(names) == 1, names
        proj = resolve_workset_project(
            WorksetSpec.from_workset(ws), names[0], std, config,
        )
        box_tier, ws_tier = box_workset_settings_paths(proj)
        assert self._effective_image(config, box_tier, ws_tier) == "carry/img:8"

    def test_move_standalone_to_default_carries_box_settings(self, env, capsys):
        config, std, tmp_home = env
        pdir = _standalone(env)
        self._set_box_image(pdir, "carry/img:9")
        capsys.readouterr()

        dest = tmp_home / "moved"
        assert run_move(_move_args(pdir, dest, to_default=True)) == 0
        capsys.readouterr()

        proj = resolve_project(std, config, project_dir=str(dest), initialize=False)
        from kanibako.settings.paths import box_workset_settings_paths
        box_tier, ws_tier = box_workset_settings_paths(proj)
        assert self._effective_image(config, box_tier, ws_tier) == "carry/img:9"

    def test_move_standalone_to_standalone_does_not_nest_box_data(self, env, capsys):
        """A standalone→standalone move copies from ``box_data/``, so the destination
        gets ``<dst>/box_data/…`` — never a stranded ``<dst>/box_data/box_data/``."""
        config, std, tmp_home = env
        pdir = _standalone(env)
        self._set_box_image(pdir, "carry/img:10")
        capsys.readouterr()

        dest = tmp_home / "moved_sa"
        assert run_move(_move_args(pdir, dest, to_standalone=True)) == 0
        capsys.readouterr()

        assert not (dest / "box_data" / "box_data").exists()
        assert load_doc(dest / "box_data" / "box.yaml")["box"]["image"] == (
            "carry/img:10"
        )
        # The ROOT file still exists (detection marker) and carries the FRESH kuid.
        assert (dest / "workset.yaml").is_file()

    def test_a_root_stored_value_is_not_pinned_into_the_box_tier_on_convert(
        self, env, capsys,
    ):
        """⚑ A ``box.*`` key in a standalone's ROOT file sits at the WORKSET tier, and
        a convert must NOT persist it into the destination's BOX tier (Jei, 2026-08-26:
        "copy/persist only those elements that are within the box settings").  Doing so
        would PIN an overridable workset default as a box-scope override that later
        workset edits could not reach.  The box leaves the workset the value belonged
        to, so it stops resolving — which is what a downward default MEANS.  Its own
        BOX-tier settings still travel
        (:meth:`test_convert_standalone_to_default_carries_box_settings`)."""
        config, std, tmp_home = env
        pdir = _standalone(env)
        # The value is authored at the WORKSET tier only; the box tier is absent.
        root_doc = load_doc(pdir / "workset.yaml")
        root_doc.setdefault("box", {})["image"] = "legacy/img:11"
        dump_doc(pdir / "workset.yaml", root_doc)
        assert not (pdir / "box_data" / "box.yaml").exists()

        assert run_convert(_convert_args(pdir, to_default=True)) == 0
        capsys.readouterr()

        proj = resolve_project(std, config, project_dir=str(pdir), initialize=False)
        from kanibako.settings.paths import box_workset_settings_paths
        box_tier, ws_tier = box_workset_settings_paths(proj)
        assert "image" not in (load_doc(box_tier).get("box") or {})
        assert self._effective_image(config, box_tier, ws_tier) != "legacy/img:11"
        # ⚑ and the source's workset IDENTITY is NOT inherited either.
        assert "workset" not in load_doc(box_tier)

    def test_the_destination_workset_default_wins_over_the_source_root_value(
        self, env, capsys,
    ):
        """⚑ THE PAIRED HALF, at the WORKSET destination: a box that ARRIVES in a workset
        resolves THAT workset's ``box.*`` default, and the source's own root-stored value
        is neither carried nor pinned over it.  Were the source value persisted into the
        box tier it would OUTRANK the destination workset (cascade ``… < workset < box``)
        — a box-scope override the arriving user never set and could not reach by editing
        the workset."""
        config, std, tmp_home = env
        create_workset("ws", tmp_home / "ws_root", std)
        # The DESTINATION workset publishes a downward default...
        ws_doc = load_doc(tmp_home / "ws_root" / "workset.yaml")
        ws_doc.setdefault("box", {})["image"] = "dest/img:12"
        dump_doc(tmp_home / "ws_root" / "workset.yaml", ws_doc)
        # ...while the SOURCE has one of its own, at its own workset tier.
        pdir = _standalone(env)
        root_doc = load_doc(pdir / "workset.yaml")
        root_doc.setdefault("box", {})["image"] = "legacy/img:12"
        dump_doc(pdir / "workset.yaml", root_doc)
        assert not (pdir / "box_data" / "box.yaml").exists()

        assert run_convert(_convert_args(pdir, to_workset="ws")) == 0
        capsys.readouterr()

        from kanibako.settings.paths import WorksetSpec, box_workset_settings_paths
        ws = load_workset(tmp_home / "ws_root", "ws", early_system=std.early_system)
        names = list(ws.project_names) if hasattr(ws, "project_names") else [
            p.name for p in ws.projects
        ]
        proj = resolve_workset_project(
            WorksetSpec.from_workset(ws), names[0], std, config,
        )
        box_tier, ws_tier = box_workset_settings_paths(proj)
        assert "image" not in (load_doc(box_tier).get("box") or {})
        assert self._effective_image(config, box_tier, ws_tier) == "dest/img:12"

    def test_a_root_stored_value_is_not_pinned_into_the_box_tier_on_move(
        self, env, capsys,
    ):
        """Same rule at the STANDALONE destination (the S1 site): the move establishes a
        FRESH root — a new workset scope — so a value the source authored at ITS workset
        tier is not carried down into the destination's box tier."""
        config, std, tmp_home = env
        pdir = _standalone(env)
        root_doc = load_doc(pdir / "workset.yaml")
        root_doc.setdefault("box", {})["image"] = "legacy/img:13"
        dump_doc(pdir / "workset.yaml", root_doc)
        assert not (pdir / "box_data" / "box.yaml").exists()

        dest = tmp_home / "moved_legacy"
        assert run_move(_move_args(pdir, dest, to_standalone=True)) == 0
        capsys.readouterr()

        assert "image" not in (
            load_doc(dest / "box_data" / "box.yaml").get("box") or {}
        )
        assert "image" not in (load_doc(dest / "workset.yaml").get("box") or {})

    def test_a_workset_default_resolves_inside_and_is_never_persisted_on_the_way_out(
        self, env, capsys,
    ):
        """⚑ THE RULE, both halves in one box's lifetime.  A ``box.*`` key at the WORKSET
        tier is a downward default: a box INSIDE the workset RESOLVES it through the
        cascade, with nothing copied into its own tier, and a box that LEAVES stops
        resolving it — the value was the workset's and stays there for the boxes that
        stayed.  (Mutation: restore the workset-tier underlay in ``carried_box_settings``
        → the move writes ``wsdefault/img:14`` into the destination's box tier → RED.)"""
        config, std, tmp_home = env
        create_workset("ws", tmp_home / "ws_root", std)
        ws_file = tmp_home / "ws_root" / "workset.yaml"
        ws_doc = load_doc(ws_file)
        ws_doc.setdefault("box", {})["image"] = "wsdefault/img:14"
        dump_doc(ws_file, ws_doc)

        pdir = _standalone(env)
        assert run_convert(_convert_args(pdir, to_workset="ws")) == 0
        capsys.readouterr()

        from kanibako.settings.paths import WorksetSpec, box_workset_settings_paths
        ws = load_workset(tmp_home / "ws_root", "ws", early_system=std.early_system)
        names = list(ws.project_names) if hasattr(ws, "project_names") else [
            p.name for p in ws.projects
        ]
        proj = resolve_workset_project(
            WorksetSpec.from_workset(ws), names[0], std, config,
        )
        box_tier, ws_tier = box_workset_settings_paths(proj)
        # INSIDE: RESOLVED through the cascade, and NOT copied down into the box tier.
        assert self._effective_image(config, box_tier, ws_tier) == "wsdefault/img:14"
        assert "image" not in (load_doc(box_tier).get("box") or {})

        # OUTSIDE: convert it back out (an external-connected box is ``convert``'s to
        # move, not ``move``'s) — the workset's default does not follow it.
        assert run_convert(_convert_args(pdir, to_standalone=True)) == 0
        capsys.readouterr()
        assert "image" not in (
            load_doc(pdir / "box_data" / "box.yaml").get("box") or {}
        )
        assert "image" not in (load_doc(pdir / "workset.yaml").get("box") or {})
        # ...and it is untouched for the boxes that stayed.
        assert load_doc(ws_file)["box"]["image"] == "wsdefault/img:14"


# ---------------------------------------------------------------------------
# an OSError escaping a relocation is reported, not raised
# ---------------------------------------------------------------------------

def _remap_case(env):
    config, std, tmp_home = env
    pdir = _default(env)
    new = tmp_home / "moved_here"
    pdir.rename(new)
    return run_remap, _remap_args(pdir, new)


def _move_case(env):
    config, std, tmp_home = env
    return run_move, _move_args(_default(env), tmp_home / "dest")


def _convert_case(env):
    return run_convert, _convert_args(_default(env), to_standalone=True)


class TestANullPartitionArmIsSkippedNotWarned:
    """A null partition arm is an ADDRESS THAT DOES NOT EXIST, so the relocation skips
    it the way it skips a directory that was never published.

    ⚑ THE SHAPE, measured: ``workset.channels.{mailboxes,share_global}`` DEFAULT to the
    system partition, so a null ``system.channels.mailboxes`` — a declared value that
    omits the bind (spec §2a) — reaches the relocation as a null arm on BOTH sides.  The
    per-arm loop then divides nothing and stats nothing; without the skip, ``None``
    raises ``AttributeError`` inside the best-effort ``except Exception`` and the user
    is told a channel could not be relocated, naming an address that never existed.
    """

    @staticmethod
    def _null_mailboxes(std):
        """Null the SYSTEM key, through the production writer — the key the workset-local
        partition keys default to, so both sides of the move read the null."""
        from kanibako.settings.config_io import write_nested_key

        write_nested_key(std.settings, ("system", "channels"), "mailboxes", None)
        nulled = load_std_paths()
        assert nulled.channels_mailboxes is None
        return nulled

    def test_the_move_completes_and_names_no_relocation_failure(self, env, capsys):
        config, std, tmp_home = env
        pdir = _default(env, contents="movedata")
        create_workset("ws", tmp_home / "ws_root", std)
        self._null_mailboxes(std)
        capsys.readouterr()

        # A move ACROSS partitions: same name and same address would be the idempotent
        # no-op that returns before the loop, so the arms would never be read.
        rc = run_move(_move_args(pdir, tmp_home / "dest_ext", to_workset="ws"))
        err = capsys.readouterr().err
        assert rc == 0, err
        ws2 = load_workset(tmp_home / "ws_root", "ws", early_system=std.early_system)
        assert any(p.name == "proj" for p in ws2.projects)
        assert "could not relocate channel" not in err, err
        assert "Traceback" not in err

    def test_the_arms_reach_the_loop_as_nulls(self, env, capsys):
        """⚑ THE OTHER HALF: the null really is present at the loop's own inputs, so the
        silent pass above is the skip and not a loop that never ran."""
        from kanibako.channels.channels import (
            WS_TOKEN_PRIMARY, own_partition_dirs, partition_key_paths,
        )

        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        nulled = self._null_mailboxes(std)
        part = partition_key_paths(nulled, WS_TOKEN_PRIMARY, std.primary_workset)
        assert part.mailboxes is None
        assert part.share_global is not None  # the control: one key, one arm
        own = own_partition_dirs(
            nulled, WS_TOKEN_PRIMARY, "proj", ws_root=ws.root,
        )
        assert own.mailbox is None
        assert own.share_global is not None

    def test_a_live_arm_still_moves(self, env, capsys):
        """The control: with both arms live the loop MOVES a published directory, so the
        skip above cannot pass because the relocation never ran at all."""
        from kanibako.channels.channels import WS_TOKEN_PRIMARY, own_partition_dirs

        config, std, tmp_home = env
        pdir = _default(env, contents="movedata")
        create_workset("ws", tmp_home / "ws_root", std)
        src = own_partition_dirs(
            std, WS_TOKEN_PRIMARY, "proj", ws_root=std.primary_workset,
        )
        assert src.mailbox is not None
        src.mailbox.mkdir(parents=True, exist_ok=True)
        (src.mailbox / "mail.md").write_text("m")
        capsys.readouterr()

        rc = run_move(_move_args(pdir, tmp_home / "dest_ext", to_workset="ws"))
        err = capsys.readouterr().err
        assert rc == 0, err
        assert not (src.mailbox / "mail.md").exists(), "the live arm did not move"
        assert "could not relocate channel" not in err


class TestRelocationOSErrorReported:
    """``run_remap`` / ``run_move`` / ``run_convert`` print a named ``Error:`` line and return 1."""

    @pytest.mark.parametrize("seam", ["resolve_lifecycle_target", "_run_steps"])
    @pytest.mark.parametrize("case", [_remap_case, _move_case, _convert_case])
    def test_oserror_is_an_error_line(self, env, capsys, monkeypatch, case, seam):
        run, args = case(env)
        capsys.readouterr()

        def boom(*_a, **_k):
            raise PermissionError(13, "Permission denied", "/nowhere/blocked")

        monkeypatch.setattr(_lifecycle, seam, boom)
        rc = run(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert (
            "Error: the relocation failed: [Errno 13] Permission denied: '/nowhere/blocked'"
            in err
        )
        assert "Traceback" not in err

    @pytest.mark.parametrize("case", [_remap_case, _move_case, _convert_case])
    def test_shutil_error_names_each_failed_entry(self, env, capsys, monkeypatch, case):
        run, args = case(env)
        capsys.readouterr()

        def boom(*_a, **_k):
            raise shutil.Error([("/src/a.txt", "/dst/a.txt", "[Errno 28] No space left")])

        monkeypatch.setattr(_lifecycle, "_run_steps", boom)
        rc = run(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert (
            "Error: the relocation failed; 1 entry failed:\n  /src/a.txt: [Errno 28] No space left"
            in err
        )
        assert "Traceback" not in err


class TestInvalidNameReported:
    """An invalid ``--name`` is a named ``Error:`` line and rc 1, never a traceback."""

    def test_move_invalid_name(self, env, capsys):
        config, std, tmp_home = env
        pdir = _default(env)
        capsys.readouterr()
        rc = run_move(_move_args(pdir, tmp_home / "dest", name="bad/name"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "Error: Invalid box name 'bad/name':" in err
        assert "Traceback" not in err
        assert pdir.is_dir() and not (tmp_home / "dest").exists()

    def test_convert_invalid_name(self, env, capsys):
        pdir = _default(env)
        capsys.readouterr()
        rc = run_convert(_convert_args(pdir, to_standalone=True, name="bad/name"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "Error: Invalid box name 'bad/name':" in err
        assert "Traceback" not in err
        assert not (pdir / "box_data").exists()


# ---------------------------------------------------------------------------
# The landings a relocation cannot record
# ---------------------------------------------------------------------------

def _named(env, ws, name="proj", contents="wsdata"):
    """A named member of *ws*, its workspace leaf at ``workspaces/<name>``."""
    config, std, tmp_home = env
    leaf = ws.workspaces_dir / name
    leaf.mkdir(parents=True)
    (leaf / "file.txt").write_text(contents)
    add_project(ws, name, leaf, std)
    return leaf


def _external_member(env, ws, name="proj"):
    """A CONNECTED member of *ws* whose workspace is the user's own directory."""
    config, std, tmp_home = env
    ext = tmp_home / "ext_repo"
    ext.mkdir()
    (ext / "file.txt").write_text("mine")
    add_project(ws, name, ext, std)
    return ext


class TestInTreeLandingRefused:
    """F1: inside a workset a box lives at ``workspaces/<name>``, and nowhere else.

    Every case runs with ``--force`` (``_move_args``/``_convert_args`` default it), so
    each one also pins that ``--force`` is not a way past the refusal.
    """

    def test_ws_to_ws_move_to_a_non_canonical_in_tree_path(self, env, capsys):
        """A move naming a path inside the target workset, other than its own leaf."""
        config, std, tmp_home = env
        ws1 = create_workset("ws1", tmp_home / "ws1_root", std)
        ws2 = create_workset("ws2", tmp_home / "ws2_root", std)
        leaf = _named(env, ws1)
        stray = ws2.root / "proj"
        rc = run_move(_move_args(str(leaf), stray, to_workset="ws2"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "Refusing to record" in err
        assert str(ws2.workspaces_dir / "proj") in err
        # Refused before any write: the source is whole and no stray copy was made.
        assert (leaf / "file.txt").read_text() == "wsdata"
        assert not stray.exists()
        assert not (ws2.workspaces_dir / "proj").exists()

    def test_same_workset_move_to_a_non_canonical_in_tree_path(self, env, capsys):
        """The source's own workset, no ``--workset``: the landing is still refused, and
        the box keeps recording the leaf it already had."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        leaf = _named(env, ws)
        other = ws.root / "other"
        rc = run_move(_move_args(str(leaf), other))
        assert rc == 1
        assert "Refusing to record" in capsys.readouterr().err
        assert (leaf / "file.txt").read_text() == "wsdata"
        assert not other.exists()
        again = resolve_lifecycle_target(str(leaf), std, config)
        assert again.workspace_path == leaf.resolve()
        assert again.mode == BoxMode.named

    def test_primary_move_into_the_target_workset_root(self, env, capsys):
        """A primary box aimed at a path inside a workset root, not at ``workspaces/``."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env, contents="primary")
        stray = ws.root / "proj"
        rc = run_move(_move_args(pdir, stray, to_workset="ws"))
        assert rc == 1
        assert "Refusing to record" in capsys.readouterr().err
        assert (pdir / "file.txt").read_text() == "primary"
        assert not stray.exists()
        assert not (ws.workspaces_dir / "proj").exists()

    def test_primary_move_into_another_members_leaf(self, env, capsys):
        """``workspaces/<some other name>`` is as non-canonical as the workset root."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env, contents="primary")
        rc = run_move(_move_args(pdir, ws.workspaces_dir / "zzz", to_workset="ws"))
        assert rc == 1
        assert "Refusing to record" in capsys.readouterr().err
        assert (pdir / "file.txt").read_text() == "primary"
        assert not (ws.workspaces_dir / "zzz").exists()

    def test_standalone_move_into_the_target_workset_root(self, env, capsys):
        """A standalone source is held to the same one-leaf rule."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        sdir = _standalone(env)
        stray = ws.root / "sa"
        rc = run_move(_move_args(sdir, stray, to_workset="ws"))
        assert rc == 1
        assert "Refusing to record" in capsys.readouterr().err
        assert (sdir / "file.txt").read_text() == "x"
        assert not stray.exists()

    def test_the_advice_is_in_the_refused_commands_own_syntax(self, env, capsys):
        """``box move`` has no ``--move``; each verb is told a command it really has."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env, contents="primary")
        leaf = ws.workspaces_dir / "proj"
        bare = "kanibako box convert proj --workset ws --move"

        rc = run_move(_move_args(pdir, ws.root / "proj", to_workset="ws"))
        assert rc == 1
        err = capsys.readouterr().err
        assert _printed_routes(err) == [f"kanibako box move proj {leaf} --workset ws", bare]
        assert "\n  kanibako box move" in err and f"\n  {bare}\n" in err
        assert err.rstrip().endswith("\nOr choose a destination outside the workset.")

        rc = run_convert(_convert_args(str(pdir), to_workset="ws",
                                       move=str(ws.root / "proj")))
        assert rc == 1
        err = capsys.readouterr().err
        assert _printed_routes(err) == [bare]
        assert f"\n  {bare}\n" in err
        assert (pdir / "file.txt").read_text() == "primary"

    def test_the_advice_carries_a_rename(self, env, capsys):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env, contents="primary")
        rc = run_move(_move_args(pdir, ws.root / "x", to_workset="ws", name="renamed"))
        assert rc == 1
        assert _printed_routes(capsys.readouterr().err) == [
            f"kanibako box move proj {ws.workspaces_dir / 'renamed'} --workset ws --name renamed",
            "kanibako box convert proj --workset ws --move --name renamed",
        ]

    def test_remap_onto_a_non_canonical_in_tree_path(self, env, capsys):
        """``remap`` records records only, but still not a workspace that never was."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        leaf = _named(env, ws)
        other = ws.root / "other"
        other.mkdir()
        (other / "file.txt").write_text("moved by hand")
        rc = run_remap(_remap_args(str(leaf), other))
        assert rc == 1
        err = capsys.readouterr().err
        assert "Refusing to record" in err
        assert f"Move the files to `{leaf.resolve()}` and run `kanibako box remap`" in err
        again = resolve_lifecycle_target(str(leaf), std, config)
        assert again.workspace_path == leaf.resolve()
        assert (other / "file.txt").read_text() == "moved by hand"


class TestExternalSourceNotRelocated:
    """D: the user's own directory is never copied, and never re-recorded elsewhere."""

    def test_convert_bare_move_from_an_external_member(self, env, capsys):
        config, std, tmp_home = env
        ws1 = create_workset("ws1", tmp_home / "ws1_root", std)
        ws2 = create_workset("ws2", tmp_home / "ws2_root", std)
        ext = _external_member(env, ws1)
        rc = run_convert(_convert_args("proj", to_workset="ws2", move=_BARE_MOVE))
        assert rc == 1
        err = capsys.readouterr().err
        assert "external-connected project" in err
        assert "box remap" in err
        assert (ext / "file.txt").read_text() == "mine"
        assert not (ws2.workspaces_dir / "proj").exists()
        again = resolve_lifecycle_target(str(ext), std, config)
        assert again.workspace_path == ext.resolve()

    def test_convert_default_move_from_an_external_member(self, env, capsys):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        ext = _external_member(env, ws)
        dest = tmp_home / "newp"
        rc = run_convert(_convert_args("proj", to_default=True, move=str(dest)))
        assert rc == 1
        assert "external-connected project" in capsys.readouterr().err
        assert not dest.exists()
        assert (ext / "file.txt").read_text() == "mine"

    def test_convert_standalone_move_from_an_external_member(self, env, capsys):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        ext = _external_member(env, ws)
        dest = tmp_home / "newsa"
        rc = run_convert(_convert_args("proj", to_standalone=True, move=str(dest)))
        assert rc == 1
        assert "external-connected project" in capsys.readouterr().err
        assert not dest.exists()
        assert (ext / "file.txt").read_text() == "mine"

    def test_a_locked_external_box_reports_the_lock_first(self, env, capsys):
        """The lock is the first fact the user must act on, so it outranks the refusal."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        ext = _external_member(env, ws)
        state = resolve_lifecycle_target(str(ext), std, config)
        state.metadata_path.mkdir(parents=True, exist_ok=True)
        (state.metadata_path / ".kanibako.lock").write_text("")
        rc = run_move(_move_args(str(ext), tmp_home / "somewhere", force=False))
        assert rc == 2
        err = capsys.readouterr().err
        assert "lock file found" in err
        assert "external-connected" not in err


class TestOccupiedLandingRefused:
    """N1: an unregistered leaf of the target's new name is the user's, not ours."""

    def test_in_place_rename_onto_an_occupied_leaf(self, env, capsys):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        leaf = _named(env, ws, "alpha")
        occupied = ws.workspaces_dir / "beta"
        occupied.mkdir(parents=True)
        (occupied / "keep.txt").write_text("keep")
        rc = run_convert(_convert_args("alpha", to_workset="ws", name="beta"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "Refusing to land 'beta'" in err
        assert str(occupied) in err
        assert (occupied / "keep.txt").read_text() == "keep"
        assert (leaf / "file.txt").read_text() == "wsdata"
        again = resolve_lifecycle_target(str(leaf), std, config)
        assert again.name == "alpha"
        assert again.workspace_path == leaf.resolve()

    def test_bare_move_onto_an_occupied_leaf(self, env, capsys):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env, contents="primary")
        occupied = ws.workspaces_dir / "proj"
        occupied.mkdir(parents=True)
        (occupied / "keep.txt").write_text("keep")
        rc = run_convert(_convert_args(str(pdir), to_workset="ws", move=_BARE_MOVE))
        assert rc == 1
        assert "already exists" in capsys.readouterr().err
        assert (occupied / "keep.txt").read_text() == "keep"
        assert (pdir / "file.txt").read_text() == "primary"


class TestLandingsThatMustKeepWorking:
    """The exemptions: what F1 and the occupied-landing check must NOT refuse."""

    def test_same_workset_same_name_move_to_an_external_path(self, env):
        """The source's own leaves are exempt, and an external landing is never in-tree."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        leaf = _named(env, ws)
        ext = tmp_home / "ext_repo2"
        rc = run_move(_move_args(str(leaf), ext))
        assert rc == 0
        assert (ext / "file.txt").read_text() == "wsdata"
        # The old in-tree leaf is retired and the discoverability link takes its place.
        assert leaf.is_symlink()
        assert os.readlink(leaf) == str(ext)
        again = resolve_lifecycle_target(str(leaf), std, config)
        assert again.workspace_path == ext.resolve()

    def test_remap_onto_an_existing_destination(self, env):
        """``remap``'s files are meant to be where it records them."""
        config, std, tmp_home = env
        pdir = _default(env, contents="keep")
        moved = tmp_home / "moved_here"
        pdir.rename(moved)
        rc = run_remap(_remap_args(str(pdir), moved))
        assert rc == 0
        assert (moved / "file.txt").read_text() == "keep"
        assert str(moved) in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary)).values()

    def test_bare_move_into_the_workset(self, env):
        """The canonical landing is what F1 exists to leave alone."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env, contents="primary")
        rc = run_convert(_convert_args(str(pdir), to_workset="ws", move=_BARE_MOVE))
        assert rc == 0
        assert (ws.workspaces_dir / "proj" / "file.txt").read_text() == "primary"


# ---------------------------------------------------------------------------
# a PRIMARY box under a repointed workset.workspaces
# ---------------------------------------------------------------------------

class TestPrimaryBoxUnderARepointedWorkspaces:
    """A PRIMARY box whose workspace lies inside a registered workset's RESOLVED
    ``workset.workspaces``.  The path space is where a member MAY live and only a
    per-workset ``boxes:`` entry makes one a member (spec § Detection & import), so
    the PRIMARY registry decides — and a member the workset DOES record keeps it."""

    def _repointed(self, env, ws_name, workspaces_dir):
        """A NAMED workset whose ``workset.workspaces`` is repointed at *workspaces_dir*."""
        config, std, tmp_home = env
        ws = create_workset(ws_name, tmp_home / f"{ws_name}_root", std)
        dump_doc(ws.root / "workset.yaml", {"workset": {"workspaces": str(workspaces_dir)}})
        return load_workset(ws.root, ws.name, early_system=std.early_system)

    def _under_repointed(self, env, box="beta", ws_name="wsa", dir_name="extws"):
        """A primary box whose workspace sits under a repointed ``workset.workspaces``.

        The repointed dir is the box's OWN parent, so a destination beside it is
        outside the workset's path space.
        """
        config, std, tmp_home = env
        pdir = tmp_home / dir_name / box
        pdir.mkdir(parents=True)
        (pdir / "file.txt").write_text("keep")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        return pdir, self._repointed(env, ws_name, pdir.parent)

    def _recorded_member(self, env, ws_name, data_dir, member):
        """An in-tree member of a repointed workset whose leaf is ALSO a PRIMARY box."""
        from kanibako.settings.paths import (
            WorksetSpec, register_primary_box_name, resolve_workset_project,
        )

        config, std, tmp_home = env
        ws = self._repointed(env, ws_name, data_dir)
        leaf = data_dir / member
        leaf.mkdir(parents=True)
        (leaf / "file.txt").write_text("member")
        add_project(ws, member, leaf, std)
        resolve_workset_project(
            WorksetSpec.from_workset(ws), member, std, config, initialize=True,
        )
        register_primary_box_name(std.primary_workset, member, leaf, early=_early_scope(std, BoxMode.primary))
        return ws, leaf

    def test_the_primary_registry_decides_not_the_workset_path_space(self, env):
        """The ordering this pins: the path space DOES claim the dir, and the PRIMARY
        registry decides anyway — a bare containment is not a membership record."""
        from kanibako.settings.paths import detect_project_mode

        config, std, tmp_home = env
        pdir, ws = self._under_repointed(env)
        assert detect_project_mode(pdir, std, config).mode is BoxMode.named
        state = resolve_lifecycle_target("beta", std, config)
        assert state.mode is BoxMode.primary
        assert state.name == "beta"
        assert state.ws is None

    def test_the_path_form_resolves_too(self, env):
        """PIN: the workspace path reaches the same box the bare name does."""
        config, std, tmp_home = env
        pdir, ws = self._under_repointed(env)
        assert resolve_lifecycle_target(str(pdir), std, config).mode is BoxMode.primary

    def test_move_reaches_the_box(self, env, capsys):
        """PIN: ``box move`` relocates the box instead of refusing to find it."""
        config, std, tmp_home = env
        pdir, ws = self._under_repointed(env)
        dest = tmp_home / "moved"
        assert run_move(_move_args("beta", dest)) == 0
        assert (dest / "file.txt").read_text() == "keep"
        assert not pdir.exists()
        assert str(dest.resolve()) in load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary)).values()

    def test_convert_under_another_name_refuses_rather_than_copy_the_workspace(
        self, env, capsys
    ):
        """PIN: an in-place convert under another name would leave the source tree
        behind with no box owning it, so it refuses instead of copying."""
        config, std, tmp_home = env
        pdir, ws = self._under_repointed(env)
        rc = run_convert(_convert_args("beta", to_workset="wsa", name="beta2"))
        assert rc == 1
        # ⚑ ON THE FILESYSTEM, not just the return code: the copy this refuses is
        # invisible to a membership-only assertion.
        assert not (pdir.parent / "beta2").exists()
        assert (pdir / "file.txt").read_text() == "keep"
        assert load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))["beta"] == str(pdir)
        assert not any(p.name == "beta2" for p in load_workset(ws.root, "wsa", early_system=std.early_system).projects)

    def test_convert_under_another_name_with_move_lands_one_tree(self, env):
        """PIN: the route the refusal names — ``--move`` — relocates the tree, so
        exactly one workspace exists and the member owns it."""
        config, std, tmp_home = env
        pdir, ws = self._under_repointed(env)
        rc = run_convert(
            _convert_args("beta", to_workset="wsa", name="beta2", move=_BARE_MOVE)
        )
        assert rc == 0
        assert not pdir.exists()
        assert [p.name for p in pdir.parent.iterdir()] == ["beta2"]
        assert load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary)) == {}
        assert any(p.name == "beta2" for p in load_workset(ws.root, "wsa", early_system=std.early_system).projects)

    def test_convert_in_place_under_its_own_name_records_where_it_stands(self, env):
        """PIN: the box's own path IS the landing leaf, so the same-name convert
        records it there and copies nothing."""
        config, std, tmp_home = env
        pdir, ws = self._under_repointed(env)
        rc = run_convert(_convert_args("beta", to_workset="wsa"))
        assert rc == 0
        assert (pdir / "file.txt").read_text() == "keep"
        assert [q.name for q in pdir.parent.iterdir()] == ["beta"]
        assert load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary)) == {}
        member = next(
            p for p in load_workset(ws.root, "wsa", early_system=std.early_system).projects if p.name == "beta"
        )
        assert Path(member.source_path).resolve() == pdir.resolve()

    def test_a_recorded_member_keeps_the_workset(self, env):
        """PIN: a member the workset records at this path is NOT stolen by the
        PRIMARY registry, even when the same path is a primary box."""
        config, std, tmp_home = env
        ws, leaf = self._recorded_member(env, "wsa2", tmp_home / "wsdata", "alpha")
        state = resolve_lifecycle_target("alpha", std, config)
        assert state.mode is BoxMode.named
        assert state.owner == "workset:wsa2"
        assert state.workspace_path == leaf.resolve()

    def test_a_connected_external_member_keeps_the_workset(self, env):
        """PIN: the EXTERNAL connect record is a membership too — the one no path
        containment finds, since the leaf is outside every workset root."""
        from kanibako.settings.paths import (
            WorksetSpec, register_primary_box_name, resolve_workset_project,
        )

        config, std, tmp_home = env
        ws = create_workset("wsa3", tmp_home / "wsa3_root", std)
        leaf = tmp_home / "extmem" / "delta"
        leaf.mkdir(parents=True)
        (leaf / "file.txt").write_text("member")
        add_project(ws, "delta", leaf, std)
        resolve_workset_project(
            WorksetSpec.from_workset(ws), "delta", std, config, initialize=True,
        )
        register_primary_box_name(std.primary_workset, "delta", leaf, early=_early_scope(std, BoxMode.primary))
        state = resolve_lifecycle_target("delta", std, config)
        assert state.mode is BoxMode.named
        assert state.owner == "workset:wsa3"


# ---------------------------------------------------------------------------
# a refusal's cure is the next command the user runs
# ---------------------------------------------------------------------------

def _printed_routes(err):
    """The ``kanibako box …`` commands a refusal printed, in the order printed."""
    return [a or b for a, b in re.findall(
        r"`(kanibako box (?:convert|move) [^`]+)`|^  (kanibako box (?:convert|move) .+)$",
        err, re.M)]


def _run_printed(route, *, move_dest=None, name=None):
    """Re-dispatch a printed route to the entry point it names.

    PIN BY RUNNING, NOT BY TEXT: the argv the refusal printed is what reaches
    ``run_convert`` / ``run_move``.  Only the ``<path>`` and ``<name>`` placeholders
    are filled in, as the user would, and ``force`` stands in for the confirmation a
    human types.
    """
    argv = route.split()[2:]  # drop the leading `kanibako box`
    verb, rest = argv[0], list(argv[1:])
    if move_dest is not None:
        rest = [move_dest if a == "<path>" else a for a in rest]
    if name is not None:
        rest = [a.replace("<name>", name) for a in rest]

    def flag(name):
        return rest[rest.index(name) + 1] if name in rest else None

    if verb == "convert":
        return run_convert(_convert_args(
            rest[0], to_workset=flag("--workset"),
            move=(_BARE_MOVE if "--move" in rest else None),
            name=flag("--name"),
        ))
    return run_move(_move_args(
        rest[0], rest[1], to_workset=flag("--workset"), name=flag("--name"),
    ))


def _standalone_under_repointed(env, box="beta", ws_name="wsa", dir_name="extws"):
    """A STANDALONE box whose ROOT sits under a repointed ``workset.workspaces``.

    The repointed dir is the box root's own parent, so a destination beside it
    is outside the workset's path space.
    """
    config, std, tmp_home = env
    root = tmp_home / dir_name / box
    root.mkdir(parents=True)
    (root / "file.txt").write_text("keep")
    proj = resolve_standalone_project(
        std, config, project_dir=str(root), initialize=True,
    )
    ws = create_workset(ws_name, tmp_home / f"{ws_name}_root", std)
    dump_doc(ws.root / "workset.yaml", {"workset": {"workspaces": str(root.parent)}})
    return proj, load_workset(ws.root, ws.name, early_system=std.early_system)


class TestRefusalCuresReachTheBoxTheyName:
    """Every command an in-tree refusal prints, RUN as printed.

    A bare box NAME is not a reference every mode answers to, so the reference
    a refusal prints is the one its source's mode resolves.
    """

    def _in_place_refusal(self, env, capsys):
        config, std, tmp_home = env
        assert run_convert(_convert_args(str(tmp_home / "extws" / "beta"),
                                         to_workset="wsa")) == 1
        return _printed_routes(capsys.readouterr().err)

    def _in_tree_refusal(self, env, capsys):
        config, std, tmp_home = env
        assert run_move(_move_args(
            str(tmp_home / "extws" / "beta"), tmp_home / "extws" / "other",
            to_workset="wsa",
        )) == 1
        return _printed_routes(capsys.readouterr().err)

    @pytest.mark.parametrize("route_index", [0, 1])
    def test_both_in_place_convert_cures_run_for_a_standalone_source(
        self, env, capsys, route_index,
    ):
        """PIN: each cure the in-place convert names, run as printed, lands the
        standalone box."""
        config, std, tmp_home = env
        proj, ws = _standalone_under_repointed(env)
        routes = self._in_place_refusal(env, capsys)
        assert len(routes) == 2

        move_dest = tmp_home / "elsewhere" / "beta"
        assert _run_printed(routes[route_index], move_dest=str(move_dest)) == 0

        members = load_workset(ws.root, "wsa", early_system=std.early_system).projects
        if routes[route_index].split()[2] == "convert":
            # the member now stands at the workset's own leaf
            assert [p.name for p in members] == [proj.name]
            assert Path(members[0].source_path).resolve() == (
                tmp_home / "extws" / proj.name
            ).resolve()
        else:
            # moved OUT of the workset: standalone again, at the path the user gave
            assert members == []
            from kanibako.project import registry_store

            assert list(registry_store.load_standalone(std.registry).values()) == [
                str(move_dest)
            ]

    @pytest.mark.parametrize("route_index", [0, 1])
    def test_both_in_tree_landing_cures_run_for_a_standalone_source(
        self, env, capsys, route_index,
    ):
        """PIN: each cure the in-tree-landing refusal names, run as printed, puts
        the member at the leaf the refusal named."""
        config, std, tmp_home = env
        proj, ws = _standalone_under_repointed(env)
        routes = self._in_tree_refusal(env, capsys)
        assert len(routes) == 2

        assert _run_printed(routes[route_index]) == 0

        members = load_workset(ws.root, "wsa", early_system=std.early_system).projects
        assert [p.name for p in members] == [proj.name]
        leaf = tmp_home / "extws" / proj.name
        assert Path(members[0].source_path).resolve() == leaf.resolve()
        assert not (tmp_home / "extws" / "other").exists()

    @pytest.mark.parametrize("verb", ["move", "convert"])
    @pytest.mark.parametrize("held", [False, True])
    def test_the_in_tree_landing_cure_runs_for_a_member_at_its_leaf(
        self, env, capsys, verb, held,
    ):
        """PIN: a cure to the leaf a member already stands at is refused as its current
        location, so the cure is a new name, in the refused verb.  *held*: the asked
        ``--name`` is another member's, so the cure's leaf falls back to the box's own."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        others = {"renamed": _named(env, ws, name="renamed").resolve()} if held else {}
        leaf = _named(env, ws)
        name = "renamed" if held else None
        if verb == "move":
            rc = run_move(_move_args(str(leaf), ws.root / "other", name=name))
        else:
            rc = run_convert(_convert_args(str(leaf), to_workset="ws",
                                           move=str(ws.root / "other"), name=name))
        assert rc == 1
        routes = _printed_routes(capsys.readouterr().err)
        assert routes[0].split()[2] == verb

        assert _run_printed(routes[0], name="gamma") == 0
        gamma = ws.workspaces_dir / "gamma"
        members = load_workset(ws.root, "ws", early_system=std.early_system).projects
        assert {p.name: Path(p.source_path).resolve() for p in members} == {
            **others, "gamma": gamma.resolve()}
        assert (gamma / "file.txt").read_text() == "wsdata"

    def test_the_cure_is_a_reference_the_resolver_takes_back(self, env, capsys):
        """PIN: the reference printed resolves to the SAME box the refusal fired
        on, and the box's own workspace path does NOT — it is nested under the
        root, so a root inside a workset's path space makes the mode read from
        the path space rather than from the box."""
        config, std, tmp_home = env
        proj, ws = _standalone_under_repointed(env)
        for route in self._in_place_refusal(env, capsys):
            again = resolve_lifecycle_target(route.split()[3], std, config)
            assert again.mode is BoxMode.standalone
            assert again.name == proj.name
        from kanibako.errors import WorksetError

        with pytest.raises(WorksetError):
            resolve_lifecycle_target(
                str(tmp_home / "extws" / "beta" / "workspace"), std, config,
            )

    def test_an_unregistered_standalone_is_not_reachable_by_name(
        self, env, capsys,
    ):
        """PIN: why the cure prints a path.  An UNregistered standalone is in no
        registry the lifecycle route reads, so its bare name is path-ified against
        the shell's cwd and misses; the box stays reachable by path (spec
        § Detection & import: unregistered ⇒ path or ancestor-walk only)."""
        from kanibako.errors import ProjectError
        from kanibako.project import registry_store

        config, std, tmp_home = env
        proj, ws = _standalone_under_repointed(env)
        registry_store.unregister_standalone(std.registry, proj.name)
        assert proj.name not in registry_store.load_standalone(std.registry)
        with pytest.raises(ProjectError):
            resolve_lifecycle_target(proj.name, std, config)

    def test_a_registered_standalone_is_reachable_by_name(self, env, capsys):
        """The other side of that same door: once the standalone IS registered,
        ``box create --standalone --register``'s promise holds — the bare name
        resolves to the box instead of being path-ified against the cwd."""
        from kanibako.project import registry_store

        config, std, tmp_home = env
        proj, ws = _standalone_under_repointed(env)
        root = tmp_home / "extws" / "beta"
        assert proj.name in registry_store.load_standalone(std.registry)

        state = resolve_lifecycle_target(proj.name, std, config)
        assert state.mode is BoxMode.standalone
        assert state.name == proj.name
        assert Path(state.metadata_path).resolve() == root.resolve()

    @pytest.mark.parametrize("owner", ["beta", "foo"])
    def test_the_connect_in_place_cure_runs(self, env, capsys, owner):
        """PIN: ``workset connect`` on a primary box's in-tree leaf prints an in-place
        convert that lands the box at that leaf, whether or not its name is the leaf's."""
        from kanibako.commands.workset_cmd import run_connect

        config, std, tmp_home = env
        leaf = tmp_home / "extws" / "beta"
        leaf.mkdir(parents=True)
        (leaf / "file.txt").write_text("keep")
        resolve_project(std, config, project_dir=str(leaf), initialize=True,
                        name_override=owner)
        ws = create_workset("wsa", tmp_home / "wsa_root", std)
        dump_doc(ws.root / "workset.yaml", {"workset": {"workspaces": str(leaf.parent)}})
        assert run_connect(argparse.Namespace(
            workset="wsa", source=str(leaf), project_name=None, force=False,
        )) == 1
        err = capsys.readouterr().err
        route = re.findall(r"^  (kanibako box convert .+)$", err, re.M)[0]

        assert _run_printed(route) == 0
        members = load_workset(ws.root, "wsa", early_system=std.early_system).projects
        assert [p.name for p in members] == ["beta"]
        assert Path(members[0].source_path).resolve() == leaf.resolve()
        assert (leaf / "file.txt").read_text() == "keep"
        assert load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary)) == {}


class TestCuresAvoidAHeldName:
    """A cure never names a box name another member of the target workset holds.

    Every printed command is RUN as printed; the sibling holding the name keeps it.
    """

    def _members(self, env, ws):
        config, std, tmp_home = env
        return {p.name: Path(p.source_path).resolve() for p in load_workset(
            ws.root, ws.name, early_system=std.early_system).projects}

    def _primary_in_leaf(self, env, owner):
        """A primary box *owner* standing at ``wsa``'s repointed leaf ``extws/beta``."""
        config, std, tmp_home = env
        leaf = tmp_home / "extws" / "beta"
        leaf.mkdir(parents=True)
        (leaf / "file.txt").write_text("keep")
        resolve_project(std, config, project_dir=str(leaf), initialize=True,
                        name_override=owner)
        ws = create_workset("wsa", tmp_home / "wsa_root", std)
        dump_doc(ws.root / "workset.yaml", {"workset": {"workspaces": str(leaf.parent)}})
        return leaf, load_workset(ws.root, "wsa", early_system=std.early_system)

    @pytest.mark.parametrize("route_index", [0, 1])
    def test_in_tree_landing_cures_drop_a_held_name(self, env, capsys, route_index):
        """The move form names the leaf its own name reaches, not the held one's."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        held = _named(env, ws, name="renamed")
        pdir = _default(env, contents="primary")
        assert run_move(_move_args(pdir, ws.root / "x", to_workset="ws",
                                   name="renamed")) == 1
        err = capsys.readouterr().err
        routes = _printed_routes(err)
        assert len(routes) == 2

        assert _run_printed(routes[route_index]) == 0
        assert f"would live at `{ws.workspaces_dir / 'proj'}`" in err
        assert self._members(env, ws) == {
            "renamed": held.resolve(), "proj": (ws.workspaces_dir / "proj").resolve(),
        }
        assert (ws.workspaces_dir / "proj" / "file.txt").read_text() == "primary"

    @pytest.mark.parametrize("owner", ["beta", "foo"])
    def test_in_place_convert_cure_drops_a_held_name(self, env, capsys, owner):
        """Under its own name the box either stands at its leaf already (convert it
        there) or moves to that leaf — never to the held one."""
        config, std, tmp_home = env
        leaf, ws = self._primary_in_leaf(env, owner)
        held = _external_member(env, ws, name="gamma")
        assert run_convert(_convert_args(owner, to_workset="wsa", name="gamma")) == 1
        err = capsys.readouterr().err
        route = _printed_routes(err)[0]
        own = tmp_home / "extws" / owner

        assert _run_printed(route) == 0
        if owner == "beta":
            assert f"would live at {tmp_home / 'extws' / 'gamma'} " in err
            assert "--move" not in route
        else:
            assert f"would live at {own} " in err
        assert self._members(env, ws) == {"gamma": held.resolve(), owner: own.resolve()}
        assert (own / "file.txt").read_text() == "keep"

    def test_in_tree_landing_with_both_names_held(self, env, capsys):
        """Dropping ``--name`` cannot help when the box's own name is held too."""
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        held = _named(env, ws, name="renamed")
        other = _external_member(env, ws, name="proj")
        pdir = _default(env, contents="primary")
        assert run_move(_move_args(pdir, ws.root / "x", to_workset="ws",
                                   name="renamed")) == 1
        err = capsys.readouterr().err
        assert "'renamed' and 'proj' already held in workset 'ws'" in err
        routes = _printed_routes(err)
        assert routes == ["kanibako box convert proj --workset ws --move --name <name>"]

        assert _run_printed(routes[0], name="fresh") == 0
        assert self._members(env, ws) == {
            "renamed": held.resolve(), "proj": other.resolve(),
            "fresh": (ws.workspaces_dir / "fresh").resolve(),
        }

    def test_in_place_convert_with_both_names_held(self, env, capsys):
        config, std, tmp_home = env
        leaf, ws = self._primary_in_leaf(env, "beta")
        gamma = _external_member(env, ws, name="gamma")
        beta = tmp_home / "beta_elsewhere"
        beta.mkdir()
        add_project(ws, "beta", beta, std)
        assert run_convert(_convert_args("beta", to_workset="wsa", name="gamma")) == 1
        err = capsys.readouterr().err
        assert "'gamma' and 'beta' already held in workset 'wsa'" in err
        route = _printed_routes(err)[0]
        assert route.endswith("--move --name <name>")

        assert _run_printed(route, name="delta") == 0
        delta = tmp_home / "extws" / "delta"
        assert self._members(env, ws) == {
            "gamma": gamma.resolve(), "beta": beta.resolve(), "delta": delta.resolve(),
        }
        assert (delta / "file.txt").read_text() == "keep"


class TestRelocationOutOfTheLandingLeaf:
    """A box moving OUT of the leaf it stands in is not a collision with itself."""

    def test_box_move_out_of_the_repointed_leaf_is_exempt(self, env):
        """PIN: the unregistered-leaf exemption reads no relocation test.  The
        whole workspace moves, the workset records the new path as an EXTERNAL
        member, the in-path-space leaf is left as a link to it, and exactly one
        copy of the tree exists."""
        config, std, tmp_home = env
        pdir = tmp_home / "extws" / "beta"
        pdir.mkdir(parents=True)
        (pdir / "file.txt").write_text("keep")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        ws = create_workset("wsa", tmp_home / "wsa_root", std)
        dump_doc(ws.root / "workset.yaml", {"workset": {"workspaces": str(pdir.parent)}})

        dest = tmp_home / "elsewhere" / "beta"
        assert run_move(_move_args("beta", dest, to_workset="wsa")) == 0

        assert pdir.is_symlink()
        assert pdir.resolve() == dest.resolve()
        assert (dest / "file.txt").read_text() == "keep"
        assert load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary)) == {}
        member = next(p for p in load_workset(ws.root, "wsa", early_system=std.early_system).projects
                      if p.name == "beta")
        assert Path(member.source_path).resolve() == dest.resolve()


class TestInPlaceConvertRollbackKeepsAnEmptyWorkspace:
    """A late failure in an in-place convert OUT of standalone restores ``workspace/``,
    empty or not: the lift removes the emptied dir either way."""

    @pytest.mark.parametrize("contents", [False, True], ids=["empty", "with-a-file"])
    def test_the_workspace_dir_comes_back(self, env, capsys, monkeypatch, contents):
        config, std, tmp_home = env
        pdir = tmp_home / "sa"
        pdir.mkdir()
        resolve_standalone_project(std, config, project_dir=str(pdir), initialize=True)
        workspace = pdir / "workspace"
        assert workspace.is_dir()
        if contents:
            (workspace / "file.txt").write_text("x")
        before = sorted(p.name for p in workspace.iterdir())

        def boom(*_a, **_k):
            raise PermissionError(13, "Permission denied", "/nowhere/blocked")

        # Runs after the lift (step 2), so the unwind must undo it.
        monkeypatch.setattr(_lifecycle, "_apply_ownership_and_markers", boom)
        rc = run_convert(_convert_args(pdir, to_default=True))

        assert rc == 1, capsys.readouterr().err
        assert workspace.is_dir()
        assert sorted(p.name for p in workspace.iterdir()) == before

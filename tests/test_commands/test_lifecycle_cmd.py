"""CLI-level tests for the lifecycle entry points (remap / move / convert).

These exercise the thin ``run_remap`` / ``run_move`` / ``run_convert`` wrappers
(arg parsing + friendly errors) on top of the Phase-1 engine.
"""

from __future__ import annotations

import argparse

import pytest

from kanibako.cli import build_parser
from kanibako.commands.box._lifecycle import (
    _BARE_MOVE,
    run_convert,
    run_move,
    run_remap,
)
from kanibako.settings.config import load_config
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.paths import load_primary_boxes
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
            root, load_doc(root / "workset.yaml"),
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
        names = load_primary_boxes(std.primary_workset)
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
        ws2 = load_workset(ws.root, ws.name)
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
        # P8b/Option A: primary identity is the names.yaml registration, not disk.
        assert proj.mode == BoxMode.primary
        assert "project" not in load_doc(proj.metadata_path / "box.yaml")
        assert str(pdir) in load_primary_boxes(std.primary_workset).values()

    def test_convert_to_workset_inplace_external(self, env):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env)
        rc = run_convert(_convert_args(pdir, to_workset="ws"))
        assert rc == 0
        ws2 = load_workset(ws.root, ws.name)
        assert any(p.name == "proj" for p in ws2.projects)
        # P8b/Option A: the external workspace is recorded in the workset's
        # per-workset ``boxes:`` registry, not an on-disk ``resolved.workspace``.
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc
        reg = workset_registry.load_workset_boxes(
            workset_registry.resolve_workset_registry_path(
                ws.root, load_doc(ws.root / "workset.yaml"),
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
# F-7: cross-kind (box-vs-workset) name policy on DEFAULT-mode rename edges
# ---------------------------------------------------------------------------

class TestConvertMoveCrossKindName:
    """``box convert/move --default --name <X>`` enforces the SAME per-kind name
    policy as ``create`` (``system-design-1.8.0.md`` § "Detection & import",
    "Cross-kind name semantics"; Jei 2026-07-08).

    A ``--name`` that lands a box in primary/default mode and collides with a
    WORKSET name shadows that workset in bare-name resolution, so it REFUSES
    unless ``--force``; a SAME-KIND (another primary box) collision refuses
    UNCONDITIONALLY.  Pre-fix these rename edges ignored ``--name`` and routed
    through ``assign_primary_box_name`` (basename auto-suffix only), never
    consulting the cross-kind arm.
    """

    def test_convert_default_name_collides_workset_refuses(self, env):
        """t1: convert --default --name <workset> without --force → clean rc=1,
        teaches --force, and mints NO box (refused before any copy)."""
        config, std, tmp_home = env
        create_workset("common", tmp_home / "ws_root", std)
        pdir = _standalone(env)  # standalone source → true mint path

        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = run_convert(
                _convert_args(pdir, to_default=True, name="common", force=False)
            )
        assert rc == 1
        assert "--force" in buf.getvalue()
        # No primary box minted under the workset name; workset intact; source
        # still standalone (nothing copied/registered on refusal).
        assert "common" not in load_primary_boxes(std.primary_workset)
        assert not (std.boxes / "common").exists()
        from kanibako.project import registry_store
        assert "common" in registry_store.load_section(std.registry, "worksets")
        assert (pdir / "box_data").is_dir()

    def test_convert_default_name_collides_workset_force_shadows(self, env):
        """t2: with --force the box takes the workset name (deliberate shadow);
        both coexist and a bare resolve now hits the BOX."""
        config, std, tmp_home = env
        create_workset("common", tmp_home / "ws_root", std)
        pdir = _standalone(env)

        rc = run_convert(
            _convert_args(pdir, to_default=True, name="common", force=True)
        )
        assert rc == 0
        # Box registered under the shadowed name; workset still registered.
        assert "common" in load_primary_boxes(std.primary_workset)
        from kanibako.project import registry_store
        assert "common" in registry_store.load_section(std.registry, "worksets")
        # Bare resolution is deterministic — the primary box wins (shadow).
        from pathlib import Path

        from kanibako.settings.paths import resolve_name
        _resolved, kind = resolve_name(
            std.registry, "common", cwd=Path(tmp_home),
            primary_workset=std.primary_workset,
        )
        assert kind == "project"

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
        assert load_primary_boxes(std.primary_workset).get("taken") == str(taken_dir)
        assert (pdir / "box_data").is_dir()

    def test_move_default_name_collides_workset_refuses(self, env):
        """t4: box move --default --name <workset> mirror of t1 — refuses without
        --force and moves no files."""
        config, std, tmp_home = env
        create_workset("common", tmp_home / "ws_root", std)
        pdir = _default(env, name="mvsrc")
        dest = tmp_home / "mv_dest"

        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = run_move(
                _move_args(pdir, dest, to_default=True, name="common", force=False)
            )
        assert rc == 1
        assert "--force" in buf.getvalue()
        # No copy performed (refused up front); dest absent, source intact.
        assert not dest.exists()
        assert pdir.is_dir()
        assert "common" not in load_primary_boxes(std.primary_workset)

    def test_convert_named_workset_name_equals_global_workset_succeeds(self, env):
        """t5: the cross-kind guard must NOT reach a NAMED-workset target — a
        project named the same as a global workset still converts."""
        config, std, tmp_home = env
        create_workset("tw", tmp_home / "tw_root", std)
        create_workset("gname", tmp_home / "g_root", std)
        pdir = _default(env, name="cbox")

        rc = run_convert(
            _convert_args(pdir, to_workset="tw", name="gname")
        )
        assert rc == 0
        ws2 = load_workset(tmp_home / "tw_root", "tw")
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
        boxes0 = load_primary_boxes(std.primary_workset)
        name = next(n for n, p in boxes0.items() if p == str(pdir))
        meta_before = std.boxes / name
        assert meta_before.is_dir()

        dest = tmp_home / "moved_here"
        rc = run_move(
            _move_args(pdir, dest, to_default=True, name=name, force=True)
        )
        assert rc == 0

        boxes1 = load_primary_boxes(std.primary_workset)
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
        boxes0 = load_primary_boxes(std.primary_workset)
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

        boxes1 = load_primary_boxes(std.primary_workset)
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
        boxes0 = load_primary_boxes(std.primary_workset)
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
        boxes1 = load_primary_boxes(std.primary_workset)
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
        from kanibako.settings.config import load_merged_config, config_file_path
        from kanibako.settings.paths import xdg
        return load_merged_config(
            config_file_path(xdg("XDG_CONFIG_HOME", ".config")),
            box_tier, workset_path=ws_tier,
        ).box_image

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
        ws = load_workset(tmp_home / "ws_root", "ws")
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
        ws = load_workset(tmp_home / "ws_root", "ws")
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
        ws = load_workset(tmp_home / "ws_root", "ws")
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

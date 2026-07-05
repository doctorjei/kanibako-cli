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
from kanibako.config import load_config, read_project_meta
from kanibako.names import read_names
from kanibako.paths import (
    BoxMode,
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.utils import project_hash
from kanibako.workset import add_project, create_workset, load_workset


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

    from kanibako import registry_store, workset_registry
    from kanibako.config_io import load_doc

    out = {}
    for name, root_str in registry_store.load_section(
        std.registry, "worksets"
    ).items():
        root = Path(root_str)
        registry_path = workset_registry.resolve_workset_registry_path(
            root, load_doc(root / "settings.yaml"),
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
        names = read_names(std.registry)
        assert str(new) in names["projects"].values()

        # P8b/Option A: the remapped workspace resolves from the registry, not an
        # on-disk ``resolved.workspace`` (read_project_meta is None).
        proj = resolve_project(std, config, project_dir=str(new), initialize=False)
        assert proj.project_path == new.resolve()
        assert proj.project_hash == project_hash(str(new.resolve()))
        assert read_project_meta(proj.metadata_path / "settings.yaml") is None

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
        ws2 = load_workset(ws.root)
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
        # P8b/Option A: Drift I marker settings.yaml at the ROOT (materialized by
        # the sparse kuid write), but no on-disk ``project:`` identity — the
        # standalone box is registered in registry.standalone.
        from kanibako.registry_store import load_standalone
        assert read_project_meta(pdir / "settings.yaml") is None
        assert (pdir / "settings.yaml").is_file()
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
        assert read_project_meta(proj.metadata_path / "settings.yaml") is None
        assert str(pdir) in read_names(std.registry)["projects"].values()

    def test_convert_to_workset_inplace_external(self, env):
        config, std, tmp_home = env
        ws = create_workset("ws", tmp_home / "ws_root", std)
        pdir = _default(env)
        rc = run_convert(_convert_args(pdir, to_workset="ws"))
        assert rc == 0
        ws2 = load_workset(ws.root)
        assert any(p.name == "proj" for p in ws2.projects)
        # P8b/Option A: the external workspace is recorded in the workset's
        # per-workset ``boxes:`` registry, not an on-disk ``resolved.workspace``.
        from kanibako import workset_registry
        from kanibako.config_io import load_doc
        reg = workset_registry.load_workset_boxes(
            workset_registry.resolve_workset_registry_path(
                ws.root, load_doc(ws.root / "settings.yaml"),
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
        # Drift I: settings.yaml at the root; drift H: files in workspace/ subdir.
        assert (dest / "settings.yaml").is_file()
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
        # Drift I: settings.yaml at the root; drift H: files in workspace/ subdir.
        assert (dest / "settings.yaml").is_file()
        assert (dest / "box_data").is_dir()
        assert (dest / "workspace" / "file.txt").read_text() == "live"
        assert not pdir.exists()


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

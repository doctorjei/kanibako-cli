"""Tests for the redesigned kanibako box move command."""

from __future__ import annotations

import argparse
import os

import pytest

from kanibako.commands.box._lifecycle import run_move
from kanibako.settings.config import load_config
from kanibako.settings.config_io import load_doc
from kanibako.settings.paths import load_std_paths, resolve_project
from kanibako.utils import project_hash


def _move_args(old, new, *, force=True, to_default=False, to_standalone=False,
               to_workset=None, name=None):
    return argparse.Namespace(
        old=str(old) if old is not None else None,
        new=str(new) if new is not None else None,
        force=force,
        to_default=to_default,
        to_standalone=to_standalone,
        to_workset=to_workset,
        name=name,
    )


class TestBoxMove:
    def test_move_project(self, config_file, tmp_home, credentials_dir):
        """Move a project workspace to a new location (records rewritten)."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "myproject"
        project_dir.mkdir()
        (project_dir / "f.txt").write_text("data")
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        dest = tmp_home / "newlocation"
        rc = run_move(_move_args(project_dir, dest))
        assert rc == 0
        assert dest.is_dir()
        assert (dest / "f.txt").read_text() == "data"
        assert not project_dir.exists()

        # PRIMARY membership updated to the new path.
        from kanibako.settings.paths import load_primary_boxes
        boxes = load_primary_boxes(std.primary_workset)
        assert str(dest) in boxes.values()
        assert str(project_dir) not in boxes.values()

        # P8b/Option A: the moved box resolves at the new workspace from the
        # membership (above), not an on-disk ``resolved.workspace``.
        proj = resolve_project(std, config, project_dir=str(dest), initialize=False)
        # The primary move re-derives the box name from the new workspace basename.
        assert boxes.get(proj.name) == str(dest)
        assert proj.project_path == dest.resolve()
        assert proj.project_hash == project_hash(str(dest.resolve()))
        assert "project" not in load_doc(proj.metadata_path / "box.yaml")

    def test_move_requires_both_paths(self, config_file, tmp_home, credentials_dir):
        """move with a missing path returns an error (no cwd fallback)."""
        rc = run_move(_move_args(str(tmp_home / "dest"), None))
        assert rc == 1

    def test_move_same_path_errors(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "proj"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        rc = run_move(_move_args(project_dir, project_dir))
        assert rc == 1

    def test_move_dest_exists_errors(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "proj"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        dest = tmp_home / "existing"
        dest.mkdir()
        rc = run_move(_move_args(project_dir, dest))
        assert rc == 1

    def test_move_no_metadata_errors(self, config_file, tmp_home, credentials_dir):
        project_dir = tmp_home / "plain"
        project_dir.mkdir()
        dest = tmp_home / "newplace"
        rc = run_move(_move_args(project_dir, dest))
        assert rc == 1

    def test_move_with_workset_target(self, config_file, tmp_home, credentials_dir):
        """move + --workset relocates AND changes ownership."""
        from kanibako.project.workset import create_workset, load_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("ws", tmp_home / "ws_root", std)
        project_dir = tmp_home / "movable"
        project_dir.mkdir()
        (project_dir / "f.txt").write_text("x")
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # Land outside the workset (external member of ws).
        dest = tmp_home / "moved_ext"
        rc = run_move(_move_args(project_dir, dest, to_workset="ws"))
        assert rc == 0
        assert dest.is_dir()
        ws2 = load_workset(ws.root, ws.name)
        assert any(p.name == "movable" for p in ws2.projects)


    def test_move_keeps_a_named_box_logs(self, config_file, tmp_home, credentials_dir):
        """🛑 A move RELEASES the box from its old place through ``remove_project``, and
        must not take its logs with it: both files survive a same-workset move."""
        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.paths import (
            WorksetSpec, box_log_files, resolve_workset_project,
        )

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("ws", tmp_home / "ws_root", std)
        source = tmp_home / "boxa_src"
        source.mkdir()
        add_project(ws, "boxa", source)
        resolve_workset_project(
            WorksetSpec.from_workset(ws), "boxa", std, config, initialize=True,
        )
        logs = box_log_files(ws.logs_dir, "boxa")
        ws.logs_dir.mkdir(parents=True, exist_ok=True)
        for log in logs:
            log.write_text("x")

        rc = run_move(_move_args(ws.workspaces_dir / "boxa", tmp_home / "boxa_moved"))
        assert rc == 0
        assert [log for log in logs if not log.exists()] == []


    def test_a_failed_retire_is_a_note_and_the_move_succeeds(
        self, config_file, tmp_home, credentials_dir, monkeypatch, capsys,
    ):
        """The old workspace's delete failing leaves it, names it, and keeps rc 0."""
        import shutil

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "stuck"
        project_dir.mkdir()
        (project_dir / "f.txt").write_text("data")
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)
        real_rmtree = shutil.rmtree

        def rmtree(path, *a, **kw):
            if os.fspath(path) == os.fspath(project_dir):
                raise PermissionError(13, "Permission denied", os.fspath(path))
            return real_rmtree(path, *a, **kw)

        monkeypatch.setattr(shutil, "rmtree", rmtree)
        dest = tmp_home / "unstuck"
        assert run_move(_move_args(project_dir, dest)) == 0
        assert (dest / "f.txt").read_text() == "data"
        assert (project_dir / "f.txt").read_text() == "data"
        err = capsys.readouterr().err
        assert f"Note: could not remove the old workspace {project_dir}" in err


def _seed_links(tree, outside):
    """Put inside, escaping, directory, absolute and dangling links in *tree*."""
    (outside / "deep").mkdir(parents=True)
    (outside / "big.txt").write_text("outside data")
    (outside / "deep" / "nested.txt").write_text("nested")
    (tree / "f.txt").write_text("data")
    texts = {
        "q70_in": "f.txt",
        "q70_out": os.path.relpath(outside / "big.txt", os.path.realpath(tree)),
        "q70_dir": os.path.relpath(outside, os.path.realpath(tree)),
        "q70_abs": str(outside / "big.txt"),
        "q70_gone": str(outside / "no-such"),
    }
    for name, text in texts.items():
        (tree / name).symlink_to(text)
    assert (tree / "q70_out").read_text() == "outside data"
    return texts


def _assert_links_verbatim(tree, texts):
    """Each seeded link is a link with its exact text; the outside tree never materialized."""
    for name, text in texts.items():
        assert (tree / name).is_symlink(), name
        assert os.readlink(tree / name) == text, name
    assert not [p for p in tree.rglob("*") if p.name == "nested.txt" and not p.is_symlink()]


class TestBoxMoveKeepsLinks:
    """Q70/Q74: every tree a move copies carries its symlinks verbatim."""

    def test_workspace_links_are_copied_verbatim(self, config_file, tmp_home, credentials_dir):
        """A dangling link no longer aborts the move; an escaping one is not rewritten."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "linked"
        project_dir.mkdir()
        texts = _seed_links(project_dir, tmp_home / "outside")
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        dest = tmp_home / "deeper" / "linked_moved"
        dest.parent.mkdir()
        rc = run_move(_move_args(project_dir, dest))
        assert rc == 0
        assert not project_dir.exists()
        assert (dest / "f.txt").read_text() == "data"
        _assert_links_verbatim(dest, texts)

    def test_workset_to_workset_move_keeps_links_through_the_stash(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The home rides the stash (two copies) and the workspace one copy: all verbatim."""
        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        ws_b = create_workset("wsb", tmp_home / "elsewhere" / "wsb_root", std)
        internal = ws_a.workspaces_dir / "b1"
        internal.mkdir(parents=True)
        add_project(ws_a, "b1", internal, std)
        ws_texts = _seed_links(internal, tmp_home / "outside_ws")
        home = ws_a.projects_dir / "b1" / "home"
        home.mkdir(parents=True, exist_ok=True)
        home_texts = _seed_links(home, tmp_home / "outside_home")

        dest = ws_b.workspaces_dir / "b1"
        rc = run_move(_move_args(internal, dest, to_workset="wsb"))
        assert rc == 0
        assert not internal.exists()
        _assert_links_verbatim(dest, ws_texts)
        _assert_links_verbatim(ws_b.projects_dir / "b1" / "home", home_texts)


class TestTargetWorksetResolutionIsCaseBlind:
    """``_resolve_target_workset`` backs ``box move --to-workset`` and convert-to-workset.

    Spec §0, ``⚑ NAMING RULES``: workset names compare without regard to case, and
    nothing folds them on entry — so an exact-match lookup here refused a workset that
    plainly exists.  ⚑ Fails on the pre-image.
    """

    def test_a_case_variant_resolves_to_the_REGISTERED_workset(
        self, config_file, tmp_home,
    ):
        from kanibako.commands.box._lifecycle import _resolve_target_workset
        from kanibako.project.workset import create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        create_workset("target", tmp_home / "ws_target", std)

        ws = _resolve_target_workset("TarGet", std)
        # The Workset carries the name as REGISTERED, never as typed.
        assert ws.name == "target"

    def test_a_genuine_miss_still_raises(self, config_file, tmp_home):
        """The complement: case-blind is not match-anything."""
        import pytest

        from kanibako.commands.box._lifecycle import _resolve_target_workset
        from kanibako.errors import WorksetError

        config = load_config(config_file)
        std = load_std_paths(config)

        with pytest.raises(WorksetError, match="not found"):
            _resolve_target_workset("nosuchworkset", std)


class TestBoxMoveOfTheWorksetsOwnWorkspace:
    """``run_move`` on a member whose leaf the workset names, wherever that leaf is.

    A leaf under a repointed ``workset.workspaces`` dir, or behind a symlink, is the
    workset's OWN workspace.  Read as external it was refused with a false
    "external-connected" message, because the caller's copy-nothing arm is keyed on
    that same test.
    """

    def _repointed_member(self, std, tmp_home, name="ws1", member="alpha"):
        from kanibako.project.workset import (
            add_project,
            create_workset,
            load_workset,
        )
        from kanibako.settings.config_io import dump_doc, load_doc

        ws = create_workset(name, tmp_home / f"{name}_root", std)
        data = load_doc(ws.root / "workset.yaml")
        data.setdefault("workset", {})["workspaces"] = str(tmp_home / f"{name}-data")
        dump_doc(ws.root / "workset.yaml", data)
        ws = load_workset(ws.root, ws.name)
        leaf = ws.workspaces_dir / member
        leaf.mkdir(parents=True)
        (leaf / "f.txt").write_text("mine")
        add_project(ws, member, leaf, std)
        return ws, leaf

    def test_a_repointed_member_moves_to_an_external_path(self, config_file,
                                                         tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        ws, leaf = self._repointed_member(std, tmp_home)
        dest = tmp_home / "ext" / "alpha"

        rc = run_move(_move_args(leaf, dest))

        assert rc == 0
        assert (dest / "f.txt").read_text() == "mine"
        # the success-path retire removed the member's LEAF and the external
        # member's discoverability link took the spot — not the repointed parent
        assert leaf.is_symlink() and leaf.resolve() == dest.resolve()
        assert leaf.parent.is_dir()

    def test_a_symlinked_member_moves_to_an_external_path(self, config_file,
                                                         tmp_home, credentials_dir):
        import shutil

        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("ws1", tmp_home / "ws1_root", std)
        leaf = ws.workspaces_dir / "alpha"
        leaf.mkdir(parents=True)
        (leaf / "f.txt").write_text("mine")
        add_project(ws, "alpha", leaf, std)
        real = tmp_home / "real"
        shutil.move(str(leaf), str(real))
        leaf.symlink_to(real)
        dest = tmp_home / "ext" / "alpha"

        rc = run_move(_move_args(leaf, dest))

        assert rc == 0
        assert (dest / "f.txt").read_text() == "mine"
        # the user's own directory is never moved and never deleted through the link
        assert (real / "f.txt").read_text() == "mine"

    def test_a_failed_move_of_a_symlinked_member_keeps_the_link(
            self, config_file, tmp_home, credentials_dir, monkeypatch):
        """Rollback deletes only what the op created: the user's ``workspaces/<name>`` link.

        The target unwind released the record as an external member's discoverability
        link and unlinked it; the restore then made an EMPTY in-tree dir in its place.
        """
        import shutil

        import kanibako.commands.box._lifecycle as lc
        from kanibako.project.workset import add_project, create_workset, load_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("ws1", tmp_home / "ws1_root", std)
        leaf = ws.workspaces_dir / "alpha"
        leaf.mkdir(parents=True)
        (leaf / "f.txt").write_text("mine")
        add_project(ws, "alpha", leaf, std)
        real = tmp_home / "real"
        shutil.move(str(leaf), str(real))
        leaf.symlink_to(real)
        dest = tmp_home / "ext" / "alpha"

        def boom(*a, **kw):
            raise RuntimeError("injected late failure")
        monkeypatch.setattr(lc, "write_box_enable_vault", boom)

        with pytest.raises(RuntimeError, match="injected late failure"):
            run_move(_move_args(leaf, dest))

        assert leaf.is_symlink() and os.readlink(leaf) == str(real)
        assert (real / "f.txt").read_text() == "mine"
        assert not dest.exists()
        member = next(p for p in load_workset(ws.root, "ws1").projects if p.name == "alpha")
        assert member.source_path.resolve() == real.resolve()

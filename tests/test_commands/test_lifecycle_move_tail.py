"""A relocation's source tail: the old vault leaves, the old standalone root, and a retry.

A primary source's old vault leaves go through ``remove_path``, as every other mode's do:
a linked leaf loses only its link and the Note says so, and a leaf that will not go is
named.  A standalone source's old root goes once it is empty.  A retry of a move that
already landed says "Nothing to do".
"""

from __future__ import annotations

import argparse
import hashlib
import os

import pytest

from kanibako.commands.box import _lifecycle
from kanibako.commands.box._lifecycle import (
    UNCHANGED,
    TargetSpec,
    execute_lifecycle,
    resolve_lifecycle_target,
    run_convert,
    run_move,
)
from kanibako.runtime.container import remove_box_tree
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_project, resolve_standalone_project


@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    config = load_config(config_file)
    return config, load_std_paths(config), tmp_home


def _digest(path):
    """Content hash of the tree *path*."""
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        h.update(str(p.relative_to(path)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _primary(env):
    """Primary box ``proj``, resolved; its rw leaf exists."""
    config, std, tmp_home = env
    pdir = tmp_home / "proj"
    pdir.mkdir()
    resolve_project(std, config, project_dir=str(pdir), initialize=True)
    state = resolve_lifecycle_target(str(pdir), std, config)
    state.vault_rw.mkdir(parents=True, exist_ok=True)
    return state


def _rename_move(env, state):
    config, std, tmp_home = env
    return execute_lifecycle(
        state, TargetSpec(location=tmp_home / "proj2", ownership=UNCHANGED, name="proj2"),
        std, config, confirm=lambda: True,
    )


def _standalone(env, name="sa"):
    config, std, tmp_home = env
    root = tmp_home / name
    root.mkdir()
    resolve_standalone_project(std, config, project_dir=str(root), initialize=True)
    return root


def _move_args(old, new, *, name=None, to_workset=None):
    return argparse.Namespace(
        old=str(old), new=str(new), force=True, to_default=False, to_standalone=False,
        to_workset=to_workset, name=name,
    )


class TestPrimaryOldVaultLeaves:
    def test_a_linked_leaf_loses_only_its_link_and_the_note_says_so(self, env, capsys):
        state = _primary(env)
        leaf = state.vault_rw
        outside = env[2] / "outside"
        (outside / "sub").mkdir(parents=True)
        (outside / "sub" / "canary.txt").write_text("user data")
        before = _digest(outside)
        for entry in leaf.iterdir():
            entry.unlink()
        leaf.rmdir()
        leaf.symlink_to(outside)

        _rename_move(env, state)

        err = capsys.readouterr().err
        assert not leaf.is_symlink() and not leaf.exists()
        assert _digest(outside) == before
        assert f"removed the link {leaf}; left its target {outside}" in err
        assert "could not remove" not in err

    def test_a_leaf_holding_a_read_only_dir_is_removed(self, env, capsys):
        state = _primary(env)
        sub = state.vault_rw / "sub"
        sub.mkdir()
        (sub / "f.txt").write_text("x")
        os.chmod(sub, 0o555)

        _rename_move(env, state)

        assert not state.vault_rw.exists()
        assert "could not remove" not in capsys.readouterr().err

    def test_a_leaf_that_will_not_go_is_named(self, env, capsys, monkeypatch):
        state = _primary(env)
        leaf = state.vault_rw
        (leaf / "a.txt").write_text("x")
        real = _lifecycle.remove_path

        def refuse(path):
            if path == leaf:
                raise PermissionError(13, "Permission denied", str(path))
            return real(path)

        monkeypatch.setattr(_lifecycle, "remove_path", refuse)

        _rename_move(env, state)

        assert (leaf / "a.txt").read_text() == "x"
        assert f"could not remove the old store of 'proj'; left {leaf}" in capsys.readouterr().err


class TestStandaloneOldRoot:
    def test_an_emptied_old_root_is_removed(self, env):
        config, std, tmp_home = env
        root = _standalone(env)
        assert run_move(_move_args(root, tmp_home / "sa2")) == 0
        assert not root.exists()

    def test_an_old_root_holding_a_user_file_is_kept(self, env):
        config, std, tmp_home = env
        root = _standalone(env)
        (root / "note.txt").write_text("mine")
        assert run_move(_move_args(root, tmp_home / "sa2")) == 0
        assert sorted(p.name for p in root.iterdir()) == ["note.txt"]
        assert (root / "note.txt").read_text() == "mine"

    @pytest.mark.parametrize("target", [{"to_default": True}, {"to_workset": "w1"}])
    def test_an_in_place_convert_out_keeps_the_root_it_lands_at(self, env, target):
        from kanibako.project.workset import create_workset

        config, std, tmp_home = env
        create_workset("w1", tmp_home / "w1", std)
        root = _standalone(env)
        if (root / "canon").exists():
            remove_box_tree(root / "canon")
        args = argparse.Namespace(old=str(root), force=True, to_default=False,
                                  to_standalone=False, to_workset=None, move=None, name=None)
        for key, value in target.items():
            setattr(args, key, value)
        assert run_convert(args) == 0
        assert root.is_dir()
        assert resolve_lifecycle_target(str(root), std, config).workspace_path == root


class TestRetryOfACompletedMove:
    def test_a_standalone_retry_has_nothing_to_do(self, env, capsys):
        config, std, tmp_home = env
        root, dest = _standalone(env), tmp_home / "sa2"
        assert run_move(_move_args(root, dest)) == 0
        capsys.readouterr()
        before = _digest(tmp_home)

        assert run_move(_move_args(root, dest)) == 1

        err = capsys.readouterr().err
        assert f"Nothing to do: no box is at {root}, and {dest} already is box" in err
        assert _digest(tmp_home) == before

    def test_a_primary_retry_has_nothing_to_do(self, env, capsys):
        config, std, tmp_home = env
        pdir, dest = tmp_home / "proj", tmp_home / "proj2"
        pdir.mkdir()
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        assert run_move(_move_args(pdir, dest)) == 0
        capsys.readouterr()

        assert run_move(_move_args(pdir, dest)) == 1

        assert "Nothing to do:" in capsys.readouterr().err

    @pytest.mark.parametrize("flags", [{"name": "other"}, {"to_workset": "nosuch"}])
    def test_a_retry_asking_for_a_different_box_still_errors(self, env, capsys, flags):
        config, std, tmp_home = env
        root, dest = _standalone(env), tmp_home / "sa2"
        assert run_move(_move_args(root, dest)) == 0
        capsys.readouterr()

        assert run_move(_move_args(root, dest, **flags)) == 1

        assert "Nothing to do" not in capsys.readouterr().err

    def test_an_unknown_path_still_errors(self, env, capsys):
        tmp_home = env[2]
        (tmp_home / "never").mkdir()
        (tmp_home / "plain").mkdir()
        assert run_move(_move_args(tmp_home / "never", tmp_home / "plain")) == 1
        assert run_move(_move_args(tmp_home / "gone", tmp_home / "plain")) == 1
        err = capsys.readouterr().err
        assert "Nothing to do" not in err
        assert f"No project data found for {tmp_home / 'never'}" in err

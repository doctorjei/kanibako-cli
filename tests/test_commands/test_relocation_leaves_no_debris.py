"""A relocation or duplicate that fails part-way leaves no half-copied destination.

A copy that raises or is interrupted must remove the root it created, read-only
directories included, and never a root that already existed.  Whatever cannot be
removed, and whatever a successful move leaves behind, is named in a Note.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat

import pytest

from kanibako.commands.box import _duplicate as dup
from kanibako.commands.box import _lifecycle as lc
from kanibako.commands.box._lifecycle import (
    TargetSpec,
    execute_lifecycle,
    resolve_lifecycle_target,
)
from kanibako.project.workset import add_project, create_workset
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_project
from tests.support.protected_trees import reap_box_stores

needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory permissions")


@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    config = load_config(config_file)
    return config, load_std_paths(config), tmp_home


def _primary(env, name="proj"):
    config, std, tmp_home = env
    pdir = tmp_home / name
    (pdir / "ro").mkdir(parents=True)
    (pdir / "file.txt").write_text("keep")
    (pdir / "ro" / "r.txt").write_text("r")
    (pdir / "ro").chmod(0o555)
    resolve_project(std, config, project_dir=str(pdir), initialize=True)
    return pdir


def _copy_then(exc):
    """A tree copier that copies everything, then fails as *exc* would mid-copy."""
    real = lc.copy_tree_keeping_links

    def copier(src, dst, *a, **kw):
        real(src, dst, *a, **kw)
        raise exc

    return copier


def _tree(path):
    return sorted((str(p.relative_to(path)), stat.S_IMODE(p.lstat().st_mode),
                   p.read_bytes() if p.is_file() else b"") for p in path.rglob("*"))


@pytest.fixture(autouse=True)
def _reopen(tmp_home):
    """Reap the protected box stores a convert seeds, then re-open what remains."""
    yield
    reap_box_stores(tmp_home)
    for root, dirs, _ in os.walk(tmp_home):
        for d in dirs:
            p = os.path.join(root, d)
            if not os.path.islink(p):
                os.chmod(p, 0o755)


class TestAFailedCopyRemovesItsDestination:

    @pytest.mark.parametrize("exc", [KeyboardInterrupt(), shutil.Error([("a", "b", "x")])])
    @pytest.mark.parametrize("ownership", [None, "standalone"])
    def test_a_copy_that_fails_leaves_no_destination(self, env, monkeypatch, exc, ownership):
        config, std, tmp_home = env
        pdir = _primary(env)
        before = _tree(pdir)
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "moved"
        monkeypatch.setattr(lc, "copy_tree_keeping_links", _copy_then(exc))
        with pytest.raises(type(exc)):
            spec = (TargetSpec(location=dest) if ownership is None
                    else TargetSpec(location=dest, ownership=ownership))
            execute_lifecycle(state, spec, std, config, confirm=lambda: True)
        assert not dest.exists()
        assert _tree(pdir) == before

    def test_an_interrupted_workset_to_workset_move_leaves_no_destination(
        self, env, monkeypatch,
    ):
        config, std, tmp_home = env
        ws1 = create_workset("ws1", tmp_home / "ws1_root", std)
        ws2 = create_workset("ws2", tmp_home / "ws2_root", std)
        leaf = ws1.workspaces_dir / "alpha"
        (leaf / "ro").mkdir(parents=True)
        (leaf / "file.txt").write_text("keep")
        (leaf / "ro").chmod(0o555)
        add_project(ws1, "alpha", leaf, std)
        before = _tree(leaf)
        state = resolve_lifecycle_target(str(leaf), std, config)
        dest = ws2.workspaces_dir / "alpha"
        monkeypatch.setattr(lc, "copy_tree_keeping_links", _copy_then(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            execute_lifecycle(state, TargetSpec(location=dest, ownership="ws2"),
                              std, config, confirm=lambda: True)
        assert not dest.exists()
        assert _tree(leaf) == before

    def test_the_retry_after_a_failed_copy_succeeds(self, env, monkeypatch):
        config, std, tmp_home = env
        pdir = _primary(env)
        dest = tmp_home / "moved"
        state = resolve_lifecycle_target(str(pdir), std, config)
        with monkeypatch.context() as m:
            m.setattr(lc, "copy_tree_keeping_links", _copy_then(KeyboardInterrupt()))
            with pytest.raises(KeyboardInterrupt):
                execute_lifecycle(state, TargetSpec(location=dest), std, config,
                                  confirm=lambda: True)
        state = resolve_lifecycle_target(str(pdir), std, config)
        execute_lifecycle(state, TargetSpec(location=dest), std, config, confirm=lambda: True)
        assert (dest / "file.txt").read_text() == "keep"

    def test_a_destination_that_appeared_before_the_copy_is_never_removed(self, env):
        config, std, tmp_home = env
        pdir = _primary(env)
        dest = tmp_home / "moved"
        state = resolve_lifecycle_target(str(pdir), std, config)

        def appears():
            dest.mkdir()
            (dest / "theirs.txt").write_text("theirs")
            return True

        with pytest.raises(FileExistsError):
            execute_lifecycle(state, TargetSpec(location=dest), std, config, confirm=appears)
        assert (dest / "theirs.txt").read_text() == "theirs"

    def test_a_leftover_the_unwind_cannot_remove_is_named(self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "made"
        root.mkdir()
        monkeypatch.setattr(lc, "remove_path", lambda p: False)
        lc._unwind_created_root(root)
        err = capsys.readouterr().err
        assert f"Note: could not remove {root}, which this operation created" in err


class TestALateFailureKeepsAReusedVault:

    def test_a_primary_move_keeps_its_own_vault_when_a_later_step_fails(self, env, monkeypatch):
        config, std, tmp_home = env
        pdir = _primary(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        state.vault_rw.mkdir(parents=True, exist_ok=True)
        (state.vault_rw / "v.txt").write_text("vault")

        def boom(*a, **kw):
            raise RuntimeError("late")

        monkeypatch.setattr(lc, "_carry_box_logs", boom)
        with pytest.raises(RuntimeError, match="late"):
            execute_lifecycle(state, TargetSpec(location=tmp_home / "moved"), std, config,
                              confirm=lambda: True)
        assert (state.vault_rw / "v.txt").read_text() == "vault"


class TestAFailedConvertKeepsARepointedStore:
    """``workset.boxes`` repointed at a dir that already exists: the store is the user's."""

    def _setup(self, env):
        config, std, tmp_home = env
        pdir = tmp_home / "proj"
        pdir.mkdir()
        (pdir / "file.txt").write_text("keep")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        store = tmp_home / "store"
        store.mkdir()
        (store / "keep.txt").write_text("theirs")
        (pdir / "workset.yaml").write_text(f"workset:\n  boxes: {store}\n")
        return config, std, pdir, store, resolve_lifecycle_target(str(pdir), std, config)

    def test_a_failed_metadata_copy_keeps_the_store(self, env, monkeypatch):
        config, std, pdir, store, state = self._setup(env)
        real = lc.copy_tree_keeping_links

        def copier(src, dst, *a, **kw):
            real(src, dst, *a, **kw)
            if dst == store:
                raise shutil.Error([("a", "b", "x")])

        monkeypatch.setattr(lc, "copy_tree_keeping_links", copier)
        with pytest.raises(shutil.Error):
            execute_lifecycle(state, TargetSpec(location=lc.INPLACE, ownership="standalone"),
                              std, config, confirm=lambda: True)
        assert (store / "keep.txt").read_text() == "theirs"

    def test_a_later_failure_keeps_the_store(self, env, monkeypatch, capsys):
        config, std, pdir, store, state = self._setup(env)
        (pdir / "workset.yaml").chmod(0o644)
        doc = (pdir / "workset.yaml").read_bytes()

        def boom(*a, **kw):
            raise RuntimeError("late")

        monkeypatch.setattr(lc, "_carry_vault_contents", boom)
        with pytest.raises(RuntimeError, match="late"):
            execute_lifecycle(state, TargetSpec(location=lc.INPLACE, ownership="standalone"),
                              std, config, confirm=lambda: True)
        assert (store / "keep.txt").read_text() == "theirs"
        assert (pdir / "workset.yaml").read_bytes() == doc
        assert stat.S_IMODE((pdir / "workset.yaml").stat().st_mode) == 0o644
        err = capsys.readouterr().err
        assert f"Note: {store} existed before this operation; it still holds home, " \
               "a partial leftover that is yours to remove." in err


@needs_non_root
class TestAFailedInPlaceConvertLeavesTheRootAsFound:
    """The root ``workset.yaml`` alone reads as standalone: a leftover one makes every retry
    answer "Nothing to do"."""

    def test_a_late_failure_restores_the_root_and_the_retry_converts(self, env, monkeypatch):
        config, std, tmp_home = env
        pdir = _primary(env)
        (pdir / ".gitignore").write_text("mine\n")
        before = _tree(pdir)

        def boom(*a, **kw):
            raise RuntimeError("late")

        with monkeypatch.context() as m:
            m.setattr(lc, "_carry_vault_contents", boom)
            with pytest.raises(RuntimeError, match="late"):
                execute_lifecycle(
                    resolve_lifecycle_target(str(pdir), std, config),
                    TargetSpec(location=lc.INPLACE, ownership="standalone"),
                    std, config, confirm=lambda: True)
        assert _tree(pdir) == before
        execute_lifecycle(resolve_lifecycle_target(str(pdir), std, config),
                          TargetSpec(location=lc.INPLACE, ownership="standalone"),
                          std, config, confirm=lambda: True)
        assert (pdir / "workspace" / ".gitignore").read_text() == "mine\n"
        assert (pdir / "workset.yaml").is_file()

    def test_a_read_only_directory_is_swept_in_and_lifted_out(self, env):
        config, std, tmp_home = env
        pdir = _primary(env)
        execute_lifecycle(resolve_lifecycle_target(str(pdir), std, config),
                          TargetSpec(location=lc.INPLACE, ownership="standalone"),
                          std, config, confirm=lambda: True)
        ro = pdir / "workspace" / "ro"
        assert (ro / "r.txt").read_text() == "r"
        assert stat.S_IMODE(ro.stat().st_mode) == 0o555
        execute_lifecycle(resolve_lifecycle_target(str(pdir), std, config),
                          TargetSpec(location=lc.INPLACE, ownership="default"),
                          std, config, confirm=lambda: True)
        assert (pdir / "ro" / "r.txt").read_text() == "r"
        assert stat.S_IMODE((pdir / "ro").stat().st_mode) == 0o555
        assert not (pdir / "workspace").exists()

    def test_a_failed_sweep_puts_a_read_only_directory_back(self, env, monkeypatch):
        config, std, tmp_home = env
        pdir = _primary(env)
        before = _tree(pdir)

        def boom(*a, **kw):
            raise RuntimeError("late")

        monkeypatch.setattr(lc, "_copy_metadata", boom)
        with pytest.raises(RuntimeError, match="late"):
            execute_lifecycle(resolve_lifecycle_target(str(pdir), std, config),
                              TargetSpec(location=lc.INPLACE, ownership="standalone"),
                              std, config, confirm=lambda: True)
        assert _tree(pdir) == before


class TestLeftoversInAnExistingDirectoryAreNamed:

    def test_only_what_the_op_added_is_named(self, tmp_path, capsys):
        (tmp_path / "theirs.txt").write_text("theirs")
        before = lc._entry_names(tmp_path)
        (tmp_path / "added").mkdir()
        lc.note_added_leftovers(tmp_path, before)
        assert capsys.readouterr().err == (
            f"Note: {tmp_path} existed before this operation; it still holds added, "
            "a partial leftover that is yours to remove.\n")
        assert (tmp_path / "theirs.txt").read_text() == "theirs"

    def test_nothing_added_says_nothing(self, tmp_path, capsys):
        (tmp_path / "theirs.txt").write_text("theirs")
        lc.note_added_leftovers(tmp_path, lc._entry_names(tmp_path))
        assert capsys.readouterr().err == ""

    def test_an_existing_workset_leaf_is_kept_and_named(self, env, capsys):
        config, std, tmp_home = env
        ws = create_workset("ws1", tmp_home / "ws1_root", std)
        leaf = ws.projects_dir / "alpha"
        leaf.mkdir(parents=True)
        (leaf / "theirs.txt").write_text("theirs")
        existed = lc._existing_member_leaves(ws, "alpha")
        (leaf / "home").mkdir()
        lc._unwind_target_member(ws, "alpha", existed)
        assert (leaf / "theirs.txt").read_text() == "theirs"
        assert f"Note: {leaf} existed before this operation; it still holds home, " \
               in capsys.readouterr().err


@needs_non_root
class TestLeftoversOfASuccessfulMoveAreNamed:

    def test_the_old_workspace_note_names_what_remains(self, env, capsys):
        config, std, tmp_home = env
        pdir = _primary(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "moved"
        execute_lifecycle(state, TargetSpec(location=dest), std, config, confirm=lambda: True)
        err = capsys.readouterr().err
        assert f"Note: could not fully remove the old workspace {pdir}" in err
        held = err.split(f"{pdir} still holds ", 1)[1].split(", a partial leftover", 1)[0]
        assert "ro" in held.split(", ")

    def test_a_primary_to_standalone_convert_names_the_old_vault_it_leaves(self, env, capsys):
        config, std, tmp_home = env
        pdir = _primary(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        vault = state.vault_rw
        (vault / "ro").mkdir(parents=True)
        (vault / "ro" / "r.txt").write_text("r")
        (vault / "ro").chmod(0o555)
        execute_lifecycle(state, TargetSpec(location=tmp_home / "sa", ownership="standalone"),
                          std, config, confirm=lambda: True)
        err = capsys.readouterr().err
        assert vault.is_dir()
        assert f"Note: left the vault at {vault} in place — it could not be fully removed" in err


class TestAFailedDuplicateRemovesItsDestination:

    def _args(self, src, dst, to_mode=None):
        return argparse.Namespace(
            source_path=str(src), new_path=str(dst), to_mode=to_mode, bare=False,
            force=True, box=None, workset=None, project_name=None, register=False)

    @pytest.mark.parametrize("to_mode", [None, "standalone"])
    def test_an_interrupted_duplicate_leaves_no_destination(self, env, monkeypatch, to_mode):
        config, std, tmp_home = env
        pdir = _primary(env)
        before = _tree(pdir)
        dst = tmp_home / "copy"
        monkeypatch.setattr(dup, "copy_tree_keeping_links", _copy_then(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            dup.run_duplicate(self._args(pdir, dst, to_mode))
        assert not dst.exists()
        assert _tree(pdir) == before

    def test_a_duplicate_into_an_existing_directory_keeps_it(self, env, monkeypatch, capsys):
        config, std, tmp_home = env
        pdir = _primary(env)
        dst = tmp_home / "copy"
        dst.mkdir()
        (dst / "theirs.txt").write_text("theirs")
        monkeypatch.setattr(dup, "copy_tree_keeping_links", _copy_then(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            dup.run_duplicate(self._args(pdir, dst))
        assert (dst / "theirs.txt").read_text() == "theirs"
        err = capsys.readouterr().err
        assert f"Note: {dst} existed before this operation; it still holds " in err
        assert "theirs.txt" not in err

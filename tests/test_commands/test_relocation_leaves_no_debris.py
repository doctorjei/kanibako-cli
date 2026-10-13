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
from kanibako.errors import ProjectError
from kanibako.runtime import container as container_mod
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
        monkeypatch.setattr(container_mod, "remove_path", lambda p: False)
        assert lc._unwind_created_root(root) is False
        err = capsys.readouterr().err
        assert f"Note: could not remove {root}, which this operation created" in err

    def test_the_metadata_tree_remover_reports_its_leftover_too(self, tmp_path, capsys):
        """The escalating box-tree remover goes through the same verdict and Note."""
        root = tmp_path / "made"
        root.mkdir()
        assert lc._unwind_created_root(root, lambda p: False) is False
        assert f"Note: could not remove {root}" in capsys.readouterr().err
        assert lc._unwind_created_root(root, lc.remove_box_tree) is True
        assert not root.exists()


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


    def test_an_interrupt_in_the_success_tail_keeps_both_vaults_and_names_the_old(
        self, env, monkeypatch, capsys,
    ):
        config, std, tmp_home = env
        pdir = tmp_home / "proj"
        pdir.mkdir()
        (pdir / "file.txt").write_text("keep")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)
        state = resolve_lifecycle_target(str(pdir), std, config)
        state.vault_rw.mkdir(parents=True, exist_ok=True)
        (state.vault_rw / "precious.txt").write_text("precious")

        def boom(*a, **kw):
            raise KeyboardInterrupt()

        monkeypatch.setattr(lc, "_relocate_channel_partition", boom)
        with pytest.raises(KeyboardInterrupt):
            execute_lifecycle(state, TargetSpec(location=lc.INPLACE, ownership="standalone"),
                              std, config, confirm=lambda: True)
        assert (state.vault_rw / "precious.txt").read_text() == "precious"
        assert (pdir / "vault" / "rw" / "precious.txt").read_text() == "precious"
        err = capsys.readouterr().err
        assert "yours to remove" not in err
        assert (f"Note: could not remove the old store of '{state.name}': interrupted; left "
                in err)
        assert str(state.vault_rw) in err

    def test_a_claim_in_an_existing_vault_folder_is_named(self, env, monkeypatch, capsys):
        config, std, tmp_home = env
        pdir = tmp_home / "proj"
        (pdir / "vault").mkdir(parents=True)
        (pdir / "vault" / "notes.txt").write_text("note")
        resolve_project(std, config, project_dir=str(pdir), initialize=True)

        def boom(*a, **kw):
            raise RuntimeError("late")

        monkeypatch.setattr(lc, "_carry_vault_contents", boom)
        with pytest.raises(RuntimeError, match="late"):
            execute_lifecycle(resolve_lifecycle_target(str(pdir), std, config),
                              TargetSpec(location=lc.INPLACE, ownership="standalone"),
                              std, config, confirm=lambda: True)
        assert (pdir / "vault" / "notes.txt").read_text() == "note"
        assert f"Note: {pdir / 'vault'} existed before this operation; it still holds " \
               ".gitignore, a partial leftover" in capsys.readouterr().err


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

    def test_the_old_workspace_note_names_what_remains(self, env, capsys, monkeypatch):
        config, std, tmp_home = env
        pdir = _primary(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        # The escalating remover's own verdict on an entry it cannot take (in-box root).
        real = lc.remove_box_tree
        monkeypatch.setattr(lc, "remove_box_tree", lambda p: p != pdir and real(p))
        dest = tmp_home / "moved"
        execute_lifecycle(state, TargetSpec(location=dest), std, config, confirm=lambda: True)
        err = capsys.readouterr().err
        assert f"Note: could not fully remove the old workspace {pdir}" in err
        held = err.split(f"{pdir} still holds ", 1)[1].split(", a partial leftover", 1)[0]
        assert "ro" in held.split(", ")

    def test_a_primary_to_standalone_convert_names_the_old_vault_it_leaves(
            self, env, capsys, monkeypatch):
        config, std, tmp_home = env
        pdir = _primary(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        vault = state.vault_rw
        (vault / "ro").mkdir(parents=True)
        (vault / "ro" / "r.txt").write_text("r")
        # The escalating remover's own verdict on an entry it cannot take (in-box root).
        real = lc.remove_path
        monkeypatch.setattr(lc, "remove_path", lambda p: p != vault and real(p))
        execute_lifecycle(state, TargetSpec(location=tmp_home / "sa", ownership="standalone"),
                          std, config, confirm=lambda: True)
        err = capsys.readouterr().err
        assert vault.is_dir()
        assert f"Note: could not remove the old store of '{state.name}'; left " in err
        assert str(vault) in err


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


# ---------------------------------------------------------------------------
# The point of no return: an interrupt anywhere loses nothing and leaves one row
# ---------------------------------------------------------------------------

_SEEDS = ("XWS", "XRW", "XRO", "XHOME", "XSNAP", "XMAIL")


def _digest(path):
    import hashlib
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(path)).encode() + p.read_bytes())
    return h.hexdigest()


def _rows(tmp_home):
    """Every box row of every registry: ``(section, name, path)``."""
    import yaml
    out = []
    for f in sorted(tmp_home.rglob("*.yaml")):
        if (tmp_home / "t") in f.parents:
            continue
        doc = yaml.safe_load(f.read_text()) or {}
        if not isinstance(doc, dict):
            continue
        for section in ("standalone", "boxes"):
            if isinstance(doc.get(section), dict):
                out += [(section, name, path) for name, path in doc[section].items()]
    return out


def _homes(state, std):
    """Where each seed lives for the box *state* describes."""
    from kanibako.channels.channels import own_partition_dirs
    from kanibako.settings.paths import box_log_files, box_logs_dir_for
    from kanibako.snapshots import box_snapshot_store

    logs = box_logs_dir_for(std, state.mode, state.metadata_path,
                            state.ws.root if state.ws else None,
                            workset_name=state.ws.name if state.ws else None)
    mail = own_partition_dirs(std, lc._state_ws_token(state), state.name,
                              ws_root=lc._state_ws_root(state, std)).mailbox
    return {"XWS": state.workspace_path, "XRW": state.vault_rw, "XRO": state.vault_ro,
            "XHOME": state.shell_path, "XSNAP": box_snapshot_store(state.vault_rw, state.name),
            "XMAIL": mail, "log": box_log_files(logs, state.name)[0]}


def _seed(state, std, tag):
    """Seed every tree the box *state* describes; return ``{seed: digest}`` and its homes."""
    homes = _homes(state, std)
    for seed in _SEEDS:
        (homes[seed] / seed / "sub").mkdir(parents=True, exist_ok=True)
        (homes[seed] / seed / "f1").write_text(f"{tag}-{seed}")
        (homes[seed] / seed / "sub" / "f2").write_text(f"{tag}-{seed}-2")
    homes["log"].parent.mkdir(parents=True, exist_ok=True)
    homes["log"].write_text(f"{tag}-log")
    return {seed: _digest(homes[seed] / seed) for seed in _SEEDS}, homes


class _Scenario:
    """One relocation: its source, its spec, and whether it stays where it is."""

    def __init__(self, env, kind):
        config, std, tmp_home = env
        self.std, self.config, self.tmp_home, self.kind = std, config, tmp_home, kind
        p = tmp_home / "p"
        p.mkdir(exist_ok=True)
        inplace = lc.INPLACE
        if kind in ("A", "D", "F", "PM"):
            src = p / "k"
            src.mkdir()
            (src / "aaa.txt").write_text("a")
            resolve_project(std, config, project_dir=str(src), initialize=True)
        elif kind == "WR":
            ws = create_workset("W", p / "W", std)
            src = ws.workspaces_dir / "k"
            src.mkdir(parents=True)
            add_project(ws, "k", src, std)
        elif kind == "WW":
            ws1 = create_workset("W1", p / "W1", std)
            create_workset("W2", p / "W2", std)
            src = ws1.workspaces_dir / "m"
            src.mkdir(parents=True)
            add_project(ws1, "m", src, std)
        else:
            from kanibako.settings.paths import resolve_standalone_project
            src = p / "s"
            src.mkdir()
            resolve_standalone_project(std, config, str(src), initialize=True)
        if kind in ("D", "E", "F"):
            create_workset("W", p / "W", std)
        if kind == "F":
            execute_lifecycle(resolve_lifecycle_target(str(src), std, config),
                              TargetSpec(location=inplace, ownership="W"), std, config,
                              confirm=lambda: True)
        if kind == "Bs":
            (p / "x").mkdir()
        self.src = src
        self.spec = {
            "A": TargetSpec(location=inplace, ownership="standalone"),
            "B": TargetSpec(location=p / "s2", ownership="standalone"),
            "Bs": TargetSpec(location=p / "x" / "s", ownership="standalone"),
            "C": TargetSpec(location=inplace, ownership="default"),
            "D": TargetSpec(location=inplace, ownership="W"),
            "E": TargetSpec(location=inplace, ownership="W"),
            "F": TargetSpec(location=inplace, ownership="standalone"),
            "WW": TargetSpec(location=lc.BARE_INTO_WS, ownership="W2"),
            "PM": TargetSpec(location=p / "k2"),
            # An in-tree member renamed in its own workset: its leaf is copied and retired.
            "WR": TargetSpec(location=inplace, ownership="W", name="k3"),
        }[kind]
        self.inplace = kind not in ("B", "Bs", "WW", "PM", "WR")
        self.state = resolve_lifecycle_target(str(src), std, config)
        self.digests, self.src_homes = _seed(self.state, std, kind)
        self.src_rows = _rows(tmp_home)

    def run(self):
        return execute_lifecycle(resolve_lifecycle_target(str(self.src), self.std, self.config),
                                 self.spec, self.std, self.config, confirm=lambda: True)


#: Points before the source row is dropped (rolled back) and after it (the success tail).
_BEFORE = [("_carry_box_logs", "after"), ("_stash_source_marker", "after"),
           ("repoint_box_mounted_links", "before"), ("_drop_source_row", "after")]
#: The registry writes: each must be undone, whatever it wrote, from the moment it returns.
_REGISTRY = [("_safe_unregister", "after"), ("_drop_registry_row", "after"),
             ("register_primary_box_name", "after"),
             ("assign_primary_box_name", "after"), ("add_project", "after"),
             ("release_project", "after")]
_TAIL = [("_finish_relocation", "before"), ("_relocate_channel_partition", "before"), ("_relocate_snapshot_store", "before"),
         ("_relocate_snapshot_store", "after"), ("remove_path", "after"),
         ("_retire_old_store", "before"), ("_retire_old_workspace", "before"),
         ("_dispose_stash", "before")]


def _interrupt_at(monkeypatch, name, when):
    fired = []
    real = getattr(lc, name)

    def wrapped(*a, **kw):
        if when == "before" and not fired:
            fired.append(name)
            raise KeyboardInterrupt(name)
        out = real(*a, **kw)
        if not fired:
            fired.append(name)
            raise KeyboardInterrupt(name)
        return out

    monkeypatch.setattr(lc, name, wrapped)
    return fired


def _named(path, err, root):
    """*path*, or a directory holding it, is named in *err* as a whole path."""
    import re
    named = set(re.findall(r"/[^\s,;]+", err))
    return any(str(p) in named for p in (path, *path.parents) if root in p.parents)


@pytest.fixture
def stash_dir(tmp_home, monkeypatch):
    import tempfile
    (tmp_home / "t").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_home / "t"))


@pytest.mark.usefixtures("stash_dir")
class TestAnInterruptLosesNothingAndLeavesOneRow:
    """Before the source row is dropped an interrupt unwinds to the source alone; after it
    the destination is the box, nothing is unwound, and the Note names what is left."""

    @pytest.mark.parametrize("point", _BEFORE + _REGISTRY + _TAIL,
                             ids=lambda p: f"{p[0]}-{p[1]}")
    @pytest.mark.parametrize("kind", ["A", "B", "Bs", "C", "D", "E", "F", "WW", "PM", "WR"])
    def test_every_point(self, env, monkeypatch, capsys, kind, point):
        sc = _Scenario(env, kind)
        capsys.readouterr()
        with monkeypatch.context() as m:
            fired = _interrupt_at(m, *point)
            try:
                sc.run()
            except KeyboardInterrupt:
                assert fired
        err = capsys.readouterr().err
        rows = _rows(sc.tmp_home)
        assert len(rows) == 1, rows
        rolled_back = bool(fired) and point not in _TAIL
        if rolled_back:
            assert sorted(rows) == sorted(sc.src_rows)
        _section, _name, where = rows[0]
        box = resolve_lifecycle_target(where, sc.std, sc.config)
        homes = _homes(box, sc.std)
        for seed in _SEEDS:
            if _digest(homes[seed] / seed) == sc.digests[seed]:
                continue
            # Only steps 4b and 4c have no finishing command: interrupted in the tail,
            # they leave the source copy and the Note names it.
            assert fired and not rolled_back and seed in ("XSNAP", "XMAIL"), (seed, err)
            assert _digest(sc.src_homes[seed] / seed) == sc.digests[seed]
            assert _named(sc.src_homes[seed], err, sc.tmp_home), (seed, err)
        assert homes["log"].read_text() == f"{kind}-log"
        if fired and not rolled_back:
            # A move's old workspace is a full copy of the user's files until STEP 5.
            for seed in ("XRW", "XRO", "XHOME", *(() if sc.inplace else ("XWS",))):
                left = sc.src_homes[seed]
                if left.exists() and left != homes[seed]:
                    assert _named(left, err, sc.tmp_home), (seed, err)
        if rolled_back:
            assert sorted(_rows(sc.tmp_home)) == sorted(sc.src_rows)
            new = sc.run()
            assert _digest(_homes(new, sc.std)["XHOME"] / "XHOME") == sc.digests["XHOME"]
        elif sc.inplace and kind in _VACATING and (sc.src_homes["XSNAP"] / "XSNAP").exists():
            # The retry carries the store step 4c left in the vacated standalone root,
            # marked as a real snapshot write leaves it.
            _mark(sc.src_homes["XSNAP"])
            new = sc.run()
            assert _digest(_homes(new, sc.std)["XSNAP"] / "XSNAP") == sc.digests["XSNAP"]
            assert not (sc.src_homes["XSNAP"] / "XSNAP").exists()
        elif sc.inplace:
            with pytest.raises(ProjectError, match="Nothing to do") as exc:
                sc.run()
            # The box's store is empty exactly when 4c left it behind: then the cure is named.
            stranded = (sc.src_homes["XSNAP"] / "XSNAP").exists()
            assert ("an interrupted run left" in str(exc.value)) is stranded, str(exc.value)
            if stranded:
                assert str(homes["XSNAP"]) in str(exc.value)


#: The in-place converts OUT of standalone: the root they vacate becomes the workspace.
_VACATING = ("C", "E")


def _mark(store):
    """Mark *store*'s base as every snapshot write does (``kanibako.snapshots``)."""
    from kanibako.snapshots import LAYOUT_MARKER_NAME, LAYOUT_MARKER_VALUE
    (store.parent / LAYOUT_MARKER_NAME).write_text(LAYOUT_MARKER_VALUE)


@pytest.mark.usefixtures("stash_dir")
@pytest.mark.parametrize("kind", _VACATING)
class TestARetryCarriesTheStoreAnInterruptedTailLeft:
    """Interrupted before step 4c, the convert leaves the snapshot store in the standalone
    root it vacated; running the same convert again moves it, and never merges or follows
    a link to do so."""

    def _stranded(self, env, monkeypatch, kind):
        sc = _Scenario(env, kind)
        with monkeypatch.context() as m:
            _interrupt_at(m, "_relocate_snapshot_store", "before")
            with pytest.raises(KeyboardInterrupt):
                sc.run()
        box = resolve_lifecycle_target(str(sc.src), sc.std, sc.config)
        old, new = sc.src_homes["XSNAP"], _homes(box, sc.std)["XSNAP"]
        assert (old / "XSNAP").is_dir() and not new.exists()
        _mark(old)
        return sc, old, new

    def test_an_unmarked_base_is_never_guessed_from(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        from kanibako.snapshots import LAYOUT_MARKER_NAME
        (old.parent / LAYOUT_MARKER_NAME).unlink()
        other = old.parent / "someone_else"
        old.rename(other)
        with pytest.raises(ProjectError, match="Nothing to do"):
            sc.run()
        assert _digest(other / "XSNAP") == sc.digests["XSNAP"]

    def test_old_only_is_moved(self, env, monkeypatch, capsys, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        capsys.readouterr()
        sc.run()
        assert _digest(new / "XSNAP") == sc.digests["XSNAP"]
        assert not old.exists()
        assert f"from {old} to {new}" in capsys.readouterr().err

    def test_new_only_has_nothing_to_do(self, env, monkeypatch, kind):
        sc = _Scenario(env, kind)
        new = _homes(sc.run(), sc.std)["XSNAP"]
        with pytest.raises(ProjectError, match="Nothing to do") as exc:
            sc.run()
        assert "Note:" not in str(exc.value)  # a real no-op: the box has its snapshots
        assert _digest(new / "XSNAP") == sc.digests["XSNAP"]

    def test_an_unmarked_store_named_for_the_box_is_left(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        from kanibako.snapshots import LAYOUT_MARKER_NAME
        (old.parent / LAYOUT_MARKER_NAME).unlink()
        with pytest.raises(ProjectError, match="Nothing to do") as exc:
            sc.run()
        assert "an interrupted run left" in str(exc.value)
        assert _digest(old / "XSNAP") == sc.digests["XSNAP"]
        assert not new.exists()

    def test_an_empty_store_under_the_new_name_takes_the_old(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        new.mkdir(parents=True)
        sc.run()
        assert _digest(new / "XSNAP") == sc.digests["XSNAP"]
        assert not old.exists()

    def test_both_empty_leaves_one_empty_store(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        shutil.rmtree(old / "XSNAP")
        new.mkdir(parents=True)
        sc.run()
        assert new.is_dir() and not os.listdir(new)
        assert not old.exists()

    def test_both_holding_snapshots_is_refused_naming_both(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        (new / "20260101T000000Z").mkdir(parents=True)
        with pytest.raises(ProjectError, match="does not merge") as exc:
            sc.run()
        assert str(old) in str(exc.value) and str(new) in str(exc.value)
        assert _digest(old / "XSNAP") == sc.digests["XSNAP"]
        assert os.listdir(new) == ["20260101T000000Z"]

    def test_a_link_at_the_old_store_is_refused_not_followed(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        outside = sc.tmp_home / "outside"
        shutil.move(str(old), str(outside))
        old.symlink_to(outside)
        with pytest.raises(ProjectError, match="is a symlink") as exc:
            sc.run()
        assert str(old) in str(exc.value)
        assert _digest(outside / "XSNAP") == sc.digests["XSNAP"]
        assert not new.exists()

    def test_a_link_above_the_old_store_is_refused_not_followed(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        vault = old.parent.parent
        outside = sc.tmp_home / "outside"
        shutil.move(str(vault), str(outside))
        vault.symlink_to(outside)
        with pytest.raises(ProjectError, match="is a symlink") as exc:
            sc.run()
        assert str(vault) in str(exc.value)
        assert _digest(outside / old.parent.name / old.name / "XSNAP") == sc.digests["XSNAP"]
        assert not new.exists()

    def test_a_repointed_box_store_is_honored(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        target = sc.tmp_home / "elsewhere"
        target.mkdir()
        new.parent.mkdir(parents=True, exist_ok=True)
        new.symlink_to(target)
        sc.run()
        assert _digest(target / "XSNAP") == sc.digests["XSNAP"]
        assert new.is_symlink() and not old.exists()

    def test_a_broken_box_store_repoint_is_refused(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        new.parent.mkdir(parents=True, exist_ok=True)
        new.symlink_to(sc.tmp_home / "gone")
        with pytest.raises(ProjectError, match="does not lead to a directory"):
            sc.run()
        assert _digest(old / "XSNAP") == sc.digests["XSNAP"]

    def test_a_box_store_repointed_at_the_old_store_is_left(self, env, monkeypatch, kind):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        new.parent.mkdir(parents=True, exist_ok=True)
        new.symlink_to(old)
        with pytest.raises(ProjectError, match="Nothing to do"):
            sc.run()
        assert _digest(old / "XSNAP") == sc.digests["XSNAP"]

    @pytest.mark.parametrize("where", ["store", "base"])
    def test_a_looped_link_is_refused(self, env, monkeypatch, kind, where):
        sc, old, new = self._stranded(env, monkeypatch, kind)
        looped = old if where == "store" else old.parent
        held = sc.tmp_home / "held"
        shutil.move(str(looped), str(held))
        looped.symlink_to(looped)
        with pytest.raises(ProjectError, match="is a symlink") as exc:
            sc.run()
        assert str(looped) in str(exc.value)
        assert not new.exists()


@pytest.mark.usefixtures("stash_dir")
def test_a_users_own_versions_dir_is_never_moved_by_a_no_op(env):
    """An ordinary ``vault/.versions/<box>`` in a primary box's workspace is the user's."""
    from kanibako.snapshots import box_snapshot_store

    config, std, tmp_home = env
    pdir = tmp_home / "p" / "k"
    pdir.mkdir(parents=True)
    resolve_project(std, config, project_dir=str(pdir), initialize=True)
    state = resolve_lifecycle_target(str(pdir), std, config)
    mine = pdir / "vault" / ".versions" / state.name
    (mine / "notes").mkdir(parents=True)
    (mine / "notes" / "a.txt").write_text("user data")
    with pytest.raises(ProjectError, match="Nothing to do") as exc:
        execute_lifecycle(state, TargetSpec(location=lc.INPLACE, ownership="default"),
                          std, config, confirm=lambda: True)
    assert (mine / "notes" / "a.txt").read_text() == "user data"
    assert not box_snapshot_store(state.vault_rw, state.name).exists()
    assert "an interrupted run left" in str(exc.value)


class TestTheTailKeepsWhatItCouldNotCarry:

    @pytest.mark.usefixtures("stash_dir")
    def test_a_failed_snapshot_carry_keeps_the_old_store(self, env, monkeypatch, capsys):
        import kanibako.snapshots as snapshots

        sc = _Scenario(env, "B")

        def boom(*a, **kw):
            raise RuntimeError("no snapshots today")

        monkeypatch.setattr(snapshots, "relocate_snapshot_store", boom)
        sc.run()
        assert _digest(sc.src_homes["XSNAP"] / "XSNAP") == sc.digests["XSNAP"]
        err = capsys.readouterr().err
        assert "could not move the vault snapshots" in err
        assert "still on disk under the old name" in err

    @pytest.mark.usefixtures("stash_dir")
    @pytest.mark.parametrize("kind", ["A", "B", "C", "E", "F"])
    def test_a_successful_relocation_carries_the_snapshots(self, env, kind):
        sc = _Scenario(env, kind)
        new = sc.run()
        homes = _homes(new, sc.std)
        assert _digest(homes["XSNAP"] / "XSNAP") == sc.digests["XSNAP"]
        assert not (sc.src_homes["XSNAP"] / "XSNAP").exists()
        assert len(_rows(sc.tmp_home)) == 1

    @pytest.mark.usefixtures("stash_dir")
    def test_a_rolled_back_same_leaf_move_keeps_the_source_row(self, env, monkeypatch):
        from kanibako.project import registry_store

        sc = _Scenario(env, "Bs")
        name = sc.state.name
        assert registry_store.standalone_root(sc.std.registry, name) == str(sc.src)
        _interrupt_at(monkeypatch, "_carry_box_logs", "after")
        with pytest.raises(KeyboardInterrupt):
            sc.run()
        assert registry_store.standalone_root(sc.std.registry, name) == str(sc.src)
        assert (sc.src / "workset.yaml").is_file()


class TestLandedBindsResolveByIdentity:
    """While the links are repointed the source row is still registered, so the landed
    box is resolved by its identity; its bind set is the one it shows after the op."""

    @pytest.mark.usefixtures("stash_dir")
    @pytest.mark.parametrize("kind", ["C", "E", "F"])
    def test_the_landed_set_is_the_shown_set(self, env, monkeypatch, kind):
        import yaml

        sc = _Scenario(env, kind)
        outside = sc.tmp_home / "outside"
        outside.mkdir()
        (outside / "f.txt").write_text("host")
        ws = sc.state.workspace_path
        (ws / "mounted").symlink_to(os.path.relpath(outside, os.path.realpath(ws)))
        bind = {"box": {"bindings": {"ro": {"/opt/m0": ["{meta.box.workspace}/mounted"]}}}}
        if kind == "F":
            # A W-tier binding: the member mounts it, the standalone it becomes does not.
            tier = sc.state.ws.root / "workset.yaml"
            doc = (yaml.safe_load(tier.read_text()) if tier.is_file() else None) or {}
            doc.update(bind)
            tier.write_text(yaml.safe_dump(doc))
        else:
            box_tier = lc.box_metadata_dir(
                sc.state.mode, sc.state.metadata_path,
                early=lc._early_scope(sc.std, sc.state.mode,
                                      sc.state.ws.name if sc.state.ws else None)) / "box.yaml"
            box_tier.write_text(yaml.safe_dump(bind))
        before = resolve_lifecycle_target(str(sc.src), sc.std, sc.config).bind_sources
        assert before
        landed = []
        real = lc._landed_bind_sources
        monkeypatch.setattr(lc, "_landed_bind_sources",
                            lambda *a: landed.append(real(*a)) or landed[-1])
        new = sc.run()
        shown = resolve_lifecycle_target(str(new.metadata_path if new.mode is lc.BoxMode.standalone
                                             else new.workspace_path),
                                         sc.std, sc.config).bind_sources
        assert landed == [shown]
        link = new.workspace_path / "mounted"
        if kind == "F":
            assert shown != before
            assert str(link) not in shown
        else:
            assert str(link) in shown
            assert (link / "f.txt").read_text() == "host"


#: Interrupts that leave the snapshot store under the old name (before 4c ran), and the
#: kinds whose relocation carries a store (``PM`` keeps its name and base).
_STRANDED = [("_finish_relocation", "before"), ("_relocate_channel_partition", "before"),
             ("_relocate_snapshot_store", "before")]
_CARRIES = ["A", "B", "Bs", "C", "D", "E", "F", "WW", "WR"]


def _cure(err):
    """The one cure command the interrupted tail's Note prints."""
    lines = [line for line in err.splitlines() if "those snapshots back, run: " in line]
    assert len(lines) == 1, err
    return lines[0].split("run: ", 1)[1]


def _landed(sc):
    (_section, _name, where), = _rows(sc.tmp_home)
    return resolve_lifecycle_target(where, sc.std, sc.config)


def _plant_store(monkeypatch, name):
    """Give the destination a store of its own when lc.*name* runs (``new_state`` arg 2)."""
    from kanibako.snapshots import box_snapshot_store
    real = getattr(lc, name)

    def wrapped(state, new_state, *a, **kw):
        theirs = box_snapshot_store(new_state.vault_rw, new_state.name) / "theirs"
        theirs.mkdir(parents=True, exist_ok=True)
        (theirs / "f").write_text("theirs")
        return real(state, new_state, *a, **kw)

    monkeypatch.setattr(lc, name, wrapped)


def _run_cure(cmd):
    import subprocess
    subprocess.run(["bash", "-c", cmd], check=True)


@pytest.mark.usefixtures("stash_dir")
class TestAnInterruptedTailNamesTheSnapshotCure:
    """Interrupted before step 4c, the Note names the one command that carries the store,
    with the real old and new paths; running it gives the box its snapshots back."""

    @pytest.mark.parametrize("point", _STRANDED, ids=lambda p: f"{p[0]}-{p[1]}")
    @pytest.mark.parametrize("kind", _CARRIES)
    def test_new_store_absent(self, env, monkeypatch, capsys, kind, point):
        import shlex
        from pathlib import Path
        from kanibako.snapshots import box_snapshot_store

        sc = _Scenario(env, kind)
        capsys.readouterr()
        with monkeypatch.context() as m:
            _interrupt_at(m, *point)
            with pytest.raises(KeyboardInterrupt):
                sc.run()
        cmd = _cure(capsys.readouterr().err)
        box = _landed(sc)
        dst = box_snapshot_store(box.vault_rw, box.name)
        assert not dst.exists()
        argv = shlex.split(cmd)
        assert argv[:3] == ["mkdir", "-p", "--"] and argv[3] == str(dst.parent)
        assert argv[4:7] == ["&&", "mv", "--"] and argv[8] == str(dst)
        src = Path(argv[7])
        assert _digest(src / "XSNAP") == sc.digests["XSNAP"]
        _run_cure(cmd)
        assert _digest(dst / "XSNAP") == sc.digests["XSNAP"]
        assert not src.exists()

    @pytest.mark.parametrize("point", _STRANDED, ids=lambda p: f"{p[0]}-{p[1]}")
    @pytest.mark.parametrize("kind", _CARRIES)
    def test_new_store_present_is_kept(self, env, monkeypatch, capsys, kind, point):
        from kanibako.snapshots import box_snapshot_store

        sc = _Scenario(env, kind)
        capsys.readouterr()
        with monkeypatch.context() as m:
            _interrupt_at(m, *point)
            _plant_store(m, "_note_interrupted_tail")
            with pytest.raises(KeyboardInterrupt):
                sc.run()
        cmd = _cure(capsys.readouterr().err)
        box = _landed(sc)
        dst = box_snapshot_store(box.vault_rw, box.name)
        src = _stranded_from(cmd, dst)
        assert _digest(src / "XSNAP") == sc.digests["XSNAP"]
        _run_cure(cmd)
        assert _digest(dst / "XSNAP") == sc.digests["XSNAP"]
        assert (dst / "theirs" / "f").read_text() == "theirs"

    @pytest.mark.parametrize("kind", _CARRIES)
    def test_interrupted_mid_merge(self, env, monkeypatch, capsys, kind):
        """T2: the carry merging into an existing store is interrupted after one entry."""
        import kanibako.snapshots as snapshots
        from kanibako.snapshots import box_snapshot_store

        sc = _Scenario(env, kind)
        extra = sc.src_homes["XSNAP"] / "XSNAP2"
        extra.mkdir()
        (extra / "f").write_text("two")
        capsys.readouterr()
        real = snapshots._unique_snapshot_dest
        calls = []

        def second_interrupts(*a, **kw):
            calls.append(a)
            if len(calls) == 2:
                raise KeyboardInterrupt("mid-merge")
            return real(*a, **kw)

        with monkeypatch.context() as m:
            _plant_store(m, "_relocate_snapshot_store")
            m.setattr(snapshots, "_unique_snapshot_dest", second_interrupts)
            with pytest.raises(KeyboardInterrupt):
                sc.run()
        assert len(calls) == 2
        cmd = _cure(capsys.readouterr().err)
        box = _landed(sc)
        dst = box_snapshot_store(box.vault_rw, box.name)
        _stranded_from(cmd, dst)
        _run_cure(cmd)
        assert _digest(dst / "XSNAP") == sc.digests["XSNAP"]
        assert (dst / "XSNAP2" / "f").read_text() == "two"
        assert (dst / "theirs" / "f").read_text() == "theirs"

    @pytest.mark.parametrize("kind", _CARRIES + ["PM"])
    def test_no_cure_once_the_store_is_carried(self, env, monkeypatch, capsys, kind):
        """T3, and a relocation that carries no store: nothing to name."""
        sc = _Scenario(env, kind)
        capsys.readouterr()
        with monkeypatch.context() as m:
            _interrupt_at(m, "_relocate_snapshot_store", "after")
            with pytest.raises(KeyboardInterrupt):
                sc.run()
        assert "those snapshots back" not in capsys.readouterr().err


def _stranded_from(cmd, dst):
    """The source a merge cure names: ``mv -n -- <src>/* <dst>/``, *dst* the real store."""
    import shlex
    from pathlib import Path
    argv = shlex.split(cmd)
    assert argv[:3] == ["mv", "-n", "--"] and argv[4] == f"{dst}/", cmd
    assert argv[3].endswith("/*")
    return Path(argv[3][:-2])

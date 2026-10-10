"""A named box's vault leaf that LINKS outside its arm survives every relocation outcome.

The carry refuses such a leaf (``_is_per_box_leaf``) and warns that its contents remain
at the source, so no retirement may delete it: not on success, not on a rollback, not
on an interrupt at any step.  Each test pins the pointer's exact text and its target's
bytes.
"""

from __future__ import annotations

import hashlib
import os
import shutil

import pytest

from kanibako.commands.box import _lifecycle
from kanibako.commands.box._lifecycle import (
    TargetSpec,
    execute_lifecycle,
    resolve_lifecycle_target,
)
from kanibako.project.workset import add_project, create_workset, load_workset
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths

MOD = "kanibako.commands.box._lifecycle"


@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    config = load_config(config_file)
    return config, load_std_paths(config), tmp_home


def _target(tmp_home, kind):
    """The link target for leaf *kind*: a dir with files, a file, or nothing."""
    out = tmp_home / "outside"
    if kind == "dir":
        (out / "sub").mkdir(parents=True)
        (out / "a.txt").write_text("user data")
        (out / "sub" / "b.txt").write_text("nested")
    elif kind == "file":
        out.write_text("a file")
    return out


def _digest(path):
    """Content hash of *path* (tree, file, or absent); never follows into a link."""
    h = hashlib.sha256()
    if path.is_file():
        h.update(path.read_bytes())
    elif path.is_dir():
        for p in sorted(path.rglob("*")):
            h.update(str(p.relative_to(path)).encode())
            if p.is_file():
                h.update(p.read_bytes())
    else:
        h.update(b"absent")
    return h.hexdigest()


def _linked_member(env, kind):
    """``b1`` in ``wsa`` whose rw leaf links to an outside *kind* target."""
    config, std, tmp_home = env
    ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
    ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
    internal = ws_a.workspaces_dir / "b1"
    internal.mkdir(parents=True)
    add_project(ws_a, "b1", internal, std)
    leaf = ws_a.root / "vault" / "rw" / "b1"
    shutil.rmtree(leaf)
    target = _target(tmp_home, kind)
    leaf.symlink_to(target)
    state = resolve_lifecycle_target(str(internal), std, config)
    return state, ws_a, ws_b, leaf, os.readlink(leaf), target, _digest(target)


def _move(env, state, ws_b):
    config, std, _ = env
    return execute_lifecycle(
        state, TargetSpec(location=ws_b.workspaces_dir / "b1", ownership="wsb"),
        std, config, confirm=lambda: True,
    )


def _assert_pointer(leaf, text, target, digest):
    assert leaf.is_symlink(), f"{leaf} was deleted"
    assert os.readlink(leaf) == text
    assert _digest(target) == digest


def _member_of(env, ws, name="b1"):
    _config, std, _ = env
    return any(p.name == name for p in load_workset(
        ws.root, ws.name, early_system=std.early_system).projects)


KINDS = ("dir", "file", "dangling")


@pytest.mark.parametrize("kind", KINDS)
def test_a_successful_move_keeps_the_pointer(env, kind, capsys):
    state, _ws_a, ws_b, leaf, text, target, digest = _linked_member(env, kind)
    new = _move(env, state, ws_b)
    _assert_pointer(leaf, text, target, digest)
    assert new.vault_rw.is_dir() and not new.vault_rw.is_symlink()
    assert f"left the vault at {leaf} in place" in capsys.readouterr().err


@pytest.mark.parametrize("kind", KINDS)
def test_a_rolled_back_move_keeps_the_pointer_and_the_record(env, kind, monkeypatch):
    state, ws_a, ws_b, leaf, text, target, digest = _linked_member(env, kind)
    real = _lifecycle.add_project

    def _refuse_target(ws, *a, **kw):
        if ws.name == "wsb":
            raise OSError("injected target failure")
        return real(ws, *a, **kw)

    monkeypatch.setattr(f"{MOD}.add_project", _refuse_target)
    with pytest.raises(OSError, match="injected target"):
        _move(env, state, ws_b)
    _assert_pointer(leaf, text, target, digest)
    assert _member_of(env, ws_a)
    assert (ws_a.projects_dir / "b1").is_dir()


def _interrupt_at(monkeypatch, name, when=lambda *a, **kw: True):
    real = getattr(_lifecycle, name)

    def _wrapped(*a, **kw):
        if when(*a, **kw):
            raise KeyboardInterrupt
        return real(*a, **kw)

    monkeypatch.setattr(f"{MOD}.{name}", _wrapped)


def _under(ws_b):
    return lambda src, dst, *a, **kw: ws_b.root.resolve() in dst.resolve().parents


@pytest.mark.parametrize("step", [
    "stash", "release", "retire", "add_target", "leg2", "logs",
])
def test_an_interrupt_before_the_point_of_no_return_keeps_the_pointer(
    env, step, monkeypatch,
):
    state, ws_a, ws_b, leaf, text, target, digest = _linked_member(env, "dir")
    if step == "stash":
        _interrupt_at(monkeypatch, "copy_tree_keeping_links")
    elif step == "release":
        _interrupt_at(monkeypatch, "release_project")
    elif step == "retire":
        _interrupt_at(monkeypatch, "_retire_old_store")
    elif step == "add_target":
        _interrupt_at(monkeypatch, "add_project", lambda ws, *a, **kw: ws.name == "wsb")
    elif step == "leg2":
        _interrupt_at(monkeypatch, "_copy_vault_leaf_contents", _under(ws_b))
    else:
        _interrupt_at(monkeypatch, "_carry_box_logs")
    with pytest.raises(KeyboardInterrupt):
        _move(env, state, ws_b)
    _assert_pointer(leaf, text, target, digest)
    assert _member_of(env, ws_a)


def test_an_interrupt_in_the_success_tail_keeps_the_pointer(env, monkeypatch):
    state, _ws_a, ws_b, leaf, text, target, digest = _linked_member(env, "dir")
    _interrupt_at(monkeypatch, "_relocate_channel_partition")
    with pytest.raises(KeyboardInterrupt):
        _move(env, state, ws_b)
    _assert_pointer(leaf, text, target, digest)


def test_a_named_to_primary_convert_keeps_the_pointer(env):
    config, std, tmp_home = env
    state, _ws_a, _ws_b, leaf, text, target, digest = _linked_member(env, "dir")
    execute_lifecycle(
        state, TargetSpec(location=tmp_home / "landed", ownership="default"),
        std, config, confirm=lambda: True,
    )
    _assert_pointer(leaf, text, target, digest)

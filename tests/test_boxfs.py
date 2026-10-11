"""boxfs: host-side file operations on a box-writable path never follow a symlink."""

from __future__ import annotations

import logging
import os
import stat

import pytest

from kanibako import boxfs
from kanibako.boxfs import SymlinkRefusedError, first_symlink


@pytest.fixture
def tree(tmp_path):
    """A box root, a real dir in it, and a host sentinel outside it."""
    root = tmp_path / "box"
    (root / "a" / "b").mkdir(parents=True)
    outside = tmp_path / "host"
    outside.mkdir()
    sentinel = outside / "secret"
    sentinel.write_text("host data")
    return root, outside, sentinel


# --- the check -----------------------------------------------------------------


def test_no_link_control(tree):
    root, _, _ = tree
    (root / "a" / "b" / "f").write_text("x")
    assert first_symlink(root / "a" / "b" / "f", root) is None
    assert first_symlink(root / "a" / "b" / "missing", root) is None


def test_leaf_link(tree):
    root, _, sentinel = tree
    leaf = root / "a" / "b" / "f"
    leaf.symlink_to(sentinel)
    assert first_symlink(leaf, root) == leaf


def test_parent_link_outside_root(tree):
    root, outside, _ = tree
    (root / "a" / "out").symlink_to(outside)
    path = root / "a" / "out" / "secret"
    assert first_symlink(path, root) == root / "a" / "out"


def test_link_inside_root_is_still_a_link(tree):
    root, _, _ = tree
    (root / "a" / "in").symlink_to(root / "a" / "b")
    assert first_symlink(root / "a" / "in" / "f", root) == root / "a" / "in"


def test_dangling_link(tree):
    root, _, _ = tree
    (root / "a" / "dang").symlink_to(root / "nowhere")
    assert first_symlink(root / "a" / "dang", root) == root / "a" / "dang"
    assert first_symlink(root / "a" / "dang" / "x" / "f", root) == root / "a" / "dang"


def test_path_is_root(tree):
    root, _, _ = tree
    assert first_symlink(root, root) is None


def test_path_outside_root_is_a_caller_bug(tree):
    root, outside, _ = tree
    with pytest.raises(ValueError):
        first_symlink(outside / "secret", root)


def test_first_link_is_the_one_nearest_root(tree):
    root, outside, _ = tree
    (outside / "deep").mkdir()
    (outside / "deep" / "inner").symlink_to(outside)
    (root / "a" / "top").symlink_to(outside)
    # top -> host, and host/deep/inner is a second link below it.
    assert first_symlink(root / "a" / "top" / "deep" / "inner" / "f", root) == root / "a" / "top"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_eacces_dir_fails_closed_without_raising(tree):
    root, _, _ = tree
    locked = root / "a" / "b"
    (locked / "c").mkdir()
    locked.chmod(0)
    try:
        # b cannot be opened: b comes back, as if a link (fail closed).
        assert first_symlink(locked / "c" / "f", root) == locked
        assert first_symlink(locked / "f", root) == locked
        # b can be opened but not searched: only the leaf cannot be probed.
        locked.chmod(0o400)
        assert first_symlink(locked / "f", root) == locked / "f"
    finally:
        locked.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_eacces_below_a_link_reports_the_link(tree):
    root, outside, _ = tree
    (outside / "locked").mkdir()
    (outside / "locked" / "c").mkdir()
    (root / "a" / "out").symlink_to(outside / "locked")
    (outside / "locked").chmod(0)
    try:
        assert first_symlink(root / "a" / "out" / "c" / "f", root) == root / "a" / "out"
    finally:
        (outside / "locked").chmod(0o755)


def test_warn_and_skip_logs_one_warning(tree, caplog):
    root, _, sentinel = tree
    leaf = root / "a" / "f"
    leaf.symlink_to(sentinel)
    with caplog.at_level(logging.WARNING, logger="kanibako"):
        assert boxfs.no_symlink_or_warn(leaf, root) is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(leaf) in warnings[0].getMessage()
    assert str(sentinel) in warnings[0].getMessage()
    assert boxfs.no_symlink_or_warn(root / "a" / "b" / "g", root) is True


def test_refusal_is_an_eloop_oserror(tree):
    root, _, sentinel = tree
    leaf = root / "a" / "f"
    leaf.symlink_to(sentinel)
    with pytest.raises(OSError) as info:
        boxfs.read_bytes(leaf, root)
    assert isinstance(info.value, SymlinkRefusedError)
    assert info.value.link == leaf


# --- the operations ------------------------------------------------------------


def _links(root, outside):
    """(leaf link, path under a parent link), both leading outside *root*."""
    leaf = root / "a" / "leaf"
    leaf.symlink_to(outside / "secret")
    (root / "a" / "par").symlink_to(outside)
    return leaf, root / "a" / "par" / "secret"


def test_read_refuses_links_and_reads_files(tree):
    root, outside, _ = tree
    for p in _links(root, outside):
        with pytest.raises(SymlinkRefusedError):
            boxfs.read_bytes(p, root)
    (root / "a" / "f").write_bytes(b"\x00ok")
    assert boxfs.read_bytes(root / "a" / "f", root) == b"\x00ok"
    assert boxfs.read_text(root / "a" / "f", root) == "\x00ok"


def test_read_refuses_a_fifo_without_hanging(tree):
    root, _, _ = tree
    os.mkfifo(root / "a" / "fifo")
    with pytest.raises(OSError):
        boxfs.read_bytes(root / "a" / "fifo", root)


def test_write_refuses_links_and_writes_files(tree):
    root, outside, sentinel = tree
    for p in _links(root, outside):
        with pytest.raises(SymlinkRefusedError):
            boxfs.write_text(p, root, "pwned")
    assert sentinel.read_text() == "host data"
    boxfs.write_text(root / "a" / "new" / "f", root, "hi")
    assert (root / "a" / "new" / "f").read_text() == "hi"
    boxfs.write_bytes(root / "a" / "new" / "f", root, b"yo")
    assert (root / "a" / "new" / "f").read_bytes() == b"yo"


def test_copy_file_keeps_mode_and_times_and_refuses_links(tree):
    root, outside, sentinel = tree
    src = outside / "src"
    src.write_text("payload")
    src.chmod(0o640)
    os.utime(src, ns=(1_000_000_000, 2_000_000_000))
    dst = root / "a" / "dst"
    boxfs.copy_file(src, dst, src_root=None, dst_root=root)
    assert dst.read_text() == "payload"
    assert stat.S_IMODE(dst.stat().st_mode) == 0o640
    assert dst.stat().st_mtime_ns == 2_000_000_000
    for p in _links(root, outside):
        with pytest.raises(SymlinkRefusedError):
            boxfs.copy_file(src, p, src_root=None, dst_root=root)
        with pytest.raises(SymlinkRefusedError):
            boxfs.copy_file(p, outside / "out", src_root=root, dst_root=None)
    assert sentinel.read_text() == "host data"
    assert not (outside / "out").exists()


def test_copy_tree_refuses_a_root_link_and_keeps_inner_links_verbatim(tree):
    root, outside, sentinel = tree
    src = root / "a" / "b"
    (src / "f").write_text("x")
    (src / "f").chmod(0o751)
    (src / "d" / "e").mkdir(parents=True)
    (src / "d" / "e" / "g").write_text("deep")
    (src / "d").chmod(0o550)
    (src / "inner").symlink_to(sentinel)
    (src / "to_host_dir").symlink_to(outside)
    os.mkfifo(src / "fifo")
    dst = outside / "copy"
    try:
        boxfs.copy_tree(src, dst, src_root=root, dst_root=None)
    finally:
        (src / "d").chmod(0o755)
    assert (dst / "f").read_text() == "x"
    assert stat.S_IMODE((dst / "f").stat().st_mode) == 0o751
    assert stat.S_IMODE((dst / "d").stat().st_mode) == 0o550
    assert (dst / "d" / "e" / "g").read_text() == "deep"
    assert (dst / "to_host_dir").is_symlink()
    assert not (dst / "fifo").exists()
    (dst / "d").chmod(0o755)
    with pytest.raises(FileExistsError):
        boxfs.copy_tree(src, dst, src_root=root, dst_root=None)
    assert (dst / "inner").is_symlink()
    assert os.readlink(dst / "inner") == str(sentinel)
    (root / "a" / "par").symlink_to(outside)
    with pytest.raises(SymlinkRefusedError):
        boxfs.copy_tree(root / "a" / "par", outside / "copy2", src_root=root, dst_root=None)
    with pytest.raises(SymlinkRefusedError):
        boxfs.copy_tree(outside / "copy", root / "a" / "par" / "x", src_root=None, dst_root=root)
    assert not (outside / "copy2").exists()
    assert not (outside / "x").exists()


def test_touch_refuses_links_and_creates_files(tree):
    root, outside, sentinel = tree
    before = sentinel.stat().st_mtime_ns
    os.utime(sentinel, ns=(1, 1))
    for p in _links(root, outside):
        with pytest.raises(SymlinkRefusedError):
            boxfs.touch(p, root)
    assert sentinel.stat().st_mtime_ns == 1
    assert before != 1
    boxfs.touch(root / "a" / "t", root)
    assert (root / "a" / "t").is_file()


def test_unlink_refuses_links_and_removes_files(tree):
    root, outside, sentinel = tree
    leaf, under = _links(root, outside)
    for p in (leaf, under):
        with pytest.raises(SymlinkRefusedError):
            boxfs.unlink(p, root)
    assert sentinel.exists() and leaf.is_symlink()
    (root / "a" / "f").write_text("x")
    boxfs.unlink(root / "a" / "f", root)
    assert not (root / "a" / "f").exists()
    boxfs.unlink(root / "a" / "f", root, missing_ok=True)
    with pytest.raises(FileNotFoundError):
        boxfs.unlink(root / "a" / "f", root)


def test_rmtree_contents_removes_inner_links_without_following(tree):
    root, outside, sentinel = tree
    d = root / "a" / "b"
    (d / "sub").mkdir()
    (d / "sub" / "f").write_text("x")
    (d / "file").write_text("x")
    (d / "to_host_dir").symlink_to(outside)
    (d / "to_host_file").symlink_to(sentinel)
    boxfs.rmtree_contents(d, root)
    assert d.is_dir() and list(d.iterdir()) == []
    assert sentinel.read_text() == "host data"
    (root / "a" / "par").symlink_to(outside)
    with pytest.raises(SymlinkRefusedError):
        boxfs.rmtree_contents(root / "a" / "par", root)
    assert sentinel.exists()


def test_chmod_refuses_links_and_sets_modes(tree):
    root, outside, sentinel = tree
    sentinel.chmod(0o644)
    for p in _links(root, outside):
        with pytest.raises(SymlinkRefusedError):
            boxfs.chmod(p, root, 0o777)
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o644
    f = root / "a" / "f"
    f.write_text("x")
    boxfs.chmod(f, root, 0o000)
    assert stat.S_IMODE(f.stat().st_mode) == 0
    boxfs.chmod(f, root, 0o600)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_rename_refuses_links_at_either_end(tree):
    root, outside, sentinel = tree
    leaf, under = _links(root, outside)
    f = root / "a" / "f"
    f.write_text("x")
    for p in (leaf, under):
        with pytest.raises(SymlinkRefusedError):
            boxfs.rename(f, p, root)
        with pytest.raises(SymlinkRefusedError):
            boxfs.rename(p, root / "a" / "g", root)
    assert sentinel.read_text() == "host data"
    boxfs.rename(f, root / "a" / "b" / "g", root)
    assert (root / "a" / "b" / "g").read_text() == "x"


def test_chmod_tree_skips_links_and_sets_modes(tree):
    root, outside, sentinel = tree
    sentinel.chmod(0o600)
    d = root / "a" / "b"
    (d / "sub").mkdir()
    (d / "sub" / "f").write_text("x")
    (d / "sub" / "f").chmod(0o600)
    (d / "to_host_file").symlink_to(sentinel)
    (d / "to_host_dir").symlink_to(outside)
    boxfs.chmod_tree(d, root, add=0o044, remove=0o002)
    assert stat.S_IMODE((d / "sub" / "f").stat().st_mode) == 0o644
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o600
    (root / "a" / "par").symlink_to(outside)
    with pytest.raises(SymlinkRefusedError):
        boxfs.chmod_tree(root / "a" / "par", root, add=0o777)
    assert stat.S_IMODE(outside.stat().st_mode) != 0o777


def test_write_lands_in_the_verified_parent_when_it_is_swapped_for_a_link(tree, monkeypatch):
    """The race a path-based check leaves open: the parent becomes a link after the walk."""
    root, outside, _ = tree
    parent = root / "a" / "b"
    real_replace = boxfs._replace_at

    def swap_then_replace(*args, **kwargs):
        parent.rename(root / "a" / "moved")
        parent.symlink_to(outside)
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(boxfs, "_replace_at", swap_then_replace)
    boxfs.write_text(parent / "f", root, "box data")
    assert not (outside / "f").exists()
    assert (root / "a" / "moved" / "f").read_text() == "box data"


def test_read_follows_no_link_swapped_in_after_the_walk(tree, monkeypatch):
    root, outside, _ = tree
    parent = root / "a" / "b"
    (parent / "secret").write_text("box copy")
    real_open_leaf = boxfs._open_leaf

    def swap_then_open(*args, **kwargs):
        parent.rename(root / "a" / "moved")
        parent.symlink_to(outside)
        return real_open_leaf(*args, **kwargs)

    monkeypatch.setattr(boxfs, "_open_leaf", swap_then_open)
    assert boxfs.read_text(parent / "secret", root) == "box copy"

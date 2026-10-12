"""boxfs: host-side file operations on a box-writable path never follow a symlink."""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import stat
import sys

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


def test_unlink_removes_a_leaf_link_itself_and_refuses_a_parent_link(tree):
    root, outside, sentinel = tree
    leaf, under = _links(root, outside)
    with pytest.raises(SymlinkRefusedError):
        boxfs.unlink(under, root)
    boxfs.unlink(leaf, root)
    assert not leaf.is_symlink() and sentinel.read_text() == "host data"
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


def test_rename_moves_leaf_links_and_refuses_parent_links(tree):
    root, outside, sentinel = tree
    leaf, under = _links(root, outside)
    f = root / "a" / "f"
    f.write_text("x")
    with pytest.raises(SymlinkRefusedError):
        boxfs.rename(f, under, root)
    with pytest.raises(SymlinkRefusedError):
        boxfs.rename(under, root / "a" / "g", root)
    boxfs.rename(leaf, root / "a" / "moved_link", root)
    assert (root / "a" / "moved_link").is_symlink() and not leaf.is_symlink()
    boxfs.rename(f, root / "a" / "moved_link", root)  # replaces the link itself
    assert not (root / "a" / "moved_link").is_symlink()
    assert sentinel.read_text() == "host data"
    boxfs.rename(root / "a" / "moved_link", root / "a" / "b" / "g", root)
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


# --- round 2 -------------------------------------------------------------------


def test_root_that_cannot_be_opened_comes_back(tree):
    root, _, _ = tree
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    root.chmod(0)
    try:
        assert first_symlink(root / "a" / "f", root) == root
    finally:
        root.chmod(0o755)


def test_copy_tree_merge_replaces_files_and_refuses_dst_links(tree, caplog):
    root, outside, sentinel = tree
    src = outside / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "f").write_text("new")
    (src / "secret").write_text("new")
    (src / "plain").write_text("new")
    (src / "d" / "f").chmod(0o640)
    dst = root / "a" / "dst"
    (dst / "d").mkdir(parents=True)
    (dst / "d" / "f").write_text("old")
    (dst / "keep").write_text("mine")
    (dst / "secret").symlink_to(sentinel)  # a box link in dst: never followed
    with caplog.at_level(logging.WARNING, logger="kanibako"):
        skipped = boxfs.copy_tree(src, dst, src_root=None, dst_root=root, merge=True)
    assert (dst / "d" / "f").read_text() == "new"
    assert _st_mode(dst / "d" / "f") == 0o640
    assert (dst / "plain").read_text() == "new"
    assert (dst / "keep").read_text() == "mine"
    assert (dst / "secret").is_symlink() and sentinel.read_text() == "host data"
    assert skipped == [src / "secret"]
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 1
    with pytest.raises(FileExistsError):
        boxfs.copy_tree(src, dst, src_root=None, dst_root=root)


def _st_mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def test_copy_tree_skip_links_warns_once_per_link(tree, caplog):
    root, outside, sentinel = tree
    src = root / "a" / "b"
    (src / "f").write_text("x")
    (src / "l1").symlink_to(sentinel)
    (src / "l2").symlink_to(outside)
    with caplog.at_level(logging.WARNING, logger="kanibako"):
        skipped = boxfs.copy_tree(src, outside / "c", src_root=root, dst_root=None, skip_links=True)
    assert sorted(skipped) == [src / "l1", src / "l2"]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert sorted(os.listdir(outside / "c")) == ["f"]


def test_copy_tree_into_itself_does_not_recurse(tree):
    root, _, _ = tree
    src = root / "a"
    (src / "b" / "f").write_text("x")
    boxfs.copy_tree(src, src / "b" / "copy", src_root=root, dst_root=root)
    assert (src / "b" / "copy" / "b" / "f").read_text() == "x"
    assert not (src / "b" / "copy" / "b" / "copy").exists()
    with pytest.raises(OSError):
        boxfs.copy_tree(src, src, src_root=root, dst_root=root, merge=True)


def _deep(top, depth, leaf=None):
    """Make or walk *depth* nested ``d`` dirs under *top* via fds; return the bottom fd."""
    fd = os.open(top, os.O_RDONLY | os.O_DIRECTORY)
    for _ in range(depth):
        if leaf is not None:
            with contextlib.suppress(FileExistsError):
                os.mkdir("d", dir_fd=fd)
        nxt = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
        os.close(fd)
        fd = nxt
    if leaf is not None:
        wfd = os.open(leaf, os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=fd)
        os.write(wfd, b"bottom")
        os.close(wfd)
    return fd


def test_deep_trees_never_raise_recursion_error(tree):
    root, outside, _ = tree
    depth = sys.getrecursionlimit() + 100
    deep = root / "a" / "deep"
    deep.mkdir()
    os.close(_deep(deep, depth, leaf="f"))
    boxfs.copy_tree(deep, outside / "deep", src_root=root, dst_root=None)
    fd = _deep(outside / "deep", depth)
    try:
        rfd = os.open("f", os.O_RDONLY, dir_fd=fd)
        assert os.read(rfd, 100) == b"bottom"
        os.close(rfd)
    finally:
        os.close(fd)
    boxfs.chmod_tree(deep, root, add=0o040)
    fd = _deep(deep, depth)
    try:
        assert os.stat("f", dir_fd=fd).st_mode & 0o040
    finally:
        os.close(fd)
    try:
        boxfs.rmtree_contents(root / "a", root)
    except RecursionError:  # pragma: no cover - the failure this test pins
        pytest.fail("rmtree_contents raised RecursionError")
    except OSError:
        pass  # Python < 3.12: shutil.rmtree is recursive; refused as OSError
    else:
        assert list((root / "a").iterdir()) == []


def test_mkdir(tree):
    root, outside, _ = tree
    boxfs.mkdir(root / "a" / "n", root, mode=0o700)
    assert _st_mode(root / "a" / "n") == 0o700
    with pytest.raises(FileExistsError):
        boxfs.mkdir(root / "a" / "n", root)
    boxfs.mkdir(root / "a" / "n", root, exist_ok=True)
    with pytest.raises(FileNotFoundError):
        boxfs.mkdir(root / "x" / "y", root)
    boxfs.mkdir(root / "x" / "y", root, parents=True)
    assert (root / "x" / "y").is_dir()
    leaf, under = _links(root, outside)
    for p in (leaf, under):
        with pytest.raises(SymlinkRefusedError):
            boxfs.mkdir(p, root, exist_ok=True)
    with pytest.raises(SymlinkRefusedError):
        boxfs.mkdir(root / "a" / "par" / "new", root, parents=True)
    assert not (outside / "new").exists()
    (root / "a" / "file").write_text("x")
    with pytest.raises(FileExistsError):
        boxfs.mkdir(root / "a" / "file", root, exist_ok=True)


def test_stat_refuses_links(tree):
    root, outside, _ = tree
    (root / "a" / "f").write_text("12345")
    assert boxfs.stat(root / "a" / "f", root).st_size == 5
    assert stat.S_ISDIR(boxfs.stat(root / "a" / "b", root).st_mode)
    leaf, under = _links(root, outside)
    for p in (leaf, under):
        with pytest.raises(SymlinkRefusedError):
            boxfs.stat(p, root)
    with pytest.raises(FileNotFoundError):
        boxfs.stat(root / "a" / "missing", root)


def test_read_bytes_with_stat_reads_both_from_one_file(tree):
    root, outside, _ = tree
    f = root / "a" / "f"
    f.write_bytes(b"abc")
    os.utime(f, ns=(5_000_000_000, 7_000_000_000))
    data, st = boxfs.read_bytes_with_stat(f, root)
    assert data == b"abc" and st.st_size == 3 and st.st_mtime_ns == 7_000_000_000
    for p in _links(root, outside):
        with pytest.raises(SymlinkRefusedError):
            boxfs.read_bytes_with_stat(p, root)


def test_write_keeps_an_existing_files_mode(tree):
    root, _, _ = tree
    f = root / "a" / "f"
    f.write_text("old")
    f.chmod(0o644)
    boxfs.write_text(f, root, "new")
    assert _st_mode(f) == 0o644 and f.read_text() == "new"
    boxfs.write_text(f, root, "newer", mode=0o600)
    assert _st_mode(f) == 0o600
    boxfs.write_bytes(root / "a" / "g", root, b"x")
    assert _st_mode(root / "a" / "g") == 0o600


# --- round 3 -------------------------------------------------------------------


def test_listdir_reads_through_the_verified_dir_and_refuses_links(tree):
    root, outside, _ = tree
    (root / "a" / "b" / "f").write_text("x")
    (root / "a" / "b" / "l").symlink_to(outside)
    assert sorted(boxfs.listdir(root / "a" / "b", root)) == ["f", "l"]
    assert sorted(boxfs.listdir(root, root)) == ["a"]
    (root / "a" / "par").symlink_to(outside)
    (root / "a" / "leafdir").symlink_to(outside)
    with pytest.raises(SymlinkRefusedError):
        boxfs.listdir(root / "a" / "par" / "sub", root)  # parent link
    with pytest.raises(SymlinkRefusedError):
        boxfs.listdir(root / "a" / "leafdir", root)  # the dir itself is a link
    with pytest.raises(FileNotFoundError):
        boxfs.listdir(root / "a" / "missing", root)


def test_write_sets_times_when_given(tree):
    root, outside, sentinel = tree
    f = root / "a" / "f"
    boxfs.write_bytes(f, root, b"gz", times=(3_000_000_000, 4_000_000_000))
    assert f.stat().st_mtime_ns == 4_000_000_000
    assert f.stat().st_atime_ns == 3_000_000_000
    boxfs.write_text(f, root, "t", times=(1_000_000_000, 2_000_000_000))
    assert f.stat().st_mtime_ns == 2_000_000_000
    leaf, under = _links(root, outside)
    before = sentinel.stat().st_mtime_ns
    for p in (leaf, under):
        with pytest.raises(SymlinkRefusedError):
            boxfs.write_bytes(p, root, b"x", times=(1, 1))
    assert sentinel.stat().st_mtime_ns == before


# --- round 4: rmtree -----------------------------------------------------------


def test_rmtree_removes_a_normal_tree(tree):
    root, _, _ = tree
    d = root / "a" / "b"
    (d / "sub" / "deeper").mkdir(parents=True)
    (d / "sub" / "deeper" / "f").write_text("x")
    (d / "g").write_text("x")
    boxfs.rmtree(d, root)
    assert not d.exists() and (root / "a").is_dir()
    boxfs.rmtree(d, root, missing_ok=True)
    with pytest.raises(FileNotFoundError):
        boxfs.rmtree(d, root)


def test_rmtree_removes_a_leaf_link_as_a_link(tree):
    root, outside, sentinel = tree
    (outside / "dir").mkdir()
    (outside / "dir" / "keep").write_text("host data")
    (root / "a" / "to_dir").symlink_to(outside / "dir")
    (root / "a" / "to_file").symlink_to(sentinel)
    boxfs.rmtree(root / "a" / "to_dir", root)
    boxfs.rmtree(root / "a" / "to_file", root)
    assert not os.path.lexists(root / "a" / "to_dir")
    assert not os.path.lexists(root / "a" / "to_file")
    assert (outside / "dir" / "keep").read_text() == "host data"
    assert sentinel.read_text() == "host data"


def test_rmtree_removes_inner_links_without_following(tree):
    root, outside, sentinel = tree
    d = root / "a" / "b"
    (d / "to_host_dir").symlink_to(outside)
    (d / "to_host_file").symlink_to(sentinel)
    boxfs.rmtree(d, root)
    assert not d.exists()
    assert sentinel.read_text() == "host data"


def test_rmtree_refuses_a_parent_link(tree):
    root, outside, sentinel = tree
    (outside / "victim").mkdir()
    (root / "a" / "par").symlink_to(outside)
    for p in (root / "a" / "par" / "victim", root / "a" / "par" / "secret"):
        with pytest.raises(SymlinkRefusedError):
            boxfs.rmtree(p, root)
    assert (outside / "victim").is_dir() and sentinel.read_text() == "host data"


def test_rmtree_refuses_the_root(tree):
    root, _, _ = tree
    with pytest.raises(ValueError):
        boxfs.rmtree(root, root)
    assert (root / "a" / "b").is_dir()


def _swap_after_open(monkeypatch, parent, outside):
    """Patch ``os.open``: once the walk opens *parent*, swap it for a link to *outside*."""
    real_open = os.open

    def open_then_swap(name, *args, **kwargs):
        fd = real_open(name, *args, **kwargs)
        if name == parent.name and not parent.is_symlink():
            parent.rename(parent.with_name("moved"))
            parent.symlink_to(outside)
        return fd

    monkeypatch.setattr(boxfs.os, "open", open_then_swap)


def test_rmdir_removes_only_an_empty_directory(tree):
    root, _, _ = tree
    d = root / "a" / "b"
    (d / "full").mkdir()
    (d / "full" / "f").write_text("x")
    (d / "empty").mkdir()
    boxfs.rmdir(d / "empty", root)
    assert not (d / "empty").exists()
    with pytest.raises(OSError) as exc:
        boxfs.rmdir(d / "full", root)
    assert exc.value.errno == errno.ENOTEMPTY
    assert (d / "full" / "f").read_text() == "x"


@pytest.mark.parametrize("where", ["outside", "inside"])
def test_rmdir_never_follows_a_link(tree, where):
    root, outside, sentinel = tree
    target = outside / "empty" if where == "outside" else root / "a" / "empty"
    target.mkdir()
    (root / "a" / "b" / "leaf").symlink_to(target)
    (root / "a" / "par").symlink_to(target.parent)
    with pytest.raises(NotADirectoryError):
        boxfs.rmdir(root / "a" / "b" / "leaf", root)
    with pytest.raises(SymlinkRefusedError):
        boxfs.rmdir(root / "a" / "par" / "empty", root)
    assert (root / "a" / "b" / "leaf").is_symlink()
    assert target.is_dir() and sentinel.read_text() == "host data"


def test_rmtree_acts_in_the_verified_parent_when_it_is_swapped_for_a_link(tree, monkeypatch):
    root, outside, sentinel = tree
    parent = root / "a" / "b"
    (parent / "victim" / "sub").mkdir(parents=True)
    (outside / "victim").mkdir()
    (outside / "victim" / "keep").write_text("host data")
    _swap_after_open(monkeypatch, parent, outside)
    boxfs.rmtree(parent / "victim", root)
    monkeypatch.undo()
    assert parent.is_symlink()
    assert not (root / "a" / "moved" / "victim").exists()
    assert (outside / "victim" / "keep").read_text() == "host data"
    assert sentinel.read_text() == "host data"


def test_rmtree_refuses_a_parent_swapped_for_a_link_before_its_open(tree, monkeypatch):
    root, outside, _ = tree
    parent = root / "a" / "b"
    (parent / "victim").mkdir()
    (outside / "victim").mkdir()
    real_open = os.open

    def swap_then_open(name, *args, **kwargs):
        if name == parent.name and not parent.is_symlink():
            parent.rename(root / "a" / "moved")
            parent.symlink_to(outside)
        return real_open(name, *args, **kwargs)

    monkeypatch.setattr(boxfs.os, "open", swap_then_open)
    with pytest.raises(SymlinkRefusedError):
        boxfs.rmtree(parent / "victim", root)
    monkeypatch.undo()
    assert (outside / "victim").is_dir()
    assert (root / "a" / "moved" / "victim").is_dir()


def test_rmtree_deep_tree_never_raises_recursion_error(tree):
    root, _, _ = tree
    deep = root / "a" / "deep"
    deep.mkdir()
    os.close(_deep(deep, sys.getrecursionlimit() + 100, leaf="f"))
    try:
        boxfs.rmtree(deep, root)
    except RecursionError:  # pragma: no cover - the failure this test pins
        pytest.fail("rmtree raised RecursionError")
    except OSError as exc:  # Python < 3.12: shutil.rmtree is recursive
        assert exc.errno == errno.EOVERFLOW
    else:
        assert not deep.exists()


def test_rmtree_missing_ok_never_hides_an_entry_vanishing_mid_walk(tree, monkeypatch):
    """*missing_ok* covers a missing path; a box racing the walk raises or loses nothing."""
    root, _, _ = tree
    t = root / "a" / "t"
    (t / "s").mkdir(parents=True)
    for n in ("f1", "f2"):
        (t / n).write_text("x")
    real_unlink = os.unlink
    fired = []

    def box_removes_it_first(name, *args, **kwargs):
        if name == "f1" and not fired:
            fired.append(1)
            real_unlink(t / "f1")
        return real_unlink(name, *args, **kwargs)

    monkeypatch.setattr(boxfs.os, "unlink", box_removes_it_first)
    try:
        boxfs.rmtree(t, root, missing_ok=True)
    except FileNotFoundError as exc:  # Python < 3.13: shutil raises it
        assert exc.filename == str(t / "f1")
    else:
        assert not t.exists()
    assert fired


def test_rmtree_names_the_full_path_of_an_entry_it_cannot_remove(tree, monkeypatch):
    root, _, _ = tree
    d = root / "a" / "b"
    (d / "sub" / "deeper").mkdir(parents=True)
    real_rmdir = os.rmdir

    def refuse_deeper(name, *args, **kwargs):
        if name == "deeper":
            raise PermissionError(errno.EACCES, "Permission denied", name)
        return real_rmdir(name, *args, **kwargs)

    monkeypatch.setattr(boxfs.os, "rmdir", refuse_deeper)
    with pytest.raises(PermissionError) as info:
        boxfs.rmtree(d, root)
    assert info.value.filename == str(d / "sub" / "deeper")
    with pytest.raises(PermissionError) as info:
        boxfs.rmtree_contents(d, root)
    assert info.value.filename == str(d / "sub" / "deeper")


def test_rmtree_refuses_a_leaf_swapped_for_a_link_after_its_lstat(tree, monkeypatch):
    root, outside, sentinel = tree
    victim = root / "a" / "victim"
    victim.mkdir()
    (outside / "keep").write_text("host data")
    real_rmtree = boxfs.shutil.rmtree

    def swap_then_rmtree(*args, **kwargs):
        victim.rename(root / "a" / "moved")
        victim.symlink_to(outside)
        return real_rmtree(*args, **kwargs)

    monkeypatch.setattr(boxfs.shutil, "rmtree", swap_then_rmtree)
    with pytest.raises(SymlinkRefusedError) as info:
        boxfs.rmtree(victim, root)
    assert info.value.filename == str(victim) and info.value.link == victim
    assert (outside / "keep").read_text() == "host data"
    assert sentinel.read_text() == "host data"


def test_copy_tree_follow_src_links_is_for_a_host_source_only(tree):
    """Following links on a BOX-side source would read through a link the box planted."""
    root, outside, sentinel = tree
    (root / "a" / "b" / "out").symlink_to(sentinel)
    with pytest.raises(ValueError):
        boxfs.copy_tree(
            root / "a", outside / "copy", src_root=root, dst_root=None, follow_src_links=True,
        )
    assert not (outside / "copy").exists()


def test_copy_tree_follow_src_links_skips_a_dangling_link(tree, caplog):
    root, outside, _ = tree
    src = outside / "src"
    src.mkdir()
    (src / "f").write_text("x")
    (src / "gone").symlink_to(outside / "missing")
    with caplog.at_level(logging.WARNING, logger="kanibako"):
        skipped = boxfs.copy_tree(
            src, root / "a" / "dst", src_root=None, dst_root=root, follow_src_links=True,
        )
    assert skipped == [src / "gone"]
    assert (root / "a" / "dst" / "f").read_text() == "x"
    assert not os.path.lexists(root / "a" / "dst" / "gone")

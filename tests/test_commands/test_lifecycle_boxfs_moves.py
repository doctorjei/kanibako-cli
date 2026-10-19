"""The relocation moves go through ``boxfs.rename``: across devices, and never through a link.

Each rerouted site is run with every rename between two directories refused with EXDEV,
as when the source and its destination (or the ``/tmp`` stash) sit on two filesystems.
"""

from __future__ import annotations

import errno
import os
from types import SimpleNamespace

import pytest

from kanibako.commands.box import _lifecycle as lc


@pytest.fixture
def exdev(monkeypatch):
  real = os.rename
  refused: list[str] = []

  def rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
    if src_dir_fd is not None and src_dir_fd != dst_dir_fd:
      refused.append(str(src))
      raise OSError(errno.EXDEV, "Invalid cross-device link", str(src))
    return real(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

  monkeypatch.setattr(os, "rename", rename)
  return refused


def test_marker_stash_crosses_devices_and_puts_back(tmp_path, monkeypatch, exdev):
  root = tmp_path / "root"
  root.mkdir()
  reg, ws = root / "registry.yaml", root / "workset.yaml"
  reg.write_text("boxes: {}\n")
  real_ws = tmp_path / "real-workset.yaml"
  real_ws.write_text("ws\n")
  ws.symlink_to(real_ws)
  monkeypatch.setattr(lc, "_dispose_stash", lambda stash: None)
  unwind = lc._Unwind()
  done = lc._stash_source_marker(lc._SourceTeardown(name="b", markers=(reg, ws)), unwind)
  assert exdev == ["registry.yaml", "workset.yaml"]
  assert not os.path.lexists(reg) and not os.path.lexists(ws)
  assert (done.stash / "registry.yaml").read_text() == "boxes: {}\n"
  assert os.readlink(done.stash / "workset.yaml") == str(real_ws)
  unwind.run()
  assert reg.read_text() == "boxes: {}\n"
  assert ws.is_symlink() and os.readlink(ws) == str(real_ws)
  assert real_ws.read_text() == "ws\n"


def test_log_carry_crosses_devices_and_undoes(tmp_path, monkeypatch, exdev):
  src_logs, dst_logs = tmp_path / "src", tmp_path / "dst"
  src_logs.mkdir()
  src, dst = src_logs / "b.jsonl", dst_logs / "b2.jsonl"
  src.write_text("log\n")
  monkeypatch.setattr(lc, "box_logs_dir_for", lambda *a, **kw: src_logs)
  state = SimpleNamespace(name="b", mode=None, metadata_path=tmp_path, ws=None)
  unwind = lc._Unwind()
  lc._carry_box_logs(state, None, unwind, dst_logs=dst_logs, dst_name="b2")  # type: ignore[arg-type]
  assert "b.jsonl" in exdev
  assert dst.read_text() == "log\n" and not src.exists()
  unwind.run()
  assert src.read_text() == "log\n" and not dst.exists()


def test_move_entry_crosses_devices_without_following_a_leaf_link(tmp_path, exdev):
  root = tmp_path / "root"
  (root / "workspace").mkdir(parents=True)
  outside = tmp_path / "host"
  outside.mkdir()
  (root / "ln").symlink_to(outside)
  (root / "d").mkdir()
  (root / "d" / "f").write_text("x")
  lc._move_entry(root / "ln", root / "workspace" / "ln", root=root)
  lc._move_entry(root / "d", root / "workspace" / "d", root=root)
  assert exdev == ["ln", "d"]
  assert os.readlink(root / "workspace" / "ln") == str(outside)
  assert (root / "workspace" / "d" / "f").read_text() == "x"
  assert not os.path.lexists(root / "ln") and not os.path.lexists(root / "d")
  assert list(outside.iterdir()) == []


def test_channel_partition_crosses_devices(tmp_path, monkeypatch, exdev):
  old_mb, new_mb = tmp_path / "old" / "mailboxes" / "b", tmp_path / "new" / "mailboxes" / "b2"
  old_mb.mkdir(parents=True)
  (old_mb / "msg.md").write_text("hi\n")
  tokens = iter(["ws-a", "ws-b"])
  monkeypatch.setattr(lc, "_state_ws_token", lambda st: next(tokens))
  monkeypatch.setattr(
    lc, "_old_partition", lambda *a: SimpleNamespace(mailbox=old_mb, share_global=None)
  )
  monkeypatch.setattr(
    lc, "_own_partition", lambda *a: SimpleNamespace(mailbox=new_mb, share_global=None)
  )
  old, new = SimpleNamespace(name="b"), SimpleNamespace(name="b2")
  lc._relocate_channel_partition(old, new, None, None)  # type: ignore[arg-type]
  assert exdev == ["b"]
  assert (new_mb / "msg.md").read_text() == "hi\n"
  assert not old_mb.exists()


def test_move_entry_replaces_a_dangling_link_at_the_landing(tmp_path):
  root = tmp_path / "root"
  (root / "workspace" / "d").mkdir(parents=True)
  (root / "workspace" / "d" / "f").symlink_to(tmp_path / "nowhere")
  (root / "f").write_text("x")
  lc._move_entry(root / "f", root / "workspace" / "d", root=root)
  landed = root / "workspace" / "d" / "f"
  assert not landed.is_symlink() and landed.read_text() == "x"
  assert not os.path.lexists(root / "f")

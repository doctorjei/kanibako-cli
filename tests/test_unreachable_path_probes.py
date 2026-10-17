"""A path kanibako cannot reach (EACCES, a link loop) is never read as gone.

Each seam here answered ``Path.exists()``/``is_dir()``/``iterdir()``, which raise a bare
``PermissionError`` on an unreadable parent: a traceback, never a refusal.  Absence is
only ``ENOENT``/``ENOTDIR``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kanibako.errors import ConfigError
from kanibako.settings.config_io import load_doc
from kanibako.utils import stat_unless_gone

needs_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 directory")


@pytest.fixture
def locked(tmp_path):
  """``tmp_path/locked`` (mode 000) holding ``f`` and ``d/``; reopened at teardown."""
  locked = tmp_path / "locked"
  (locked / "d").mkdir(parents=True)
  (locked / "f").write_text("a: 1\n")
  locked.chmod(0)
  yield locked
  locked.chmod(0o755)


def test_a_loop_is_named_as_a_loop(tmp_path):
  os.symlink("lp", tmp_path / "lp")
  with pytest.raises(ConfigError) as info:
    stat_unless_gone(tmp_path / "lp")
  assert str(info.value) == (
    f"{tmp_path / 'lp'}: it is a symlink loop that kanibako "
    "cannot resolve. Remove or repair the link, then retry."
  )


@needs_non_root
def test_iterdir_unless_gone_refuses_an_unlistable_dir_and_passes_a_gone_one(tmp_path, locked):
  from kanibako.utils import iterdir_unless_gone

  (tmp_path / "plain").write_text("")
  assert iterdir_unless_gone(tmp_path / "plain") is None
  assert iterdir_unless_gone(tmp_path / "absent") is None
  with pytest.raises(ConfigError, match=f"^{locked} could not be read"):
    iterdir_unless_gone(locked)
  with pytest.raises(ConfigError, match=f"^{locked / 'd'} could not be read"):
    iterdir_unless_gone(locked / "d")


def test_load_doc_reads_only_enoent_and_enotdir_as_missing(tmp_path):
  (tmp_path / "file").write_text("x: 1\n")
  assert load_doc(tmp_path / "absent.yaml") == {}
  assert load_doc(tmp_path / "file" / "under-a-file.yaml") == {}
  os.symlink("loop.yaml", tmp_path / "loop.yaml")
  with pytest.raises(ConfigError, match="it is a symlink loop"):
    load_doc(tmp_path / "loop.yaml")


@needs_non_root
def test_load_doc_refuses_a_file_under_an_unreadable_parent_by_name(locked):
  with pytest.raises(ConfigError) as info:
    load_doc(locked / "f")
  assert str(info.value) == (
    f"{locked / 'f'} could not be read (Permission denied). "
    "Make it and its parent directories readable, then retry."
  )


@needs_non_root
def test_session_lock_held_counts_an_unprobeable_lock_as_held(tmp_path, locked):
  from kanibako.commands.box._lifecycle import session_lock_held

  assert session_lock_held(tmp_path / "absent.lock") is False
  assert session_lock_held(tmp_path / "absent" / "x.lock") is False
  assert session_lock_held(locked / "f") is True


@needs_non_root
def test_box_running_reason_counts_an_unprobeable_lock_file_as_present(monkeypatch, locked):
  """The second probe, past the lock: no runtime answer + a lock it cannot stat ⇒ running."""
  from kanibako.commands.box import _lifecycle

  monkeypatch.setattr(_lifecycle, "session_lock_held", lambda lock_file: False)
  reason = _lifecycle.box_running_reason(None, locked / "f")
  assert reason is not None and reason.startswith("a lock file exists")


@needs_non_root
def test_existing_member_leaves_keeps_a_leaf_it_cannot_stat(tmp_path, monkeypatch, locked):
  """An unprobeable leaf is ON disk (the user's, never removed); only a gone one is not."""
  from kanibako.commands.box import _lifecycle

  present, dangling = tmp_path / "present", tmp_path / "dangling"
  present.mkdir()
  os.symlink("nowhere", dangling)
  leaves = (present, locked / "d", tmp_path / "absent", dangling)
  monkeypatch.setattr(_lifecycle, "_member_leaves", lambda ws, name: leaves)
  got = _lifecycle._existing_member_leaves(object(), "x")  # type: ignore[arg-type]
  assert set(got) == {present, locked / "d", dangling}
  assert got[locked / "d"] is None


def test_stat_unless_gone_absent_forms(tmp_path):
  (tmp_path / "file").write_text("")
  assert stat_unless_gone(tmp_path / "absent") is None
  assert stat_unless_gone(Path(tmp_path / "file" / "x")) is None


def _guarded_readers():
  """Each reader that answered "absent" from ``is_file()``/``exists()`` before ``load_doc``."""
  from kanibako.project import registry_store, workset_registry
  from kanibako.project.workset import load_workset_settings_doc
  from kanibako.runtime import baseline
  from kanibako.settings import config

  return {
    "workset.yaml": lambda f: load_workset_settings_doc(f.parent),
    "registry.yaml": registry_store.load_registry,
    "workset boxes": workset_registry.load_workset_boxes,
    "unregister": lambda f: workset_registry.unregister_workset_box(f, "x"),
    "system agent": config.read_system_agent,
    "box vault": config.read_box_enable_vault,
    "image baseline": baseline._read_doc,
  }


@pytest.mark.parametrize("reader", list(_guarded_readers()))
def test_a_guarded_reader_refuses_a_looped_file(tmp_path, reader):
  os.symlink("workset.yaml", tmp_path / "workset.yaml")
  with pytest.raises(ConfigError, match="it is a symlink loop"):
    _guarded_readers()[reader](tmp_path / "workset.yaml")


@needs_non_root
@pytest.mark.parametrize("reader", list(_guarded_readers()))
def test_a_guarded_reader_refuses_a_file_under_an_unreadable_parent(locked, reader):
  with pytest.raises(ConfigError, match=f"^{locked / 'workset.yaml'} could not be read"):
    _guarded_readers()[reader](locked / "workset.yaml")


def test_is_file_unless_gone_absent_forms(tmp_path):
  from kanibako.utils import is_file_unless_gone

  (tmp_path / "file").write_text("")
  assert is_file_unless_gone(tmp_path / "file")
  assert not is_file_unless_gone(tmp_path)
  assert not is_file_unless_gone(tmp_path / "absent")
  assert not is_file_unless_gone(tmp_path / "file" / "x")

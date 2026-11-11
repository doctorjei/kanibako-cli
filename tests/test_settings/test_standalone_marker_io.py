"""Standalone marker transport preserves raw bytes and rooted file policy."""

from __future__ import annotations

import errno
import os
import stat
from unittest.mock import Mock

import pytest

from kanibako.errors import ConfigError
from kanibako.settings import config


@pytest.mark.parametrize(
  ("raw", "expected"),
  [
    (b"__STANDALONE__\n", True),
    (b"\t __Standalone__ \r\n", True),
    (b"__standalone__", True),
    (b"\xef\xbb\xbf__STANDALONE__", False),
    (b"\xff__STANDALONE__", False),
    (b"__STANDALONE__\xff", False),
    (" __STANDALONE__".encode(), False),
    (b"__STANDALONE__\nother", False),
    (b'"__STANDALONE__"\n', True),
    (b"' \t__Standalone__ '\n", True),
    (b'"\\u005f_STANDALONE__"\n', True),
    (b'# marker\n"__standalone__" # root\n', True),
    (b'"__STANDALONE__"\n# invalid utf-8: \xff', False),
    (b'["__STANDALONE__"]\n', False),
    (b'{"marker": "__STANDALONE__"}\n', False),
    (b'!!str "__STANDALONE__"\n', False),
    (b'!!python/object/apply:os.system ["false"]', False),
    (b'"__STANDALONE__"\n---\n"other"', False),
    (b'"__STANDALONE__', False),
    (b'"other"', False),
    (b"None\n", False),
    (b"True\n", False),
    (b"42\n", False),
    (b"", False),
  ],
)
def test_marker_keeps_byte_trim_casefold_and_replacement_decode(tmp_path, raw, expected):
  path = tmp_path / config.WORKSET_REGISTRY_FILE
  path.write_bytes(raw)
  assert config.is_standalone_mark_file(path) is expected
  assert path.read_bytes() == raw


@pytest.mark.parametrize("existing", [b" \t__Standalone__\r\n", b"' __Standalone__ '\n"])
def test_marker_read_and_write_keep_explicit_roots_and_newline(tmp_path, monkeypatch, existing):
  path = tmp_path / config.WORKSET_REGISTRY_FILE
  calls = []
  read, write = config.read_box_doc_bytes, config.write_box_doc_text

  def read_box(candidate, root):
    calls.append(("read", candidate, root))
    return read(candidate, root)

  def write_box(candidate, root, text):
    calls.append(("write", candidate, root, text))
    write(candidate, root, text)

  monkeypatch.setattr(config, "read_box_doc_bytes", read_box)
  monkeypatch.setattr(config, "write_box_doc_text", write_box)
  config.write_standalone_mark(tmp_path)
  assert calls == [("read", path, tmp_path), ("write", path, tmp_path, '"__STANDALONE__"\n')]
  assert path.read_bytes() == b'"__STANDALONE__"\n'
  from yapyon import yamlplus

  assert yamlplus.loads(path.read_text()) == "__STANDALONE__"
  path.write_bytes(existing)
  before = path.stat()
  calls.clear()
  config.write_standalone_mark(tmp_path)
  assert calls == [("read", path, tmp_path)]
  assert path.read_bytes() == existing
  assert path.stat().st_ino == before.st_ino
  assert path.stat().st_mtime_ns == before.st_mtime_ns


def test_leaf_marker_link_refuses_without_touching_target(tmp_path):
  root = tmp_path / "root"
  root.mkdir()
  target = tmp_path / "outside"
  target.write_bytes(b"__STANDALONE__\n")
  target.chmod(0o640)
  before = (target.read_bytes(), target.stat().st_mode, target.stat().st_ino)
  path = root / config.WORKSET_REGISTRY_FILE
  path.symlink_to(target)
  assert config.is_standalone_mark_file(path) is False
  with pytest.raises(ConfigError, match="is a symlink"):
    config.write_standalone_mark(root)
  assert path.is_symlink()
  assert (target.read_bytes(), target.stat().st_mode, target.stat().st_ino) == before


def test_explicit_marker_root_can_still_be_a_symlink(tmp_path):
  target = tmp_path / "real"
  target.mkdir()
  root = tmp_path / "root"
  root.symlink_to(target, target_is_directory=True)
  config.write_standalone_mark(root)
  assert root.is_symlink()
  assert (target / config.WORKSET_REGISTRY_FILE).read_bytes() == b'"__STANDALONE__"\n'
  assert config.is_standalone_mark_file(root / config.WORKSET_REGISTRY_FILE)


def test_fifo_marker_is_not_read_as_data_or_written(tmp_path, monkeypatch):
  path = tmp_path / config.WORKSET_REGISTRY_FILE
  os.mkfifo(path)
  monkeypatch.setattr(config, "write_box_doc_text", lambda *_: pytest.fail("FIFO write"))
  assert config.is_standalone_mark_file(path) is False
  with pytest.raises(ConfigError, match="a file that is not the standalone mark"):
    config.write_standalone_mark(tmp_path)
  assert stat.S_ISFIFO(path.stat().st_mode)


@pytest.mark.parametrize("number", [errno.ENOENT, errno.ENOTDIR, errno.EINVAL])
def test_marker_keeps_false_read_errors(tmp_path, monkeypatch, number):
  def failed(*_):
    raise OSError(number, "synthetic read failure")

  monkeypatch.setattr(config, "read_box_doc_bytes", failed)
  assert not config.is_standalone_mark_file(tmp_path / config.WORKSET_REGISTRY_FILE)


def test_marker_permission_error_keeps_path_diagnostic_and_cause(tmp_path, monkeypatch):
  path = tmp_path / config.WORKSET_REGISTRY_FILE
  error = PermissionError(errno.EACCES, "synthetic denial", str(path))

  def failed(*_):
    raise error

  monkeypatch.setattr(config, "read_box_doc_bytes", failed)
  with pytest.raises(ConfigError) as caught:
    config.is_standalone_mark_file(path)
  assert str(path) in str(caught.value)
  assert caught.value.__cause__ is error


def test_marker_write_does_not_create_a_missing_root(tmp_path):
  root = tmp_path / "missing"
  with pytest.raises(FileNotFoundError):
    config.write_standalone_mark(root)
  assert not root.exists()


@pytest.mark.parametrize("raw", [b" \t__Standalone__\r\n", b"# mark\n' __Standalone__ '\n"])
def test_drop_and_restore_use_rooted_byte_doors_before_unlink(tmp_path, monkeypatch, raw):
  from kanibako import boxfs
  from kanibako.settings import config_io, paths

  path = tmp_path / config.WORKSET_REGISTRY_FILE
  path.write_bytes(raw)
  undo = []
  read = Mock(wraps=config_io.read_box_doc_bytes)
  write = Mock(wraps=config_io.write_box_doc_bytes)
  unlink = boxfs.unlink

  def delete(candidate, root):
    assert len(undo) == 1
    assert candidate.read_bytes() == raw
    unlink(candidate, root)

  monkeypatch.setattr(config_io, "read_box_doc_bytes", read)
  monkeypatch.setattr(config_io, "write_box_doc_bytes", write)
  monkeypatch.setattr(boxfs, "unlink", delete)
  paths.drop_standalone_marker(tmp_path, undo.append)
  read.assert_called_once_with(path, tmp_path)
  write.assert_not_called()
  assert not path.exists()
  undo[0]()
  write.assert_called_once_with(path, tmp_path, raw)
  assert path.read_bytes() == raw
  undo[0]()
  write.assert_called_once()


@pytest.mark.parametrize("replacement", ["file", "directory", "symlink"])
def test_marker_restore_keeps_entries_created_after_drop(tmp_path, replacement):
  from kanibako import boxfs
  from kanibako.settings.paths import drop_standalone_marker

  path = tmp_path / config.WORKSET_REGISTRY_FILE
  path.write_bytes(b"__STANDALONE__\n")
  undo = []
  drop_standalone_marker(tmp_path, undo.append)
  target = tmp_path / "outside"
  target.write_bytes(b"user bytes\xff")
  target.chmod(0o640)
  before = (target.read_bytes(), target.stat().st_ino, target.stat().st_mode)
  if replacement == "file":
    path.hardlink_to(target)
  elif replacement == "directory":
    path.mkdir()
  else:
    path.symlink_to(target)
  if replacement == "symlink":
    with pytest.raises(boxfs.SymlinkRefusedError):
      undo[0]()
    assert path.is_symlink()
  else:
    undo[0]()
    assert path.is_dir() if replacement == "directory" else path.samefile(target)
  assert (target.read_bytes(), target.stat().st_ino, target.stat().st_mode) == before


@pytest.mark.parametrize("operation", ["read", "unlink"])
def test_marker_drop_warns_on_io_failure_without_losing_bytes(
  tmp_path, monkeypatch, caplog, operation
):
  from kanibako import boxfs
  from kanibako.settings import config_io, paths

  path = tmp_path / config.WORKSET_REGISTRY_FILE
  raw = b"__STANDALONE__\n"
  path.write_bytes(raw)
  failure = PermissionError(errno.EACCES, "synthetic denial", str(path))
  failed = Mock(side_effect=failure)
  if operation == "read":
    monkeypatch.setattr(config_io, "read_box_doc_bytes", failed)
  else:
    monkeypatch.setattr(boxfs, "unlink", failed)
  undo = []
  with caplog.at_level("WARNING"):
    paths.drop_standalone_marker(tmp_path, undo.append)
  failed.assert_called_once_with(path, tmp_path)
  assert len(undo) == (operation == "unlink")
  assert path.read_bytes() == raw
  assert len(caplog.records) == 1
  message = caplog.records[0].getMessage()
  assert message.count(str(path)) == 1
  assert "could not remove the standalone marker" in message
  assert "synthetic denial" in message
  if undo:
    undo[0]()
    assert path.read_bytes() == raw


@pytest.mark.parametrize("operation", ["unlink", "restore"])
def test_marker_link_swap_never_follows_target(tmp_path, monkeypatch, caplog, operation):
  from kanibako import boxfs
  from kanibako.settings import config_io, paths

  path = tmp_path / config.WORKSET_REGISTRY_FILE
  path.write_bytes(b"__STANDALONE__\n")
  target = tmp_path / "outside"
  target.write_bytes(b"outside bytes\xff")
  target.chmod(0o640)
  before = (target.read_bytes(), target.stat().st_ino, target.stat().st_mode)
  undo = []
  if operation == "unlink":
    unlink = boxfs.unlink

    def swap_and_unlink(candidate, root):
      candidate.unlink()
      candidate.symlink_to(target)
      unlink(candidate, root)

    monkeypatch.setattr(boxfs, "unlink", swap_and_unlink)
    with caplog.at_level("WARNING"):
      paths.drop_standalone_marker(tmp_path, undo.append)
    assert caplog.records == []
    assert not path.exists()
    assert not path.is_symlink()
    undo[0]()
    assert path.read_bytes() == b"__STANDALONE__\n"
  else:
    paths.drop_standalone_marker(tmp_path, undo.append)
    write = config_io.write_box_doc_bytes

    def swap_and_write(candidate, root, raw):
      candidate.symlink_to(target)
      write(candidate, root, raw)

    monkeypatch.setattr(config_io, "write_box_doc_bytes", swap_and_write)
    with pytest.raises(boxfs.SymlinkRefusedError):
      undo[0]()
  assert len(undo) == 1
  assert path.is_symlink() is (operation == "restore")
  assert (target.read_bytes(), target.stat().st_ino, target.stat().st_mode) == before


def test_marker_restore_does_not_create_a_missing_root(tmp_path):
  from kanibako.settings.paths import _restore_mark

  root = tmp_path / "missing"
  with pytest.raises(FileNotFoundError):
    _restore_mark(root / config.WORKSET_REGISTRY_FILE, root, b"__STANDALONE__\n")
  assert not root.exists()


def test_marker_drop_keeps_file_if_undo_registration_fails(tmp_path):
  from kanibako.settings.paths import drop_standalone_marker

  path = tmp_path / config.WORKSET_REGISTRY_FILE
  raw = b"__STANDALONE__\n"
  path.write_bytes(raw)
  failed = Mock(side_effect=RuntimeError("undo registration failed"))
  with pytest.raises(RuntimeError, match="undo registration failed"):
    drop_standalone_marker(tmp_path, failed)
  failed.assert_called_once()
  assert path.read_bytes() == raw

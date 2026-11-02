"""Native JSON transport preserves VS Code's host/box file policies."""

from __future__ import annotations

import json
import os

import pytest

from kanibako.vscode import vscode_config as vc


def test_host_read_uses_default_encoding_and_facade_parser(tmp_path, monkeypatch):
  path = tmp_path / "settings.json"
  path.write_text('{"label": "café"}')
  calls = []
  read, parse = vc.read_doc_text, vc.parse_json_text

  def read_host(candidate):
    calls.append(("read", candidate))
    return read(candidate)

  def parse_json(text):
    calls.append(("parse", text))
    return parse(text)

  monkeypatch.setattr(vc, "read_doc_text", read_host)
  monkeypatch.setattr(vc, "parse_json_text", parse_json)
  assert vc._read_existing_config(path) == {"label": "café"}
  assert calls == [("read", path), ("parse", '{"label": "café"}')]


def test_box_read_uses_explicit_root_without_host_transport(tmp_path, monkeypatch):
  root = tmp_path / "home"
  root.mkdir()
  path = root / "settings.json"
  path.write_text('{"label": "café"}', encoding="utf-8")
  calls = []
  read = vc.read_box_doc_text

  def read_box(candidate, boundary):
    calls.append((candidate, boundary))
    return read(candidate, boundary)

  monkeypatch.setattr(vc, "read_box_doc_text", read_box)
  monkeypatch.setattr(vc, "read_doc_text", lambda *_: pytest.fail("host box-path read"))
  assert vc._read_existing_config(path, root) == {"label": "café"}
  assert calls == [(path, root)]


@pytest.mark.parametrize("rooted", [False, True], ids=["host", "box"])
@pytest.mark.parametrize("text", ["", "{", "null", "[]", '"text"'])
def test_invalid_or_nonobject_json_remains_empty(tmp_path, rooted, text):
  path = tmp_path / "settings.json"
  path.write_text(text)
  assert vc._read_existing_config(path, tmp_path if rooted else None) == {}


@pytest.mark.parametrize("rooted", [False, True], ids=["host", "box"])
def test_unreadable_or_missing_file_remains_empty(tmp_path, monkeypatch, rooted):
  path = tmp_path / "missing.json"
  root = tmp_path if rooted else None
  assert vc._read_existing_config(path, root) == {}

  def denied(*_):
    raise PermissionError("synthetic denial")

  monkeypatch.setattr(vc, "read_box_doc_text" if rooted else "read_doc_text", denied)
  assert vc._read_existing_config(path, root) == {}


@pytest.mark.parametrize("rooted", [False, True], ids=["host", "box"])
def test_decode_error_escapes_before_parse_and_never_rewrites(tmp_path, rooted):
  path = tmp_path / "settings.json"
  path.write_bytes(b"\xff")
  with pytest.raises(UnicodeDecodeError):
    vc._read_existing_config(path, tmp_path if rooted else None)
  assert path.read_bytes() == b"\xff"


def test_parser_type_error_stays_a_tolerated_corrupt_document(tmp_path, monkeypatch):
  path = tmp_path / "settings.json"
  path.write_text("{}")

  def reject(_):
    raise TypeError("synthetic parser input")

  monkeypatch.setattr(vc, "parse_json_text", reject)
  assert vc._read_existing_config(path) == {}


def test_host_seed_keeps_symlink_inode_exact_bytes_and_idempotency(tmp_path, monkeypatch):
  target = tmp_path / "original.json"
  target.write_text('{"extensions": ["old"], "workspaceFolder": "/mine", "x": 1}')
  path = tmp_path / "settings.json"
  path.symlink_to(target)
  inode = target.stat().st_ino
  writes = []
  overwrite = vc.overwrite_doc_text

  def write_host(candidate, text):
    writes.append((candidate, text))
    overwrite(candidate, text)

  monkeypatch.setattr(vc, "overwrite_doc_text", write_host)
  assert vc.seed_attached_container_config(path, workspace_folder="/new", extension="new")
  expected = (
    json.dumps({"extensions": ["old", "new"], "workspaceFolder": "/mine", "x": 1}, indent=2) + "\n"
  )
  assert writes == [(path, expected)]
  assert path.is_symlink()
  assert target.stat().st_ino == inode
  assert target.read_bytes() == expected.encode()
  assert not vc.seed_attached_container_config(path, workspace_folder="/new", extension="new")
  assert writes == [(path, expected)]


def test_box_hook_creates_home_then_uses_box_facade_atomically(tmp_path, monkeypatch):
  root = tmp_path / "missing" / "home"
  path = root / ".claude" / "settings.json"
  calls = []
  write = vc.write_box_doc_text

  def write_box(candidate, boundary, text):
    calls.append((candidate, boundary, root.is_dir(), text))
    write(candidate, boundary, text)

  monkeypatch.setattr(vc, "write_box_doc_text", write_box)
  monkeypatch.setattr(vc, "overwrite_doc_text", lambda *_: pytest.fail("host box-path write"))
  assert vc.seed_session_start_hook(path, root=root)
  first = json.loads(path.read_text())
  assert calls == [(path, root, True, json.dumps(first, indent=2) + "\n")]
  before = path.stat()
  assert not vc.seed_session_start_hook(path, root=root)
  assert path.stat().st_ino == before.st_ino
  assert len(calls) == 1
  path.write_text('{"kept": "value"}')
  before = path.stat().st_ino
  assert vc.seed_session_start_hook(path, root=root)
  assert path.stat().st_ino != before
  assert json.loads(path.read_text())["kept"] == "value"


@pytest.mark.parametrize("linked", ["leaf", "parent"])
def test_box_hook_refuses_links_without_touching_host_target(tmp_path, linked, caplog):
  root = tmp_path / "home"
  root.mkdir()
  outside = tmp_path / "host"
  outside.mkdir()
  target = outside / "settings.json"
  target.write_text('{"host": true}')
  target.chmod(0o640)
  before = (target.read_bytes(), target.stat().st_mode, target.stat().st_ino)
  if linked == "leaf":
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").symlink_to(target)
  else:
    (root / ".claude").symlink_to(outside)
  path = root / ".claude" / "settings.json"
  assert not vc.seed_session_start_hook(path, root=root)
  assert (target.read_bytes(), target.stat().st_mode, target.stat().st_ino) == before
  assert any(str(path) in record.message for record in caplog.records)


def test_box_read_refuses_nonregular_file_without_blocking(tmp_path):
  path = tmp_path / "settings.json"
  os.mkfifo(path)
  assert vc._read_existing_config(path, tmp_path) == {}


def test_box_read_keeps_explicit_root_boundary(tmp_path):
  root = tmp_path / "home"
  root.mkdir()
  path = tmp_path / "outside.json"
  path.write_text("{}")
  with pytest.raises(ValueError):
    vc._read_existing_config(path, root)

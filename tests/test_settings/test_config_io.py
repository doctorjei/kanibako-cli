"""Native configuration codecs and host transport."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kanibako.settings import config_io


@pytest.mark.parametrize(
  ("text", "expected"),
  [
    (
      '{"url": "https://example.com/*keep*/", "value": ",}"}',
      {"url": "https://example.com/*keep*/", "value": ",}"},
    ),
    (
      '{\n  // note\n  "url": "https://example.com", /* note */\n  "list": [1,],\n}',
      {"url": "https://example.com", "list": [1]},
    ),
    ('{"value": 1, "value": 2}', {"value": 2}),
    ("[true, false, null]", [True, False, None]),
    ('"text"', "text"),
    ("null", None),
    ('{"value": 1 // inline\n}', None),
    ("not json", None),
    ("", None),
    ('{"value": "before/*removed*/after",}', {"value": "beforeafter"}),
  ],
)
def test_jsonc_keeps_strict_first_and_existing_fallback(text, expected):
  assert config_io.load_jsonc(text) == expected


def test_jsonc_attempts_strict_then_stripped_text(monkeypatch):
  calls = []
  real_parse = config_io.parse_json_text

  def parse(text):
    calls.append(text)
    return real_parse(text)

  monkeypatch.setattr(config_io, "parse_json_text", parse)
  text = '{\n// note\n"value": 1,\n}'
  assert config_io.load_jsonc(text) == {"value": 1}
  assert calls == [text, '{\n\n"value": 1\n}']
  calls.clear()
  assert config_io.load_jsonc('{"value": 1}') == {"value": 1}
  assert calls == ['{"value": 1}']


def test_strict_native_json_does_not_accept_jsonc():
  with pytest.raises(json.JSONDecodeError):
    config_io.parse_json_text('{"value": 1,}')
  assert config_io.parse_json_text('{"value": null}') == {"value": None}


def test_host_reader_retains_explicit_and_default_encoding(tmp_path, monkeypatch):
  path = tmp_path / "settings.json"
  calls = []

  def read(self, *, encoding=None):
    calls.append((self, encoding))
    return "café"

  monkeypatch.setattr(Path, "read_text", read)
  assert config_io.read_doc_text(path) == "café"
  assert config_io.read_doc_text(path, encoding="utf-8") == "café"
  assert calls == [(path, None), (path, "utf-8")]


def test_explicit_utf8_read_normalizes_newlines_and_preserves_decode_error(tmp_path):
  path = tmp_path / "settings.json"
  path.write_bytes("café\r\n雪\r".encode("utf-8"))
  assert config_io.read_doc_text(path, encoding="utf-8") == "café\n雪\n"
  path.write_bytes(b"\xff")
  with pytest.raises(UnicodeDecodeError):
    config_io.read_doc_text(path, encoding="utf-8")


def test_pretty_json_overwrite_keeps_links_inode_and_exact_bytes(tmp_path):
  target = tmp_path / "real.json"
  target.write_text("old")
  target.chmod(0o640)
  alias = tmp_path / "settings.json"
  alias.symlink_to(target)
  before = target.stat()
  data = {"name": "café", "enabled": True}
  text = config_io.render_json_text(data, indent=2) + "\n"
  config_io.overwrite_doc_text(alias, text)
  assert alias.is_symlink()
  assert target.read_bytes() == b'{\n  "name": "caf\\u00e9",\n  "enabled": true\n}\n'
  assert target.stat().st_ino == before.st_ino
  assert target.stat().st_mode == before.st_mode
  nested = tmp_path / "nested" / "settings.json"
  config_io.overwrite_doc_text(nested, text)
  assert nested.read_bytes() == target.read_bytes()


def test_native_render_defaults_and_explicit_separators_are_unchanged():
  data = {"x": [None, True, "雪"]}
  assert config_io.render_json_text(data) == json.dumps(data)
  assert config_io.render_json_text(data, separators=(",", ":")) == json.dumps(
    data, separators=(",", ":")
  )


@pytest.mark.parametrize("command", ["code", "diagnose"])
def test_command_reads_keep_utf8_and_original_oserror_policy(tmp_path, monkeypatch, command):
  from kanibako.commands import code_cmd, diagnose

  path = tmp_path / "Code" / "User" / "settings.json"
  path.parent.mkdir(parents=True)
  path.write_text("{}")
  failure = PermissionError(13, "denied", str(path))
  calls = []

  def read(value, *, encoding=None):
    calls.append((value, encoding))
    raise failure

  if command == "code":
    monkeypatch.setattr(code_cmd, "user_config_home", lambda: tmp_path)
    monkeypatch.setattr(code_cmd, "read_doc_text", read)
    assert code_cmd._wire_docker_path(tmp_path / "wrapper") == 1
  else:
    monkeypatch.setattr(diagnose, "read_doc_text", read)
    assert diagnose._check_vscode_docker_path(path)[0] == "--"
  assert calls == [(path, "utf-8")]


@pytest.mark.parametrize("command", ["code", "diagnose"])
def test_command_reads_keep_unicode_error_visible(tmp_path, monkeypatch, command):
  from kanibako.commands import code_cmd, diagnose

  path = tmp_path / "Code" / "User" / "settings.json"
  path.parent.mkdir(parents=True)
  path.write_bytes(b"\xff")
  if command == "code":
    monkeypatch.setattr(code_cmd, "user_config_home", lambda: tmp_path)
    with pytest.raises(UnicodeDecodeError):
      code_cmd._wire_docker_path(tmp_path / "wrapper")
  else:
    with pytest.raises(UnicodeDecodeError):
      diagnose._check_vscode_docker_path(path)


def test_wire_rewrites_through_host_link_only_after_consent(tmp_path, monkeypatch):
  from kanibako.commands import code_cmd

  path = tmp_path / "Code" / "User" / "settings.json"
  path.parent.mkdir(parents=True)
  target = tmp_path / "real-settings.json"
  original = '{"editor.fontFamily": "雪"}\n'
  target.write_text(original, encoding="utf-8")
  path.symlink_to(target)
  before = target.stat()
  wrapper = tmp_path / "wrapper"
  wrapper.write_text("wrapper")
  monkeypatch.setattr(code_cmd, "user_config_home", lambda: tmp_path)
  monkeypatch.setattr(code_cmd.sys.stdin, "isatty", lambda: True)
  monkeypatch.setattr("builtins.input", lambda: "n")
  assert code_cmd._wire_docker_path(wrapper) == 1
  assert target.read_bytes() == original.encode("utf-8")
  monkeypatch.setattr("builtins.input", lambda: "y")
  assert code_cmd._wire_docker_path(wrapper) is None
  assert path.is_symlink()
  assert target.stat().st_ino == before.st_ino
  expected = {"editor.fontFamily": "雪", "dev.containers.dockerPath": str(wrapper)}
  assert target.read_bytes() == (json.dumps(expected, indent=2) + "\n").encode("utf-8")


@pytest.mark.parametrize(
  "text",
  [
    "",
    'name = "literal"\nenabled = true\nvalues = [1, 2.5, false]\n',
    "when = 2026-11-02T12:30:00Z\nday = 2026-11-02\ntime = 12:30:00\n",
    '[model_providers.native]\nbase_url = "https://e.example/v1"\nenv_key = "TOKEN"\n',
    "value = inf\nnegative = -inf\n",
  ],
)
def test_native_toml_keeps_stdlib_values_and_types(text):
  import tomllib

  assert config_io.parse_toml_text(text) == tomllib.loads(text)


def test_native_toml_keeps_nan():
  import math

  assert math.isnan(config_io.parse_toml_text("value = nan\n")["value"])


@pytest.mark.parametrize("text", ["[broken", "value = null", "value = 1\nvalue = 2\n"])
def test_native_toml_preserves_stdlib_syntax_errors(text):
  import tomllib

  with pytest.raises(tomllib.TOMLDecodeError) as native:
    tomllib.loads(text)
  with pytest.raises(tomllib.TOMLDecodeError) as facade:
    config_io.parse_toml_text(text)
  assert str(facade.value) == str(native.value)


def test_native_yaml_does_not_inherit_kanibako_document_guards():
  import yaml

  text = "value: None\nrepeat: first\nrepeat: last\n"
  assert (
    config_io.parse_native_yaml(text)
    == yaml.safe_load(text)
    == {"value": "None", "repeat": "last"}
  )
  assert config_io.parse_doc_text("value: None\n") == {"value": None}
  cyclic = config_io.parse_native_yaml("value: &loop [*loop]\n")["value"]
  assert cyclic[0] is cyclic
  text = "value: " + "[" * 70 + "1" + "]" * 70
  data = config_io.parse_native_yaml(text)["value"]
  for _ in range(70):
    data = data[0]
  assert data == 1


def test_packaged_byte_reader_preserves_a_traversable_resource(tmp_path):
  import zipfile

  data = b"\x00\xff\xef\xbb\xbf\r\n{}\n"
  archive = tmp_path / "package.zip"
  with zipfile.ZipFile(archive, "w") as stream:
    stream.writestr("data/hooks.json", data)
  with zipfile.ZipFile(archive) as stream:
    resource = zipfile.Path(stream, "data/hooks.json")
    assert config_io.read_packaged_bytes(resource) == data
    with pytest.raises(FileNotFoundError):
      config_io.read_packaged_bytes(zipfile.Path(stream, "absent.json"))


def test_box_byte_transport_keeps_exact_bytes_atomic_replace_and_mode(tmp_path):
  root = tmp_path / "home"
  root.mkdir()
  path = root / "nested" / "hooks.json"
  first = b"\xff\x00\r\n{}\n"
  config_io.write_box_doc_bytes(path, root, first)
  assert config_io.read_box_doc_bytes(path, root) == first
  path.chmod(0o640)
  inode = path.stat().st_ino
  config_io.write_box_doc_bytes(path, root, b"replacement\n")
  assert config_io.read_box_doc_bytes(path, root) == b"replacement\n"
  assert path.stat().st_ino != inode
  assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("link", ["leaf", "parent"])
def test_box_byte_transport_refuses_links_without_touching_target(tmp_path, link):
  from kanibako.boxfs import SymlinkRefusedError

  root = tmp_path / "home"
  root.mkdir()
  outside = tmp_path / "outside"
  outside.mkdir()
  target = outside / "hooks.json"
  target.write_bytes(b"host sentinel\n")
  before = (target.read_bytes(), target.stat().st_mode, target.stat().st_ino)
  if link == "leaf":
    (root / "nested").mkdir()
    (root / "nested" / "hooks.json").symlink_to(target)
  else:
    (root / "nested").symlink_to(outside)
  path = root / "nested" / "hooks.json"
  with pytest.raises(SymlinkRefusedError):
    config_io.read_box_doc_bytes(path, root)
  with pytest.raises(SymlinkRefusedError):
    config_io.write_box_doc_bytes(path, root, b"new")
  assert (target.read_bytes(), target.stat().st_mode, target.stat().st_ino) == before


def test_box_byte_transport_keeps_root_and_error_contracts(tmp_path, monkeypatch):
  root = tmp_path / "home"
  root.mkdir()
  outside = tmp_path / "outside.json"
  outside.write_bytes(b"{}")
  original = outside.read_bytes()
  for operation, args in (
    (config_io.read_box_doc_bytes, (outside, root)),
    (config_io.write_box_doc_bytes, (outside, root, b"new")),
  ):
    with pytest.raises(ValueError):
      operation(*args)
  assert outside.read_bytes() == original
  error = PermissionError("synthetic denial")

  def denied(*_):
    raise error

  monkeypatch.setattr(config_io.boxfs, "read_bytes", denied)
  with pytest.raises(PermissionError) as caught:
    config_io.read_box_doc_bytes(root / "config", root)
  assert caught.value is error
  monkeypatch.setattr(config_io.boxfs, "write_bytes", denied)
  with pytest.raises(PermissionError) as caught:
    config_io.write_box_doc_bytes(root / "config", root, b"{}")
  assert caught.value is error


@pytest.mark.parametrize("kind", [str, bytes, bytearray])
def test_native_json_preserves_stdlib_text_and_byte_inputs(kind):
  raw = '{"value": [true, null, "café"]}'
  data = raw if kind is str else kind(raw.encode("utf-8"))
  assert config_io.parse_json_text(data) == json.loads(data)


@pytest.mark.parametrize("data", [b"{", bytearray(b"{"), b"\xff"])
def test_native_json_keeps_byte_input_errors(data):
  try:
    json.loads(data)
  except ValueError as error:
    with pytest.raises(type(error)) as raised:
      config_io.parse_json_text(data)
    assert raised.value.args == error.args
  else:
    pytest.fail("synthetic malformed JSON unexpectedly parsed")


def test_host_copy_preserves_copy2_links_metadata_and_inode(tmp_path):
  import os

  source = tmp_path / "source.json"
  source.write_bytes(b'{"value": 1}\n')
  source.chmod(0o640)
  os.utime(source, ns=(1000000000, 2000000000))
  source_link = tmp_path / "source-link.json"
  source_link.symlink_to(source)
  held = tmp_path / "held.json"
  held.write_text("old")
  inode = held.stat().st_ino
  destination = tmp_path / "destination.json"
  destination.symlink_to(held)

  config_io.copy_doc_file(source_link, destination)

  assert source_link.is_symlink() and destination.is_symlink()
  assert held.read_bytes() == source.read_bytes()
  assert held.stat().st_ino == inode
  assert held.stat().st_mode == source.stat().st_mode
  assert held.stat().st_mtime_ns == source.stat().st_mtime_ns


def test_host_copy_does_not_create_parents_and_keeps_copy_errors(tmp_path, monkeypatch):
  source = tmp_path / "source.json"
  source.write_text("{}")
  destination = tmp_path / "absent" / "config.json"
  with pytest.raises(FileNotFoundError):
    config_io.copy_doc_file(source, destination)
  assert not destination.parent.exists()
  error = PermissionError("synthetic copy denial")
  seen = []

  def fail(src, dst):
    seen.append((src, dst))
    raise error

  monkeypatch.setattr(config_io.shutil, "copy2", fail)
  with pytest.raises(PermissionError) as raised:
    config_io.copy_doc_file(source, destination)
  assert raised.value is error
  assert seen == [(str(source), str(destination))]


def test_fixed_json_replace_keeps_temp_mode_and_replaces_host_link(tmp_path):
  held = tmp_path / "held.json"
  held.write_text("untouched")
  destination = tmp_path / "config.json"
  destination.symlink_to(held)
  temporary = destination.with_suffix(".json.tmp")
  temporary.write_text("old temp")
  temporary.chmod(0o640)
  before = temporary.stat()
  data = {"unknown": [True, None], "value": "literal"}

  config_io.replace_json_doc(destination, data)

  assert not destination.is_symlink()
  assert destination.read_text() == json.dumps(data, indent=2) + "\n"
  assert destination.stat().st_ino == before.st_ino
  assert destination.stat().st_mode == before.st_mode
  assert held.read_text() == "untouched"
  assert not temporary.exists()


def test_fixed_json_replace_retains_existing_temp_link_behavior(tmp_path):
  held = tmp_path / "held.json"
  held.write_text("old")
  destination = tmp_path / "config.json"
  destination.write_text("destination")
  temporary = destination.with_suffix(".json.tmp")
  temporary.symlink_to(held)

  config_io.replace_json_doc(destination, {"value": 1})

  assert destination.is_symlink()
  assert held.read_text() == '{\n  "value": 1\n}\n'
  assert not temporary.is_symlink()


def test_fixed_json_replace_serializes_before_open_and_never_creates_parents(tmp_path):
  destination = tmp_path / "config.json"
  destination.write_text("destination")
  temporary = destination.with_suffix(".json.tmp")
  temporary.write_text("keep temp")
  with pytest.raises(TypeError):
    config_io.replace_json_doc(destination, {"value": object()})
  assert destination.read_text() == "destination"
  assert temporary.read_text() == "keep temp"
  absent = tmp_path / "absent" / "config.json"
  with pytest.raises(FileNotFoundError):
    config_io.replace_json_doc(absent, {})
  assert not absent.parent.exists()


def test_fixed_json_replace_preserves_temp_after_replace_failure(tmp_path):
  destination = tmp_path / "config.json"
  destination.mkdir()
  temporary = destination.with_suffix(".json.tmp")
  with pytest.raises(IsADirectoryError):
    config_io.replace_json_doc(destination, {"value": 1})
  assert destination.is_dir()
  assert temporary.read_text() == '{\n  "value": 1\n}\n'


def test_existing_parent_overwrite_creates_leaf_and_preserves_host_links(tmp_path):
  path = tmp_path / "new.json"
  config_io.overwrite_doc_text_in_existing_parent(path, "first\n")
  assert path.read_bytes() == b"first\n"
  path.chmod(0o640)
  before = path.stat()
  link = tmp_path / "link.json"
  link.symlink_to(path)
  config_io.overwrite_doc_text_in_existing_parent(link, "replacement\n")
  assert link.is_symlink()
  assert path.read_bytes() == b"replacement\n"
  assert path.stat().st_ino == before.st_ino
  assert path.stat().st_mode == before.st_mode


def test_existing_parent_overwrite_does_not_create_missing_parents(tmp_path):
  path = tmp_path / "absent" / "config.json"
  with pytest.raises(FileNotFoundError):
    config_io.overwrite_doc_text_in_existing_parent(path, "value")
  assert not path.parent.exists()


def test_existing_parent_overwrite_keeps_default_encoding_and_write_error(tmp_path, monkeypatch):
  path = tmp_path / "config.json"
  path.write_text("keep")
  error = PermissionError("synthetic write denial")
  calls = []

  def fail(source, text, *args, **kwargs):
    calls.append((source, text, args, kwargs))
    raise error

  monkeypatch.setattr(Path, "write_text", fail)
  with pytest.raises(PermissionError) as raised:
    config_io.overwrite_doc_text_in_existing_parent(path, "new")
  assert raised.value is error
  assert calls == [(path, "new", (), {})]
  assert path.read_text() == "keep"

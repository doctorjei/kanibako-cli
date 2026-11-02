"""Native JSON(C) configuration codecs and host transport."""

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

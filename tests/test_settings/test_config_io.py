"""Native configuration codecs and host transport."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kanibako.errors import ConfigError, PackagingError
from kanibako.settings import config_io


@pytest.mark.parametrize(
  ("line", "column"),
  [
    (None, 10),
    (1, None),
    (0, 10),
    (-1, 10),
    (4, 10),
    (1, 0),
    (1, -1),
    (1, 100),
    (True, 10),
    (False, 10),
    (1, True),
    (1, False),
    (1.0, 10),
    ("1", 10),
    (1, -0.5),
    (1, "10"),
    (1, 1),
    (2, 7),
  ],
)
def test_owned_parse_error_without_matching_location_has_no_brace_cure(
  tmp_path, monkeypatch, line, column
):
  from yapyon import AkanError

  path = tmp_path / "settings.yaml"
  text = "value: {$HOME}/x\nother: ]\n"
  path.write_text(text)
  failure = AkanError("bad input", line, column, source=str(path))

  def fail(*args, **kwargs):
    raise failure

  monkeypatch.setattr(config_io, "parse_doc_text", fail)
  with pytest.raises(ConfigError) as caught:
    config_io.load_doc(path)
  assert caught.value.__cause__ is failure
  assert str(failure) in str(caught.value)
  assert "must be quoted" not in str(caught.value)
  assert "{$HOME}" not in str(caught.value)
  assert path.read_text() == text


@pytest.mark.parametrize(
  ("text", "column", "cure"),
  [
    ("v: {$X}/x\n", 3, "v: '{$X}/x'"),
    ("{$X}/x\n", 0, "'{$X}/x'"),
    ("v: {$X}/x\n", 2, None),
    ("{$X}/x\n", -1, None),
    ("{$X}/x\n", False, None),
    ("{$X}/x\n", None, None),
    ("{$X}/x\n", 0.0, None),
    ("{$X}/x\n", "0", None),
  ],
)
def test_owned_column_adapter_at_opening_brace(tmp_path, monkeypatch, text, column, cure):
  from yapyon import AkanError

  path = tmp_path / "settings.yaml"
  path.write_text(text)
  failure = AkanError("opening-brace refusal", 1, column, source=str(path))

  def fail(*args, **kwargs):
    raise failure

  def forbidden(*args, **kwargs):
    pytest.fail("owned diagnostic used YAML fallback")

  monkeypatch.setattr(config_io, "parse_doc_text", fail)
  monkeypatch.setattr(yaml, "load", forbidden)
  with pytest.raises(ConfigError) as caught:
    config_io.load_doc(path)
  assert caught.value.__cause__ is failure
  assert str(failure) in str(caught.value)
  message = str(caught.value)
  assert ("must be quoted" in message) is (cure is not None)
  if cure is not None:
    assert cure in message
  assert path.read_text() == text


@pytest.mark.parametrize("source", [None, "another-fragment.yaml"])
def test_owned_parse_error_from_another_source_has_no_brace_cure(tmp_path, monkeypatch, source):
  from yapyon import AkanError

  path = tmp_path / "settings.yaml"
  path.write_text("value: {$HOME}/x\n")
  failure = AkanError("bad input", 1, 15, source=source)

  def fail(*args, **kwargs):
    raise failure

  monkeypatch.setattr(config_io, "parse_doc_text", fail)
  with pytest.raises(ConfigError) as caught:
    config_io.load_doc(path)
  assert caught.value.__cause__ is failure
  assert "must be quoted" not in str(caught.value)


@pytest.mark.parametrize("recursion", [False, True])
def test_owned_parse_refusal_never_falls_back_to_yaml(tmp_path, monkeypatch, recursion):
  from yapyon import AkanError

  path = tmp_path / "settings.yaml"
  path.write_text("value: {$HOME}/x\n")

  def forbidden(*args, **kwargs):
    pytest.fail("owned refusal fell back to YAML")

  def exhaust(*args, **kwargs):
    raise RecursionError("parser stack exhausted")

  monkeypatch.setattr(yaml, "load", forbidden)
  if recursion:
    monkeypatch.setattr(config_io, "parse_doc_text", exhaust)
  with pytest.raises(ConfigError) as caught:
    config_io.load_doc(path)
  assert isinstance(caught.value.__cause__, RecursionError if recursion else AkanError)
  assert ("must be quoted" in str(caught.value)) is not recursion


@pytest.mark.parametrize("buffer", [None, "value: {$HOME}/x\n\0"])
def test_native_yaml_problem_without_matching_mark_has_no_brace_cure(buffer):
  mark = yaml.Mark("native", 0, 0, 0, buffer, 0) if buffer is not None else None
  failure = yaml.MarkedYAMLError(problem="bad input", problem_mark=mark)
  assert config_io.yaml_problem(failure) == (
    "bad input (line 1, column 1)" if mark is not None else "bad input"
  )
  assert config_io.yaml_problem(yaml.YAMLError("other\nproblem")) == "other problem"


def test_owned_emitter_preserves_shared_containers_until_public_export(monkeypatch):
  from yapyon import loads, yamlplus

  shared = ({"value": "雪"},)
  original = {"first": shared, "second": shared}
  real_dumps = yamlplus.dumps

  def capture(document):
    assert document["first"] is document["second"]
    assert document["first"] is not shared
    assert document["first"][0] is not shared[0]
    return real_dumps(document)

  monkeypatch.setattr(yamlplus, "dumps", capture)
  assert loads(config_io._dump_text(original)) == {
    "first": [{"value": "雪"}],
    "second": [{"value": "雪"}],
  }
  assert original["first"] is original["second"] is shared


def test_owned_emitter_leaves_shared_output_budget_to_public_writer(tmp_path, monkeypatch):
  from yapyon import yamlplus

  shared = {"value": "x" * 80}
  original = {"first": shared, "second": shared}
  path = tmp_path / "settings.yaml"
  path.write_bytes(b"sentinel\n")
  real_dumps = yamlplus.dumps

  def bounded(document):
    assert document["first"] is document["second"]
    return real_dumps(document, max_size=128)

  monkeypatch.setattr(yamlplus, "dumps", bounded)
  with pytest.raises(ValueError, match="max_size"):
    config_io.dump_doc(path, original)
  assert path.read_bytes() == b"sentinel\n"
  assert original["first"] is original["second"] is shared


@pytest.mark.parametrize("kind", [dict, list])
def test_owned_emitter_public_cycle_error_leaves_file_and_input_untouched(tmp_path, kind):
  from yapyon import AkanError

  cycle = kind()
  if kind is dict:
    cycle["again"] = cycle
  else:
    cycle.append(cycle)
  path = tmp_path / "settings.yaml"
  path.write_bytes(b"sentinel\n")
  with pytest.raises(AkanError, match="cycle"):
    config_io.dump_doc(path, {"loop": cycle})
  assert path.read_bytes() == b"sentinel\n"
  assert (cycle["again"] if kind is dict else cycle[0]) is cycle


def test_owned_emitter_uses_public_yamlplus_without_legacy_fallback(monkeypatch):
  from yapyon import loads, yamlplus

  original = {"box": {"bindings": {"rw": {"/workspace": ("~/source", "z")}}}}
  calls = []
  real_dumps = yamlplus.dumps

  def capture(document):
    calls.append(document)
    return real_dumps(document)

  def forbidden(*args, **kwargs):
    pytest.fail("owned emitter used PyYAML")

  monkeypatch.setattr(yamlplus, "dumps", capture)
  monkeypatch.setattr(yaml, "dump", forbidden)
  text = config_io._dump_text(original)
  assert loads(text) == {"box": {"bindings": {"rw": {"/workspace": ["~/source", "z"]}}}}
  assert len(calls) == 1
  assert calls[0] is not original
  assert calls[0]["box"] is not original["box"]
  assert original["box"]["bindings"]["rw"]["/workspace"] == ("~/source", "z")


def test_owned_emitter_copies_nested_tuples_and_preserves_scalar_meanings():
  from yapyon import loads

  from kanibako.settings.settings_resolve import DataExpr

  values = [None, False, 0, "", "None", "on", "雪", DataExpr("{meta.box.home}"), "{$HOME}"]
  original = {"box": {"env": {"LABEL": "None"}}, "rows": ({"values": values},)}
  text = config_io._dump_text(original)
  actual = loads(text)
  assert list(actual) == ["box", "rows"]
  assert actual == {"box": {"env": {"LABEL": "None"}}, "rows": [{"values": values}]}
  assert config_io.parse_doc_text(text) == actual
  assert text.endswith("\n") and "雪" in text
  assert isinstance(original["rows"], tuple)
  assert original["rows"][0]["values"] is values
  assert isinstance(values[-2], DataExpr)


@pytest.mark.parametrize(
  "source",
  [
    "box:\n  shell: !!binary YWJj\n",
    "box:\n  shell: 2026-11-02\n",
    "box:\n  shell: 2026-11-02T03:04:05.123456+05:30\n",
    "box:\n  shell: 2026-11-02T03:04:05Z\n",
  ],
)
def test_owned_emitter_preserves_legacy_scalar_consumer_text(tmp_path, source):
  from yapyon import loads

  from kanibako.settings.settings_launch import _box_scalar_config

  path = tmp_path / "settings.yaml"
  path.write_text(source)
  original = config_io.parse_template_yaml(source)
  value = original["box"]["shell"]
  before = _box_scalar_config({"box.shell": value}).box_shell
  config_io.dump_doc(path, original)
  text = path.read_text()
  current = config_io.load_doc(path)["box"]["shell"]
  public = loads(text)["box"]["shell"]
  assert _box_scalar_config({"box.shell": current}).box_shell == before
  assert _box_scalar_config({"box.shell": public}).box_shell == before
  assert current == public == str(value)
  assert original["box"]["shell"] is value


@pytest.mark.parametrize("existing", [False, True])
def test_owned_emitter_failure_never_reaches_file_transport(tmp_path, monkeypatch, existing):
  path = tmp_path / "nested" / "settings.yaml"
  if existing:
    path.parent.mkdir()
    path.write_bytes(b"host sentinel\n")
    path.chmod(0o640)
    before = path.stat()
  writes = []
  monkeypatch.setattr(config_io, "write_doc_text", lambda *args: writes.append(args))
  with pytest.raises(TypeError):
    config_io.dump_doc(path, {"box": {"shell": object()}}, header="# header\n")
  assert writes == []
  if existing:
    after = path.stat()
    assert path.read_bytes() == b"host sentinel\n"
    assert (after.st_ino, after.st_mode) == (before.st_ino, before.st_mode)
  else:
    assert not path.parent.exists()


def test_owned_emitter_keeps_public_writer_failure_without_fallback(tmp_path, monkeypatch):
  from yapyon import yamlplus

  path = tmp_path / "settings.yaml"
  failure = RuntimeError("writer failed")

  def fail(_document):
    raise failure

  monkeypatch.setattr(yamlplus, "dumps", fail)
  with pytest.raises(RuntimeError) as caught:
    config_io.dump_doc(path, {"box": {"shell": "sh"}})
  assert caught.value is failure
  assert not path.exists()


def test_packaged_mapping_uses_public_yapyon_and_copies_plain_containers(tmp_path, monkeypatch):
  import yapyon
  from yapyon import yamlplus

  path = tmp_path / "packaged.yaml"
  text = """base: 6
result: (base)
"on": False
"None": None
"1": 0
"null": ""
rows: [{"first": [None, False, 0, "", {}, []], "last": {"雪": "literal"}}]
"""
  path.write_text(text)
  real_loads = yapyon.loads
  parsed = []

  def capture(document, **kwargs):
    assert document == text
    assert kwargs == {"source": str(path)}
    result = real_loads(document, **kwargs)
    parsed.append(result)
    return result

  def forbidden(*args, **kwargs):
    pytest.fail("packaged mapping used a legacy or YamlPlus reader")

  monkeypatch.setattr(yapyon, "loads", capture)
  monkeypatch.setattr(yamlplus, "loads", forbidden)
  monkeypatch.setattr(config_io, "parse_packaged", forbidden)
  actual = config_io.load_packaged_mapping(path, path.name)
  assert len(parsed) == 1
  assert list(actual) == ["base", "result", "on", "None", "1", "null", "rows"]
  assert actual["result"] == 6
  assert actual["on"] is False
  assert actual["None"] is None
  assert "absent" not in actual
  assert type(actual["1"]) is int and actual["1"] == 0
  assert actual["null"] == ""
  assert list(actual["rows"][0]) == ["first", "last"]
  assert actual["rows"][0]["first"] == [None, False, 0, "", {}, []]

  def plain_and_detached(value, original):
    if isinstance(original, dict):
      assert type(value) is dict and value is not original
      assert list(value) == list(original)
      assert all(type(key) is str for key in value)
      for key in original:
        plain_and_detached(value[key], original[key])
    elif isinstance(original, list):
      assert type(value) is list and value is not original
      for child, source in zip(value, original, strict=True):
        plain_and_detached(child, source)
    else:
      assert type(value) is type(original) and value == original

  plain_and_detached(actual, parsed[0])
  actual["rows"][0]["last"]["雪"] = "changed"
  actual["rows"][0]["first"].append("new")
  assert parsed[0]["rows"][0]["last"]["雪"] == "literal"
  fresh = config_io.load_packaged_mapping(path, path.name)
  assert fresh == parsed[0]
  assert fresh["rows"] is not actual["rows"]


@pytest.mark.parametrize("text", ["[broken", "", "# empty\n", "name: bare\n"])
def test_packaged_mapping_parse_failure_names_resource_and_cause(tmp_path, monkeypatch, text):
  from yapyon import AkanError

  path = tmp_path / "broken.yaml"
  path.write_text(text)

  def forbidden(*args, **kwargs):
    pytest.fail("parse failure fell back to YAML")

  monkeypatch.setattr(config_io, "parse_packaged", forbidden)
  with pytest.raises(PackagingError) as raised:
    config_io.load_packaged_mapping(path, "shipped-defaults.yaml")
  assert "shipped-defaults.yaml" in str(raised.value)
  assert str(path) in str(raised.value)
  assert isinstance(raised.value.__cause__, AkanError)


@pytest.mark.parametrize("text", ['"scalar"', "None", "False", "0", "[]", '[{"a": 1}]'])
def test_packaged_mapping_refuses_nonmapping_values(tmp_path, text):
  path = tmp_path / "shape.yaml"
  path.write_text(text)
  with pytest.raises(PackagingError, match="shape.yaml.*not a Yapyon mapping"):
    config_io.load_packaged_mapping(path, path.name)


def test_packaged_mapping_accepts_explicit_empty_mapping(tmp_path):
  path = tmp_path / "empty-map.yaml"
  path.write_text("{}")
  actual = config_io.load_packaged_mapping(path, path.name)
  assert type(actual) is dict and actual == {}


def test_packaged_mapping_preserves_missing_and_decode_errors(tmp_path):
  path = tmp_path / "absent.yaml"
  with pytest.raises(FileNotFoundError):
    config_io.load_packaged_mapping(path, path.name)
  path.write_bytes(b"\xff")
  with pytest.raises(UnicodeDecodeError):
    config_io.load_packaged_mapping(path, path.name)


def test_packaged_mapping_preserves_permission_error_and_default_encoding(tmp_path, monkeypatch):
  path = tmp_path / "denied.yaml"
  failure = PermissionError(13, "denied", str(path))
  calls = []

  def denied(source, *args, **kwargs):
    calls.append((source, args, kwargs))
    raise failure

  monkeypatch.setattr(Path, "read_text", denied)
  with pytest.raises(PermissionError) as raised:
    config_io.load_packaged_mapping(path, path.name)
  assert raised.value is failure
  assert calls == [(path, (), {})]


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


@pytest.mark.parametrize("read", [config_io.parse_template_yaml])
@pytest.mark.parametrize(
  ("text", "expected"),
  [
    ("", None),
    ("# empty\n", None),
    ("None", None),
    ("'None'", "None"),
    ("none", "none"),
    ("[null, ~, None, 'None', none]", [None, None, None, "None", "none"]),
    ("on: yes\n2: value\n", {True: True, 2: "value"}),
    ("base: &b {a: 1}\nvalue: {<<: *b, a: 2}\n", {"base": {"a": 1}, "value": {"a": 2}}),
    ("base: &b [1]\nvalue: *b\n", {"base": [1], "value": [1]}),
  ],
)
def test_guarded_yaml_facades_keep_native_values(read, text, expected):
  assert read(text) == expected


@pytest.mark.parametrize("read", [config_io.parse_template_yaml])
@pytest.mark.parametrize(
  ("text", "error"),
  [
    ("[broken", yaml.YAMLError),
    ("---\na: 1\n---\nb: 2\n", yaml.YAMLError),
    ("a: 1\na: 2\n", config_io.DuplicateKeyError),
    ("value: &loop [*loop]\n", config_io.CyclicAnchorError),
    ("value: " + "[" * 70 + "1" + "]" * 70, config_io.DocumentTooDeepError),
  ],
)
def test_guarded_yaml_facades_keep_refusals(read, text, error):
  with pytest.raises(error):
    read(text)


@pytest.mark.parametrize("read", [config_io.parse_template_yaml])
def test_guarded_yaml_facades_keep_nonfinite_values(read):
  import math

  value = read("[.nan, .inf, -.inf]")
  assert math.isnan(value[0])
  assert value[1:] == [math.inf, -math.inf]


@pytest.mark.parametrize(
  "value", [None, True, False, 0, 1, -1, 1.0, -0.0, 1.0e20, "None", "on", "雪", "${HOME}"]
)
def test_yamlplus_scalars_keep_owned_reader_types(value):
  from yapyon import yamlplus

  text = yamlplus.dumps(value)
  yamlplus.check(text)
  for read in (yamlplus.loads, config_io.parse_packaged, config_io.parse_doc_text):
    actual = read(text)
    assert type(actual) is type(value)
    assert actual == value
    if isinstance(value, float):
      assert actual.hex() == value.hex()


def test_yamlplus_mapping_preserves_exact_keys_order_and_literal_values():
  from yapyon import yamlplus

  old = """"null": [null, true, 1, 1.0, 'None', '{meta.box.home}']
"on": '${HOME}'
"None": '雪'
"1": false
"""
  text = """"null": [None, True, 1, 1.0, "None", "{meta.box.home}"]
"on": "${HOME}"
"None": "雪"
"1": False
"""
  expected = config_io.parse_packaged(old)
  yamlplus.check(text)
  for read in (yamlplus.loads, config_io.parse_packaged, config_io.parse_doc_text):
    actual = read(text)
    assert list(actual) == list(expected)
    assert all(type(key) is str for key in actual)
    assert actual == expected
    assert type(actual["null"]) is list
    assert [type(value) for value in actual["null"]] == [type(value) for value in expected["null"]]
    assert actual["1"] is False


def test_whole_manifest_yamlplus_codec_contract():
  from collections.abc import Mapping
  from importlib.resources import files

  from yapyon import yamlplus

  text = files("kanibako").joinpath("data/rom/settings/keyspace-manifest.yaml").read_text()
  yamlplus.check(text)

  def typed(value):
    if isinstance(value, Mapping):
      return ("mapping", [(typed(key), typed(child)) for key, child in value.items()])
    if isinstance(value, list):
      return (list, [typed(child) for child in value])
    return (type(value), value.hex() if type(value) is float else value)

  loader = yaml.SafeLoader(text)
  try:
    root = loader.get_single_node()
    assert isinstance(root, yaml.MappingNode) and root.value

    def node_contract(node):
      if isinstance(node, yaml.MappingNode):
        return (
          "mapping",
          [(node_contract(key), node_contract(child)) for key, child in node.value],
        )
      if isinstance(node, yaml.SequenceNode):
        return (list, [node_contract(child) for child in node.value])
      # Owned documents read plain None as null; quoted None remains text.
      value = (
        None if node.style is None and node.value == "None" else loader.construct_object(node)
      )
      return typed(value)

    expected = node_contract(root)
  finally:
    loader.dispose()
  for read in (config_io.parse_packaged, config_io.parse_doc_text, yamlplus.loads):
    assert typed(read(text)) == expected, read.__name__


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


def test_rooted_in_place_bytes_preserve_exact_content_and_shared_inode(tmp_path):
  root = tmp_path / "home"
  root.mkdir()
  path = root / ".gitignore"
  path.write_bytes(b"original contents\r\n")
  path.chmod(0o640)
  alias = root / "alias"
  alias.hardlink_to(path)
  before = path.stat()
  data = b"\xff\x00\r\n"
  assert config_io.overwrite_box_doc_bytes_in_existing_parent(path, root, data) == len(data)
  assert path.read_bytes() == alias.read_bytes() == data
  assert (path.stat().st_ino, path.stat().st_mode) == (before.st_ino, before.st_mode)
  assert config_io.overwrite_box_doc_bytes_in_existing_parent(path, root, b"") == 0
  assert alias.read_bytes() == b""


def test_rooted_in_place_bytes_create_leaf_but_not_parents_or_invalid_content(tmp_path):
  root = tmp_path / "home"
  root.mkdir()
  path = root / ".gitignore"
  assert config_io.overwrite_box_doc_bytes_in_existing_parent(path, root, b"new") == 3
  with pytest.raises(FileNotFoundError):
    config_io.overwrite_box_doc_bytes_in_existing_parent(root / "missing" / "f", root, b"x")
  assert not (root / "missing").exists()
  with pytest.raises(TypeError):
    config_io.overwrite_box_doc_bytes_in_existing_parent(path, root, "invalid")
  assert path.read_bytes() == b"new"


@pytest.mark.parametrize("link", ["leaf", "parent"])
def test_rooted_in_place_bytes_refuse_links_without_touching_host(tmp_path, link):
  from kanibako.boxfs import SymlinkRefusedError

  root = tmp_path / "home"
  root.mkdir()
  outside = tmp_path / "outside"
  outside.mkdir()
  sentinel = outside / ".gitignore"
  sentinel.write_bytes(b"keep\r\n")
  before = sentinel.stat()
  if link == "leaf":
    (root / "nested").mkdir()
    (root / "nested" / ".gitignore").symlink_to(sentinel)
  else:
    (root / "nested").symlink_to(outside)
  with pytest.raises(SymlinkRefusedError):
    config_io.overwrite_box_doc_bytes_in_existing_parent(
      root / "nested" / ".gitignore", root, b"bad"
    )
  assert sentinel.read_bytes() == b"keep\r\n"
  assert (sentinel.stat().st_ino, sentinel.stat().st_mode) == (before.st_ino, before.st_mode)
  with pytest.raises(ValueError):
    config_io.overwrite_box_doc_bytes_in_existing_parent(sentinel, root, b"bad")


def test_rooted_in_place_bytes_delegate_exact_root_and_errors(tmp_path, monkeypatch):
  root = tmp_path / "home"
  path = root / ".gitignore"
  data = b"\xff\r\n"
  seen = []
  error = PermissionError("injected rooted denial")

  def denied(*args):
    seen.append(args)
    raise error

  monkeypatch.setattr(config_io.boxfs, "write_bytes_in_place", denied)
  with pytest.raises(PermissionError) as raised:
    config_io.overwrite_box_doc_bytes_in_existing_parent(path, root, data)
  assert raised.value is error
  assert seen == [(path, root, data)]


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


@pytest.mark.parametrize("link", ["leaf", "parent", "hard"])
def test_host_byte_transport_keeps_raw_bytes_links_inode_and_mode(tmp_path, link):
  target = tmp_path / "config"
  target.write_bytes(b"\xff\x00before\r\nafter\rtail\n")
  target.chmod(0o640)
  path = tmp_path / "alias"
  if link == "leaf":
    path.symlink_to(target)
  elif link == "parent":
    path.symlink_to(tmp_path, target_is_directory=True)
    path = path / target.name
  else:
    path.hardlink_to(target)
  before = target.stat()
  assert config_io.read_doc_bytes(path) == target.read_bytes()
  replacement = b"\xfe\x00new\r\nshort\r\n"
  assert config_io.overwrite_doc_bytes_in_existing_parent(path, replacement) == len(replacement)
  assert target.read_bytes() == replacement
  assert config_io.read_doc_bytes(path) == replacement
  assert target.stat().st_ino == before.st_ino
  assert target.stat().st_mode == before.st_mode
  if link == "leaf":
    assert path.is_symlink()
  elif link == "parent":
    assert path.parent.is_symlink()
  else:
    assert path.stat().st_ino == before.st_ino


def test_host_byte_overwrite_creates_leaf_and_truncates_to_empty(tmp_path):
  path = tmp_path / "config"
  assert config_io.overwrite_doc_bytes_in_existing_parent(path, b"\xff\r\n") == 3
  assert path.read_bytes() == b"\xff\r\n"
  inode = path.stat().st_ino
  assert config_io.overwrite_doc_bytes_in_existing_parent(path, b"") == 0
  assert path.read_bytes() == b""
  assert path.stat().st_ino == inode


@pytest.mark.parametrize("link", ["leaf", "parent", "hard"])
def test_host_append_preserves_existing_bytes_links_inode_and_mode(tmp_path, link):
  target = tmp_path / "config"
  original = b"\xff\x00before\r\nafter\rtail"
  target.write_bytes(original)
  target.chmod(0o640)
  path = tmp_path / "alias"
  if link == "leaf":
    path.symlink_to(target)
  elif link == "parent":
    path.symlink_to(tmp_path, target_is_directory=True)
    path = path / target.name
  else:
    path.hardlink_to(target)
  before = target.stat()
  config_io.append_doc_text_in_existing_parent(path, "\nnew\r\nlast\r")
  assert target.read_bytes() == original + b"\nnew\r\nlast\r"
  assert target.stat().st_ino == before.st_ino
  assert target.stat().st_mode == before.st_mode
  if link == "leaf":
    assert path.is_symlink()
  elif link == "parent":
    assert path.parent.is_symlink()
  else:
    assert path.stat().st_ino == before.st_ino


def test_host_append_creates_leaf_and_uses_default_open(tmp_path, monkeypatch):
  import builtins

  path = tmp_path / "config"
  real_open = builtins.open
  calls = []

  def capture(*args, **kwargs):
    calls.append((args, kwargs))
    return real_open(*args, **kwargs)

  monkeypatch.setattr(config_io, "open", capture, raising=False)
  config_io.append_doc_text_in_existing_parent(path, "first\n")
  config_io.append_doc_text_in_existing_parent(path, "second\n")
  assert path.read_bytes() == b"first\nsecond\n"
  assert calls == [((path, "a"), {}), ((path, "a"), {})]


@pytest.mark.parametrize(
  ("operation", "args"),
  [
    (config_io.read_doc_bytes, ()),
    (config_io.overwrite_doc_bytes_in_existing_parent, (b"new",)),
    (config_io.append_doc_text_in_existing_parent, ("new",)),
    (config_io.unlink_doc_file, ()),
  ],
)
def test_host_transports_keep_missing_parent_and_directory_errors(tmp_path, operation, args):
  path = tmp_path / "absent" / "config"
  with pytest.raises(FileNotFoundError):
    operation(path, *args)
  assert not path.parent.exists()
  with pytest.raises(IsADirectoryError):
    operation(tmp_path, *args)


@pytest.mark.parametrize(
  ("operation", "args"),
  [
    (config_io.read_doc_bytes, ()),
    (config_io.overwrite_doc_bytes_in_existing_parent, (b"new",)),
    (config_io.append_doc_text_in_existing_parent, ("new",)),
    (config_io.unlink_doc_file, ()),
  ],
)
def test_host_transports_propagate_real_permission_errors(tmp_path, operation, args):
  import os

  if os.geteuid() == 0:
    pytest.skip("root bypasses filesystem permissions")
  parent = tmp_path / "locked"
  parent.mkdir()
  path = parent / "config"
  original = b"\xffkeep\r\n"
  path.write_bytes(original)
  parent.chmod(0)
  try:
    with pytest.raises(PermissionError) as raised:
      operation(path, *args)
    assert raised.value.filename == str(path)
  finally:
    parent.chmod(0o700)
  assert path.read_bytes() == original


def test_host_unlink_preserves_missing_ok_and_other_errors(tmp_path):
  path = tmp_path / "absent" / "config"
  with pytest.raises(FileNotFoundError):
    config_io.unlink_doc_file(path)
  with pytest.raises(FileNotFoundError):
    config_io.unlink_doc_file(path, missing_ok=False)
  config_io.unlink_doc_file(path, missing_ok=True)
  assert not path.parent.exists()
  with pytest.raises(IsADirectoryError):
    config_io.unlink_doc_file(tmp_path, missing_ok=True)


@pytest.mark.parametrize("link", ["leaf", "parent", "hard", "dangling"])
def test_host_unlink_removes_leaf_not_link_target(tmp_path, link):
  target = tmp_path / "config"
  target.write_bytes(b"\xffkeep\r\n")
  before = target.stat()
  path = tmp_path / "alias"
  if link == "leaf":
    path.symlink_to(target)
  elif link == "parent":
    path.symlink_to(tmp_path, target_is_directory=True)
    path = path / target.name
  elif link == "hard":
    path.hardlink_to(target)
  else:
    path.symlink_to(tmp_path / "missing")
  config_io.unlink_doc_file(path)
  assert not path.exists() and not path.is_symlink()
  if link == "parent":
    assert path.parent.is_symlink()
    assert not target.exists()
  else:
    assert target.read_bytes() == b"\xffkeep\r\n"
    assert target.stat().st_ino == before.st_ino
    assert target.stat().st_mode == before.st_mode


class TestReadDocTextCapped:
  """The capped host-text door: it reads cap+1 at most, so a caller can tell a
  whole file from an oversized one without slurping the rest."""

  def test_a_file_under_the_cap_comes_back_whole(self, tmp_path: Path) -> None:
    path = tmp_path / "small.txt"
    path.write_text("one line\n", encoding="utf-8")
    got = config_io.read_doc_text_capped(path, cap=64, encoding="utf-8")
    assert got == "one line\n"

  def test_a_file_exactly_at_the_cap_is_not_flagged_oversize(self, tmp_path: Path) -> None:
    path = tmp_path / "exact.txt"
    path.write_text("x" * 8, encoding="utf-8")
    got = config_io.read_doc_text_capped(path, cap=8, encoding="utf-8")
    assert len(got) == 8 and not len(got) > 8

  def test_an_oversized_file_returns_cap_plus_one_never_more(self, tmp_path: Path) -> None:
    path = tmp_path / "huge.txt"
    path.write_text("y" * 10_000, encoding="utf-8")
    got = config_io.read_doc_text_capped(path, cap=8, encoding="utf-8")
    assert len(got) == 9 and len(got) > 8

  def test_missing_file_raises_like_a_plain_read(self, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
      config_io.read_doc_text_capped(tmp_path / "gone.txt", cap=8, encoding="utf-8")

  def test_undecodable_bytes_raise_like_a_plain_read(self, tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(UnicodeDecodeError):
      config_io.read_doc_text_capped(path, cap=8, encoding="utf-8")


def test_owned_reader_uses_public_yapyon_and_detaches_containers(monkeypatch):
  import yapyon
  from yapyon import yamlplus

  text = 'base: 6\nresult: (base)\nrows: [{value: "{meta.box.home}"}, "{$HOME}"]\n'
  real_loads = yapyon.loads
  parsed = []

  def capture(document, **kwargs):
    assert document == text and kwargs == {"source": "settings.yaml"}
    value = real_loads(document, **kwargs)
    parsed.append(value)
    return value

  def forbidden(*args, **kwargs):
    pytest.fail("owned reader used a legacy or YamlPlus parser")

  monkeypatch.setattr(yapyon, "loads", capture)
  monkeypatch.setattr(yamlplus, "loads", forbidden)
  monkeypatch.setattr(yaml, "load", forbidden)
  actual = config_io.parse_doc_text(text, source="settings.yaml")
  assert actual == {"base": 6, "result": 6, "rows": [{"value": "{meta.box.home}"}, "{$HOME}"]}
  assert type(actual) is dict and type(actual["rows"]) is list
  assert type(actual["rows"][0]) is dict
  assert actual is not parsed[0] and actual["rows"] is not parsed[0]["rows"]
  actual["rows"][0]["value"] = "changed"
  assert parsed[0]["rows"][0]["value"] == "{meta.box.home}"


@pytest.mark.parametrize("text", ["", " \n\t\n", "# comment\n  # more\n"])
def test_owned_reader_empty_text_is_not_a_parse_failure(text):
  assert config_io.parse_doc_text(text) is None


@pytest.mark.parametrize("text", ["[broken", "a: 1\na: 2\n", '8080: "value"', "name: bare"])
def test_owned_reader_keeps_public_failure_without_fallback(monkeypatch, text):
  from yapyon import AkanError

  def forbidden(*args, **kwargs):
    pytest.fail("owned parse failure fell back to YAML")

  monkeypatch.setattr(yaml, "load", forbidden)
  with pytest.raises(AkanError) as caught:
    config_io.parse_doc_text(text, source="owned.cfg")
  assert "owned.cfg:" in str(caught.value)


@pytest.mark.parametrize("text", [b'"__STANDALONE__"', b'("__STANDALONE__")'])
def test_marker_reads_full_public_yapyon(monkeypatch, text):
  from yapyon import yamlplus

  def forbidden(*args, **kwargs):
    pytest.fail("marker used the output-profile reader")

  monkeypatch.setattr(yamlplus, "loads", forbidden)
  assert config_io.parse_standalone_marker(text) == "__STANDALONE__"


@pytest.mark.parametrize(
  "raw",
  [b"\xff", b"[broken", b"{}", b"False", b"0", b"None", b"", b"[" * 132 + b"1" + b"]" * 132],
)
def test_marker_invalid_or_nonstring_is_not_a_marker(raw):
  assert config_io.parse_standalone_marker(raw) is None


@pytest.mark.parametrize(
  "text,key",
  [
    ("box: {a: 1}\nbox: {b: 2}\n", "box"),
    ("box:\n  env:\n    A: '1'\n    A: '2'\n", "box.env.A"),
    ("self:\n  model: opus\n  model: sonnet\n", "self.model"),
    ("list:\n  - {k: 1}\n  - k: 1\n    k: 2\n", "list[1].k"),
    ("=: x\n=: y\n", "="),
    ("x:\n  <<:\n    a: 1\n    a: 2\n", "x.<<.a"),
    ("x:\n  <<:\n    - {b: 1}\n    - a: 1\n      a: 2\n", "x.<<[1].a"),
  ],
)
def test_native_templates_keep_duplicate_paths(text, key):
  with pytest.raises(config_io.DuplicateKeyError) as caught:
    config_io.parse_template_yaml(text)
  assert caught.value.dotted == key
  assert "line" in caught.value.first and "line" in caught.value.second


@pytest.mark.parametrize(
  "text,path",
  [
    ("box: &a {image: ok:1, x: *a}\n", "box.x"),
    ("box: &a {items: [*a]}\n", "box.items[0]"),
    ("c: &c\n  x: &d\n    y: *c\ne: &e\n  z: *d\n", "e.z.y.x"),
  ],
)
def test_native_templates_keep_cycle_paths(text, path):
  with pytest.raises(config_io.CyclicAnchorError) as caught:
    config_io.parse_template_yaml(text)
  assert caught.value.dotted == path


def test_native_templates_keep_shared_nodes_and_merge_overrides():
  value = config_io.parse_template_yaml("c: &c\n  x: &d\n    y: 1\ne: &e\n  z: *d\n")
  assert value["c"]["x"] is value["e"]["z"]
  assert config_io.parse_template_yaml("a:\n  =: x\n") == {"a": {"=": "x"}}
  assert config_io.parse_template_yaml("base: &b {a: 1}\nbox:\n  <<: *b\n  a: 2\n") == {
    "base": {"a": 1},
    "box": {"a": 2},
  }


def test_native_template_depth_boundary_and_parser_stack_failure(monkeypatch):
  def nested(depth):
    return (
      "\n".join("  " * i + f"k{i}:" for i in range(depth - 1))
      + "\n"
      + "  " * (depth - 1)
      + "leaf: 1\n"
    )

  value = config_io.parse_template_yaml(nested(config_io.MAX_DOC_DEPTH))
  for i in range(config_io.MAX_DOC_DEPTH - 1):
    value = value[f"k{i}"]
  assert value == {"leaf": 1}
  with pytest.raises(config_io.DocumentTooDeepError):
    config_io.parse_template_yaml(nested(config_io.MAX_DOC_DEPTH + 1))

  def exhausted(*args, **kwargs):
    raise RecursionError("simulated parser stack exhaustion")

  monkeypatch.setattr(yaml, "load", exhausted)
  with pytest.raises(config_io.DocumentTooDeepError) as caught:
    config_io.parse_template_yaml("{}")
  assert isinstance(caught.value.__cause__, RecursionError)


def test_native_shared_graph_guard_visits_distinct_containers():
  class CountingList(list):
    visits = 0

    def __iter__(self):
      self.visits += 1
      return super().__iter__()

  fanout, levels = 2, 3
  nodes = [CountingList(["seed"])]
  for _ in range(levels):
    nodes.append(CountingList([nodes[-1]] * fanout))
  config_io._guard_document({"top": nodes[-1]})
  assert len({id(node) for node in nodes if node.visits}) == levels + 1
  assert sum(node.visits for node in nodes) <= 1 + fanout * (len(nodes) - 1)

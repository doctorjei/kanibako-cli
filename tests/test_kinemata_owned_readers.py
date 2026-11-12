"""Owned tooling consumers use the runtime reader, without running checker engines."""

import re
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from yapyon import AkanError  # type: ignore[import-untyped]

import kinemata_views as views
from kanibako.settings.config_io import parse_doc_text, parse_native_yaml, read_doc_text


def test_real_owned_resources_and_delegating_views():
  core = views.core_defaults_file()
  descriptors = views.plugin_descriptors()
  sources = [core, *descriptors]
  owners = {source: views.owner_rows(source) for source in sources}
  assert all(owners.values())
  assert views.EntryOwners(rows="all").rows == [
    {"key": f"{source}:{where}", "owner": row["owner"]}
    for source, rows in owners.items()
    for where, row in rows
  ]
  core_rows = views.EntryOwners(rows="core").rows
  assert core_rows == [
    {"key": row["box_dest"], "owner": row["owner"]} for _, row in owners[core] if "box_dest" in row
  ]
  assert core_rows
  creds = views.AgentCredFiles()
  expected = []
  for source in descriptors:
    doc = parse_doc_text(read_doc_text(views._TREE / source), source=source)
    for row in doc["descriptor"].get("cred_files", []):
      expected.append({"key": row["host_rel"], "source": source})
  assert creds.rows == expected
  assert creds.declared(".credentials.json")
  synced = views.synced_cells()
  credential_owners = views.EntryOwners(rows="creds").rows
  assert credential_owners == [
    {"key": f"~/{row['home_rel']}", "owner": row["owner"]}
    for source in descriptors
    for where, row in owners[source]
    if where.startswith("descriptor.cred_files[") and f"~/{row['home_rel']}" in synced
  ]
  assert credential_owners


def test_owned_consumers_accept_yapyon_values(tmp_path: Path, monkeypatch):
  source = "plugin.yaml"
  path = tmp_path / source
  path.write_text(
    'role: "shared"\n'
    'credential: ".agent/token"\n'
    "descriptor:\n"
    "  cred_files:\n"
    "    - host_rel: (credential)\n"
    '      home_rel: ".agent/token"\n'
    "      owner: (role)\n"
    "nested:\n"
    '  - owner: "private"\n'
    '    value: "{meta.box.home}"\n',
    encoding="utf-8",
  )
  monkeypatch.setattr(views, "_TREE", tmp_path)
  monkeypatch.setattr(views, "plugin_descriptors", lambda: [source])
  monkeypatch.setattr(views, "core_defaults_file", lambda: source)
  with (
    patch("kanibako.settings.config_io.parse_packaged", side_effect=AssertionError("legacy")),
    patch("yaml.load", side_effect=AssertionError("native YAML")),
    patch("yapyon.yamlplus.loads", side_effect=AssertionError("output profile")),
  ):
    assert views.owner_rows(source) == [
      (
        "descriptor.cred_files[0]",
        {
          "host_rel": ".agent/token",
          "home_rel": ".agent/token",
          "owner": "shared",
        },
      ),
      ("nested[0]", {"owner": "private", "value": "{meta.box.home}"}),
    ]
    assert views.AgentCredFiles().rows == [{"key": ".agent/token", "source": source}]
    assert views.EntryOwners(rows="all").rows


@pytest.mark.parametrize("consumer", ["owners", "creds", "entry"])
@pytest.mark.parametrize("text", ["[broken", "owner: bare", "owner: 1\nowner: 2\n"])
def test_owned_failures_name_source(tmp_path: Path, monkeypatch, consumer, text):
  source = "broken.yaml"
  path = tmp_path / source
  path.write_text(text, encoding="utf-8")
  monkeypatch.setattr(views, "_TREE", tmp_path)
  monkeypatch.setattr(views, "plugin_descriptors", lambda: [source])
  monkeypatch.setattr(views, "core_defaults_file", lambda: source)
  with pytest.raises(AkanError) as caught:
    if consumer == "owners":
      views.owner_rows(source)
    elif consumer == "creds":
      views.AgentCredFiles()
    else:
      views.EntryOwners(rows="all")
  assert f"{path}:" in str(caught.value)


@pytest.mark.parametrize("consumer", ["owners", "creds"])
@pytest.mark.parametrize("content", [None, b"\xff", b"# empty\r\n"])
def test_owned_transport_and_empty_documents(tmp_path: Path, monkeypatch, consumer, content):
  source = "transport.yaml"
  path = tmp_path / source
  if content is not None:
    path.write_bytes(content)
  monkeypatch.setattr(views, "_TREE", tmp_path)
  monkeypatch.setattr(views, "plugin_descriptors", lambda: [source])
  error = FileNotFoundError if content is None else UnicodeDecodeError
  if content == b"# empty\r\n":
    result = views.owner_rows(source) if consumer == "owners" else views.AgentCredFiles().rows
    assert result == []
  else:
    with pytest.raises(error):
      if consumer == "owners":
        views.owner_rows(source)
      else:
        views.AgentCredFiles()


@pytest.fixture
def cred_owner_oracle(tmp_path: Path, monkeypatch):
  config = tomllib.loads(
    (Path(__file__).resolve().parents[1] / "kinemata.toml").read_text(encoding="utf-8")
  )
  oracles = [row for row in config["parity"] if row["registry"] == "cred-entry-owners"]
  assert len(oracles) == 1
  oracle = oracles[0]
  assert oracle["command"][:2] == ["{python}", "-c"]
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(views, "plugin_descriptors", lambda: ["plugin.yaml", "other.yaml"])
  monkeypatch.setattr(
    views,
    "synced_cells",
    lambda: {
      "~/.agent/token": {"owner": "agent"},
      "~/.other/token": {"owner": "workset"},
      "~/.unowned/token": {},
      "~/.null/token": {"owner": None},
      "~/.absent/token": {"owner": "box"},
    },
  )
  (tmp_path / "other.yaml").write_text("descriptor: {}\n", encoding="utf-8")
  with (
    patch("kanibako.settings.config_io.parse_packaged", side_effect=AssertionError("legacy")),
    patch("yaml.load", side_effect=AssertionError("native YAML")),
    patch("yapyon.yamlplus.loads", side_effect=AssertionError("output profile")),
    patch.object(views, "owner_rows", side_effect=AssertionError("declaration traversal")),
    patch.object(views, "EntryOwners", side_effect=AssertionError("declaration view")),
  ):
    yield oracle


def test_cred_owner_oracle_yapyon_and_manifest_authority(
  tmp_path: Path, cred_owner_oracle, capsys
):
  (tmp_path / "plugin.yaml").write_text(
    'credential: ".agent/token"\n'
    "descriptor:\n"
    "  cred_files:\n"
    "    - home_rel: (credential)\n"
    '      owner: "not-the-manifest-owner"\n'
    '    - home_rel: ".unowned/token"\n'
    '    - home_rel: ".null/token"\n'
    '    - home_rel: ".unknown/token"\n',
    encoding="utf-8",
  )
  (tmp_path / "other.yaml").write_text(
    'descriptor:\n  cred_files:\n    - home_rel: ".other/token"\n', encoding="utf-8"
  )
  with (
    patch("kanibako.settings.config_io.read_doc_text", wraps=read_doc_text) as reader,
    patch("kanibako.settings.config_io.parse_doc_text", wraps=parse_doc_text) as parser,
  ):
    exec(compile(cred_owner_oracle["command"][-1], "cred-entry-owners-oracle", "exec"), {})
  assert [call.args[0] for call in reader.call_args_list] == [
    Path("plugin.yaml"),
    Path("other.yaml"),
  ]
  assert [call.kwargs for call in parser.call_args_list] == [
    {"source": "plugin.yaml"},
    {"source": "other.yaml"},
  ]
  output = capsys.readouterr().out
  assert output == (
    "kanibako-entry-owner: ~/.agent/token=agent\nkanibako-entry-owner: ~/.other/token=workset\n"
  )
  assert re.findall(cred_owner_oracle["extract"], output) == [
    ("~/.agent/token", "agent"),
    ("~/.other/token", "workset"),
  ]
  assert cred_owner_oracle["field"] == "owner"
  assert cred_owner_oracle["authority"] == "produced"


@pytest.mark.parametrize("text", ["[broken", "owner: bare", "owner: 1\nowner: 2\n"])
def test_cred_owner_oracle_failures_name_source(tmp_path: Path, cred_owner_oracle, text):
  (tmp_path / "plugin.yaml").write_text(text, encoding="utf-8")
  with pytest.raises(AkanError) as caught:
    exec(compile(cred_owner_oracle["command"][-1], "cred-entry-owners-oracle", "exec"), {})
  assert "plugin.yaml:" in str(caught.value)


@pytest.mark.parametrize("content", [None, b"\xff"])
def test_cred_owner_oracle_transport_failures(tmp_path: Path, cred_owner_oracle, content):
  if content is not None:
    (tmp_path / "plugin.yaml").write_bytes(content)
  error = FileNotFoundError if content is None else UnicodeDecodeError
  with pytest.raises(error):
    exec(compile(cred_owner_oracle["command"][-1], "cred-entry-owners-oracle", "exec"), {})


def test_native_yaml_keeps_workflow_scalar_semantics():
  assert parse_native_yaml("on: [push]\nenabled: yes\nvalue: None\n") == {
    True: ["push"],
    "enabled": True,
    "value": "None",
  }

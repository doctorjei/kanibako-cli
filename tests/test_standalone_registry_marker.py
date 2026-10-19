"""A ``registry.yaml`` holding only ``__STANDALONE__`` DEFINES a standalone root.

``system-design-1.8.0.md`` § Detection & import and the keyspec's
``meta.workset.registry`` row: the registry is fixed at
``{meta.workset.path}/registry.yaml`` and no file sets it; create writes the mark
there, and detection reads THAT root's own file (raw text, trimmed, case-blind).  A
stored ``workset.registry`` — the retired key, and the old ``registry: null`` marker —
refuses, naming the cure.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.errors import ConfigError
from kanibako.launch import box_resolve
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.paths import STANDALONE_META_DIR
from kanibako.settings.settings_resolve import SettingsError

MARK = "__STANDALONE__\n"
MIGRATION_HEADING = "The workset registry is a fixed file, and a standalone box is marked in it"


def _cli(argv: list[str]) -> int:
  from kanibako.cli import main

  try:
    main(argv)
  except SystemExit as exc:
    return int(exc.code or 0)
  return 0


def _create_standalone(tmp_home: Path) -> Path:
  root = tmp_home / "home" / "sa"
  root.mkdir()
  assert _cli(["create", str(root), "--standalone", "--no-vault"]) == 0
  return root


def _info(root: Path, capsys) -> tuple[int, str]:
  capsys.readouterr()
  rc = _cli(["box", "info", str(root)])
  out = capsys.readouterr()
  return rc, out.out + out.err


class TestCreateWritesTheMark:
  def test_registry_file_holds_only_the_mark(self, config_file, tmp_home, credentials_dir):
    root = _create_standalone(tmp_home)
    assert (root / "registry.yaml").read_text() == MARK
    doc = yaml.safe_load((root / WORKSET_META_FILE).read_text())
    assert doc["workset"]["kuid"]
    assert "registry" not in doc["workset"]

  def test_create_refuses_over_a_workset_registry(self, config_file, tmp_home, credentials_dir):
    root = tmp_home / "home" / "sa"
    root.mkdir()
    members = "boxes:\n  a: /x\n"
    (root / "registry.yaml").write_text(members)
    assert _cli(["create", str(root), "--standalone", "--no-vault"]) != 0
    assert (root / "registry.yaml").read_text() == members


class TestDetection:
  def test_created_root_is_standalone(self, config_file, tmp_home, credentials_dir, capsys):
    root = _create_standalone(tmp_home)
    assert box_resolve.is_standalone_root(root) is True
    assert "Mode:         standalone" in _info(root, capsys)[1]

  def test_removing_the_mark_reads_not_standalone(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    root = _create_standalone(tmp_home)
    (root / "registry.yaml").unlink()
    assert (root / STANDALONE_META_DIR).is_dir()
    assert box_resolve.is_standalone_root(root) is False
    assert "Mode:         standalone" not in _info(root, capsys)[1]

  @pytest.mark.parametrize(
    "text", [MARK, "__standalone__", "  __Standalone__ \n\n", "\t__STANDALONE__"]
  )
  def test_the_mark_is_trimmed_and_case_blind(self, tmp_path, text):
    (tmp_path / "registry.yaml").write_text(text)
    assert box_resolve.is_standalone_root(tmp_path) is True

  @pytest.mark.parametrize(
    "text",
    ["", "boxes: {}\n", "__STANDALONE__\nboxes: {}\n", "# __STANDALONE__\n", "__STANDALONE"],
  )
  def test_anything_else_is_not_the_mark(self, tmp_path, text):
    (tmp_path / "registry.yaml").write_text(text)
    assert box_resolve.is_standalone_root(tmp_path) is False

  def test_box_data_alone_is_not_the_marker(self, tmp_path):
    (tmp_path / STANDALONE_META_DIR).mkdir()
    (tmp_path / WORKSET_META_FILE).write_text("workset:\n  kuid: abcde\n")
    assert box_resolve.is_standalone_root(tmp_path) is False

  def test_a_containing_roots_mark_never_counts(self, tmp_path):
    (tmp_path / "registry.yaml").write_text(MARK)
    (tmp_path / "child").mkdir()
    assert box_resolve.is_standalone_root(tmp_path / "child") is False

  def test_malformed_settings_file_is_refused_only_when_strict(self, tmp_path):
    (tmp_path / "registry.yaml").write_text(MARK)
    (tmp_path / WORKSET_META_FILE).write_text("workset:\n  canon: @x\n")
    assert box_resolve.is_standalone_root(tmp_path) is True
    with pytest.raises(ConfigError, match="is not valid YAML"):
      box_resolve.is_standalone_root(tmp_path, strict=True)


@pytest.mark.writes_undeclared(
  "workset.registry",
  reason="the retired key is parsed into the store before the refusal names it; the "
  "refusal and its cure are what these tests run.",
)
class TestStoredRegistryKeyRefuses:
  """The migration guard: a stored ``workset.registry`` refuses, naming the cure."""

  def test_the_old_null_marker_refuses_with_the_mark_as_cure(self, tmp_path):
    (tmp_path / WORKSET_META_FILE).write_text("workset:\n  kuid: abcde\n  registry: null\n")
    with pytest.raises(SettingsError) as exc:
      box_resolve.is_standalone_root(tmp_path)
    message = str(exc.value)
    assert "'workset.registry' is RETIRED" in message
    assert str(tmp_path / "registry.yaml") in message
    assert "__STANDALONE__" in message
    assert MIGRATION_HEADING in message
    assert not (tmp_path / "registry.yaml").exists()

  def test_a_path_value_refuses_naming_the_fixed_file(self, tmp_path):
    (tmp_path / WORKSET_META_FILE).write_text("workset:\n  registry: /srv/reg.yaml\n")
    with pytest.raises(SettingsError) as exc:
      box_resolve.is_standalone_root(tmp_path)
    message = str(exc.value)
    assert "meta.workset.registry" in message
    assert "remove that line" in message
    assert "__STANDALONE__" not in message

  def test_an_old_standalone_box_refuses_at_box_info(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    root = _create_standalone(tmp_home)
    (root / "registry.yaml").unlink()
    doc = yaml.safe_load((root / WORKSET_META_FILE).read_text())
    doc["workset"]["registry"] = None
    (root / WORKSET_META_FILE).write_text(yaml.safe_dump(doc))
    rc, out = _info(root, capsys)
    assert rc == 1, out
    assert "'workset.registry' is RETIRED" in out
    assert "__STANDALONE__" in out
    assert not (root / "registry.yaml").exists()

  def test_a_system_file_entry_refuses_at_the_resolve(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    root = tmp_home / "home" / "pb"
    root.mkdir()
    assert _cli(["create", str(root)]) == 0
    std = load_std_paths(load_config(config_file))
    std.settings.parent.mkdir(parents=True, exist_ok=True)
    std.settings.write_text("workset:\n  registry: /srv/reg.yaml\n")
    assert box_resolve.is_standalone_root(root) is False
    rc, out = _info(root, capsys)
    assert rc == 1, out
    assert "'workset.registry' is RETIRED" in out
    assert str(std.settings) in out

  def test_set_refuses_the_retired_key_and_writes_nothing(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    root = tmp_home / "home" / "ws2"
    assert _cli(["workset", "create", str(root), "--name", "ws2"]) == 0
    settings = root / WORKSET_META_FILE
    before = settings.read_text() if settings.exists() else None
    capsys.readouterr()
    assert _cli(["workset", "set", "ws2", "workset.registry", "--null", "--force"]) == 1
    assert "workset.registry" in capsys.readouterr().err
    assert (settings.read_text() if settings.exists() else None) == before
    assert box_resolve.is_standalone_root(root) is False


class TestMalformedRootFileDetection:
  """A malformed ``workset.yaml`` is refused only for a dir no registry claims."""

  def _detect(self, config_file, target: Path):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import detect_project_mode, load_std_paths

    config = load_config(config_file)
    return detect_project_mode(target, load_std_paths(config), config)

  def test_registered_primary_box_still_resolves(self, config_file, tmp_home, credentials_dir):
    from kanibako.settings.paths import BoxMode

    root = tmp_home / "home" / "pb"
    root.mkdir()
    assert _cli(["create", str(root)]) == 0
    (root / WORKSET_META_FILE).write_text("bad: @x\n")
    result = self._detect(config_file, root)
    assert (result.mode, result.project_root) == (BoxMode.primary, root)

  def test_unclaimed_dir_is_refused(self, config_file, tmp_home, credentials_dir):
    root = tmp_home / "home" / "loose"
    root.mkdir()
    (root / WORKSET_META_FILE).write_text("bad: @x\n")
    with pytest.raises(ConfigError, match="is not valid YAML"):
      self._detect(config_file, root)

  def test_malformed_ancestor_file_stays_lenient(self, config_file, tmp_home, credentials_dir):
    from kanibako.settings.paths import BoxMode

    parent = tmp_home / "home" / "up"
    (parent / "child").mkdir(parents=True)
    (parent / WORKSET_META_FILE).write_text("bad: @x\n")
    result = self._detect(config_file, parent / "child")
    assert (result.mode, result.project_root) == (BoxMode.primary, parent / "child")

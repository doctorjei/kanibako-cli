"""Workset-identity refusals reach every verb, though detection loads no workset.

Detection names the path space from the registries alone (system-design § *Box
designation*), so ``_load_workset``'s identity refusals no longer fire there.  Each
resolver that follows re-raises them, cure and all, instead of reporting a miss.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.errors import (
  LegacyRegistryIdentityError,
  LegacyWorksetIdentityError,
  ReservedWorksetNameError,
)
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths

RETIRED = "'workset.meta' is a RETIRED location"


def _cli(argv: list[str]) -> int:
  from kanibako.cli import main

  try:
    main(argv)
  except SystemExit as exc:
    return int(exc.code or 0)
  return 0


def _member(root: Path, std) -> Path:
  from kanibako.project import workset_registry

  member = root / "workspaces" / "m"
  member.mkdir(parents=True, exist_ok=True)
  workset_registry.register_workset_box(workset_registry.workset_registry_path(root), "m", member)
  return member


def _legacy_identity(config_file, tmp_home):
  """A named workset whose ``workset.yaml`` still holds the retired identity table."""
  from kanibako.project.workset import create_workset
  from kanibako.settings.config_io import dump_doc

  config = load_config(config_file)
  std = load_std_paths(config)
  ws = create_workset("team", tmp_home / "team", std)
  member = _member(ws.root, std)
  dump_doc(ws.root / "workset.yaml", {"workset": {"meta": {"name": "team"}}})
  return config, std, member


def _reserved_key(config_file, tmp_home, key="Default"):
  """A workset tree registered under a reserved key, as a pre-1.8 registry holds it."""
  from kanibako.project import registry_store

  config = load_config(config_file)
  std = load_std_paths(config)
  root = tmp_home / "oldws"
  for leaf in ("workspaces", "boxes", "vault", "auth"):
    (root / leaf).mkdir(parents=True, exist_ok=True)
  section = registry_store.load_section(std.registry, "worksets")
  section[key] = str(root)
  registry_store.save_section(std.registry, "worksets", section)
  return config, std, _member(root, std)


class TestTheCureLandsOnEveryResolver:
  def test_resolve_any_project_verbs_name_the_retired_table(
    self, config_file, tmp_home, monkeypatch, capsys
  ):
    """``box info`` (resolve_any_project): the cure, not "No workset found"."""
    _config, _std, member = _legacy_identity(config_file, tmp_home)
    monkeypatch.chdir(tmp_home)
    assert _cli(["box", "info", str(member)]) == 1
    err = capsys.readouterr().err
    assert RETIRED in err
    assert "No workset found" not in err

  def test_resolve_lifecycle_target_names_the_reserved_key(self, config_file, tmp_home):
    """``box move``/``remap``/``convert`` (resolve_lifecycle_target)."""
    from kanibako.commands.box._lifecycle import resolve_lifecycle_target

    config, std, member = _reserved_key(config_file, tmp_home)
    with pytest.raises(ReservedWorksetNameError, match="'Default' is registered under a reserved"):
      resolve_lifecycle_target(str(member), std, config)

  def test_resolve_box_identity_names_the_retired_table(self, config_file, tmp_home):
    """archive, diagnose, and resolve_workset_project (resolve_box_identity)."""
    from kanibako.launch import box_resolve

    config, std, member = _legacy_identity(config_file, tmp_home)
    with pytest.raises(LegacyWorksetIdentityError, match=RETIRED):
      box_resolve.resolve_box_identity(member, std, config)

  def test_box_rm_by_path_names_the_retired_table(
    self, config_file, tmp_home, monkeypatch, capsys
  ):
    """``box rm <path>``: the cure, not "is not a registered box"."""
    _config, _std, member = _legacy_identity(config_file, tmp_home)
    monkeypatch.chdir(tmp_home)
    assert _cli(["box", "rm", str(member)]) == 1
    err = capsys.readouterr().err
    assert RETIRED in err
    assert "is not a registered box" not in err

  def test_duplicate_into_a_workset_names_the_reserved_key(
    self, config_file, tmp_home, monkeypatch, capsys
  ):
    """``box duplicate --to named --workset``: the cure, not "already a workset project"."""
    from kanibako.project.workset import create_workset

    _config, std, member = _reserved_key(config_file, tmp_home)
    create_workset("dest", tmp_home / "dest", std)
    monkeypatch.chdir(tmp_home)
    argv = ["box", "duplicate", str(member), str(tmp_home / "dup"), "--to", "named"]
    assert _cli([*argv, "--workset", "dest"]) == 1
    err = capsys.readouterr().err
    assert "'Default' is registered under a reserved name" in err
    assert "already a workset project" not in err


class TestAnUnreadableRegistryStopsOnlyItsOwnPaths:
  """A retired ``registry.yaml`` refuses a path under its root, and no other lookup."""

  def _setup(self, config_file, tmp_home):
    from kanibako.project.workset import create_workset
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import dump_doc

    config = load_config(config_file)
    std = load_std_paths(config)
    bad = create_workset("bad", tmp_home / "bad", std)
    reg = workset_registry.workset_registry_path(bad.root)
    dump_doc(reg, {"boxes": {}, "workset": {"name": "bad"}})
    good = create_workset("good", tmp_home / "good", std)
    return config, std, bad.root, _member(good.root, std)

  def test_another_worksets_path_still_finds_its_space(self, config_file, tmp_home):
    from kanibako.launch import box_resolve
    from kanibako.project.workset import list_worksets

    _config, std, _bad, member = self._setup(config_file, tmp_home)
    found = [name for name, _r, _b in box_resolve.path_space_worksets(list_worksets(std), member)]
    assert found == ["good"]

  def test_a_bare_primary_name_resolves_from_outside_both(self, config_file, tmp_home):
    from kanibako.project.names import resolve_name
    from kanibako.settings.paths import resolve_project

    config, std, _bad, _member_path = self._setup(config_file, tmp_home)
    pdir = tmp_home / "solo"
    pdir.mkdir()
    resolve_project(std, config, project_dir=str(pdir), initialize=True)
    path, kind = resolve_name(
      std.registry,
      "solo",
      cwd=tmp_home,
      primary_workset=std.primary_workset,
      early_system=std.early_system,
    )
    assert (path, kind) == (str(pdir), "project")

  def test_a_path_under_its_root_still_refuses(self, config_file, tmp_home):
    from kanibako.launch import box_resolve
    from kanibako.project.workset import list_worksets

    _config, std, bad, _member_path = self._setup(config_file, tmp_home)
    with pytest.raises(LegacyRegistryIdentityError):
      list(box_resolve.path_space_worksets(list_worksets(std), bad / "sub"))

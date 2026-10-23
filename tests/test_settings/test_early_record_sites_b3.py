"""The production sites in ``start`` and ``workset`` hand their early readers ``std``'s
record, under the partition name the read is for.

``early_reads`` is :mod:`test_early_record_sites`'s spy: a read of the system tier with no
scope raises.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kanibako.channels.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE
from kanibako.commands import start
from kanibako.project import workset
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.paths import BoxMode, STANDALONE_META_DIR


@pytest.fixture
def _early_log(monkeypatch) -> tuple[list[str], list[tuple[Path, str]]]:
  """Every early read's scope name, and its ``(workset root, scope name)``; a read with no
  scope raises."""
  from kanibako.settings import workset_dirkeys

  real = workset_dirkeys.early_repoint
  names: list[str] = []
  pairs: list[tuple[Path, str]] = []

  def spy(workset_root, workset_settings, key, *, early=None):
    if early is None:
      raise AssertionError(f"workset.{key} under {workset_root} was read with no record")
    names.append(early.workset_name)
    pairs.append((Path(workset_root), early.workset_name))
    return real(workset_root, workset_settings, key, early=early)

  for module in list(sys.modules.values()):
    if getattr(module, "early_repoint", None) is real:
      monkeypatch.setattr(module, "early_repoint", spy)
  return names, pairs


@pytest.fixture
def early_reads(_early_log) -> list[str]:
  """The scope names every early read is made under."""
  return _early_log[0]


@pytest.fixture
def early_read_pairs(_early_log) -> list[tuple[Path, str]]:
  """``(workset root, scope name)`` for every early read."""
  return _early_log[1]


def _team(std, tmp_home: Path):
  """A NAMED workset ``team``."""
  return workset.create_workset("team", tmp_home / "wsroot", std)


class TestWorksetSites:
  def test_create_reads_its_own_name_and_the_default_under_primary(
    self, std, tmp_home, early_reads
  ):
    _team(std, tmp_home)
    # The logs-share check walks the default workset under the token its boxes read.
    assert set(early_reads) == {"team", WS_TOKEN_PRIMARY}

  def test_connect_and_release_a_member(self, std, tmp_home, early_read_pairs):
    ws = _team(std, tmp_home)
    source = tmp_home / "ext-src"
    source.mkdir()
    early_read_pairs.clear()
    workset.add_project(ws, "ext", source, std)
    workset.release_project(ws, "ext")
    # The vault lay resolves the system tier, which reads the default workset's keys:
    # each workset is read under its own name.
    token_of = {ws.root: "team", std.primary_workset: WS_TOKEN_PRIMARY}
    assert (ws.root, "team") in early_read_pairs
    assert all(token_of.get(root) == name for root, name in early_read_pairs), early_read_pairs


class TestStartSites:
  def test_the_null_workspace_check_of_a_named_member(self, std, tmp_home, early_reads):
    ws = _team(std, tmp_home)
    source = tmp_home / "app-src"
    source.mkdir()
    workset.add_project(ws, "app", source, std)
    proj = MagicMock(mode=BoxMode.named, group=ws, project_path=source)
    proj.name = "app"
    early_reads.clear()
    start._refuse_null_workspace_bind(std, proj)
    assert early_reads == ["team"]

  def test_the_null_workspace_check_of_a_standalone_box(self, std, tmp_home, early_reads):
    root = tmp_home / "lone"
    (root / STANDALONE_META_DIR).mkdir(parents=True)
    (root / WORKSET_META_FILE).write_text("box: {}\n")
    proj = MagicMock(mode=BoxMode.standalone, group=None, metadata_path=root, project_path=root)
    proj.name = "lone"
    start._refuse_null_workspace_bind(std, proj)
    assert early_reads == [WS_TOKEN_STANDALONE]

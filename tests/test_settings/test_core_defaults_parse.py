"""``core_defaults`` parses ``core-defaults.yaml`` once per process and copies out per call (P8)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from kanibako.settings import core_defaults
from kanibako.settings.config import load_merged_config
from tests.support.filenames import CONFIG_FILENAME


@pytest.fixture
def cold_parse():
  """A cold parse cache on both sides, so no test inherits or leaves a parse."""
  core_defaults._parse_doc.cache_clear()
  yield
  core_defaults._parse_doc.cache_clear()


def test_repeated_resolves_parse_the_shipped_file_once(cold_parse, tmp_path, monkeypatch):
  """Three resolves, each reading the core defaults, parse the file ONCE between them.

  MUTATION: bypass the cache (``_parse_doc.__wrapped__()``) and the count is 3.
  """
  calls: list[str] = []

  def counting_safe_load(text: str) -> object:
    calls.append(text)
    return yaml.safe_load(text)

  monkeypatch.setattr(core_defaults, "yaml", SimpleNamespace(safe_load=counting_safe_load))
  for _ in range(3):
    load_merged_config(tmp_path / CONFIG_FILENAME, None)
  assert len(calls) == 1


def test_mutating_a_returned_doc_does_not_reach_the_next(cold_parse):
  """A caller that clobbers a NESTED table of its copy leaves the next reader's intact."""
  first = core_defaults._load_doc()
  assert core_defaults._load_doc() is not first
  row = first["core"][0]
  original = dict(row)
  row["box_dest"] = "/clobbered"
  first["core"].clear()
  first["agent_default"] = {}

  second = core_defaults._load_doc()
  assert second["core"][0] == original
  assert second["agent_default"]
  assert core_defaults._parse_doc()["core"][0] == original

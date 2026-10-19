"""Every registry writer stores a path as :func:`kanibako.utils.literal_path`.

Registry entries are data, never keystore references: each writer seam normalizes the
value it stores, and a value already literal is stored unchanged (round-trips stay put).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.project import names, registry_store, workset_registry
from kanibako.settings.paths import load_primary_boxes, register_primary_box_name


@pytest.fixture
def reg(tmp_path: Path) -> Path:
  return tmp_path / "global" / "registry.yaml"


def test_workset_box_stored_normalized(tmp_path: Path) -> None:
  ws_reg = tmp_path / "ws" / "registry.yaml"
  workset_registry.register_workset_box(ws_reg, "alpha", Path("/abs/a/../alpha"))
  assert workset_registry.load_workset_boxes(ws_reg) == {"alpha": "/abs/alpha"}


def test_primary_box_stored_normalized(tmp_path: Path) -> None:
  primary = tmp_path / "primary"
  primary.mkdir()
  register_primary_box_name(primary, "alpha", "/abs/a/../alpha")
  assert load_primary_boxes(primary) == {"alpha": "/abs/alpha"}


def test_standalone_stored_normalized(reg: Path) -> None:
  registry_store.register_standalone(reg, "solo", Path("/abs/a/../solo"))
  assert registry_store.load_standalone(reg) == {"solo": "/abs/solo"}


def test_deregistered_stored_normalized(reg: Path) -> None:
  registry_store.register_deregistered(
    reg, "gone", kind="primary", workspace="/abs/a/../w", metadata="/abs/b/../m"
  )
  entry = registry_store.load_deregistered(reg)["gone"]
  assert (entry["workspace"], entry["metadata"]) == ("/abs/w", "/abs/m")


def test_workset_name_stored_normalized(reg: Path) -> None:
  names.register_name(reg, "team", "/abs/a/../team")
  assert names.read_names(reg)["worksets"] == {"team": "/abs/team"}


def test_literal_values_round_trip_unchanged(tmp_path: Path, reg: Path) -> None:
  """A stored literal path copied back verbatim (readopt, park, rollback) is unchanged."""
  ws_reg = tmp_path / "ws" / "registry.yaml"
  workset_registry.register_workset_box(ws_reg, "alpha", Path("/abs/alpha"))
  stored = workset_registry.load_workset_boxes(ws_reg)["alpha"]
  workset_registry.register_workset_box(ws_reg, "alpha", Path(stored))
  assert workset_registry.load_workset_boxes(ws_reg) == {"alpha": "/abs/alpha"}
  registry_store.register_deregistered(
    reg, "gone", kind="standalone", workspace=stored, metadata=stored
  )
  parked = registry_store.load_deregistered(reg)["gone"]
  registry_store.register_standalone(reg, "gone", Path(str(parked["metadata"])))
  assert registry_store.load_standalone(reg) == {"gone": "/abs/alpha"}


def test_workset_name_reentry_matches_unnormalized_spelling(reg: Path) -> None:
  """Recovery re-entry with the caller's spelling is still the SAME mapping."""
  names.register_name(reg, "team", "/abs/a/../team")
  names.register_name_if_absent(reg, "team", "/abs/a/../team")
  assert names.read_names(reg)["worksets"] == {"team": "/abs/team"}


def test_membership_undo_drops_row_registered_unnormalized(tmp_path: Path) -> None:
  from kanibako.settings.paths import _workset_box_membership_undo

  primary = tmp_path / "primary"
  primary.mkdir()
  undo = _workset_box_membership_undo(primary, "alpha", Path("/abs/a/../alpha"))
  register_primary_box_name(primary, "alpha", "/abs/a/../alpha")
  undo()
  assert load_primary_boxes(primary) == {}

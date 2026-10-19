"""Tests for kanibako.project.workset_registry (the per-workset box-membership store).

Every membership function takes the per-workset registry FILE path
(``meta.workset.registry`` == ``<workset_root>/registry.yaml``, fixed). These
tests build that path explicitly from ``tmp_path``.  ADDITIVE infra (P3): no
existing flow consumes this module yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.project import workset_registry
from kanibako.settings.config_io import dump_doc, load_doc


@pytest.fixture
def reg(tmp_path: Path) -> Path:
  """The resolved per-workset ``registry.yaml`` path under a fresh workset."""
  return tmp_path / "workset" / "registry.yaml"


# ---------------------------------------------------------------------------
# Membership API — the ``boxes:`` section
# ---------------------------------------------------------------------------


def test_absent_file_is_empty_membership(reg: Path) -> None:
  """Absent registry file → empty membership (no scaffolding)."""
  assert workset_registry.load_workset_boxes(reg) == {}
  assert workset_registry.workset_box_path(reg, "mybox") is None
  # Reading does not create the file.
  assert not reg.exists()


def test_present_but_null_boxes_section_is_empty(reg: Path) -> None:
  """A registry file with a PRESENT-but-NULL ``boxes:`` section → ``{}``.

  Regression: yaml ``boxes:\\n`` makes the key present with value ``None``, so
  the ``dict.get(..., {})`` default never fires — the ``or {}`` guard coerces
  it to empty.  Mutation guard: dropping ``or {}`` makes this RED (``dict(None)``
  raises ``TypeError``).
  """
  reg.parent.mkdir(parents=True, exist_ok=True)
  reg.write_text("boxes:\n")
  assert workset_registry.load_workset_boxes(reg) == {}
  assert workset_registry.workset_box_path(reg, "mybox") is None
  # A register onto the null section still works (no crash) and preserves it.
  workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
  assert workset_registry.load_workset_boxes(reg) == {"alpha": "/abs/alpha"}


def test_register_and_load_round_trip(reg: Path) -> None:
  """Register two boxes → both come back; names/lookup agree."""
  workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
  workset_registry.register_workset_box(reg, "beta", Path("/abs/beta"))
  assert workset_registry.load_workset_boxes(reg) == {
    "alpha": "/abs/alpha",
    "beta": "/abs/beta",
  }
  assert workset_registry.workset_box_path(reg, "alpha") == "/abs/alpha"
  assert workset_registry.workset_box_path(reg, "beta") == "/abs/beta"


def test_register_replaces_path(reg: Path) -> None:
  """Re-registering a name overwrites the stored path (a moved box)."""
  workset_registry.register_workset_box(reg, "alpha", Path("/old"))
  workset_registry.register_workset_box(reg, "alpha", Path("/new"))
  assert workset_registry.load_workset_boxes(reg) == {"alpha": "/new"}


def test_register_same_name_same_path_is_idempotent(reg: Path) -> None:
  """Re-registering the SAME ``(name, path)`` is a no-op replace (no duplicate).

  The uniqueness guard must never obstruct a legitimate re-register (the
  ``connect``/import re-register flow).
  """
  workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
  workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
  assert workset_registry.load_workset_boxes(reg) == {"alpha": "/abs/alpha"}


def test_register_second_name_same_path_is_refused(reg: Path) -> None:
  """A DIFFERENT name for an ALREADY-registered workspace path is refused.

  Bug A durable fix: within a workset a workspace path maps to exactly one box
  name — a second name for the same path (the duplicate-row root cause) raises
  and does NOT add an entry.  Mutation guard: dropping the uniqueness loop lets
  both entries land, so the post-refusal membership assertion goes RED.
  """
  from kanibako.errors import ProjectError

  workset_registry.register_workset_box(reg, "alpha", Path("/abs/shared"))
  with pytest.raises(ProjectError, match="already registered"):
    workset_registry.register_workset_box(reg, "beta", Path("/abs/shared"))
  # The refused register left the membership untouched — no duplicate.
  assert workset_registry.load_workset_boxes(reg) == {"alpha": "/abs/shared"}


def test_register_second_name_same_path_spelled_differently_is_refused(
  reg: Path,
  tmp_path: Path,
) -> None:
  """A trailing-slash / ``..`` spelling of an already-registered path is refused."""
  from kanibako.errors import ProjectError

  real = tmp_path / "real"
  real.mkdir()
  workset_registry.register_workset_box(reg, "alpha", real)
  with pytest.raises(ProjectError, match="already registered"):
    workset_registry.register_workset_box(reg, "beta", Path(f"{real}/../real/"))
  assert workset_registry.load_workset_boxes(reg) == {"alpha": str(real)}


def test_register_second_name_via_a_link_is_a_twin(
  reg: Path,
  tmp_path: Path,
) -> None:
  """A link to a registered workspace is a DIFFERENT path, so a second box.

  Q4Jei 155-DUPLINK: identity is the path, not the resolved target; twin
  boxes sharing one target are allowed.
  """
  real = tmp_path / "real"
  real.mkdir()
  link = tmp_path / "alias"
  link.symlink_to(real)
  workset_registry.register_workset_box(reg, "alpha", real)
  workset_registry.register_workset_box(reg, "beta", link)
  assert workset_registry.load_workset_boxes(reg) == {
    "alpha": str(real),
    "beta": str(link),
  }
  assert workset_registry.reverse_lookup_workset_box(reg, link) == "beta"


def test_register_move_same_name_new_path_is_allowed(reg: Path) -> None:
  """A MOVE (same name → new path) is NOT obstructed by the uniqueness guard."""
  workset_registry.register_workset_box(reg, "alpha", Path("/old"))
  workset_registry.register_workset_box(reg, "alpha", Path("/new"))
  assert workset_registry.load_workset_boxes(reg) == {"alpha": "/new"}


def test_register_distinct_paths_stay_distinct(reg: Path) -> None:
  """Distinct workspace paths under distinct names are never collapsed."""
  workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
  workset_registry.register_workset_box(reg, "beta", Path("/abs/beta"))
  assert workset_registry.load_workset_boxes(reg) == {
    "alpha": "/abs/alpha",
    "beta": "/abs/beta",
  }


def test_unregister_removes_only_that_box(reg: Path) -> None:
  """Unregister removes one box; the other survives."""
  workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
  workset_registry.register_workset_box(reg, "beta", Path("/abs/beta"))
  workset_registry.unregister_workset_box(reg, "alpha")
  assert workset_registry.load_workset_boxes(reg) == {"beta": "/abs/beta"}
  assert workset_registry.workset_box_path(reg, "alpha") is None


def test_unregister_absent_name_is_noop(reg: Path) -> None:
  """Unregistering a name that is not present is a no-op (no crash)."""
  workset_registry.register_workset_box(reg, "beta", Path("/abs/beta"))
  workset_registry.unregister_workset_box(reg, "missing")
  assert workset_registry.load_workset_boxes(reg) == {"beta": "/abs/beta"}


def test_unregister_absent_file_is_noop(reg: Path) -> None:
  """Unregistering when the file is absent is a no-op and creates nothing."""
  workset_registry.unregister_workset_box(reg, "anything")
  assert not reg.exists()


def test_box_names_sorted_on_write(reg: Path) -> None:
  """Box entries are written sorted (stable diffs)."""
  workset_registry.register_workset_box(reg, "zed", Path("/z"))
  workset_registry.register_workset_box(reg, "abe", Path("/a"))
  workset_registry.register_workset_box(reg, "mid", Path("/m"))
  raw = load_doc(reg)
  assert list(raw["boxes"].keys()) == ["abe", "mid", "zed"]


# ---------------------------------------------------------------------------
# Atomicity / sparsity — unknown sections dropped out loud, no stray scaffolding
# ---------------------------------------------------------------------------


def test_register_drops_an_unknown_section_and_prints_it(reg: Path, caplog) -> None:
  """Writing ``boxes:`` drops a section the module does not read, printing its text."""
  dump_doc(reg, {"connected": {"/ext": {"workset": "w", "box": "b"}}})
  workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
  assert load_doc(reg) == {"boxes": {"alpha": "/abs/alpha"}}
  assert caplog.messages == [
    f"{reg}: dropped the section 'connected', which kanibako does not read. It held:\n"
    "connected:\n  /ext:\n    workset: w\n    box: b"
  ]


def test_unregister_drops_an_unknown_section_and_prints_it(reg: Path, caplog) -> None:
  """Unregister rewrites the file too; the unknown section goes with one warning."""
  dump_doc(reg, {"boxes": {"alpha": "/abs/alpha"}, "connected": {"/ext": {"workset": "w"}}})
  workset_registry.unregister_workset_box(reg, "alpha")
  assert load_doc(reg) == {"boxes": {}}
  assert len(caplog.messages) == 1 and "'connected'" in caplog.messages[0]


def test_the_registry_is_written_under_the_generated_header(reg: Path) -> None:
  """The file opens with the CAPITALS header, and a comment in it does not survive a write."""
  from kanibako.project.registry_store import REGISTRY_HEADER

  reg.parent.mkdir(parents=True)
  reg.write_text("# keep me?\nboxes:\n  beta: /abs/beta  # and me?\n")
  workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
  assert reg.read_text() == (REGISTRY_HEADER + "boxes:\n  alpha: /abs/alpha\n  beta: /abs/beta\n")


def test_register_writes_only_boxes_section(reg: Path) -> None:
  """A first register on a fresh file writes ONLY the ``boxes:`` section.

  No empty scaffolding of sections the module was not asked to write.
  """
  workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
  raw = load_doc(reg)
  assert list(raw.keys()) == ["boxes"]


def test_register_write_is_atomic_no_temp_leftovers(reg: Path) -> None:
  """Successive writes replace the file cleanly, leaving no temp files."""
  workset_registry.register_workset_box(reg, "a", Path("/a"))
  workset_registry.register_workset_box(reg, "b", Path("/b"))
  workset_registry.unregister_workset_box(reg, "a")
  leftovers = [p.name for p in reg.parent.iterdir() if p.name != "registry.yaml"]
  assert leftovers == []


# ---------------------------------------------------------------------------
# The path is fixed: ``meta.workset.registry`` = ``<workset_root>/registry.yaml``
# ---------------------------------------------------------------------------


def test_the_registry_is_fixed_under_the_workset_root(tmp_path: Path) -> None:
  """No setting moves it: the keyspec row is ``{meta.workset.path}/registry.yaml``."""
  workset_root = tmp_path / "myws"
  assert workset_registry.workset_registry_path(workset_root) == workset_root / "registry.yaml"


def test_resolve_is_pure_no_side_effects(tmp_path: Path) -> None:
  """The path function touches no filesystem."""
  workset_root = tmp_path / "myws"
  workset_registry.workset_registry_path(workset_root)
  assert not workset_root.exists()


def test_the_standalone_mark_is_a_registry_with_no_members(tmp_path: Path) -> None:
  """A file holding only ``__STANDALONE__`` reads as no members, never as a malformed doc."""
  reg = tmp_path / "registry.yaml"
  reg.write_text("  __standalone__\n")
  assert workset_registry.load_workset_boxes(reg) == {}


@pytest.mark.parametrize("value", [None, "/srv/reg.yaml"])
def test_the_retired_key_is_refused_at_the_set_door_and_nothing_is_written(
  tmp_path: Path, value: str | None
) -> None:
  """``workset.registry`` is no key: the ``set`` door refuses it, naming it, and writes nothing.

  Driven through :func:`set_config_value`, the door the ``workset set`` verb runs.
  """
  from kanibako.settings.config_interface import set_config_value
  from kanibako.settings.config_keys import ConfigLevel

  workset_settings = tmp_path / "myws" / "workset.yaml"
  message = set_config_value(
    "workset.registry",
    value,
    config_path=workset_settings,
    cascade_workset_path=workset_settings,
    command_scope=ConfigLevel.workset,
  )
  assert message.startswith("Error:"), message
  assert "workset.registry" in message, message
  assert not workset_settings.exists(), f"{workset_settings} was written: {message}"


def test_concurrent_registrations_keep_every_row(tmp_path: Path, monkeypatch) -> None:
  """Two registrations that read the registry at once must not drop a row.

  The read is held open until both have read, which is the interleaving that lost
  two of twenty rows in a 5×4 concurrent-create run.
  """
  import threading

  registry = tmp_path / "registry.yaml"
  workset_registry.register_workset_box(registry, "seed", tmp_path / "seed")
  real_load = workset_registry._load_boxes_raw
  both_read = threading.Barrier(2, timeout=2)

  def _slow_load(path):
    got = real_load(path)
    try:
      both_read.wait()
    except threading.BrokenBarrierError:
      pass  # serialized: the other writer is still waiting on the lock
    return got

  monkeypatch.setattr(workset_registry, "_load_boxes_raw", _slow_load)
  threads = [
    threading.Thread(
      target=workset_registry.register_workset_box, args=(registry, name, tmp_path / name)
    )
    for name in ("one", "two")
  ]
  for t in threads:
    t.start()
  for t in threads:
    t.join()

  assert set(workset_registry.load_workset_boxes(registry)) == {"seed", "one", "two"}

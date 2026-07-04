"""Tests for kanibako.workset_registry (the per-workset box-membership store).

Every membership function takes the resolved per-workset registry FILE path
(``workset.registry`` == ``<workset_root>/registry.yaml`` at default). These
tests build that path explicitly from ``tmp_path``.  ADDITIVE infra (P3): no
existing flow consumes this module yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako import workset_registry
from kanibako.config_io import dump_doc, load_doc


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
    assert workset_registry.workset_box_names(reg) == set()
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
    assert workset_registry.workset_box_names(reg) == set()
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
    assert workset_registry.workset_box_names(reg) == {"alpha", "beta"}
    assert workset_registry.workset_box_path(reg, "alpha") == "/abs/alpha"
    assert workset_registry.workset_box_path(reg, "beta") == "/abs/beta"


def test_register_replaces_path(reg: Path) -> None:
    """Re-registering a name overwrites the stored path (a moved box)."""
    workset_registry.register_workset_box(reg, "alpha", Path("/old"))
    workset_registry.register_workset_box(reg, "alpha", Path("/new"))
    assert workset_registry.load_workset_boxes(reg) == {"alpha": "/new"}


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
# Atomicity / sparsity — sibling sections preserved, no stray scaffolding
# ---------------------------------------------------------------------------


def test_register_preserves_sibling_sections(reg: Path) -> None:
    """Writing ``boxes:`` leaves an unrelated sibling section untouched."""
    dump_doc(reg, {"connected": {"/ext": {"workset": "w", "box": "b"}}})
    workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
    raw = load_doc(reg)
    assert raw["connected"] == {"/ext": {"workset": "w", "box": "b"}}
    assert raw["boxes"] == {"alpha": "/abs/alpha"}


def test_unregister_preserves_sibling_sections(reg: Path) -> None:
    """Unregister rewrites only ``boxes:``; a sibling section survives."""
    dump_doc(reg, {"connected": {"/ext": {"workset": "w"}}})
    workset_registry.register_workset_box(reg, "alpha", Path("/abs/alpha"))
    workset_registry.unregister_workset_box(reg, "alpha")
    raw = load_doc(reg)
    assert raw["connected"] == {"/ext": {"workset": "w"}}
    assert raw["boxes"] == {}


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
# Path resolver — default vs. honored repoint (mutation-proven)
# ---------------------------------------------------------------------------


def test_resolve_default_is_workset_root_registry(tmp_path: Path) -> None:
    """No ``workset.registry`` set → default ``<workset_root>/registry.yaml``."""
    workset_root = tmp_path / "myws"
    # None settings and empty settings both fall through to the default.
    assert (
        workset_registry.resolve_workset_registry_path(workset_root, None)
        == workset_root / "registry.yaml"
    )
    assert (
        workset_registry.resolve_workset_registry_path(workset_root, {})
        == workset_root / "registry.yaml"
    )
    # A ``workset`` table without ``registry`` also uses the default.
    assert (
        workset_registry.resolve_workset_registry_path(
            workset_root, {"workset": {"image": "x"}}
        )
        == workset_root / "registry.yaml"
    )


def test_resolve_honors_absolute_repoint(tmp_path: Path) -> None:
    """A SET absolute ``workset.registry`` is honored, NOT the default.

    Mutation guard: if the repoint branch is broken (always returns the default
    ``workset_root/registry.yaml``), this assertion fails.
    """
    workset_root = tmp_path / "myws"
    custom = tmp_path / "elsewhere" / "myreg.yaml"
    resolved = workset_registry.resolve_workset_registry_path(
        workset_root, {"workset": {"registry": str(custom)}}
    )
    assert resolved == custom
    assert resolved != workset_root / "registry.yaml"


def test_resolve_expands_user_home_repoint(tmp_path: Path) -> None:
    """A ``~``-based repoint expands the user home (like sibling path keys)."""
    workset_root = tmp_path / "myws"
    resolved = workset_registry.resolve_workset_registry_path(
        workset_root, {"workset": {"registry": "~/custom/reg.yaml"}}
    )
    assert resolved == Path("~/custom/reg.yaml").expanduser()
    assert resolved.is_absolute()


def test_resolve_relative_repoint_anchors_under_workset_root(tmp_path: Path) -> None:
    """A relative repoint anchors under the workset root (deterministic)."""
    workset_root = tmp_path / "myws"
    resolved = workset_registry.resolve_workset_registry_path(
        workset_root, {"workset": {"registry": "sub/reg.yaml"}}
    )
    assert resolved == workset_root / "sub" / "reg.yaml"


def test_resolve_is_pure_no_side_effects(tmp_path: Path) -> None:
    """The resolver touches no filesystem (pure function of its inputs)."""
    workset_root = tmp_path / "myws"
    workset_registry.resolve_workset_registry_path(workset_root, None)
    assert not workset_root.exists()

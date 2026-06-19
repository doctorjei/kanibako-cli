"""Tests for kanibako.registry_store (the consolidated system.registry store)."""

from __future__ import annotations

from pathlib import Path

from kanibako import registry_store
from kanibako.config_io import load_doc


def test_registry_path_under_global(tmp_path: Path) -> None:
    """The registry lives at ``{data_path}/global/registry.yaml`` (== system.registry)."""
    assert (
        registry_store.registry_path(tmp_path)
        == tmp_path / "global" / "registry.yaml"
    )


def test_fresh_tree_empty_sections(tmp_path: Path) -> None:
    """Absent registry.yaml → every section present and empty."""
    reg = registry_store.load_registry(tmp_path)
    assert reg == {
        "projects": {},
        "worksets": {},
        "workset_roots": {},
        "connected": {},
        "standalone": {},
    }


def test_sections_round_trip(tmp_path: Path) -> None:
    """All sections survive a save/load round-trip with their shapes intact."""
    reg = {
        "projects": {"myapp": "/home/user/myapp"},
        "worksets": {"ws": "/home/user/ws"},
        "workset_roots": {"ws": "/home/user/ws"},
        "connected": {"/abs/ext": {"workset": "ws", "project": "foo"}},
        "standalone": {"abc_box": "/abs/proj"},
    }
    registry_store.save_registry(tmp_path, reg)
    assert registry_store.load_registry(tmp_path) == reg


def test_save_creates_global_dir(tmp_path: Path) -> None:
    """Saving into a fresh tree creates the global/ parent dir."""
    deep = tmp_path / "a" / "b"
    registry_store.save_registry(deep, {"projects": {"x": "/x"}})
    assert (deep / "global" / "registry.yaml").is_file()


def test_name_sections_sorted_on_write(tmp_path: Path) -> None:
    """projects/worksets/workset_roots keys are written sorted (stable diffs)."""
    registry_store.save_registry(
        tmp_path,
        {"projects": {"zed": "/z", "abe": "/a", "mid": "/m"}},
    )
    raw = load_doc(registry_store.registry_path(tmp_path))
    assert list(raw["projects"].keys()) == ["abe", "mid", "zed"]


def test_save_section_preserves_other_sections(tmp_path: Path) -> None:
    """save_section swaps one section and leaves the rest untouched."""
    registry_store.save_registry(
        tmp_path,
        {
            "projects": {"keep": "/keep"},
            "connected": {"/ext": {"workset": "w", "project": "p"}},
        },
    )
    registry_store.save_section(tmp_path, "standalone", {"box1": "/proj"})

    reg = registry_store.load_registry(tmp_path)
    assert reg["projects"] == {"keep": "/keep"}
    assert reg["connected"] == {"/ext": {"workset": "w", "project": "p"}}
    assert reg["standalone"] == {"box1": "/proj"}


def test_load_section_returns_single_section(tmp_path: Path) -> None:
    registry_store.save_section(tmp_path, "worksets", {"ws": "/root"})
    assert registry_store.load_section(tmp_path, "worksets") == {"ws": "/root"}
    # Untouched sections read empty.
    assert registry_store.load_section(tmp_path, "projects") == {}


def test_atomic_write_no_partial_on_existing(tmp_path: Path) -> None:
    """A second save fully replaces the file (atomic temp + replace)."""
    registry_store.save_section(tmp_path, "projects", {"a": "/a", "b": "/b"})
    registry_store.save_section(tmp_path, "projects", {"a": "/a"})
    assert registry_store.load_section(tmp_path, "projects") == {"a": "/a"}
    # No stray temp files left in the global dir.
    global_dir = registry_store.registry_path(tmp_path).parent
    leftovers = [p.name for p in global_dir.iterdir() if p.name != "registry.yaml"]
    assert leftovers == []

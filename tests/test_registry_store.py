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
        "seeded": {"projects": {}, "standalone": {}},
        "rigs": {},
        "image_shells": {},
    }


def test_sections_round_trip(tmp_path: Path) -> None:
    """All sections survive a save/load round-trip with their shapes intact."""
    reg = {
        "projects": {"myapp": "/home/user/myapp"},
        "worksets": {"ws": "/home/user/ws"},
        "workset_roots": {"ws": "/home/user/ws"},
        "connected": {"/abs/ext": {"workset": "ws", "project": "foo"}},
        "standalone": {"abc_box": "/abs/proj"},
        "seeded": {
            "projects": {"myapp": True},
            "standalone": {"abc_box": True},
        },
        "rigs": {"corp/base:1.0": {"kind": "prefab"}},
        "image_shells": {"sha256:abc": "/bin/bash"},
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


def test_register_standalone_adds_entry(tmp_path: Path) -> None:
    registry_store.register_standalone(tmp_path, "abc_box", Path("/abs/proj"))
    assert registry_store.load_standalone(tmp_path) == {"abc_box": "/abs/proj"}
    assert registry_store.standalone_box_names(tmp_path) == {"abc_box"}


def test_register_standalone_overwrites_root(tmp_path: Path) -> None:
    registry_store.register_standalone(tmp_path, "abc_box", Path("/old"))
    registry_store.register_standalone(tmp_path, "abc_box", Path("/new"))
    assert registry_store.load_standalone(tmp_path) == {"abc_box": "/new"}


def test_unregister_standalone(tmp_path: Path) -> None:
    registry_store.register_standalone(tmp_path, "abc_box", Path("/abs/proj"))
    registry_store.unregister_standalone(tmp_path, "abc_box")
    assert registry_store.load_standalone(tmp_path) == {}
    # No-op when absent.
    registry_store.unregister_standalone(tmp_path, "missing")


def test_standalone_name_for_root(tmp_path: Path) -> None:
    registry_store.register_standalone(tmp_path, "abc_box", Path("/abs/proj"))
    assert (
        registry_store.standalone_name_for_root(tmp_path, Path("/abs/proj"))
        == "abc_box"
    )
    assert (
        registry_store.standalone_name_for_root(tmp_path, Path("/other")) is None
    )


def test_standalone_register_preserves_other_sections(tmp_path: Path) -> None:
    registry_store.save_section(tmp_path, "projects", {"keep": "/keep"})
    registry_store.register_standalone(tmp_path, "abc_box", Path("/proj"))
    assert registry_store.load_section(tmp_path, "projects") == {"keep": "/keep"}


def test_section_at_helpers_round_trip_by_registry_path(tmp_path: Path) -> None:
    """load_section_at / save_section_at operate via the registry.yaml path.

    They back the path-based section-owners (rig_registry, shells); the
    ``data_path`` is recovered from ``…/global/registry.yaml``.
    """
    reg_file = registry_store.registry_path(tmp_path)
    registry_store.save_section_at(reg_file, "rigs", {"corp/x:1": {"kind": "prefab"}})
    assert registry_store.load_section_at(reg_file, "rigs") == {
        "corp/x:1": {"kind": "prefab"}
    }
    # Empty/absent section reads {}.
    assert registry_store.load_section_at(reg_file, "image_shells") == {}


def test_section_at_preserves_sibling_sections(tmp_path: Path) -> None:
    """A section-owner writing via *_at preserves every other section."""
    reg_file = registry_store.registry_path(tmp_path)
    registry_store.save_section(tmp_path, "projects", {"keep": "/keep"})
    registry_store.save_section_at(reg_file, "rigs", {"r": {"kind": "extended"}})
    registry_store.save_section_at(reg_file, "image_shells", {"sha256:a": "/bin/sh"})

    reg = registry_store.load_registry(tmp_path)
    assert reg["projects"] == {"keep": "/keep"}
    assert reg["rigs"] == {"r": {"kind": "extended"}}
    assert reg["image_shells"] == {"sha256:a": "/bin/sh"}


# ---------------------------------------------------------------------------
# Seeded-flag helpers (the per-box seed-once primitives for primary + standalone)
# ---------------------------------------------------------------------------


def test_seeded_default_false_both_domains(tmp_path: Path) -> None:
    """An unrecorded box reads as unseeded for either domain."""
    assert registry_store.is_box_seeded(tmp_path, "projects", "myapp") is False
    assert registry_store.is_box_seeded(tmp_path, "standalone", "abc_box") is False


def test_mark_box_seeded_persists_projects(tmp_path: Path) -> None:
    """mark_box_seeded_entry sets True and survives a fresh load_registry."""
    registry_store.mark_box_seeded_entry(tmp_path, "projects", "myapp")
    assert registry_store.is_box_seeded(tmp_path, "projects", "myapp") is True
    # Persisted: a brand-new load (no in-process state) still sees True.
    assert (
        registry_store.load_registry(tmp_path)["seeded"]["projects"]["myapp"]
        is True
    )
    # A different name in the same domain is unaffected.
    assert registry_store.is_box_seeded(tmp_path, "projects", "other") is False


def test_mark_box_seeded_persists_standalone(tmp_path: Path) -> None:
    registry_store.mark_box_seeded_entry(tmp_path, "standalone", "abc_box")
    assert registry_store.is_box_seeded(tmp_path, "standalone", "abc_box") is True
    # Marking standalone does NOT bleed into the projects domain.
    assert registry_store.is_box_seeded(tmp_path, "projects", "abc_box") is False


def test_mark_box_seeded_idempotent(tmp_path: Path) -> None:
    registry_store.mark_box_seeded_entry(tmp_path, "projects", "myapp")
    registry_store.mark_box_seeded_entry(tmp_path, "projects", "myapp")
    assert registry_store.is_box_seeded(tmp_path, "projects", "myapp") is True


def test_seeded_survives_unrelated_register_standalone(tmp_path: Path) -> None:
    """A seeded flag is preserved across an unrelated registry mutation."""
    registry_store.mark_box_seeded_entry(tmp_path, "projects", "myapp")
    # An unrelated section write must not drop the seeded flag.
    registry_store.register_standalone(tmp_path, "abc_box", Path("/proj"))
    registry_store.save_section(tmp_path, "projects", {"myapp": "/p"})
    assert registry_store.is_box_seeded(tmp_path, "projects", "myapp") is True
    assert registry_store.load_standalone(tmp_path) == {"abc_box": "/proj"}


def test_legacy_registry_without_seeded_key_loads_empty(tmp_path: Path) -> None:
    """A registry.yaml predating the seeded section loads with empty subdicts."""
    from kanibako.config_io import dump_doc

    # Write a legacy file with NO 'seeded' key.
    dump_doc(
        registry_store.registry_path(tmp_path),
        {"standalone": {"abc_box": "/p"}},
    )
    reg = registry_store.load_registry(tmp_path)
    assert reg["seeded"] == {"projects": {}, "standalone": {}}
    assert registry_store.is_box_seeded(tmp_path, "standalone", "abc_box") is False
    assert registry_store.is_box_seeded(tmp_path, "projects", "abc_box") is False


def test_seeded_section_inner_keys_sorted_on_write(tmp_path: Path) -> None:
    """Inner per-domain keys are written sorted for stable diffs."""
    reg = registry_store.load_registry(tmp_path)
    reg["seeded"]["projects"] = {"zed": True, "abe": True, "mid": True}
    registry_store.save_registry(tmp_path, reg)
    raw = load_doc(registry_store.registry_path(tmp_path))
    assert list(raw["seeded"]["projects"].keys()) == ["abe", "mid", "zed"]


def test_atomic_write_no_partial_on_existing(tmp_path: Path) -> None:
    """A second save fully replaces the file (atomic temp + replace)."""
    registry_store.save_section(tmp_path, "projects", {"a": "/a", "b": "/b"})
    registry_store.save_section(tmp_path, "projects", {"a": "/a"})
    assert registry_store.load_section(tmp_path, "projects") == {"a": "/a"}
    # No stray temp files left in the global dir.
    global_dir = registry_store.registry_path(tmp_path).parent
    leftovers = [p.name for p in global_dir.iterdir() if p.name != "registry.yaml"]
    assert leftovers == []

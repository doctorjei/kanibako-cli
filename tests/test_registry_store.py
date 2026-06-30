"""Tests for kanibako.registry_store (the consolidated system.registry store).

Every public function takes the resolved ``config.registry`` FILE path
(``std.registry`` == ``{data_path}/global/registry.yaml`` at default config).
These tests build that file path explicitly from ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako import registry_store
from kanibako.config_io import load_doc


@pytest.fixture
def reg(tmp_path: Path) -> Path:
    """The resolved ``config.registry`` file path under a fresh tree."""
    return tmp_path / "global" / "registry.yaml"


def test_fresh_tree_empty_sections(reg: Path) -> None:
    """Absent registry.yaml → every section present and empty."""
    loaded = registry_store.load_registry(reg)
    assert loaded == {
        "projects": {},
        "worksets": {},
        "connected": {},
        "standalone": {},
        "rigs": {},
        "image_shells": {},
    }


def test_sections_round_trip(reg: Path) -> None:
    """All sections survive a save/load round-trip with their shapes intact."""
    sections = {
        "projects": {"myapp": "/home/user/myapp"},
        "worksets": {"ws": "/home/user/ws"},
        "connected": {"/abs/ext": {"workset": "ws", "project": "foo"}},
        "standalone": {"abc_box": "/abs/proj"},
        "rigs": {"corp/base:1.0": {"kind": "prefab"}},
        "image_shells": {"sha256:abc": "/bin/bash"},
    }
    registry_store.save_registry(reg, sections)
    assert registry_store.load_registry(reg) == sections


def test_save_creates_global_dir(tmp_path: Path) -> None:
    """Saving into a fresh tree creates the registry's parent dir."""
    reg = tmp_path / "a" / "b" / "global" / "registry.yaml"
    registry_store.save_registry(reg, {"projects": {"x": "/x"}})
    assert reg.is_file()


def test_name_sections_sorted_on_write(reg: Path) -> None:
    """projects/worksets keys are written sorted (stable diffs)."""
    registry_store.save_registry(
        reg,
        {"projects": {"zed": "/z", "abe": "/a", "mid": "/m"}},
    )
    raw = load_doc(reg)
    assert list(raw["projects"].keys()) == ["abe", "mid", "zed"]


def test_save_section_preserves_other_sections(reg: Path) -> None:
    """save_section swaps one section and leaves the rest untouched."""
    registry_store.save_registry(
        reg,
        {
            "projects": {"keep": "/keep"},
            "connected": {"/ext": {"workset": "w", "project": "p"}},
        },
    )
    registry_store.save_section(reg, "standalone", {"box1": "/proj"})

    loaded = registry_store.load_registry(reg)
    assert loaded["projects"] == {"keep": "/keep"}
    assert loaded["connected"] == {"/ext": {"workset": "w", "project": "p"}}
    assert loaded["standalone"] == {"box1": "/proj"}


def test_load_section_returns_single_section(reg: Path) -> None:
    registry_store.save_section(reg, "worksets", {"ws": "/root"})
    assert registry_store.load_section(reg, "worksets") == {"ws": "/root"}
    # Untouched sections read empty.
    assert registry_store.load_section(reg, "projects") == {}


def test_register_standalone_adds_entry(reg: Path) -> None:
    registry_store.register_standalone(reg, "abc_box", Path("/abs/proj"))
    assert registry_store.load_standalone(reg) == {"abc_box": "/abs/proj"}
    assert registry_store.standalone_box_names(reg) == {"abc_box"}


def test_register_standalone_overwrites_root(reg: Path) -> None:
    registry_store.register_standalone(reg, "abc_box", Path("/old"))
    registry_store.register_standalone(reg, "abc_box", Path("/new"))
    assert registry_store.load_standalone(reg) == {"abc_box": "/new"}


def test_unregister_standalone(reg: Path) -> None:
    registry_store.register_standalone(reg, "abc_box", Path("/abs/proj"))
    registry_store.unregister_standalone(reg, "abc_box")
    assert registry_store.load_standalone(reg) == {}
    # No-op when absent.
    registry_store.unregister_standalone(reg, "missing")


def test_standalone_name_for_root(reg: Path) -> None:
    registry_store.register_standalone(reg, "abc_box", Path("/abs/proj"))
    assert (
        registry_store.standalone_name_for_root(reg, Path("/abs/proj"))
        == "abc_box"
    )
    assert (
        registry_store.standalone_name_for_root(reg, Path("/other")) is None
    )


def test_standalone_register_preserves_other_sections(reg: Path) -> None:
    registry_store.save_section(reg, "projects", {"keep": "/keep"})
    registry_store.register_standalone(reg, "abc_box", Path("/proj"))
    assert registry_store.load_section(reg, "projects") == {"keep": "/keep"}


def test_section_round_trip_by_registry_path(reg: Path) -> None:
    """load_section / save_section operate via the registry.yaml path.

    They back the path-based section-owners (rig_registry, shells).
    """
    registry_store.save_section(reg, "rigs", {"corp/x:1": {"kind": "prefab"}})
    assert registry_store.load_section(reg, "rigs") == {
        "corp/x:1": {"kind": "prefab"}
    }
    # Empty/absent section reads {}.
    assert registry_store.load_section(reg, "image_shells") == {}


def test_section_preserves_sibling_sections(reg: Path) -> None:
    """A section-owner writing a section preserves every other section."""
    registry_store.save_section(reg, "projects", {"keep": "/keep"})
    registry_store.save_section(reg, "rigs", {"r": {"kind": "extended"}})
    registry_store.save_section(reg, "image_shells", {"sha256:a": "/bin/sh"})

    loaded = registry_store.load_registry(reg)
    assert loaded["projects"] == {"keep": "/keep"}
    assert loaded["rigs"] == {"r": {"kind": "extended"}}
    assert loaded["image_shells"] == {"sha256:a": "/bin/sh"}


# ---------------------------------------------------------------------------
# No `seeded` section (B7): registry MEMBERSHIP is the seed signal — the
# per-box `seeded` flag section and its helpers are GONE.
# ---------------------------------------------------------------------------


def test_no_seeded_section_in_loaded_registry(reg: Path) -> None:
    """A fresh registry has NO ``seeded`` section (B7 clean break)."""
    loaded = registry_store.load_registry(reg)
    assert "seeded" not in loaded
    # The membership sections are present and empty.
    assert loaded["projects"] == {}
    assert loaded["standalone"] == {}


def test_seeded_helpers_removed(reg: Path) -> None:
    """The seeded-flag read/write primitives no longer exist on the module."""
    assert not hasattr(registry_store, "is_box_seeded")
    assert not hasattr(registry_store, "mark_box_seeded_entry")


def test_legacy_seeded_section_is_dropped_not_round_tripped(reg: Path) -> None:
    """A legacy registry.yaml with a ``seeded`` section ignores + drops it.

    Clean break (pre-release, no read-compat): the section is neither read into
    the loaded shape nor re-persisted on the next save.
    """
    from kanibako.config_io import dump_doc

    dump_doc(
        reg,
        {
            "standalone": {"abc_box": "/p"},
            "seeded": {"projects": {"myapp": True}, "standalone": {"abc_box": True}},
        },
    )
    loaded = registry_store.load_registry(reg)
    assert "seeded" not in loaded
    assert registry_store.load_standalone(reg) == {"abc_box": "/p"}
    # Round-trip a save: the seeded section does not reappear on disk.
    registry_store.save_registry(reg, loaded)
    raw = load_doc(reg)
    assert "seeded" not in raw


def test_atomic_write_no_partial_on_existing(reg: Path) -> None:
    """A second save fully replaces the file (atomic temp + replace)."""
    registry_store.save_section(reg, "projects", {"a": "/a", "b": "/b"})
    registry_store.save_section(reg, "projects", {"a": "/a"})
    assert registry_store.load_section(reg, "projects") == {"a": "/a"}
    # No stray temp files left in the global dir.
    global_dir = reg.parent
    leftovers = [p.name for p in global_dir.iterdir() if p.name != "registry.yaml"]
    assert leftovers == []


# ---------------------------------------------------------------------------
# config.registry is the SINGLE SOURCE of the registry location (REG-SINGLE-SOURCE).
#
# The store is path-based: it writes/reads exactly the resolved ``config.registry``
# file path (``std.registry``).  At DEFAULT config that path is
# ``{config.data}/global/registry.yaml`` (byte-identical to the old reconstructed
# tail); a REPOINTED ``config.registry`` is now honored end-to-end.
# ---------------------------------------------------------------------------


def test_default_config_registry_resolves_under_data(tmp_path: Path) -> None:
    """Resolution oracle: default ``config.registry`` == ``config.data/global/registry.yaml``.

    This is the equivalence anchor — the path the store targets at default config
    matches the old ``data_path``-relative reconstruction byte-for-byte.
    """
    from kanibako.paths import resolve_system_paths

    home = tmp_path / "home"
    data_home = tmp_path / "xdg-data"
    resolved = resolve_system_paths({}, data_home=data_home, home=home)
    assert (
        resolved["config.registry"]
        == resolved["config.data"] / "global" / "registry.yaml"
    )


def test_repointed_config_registry_is_honored(tmp_path: Path) -> None:
    """A repointed ``config.registry`` is the file the store actually uses.

    Resolve ``config.registry`` to a location OUTSIDE ``config.data`` and prove
    that ``save_registry``/``load_registry`` (via the resolved path) read and write
    THAT file — and that the default location is NOT touched.  This is the new
    honored behavior the block delivers; before, the store reconstructed
    ``data_path/global/registry.yaml`` and silently ignored the repoint.
    """
    from kanibako.paths import resolve_system_paths

    home = tmp_path / "home"
    data_home = tmp_path / "xdg-data"
    custom = tmp_path / "elsewhere" / "myregistry.yaml"

    resolved = resolve_system_paths(
        {"config.registry": str(custom)}, data_home=data_home, home=home,
    )
    registry = resolved["config.registry"]
    assert registry == custom  # the repoint resolved through, not the default

    registry_store.save_section(registry, "projects", {"myapp": "/home/user/myapp"})

    # The repointed file holds the data...
    assert custom.is_file()
    assert registry_store.load_section(registry, "projects") == {
        "myapp": "/home/user/myapp"
    }
    # ...and the DEFAULT location was never created (the repoint is honored, not
    # ignored with a reconstructed default).
    default_loc = resolved["config.data"] / "global" / "registry.yaml"
    assert not default_loc.exists()

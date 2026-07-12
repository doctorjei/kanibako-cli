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
    """Absent registry.yaml → every section present and empty.

    ``projects`` is NOT among them — it was RETIRED (clean split, 2026-07-08):
    default-mode box identity lives in the primary per-workset ``boxes:``
    membership now, not in the global registry.
    """
    loaded = registry_store.load_registry(reg)
    assert loaded == {
        "worksets": {},
        "standalone": {},
        "deregistered": {},
        "rigs": {},
        "image_shells": {},
    }


def test_sections_round_trip(reg: Path) -> None:
    """All sections survive a save/load round-trip with their shapes intact."""
    sections = {
        "worksets": {"ws": "/home/user/ws"},
        "standalone": {"abc_box": "/abs/proj"},
        "deregistered": {
            "gone": {"kind": "primary", "workspace": "/w", "metadata": "/m"}
        },
        "rigs": {"corp/base:1.0": {"kind": "prefab"}},
        "image_shells": {"sha256:abc": "/bin/bash"},
    }
    registry_store.save_registry(reg, sections)
    assert registry_store.load_registry(reg) == sections


def test_save_creates_global_dir(tmp_path: Path) -> None:
    """Saving into a fresh tree creates the registry's parent dir."""
    reg = tmp_path / "a" / "b" / "global" / "registry.yaml"
    registry_store.save_registry(reg, {"worksets": {"x": "/x"}})
    assert reg.is_file()


def test_name_sections_sorted_on_write(reg: Path) -> None:
    """worksets keys are written sorted (stable diffs)."""
    registry_store.save_registry(
        reg,
        {"worksets": {"zed": "/z", "abe": "/a", "mid": "/m"}},
    )
    raw = load_doc(reg)
    assert list(raw["worksets"].keys()) == ["abe", "mid", "zed"]


def test_save_section_preserves_other_sections(reg: Path) -> None:
    """save_section swaps one section and leaves the rest untouched."""
    registry_store.save_registry(
        reg,
        {
            "worksets": {"keep": "/keep"},
            "rigs": {"corp/base:1.0": {"kind": "prefab"}},
        },
    )
    registry_store.save_section(reg, "standalone", {"box1": "/proj"})

    loaded = registry_store.load_registry(reg)
    assert loaded["worksets"] == {"keep": "/keep"}
    assert loaded["rigs"] == {"corp/base:1.0": {"kind": "prefab"}}
    assert loaded["standalone"] == {"box1": "/proj"}


def test_load_section_returns_single_section(reg: Path) -> None:
    registry_store.save_section(reg, "worksets", {"ws": "/root"})
    assert registry_store.load_section(reg, "worksets") == {"ws": "/root"}
    # Untouched sections read empty.
    assert registry_store.load_section(reg, "standalone") == {}


def test_projects_section_retired(reg: Path) -> None:
    """The ``projects`` section is retired: never surfaced, dropped on write.

    A legacy registry.yaml carrying a ``projects`` block (an older install that
    predates the clean split) is ignored on load and dropped on the next save —
    no migration, no read-compat.
    """
    from kanibako.config_io import dump_doc

    dump_doc(
        reg,
        {
            "projects": {"myapp": "/home/user/myapp"},
            "worksets": {"ws": "/root"},
        },
    )
    loaded = registry_store.load_registry(reg)
    assert "projects" not in loaded
    assert loaded["worksets"] == {"ws": "/root"}
    # Round-trip a save: the projects section does not reappear on disk.
    registry_store.save_registry(reg, loaded)
    raw = load_doc(reg)
    assert "projects" not in raw


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
    registry_store.save_section(reg, "worksets", {"keep": "/keep"})
    registry_store.register_standalone(reg, "abc_box", Path("/proj"))
    assert registry_store.load_section(reg, "worksets") == {"keep": "/keep"}


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
    registry_store.save_section(reg, "worksets", {"keep": "/keep"})
    registry_store.save_section(reg, "rigs", {"r": {"kind": "extended"}})
    registry_store.save_section(reg, "image_shells", {"sha256:a": "/bin/sh"})

    loaded = registry_store.load_registry(reg)
    assert loaded["worksets"] == {"keep": "/keep"}
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
    assert loaded["worksets"] == {}
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
    registry_store.save_section(reg, "worksets", {"a": "/a", "b": "/b"})
    registry_store.save_section(reg, "worksets", {"a": "/a"})
    assert registry_store.load_section(reg, "worksets") == {"a": "/a"}
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

    registry_store.save_section(registry, "worksets", {"myapp": "/home/user/myapp"})

    # The repointed file holds the data...
    assert custom.is_file()
    assert registry_store.load_section(registry, "worksets") == {
        "myapp": "/home/user/myapp"
    }
    # ...and the DEFAULT location was never created (the repoint is honored, not
    # ignored with a reconstructed default).
    default_loc = resolved["config.data"] / "global" / "registry.yaml"
    assert not default_loc.exists()


# ---------------------------------------------------------------------------
# deregistered section (I1: rm-without-purge parks a recovery blob by name)
# ---------------------------------------------------------------------------

class TestDeregistered:
    def test_register_and_lookup_round_trip(self, reg: Path, tmp_path: Path) -> None:
        meta = tmp_path / "boxes" / "tempwow"
        meta.mkdir(parents=True)
        registry_store.register_deregistered(
            reg,
            "tempwow",
            kind="primary",
            workspace="/home/user/tempwow",
            metadata=str(meta),
            image="ghcr.io/x:1",
            deregistered_at="2026-07-12T00:00:00+00:00",
        )
        entry = registry_store.lookup_deregistered(reg, "tempwow")
        assert entry == {
            "kind": "primary",
            "workspace": "/home/user/tempwow",
            "metadata": str(meta),
            "image": "ghcr.io/x:1",
            "deregistered_at": "2026-07-12T00:00:00+00:00",
        }
        # Round-trips through the on-disk file (not just in-memory).
        assert registry_store.load_deregistered(reg)["tempwow"] == entry

    def test_lookup_missing_returns_none(self, reg: Path) -> None:
        assert registry_store.lookup_deregistered(reg, "nope") is None

    def test_lookup_is_a_pure_read(self, reg: Path) -> None:
        """lookup never mutates — even for an entry whose dir is gone."""
        registry_store.register_deregistered(
            reg, "ghost", kind="primary", workspace="/w",
            metadata="/does/not/exist",
        )
        assert registry_store.lookup_deregistered(reg, "ghost") is not None
        # Still present after lookup (self-heal is a list/purge concern only).
        assert "ghost" in registry_store.load_deregistered(reg)

    def test_unregister_drops_and_reports(self, reg: Path, tmp_path: Path) -> None:
        registry_store.register_deregistered(
            reg, "b", kind="primary", workspace="/w", metadata=str(tmp_path),
        )
        assert registry_store.unregister_deregistered(reg, "b") is True
        assert registry_store.lookup_deregistered(reg, "b") is None
        # Idempotent: dropping an absent entry is a no-op returning False.
        assert registry_store.unregister_deregistered(reg, "b") is False

    def test_optional_fields_omitted_when_none(self, reg: Path, tmp_path: Path) -> None:
        registry_store.register_deregistered(
            reg, "bare", kind="standalone", workspace=None, metadata=str(tmp_path),
        )
        entry = registry_store.lookup_deregistered(reg, "bare")
        assert entry == {
            "kind": "standalone",
            "workspace": None,
            "metadata": str(tmp_path),
        }
        assert "image" not in entry
        assert "deregistered_at" not in entry

    def test_list_self_heals_stale_entries(self, reg: Path, tmp_path: Path) -> None:
        live_dir = tmp_path / "live"
        live_dir.mkdir()
        registry_store.register_deregistered(
            reg, "live", kind="primary", workspace="/w", metadata=str(live_dir),
        )
        registry_store.register_deregistered(
            reg, "stale", kind="primary", workspace="/w",
            metadata=str(tmp_path / "gone"),
        )
        listed = registry_store.list_deregistered(reg)
        assert set(listed) == {"live"}
        # The self-heal is PERSISTED, not just filtered in memory.
        assert set(registry_store.load_deregistered(reg)) == {"live"}

    def test_section_survives_other_section_writes(self, reg: Path, tmp_path: Path) -> None:
        registry_store.register_deregistered(
            reg, "keep", kind="primary", workspace="/w", metadata=str(tmp_path),
        )
        registry_store.register_standalone(reg, "sa", tmp_path)
        # Writing a sibling section preserves deregistered.
        assert registry_store.lookup_deregistered(reg, "keep") is not None

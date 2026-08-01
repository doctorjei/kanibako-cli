"""Tests for the host-side rig registry (``rigs`` section of registry.yaml)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.runtime import rig_registry
from kanibako.runtime.rig_registry import (
    RigRecord,
    get,
    load_registry,
    remove,
    save_registry,
    upsert,
)


@pytest.fixture
def reg_path(tmp_path: Path) -> Path:
    """Path to the consolidated registry.yaml (rigs live in its ``rigs`` section).

    The ``data_path`` is the parent of ``global/`` — i.e. ``tmp_path`` — so the
    path-based API resolves the same file ``registry_store`` would.
    """
    return tmp_path / "global" / "registry.yaml"


def _prefab() -> RigRecord:
    return RigRecord(
        name="corp/base:1.0",
        kind="prefab",
        source="ghcr.io/corp/base:1.0",
        source_type="ref",
        added="2026-06-04T00:00:00Z",
    )


def _extended() -> RigRecord:
    return RigRecord(
        name="myhack",
        kind="extended",
        image="kanibako-rig-myhack",
        parent="kanibako-oci:latest",
        foundation_source="prefab:oci",
        reproducible=False,
        created="2026-06-04T00:00:00Z",
    )


def test_registry_path_uses_consolidated_registry() -> None:
    class _Std:
        registry = Path("/some/data/global/registry.yaml")

    assert rig_registry.registry_path(_Std()) == Path(  # type: ignore[arg-type]
        "/some/data/global/registry.yaml"
    )


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "global" / "nope.yaml") == {}


def test_load_empty_file_returns_empty(reg_path: Path) -> None:
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text("")
    assert load_registry(reg_path) == {}


def test_roundtrip_prefab(reg_path: Path) -> None:
    path = reg_path
    rec = _prefab()
    save_registry(path, {rec.name: rec})

    loaded = load_registry(path)
    assert set(loaded) == {"corp/base:1.0"}
    got = loaded["corp/base:1.0"]
    assert got == rec
    assert got.name == "corp/base:1.0"
    assert got.kind == "prefab"
    assert got.source == "ghcr.io/corp/base:1.0"
    assert got.source_type == "ref"
    assert got.added == "2026-06-04T00:00:00Z"


def test_roundtrip_extended(reg_path: Path) -> None:
    path = reg_path
    rec = _extended()
    save_registry(path, {rec.name: rec})

    got = load_registry(path)["myhack"]
    assert got == rec
    assert got.kind == "extended"
    assert got.image == "kanibako-rig-myhack"
    assert got.parent == "kanibako-oci:latest"
    assert got.foundation_source == "prefab:oci"
    assert got.reproducible is False
    assert got.created == "2026-06-04T00:00:00Z"


def test_roundtrip_both_records(reg_path: Path) -> None:
    path = reg_path
    prefab, extended = _prefab(), _extended()
    save_registry(path, {prefab.name: prefab, extended.name: extended})

    loaded = load_registry(path)
    assert loaded == {prefab.name: prefab, extended.name: extended}


def test_written_file_is_valid_yaml_and_reloads_equal(reg_path: Path) -> None:
    path = reg_path
    prefab, extended = _prefab(), _extended()
    records = {prefab.name: prefab, extended.name: extended}
    save_registry(path, records)

    # The file is valid YAML; rigs live in the consolidated registry's "rigs"
    # section alongside the other (here empty) registry sections.
    raw = yaml.safe_load(path.read_text())
    assert "rigs" in raw
    assert set(raw["rigs"]) == {"corp/base:1.0", "myhack"}

    # And it round-trips back to equal records.
    assert load_registry(path) == records


def test_names_with_slash_and_colon_survive_as_keys(reg_path: Path) -> None:
    path = reg_path
    rec = RigRecord(name="corp/base:1.0", kind="prefab")
    save_registry(path, {rec.name: rec})

    raw = yaml.safe_load(path.read_text())
    assert "corp/base:1.0" in raw["rigs"]
    assert "corp/base:1.0" in load_registry(path)


def test_none_fields_are_not_written(reg_path: Path) -> None:
    path = reg_path
    # Only name + kind set; everything else defaults to None.
    rec = RigRecord(name="bare", kind="extended")
    save_registry(path, {rec.name: rec})

    table = yaml.safe_load(path.read_text())["rigs"]["bare"]
    assert table["kind"] == "extended"
    for none_field in (
        "source",
        "source_type",
        "image",
        "parent",
        "foundation_source",
        "reproducible",
        "created",
        "added",
    ):
        assert none_field not in table, none_field
    # name is the key, not stored inside the table.
    assert "name" not in table

    got = load_registry(path)["bare"]
    assert got == rec
    assert got.source is None


def test_remove_absent_returns_false(reg_path: Path) -> None:
    path = reg_path
    save_registry(path, {})
    assert remove(path, "ghost") is False


def test_remove_present_returns_true_and_deletes(reg_path: Path) -> None:
    path = reg_path
    a, b = _prefab(), _extended()
    save_registry(path, {a.name: a, b.name: b})

    assert remove(path, a.name) is True
    loaded = load_registry(path)
    assert a.name not in loaded
    assert b.name in loaded


def test_upsert_adds_then_overwrites(reg_path: Path) -> None:
    path = reg_path

    rec = RigRecord(name="myhack", kind="extended", image="kanibako-rig-myhack")
    upsert(path, rec)
    assert load_registry(path)["myhack"].image == "kanibako-rig-myhack"

    # Overwrite by same name.
    updated = RigRecord(name="myhack", kind="extended", image="kanibako-rig-NEW")
    upsert(path, updated)
    loaded = load_registry(path)
    assert len(loaded) == 1
    assert loaded["myhack"].image == "kanibako-rig-NEW"


def test_upsert_preserves_other_records(reg_path: Path) -> None:
    path = reg_path
    existing = _prefab()
    upsert(path, existing)
    upsert(path, _extended())

    loaded = load_registry(path)
    assert set(loaded) == {"corp/base:1.0", "myhack"}


def test_get_present_and_absent(reg_path: Path) -> None:
    path = reg_path
    rec = _prefab()
    save_registry(path, {rec.name: rec})

    assert get(path, rec.name) == rec
    assert get(path, "missing") is None


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    # data_path = tmp_path/"nested"/"deeper"; registry.yaml lives under its
    # "global" subdir, which save_registry creates on write.
    path = tmp_path / "nested" / "deeper" / "global" / "registry.yaml"
    rec = _prefab()
    save_registry(path, {rec.name: rec})
    assert path.exists()
    assert get(path, rec.name) == rec

"""Tests for the host-side rig registry (``rigs.yaml``)."""

from __future__ import annotations

from pathlib import Path

import yaml

from kanibako import rig_registry
from kanibako.rig_registry import (
    RigRecord,
    get,
    load_registry,
    remove,
    save_registry,
    upsert,
)


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


def test_registry_path_uses_data_path() -> None:
    class _Std:
        data_path = Path("/some/data")

    assert rig_registry.registry_path(_Std()) == Path("/some/data/rigs.yaml")  # type: ignore[arg-type]


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "nope.yaml") == {}


def test_load_empty_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
    path.write_text("")
    assert load_registry(path) == {}


def test_roundtrip_prefab(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
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


def test_roundtrip_extended(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
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


def test_roundtrip_both_records(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
    prefab, extended = _prefab(), _extended()
    save_registry(path, {prefab.name: prefab, extended.name: extended})

    loaded = load_registry(path)
    assert loaded == {prefab.name: prefab, extended.name: extended}


def test_written_file_is_valid_yaml_and_reloads_equal(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
    prefab, extended = _prefab(), _extended()
    records = {prefab.name: prefab, extended.name: extended}
    save_registry(path, records)

    # The file is valid YAML with the expected top-level shape.
    raw = yaml.safe_load(path.read_text())
    assert set(raw) == {"rigs"}
    assert set(raw["rigs"]) == {"corp/base:1.0", "myhack"}

    # And it round-trips back to equal records.
    assert load_registry(path) == records


def test_names_with_slash_and_colon_survive_as_keys(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
    rec = RigRecord(name="corp/base:1.0", kind="prefab")
    save_registry(path, {rec.name: rec})

    raw = yaml.safe_load(path.read_text())
    assert "corp/base:1.0" in raw["rigs"]
    assert "corp/base:1.0" in load_registry(path)


def test_none_fields_are_not_written(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
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


def test_remove_absent_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
    save_registry(path, {})
    assert remove(path, "ghost") is False


def test_remove_present_returns_true_and_deletes(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
    a, b = _prefab(), _extended()
    save_registry(path, {a.name: a, b.name: b})

    assert remove(path, a.name) is True
    loaded = load_registry(path)
    assert a.name not in loaded
    assert b.name in loaded


def test_upsert_adds_then_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"

    rec = RigRecord(name="myhack", kind="extended", image="kanibako-rig-myhack")
    upsert(path, rec)
    assert load_registry(path)["myhack"].image == "kanibako-rig-myhack"

    # Overwrite by same name.
    updated = RigRecord(name="myhack", kind="extended", image="kanibako-rig-NEW")
    upsert(path, updated)
    loaded = load_registry(path)
    assert len(loaded) == 1
    assert loaded["myhack"].image == "kanibako-rig-NEW"


def test_upsert_preserves_other_records(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
    existing = _prefab()
    upsert(path, existing)
    upsert(path, _extended())

    loaded = load_registry(path)
    assert set(loaded) == {"corp/base:1.0", "myhack"}


def test_get_present_and_absent(tmp_path: Path) -> None:
    path = tmp_path / "rigs.yaml"
    rec = _prefab()
    save_registry(path, {rec.name: rec})

    assert get(path, rec.name) == rec
    assert get(path, "missing") is None


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "rigs.yaml"
    rec = _prefab()
    save_registry(path, {rec.name: rec})
    assert path.exists()
    assert get(path, rec.name) == rec

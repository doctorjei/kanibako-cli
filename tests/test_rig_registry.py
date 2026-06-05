"""Tests for the host-side rig registry (``rigs.toml``)."""

from __future__ import annotations

from pathlib import Path

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

    assert rig_registry.registry_path(_Std()) == Path("/some/data/rigs.toml")  # type: ignore[arg-type]


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "nope.toml") == {}


def test_roundtrip_prefab(tmp_path: Path) -> None:
    path = tmp_path / "rigs.toml"
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
    path = tmp_path / "rigs.toml"
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
    path = tmp_path / "rigs.toml"
    prefab, extended = _prefab(), _extended()
    save_registry(path, {prefab.name: prefab, extended.name: extended})

    loaded = load_registry(path)
    assert loaded == {prefab.name: prefab, extended.name: extended}


def test_names_with_slash_and_colon_survive_as_keys(tmp_path: Path) -> None:
    path = tmp_path / "rigs.toml"
    rec = RigRecord(name="corp/base:1.0", kind="prefab")
    save_registry(path, {rec.name: rec})

    text = path.read_text()
    assert '[rigs."corp/base:1.0"]' in text
    assert "corp/base:1.0" in load_registry(path)


def test_none_fields_are_not_written(tmp_path: Path) -> None:
    path = tmp_path / "rigs.toml"
    # Only name + kind set; everything else defaults to None.
    rec = RigRecord(name="bare", kind="extended")
    save_registry(path, {rec.name: rec})

    text = path.read_text()
    assert "kind = " in text
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
        assert f"{none_field} =" not in text, none_field
    # name is the key, not stored inside the table.
    assert "name =" not in text

    got = load_registry(path)["bare"]
    assert got == rec
    assert got.source is None


def test_remove_absent_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "rigs.toml"
    save_registry(path, {})
    assert remove(path, "ghost") is False


def test_remove_present_returns_true_and_deletes(tmp_path: Path) -> None:
    path = tmp_path / "rigs.toml"
    a, b = _prefab(), _extended()
    save_registry(path, {a.name: a, b.name: b})

    assert remove(path, a.name) is True
    loaded = load_registry(path)
    assert a.name not in loaded
    assert b.name in loaded


def test_upsert_adds_then_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "rigs.toml"

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
    path = tmp_path / "rigs.toml"
    existing = _prefab()
    upsert(path, existing)
    upsert(path, _extended())

    loaded = load_registry(path)
    assert set(loaded) == {"corp/base:1.0", "myhack"}


def test_get_present_and_absent(tmp_path: Path) -> None:
    path = tmp_path / "rigs.toml"
    rec = _prefab()
    save_registry(path, {rec.name: rec})

    assert get(path, rec.name) == rec
    assert get(path, "missing") is None


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "rigs.toml"
    rec = _prefab()
    save_registry(path, {rec.name: rec})
    assert path.exists()
    assert get(path, rec.name) == rec

"""Tests for kanibako.runtime.rig_meta (in-image extended-rig metadata)."""

from __future__ import annotations

import pytest
import yaml

from kanibako.runtime.rig_meta import (
    RigMeta,
    dump_rig_meta,
    load_rig_meta,
    write_rig_meta,
)


def test_round_trip_full():
    meta = RigMeta(
        name="myhack",
        parent="kanibako-oci:latest",
        foundation_source="prefab:oci",
        created="2026-06-04T00:00:00Z",
    )
    text = dump_rig_meta(meta)
    loaded = load_rig_meta(text)
    assert loaded == meta


def test_reproducible_defaults_false():
    meta = RigMeta(name="myhack")
    assert meta.reproducible is False
    loaded = load_rig_meta(dump_rig_meta(meta))
    assert loaded.reproducible is False


def test_recipe_none_omitted_from_yaml():
    meta = RigMeta(name="myhack")
    text = dump_rig_meta(meta)
    assert "recipe" not in text
    # name / kind / reproducible are always present.
    assert "name" in text
    assert "kind" in text
    assert "reproducible" in text


def test_optional_none_fields_omitted():
    meta = RigMeta(name="myhack")
    data = yaml.safe_load(dump_rig_meta(meta))
    assert "parent" not in data
    assert "foundation_source" not in data
    assert "created" not in data
    assert "recipe" not in data


def test_recipe_round_trips():
    meta = RigMeta(
        name="myhack",
        recipe=["apt-get install -y foo", "pip install bar"],
    )
    text = dump_rig_meta(meta)
    assert "recipe" in text
    loaded = load_rig_meta(text)
    assert loaded.recipe == ["apt-get install -y foo", "pip install bar"]
    assert loaded == meta


def test_load_accepts_raw_string():
    text = "name: myhack\nkind: extended\nreproducible: false\n"
    meta = load_rig_meta(text)
    assert meta.name == "myhack"
    assert meta.kind == "extended"
    assert meta.reproducible is False


def test_load_accepts_path(tmp_path):
    meta = RigMeta(name="myhack", parent="base:latest")
    path = tmp_path / "rig.yaml"
    write_rig_meta(meta, path)
    assert load_rig_meta(path) == meta


def test_write_creates_parent_dirs(tmp_path):
    meta = RigMeta(name="myhack")
    path = tmp_path / "etc" / "kanibako" / "rig.yaml"
    write_rig_meta(meta, path)
    assert path.is_file()
    assert load_rig_meta(path) == meta


def test_missing_name_raises():
    with pytest.raises(ValueError):
        load_rig_meta("kind: extended\nreproducible: false\n")


def test_repeated_name_raises_naming_the_key_and_both_lines():
    """``load_rig_meta("name: a\\nname: b\\n").name`` used to answer ``'b'`` — the first
    name vanished with no message. Spec §0: an undeclared reading is an ERROR that NAMES
    the offending key, never a silent accept of the second value over the first."""
    with pytest.raises(ValueError) as exc:
        load_rig_meta("name: a\nname: b\n")
    assert str(exc.value) == "rig.yaml sets 'name' twice (line 1 and line 2)"


def test_repeated_key_nested_in_a_flow_map_raises():
    with pytest.raises(ValueError) as exc:
        load_rig_meta("name: myhack\nparent: {a: 1, a: 2}\n")
    assert str(exc.value) == (
        "rig.yaml sets 'parent.a' twice (line 2, column 10 and line 2, column 16)"
    )


def test_invalid_yaml_raises_value_error_naming_the_file():
    """A raw ``yaml`` error used to escape this function; its callers catch ``ValueError``,
    so the contract was already broken — an unparseable rig.yaml was a traceback."""
    with pytest.raises(ValueError) as exc:
        load_rig_meta("name: [a\n")
    assert str(exc.value).startswith("rig.yaml ")
    assert "expected" in str(exc.value)


def test_a_self_referential_anchor_raises_value_error():
    with pytest.raises(ValueError) as exc:
        load_rig_meta("name: &a {x: *a}\n")
    assert str(exc.value) == (
        "rig.yaml refers to itself at 'name.x' (a YAML alias inside its own anchor)"
    )


def test_path_source_reports_rig_yaml_too(tmp_path):
    """The refusal names ``rig.yaml`` for a file source as well as a raw string."""
    path = tmp_path / "rig.yaml"
    path.write_text("name: a\nname: b\n")

    with pytest.raises(ValueError) as exc:
        load_rig_meta(path)
    assert str(exc.value) == "rig.yaml sets 'name' twice (line 1 and line 2)"


def test_empty_document_raises():
    with pytest.raises(ValueError):
        load_rig_meta("")


def test_non_mapping_document_raises():
    with pytest.raises(ValueError):
        load_rig_meta("- just\n- a\n- list\n")


def test_unknown_keys_ignored():
    text = (
        "name: myhack\n"
        "kind: extended\n"
        "reproducible: false\n"
        "bogus_field: 123\n"
        "another: hello\n"
    )
    meta = load_rig_meta(text)
    assert meta == RigMeta(name="myhack")
    assert not hasattr(meta, "bogus_field")


def test_dump_field_order_stable():
    meta = RigMeta(
        name="myhack",
        parent="base:latest",
        foundation_source="prefab:oci",
        created="2026-06-04T00:00:00Z",
    )
    lines = [
        line.split(":", 1)[0]
        for line in dump_rig_meta(meta).splitlines()
        if line and not line.startswith(" ")
    ]
    assert lines == [
        "name",
        "kind",
        "parent",
        "foundation_source",
        "reproducible",
        "created",
    ]

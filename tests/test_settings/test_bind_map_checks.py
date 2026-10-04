"""The dest-keyed map checks live in ONE checker, and EVERY reader runs it (spec §2a).

Pins three things the map's own reader and the agent-file reader once disagreed on: a
map spelled two ways for ONE destination is REFUSED naming BOTH spellings rather than
resolved last-wins; the per-entry refusals (the retired sub-table, the entry arity,
the unrooted source) are the SAME refusals whichever reader meets them — because
:mod:`kanibako.settings.settings_resolve` owns them, not a reader; and EVERY top-level
table the agent file reads is walked, the contained-scope ones included.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from kanibako.settings.agent_file import _CONTRIBUTED, _contribution, level_table
from kanibako.settings.config_io import load_doc
from kanibako.settings.settings_assemble import (
    _parse_marker_map,
    _parse_naming_file,
    parse_bind_map,
)
from kanibako.settings.settings_resolve import SettingsError

BIND_RO = "bindings.ro"

# EVERY top-level table the agent file contributes and the cascade reads: its own root,
# its ``agent:`` table and the scopes it CONTAINS. Each is a scope's own table, so each
# holds categories at its first level — the ``agent:`` table one level deeper, under the
# node name. Built from the reader's own contributed set, so a table added there shows up
# as a NEW CASE here rather than as silence in a walk. Sorted: a set's order varies per run.
_SCOPE_TABLES: tuple[str, ...] = tuple(sorted(_CONTRIBUTED))


def _agent_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "agents" / "claude" / "agent.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _raw(body: str) -> dict:
    return yaml.safe_load(body)


def _read(path: Path) -> None:
    """The ONE reader: ``level_table`` is the agent file's single read seam (Q85/Q92)."""
    level_table(load_doc(path), sub_key="claude", node="claude", path=path)


def _under(table: str, category: str, entry: str) -> str:
    """*entry* under *category*, under the top-level *table* — the one level each differs by."""
    lines = [f"{table}:"]
    if table == "agent":
        lines.append("  claude:")
        pad = "    "
    else:
        pad = "  "
    lines.append(f"{pad}{category}:")
    lines.extend(f"{pad}  {line}" for line in entry.splitlines())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# TWO SPELLINGS OF ONE DESTINATION — refused, naming BOTH
# ---------------------------------------------------------------------------
# The spellings that collapse to one guest path under normalize_bind_dest: ~ and ~/,
# a repeated /, a . or .. segment, and a trailing /.


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("/opt/x", "/opt//x/"),
        ("/opt/x", "/opt/./x"),
        ("/opt/x", "/opt/y/../x"),
        ("/opt/x", "/opt/x/"),
        ("~/x", "~/x/"),
    ],
)
def test_two_spellings_of_one_dest_refuse_naming_both(first: str, second: str) -> None:
    with pytest.raises(SettingsError) as exc:
        parse_bind_map({first: ["/src/a"], second: ["/src/b"]}, category=BIND_RO)
    message = str(exc.value)
    assert first in message
    assert second in message


def test_two_spellings_of_one_dest_refuse_in_a_marker_map() -> None:
    with pytest.raises(SettingsError) as exc:
        _parse_marker_map({"/opt/m": "x", "/opt//m/": "y"}, path=("masks",))
    assert "/opt/m" in str(exc.value)
    assert "/opt//m/" in str(exc.value)


def test_one_dest_spelled_once_is_not_a_refusal() -> None:
    store = parse_bind_map({"/opt/x": ["/src/a"]}, category=BIND_RO)
    assert len(store) == 1


def test_distinct_dests_are_not_a_refusal() -> None:
    store = parse_bind_map({"/opt/x": ["/src/a"], "/opt/y": ["/src/b"]}, category=BIND_RO)
    assert len(store) == 2


def test_a_null_entry_at_one_dest_is_legal() -> None:
    """A present-``None`` entry UNSETS just that destination, so it is not a malformed entry."""
    store = parse_bind_map({"/opt/x": None}, category=BIND_RO)
    assert len(store) == 1
    assert store["/opt/x"] is None


def test_a_value_that_is_not_an_entry_is_left_to_the_store(tmp_path: Path) -> None:
    """The checker judges an entry where the PARSE unpacks one, so both readers agree.

    A dest-keyed map's value that is neither a sub-table nor a list is stored as it is by
    the settings tier, and the agent-file reader must return the SAME verdict — a check that
    refused it here would make that reader stricter than the tier, not equal to it.
    """
    body = "self:\n  caches:\n    /opt/c: y\n"
    _read(_agent_file(tmp_path, body))
    assert dict.get(parse_bind_map({"/opt/c": "y"}, category="caches"), "/opt/c") == "y"


def test_two_spellings_of_one_dest_refuse_at_the_agent_file_read(tmp_path: Path) -> None:
    body = "self:\n  bindings:\n    ro:\n      /opt/x: [/src/a]\n      /opt//x/: [/src/b]\n"
    with pytest.raises(SettingsError) as exc:
        _read(_agent_file(tmp_path, body))
    message = str(exc.value)
    assert "/opt/x" in message
    assert "/opt//x/" in message
    assert str(tmp_path) in message


# ---------------------------------------------------------------------------
# THE THREE MALFORMED ENTRIES — refused at the agent-file READ, not only at launch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "body", "expected"),
    [
        (
            "retired sub-table",
            "self:\n  bindings:\n    ro:\n      /opt/x: {q: 1}\n",
            "holds a sub-table",
        ),
        (
            "three elements",
            "self:\n  bindings:\n    ro:\n      /opt/y: [/src, opts, extra]\n",
            "must have 1 or 2 elements",
        ),
        (
            "bare relative source",
            "self:\n  bindings:\n    ro:\n      /opt/z: [relative/src]\n",
            "bare-relative host source",
        ),
    ],
)
def test_malformed_bind_entry_refuses_the_agent_file_read(
    tmp_path: Path, name: str, body: str, expected: str
) -> None:
    path = _agent_file(tmp_path, body)
    with pytest.raises(SettingsError) as exc:
        _read(path)
    assert expected in str(exc.value)


def test_the_three_element_refusal_names_the_category_and_the_destination(
    tmp_path: Path,
) -> None:
    """Keyspec §2a: the stale-shape refusal is "an ERROR naming the category and the entry".

    The unpacker alone names neither, so the category and the destination are the CHECKER's
    to add — and the file is named too, so the reader that meets the shape is identifiable.
    """
    body = "self:\n  bindings:\n    ro:\n      /opt/y: [/src, opts, extra]\n"
    path = _agent_file(tmp_path, body)
    with pytest.raises(SettingsError) as exc:
        _read(path)
    message = str(exc.value)
    assert BIND_RO in message
    assert "/opt/y" in message
    assert str(path) in message


def test_the_three_element_refusal_from_the_settings_tier_names_them_too() -> None:
    """The settings tier and the agent file raise the SAME refusal, context and all."""
    with pytest.raises(SettingsError) as exc:
        parse_bind_map({"/opt/y": ["/src", "opts", "extra"]}, category=BIND_RO)
    message = str(exc.value)
    assert BIND_RO in message
    assert "/opt/y" in message


def test_malformed_bind_entry_refuses_contribution_too(tmp_path: Path) -> None:
    """``_contribution`` is the ONE seam every reader of the file comes through."""
    body = "self:\n  bindings:\n    ro:\n      /opt/x: {q: 1}\n"
    with pytest.raises(SettingsError) as exc:
        _contribution(_raw(body), node="claude", path=_agent_file(tmp_path, body))
    assert "holds a sub-table" in str(exc.value)


def test_a_bare_relative_source_in_an_abstract_category_is_not_judged(tmp_path: Path) -> None:
    """``caches`` is ABSTRACT: its root comes from the key path, so the source is not refused."""
    body = "self:\n  caches:\n    /opt/c: [relative/src]\n"
    _read(_agent_file(tmp_path, body))


def test_an_agent_file_two_spellings_under_the_scope_table_refuses(tmp_path: Path) -> None:
    body = (
        "agent:\n  claude:\n    bindings:\n      ro:\n"
        "        /opt/x: [/src/a]\n        /opt//x/: [/src/b]\n"
    )
    with pytest.raises(SettingsError) as exc:
        _read(_agent_file(tmp_path, body))
    assert "/opt//x/" in str(exc.value)


def test_an_agent_file_masks_two_spellings_refuses(tmp_path: Path) -> None:
    body = "self:\n  masks:\n    /opt/m: x\n    /opt//m/: y\n"
    with pytest.raises(SettingsError) as exc:
        _read(_agent_file(tmp_path, body))
    assert "/opt//m/" in str(exc.value)


def test_a_table_valued_agent_key_is_not_read_as_a_dest_keyed_map(tmp_path: Path) -> None:
    """``transform_settings`` is ONE setting, whole — its contents are not a category map."""
    body = "self:\n  transform_settings:\n    masks:\n      /opt/m: x\n"
    _read(_agent_file(tmp_path, body))


# ---------------------------------------------------------------------------
# EVERY TOP-LEVEL TABLE — the contained scopes are read, so they are judged too
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", _SCOPE_TABLES)
def test_two_spellings_refuse_in_every_top_level_table(tmp_path: Path, table: str) -> None:
    body = _under(table, "bindings", "ro:\n  /opt/x: [/src/a]\n  /opt//x/: [/src/b]")
    path = _agent_file(tmp_path, body)
    with pytest.raises(SettingsError) as exc:
        _read(path)
    message = str(exc.value)
    assert BIND_RO in message
    assert "/opt/x" in message and "/opt//x/" in message
    assert str(path) in message


@pytest.mark.parametrize("table", _SCOPE_TABLES)
def test_a_three_element_entry_refuses_in_every_top_level_table(
    tmp_path: Path, table: str
) -> None:
    body = _under(table, "bindings", "ro:\n  /opt/y: [/src, opts, extra]")
    path = _agent_file(tmp_path, body)
    with pytest.raises(SettingsError) as exc:
        _read(path)
    message = str(exc.value)
    assert BIND_RO in message
    assert "/opt/y" in message
    assert str(path) in message


@pytest.mark.parametrize("table", _SCOPE_TABLES)
def test_a_contained_sub_dict_refuses_in_every_top_level_table(
    tmp_path: Path, table: str
) -> None:
    body = _under(table, "bindings", "ro:\n  /opt/x: {q: 1}")
    path = _agent_file(tmp_path, body)
    with pytest.raises(SettingsError) as exc:
        _read(path)
    message = str(exc.value)
    assert BIND_RO in message
    assert "holds a sub-table" in message
    assert str(path) in message


@pytest.mark.parametrize("table", _SCOPE_TABLES)
def test_a_masks_map_two_spellings_refuses_in_every_top_level_table(
    tmp_path: Path, table: str
) -> None:
    body = _under(table, "masks", "/opt/m: x\n/opt//m/: y")
    with pytest.raises(SettingsError) as exc:
        _read(_agent_file(tmp_path, body))
    assert "/opt//m/" in str(exc.value)


@pytest.mark.parametrize("table", _SCOPE_TABLES)
def test_a_bare_relative_source_refuses_in_every_top_level_table(
    tmp_path: Path, table: str
) -> None:
    body = _under(table, "bindings", "ro:\n  /opt/z: [relative/src]")
    with pytest.raises(SettingsError) as exc:
        _read(_agent_file(tmp_path, body))
    assert "bare-relative host source" in str(exc.value)


@pytest.mark.parametrize("table", _SCOPE_TABLES)
def test_a_valid_map_reads_in_every_top_level_table(tmp_path: Path, table: str) -> None:
    """The CONTROL for the five refusals above: the same shape, well spelled, reads.

    A checker that refused a legal map in one of the four tables would make that reader
    stricter than the settings tier, which stores exactly this shape unjudged.
    """
    body = _under(table, "bindings", "ro:\n  /opt/x: [/src/a]\n  rw:\n  /opt/y: [/src/b, z]")
    _read(_agent_file(tmp_path, body))


# ---------------------------------------------------------------------------
# A SCALAR WHERE THE CATEGORY'S TABLE GOES — refused, naming the key and the file
# ---------------------------------------------------------------------------
# Every category is TERMINAL, so the category token IS the key and its value is the
# map. §0 admits nothing else there: a passthrough stores a value no reader can apply.
# A present-``None`` is the ONE non-map value a category takes (spec §2h's OMIT), and it
# is pinned here too — one shape, one verdict, and the verdict has both sides.
#
# ⚑ THE SETTINGS TIER'S PARSE ONLY. The agent file's read is a DIFFERENT reader that
# CONCEDES a wrong shape on purpose, so that a broken file's repair verbs stay reachable
# (``test_agent_file.py::TestLoadSurvivesAMalformedTable``); that ruling is recorded in
# ``settings_resolve._check_node_binds`` and is untouched here.


def _settings_file(tmp_path: Path, doc: dict, name: str = "settings.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


@pytest.mark.parametrize("scope", ["system", "workset", "box"])
@pytest.mark.parametrize(
    ("category", "value"),
    [("caches", "5"), ("caches", "x"), ("masks", "5")],
)
def test_a_scalar_at_a_category_refuses_in_a_settings_file(
    tmp_path: Path, scope: str, category: str, value: str,
) -> None:
    path = _settings_file(tmp_path, {scope: {category: _scalar(value)}})
    with pytest.raises(SettingsError) as exc:
        _parse_naming_file({scope: {category: _scalar(value)}}, file_path=path)
    message = str(exc.value)
    assert f"'{scope}.{category}'" in message   # the DISCRIMINATED key, scope-qualified
    assert str(path) in message                 # and the file to edit


def _scalar(value: str) -> Any:
    return int(value) if value.isdigit() else value


@pytest.mark.parametrize(
    ("category", "example"),
    [("caches", "{box_dest: [src[, options]]}"), ("masks", "{box_dest: true}")],
)
def test_the_cure_is_printed_in_the_shape_that_category_takes(
    tmp_path: Path, category: str, example: str,
) -> None:
    """A mask has no source, so the bind example would send the reader somewhere wrong."""
    path = _settings_file(tmp_path, {"system": {category: 5}})
    with pytest.raises(SettingsError) as exc:
        _parse_naming_file({"system": {category: 5}}, file_path=path)
    assert example in str(exc.value)


@pytest.mark.parametrize("scope", ["system", "workset", "box"])
@pytest.mark.parametrize("category", ["caches", "masks"])
def test_a_null_category_is_accepted_in_a_settings_file(
    tmp_path: Path, scope: str, category: str,
) -> None:
    """⚑ THE OTHER SIDE OF THE SAME VERDICT: a bare ``<category>:`` is spec §2h's OMIT."""
    path = _settings_file(tmp_path, {scope: {category: None}})
    store = _parse_naming_file({scope: {category: None}}, file_path=path)
    assert store[scope][category] is None


@pytest.mark.parametrize("scope", ["system", "workset", "box"])
def test_a_caches_map_reads_in_a_settings_file(tmp_path: Path, scope: str) -> None:
    """The CONTROL: a legal dest-keyed map under a leaf category reads, and is not judged."""
    doc = {scope: {"caches": {"/opt/x": ["/src/a"]}}}
    _parse_naming_file(doc, file_path=_settings_file(tmp_path, doc))


def test_a_transform_settings_table_reads_in_a_settings_file(tmp_path: Path) -> None:
    doc = {"agent": {"claude": {"transform_settings": {"a": 1}}}}
    _parse_naming_file(doc, file_path=_settings_file(tmp_path, doc))


def test_a_scalar_at_a_table_valued_agent_leaf_refuses_in_a_settings_file(
    tmp_path: Path,
) -> None:
    """``transform_settings`` is spec §2d, a table-valued agent LEAF, not a §2a category.

    No category walk reaches it, so the parse judges it against the table-valued set —
    the same rule and the same words a category gets.
    """
    doc = {"agent": {"claude": {"transform_settings": 5}}}
    path = _settings_file(tmp_path, doc)
    with pytest.raises(SettingsError) as exc:
        _parse_naming_file(doc, file_path=path)
    message = str(exc.value)
    assert "'agent.claude.transform_settings'" in message
    assert str(path) in message


def test_a_null_table_valued_agent_leaf_is_accepted_in_a_settings_file(tmp_path: Path) -> None:
    doc = {"agent": {"claude": {"transform_settings": None}}}
    store = _parse_naming_file(doc, file_path=_settings_file(tmp_path, doc))
    assert store["agent"]["claude"]["transform_settings"] is None


def test_a_scalar_that_merely_ENDS_in_a_category_token_is_not_a_category(
    tmp_path: Path,
) -> None:
    """``system.channels.common`` is a path scalar; ``.channels.`` is the discriminator.

    Spec §2a gives one word to two senses, so the deep walk needs the POSITION, not the
    token — otherwise a declared path key would start refusing on its name alone.
    """
    doc = {"system": {"channels": {"common": "/tmp/x"}}}
    store = _parse_naming_file(doc, file_path=_settings_file(tmp_path, doc))
    assert store["system"]["channels"]["common"] == "/tmp/x"


# ---------------------------------------------------------------------------
# ONE CHECKER — the refusals are the same object whichever reader raises them
# ---------------------------------------------------------------------------


def test_the_checker_is_the_one_the_settings_tier_calls() -> None:
    """The refusal text is identical from the shared checker and from the map reader."""
    from kanibako.settings.settings_resolve import check_bind_map

    raw = {"/opt/x": ["/src/a"], "/opt//x/": ["/src/b"]}
    with pytest.raises(SettingsError) as via_checker:
        check_bind_map(raw, category=BIND_RO)
    with pytest.raises(SettingsError) as via_reader:
        parse_bind_map(raw, category=BIND_RO)
    assert str(via_checker.value) == str(via_reader.value)


def test_the_checker_names_the_file_when_the_reader_has_one(tmp_path: Path) -> None:
    from kanibako.settings.settings_resolve import check_bind_map

    path = _agent_file(tmp_path, "self:\n  bindings:\n    ro: {}\n")
    with pytest.raises(SettingsError) as exc:
        check_bind_map(
            {"/opt/x": ["/src/a"], "/opt//x/": ["/src/b"]}, category=BIND_RO, where=str(path)
        )
    assert str(path) in str(exc.value)


def test_refuse_dest_spelled_twice_is_the_per_map_check_on_its_own() -> None:
    from kanibako.settings.settings_resolve import refuse_dest_spelled_twice

    refuse_dest_spelled_twice({"/opt/x": 1, "/opt/y": 2}, category="masks")
    with pytest.raises(SettingsError):
        refuse_dest_spelled_twice({"/opt/x": 1, "/opt/x/": 2}, category="masks")

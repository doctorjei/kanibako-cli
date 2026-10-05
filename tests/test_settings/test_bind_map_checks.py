"""The dest-keyed map checks live in ONE checker, and EVERY reader runs it (spec §2a).

Pins three things the map's own reader and the agent-file reader once disagreed on: a
map spelled two ways for ONE destination is REFUSED naming BOTH spellings rather than
resolved last-wins; the per-entry refusals (the retired sub-table, a value that is not
a list, the entry arity, the unrooted source) are the SAME refusals whichever reader
meets them — because
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
    _file_partial,
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


def test_a_value_that_is_not_an_entry_refuses_the_same_in_both_readers(tmp_path: Path) -> None:
    """An entry is a LIST, and both readers refuse a value that is not one — the same words.

    Keyspec §2a gives the entry a list form and no bare-scalar shorthand, so a scalar
    under a dest key is a malformed entry whichever reader meets the same bytes. The
    settings-file tier and the agent-file reader therefore raise one message, and the
    reader that holds a file appends it: a check that judged the shape in one reader and
    not the other would leave the two disagreeing about the same input.
    """
    with pytest.raises(SettingsError) as exc:
        parse_bind_map({"/opt/c": "y"}, category="caches")
    tier_msg = str(exc.value)
    assert "caches entry '/opt/c'" in tier_msg
    assert "got str: 'y'" in tier_msg
    path = _agent_file(tmp_path, "self:\n  caches:\n    /opt/c: y\n")
    with pytest.raises(SettingsError) as exc:
        _read(path)
    assert str(exc.value) == f"{tier_msg} (in settings file {path})"


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


@pytest.mark.parametrize("doc", [
    {"system": {"env": {"transform_settings": "1"}}},
    {"agent": {"claude": {"env": {"transform_settings": "1"}}}},
    {"agent": {"claude": {"secret_path": {"transform_settings": "/x"}}}},
    {"agent": {"claude": {"transform_settings": {"transform_settings": 5}}}},
    {"agent": {"claude": {"transform_settings": {"a": {"transform_settings": 5}}}}},
])
def test_the_leaf_s_name_away_from_its_position_is_data(tmp_path: Path, doc: dict) -> None:
    """Only ``agent.<node>.transform_settings`` is the leaf: the same word as a family's
    entry or inside the table's own payload is DATA, and a scalar there is legal."""
    _parse_naming_file(doc, file_path=_settings_file(tmp_path, doc))


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


# ---------------------------------------------------------------------------
# THE AGENT SEGMENT IS JUDGED BEFORE THE ENTRY SHAPE
# ---------------------------------------------------------------------------
# A ``pref.agent.<name>.<category>`` map reaches this module's checks on TWO paths. The
# DEFERRED one withholds the shape verdict for ``apply_prefs`` to carry; the NON-DEFERRED
# one is the settings tier's own bind parse, which has no consumer to defer to and is
# therefore the last reader. On that second path the agent NAME is judged FIRST and by
# ``settings_prefs.agent_segment_reason`` — the same judgment the deferred path reaches —
# so an entry whose only defect is its agent segment is never reported as a shape fault.
#
# ``_file_partial`` is the reader under test: the settings tier's own builder, so these
# rows also pin the SEGMENT EXTRACTION, not just the check that consumes it.

#: Both bind-map spellings, as ``(id, category)`` — the arm arrives at the same checker
#: spelled ``bindings.ro``, so the two rows are two key PATHS, not two categories.
_BIND_SPELLINGS: tuple[tuple[str, str], ...] = (("terminal-category", "seeded"), ("arm", BIND_RO))


def _pref_agent_doc(name: str, category: str, entry: Any) -> dict:
    """``pref:`` → ``agent:`` → *name* → *category*, as one of the two bind-map spellings.

    A dotted ``bindings.ro`` KEY is not a spelling — §0 refuses a dotted name in a settings
    file — so the arm is nested one level per segment, the way a file spells it.
    """
    node: dict = {"EX": entry}
    for part in reversed(category.split(".")):
        node = {part: node}
    return {"pref": {"agent": {name: node}}}


@pytest.mark.parametrize(("spelling", "category"), _BIND_SPELLINGS)
def test_a_bogus_agent_name_is_refused_before_the_entry_shape_is(
    tmp_path: Path, spelling: str, category: str,
) -> None:
    """The agent NAME is the whole defect, so the refusal names the agent — not the shape.

    Keyspec §2a gives a dest-keyed entry a list form, so ``EX: /src/a`` is a malformed
    entry on either reading. Under a BOGUS agent the name is the only thing to fix, and a
    shape verdict would send the user to reshape an entry that is already right.

    INVERT: judge the shape first (or not at all) -> the shape message is back and this
    row goes red on ``not a valid agent``.
    """
    doc = _pref_agent_doc("zippity", category, "/src/a")
    path = _settings_file(tmp_path, doc)
    with pytest.raises(SettingsError) as exc:
        _file_partial(doc, path=path)
    message = str(exc.value)
    assert "it names agent 'zippity', which is not a valid agent" in message
    assert "not a valid agent" in message
    assert "structured entry" not in message          # NOT the shape verdict
    assert "holds a sub-table" not in message
    assert str(path) in message                        # ...and the file to edit


@pytest.mark.parametrize(("spelling", "category"), _BIND_SPELLINGS)
def test_a_valid_agents_entry_keeps_its_shape_refusal(
    tmp_path: Path, spelling: str, category: str,
) -> None:
    """THE OTHER SIDE, and the one that fails silently: a VALID agent is not refused here.

    Judging the name ahead of the shape must not over-refuse — a valid agent's malformed
    entry is still a shape fault, and calling it a bad agent name would send the user to
    rename a correct agent. This is the guard for that.

    INVERT: judge the name for every agent, valid or not -> this row goes red because the
    shape message is gone.
    """
    doc = _pref_agent_doc("claude", category, "/src/a")
    path = _settings_file(tmp_path, doc)
    with pytest.raises(SettingsError) as exc:
        _file_partial(doc, path=path)
    message = str(exc.value)
    assert "Dest-keyed binding entry must be a structured entry" in message
    assert "not a valid agent" not in message
    assert category in message
    assert str(path) in message


@pytest.mark.parametrize(("spelling", "category"), _BIND_SPELLINGS)
def test_a_valid_agents_retired_sub_table_keeps_its_parse_time_refusal(
    tmp_path: Path, spelling: str, category: str,
) -> None:
    """Keyspec §2a's RETIRED name-keyed shape is refused at PARSE time under a valid agent.

    The name verdict runs ahead of every shape, so this row pins that "ahead of" does not
    become "instead of": the shape a valid agent's entry earns is still raised by this
    parse, not left to a later door.

    INVERT: raise the name verdict for every agent, or skip the shape checks entirely once
    a ``pref.agent.`` segment is present -> this row goes red.
    """
    doc = _pref_agent_doc("claude", category, {"old_name": {"src": "/s"}})
    path = _settings_file(tmp_path, doc)
    with pytest.raises(SettingsError) as exc:
        _file_partial(doc, path=path)
    message = str(exc.value)
    assert "holds a sub-table" in message
    assert "not a valid agent" not in message


@pytest.mark.parametrize("reserved", ["default", "shell"])
def test_a_reserved_pseudo_agent_name_is_not_named_as_a_bad_agent(
    tmp_path: Path, reserved: str,
) -> None:
    """The reserved PSEUDO-AGENT tier is legal here, so the name verdict must DECLINE it.

    ``agent_ref`` reserves ``default`` and ``shell`` against an agent, a persona AND a
    harness, and :func:`settings_keyspace.is_valid_agent_segment` admits them as
    ``agent.<HERE>`` discriminators. A refusal that named one of them would refuse a
    spelling the keyspace calls valid, and the entry's own shape is the only real defect.

    INVERT: judge the segment against the discovered harnesses alone, dropping the
    pseudo-agent tier -> this row goes red on ``not a valid agent``.
    """
    doc = _pref_agent_doc(reserved, "seeded", "/src/a")
    path = _settings_file(tmp_path, doc)
    with pytest.raises(SettingsError) as exc:
        _file_partial(doc, path=path)
    message = str(exc.value)
    assert "Dest-keyed binding entry must be a structured entry" in message
    assert "not a valid agent" not in message


@pytest.mark.parametrize(("spelling", "category"), _BIND_SPELLINGS)
def test_the_deferred_read_withholds_the_name_verdict_unchanged(
    tmp_path: Path, spelling: str, category: str,
) -> None:
    """The DEFERRED read keeps its consumer's verdict, so this parse raises NOTHING here.

    A ``pref:`` table is parsed without ``valid_agents`` on the pref-requests path, and its
    consumer judges the agent segment in order. Extending the name check to that path would
    duplicate one judgment in two places and refuse before the consumer ever runs.

    INVERT: run the name check on the deferred path too -> this row goes red, because the
    parse starts raising instead of returning a store.
    """
    doc = _pref_agent_doc("zippity", category, "/src/a")
    store = _file_partial(doc, path=_settings_file(tmp_path, doc), for_pref_requests=True)
    assert store["pref"]["agent"]["zippity"] is not None


@pytest.mark.parametrize("doc", [
    # THE AGENT TIER'S OWN MAP: an ``agent.<node>`` bind map that never passes through
    # ``pref.agent.`` and therefore carries no agent segment to judge.
    {"agent": {"claude": {"seeded": {"EX": ["/src/a"]}}}},
    {"agent": {"claude": {"bindings": {"ro": {"EX": ["/src/a"]}}}}},
    # A scope's own dest-keyed category, one level shallower than any agent segment.
    {"box": {"caches": {"EX": ["/src/a"]}}},
    # ``pref.system.agent`` — a ``pref:`` table that is not the agent discriminator. Its
    # VALUE names an agent; §2h validates the target KEY, so there is no segment to judge.
    {"pref": {"system": {"agent": "claude"}}},
])
def test_a_map_outside_pref_agent_reaches_no_agent_verdict(tmp_path: Path, doc: dict) -> None:
    """The name verdict is reached ONLY for a map already under ``pref.agent.``.

    Every other bind map is judged for shape alone. This row is the boundary of the check:
    without it, discovery would be reached by parses that never named an agent at all.

    INVERT: judge the segment for every map -> the ``agent.<node>.bindings.<arm>`` row goes
    red, naming a name this map never wrote.
    """
    store = _file_partial(doc, path=_settings_file(tmp_path, doc))
    assert len(store) == 1

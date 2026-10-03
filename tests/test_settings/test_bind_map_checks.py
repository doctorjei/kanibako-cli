"""The dest-keyed map checks live in ONE checker, and EVERY reader runs it (spec §2a).

Pins two things the map's own reader and the agent-file reader once disagreed on: a
map spelled two ways for ONE destination is REFUSED naming BOTH spellings rather than
resolved last-wins, and the per-entry refusals (the retired sub-table, the entry arity,
the unrooted source) are the SAME refusals whichever reader meets them — because
:mod:`kanibako.settings.settings_resolve` owns them, not a reader.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.settings.agent_file import _contribution, load
from kanibako.settings.settings_assemble import _parse_marker_map, parse_bind_map
from kanibako.settings.settings_resolve import SettingsError

BIND_RO = "bindings.ro"


def _agent_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "agents" / "claude" / "agent.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _raw(body: str) -> dict:
    return yaml.safe_load(body)


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
    _contribution(_raw(body), node="claude", path=_agent_file(tmp_path, body))
    assert dict.get(parse_bind_map({"/opt/c": "y"}, category="caches"), "/opt/c") == "y"


def test_two_spellings_of_one_dest_refuse_at_the_agent_file_read(tmp_path: Path) -> None:
    body = "self:\n  bindings:\n    ro:\n      /opt/x: [/src/a]\n      /opt//x/: [/src/b]\n"
    with pytest.raises(SettingsError) as exc:
        load(_agent_file(tmp_path, body), node="claude")
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
        load(path, node="claude")
    assert expected in str(exc.value)


def test_malformed_bind_entry_refuses_contribution_too(tmp_path: Path) -> None:
    """``_contribution`` is the ONE seam every reader of the file comes through."""
    body = "self:\n  bindings:\n    ro:\n      /opt/x: {q: 1}\n"
    with pytest.raises(SettingsError) as exc:
        _contribution(_raw(body), node="claude", path=_agent_file(tmp_path, body))
    assert "holds a sub-table" in str(exc.value)


def test_a_bare_relative_source_in_an_abstract_category_is_not_judged(tmp_path: Path) -> None:
    """``caches`` is ABSTRACT: its root comes from the key path, so the source is not refused."""
    body = "self:\n  caches:\n    /opt/c: [relative/src]\n"
    _contribution(_raw(body), node="claude", path=_agent_file(tmp_path, body))


def test_an_agent_file_two_spellings_under_the_scope_table_refuses(tmp_path: Path) -> None:
    body = (
        "agent:\n  claude:\n    bindings:\n      ro:\n"
        "        /opt/x: [/src/a]\n        /opt//x/: [/src/b]\n"
    )
    with pytest.raises(SettingsError) as exc:
        _contribution(_raw(body), node="claude", path=_agent_file(tmp_path, body))
    assert "/opt//x/" in str(exc.value)


def test_an_agent_file_masks_two_spellings_refuses(tmp_path: Path) -> None:
    body = "self:\n  masks:\n    /opt/m: x\n    /opt//m/: y\n"
    with pytest.raises(SettingsError) as exc:
        _contribution(_raw(body), node="claude", path=_agent_file(tmp_path, body))
    assert "/opt//m/" in str(exc.value)


def test_a_table_valued_agent_key_is_not_read_as_a_dest_keyed_map(tmp_path: Path) -> None:
    """``transform_settings`` is ONE setting, whole — its contents are not a category map."""
    body = "self:\n  transform_settings:\n    masks:\n      /opt/m: x\n"
    _contribution(_raw(body), node="claude", path=_agent_file(tmp_path, body))


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

"""Unit tests for block 2a — cascade level assembly (settings_assemble).

Covers the brief's checklist: the 6-level count + MOST-SPECIFIC-FIRST order;
``agent.default`` vs ``agent.<active>`` land in the RIGHT separate levels (NOT
pre-merged), each under its TRUE discriminated §2d key (``agent.default.<key>`` /
``agent.<active-name>.<key>`` — NO bare-``agent`` collapse, spec §0/§2d);
binds become ``Bind`` with raw ``@``-refs / ``$vars``
/ ``~`` preserved (NOT expanded); ``masks`` is the keyed ``dict[box_dest →
bool|None]`` shape; absent files → empty ``KeyStore`` partials; the floor lands
on ``base``, the cascade ends at ``box`` (no ``required`` cap); NO ``machine``
path is consulted; partials are NESTED ``KeyStore``s keyed by the scope-QUALIFIED
keyspace (scope token kept, §0 namespace orthogonal to cascade); base uses the
SAME scoped keyspace as every other file (no synthetic ``base:`` wrapper).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.settings.settings_assemble import assemble_levels, parse_bind_map
from kanibako.settings.settings_merge import merge
from kanibako.settings.settings_resolve import SettingsError
from kanibako.settings.settings_store import _MISSING, Bind, BindEntry, KeyStore

# Index of each level in the returned MOST-SPECIFIC-FIRST list (S8).
BOX, WORKSET, AGENT_ACTIVE, AGENT_DEFAULT, SYSTEM, BASE = range(6)


def _write(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


# --------------------------------------------------------------------------- #
# Level count + order (S8)                                                     #
# --------------------------------------------------------------------------- #


def test_returns_six_levels_all_keystores() -> None:
    levels = assemble_levels(agent_name="claude")
    assert len(levels) == 6
    assert all(isinstance(lv, KeyStore) for lv in levels)


def test_order_is_most_specific_first(tmp_path: Path) -> None:
    # Each scope file sets a same-named scope-qualified scalar so we can read the
    # order back by value. Files are scope-ROOTED on disk; the partial keeps the
    # scope token, so the marker lives under e.g. box.marker / system.marker.
    box = _write(tmp_path / "box.yaml", {"box": {"marker": "box"}})
    ws = _write(tmp_path / "ws.yaml", {"workset": {"marker": "workset"}})
    sysf = _write(tmp_path / "sys.yaml", {"system": {"marker": "system"}})
    base = _write(tmp_path / "base.yaml", {"system": {"marker": "base"}})
    agent = _write(
        tmp_path / "agent.yaml",
        {"self": {"default": {"marker": "adef"}, "claude": {"marker": "aact"}}},
    )
    levels = assemble_levels(
        agent_name="claude",
        base_path=base,
        system_path=sysf,
        agent_path=agent,
        workset_path=ws,
        box_path=box,
    )

    def _marker(store: KeyStore, scope: str) -> object:
        sub = dict.get(store, scope, _MISSING)
        return dict.get(sub, "marker", _MISSING) if isinstance(sub, KeyStore) else _MISSING

    assert _marker(levels[BOX], "box") == "box"
    assert _marker(levels[WORKSET], "workset") == "workset"
    # The agent levels keep their TRUE discriminated key: the active level under
    # agent.claude.*, the default level under agent.default.* (NO bare-agent collapse).
    assert (
        dict.get(levels[AGENT_ACTIVE]["agent"]["claude"], "marker", _MISSING) == "aact"
    )
    assert (
        dict.get(levels[AGENT_DEFAULT]["agent"]["default"], "marker", _MISSING)
        == "adef"
    )
    assert _marker(levels[SYSTEM], "system") == "system"
    assert _marker(levels[BASE], "system") == "base"


# --------------------------------------------------------------------------- #
# Scope token KEPT — namespace orthogonal to cascade (§0)                      #
# --------------------------------------------------------------------------- #


def test_scope_token_kept_not_stripped(tmp_path: Path) -> None:
    box = _write(tmp_path / "box.yaml", {"box": {"image": "img"}})
    box_level = assemble_levels(agent_name="claude", box_path=box)[BOX]
    # The partial keeps the scope token: box.image, NOT a stripped `image`.
    assert isinstance(dict.get(box_level, "box"), KeyStore)
    assert dict.get(box_level["box"], "image", _MISSING) == "img"
    assert dict.get(box_level, "image", _MISSING) is _MISSING


def test_upward_scope_key_in_box_file_dropped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # §0 "Directional enforcement at RESOLVE" (Jei 2026-07-02, clause 4): a box
    # file may NOT set a CONTAINING scope's key — a top-level system: table is an
    # upward write, DROPPED at assembly with a warning naming the file + token; it
    # never enters the partial. (Supersedes the old "namespace orthogonal so a box
    # file MAY set system.*" pin — that upward direction is now forbidden.)
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"image": "img"}, "system": {"masks": {"/x": None}}},
    )
    with caplog.at_level("WARNING"):
        box_level = assemble_levels(agent_name="claude", box_path=box)[BOX]
    # The box's OWN-scope key survives; the upward system: table is GONE.
    assert dict.get(box_level["box"], "image", _MISSING) == "img"
    assert dict.get(box_level, "system", _MISSING) is _MISSING
    # The drop is announced (file path + dropped token), unconditionally.
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("system" in m and str(box) in m for m in warnings), warnings


def test_downward_scope_key_in_workset_file_preserved(tmp_path: Path) -> None:
    # §0 defaults-down: a workset file MAY set the box.* scope it CONTAINS — the
    # partial preserves it (the drop must NOT eat downward contributions). This
    # pins the direction the drop leaves untouched.
    ws = _write(
        tmp_path / "ws.yaml",
        {"workset": {"marker": "w"}, "box": {"masks": {"/x": None}}},
    )
    ws_level = assemble_levels(agent_name="claude", workset_path=ws)[WORKSET]
    assert dict.get(ws_level["workset"], "marker", _MISSING) == "w"
    assert isinstance(dict.get(ws_level, "box"), KeyStore)
    assert dict.get(ws_level["box"]["masks"], "/x", _MISSING) is None


# Every UPWARD (containing-scope-in-a-lower-file) direction the drop must kill.
# (path_kw, level_index, upward_token) — the file at `path_kw` carries a
# top-level `upward_token:` table naming a scope that CONTAINS the file's scope.
_UPWARD_CASES = [
    ("box_path", BOX, "system"),
    ("box_path", BOX, "workset"),
    ("box_path", BOX, "agent"),
    ("workset_path", WORKSET, "system"),
    ("workset_path", WORKSET, "agent"),
]


@pytest.mark.parametrize(("path_kw", "level_idx", "token"), _UPWARD_CASES)
def test_upward_scope_dropped_and_warned(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    path_kw: str,
    level_idx: int,
    token: str,
) -> None:
    # §0 clause 4: baseline-RED at 3e0eb9e (the upward key SURVIVED and could flip
    # the snapshot); GREEN here = the containing-scope table is DROPPED AND the
    # warning fired. BOTH asserted UNCONDITIONALLY (no vacuous pass): the own-scope
    # key must survive, the upward token must be absent, the warn must name it.
    own_scope = "box" if path_kw == "box_path" else "workset"
    f = _write(
        tmp_path / "f.yaml",
        {own_scope: {"marker": "keep"}, token: {"auth": {"share_allowed": False}}},
    )
    with caplog.at_level("WARNING"):
        level = assemble_levels(agent_name="claude", **{path_kw: f})[level_idx]
    # The own-scope contribution survives; the upward table is gone.
    assert dict.get(level[own_scope], "marker", _MISSING) == "keep"
    assert dict.get(level, token, _MISSING) is _MISSING
    # The drop is announced, naming the file and the dropped token.
    msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(token in m and str(f) in m for m in msgs), msgs


def test_upward_drop_warns_once_per_agent_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The ONE agent file builds TWO cascade levels (agent.<active> + agent.default)
    # but a `system:` upward table is dropped+warned exactly ONCE (the drop runs on
    # the shared raw view before both levels are built). Also: the `system:` table
    # never reaches EITHER agent partial (baseline it was silently ignored — now
    # it warns).
    agent = _write(
        tmp_path / "agent.yaml",
        {
            "self": {"default": {"model": "dm"}, "claude": {"model": "cm"}},
            "system": {"auth": {"share_allowed": False}},
        },
    )
    with caplog.at_level("WARNING"):
        levels = assemble_levels(agent_name="claude", agent_path=agent)
    # The two agent levels are intact and carry NO system node.
    assert dict.get(levels[AGENT_ACTIVE]["agent"]["claude"], "model", _MISSING) == "cm"
    assert dict.get(levels[AGENT_DEFAULT]["agent"]["default"], "model", _MISSING) == "dm"
    assert dict.get(levels[AGENT_ACTIVE], "system", _MISSING) is _MISSING
    assert dict.get(levels[AGENT_DEFAULT], "system", _MISSING) is _MISSING
    # Exactly ONE warning for the dropped system token (not one-per-level).
    sys_warns = [
        r for r in caplog.records
        if r.levelname == "WARNING" and "system" in r.getMessage()
        and str(agent) in r.getMessage()
    ]
    assert len(sys_warns) == 1, [r.getMessage() for r in sys_warns]


def test_base_floor_is_exempt_from_upward_drop(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The BASE level (declared floor + /etc base file) is a CODE FLOOR, NOT a user
    # scope file — it is EXEMPT: a base file's system.* is the system-scope floor
    # (spec §0: the auth gate is set "from the system-scope file/floor"), so it
    # must SURVIVE and emit NO drop warning. (Base uses the same scoped keyspace.)
    base = _write(
        tmp_path / "base.yaml", {"system": {"auth": {"share_allowed": True}}}
    )
    with caplog.at_level("WARNING"):
        base_level = assemble_levels(agent_name="claude", base_path=base)[BASE]
    # The base file's system.* survived (NOT dropped).
    assert isinstance(dict.get(base_level, "system"), KeyStore)
    assert (
        dict.get(base_level["system"]["auth"], "share_allowed", _MISSING) is True
    )
    # No drop warning fired for the exempt floor.
    assert not [
        r for r in caplog.records
        if r.levelname == "WARNING" and "upward-scope" in r.getMessage()
    ]


# --------------------------------------------------------------------------- #
# meta.* is RO everywhere — top-level meta: table dropped from EVERY file (§0)  #
# --------------------------------------------------------------------------- #


def _meta_warns(caplog: pytest.LogCaptureFixture, path: Path) -> list[str]:
    """Warnings that announce a top-level ``meta`` drop naming *path*."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING"
        and "meta" in r.getMessage()
        and str(path) in r.getMessage()
    ]


def test_top_level_meta_in_box_file_dropped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # §0 / clause 4 "meta.* remains RO everywhere": a box file's TOP-LEVEL meta:
    # table is set by the bootstrap layer only — a settings file may NOT set it, so
    # it is DROPPED at assembly with a distinct warning naming the file + `meta`.
    # Baseline-RED at 4b3083b (meta: was left OUT of the drop and SURVIVED into the
    # partial, able to override identity anchors); GREEN here. Both directions are
    # asserted UNCONDITIONALLY (the meta: table is a real, present top-level table,
    # so the "meta absent" assert is NON-vacuous — it fails if the drop is removed).
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"image": "img"}, "meta": {"box": {"mode": "standalone"}}},
    )
    with caplog.at_level("WARNING"):
        box_level = assemble_levels(agent_name="claude", box_path=box)[BOX]
    # The box's own-scope key survives; the top-level meta table is GONE.
    assert dict.get(box_level["box"], "image", _MISSING) == "img"
    assert dict.get(box_level, "meta", _MISSING) is _MISSING
    # A distinct meta-RO warning fired, naming the file and the meta token.
    assert _meta_warns(caplog, box), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


# meta is RO at EVERY scope — a top-level meta: table drops from each file view.
# (path_kw, level_index, own_scope) — the file carries its own-scope marker PLUS a
# top-level meta: table that must be dropped+warned regardless of the file's scope.
_META_CASES = [
    ("box_path", BOX, "box"),
    ("workset_path", WORKSET, "workset"),
    ("system_path", SYSTEM, "system"),
]


@pytest.mark.parametrize(("path_kw", "level_idx", "own_scope"), _META_CASES)
def test_top_level_meta_dropped_across_scopes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    path_kw: str,
    level_idx: int,
    own_scope: str,
) -> None:
    # meta.* is RO at EVERY scope (spec §0 / clause 4) — box, workset AND system
    # files each drop a top-level meta: table (system is the outermost cascade scope
    # yet still may not set meta). Own-scope key survives; meta absent; warn fired.
    f = _write(
        tmp_path / "f.yaml",
        {own_scope: {"marker": "keep"}, "meta": {"box": {"mode": "x"}}},
    )
    with caplog.at_level("WARNING"):
        level = assemble_levels(agent_name="claude", **{path_kw: f})[level_idx]
    assert dict.get(level[own_scope], "marker", _MISSING) == "keep"
    assert dict.get(level, "meta", _MISSING) is _MISSING
    assert _meta_warns(caplog, f), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


def test_top_level_meta_in_base_file_drops_but_system_scope_survives(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The base file has a DIFFERENT profile: it is EXEMPT for SCOPE keys (its
    # system.* is the system-scope floor and survives) but a top-level meta: table
    # STILL drops — meta.* is RO everywhere (spec §0 / clause 4), and a base-file
    # meta table would clobber the floor's materialized identity anchors. Both
    # directions asserted: system.* survives, meta is gone + warned.
    base = _write(
        tmp_path / "base.yaml",
        {
            "system": {"auth": {"share_allowed": True}},
            "meta": {"workset": {"name": "should-not-flow"}},
        },
    )
    with caplog.at_level("WARNING"):
        base_level = assemble_levels(agent_name="claude", base_path=base)[BASE]
    # SCOPE key exempt: the base file's system.* floor survives.
    assert isinstance(dict.get(base_level, "system"), KeyStore)
    assert (
        dict.get(base_level["system"]["auth"], "share_allowed", _MISSING) is True
    )
    # meta NOT exempt: the top-level meta table dropped, with a warning.
    assert dict.get(base_level, "meta", _MISSING) is _MISSING
    assert _meta_warns(caplog, base), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


def test_nested_scope_meta_is_untouched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The drop is TOP-LEVEL ONLY. A NESTED workset.meta table (the bootstrap workset
    # identity written by workset.py:write_workset_meta / read by read_workset_meta)
    # rides UNDER the workset scope table and must SURVIVE untouched — no descent,
    # no warning. This proves the top-level-only guarantee (bootstrap identity is
    # preserved while a settings file still cannot set the top-level meta.* anchor).
    ws = _write(
        tmp_path / "ws.yaml",
        {"workset": {"meta": {"name": "foo"}, "marker": "keep"}},
    )
    with caplog.at_level("WARNING"):
        ws_level = assemble_levels(agent_name="claude", workset_path=ws)[WORKSET]
    # The nested workset.meta.name survives verbatim; sibling own-scope key too.
    assert dict.get(ws_level["workset"], "marker", _MISSING) == "keep"
    assert isinstance(dict.get(ws_level["workset"], "meta"), KeyStore)
    assert dict.get(ws_level["workset"]["meta"], "name", _MISSING) == "foo"
    # No meta-drop warning fired (nothing top-level was dropped).
    assert not _meta_warns(caplog, ws), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


def test_top_level_meta_drop_warns_once_per_agent_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The ONE agent file builds TWO cascade levels but a top-level meta: table is
    # dropped+warned exactly ONCE (the drop runs on the shared raw view before both
    # levels build) — mirrors test_upward_drop_warns_once_per_agent_file. The meta
    # table reaches NEITHER agent partial.
    agent = _write(
        tmp_path / "agent.yaml",
        {
            "self": {"default": {"model": "dm"}, "claude": {"model": "cm"}},
            "meta": {"box": {"mode": "x"}},
        },
    )
    with caplog.at_level("WARNING"):
        levels = assemble_levels(agent_name="claude", agent_path=agent)
    # The two agent levels are intact and carry NO meta node.
    assert dict.get(levels[AGENT_ACTIVE]["agent"]["claude"], "model", _MISSING) == "cm"
    assert dict.get(levels[AGENT_DEFAULT]["agent"]["default"], "model", _MISSING) == "dm"
    assert dict.get(levels[AGENT_ACTIVE], "meta", _MISSING) is _MISSING
    assert dict.get(levels[AGENT_DEFAULT], "meta", _MISSING) is _MISSING
    # Exactly ONE meta-drop warning (not one-per-level).
    assert len(_meta_warns(caplog, agent)) == 1, _meta_warns(caplog, agent)


# --------------------------------------------------------------------------- #
# binding_derivations is the RESERVED INTERNAL node (R-8) — a top-level table  #
# in a settings file is DROPPED from EVERY file, like meta (spec §0)           #
# --------------------------------------------------------------------------- #


# A plausible forged table: one bind-shaped entry + one junk leaf (the junk leaf
# is what crashes the derived_bindings lens with ViewError if it ever rides).
_FORGED_DERIVATIONS = {
    "agent": {"claude": {"common": {"plugins": ["/forged/src", "~/forged"]}}},
    "junk": "zebra-not-a-bind",
}


def _derivations_warns(
    caplog: pytest.LogCaptureFixture, path: Path
) -> list[str]:
    """Warnings announcing a top-level ``binding_derivations`` drop naming *path*."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING"
        and "binding_derivations" in r.getMessage()
        and str(path) in r.getMessage()
    ]


@pytest.mark.parametrize(("path_kw", "level_idx", "own_scope"), _META_CASES)
def test_top_level_binding_derivations_dropped_across_scopes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    path_kw: str,
    level_idx: int,
    own_scope: str,
) -> None:
    # R-8 / spec §0 fault class: binding_derivations is the RESERVED INTERNAL
    # derivations node — machinery output regenerated per launch, never file
    # input. A hand-forged top-level table drops from box, workset AND system
    # files with a warning naming the file + token ("never enters the merge").
    # Own-scope key survives; the forged table is absent; warning fired.
    f = _write(
        tmp_path / "f.yaml",
        {own_scope: {"marker": "keep"},
         "binding_derivations": _FORGED_DERIVATIONS},
    )
    with caplog.at_level("WARNING"):
        level = assemble_levels(agent_name="claude", **{path_kw: f})[level_idx]
    assert dict.get(level[own_scope], "marker", _MISSING) == "keep"
    assert dict.get(level, "binding_derivations", _MISSING) is _MISSING
    assert _derivations_warns(caplog, f), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


def test_top_level_binding_derivations_in_base_file_drops_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Same base-file profile as meta: EXEMPT for scope keys (system.* is the
    # floor and survives) but NOT for the reserved node — a base-file forged
    # table would ride into the merged snapshot beside the real materialisation.
    base = _write(
        tmp_path / "base.yaml",
        {"system": {"auth": {"share_allowed": True}},
         "binding_derivations": _FORGED_DERIVATIONS},
    )
    with caplog.at_level("WARNING"):
        base_level = assemble_levels(agent_name="claude", base_path=base)[BASE]
    assert (
        dict.get(base_level["system"]["auth"], "share_allowed", _MISSING) is True
    )
    assert dict.get(base_level, "binding_derivations", _MISSING) is _MISSING
    assert _derivations_warns(caplog, base), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


def test_nested_binding_derivations_is_untouched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The drop is TOP-LEVEL ONLY, mirroring the meta rule: a NESTED
    # box.binding_derivations key rides under its scope table and is not this
    # rule's business — no descent, no drop, no warning. (It is not a KEY either
    # way; that refusal belongs to the closed head dispatch, not this drop.)
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"binding_derivations": {"marker": "rides"}, "image": "img"}},
    )
    with caplog.at_level("WARNING"):
        box_level = assemble_levels(agent_name="claude", box_path=box)[BOX]
    assert dict.get(box_level["box"], "image", _MISSING) == "img"
    nested = dict.get(box_level["box"], "binding_derivations", _MISSING)
    assert isinstance(nested, KeyStore)
    assert dict.get(nested, "marker", _MISSING) == "rides"
    assert not _derivations_warns(caplog, box), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


def test_arbitrary_unknown_table_still_rides(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # SCOPE TIGHT (pins the drop to the ONE reserved name): an arbitrary unknown
    # top-level table (zebra:) is NOT dropped and NOT warned about — general
    # unknown-table refusal is the backlogged keyspace-ENFORCEMENT work, not
    # this drop. If this test reddens because zebra: was dropped, the drop-set
    # widened beyond its brief.
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"image": "img"}, "zebra": {"stripes": 3}},
    )
    with caplog.at_level("WARNING"):
        box_level = assemble_levels(agent_name="claude", box_path=box)[BOX]
    zebra = dict.get(box_level, "zebra", _MISSING)
    assert isinstance(zebra, KeyStore)
    assert dict.get(zebra, "stripes", _MISSING) == 3
    assert not [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING" and "zebra" in r.getMessage()
    ]


# --------------------------------------------------------------------------- #
# agent.default vs agent.<active> split — TRUE discriminated keys (§2) #
# --------------------------------------------------------------------------- #


def test_agent_tiers_land_in_separate_levels_true_discriminated(tmp_path: Path) -> None:
    agent = _write(
        tmp_path / "agent.yaml",
        {
            "self": {
                "default": {"allow_helpers": True, "model": "dmodel"},
                "claude": {"model": "cmodel"},
            }
        },
    )
    levels = assemble_levels(agent_name="claude", agent_path=agent)
    # The active level keeps the §2d key agent.claude.*; the default level keeps
    # agent.default.* — NO bare-`agent` collapse (spec §0/§2d).
    active = levels[AGENT_ACTIVE]["agent"]["claude"]
    default = levels[AGENT_DEFAULT]["agent"]["default"]
    # active carries ONLY what claude set (NOT pre-merged with default).
    assert dict.get(active, "model", _MISSING) == "cmodel"
    assert dict.get(active, "allow_helpers", _MISSING) is _MISSING
    assert dict.get(default, "model", _MISSING) == "dmodel"
    assert dict.get(default, "allow_helpers", _MISSING) is True
    # The discriminator is KEPT (the §2d key form), not collapsed to bare `agent`.
    assert dict.get(levels[AGENT_ACTIVE]["agent"], "claude", _MISSING) is not _MISSING
    assert dict.get(levels[AGENT_DEFAULT]["agent"], "default", _MISSING) is not _MISSING
    # No bare `agent.<key>` leaked (would be a §0 violation).
    assert dict.get(levels[AGENT_ACTIVE]["agent"], "model", _MISSING) is _MISSING
    assert dict.get(levels[AGENT_DEFAULT]["agent"], "model", _MISSING) is _MISSING


def test_active_override_not_in_default_and_vice_versa(tmp_path: Path) -> None:
    agent = _write(
        tmp_path / "agent.yaml",
        {"self": {"default": {"x": "d"}, "goose": {"y": "g"}}},
    )
    levels = assemble_levels(agent_name="goose", agent_path=agent)
    active = levels[AGENT_ACTIVE]["agent"]["goose"]
    default = levels[AGENT_DEFAULT]["agent"]["default"]
    assert dict.get(active, "y", _MISSING) == "g"
    assert dict.get(active, "x", _MISSING) is _MISSING  # default key not here
    assert dict.get(default, "x", _MISSING) == "d"
    assert dict.get(default, "y", _MISSING) is _MISSING  # active key not here


def test_unknown_active_agent_yields_empty_active_level(tmp_path: Path) -> None:
    agent = _write(
        tmp_path / "agent.yaml",
        {"self": {"default": {"x": "d"}, "claude": {"y": "c"}}},
    )
    levels = assemble_levels(agent_name="codex", agent_path=agent)
    # An active agent absent from the file → empty active level (no agent.codex.*).
    assert len(levels[AGENT_ACTIVE]) == 0
    assert dict.get(levels[AGENT_DEFAULT]["agent"]["default"], "x", _MISSING) == "d"


def test_agent_categories_under_true_discriminated_name(tmp_path: Path) -> None:
    # An agent-tier bind key keeps the §2d form agent.<active-name>.bindings.*.
    agent = _write(
        tmp_path / "agent.yaml",
        {"self": {"claude": {"bindings": {"ro": {"share": ["/h/s", "/g/s"]}}}}},
    )
    active = assemble_levels(agent_name="claude", agent_path=agent)[AGENT_ACTIVE]
    bind = active["agent"]["claude"]["bindings"]["ro"]["share"]
    assert bind == Bind("/h/s", "/g/s", None)


def test_per_agent_independence_other_agent_under_own_name(tmp_path: Path) -> None:
    # §0: a settings file may set agent.<name>.* for an agent that is NOT the
    # active one. With claude active, the file's agent.goose.* must NOT leak into
    # the active level and must keep its own discriminated name (it only takes
    # effect when goose is active next launch).
    agent = _write(
        tmp_path / "agent.yaml",
        {
            "self": {
                "default": {"model": "dm"},
                "claude": {"model": "cm"},
                "goose": {"model": "gm"},
            }
        },
    )
    levels = assemble_levels(agent_name="claude", agent_path=agent)
    active = levels[AGENT_ACTIVE]["agent"]
    # The active (claude) level carries claude's subtree only — NOT goose's.
    assert dict.get(active, "claude", _MISSING) is not _MISSING
    assert dict.get(active, "goose", _MISSING) is _MISSING
    # goose's keys are simply not represented as an active level here (claude is
    # active); they live in the file for a future goose launch. The default level
    # still carries agent.default.* by its true name.
    assert (
        dict.get(levels[AGENT_DEFAULT]["agent"]["default"], "model", _MISSING) == "dm"
    )


# --------------------------------------------------------------------------- #
# Binds → Bind, refs RAW (S9 / spec §0)                                        #
# --------------------------------------------------------------------------- #


def test_binds_become_Bind_two_and_three_tuple(tmp_path: Path) -> None:
    box = _write(
        tmp_path / "box.yaml",
        {
            "box": {
                "bindings": {
                    "rw": {"home": ["/host/home", "~/"]},
                    "ro": {"sock": ["/h/s", "/g/s", "z"]},
                },
                "caches": {"c": ["/h/c", "/g/c"]},
                "seeded": {"t": ["/h/t", "/g/t"]},
                "common": {"p": ["/h/p", "/g/p"]},
                "synced": {"cred": ["/h/cred", "/g/cred"]},
            }
        },
    )
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    rw_home = box_scope["bindings"]["rw"]["home"]
    assert rw_home == Bind("/host/home", "~/", None)
    assert isinstance(rw_home, Bind)
    assert box_scope["bindings"]["ro"]["sock"] == Bind("/h/s", "/g/s", "z")
    for cat, name in [("caches", "c"), ("seeded", "t"), ("common", "p"), ("synced", "cred")]:
        assert isinstance(box_scope[cat][name], Bind)


def test_refs_left_raw_inside_bind(tmp_path: Path) -> None:
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"vault": ["@workset.vault_rw/x", "$XDG_STATE_HOME/v"]}}}},
    )
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    bind = box_scope["bindings"]["rw"]["vault"]
    # NOT expanded — tokens preserved verbatim (S9 / spec §0).
    assert bind.host == "@workset.vault_rw/x"
    assert bind.box == "$XDG_STATE_HOME/v"


def test_malformed_bind_arity_raises(tmp_path: Path) -> None:
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"bad": ["only-one"]}}}},
    )
    with pytest.raises(SettingsError):
        assemble_levels(agent_name="claude", box_path=box)


# --------------------------------------------------------------------------- #
# masks — keyed dict[box_dest → bool|None] (S5)                               #
# --------------------------------------------------------------------------- #


def test_masks_is_keyed_dict_three_state(tmp_path: Path) -> None:
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"masks": {"/secret": True, "/inherited": None}}},
    )
    masks = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]["masks"]
    assert isinstance(masks, KeyStore)
    assert dict.get(masks, "/secret", _MISSING) is True
    # present-None survives as present-None (UNMASK), distinct from absent.
    assert dict.get(masks, "/inherited", _MISSING) is None
    assert dict.get(masks, "/absent", _MISSING) is _MISSING


def test_masks_not_bind_parsed(tmp_path: Path) -> None:
    # A masks leaf is bool/None, never a Bind — masks is NOT a bind category.
    box = _write(tmp_path / "box.yaml", {"box": {"masks": {"/x": True}}})
    masks = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]["masks"]
    assert not isinstance(dict.get(masks, "/x"), Bind)


# --------------------------------------------------------------------------- #
# Absent / empty files → empty partials                                       #
# --------------------------------------------------------------------------- #


def test_absent_files_yield_empty_partials() -> None:
    levels = assemble_levels(
        agent_name="claude",
        base_path=Path("/nonexistent/base.yaml"),
        system_path=Path("/nonexistent/sys.yaml"),
        agent_path=Path("/nonexistent/agent.yaml"),
        workset_path=Path("/nonexistent/ws.yaml"),
        box_path=Path("/nonexistent/box.yaml"),
    )
    assert len(levels) == 6
    assert all(len(lv) == 0 for lv in levels)


def test_none_paths_yield_empty_partials() -> None:
    # None for every optional path (base falls back to /etc, absent in the
    # test env → empty too).
    levels = assemble_levels(agent_name="claude")
    assert len(levels) == 6
    for idx in (BOX, WORKSET, AGENT_ACTIVE, AGENT_DEFAULT, SYSTEM):
        assert len(levels[idx]) == 0


# --------------------------------------------------------------------------- #
# base: SAME scoped keyspace, NO synthetic wrapper                            #
# --------------------------------------------------------------------------- #


def test_base_uses_scoped_keyspace_no_wrapper(tmp_path: Path) -> None:
    # Real /etc files have NO `base:` table — they carry scope-rooted
    # keys (agent/system/box…) exactly like every other file. Example:
    # base sets agent.default.model.
    base = _write(tmp_path / "base.yaml", {"agent": {"default": {"model": "bm"}}})
    levels = assemble_levels(
        agent_name="claude", base_path=base
    )
    # The base file's agent.default tier is read on the BASE level (scope kept),
    # NOT lost to a missing `base:` wrapper.
    assert (
        dict.get(levels[BASE]["agent"]["default"], "model", _MISSING) == "bm"
    )


# --------------------------------------------------------------------------- #
# Floor on base; cascade ends at box (no required cap)                        #
# --------------------------------------------------------------------------- #


def test_floor_lands_on_base_level() -> None:
    # Floor keys are scope-qualified §2d forms (agent.default.* — NOT bare
    # agent.*); they explode to the nested keyspace on the BASE level.
    levels = assemble_levels(
        agent_name="claude",
        floor={"agent.default.allow_helpers": True, "agent.default.bootstrap": "tmux"},
    )
    base = levels[BASE]["agent"]["default"]
    assert dict.get(base, "allow_helpers", _MISSING) is True
    assert dict.get(base, "bootstrap", _MISSING) == "tmux"
    # The floor is NOT in any other level.
    assert len(levels[BOX]) == 0


def test_floor_dotted_keys_explode_to_nested() -> None:
    levels = assemble_levels(
        agent_name="claude",
        floor={"box.bindings.rw.home": ["/h/home", "~/"]},
    )
    bind = levels[BASE]["box"]["bindings"]["rw"]["home"]
    assert bind == Bind("/h/home", "~/", None)


def test_base_file_set_value_beats_floor_at_same_key(tmp_path: Path) -> None:
    base_file = _write(tmp_path / "base.yaml", {"agent": {"default": {"bootstrap": "none"}}})
    levels = assemble_levels(
        agent_name="claude",
        base_path=base_file,
        floor={"agent.default.bootstrap": "tmux"},
    )
    # Within the single base level, the base FILE entry wins over the floor; the
    # deep overlay must not clobber sibling floor leaves either.
    assert dict.get(levels[BASE]["agent"]["default"], "bootstrap", _MISSING) == "none"


def test_overlay_preserves_sibling_floor_leaves(tmp_path: Path) -> None:
    base_file = _write(tmp_path / "base.yaml", {"agent": {"default": {"a": "file"}}})
    levels = assemble_levels(
        agent_name="claude",
        base_path=base_file,
        floor={"agent.default.a": "floor", "agent.default.b": "floorB"},
    )
    sub = levels[BASE]["agent"]["default"]
    assert dict.get(sub, "a", _MISSING) == "file"  # file overlays floor
    assert dict.get(sub, "b", _MISSING) == "floorB"  # sibling floor leaf survives


# --------------------------------------------------------------------------- #
# No machine tier (S14)                                                        #
# --------------------------------------------------------------------------- #


def test_no_machine_path_consulted() -> None:
    # The old machine third-file (machine_config_path) was DELETED in the
    # two-layer path reshape (block #3a). No-machine-tier is now structurally
    # guaranteed: the function does not exist, so it cannot be consulted (S14).
    import kanibako.settings.config as cfg

    assert not hasattr(cfg, "machine_config_path")
    levels = assemble_levels(agent_name="claude")
    assert len(levels) == 6


# --------------------------------------------------------------------------- #
# Nested KeyStore shape (S7) + behavior + category together (S13)             #
# --------------------------------------------------------------------------- #


def test_partial_holds_behavior_and_category_together(tmp_path: Path) -> None:
    box = _write(
        tmp_path / "box.yaml",
        {
            "box": {
                "image": "ghcr.io/x:latest",  # behavior leaf
                "bindings": {"rw": {"home": ["/h", "~/"]}},  # category subtree
                "masks": {"/secret": True},
            }
        },
    )
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    assert dict.get(box_scope, "image", _MISSING) == "ghcr.io/x:latest"
    assert isinstance(box_scope["bindings"], KeyStore)
    assert isinstance(box_scope["bindings"]["rw"]["home"], Bind)
    assert isinstance(box_scope["masks"], KeyStore)


def test_nested_subtrees_are_keystores(tmp_path: Path) -> None:
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"home": ["/h", "~/"]}}}},
    )
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    assert isinstance(box_scope["bindings"], KeyStore)
    assert isinstance(box_scope["bindings"]["rw"], KeyStore)


def test_present_none_scalar_preserved(tmp_path: Path) -> None:
    # An explicit null behavior leaf is present-None (a reset), distinct from absent.
    box = _write(tmp_path / "box.yaml", {"box": {"model": None}})
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    assert dict.get(box_scope, "model", _MISSING) is None
    assert dict.get(box_scope, "absent", _MISSING) is _MISSING


# --------------------------------------------------------------------------- #
# assemble→merge: the capabilities the bare-`agent` collapse DESTROYED          #
# (§0 per-agent independence + §2d discriminated keys). Per-agent tables ride a #
# SYSTEM file — a spec-legal downward source (system ⊃ agent); a box file may    #
# NOT set agent.<agent>.* (upward, dropped at RESOLVE — spec §0 directional).    #
# --------------------------------------------------------------------------- #


def _merged(
    tmp_path: Path, *, agent_name: str, agent: dict, system: dict
) -> KeyStore:
    """Assemble the agent + SYSTEM files and run the block-2b merge — the real path
    a per-agent override or two-agent coexistence travels (cascade by name).

    The per-agent tables ride the SYSTEM file, which is a spec-LEGAL downward
    source for ``agent.*`` (``system ⊃ agent``, defaults-down); a box file may NOT
    carry ``agent.<agent>.*`` — that is an upward write dropped at RESOLVE (spec §0
    directional enforcement). ``_file_partial`` mirrors the system file's whole
    tree, so ``agent.<name>.*`` / ``agent.default.*`` flow under their TRUE
    discriminated names at the system level (below the agent-active/default
    levels in precedence).
    """
    agent_p = _write(tmp_path / "agent.yaml", agent)
    system_p = _write(tmp_path / "system.yaml", system)
    levels = assemble_levels(
        agent_name=agent_name, agent_path=agent_p, system_path=system_p
    )
    snap = merge(levels)
    return snap


def test_agent_active_override_survives_and_wins_by_name(tmp_path: Path) -> None:
    # §2d discriminated keys survive the merge distinctly (no bare-`agent`
    # collapse). The agent-file agent.claude.model (the ACTIVE level, more
    # specific) wins BY NAME over a system-file agent.claude.model default; the
    # system's agent.default.model survives under its own true name.
    snap = _merged(
        tmp_path,
        agent_name="claude",
        agent={"self": {"claude": {"model": "cm"}}},
        # System file = a legal downward source for agent.* defaults.
        system={"agent": {"default": {"model": "dm"}, "claude": {"model": "sysm"}}},
    )
    # The more-specific agent-active level wins for claude (over system default).
    assert dict.get(snap["agent"]["claude"], "model", _MISSING) == "cm"
    # agent.default.* survives by its own true name (NOT erased / collapsed).
    assert dict.get(snap["agent"]["default"], "model", _MISSING) == "dm"
    # No bare agent.model leaked (a §0 violation).
    assert dict.get(snap["agent"], "model", _MISSING) is _MISSING


def test_two_agents_coexist_under_their_own_names(tmp_path: Path) -> None:
    # A scope may carry settings for MULTIPLE agents independently; the bare
    # collapse made agent.claude.* and agent.goose.* indistinguishable. With
    # discriminated keys they coexist under their own §2d names through merge. The
    # per-agent tables ride the SYSTEM file (legal downward source, system ⊃ agent).
    snap = _merged(
        tmp_path,
        agent_name="claude",
        agent={"self": {"claude": {"model": "cm"}}},
        system={
            "agent": {
                "default": {"model": "dm"},
                "claude": {"model": "sys_claude"},
                "goose": {"model": "sys_goose"},
            }
        },
    )
    # claude resolves to the more-specific agent-active level ("cm"); goose and
    # default coexist by name from the system level, distinct from claude.
    assert dict.get(snap["agent"]["claude"], "model", _MISSING) == "cm"
    assert dict.get(snap["agent"]["goose"], "model", _MISSING) == "sys_goose"
    # agent.default.* also coexists, distinct from both.
    assert dict.get(snap["agent"]["default"], "model", _MISSING) == "dm"


# --------------------------------------------------------------------------- #
# P6c — standalone TIER MODEL: box tier EMPTY, single file plays the WORKSET   #
# tier; a box.* key resolves for box scope via R2 downward-defaults.           #
# --------------------------------------------------------------------------- #


def _box_enable_vault(snap: KeyStore) -> object:
    box = dict.get(snap, "box", _MISSING)
    return dict.get(box, "enable_vault", _MISSING) if isinstance(box, KeyStore) else _MISSING


def test_p6c_standalone_box_key_resolves_via_workset_tier(tmp_path: Path) -> None:
    # STANDALONE TIER MODEL (P6c, spec §2c): a lone box's single settings.yaml
    # now plays the WORKSET tier (box tier EMPTY). A box.* key set in it still
    # resolves for box scope via R2 downward-defaults (box ⊂ workset — the
    # workset-tier read KEEPS box.*). File carries a box-scope override.
    f = _write(tmp_path / "settings.yaml", {"box": {"enable_vault": False}})

    # P6c pair: box tier EMPTY (None), the file as the WORKSET tier.
    snap_p6c = merge(
        assemble_levels(agent_name="claude", box_path=None, workset_path=f)
    )
    assert _box_enable_vault(snap_p6c) is False

    # RESULT-EQUIVALENCE vs the pre-P6c read (file as the BOX tier): a lone box has
    # exactly ONE file, so box-vs-workset tier picks the same resolved box scope.
    snap_old = merge(
        assemble_levels(agent_name="claude", box_path=f, workset_path=None)
    )
    assert _box_enable_vault(snap_old) == _box_enable_vault(snap_p6c)

    # MUTATION-GUARD (non-vacuous): the workset-tier read is LOAD-BEARING. Dropping
    # it (both tiers empty) loses the override entirely → the key is absent (falls
    # to the floor/default downstream), NOT False. Proves the assert above is not
    # vacuously satisfied by some other source.
    snap_dropped = merge(
        assemble_levels(agent_name="claude", box_path=None, workset_path=None)
    )
    assert _box_enable_vault(snap_dropped) is _MISSING


def test_p6c_standalone_workset_scope_key_also_resolves(tmp_path: Path) -> None:
    # The unification's strict gain: a workset.* key set in the standalone file
    # (previously DROPPED as an upward write when the file was the BOX tier) now
    # survives, because the file is the WORKSET tier and workset.* is its own scope.
    f = _write(
        tmp_path / "settings.yaml",
        {"box": {"enable_vault": False}, "workset": {"marker": "w"}},
    )
    snap = merge(
        assemble_levels(agent_name="claude", box_path=None, workset_path=f)
    )
    ws = dict.get(snap, "workset", _MISSING)
    assert isinstance(ws, KeyStore)
    assert dict.get(ws, "marker", _MISSING) == "w"
    # box.* still resolves too (both scopes coexist at the workset tier).
    assert _box_enable_vault(snap) is False


# --------------------------------------------------------------------------- #
# ``pref:`` is legal in the WORKSET and BOX files ONLY (spec §2h)   #
# --------------------------------------------------------------------------- #


class TestPrefTableWriteSiteAtAssembly:
    """D4 — WARN + DROP in a file where a pref is illegal.

    Same treatment ``_drop_upward_scopes`` gives the sibling mis-scope: two
    behaviours for one fault class is the confusion §0's convention 0 forbids.
    The HARD refusal §2h calls for lives at the WRITE site (``config set``).
    """

    def test_base_file_pref_table_warns_and_drops(self, tmp_path, caplog) -> None:
        """INVERT: drop it silently -> the caplog assertion reddens."""
        base = _write(
            tmp_path / "base.yaml",
            {"pref": {"system": {"agent": "goose"}}, "system": {"cache": "/c"}},
        )
        with caplog.at_level("WARNING"):
            levels = assemble_levels(agent_name="claude", base_path=base)
        assert "pref" not in levels[BASE]
        assert levels[BASE].system.cache == "/c"   # the rest of the file survives
        assert "workset or box settings file" in caplog.text
        assert "bounds the resolution recursion" in caplog.text

    def test_system_file_pref_table_warns_and_drops(self, tmp_path, caplog) -> None:
        sysf = _write(
            tmp_path / "system.yaml", {"pref": {"system": {"agent": "goose"}}},
        )
        with caplog.at_level("WARNING"):
            levels = assemble_levels(agent_name="claude", system_path=sysf)
        assert "pref" not in levels[SYSTEM]
        assert "workset or box settings file" in caplog.text

    def test_agent_file_pref_table_warns_and_drops(self, tmp_path, caplog) -> None:
        """⚑ Why the refusal runs on the RAW view: the agent tier never mirrors
        a non-``agent:`` table into its partial, so a post-partial filter could
        not see (or warn about) a ``pref:`` table here at all."""
        agentf = _write(
            tmp_path / "agent.yaml",
            {"pref": {"system": {"agent": "goose"}},
             "self": {"claude": {"model": "opus"}}},
        )
        with caplog.at_level("WARNING"):
            levels = assemble_levels(agent_name="claude", agent_path=agentf)
        assert "pref" not in levels[AGENT_ACTIVE]
        assert levels[AGENT_ACTIVE].agent.claude.model == "opus"
        assert "workset or box settings file" in caplog.text

    def test_box_and_workset_pref_tables_are_KEPT(self, tmp_path, caplog) -> None:
        """The whole point: these two files are where a pref is legal."""
        box = _write(
            tmp_path / "box.yaml", {"pref": {"system": {"agent": "goose"}}},
        )
        ws = _write(
            tmp_path / "ws.yaml", {"pref": {"agent": {"claude": {"model": "opus"}}}},
        )
        with caplog.at_level("WARNING"):
            levels = assemble_levels(
                agent_name="claude", box_path=box, workset_path=ws,
            )
        assert levels[BOX].pref.system.agent == "goose"
        assert levels[WORKSET].pref.agent.claude.model == "opus"
        assert "workset or box settings file" not in caplog.text

    def test_a_bind_shaped_pref_value_is_parsed_as_a_bind(self, tmp_path) -> None:
        """The pref path mirrors its target's, so ``_parse_node``'s ancestor
        test makes a bind-shaped request a real ``Bind`` — the property that
        makes the NESTED-only spelling load-bearing (D5)."""
        box = _write(
            tmp_path / "box.yaml",
            {"pref": {"agent": {"claude": {"common": {"x": ["/s", "~/d"]}}}}},
        )
        levels = assemble_levels(agent_name="claude", box_path=box)
        assert isinstance(levels[BOX].pref.agent.claude.common.x, Bind)


# --------------------------------------------------------------------------- #
# RETIRED BEHAVIOR keys — the ``auto_approve`` → ``access`` refusal (R-41/RQ-2)
# --------------------------------------------------------------------------- #


class TestRefuseRetiredBehaviorKeys:
    """A stored ``auto_approve`` is REFUSED at launch, never silently ignored.

    The failure this prevents: under the closed keyspace the retired spelling is
    UNDECLARED, and an undeclared stored key is SILENT — so a box deliberately
    configured ``auto_approve: false`` would come up at the new ``full`` default
    with nothing printed.  On a permission axis that is a silent regression in
    the UNSAFE direction.
    """

    def _refuse(self, raw, **kw):
        from kanibako.settings.settings_assemble import refuse_retired_behavior_keys

        return refuse_retired_behavior_keys(raw, **kw)

    def test_scope_file_agent_default_table_is_refused(self, tmp_path) -> None:
        path = tmp_path / "settings.yaml"
        with pytest.raises(SettingsError) as exc:
            self._refuse(
                {"agent": {"default": {"auto_approve": False}}},
                level="system", path=path,
            )
        msg = str(exc.value)
        # NAMES: the key, the spelling found, the level, the file, and the cure.
        assert "auto_approve" in msg
        assert "agent.default.auto_approve" in msg
        assert "system" in msg
        assert str(path) in msg
        assert "access" in msg

    def test_the_cure_quotes_the_users_value_through_the_ruled_mapping(
        self, tmp_path,
    ) -> None:
        """R-41's mapping, in the message: true → full, false → restricted."""
        for stored, tier in ((True, "full"), (False, "restricted")):
            with pytest.raises(SettingsError) as exc:
                self._refuse(
                    {"agent": {"default": {"auto_approve": stored}}},
                    level="system", path=tmp_path / "s.yaml",
                )
            msg = str(exc.value)
            assert f"access={tier}" in msg, stored
            assert f"`access: {tier}`" in msg, stored

    def test_string_booleans_map_too(self, tmp_path) -> None:
        """Settings files carry ``true``/``yes``/``0`` as often as real bools."""
        for stored, tier in (("true", "full"), ("no", "restricted"), ("1", "full")):
            with pytest.raises(SettingsError) as exc:
                self._refuse(
                    {"agent": {"default": {"auto_approve": stored}}},
                    level="system", path=tmp_path / "s.yaml",
                )
            assert f"access={tier}" in str(exc.value), stored

    def test_an_unparseable_value_names_the_tiers_instead_of_guessing(
        self, tmp_path,
    ) -> None:
        with pytest.raises(SettingsError) as exc:
            self._refuse(
                {"agent": {"default": {"auto_approve": "sortof"}}},
                level="system", path=tmp_path / "s.yaml",
            )
        msg = str(exc.value)
        assert "restricted | editing | full" in msg
        # No invented tier: the cure carries the shape, not a guess.
        assert "access=<restricted|editing|full>" in msg

    def test_per_agent_table_is_refused_and_names_that_agent(self, tmp_path) -> None:
        with pytest.raises(SettingsError) as exc:
            self._refuse(
                {"agent": {"claude": {"auto_approve": True}}},
                level="system", path=tmp_path / "s.yaml",
            )
        assert "agent.claude.auto_approve" in str(exc.value)

    def test_agent_file_self_table_is_refused(self, tmp_path) -> None:
        """The per-agent file's FLAT shape — where ``agent set`` wrote it."""
        with pytest.raises(SettingsError) as exc:
            self._refuse(
                {"self": {"auto_approve": False, "model": "opus"}},
                level="agent", path=tmp_path / "claude.yaml", subject="claude",
            )
        msg = str(exc.value)
        assert "self.auto_approve" in msg
        assert "kanibako agent set claude access=restricted" in msg

    def test_a_pref_request_is_refused(self, tmp_path) -> None:
        with pytest.raises(SettingsError) as exc:
            self._refuse(
                {"pref": {"agent": {"claude": {"auto_approve": True}}}},
                level="box", path=tmp_path / "box.yaml", subject="claude",
            )
        msg = str(exc.value)
        assert "pref.agent.claude.auto_approve" in msg
        assert "kanibako box set pref.agent.claude.access=full" in msg

    def test_the_cure_is_level_appropriate(self, tmp_path) -> None:
        """``access`` is an AGENT-scope key, so where it may be WRITTEN depends
        on the file it was found in (the same asymmetry the selection refusal
        handles): system writes it directly, box/workset must use the §2h
        request (a bare agent key there is an UPWARD write and is dropped)."""
        cures = {}
        for level in ("base", "system", "workset", "box"):
            with pytest.raises(SettingsError) as exc:
                self._refuse(
                    {"agent": {"default": {"auto_approve": True}}},
                    level=level, path=tmp_path / "s.yaml", subject="claude",
                )
            cures[level] = str(exc.value)
        assert "kanibako system set access=full" in cures["base"]
        assert "kanibako system set access=full" in cures["system"]
        assert "kanibako workset set pref.agent.claude.access=full" in cures["workset"]
        assert "kanibako box set pref.agent.claude.access=full" in cures["box"]

    def test_a_clean_file_passes(self, tmp_path) -> None:
        """Non-vacuity: the same shapes with the SUCCESSOR key do not raise."""
        self._refuse(
            {
                "agent": {"default": {"access": "restricted"}, "claude": {"model": "o"}},
                "self": {"access": "full"},
                "pref": {"agent": {"claude": {"access": "editing"}}},
            },
            level="box", path=tmp_path / "box.yaml",
        )

    def test_an_unrelated_deep_key_of_the_same_name_is_not_swept_up(
        self, tmp_path,
    ) -> None:
        """SCOPE-TIGHT: only the declared behavior TABLES are walked, so a
        same-named leaf somewhere else is none of this rule's business."""
        self._refuse(
            {"box": {"masks": {"auto_approve": True}}},
            level="box", path=tmp_path / "box.yaml",
        )

    def test_a_present_none_leaf_is_still_the_retired_key(self, tmp_path) -> None:
        """ABSENT ≠ PRESENT-null: an empty ``auto_approve:`` leaf is still the
        stale key (the same 3-state trap the selection refusal documents)."""
        with pytest.raises(SettingsError) as exc:
            self._refuse(
                {"agent": {"default": {"auto_approve": None}}},
                level="system", path=tmp_path / "s.yaml",
            )
        assert "auto_approve" in str(exc.value)

    def test_retiring_keys_stays_empty(self) -> None:
        """§4 of the plan: this refusal must NOT repopulate the deferred
        general-enforcement gate."""
        from kanibako.settings.settings_keyspace import RETIRING_KEYS

        assert RETIRING_KEYS == frozenset()

    def test_assemble_levels_itself_does_not_raise(self, tmp_path) -> None:
        """The refusal lives at the LAUNCH seam, NOT inside assembly — a raise
        here would also break ``config set``, the very command the message
        prescribes as the cure."""
        path = _write(
            tmp_path / "system.yaml",
            {"agent": {"default": {"auto_approve": False}}},
        )
        levels = assemble_levels(agent_name="claude", system_path=path)
        assert levels is not None


# --------------------------------------------------------------------------- #
# parse_bind_map — the DEST-KEYED arm parser (R-5/R-6), ADDITIVE in P5         #
# --------------------------------------------------------------------------- #


def test_parse_bind_map_keys_on_dest_and_yields_bind_entries() -> None:
    node = parse_bind_map({"~/.claude": ["/h/claude"], "~/v": ["/h/v", "ro"]})
    assert isinstance(node, KeyStore)
    assert set(dict.keys(node)) == {"~/.claude", "~/v"}
    assert type(dict.__getitem__(node, "~/.claude")) is BindEntry
    assert dict.__getitem__(node, "~/.claude") == BindEntry("/h/claude")
    assert dict.__getitem__(node, "~/v") == BindEntry("/h/v", "ro")


def test_parse_bind_map_leaves_refs_and_vars_raw() -> None:
    # Assembly never expands (S9/spec §0 — files store UNRESOLVED). That holds for
    # the destination too, which is now a KEY.
    node = parse_bind_map({"@meta.box.path/home": ["@config.data/seed"]})
    assert set(dict.keys(node)) == {"@meta.box.path/home"}
    assert dict.__getitem__(node, "@meta.box.path/home") == BindEntry(
        "@config.data/seed"
    )


def test_parse_bind_map_preserves_a_present_none_entry() -> None:
    # A per-entry reset stays a present-``None`` leaf for the merge to classify as
    # an OMIT (§3) — it must NOT be parsed away or rejected here.
    node = parse_bind_map({"~/a": ["/h/a"], "~/b": None})
    assert dict.__getitem__(node, "~/b") is None


def test_parse_bind_map_refuses_a_non_mapping_arm() -> None:
    with pytest.raises(SettingsError) as exc:
        parse_bind_map([["/h/a", "~/a"]])
    assert "must be a mapping" in str(exc.value)


def test_parse_bind_map_refuses_a_stale_three_element_entry() -> None:
    # A NAME-keyed arm fed to the dest-keyed parser: the 3-element value is the one
    # arity that CANNOT be a dest-keyed entry, so it is caught loudly.
    with pytest.raises(SettingsError):
        parse_bind_map({"home": ["/h/src", "~/home", "ro"]})


def test_the_live_name_keyed_path_still_parses_to_bind(tmp_path: Path) -> None:
    # ⚑ P5 is a BRIDGE: adding the dest-keyed parser must not change what the
    # PRODUCTION load path produces. A settings file's bindings arm is still
    # name-keyed and still yields a 3-field ``Bind``.
    path = _write(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"home": ["/h/src", "~/home"]}}}},
    )
    levels = assemble_levels(agent_name="claude", box_path=path)
    entry = levels[BOX]["box"]["bindings"]["rw"]["home"]
    assert type(entry) is Bind
    assert entry == Bind("/h/src", "~/home")

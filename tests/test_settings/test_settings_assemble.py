"""Unit tests for block 2a — cascade level assembly (settings_assemble).

Covers the brief's checklist: the 6-level count + MOST-SPECIFIC-FIRST order;
``agent.default`` vs ``agent.<active>`` land in the RIGHT separate levels (NOT
pre-merged), each under its TRUE discriminated §2d key (``agent.default.<key>`` /
``agent.<active-name>.<key>`` — NO bare-``agent`` collapse, spec §0/§2d);
binds become ``BindEntry`` under a DEST-KEYED terminal category key — all six of
them since 2026-08-08c — with raw ``@``-refs / ``$vars``
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

from kanibako.settings.kb_store import Bind, BindEntry
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_assemble import assemble_levels, parse_bind_map
from kanibako.settings.settings_merge import merge
from kanibako.settings.settings_resolve import SettingsError

# Index of each level in the returned MOST-SPECIFIC-FIRST list (S8).
BOX, WORKSET, AGENT_ACTIVE, AGENT_DEFAULT, SYSTEM, BASE = range(6)


def _write(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


# The DECLARED leaf each scope's cascade probes use as their order/survival marker.
# A probe only needs a same-shaped scalar it can read back by scope, but the keyspace
# is CLOSED (spec §0) — an undeclared name is not a key, so the probe borrows a real
# behavior leaf of that scope rather than inventing one.
_MARKER_KEY = {"system": "template", "box": "image", "workset": "template"}


def _marker_of(store: KeyStore, scope: str) -> object:
    """The *scope*'s marker leaf out of *store*, or ``__MISSING__``."""
    sub = dict.get(store, scope, __MISSING__)
    if not isinstance(sub, KeyStore):
        return __MISSING__
    return dict.get(sub, _MARKER_KEY[scope], __MISSING__)


# --------------------------------------------------------------------------- #
# Level count + order (S8)                                                     #
# --------------------------------------------------------------------------- #


def test_returns_six_levels_all_keystores() -> None:
    levels = assemble_levels(agent_name="claude")
    assert len(levels) == 6
    assert all(isinstance(lv, KeyStore) for lv in levels)


def test_order_is_most_specific_first(tmp_path: Path) -> None:
    # Each scope file sets its own DECLARED marker leaf (``_MARKER_KEY``) so we can
    # read the order back by value. Files are scope-ROOTED on disk; the partial keeps
    # the scope token, so the marker lives under e.g. box.image / system.template.
    box = _write(tmp_path / "box.yaml", {"box": {"image": "box"}})
    ws = _write(tmp_path / "ws.yaml", {"workset": {"template": "workset"}})
    # ⚑ The all-agents marker is written in the SYSTEM file, which is where that tier
    # LIVES: the agent file is the active node's own, so ``self:`` IS ``agent.claude``
    # and it has no spelling for ``agent.default`` at all (the flatten;
    # [spec:15-21, "self"]).
    sysf = _write(
        tmp_path / "sys.yaml",
        {"system": {"template": "system"},
         "agent": {"default": {"env": {"MARKER": "adef"}}}},
    )
    base = _write(tmp_path / "base.yaml", {"system": {"template": "base"}})
    # ⚑ A CATEGORY, not a bare scalar: since the flatten the agent FILE contributes its
    # category tables to this cascade, while its flat STATE rides its own rung at the
    # launch seam (``agent_file.state_level`` → ``settings_launch._agent_state_partial``)
    # and never appears here. One value, one route — that is defect D-3 closed.
    agent = _write(tmp_path / "agent.yaml", {"self": {"env": {"MARKER": "aact"}}})
    levels = assemble_levels(
        agent_name="claude",
        base_path=base,
        system_path=sysf,
        agent_path=agent,
        workset_path=ws,
        box_path=box,
    )

    assert _marker_of(levels[BOX], "box") == "box"
    assert _marker_of(levels[WORKSET], "workset") == "workset"
    # The active agent level keeps its TRUE discriminated key agent.claude.* (NO
    # bare-agent collapse).
    assert (
        dict.get(levels[AGENT_ACTIVE]["agent"]["claude"]["env"], "MARKER", __MISSING__)
        == "aact"
    )
    # ⚑ The agent-file DEFAULT level is STRUCTURALLY EMPTY — the file has no spelling
    # for that tier. The tier itself is alive and arrives on the SYSTEM level, under
    # its own true discriminated name.
    assert len(levels[AGENT_DEFAULT]) == 0
    assert (
        dict.get(levels[SYSTEM]["agent"]["default"]["env"], "MARKER", __MISSING__)
        == "adef"
    )
    assert _marker_of(levels[SYSTEM], "system") == "system"
    assert _marker_of(levels[BASE], "system") == "base"


# --------------------------------------------------------------------------- #
# Scope token KEPT — namespace orthogonal to cascade (§0)                      #
# --------------------------------------------------------------------------- #


def test_scope_token_kept_not_stripped(tmp_path: Path) -> None:
    box = _write(tmp_path / "box.yaml", {"box": {"image": "img"}})
    box_level = assemble_levels(agent_name="claude", box_path=box)[BOX]
    # The partial keeps the scope token: box.image, NOT a stripped `image`.
    assert isinstance(dict.get(box_level, "box"), KeyStore)
    assert dict.get(box_level["box"], "image", __MISSING__) == "img"
    assert dict.get(box_level, "image", __MISSING__) is __MISSING__


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
    assert dict.get(box_level["box"], "image", __MISSING__) == "img"
    assert dict.get(box_level, "system", __MISSING__) is __MISSING__
    # The drop is announced (file path + dropped token), unconditionally.
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("system" in m and str(box) in m for m in warnings), warnings


def test_downward_scope_key_in_workset_file_preserved(tmp_path: Path) -> None:
    # §0 defaults-down: a workset file MAY set the box.* scope it CONTAINS — the
    # partial preserves it (the drop must NOT eat downward contributions). This
    # pins the direction the drop leaves untouched.
    ws = _write(
        tmp_path / "ws.yaml",
        {"workset": {"template": "w"}, "box": {"masks": {"/x": None}}},
    )
    ws_level = assemble_levels(agent_name="claude", workset_path=ws)[WORKSET]
    assert _marker_of(ws_level, "workset") == "w"
    assert isinstance(dict.get(ws_level, "box"), KeyStore)
    assert dict.get(ws_level["box"]["masks"], "/x", __MISSING__) is None


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
        {own_scope: {_MARKER_KEY[own_scope]: "keep"},
         token: {"auth": {"share_allowed": False}}},
    )
    with caplog.at_level("WARNING"):
        level = assemble_levels(agent_name="claude", **{path_kw: f})[level_idx]
    # The own-scope contribution survives; the upward table is gone.
    assert _marker_of(level, own_scope) == "keep"
    assert dict.get(level, token, __MISSING__) is __MISSING__
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
    # it warns). ⚑ The file's own tables are FLAT since the flatten; the default level
    # is built from it all the same, and is empty.
    agent = _write(
        tmp_path / "agent.yaml",
        {
            "self": {"env": {"M": "cm"}},
            "system": {"auth": {"share_allowed": False}},
        },
    )
    with caplog.at_level("WARNING"):
        levels = assemble_levels(agent_name="claude", agent_path=agent)
    # The two agent levels are intact and carry NO system node.
    assert (
        dict.get(levels[AGENT_ACTIVE]["agent"]["claude"]["env"], "M", __MISSING__) == "cm"
    )
    assert len(levels[AGENT_DEFAULT]) == 0
    assert dict.get(levels[AGENT_ACTIVE], "system", __MISSING__) is __MISSING__
    assert dict.get(levels[AGENT_DEFAULT], "system", __MISSING__) is __MISSING__
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
        dict.get(base_level["system"]["auth"], "share_allowed", __MISSING__) is True
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
    assert dict.get(box_level["box"], "image", __MISSING__) == "img"
    assert dict.get(box_level, "meta", __MISSING__) is __MISSING__
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
        {own_scope: {_MARKER_KEY[own_scope]: "keep"}, "meta": {"box": {"mode": "x"}}},
    )
    with caplog.at_level("WARNING"):
        level = assemble_levels(agent_name="claude", **{path_kw: f})[level_idx]
    assert _marker_of(level, own_scope) == "keep"
    assert dict.get(level, "meta", __MISSING__) is __MISSING__
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
        dict.get(base_level["system"]["auth"], "share_allowed", __MISSING__) is True
    )
    # meta NOT exempt: the top-level meta table dropped, with a warning.
    assert dict.get(base_level, "meta", __MISSING__) is __MISSING__
    assert _meta_warns(caplog, base), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


@pytest.mark.writes_undeclared(
    "box.meta", "box.meta.mode",
    reason="the nested <scope>.meta ride-through is the whole subject: "
           "_drop_upward_scopes looks at the TOP-LEVEL view only, so an undeclared "
           "box.meta node has to be present for the test to prove it survives.",
)
def test_nested_scope_meta_is_untouched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The drop is TOP-LEVEL ONLY: the loop never descends. A NESTED node named `meta` —
    # here a retired `<scope>.meta` spelling, which is NOT a key and NOT the workset
    # identity (that is the TOP-LEVEL `meta.workset` table, covered below) — rides through
    # assembly untouched and unwarned. Refusing an undeclared key is the VALIDATOR's job;
    # the drop only ever looks at the top-level view.
    # ⚑ `box.image` rides beside the undeclared node deliberately: the box table must
    # carry a DECLARED sibling, or the `box` node itself is an empty fabricated subtree
    # rather than the scaffolding a real downward contribution builds.
    ws = _write(
        tmp_path / "ws.yaml",
        {"workset": {"template": "keep"},
         "box": {"image": "img", "meta": {"mode": "x"}}},
    )
    with caplog.at_level("WARNING"):
        ws_level = assemble_levels(agent_name="claude", workset_path=ws)[WORKSET]
    # The nested node survives verbatim; the sibling own-scope key too.
    assert _marker_of(ws_level, "workset") == "keep"
    assert dict.get(ws_level["box"], "image", __MISSING__) == "img"
    assert isinstance(dict.get(ws_level["box"], "meta"), KeyStore)
    assert dict.get(ws_level["box"]["meta"], "mode", __MISSING__) == "x"
    # No meta-drop warning fired (nothing top-level was dropped).
    assert not _meta_warns(caplog, ws), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


def test_workset_meta_table_drops_and_warns_like_every_other_scope(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # ⚑ NO WORKSET-SCOPE CARVE-OUT. A workset root's identity is REGISTRY-BORNE
    # (system-design §Detect) and its settings file carries SETTINGS ONLY, so a
    # top-level `meta.workset` table here is the RETIRED 1.6.0/1.7.x shape that
    # `refuse_retired_workset_identity` hard-refuses — not a marker the tool wrote.
    # It therefore drops AND warns, exactly as at every other scope. (Before the
    # move it dropped SILENTLY, to spare a spurious WARNING on a sanctioned file.)
    ws = _write(
        tmp_path / "ws.yaml",
        {"workset": {"template": "keep"}, "meta": {"workset": {"name": "foo"}}},
    )
    with caplog.at_level("WARNING"):
        ws_level = assemble_levels(agent_name="claude", workset_path=ws)[WORKSET]
    assert _marker_of(ws_level, "workset") == "keep"
    assert dict.get(ws_level, "meta", __MISSING__) is __MISSING__
    warns = _meta_warns(caplog, ws)
    assert warns, [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert "top-level 'meta' table" in warns[0]


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
            "self": {"env": {"M": "cm"}},
            "meta": {"box": {"mode": "x"}},
        },
    )
    with caplog.at_level("WARNING"):
        levels = assemble_levels(agent_name="claude", agent_path=agent)
    # The two agent levels are intact and carry NO meta node.
    assert (
        dict.get(levels[AGENT_ACTIVE]["agent"]["claude"]["env"], "M", __MISSING__) == "cm"
    )
    assert len(levels[AGENT_DEFAULT]) == 0
    assert dict.get(levels[AGENT_ACTIVE], "meta", __MISSING__) is __MISSING__
    assert dict.get(levels[AGENT_DEFAULT], "meta", __MISSING__) is __MISSING__
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
        {own_scope: {_MARKER_KEY[own_scope]: "keep"},
         "binding_derivations": _FORGED_DERIVATIONS},
    )
    with caplog.at_level("WARNING"):
        level = assemble_levels(agent_name="claude", **{path_kw: f})[level_idx]
    assert _marker_of(level, own_scope) == "keep"
    assert dict.get(level, "binding_derivations", __MISSING__) is __MISSING__
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
        dict.get(base_level["system"]["auth"], "share_allowed", __MISSING__) is True
    )
    assert dict.get(base_level, "binding_derivations", __MISSING__) is __MISSING__
    assert _derivations_warns(caplog, base), [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]


@pytest.mark.writes_undeclared(
    "box.binding_derivations", "box.binding_derivations.marker",
    reason="the SCOPE-LOCAL binding_derivations table is the subject: it is not the "
           "top-level reserved node and not a key either, so the test can only show "
           "it rides through undropped by writing the undeclared table itself.",
)
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
    assert dict.get(box_level["box"], "image", __MISSING__) == "img"
    nested = dict.get(box_level["box"], "binding_derivations", __MISSING__)
    assert isinstance(nested, KeyStore)
    assert dict.get(nested, "marker", __MISSING__) == "rides"
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
    zebra = dict.get(box_level, "zebra", __MISSING__)
    assert isinstance(zebra, KeyStore)
    assert dict.get(zebra, "stripes", __MISSING__) == 3
    assert not [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING" and "zebra" in r.getMessage()
    ]


# --------------------------------------------------------------------------- #
# agent.default vs agent.<active> split — TRUE discriminated keys (§2) #
# --------------------------------------------------------------------------- #


def test_agent_tiers_land_in_separate_levels_true_discriminated(tmp_path: Path) -> None:
    # ⚑ TWO FILES, because the two tiers have two homes since the flatten: the ACTIVE
    # tier is the agent's own file (flat under ``self:``, which IS ``agent.claude``) and
    # the ALL-AGENTS tier is the SYSTEM file's ``agent: default:`` table. What the test
    # is about is unchanged: each keeps its TRUE §2d discriminated name.
    agent = _write(tmp_path / "agent.yaml", {"self": {"env": {"M": "cmodel"}}})
    sysf = _write(
        tmp_path / "sys.yaml",
        {"agent": {"default": {"allow_helpers": True, "env": {"M": "dmodel"}}}},
    )
    levels = assemble_levels(
        agent_name="claude", agent_path=agent, system_path=sysf,
    )
    active = levels[AGENT_ACTIVE]["agent"]["claude"]
    default = levels[SYSTEM]["agent"]["default"]
    # active carries ONLY what claude set (NOT pre-merged with default).
    assert dict.get(active["env"], "M", __MISSING__) == "cmodel"
    assert dict.get(active, "allow_helpers", __MISSING__) is __MISSING__
    assert dict.get(default["env"], "M", __MISSING__) == "dmodel"
    assert dict.get(default, "allow_helpers", __MISSING__) is True
    # The discriminator is KEPT (the §2d key form), not collapsed to bare `agent`.
    assert dict.get(levels[AGENT_ACTIVE]["agent"], "claude", __MISSING__) is not __MISSING__
    assert dict.get(levels[SYSTEM]["agent"], "default", __MISSING__) is not __MISSING__
    # No bare `agent.<key>` leaked (would be a §0 violation).
    assert dict.get(levels[AGENT_ACTIVE]["agent"], "env", __MISSING__) is __MISSING__
    assert dict.get(levels[SYSTEM]["agent"], "env", __MISSING__) is __MISSING__
    # And the agent file contributes NOTHING to the all-agents level.
    assert len(levels[AGENT_DEFAULT]) == 0


def test_active_override_not_in_default_and_vice_versa(tmp_path: Path) -> None:
    agent = _write(tmp_path / "agent.yaml", {"self": {"env": {"Y": "g"}}})
    sysf = _write(
        tmp_path / "sys.yaml", {"agent": {"default": {"env": {"X": "d"}}}},
    )
    levels = assemble_levels(
        agent_name="goose", agent_path=agent, system_path=sysf,
    )
    active = levels[AGENT_ACTIVE]["agent"]["goose"]
    default = levels[SYSTEM]["agent"]["default"]
    assert dict.get(active["env"], "Y", __MISSING__) == "g"
    assert dict.get(active["env"], "X", __MISSING__) is __MISSING__  # not the default's
    assert dict.get(default["env"], "X", __MISSING__) == "d"
    assert dict.get(default["env"], "Y", __MISSING__) is __MISSING__  # not the active's


def test_the_agent_file_is_read_under_whichever_node_is_active(tmp_path: Path) -> None:
    # ⚑⚑ WHAT REPLACED "an active agent absent from the file yields an empty level",
    # and the replacement is the model, not a weaker test: there is no per-node
    # discrimination INSIDE the file any more, because the file IS one node's
    # (``agents/<node>/agent.yaml``, and every production caller builds the path from
    # the active id — ``agent_settings_path(std.agents, agent_id)``). So the same bytes
    # read under codex are codex's keys; a "wrong node" file is not a thing the cascade
    # can be handed.
    agent = _write(tmp_path / "agent.yaml", {"self": {"env": {"Y": "c"}}})
    levels = assemble_levels(agent_name="codex", agent_path=agent)
    assert dict.get(levels[AGENT_ACTIVE]["agent"]["codex"]["env"], "Y", __MISSING__) == "c"
    assert dict.get(levels[AGENT_ACTIVE]["agent"], "claude", __MISSING__) is __MISSING__


def test_agent_categories_under_true_discriminated_name(tmp_path: Path) -> None:
    # An agent-tier bind key keeps the §2d form agent.<active-name>.bindings.*.
    agent = _write(
        tmp_path / "agent.yaml",
        {"self": {"bindings": {"ro": {"/g/s": ["/h/s"]}}}},
    )
    active = assemble_levels(agent_name="claude", agent_path=agent)[AGENT_ACTIVE]
    # DEST-KEYED (R-5/R-6): the arm is keyed by destination, the entry is (src[, opts]).
    bind = active["agent"]["claude"]["bindings"]["ro"]["/g/s"]
    assert bind == BindEntry("/h/s", None)


def test_agent_file_state_does_not_ride_the_file_cascade_level(tmp_path: Path) -> None:
    # ⚑⚑ DEFECT D-3 CLOSED, PINNED AS A FACT RATHER THAN LEFT AS AN ABSENCE. Agent-file
    # STATE (model / access / endpoint / …) had TWO live routes: the flat one, through
    # ``state_level`` → ``_agent_state_partial``'s own rung at the launch seam, and a
    # nested ``self: <node>: model:`` that rode THIS level and lost to the flat one
    # silently. The nested spelling is refused now, and this level carries CATEGORIES
    # ONLY — one value, one route. A state key reappearing here is a second rung.
    agent = _write(
        tmp_path / "agent.yaml",
        {"self": {"model": "opus", "env": {"M": "v"}}},
    )
    active = assemble_levels(agent_name="claude", agent_path=agent)[AGENT_ACTIVE]
    assert dict.get(active["agent"]["claude"], "env", __MISSING__) is not __MISSING__
    assert dict.get(active["agent"]["claude"], "model", __MISSING__) is __MISSING__


def test_another_agents_sub_table_in_the_agent_file_refuses(tmp_path: Path) -> None:
    # ⚑⚑ THE PURPOSE HALVED, AND THE SURVIVING HALF INVERTED
    # ([spec:15-21, "self"]). §0 still
    # lets a settings file set ``agent.<name>.*`` for an agent that is NOT active — but
    # not THIS file: ``self:`` IS ``agent.claude``, so ``self: goose:`` reads
    # ``agent.claude.goose``, which names nothing. The keys for another agent belong in
    # a CONTAINING scope's file, spelled canonically. Refused BY NAME, never dropped.
    agent = _write(
        tmp_path / "agent.yaml",
        {"self": {"env": {"M": "cm"}, "goose": {"env": {"M": "gm"}}}},
    )
    with pytest.raises(SettingsError) as exc:
        assemble_levels(agent_name="claude", agent_path=agent)
    message = str(exc.value)
    assert "self.goose" in message
    assert "agent.claude.goose" in message


def test_another_agents_keys_ride_a_containing_scope_file(tmp_path: Path) -> None:
    # The other half, kept whole: with claude active, a SYSTEM-file ``agent.goose.*``
    # keeps its own discriminated name and does not leak into the active level.
    agent = _write(tmp_path / "agent.yaml", {"self": {"env": {"M": "cm"}}})
    sysf = _write(
        tmp_path / "sys.yaml",
        {"agent": {"default": {"model": "dm"}, "goose": {"model": "gm"}}},
    )
    levels = assemble_levels(
        agent_name="claude", agent_path=agent, system_path=sysf,
    )
    assert dict.get(levels[AGENT_ACTIVE]["agent"], "goose", __MISSING__) is __MISSING__
    assert dict.get(levels[SYSTEM]["agent"]["goose"], "model", __MISSING__) == "gm"
    assert dict.get(levels[SYSTEM]["agent"]["default"], "model", __MISSING__) == "dm"


# --------------------------------------------------------------------------- #
# Binds → BindEntry, dest-keyed in EVERY category, refs RAW (S9 / spec §0)     #
# --------------------------------------------------------------------------- #


def test_every_bind_shaped_category_parses_to_dest_keyed_bind_entries(
    tmp_path: Path,
) -> None:
    # ⚑ ALL SIX bind-shaped categories are DEST-KEYED (1-or-2 element entries) as
    # of 2026-08-08c: the ``bindings`` arms at the ARM, the other four at the
    # CATEGORY TOKEN itself. The dest is GUEST-spelled in every one of them,
    # copies (``seeded`` / ``synced``) included — one dest space, two deliveries.
    box = _write(
        tmp_path / "box.yaml",
        {
            "box": {
                "bindings": {
                    "rw": {"~/": ["/host/home"]},
                    "ro": {"/g/s": ["/h/s", "z"]},
                },
                "caches": {"/g/c": ["/h/c"]},
                "seeded": {"/g/t": ["/h/t"]},
                "common": {"/g/p": ["/h/p"]},
                "synced": {"/g/cred": ["/h/cred"]},
            }
        },
    )
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    # R-11: the stored ``~/`` dest is canonicalized to the absolute guest home, so
    # ``~``, ``~/`` and ``/home/agent`` are ONE key rather than three.
    rw_home = box_scope["bindings"]["rw"]["/home/agent"]
    assert rw_home == BindEntry("/host/home", None)
    assert isinstance(rw_home, BindEntry)
    assert box_scope["bindings"]["ro"]["/g/s"] == BindEntry("/h/s", "z")
    for cat, dest, src in [
        ("caches", "/g/c", "/h/c"),
        ("seeded", "/g/t", "/h/t"),
        ("common", "/g/p", "/h/p"),
        ("synced", "/g/cred", "/h/cred"),
    ]:
        # ⚑ THE INVARIANT: the terminal value is a nested KeyStore, NOT an opaque
        # dict leaf. If it were a leaf, the box file's whole map would REPLACE the
        # workset file's whole map instead of merging entry-by-entry — see
        # ``test_a_terminal_category_merges_per_entry_not_wholesale`` below.
        assert isinstance(box_scope[cat], KeyStore)
        assert box_scope[cat][dest] == BindEntry(src, None)
        assert type(box_scope[cat][dest]) is BindEntry


def test_a_terminal_category_merges_per_entry_not_wholesale(tmp_path: Path) -> None:
    # The REASON the terminal value must parse to a nested KeyStore rather than an
    # opaque dict. Two levels each declare ONE entry under ``box.common``; after
    # the cascade merge BOTH survive. Parse the map as a leaf and the box level's
    # single-entry map would wipe the workset's.
    ws = _write(tmp_path / "ws.yaml", {"box": {"common": {"/g/ws": ["/h/ws"]}}})
    box = _write(tmp_path / "box.yaml", {"box": {"common": {"/g/box": ["/h/box"]}}})
    levels = assemble_levels(agent_name="claude", box_path=box, workset_path=ws)
    merged = merge(levels)["box"]["common"]
    assert set(dict.keys(merged)) == {"/g/ws", "/g/box"}
    assert merged["/g/ws"] == BindEntry("/h/ws", None)
    assert merged["/g/box"] == BindEntry("/h/box", None)


def test_refs_left_raw_inside_bind(tmp_path: Path) -> None:
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"$XDG_STATE_HOME/v": ["@workset.vault_rw/x"]}}}},
    )
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    node = box_scope["bindings"]["rw"]
    # NOT expanded — tokens preserved verbatim (S9 / spec §0), on BOTH sides: the
    # ``$XDG`` dest KEY is carried through too (R-11 expands a leading ``~`` only).
    assert list(dict.keys(node)) == ["$XDG_STATE_HOME/v"]
    assert node["$XDG_STATE_HOME/v"].src == "@workset.vault_rw/x"


def test_malformed_bind_arity_raises(tmp_path: Path) -> None:
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"/g/bad": ["a", "b", "c"]}}}},
    )
    # A 3-element entry is the RETIRED name-keyed shape (spec §2a, R-8).
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
    assert dict.get(masks, "/secret", __MISSING__) is True
    # present-None survives as present-None (UNMASK), distinct from absent.
    assert dict.get(masks, "/inherited", __MISSING__) is None
    assert dict.get(masks, "/absent", __MISSING__) is __MISSING__


def test_masks_not_bind_parsed(tmp_path: Path) -> None:
    # A masks leaf is bool/None, never a bind — masks is dest-keyed like the six
    # bind-shaped categories but is NOT one of them; neither bind shape may appear.
    box = _write(tmp_path / "box.yaml", {"box": {"masks": {"/x": True}}})
    masks = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]["masks"]
    assert not isinstance(dict.get(masks, "/x"), (Bind, BindEntry))


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
        dict.get(levels[BASE]["agent"]["default"], "model", __MISSING__) == "bm"
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
    assert dict.get(base, "allow_helpers", __MISSING__) is True
    assert dict.get(base, "bootstrap", __MISSING__) == "tmux"
    # The floor is NOT in any other level.
    assert len(levels[BOX]) == 0


def test_floor_dotted_keys_explode_to_nested() -> None:
    levels = assemble_levels(
        agent_name="claude",
        floor={"box.bindings.rw": {"~/": ["/h/home"]}},
    )
    # The floor key ENDS at the arm (R-5); the dest is the map key, canonicalized.
    bind = levels[BASE]["box"]["bindings"]["rw"]["/home/agent"]
    assert bind == BindEntry("/h/home", None)


def test_base_file_set_value_beats_floor_at_same_key(tmp_path: Path) -> None:
    base_file = _write(tmp_path / "base.yaml", {"agent": {"default": {"bootstrap": "none"}}})
    levels = assemble_levels(
        agent_name="claude",
        base_path=base_file,
        floor={"agent.default.bootstrap": "tmux"},
    )
    # Within the single base level, the base FILE entry wins over the floor; the
    # deep overlay must not clobber sibling floor leaves either.
    assert dict.get(levels[BASE]["agent"]["default"], "bootstrap", __MISSING__) == "none"


def test_overlay_preserves_sibling_floor_leaves(tmp_path: Path) -> None:
    # Two DECLARED agent leaves: the overlay is about the file beating the floor at
    # ONE key without disturbing its sibling, so which two keys they are is arbitrary —
    # but the keyspace is CLOSED (spec §0), so they must be real ones.
    base_file = _write(
        tmp_path / "base.yaml", {"agent": {"default": {"model": "file"}}}
    )
    levels = assemble_levels(
        agent_name="claude",
        base_path=base_file,
        floor={"agent.default.model": "floor", "agent.default.endpoint": "floorB"},
    )
    sub = levels[BASE]["agent"]["default"]
    assert dict.get(sub, "model", __MISSING__) == "file"  # file overlays floor
    # sibling floor leaf survives
    assert dict.get(sub, "endpoint", __MISSING__) == "floorB"


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
                "bindings": {"rw": {"~/": ["/h"]}},  # category subtree
                "masks": {"/secret": True},
            }
        },
    )
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    assert dict.get(box_scope, "image", __MISSING__) == "ghcr.io/x:latest"
    assert isinstance(box_scope["bindings"], KeyStore)
    assert isinstance(box_scope["bindings"]["rw"]["/home/agent"], BindEntry)
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
    # ⚑ A DECLARED box leaf: `model` is an AGENT leaf and never a box one, so
    # `box.model` would not be a key at all. `box.absent` below is only ever READ,
    # which is the point of it — nothing writes it.
    box = _write(tmp_path / "box.yaml", {"box": {"image": None}})
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    assert dict.get(box_scope, "image", __MISSING__) is None
    assert dict.get(box_scope, "absent", __MISSING__) is __MISSING__


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
    # collapse). The agent-file agent.claude.env.M (the ACTIVE level, more
    # specific) wins BY NAME over a system-file agent.claude.env.M default; the
    # system's agent.default.* survives under its own true name.
    snap = _merged(
        tmp_path,
        agent_name="claude",
        agent={"self": {"env": {"M": "cm"}}},
        # System file = a legal downward source for agent.* defaults.
        system={"agent": {"default": {"model": "dm"},
                          "claude": {"env": {"M": "sysm"}}}},
    )
    # The more-specific agent-active level wins for claude (over the system file).
    assert dict.get(snap["agent"]["claude"]["env"], "M", __MISSING__) == "cm"
    # agent.default.* survives by its own true name (NOT erased / collapsed).
    assert dict.get(snap["agent"]["default"], "model", __MISSING__) == "dm"
    # No bare agent.model leaked (a §0 violation).
    assert dict.get(snap["agent"], "model", __MISSING__) is __MISSING__


def test_two_agents_coexist_under_their_own_names(tmp_path: Path) -> None:
    # A scope may carry settings for MULTIPLE agents independently; the bare
    # collapse made agent.claude.* and agent.goose.* indistinguishable. With
    # discriminated keys they coexist under their own §2d names through merge. The
    # per-agent tables ride the SYSTEM file (legal downward source, system ⊃ agent).
    snap = _merged(
        tmp_path,
        agent_name="claude",
        agent={"self": {"env": {"M": "cm"}}},
        system={
            "agent": {
                "default": {"model": "dm"},
                "claude": {"env": {"M": "sys_claude"}},
                "goose": {"model": "sys_goose"},
            }
        },
    )
    # claude resolves to the more-specific agent-active level ("cm"); goose and
    # default coexist by name from the system level, distinct from claude.
    assert dict.get(snap["agent"]["claude"]["env"], "M", __MISSING__) == "cm"
    assert dict.get(snap["agent"]["goose"], "model", __MISSING__) == "sys_goose"
    # agent.default.* also coexists, distinct from both.
    assert dict.get(snap["agent"]["default"], "model", __MISSING__) == "dm"


# --------------------------------------------------------------------------- #
# P6c — standalone TIER MODEL: box tier EMPTY, single file plays the WORKSET   #
# tier; a box.* key resolves for box scope via R2 downward-defaults.           #
# --------------------------------------------------------------------------- #


def _box_enable_vault(snap: KeyStore) -> object:
    box = dict.get(snap, "box", __MISSING__)
    return dict.get(box, "enable_vault", __MISSING__) if isinstance(box, KeyStore) else __MISSING__


def test_p6c_standalone_box_key_resolves_via_workset_tier(tmp_path: Path) -> None:
    # STANDALONE TIER MODEL (P6c, spec §2c): a lone box's single workset.yaml
    # now plays the WORKSET tier (box tier EMPTY). A box.* key set in it still
    # resolves for box scope via R2 downward-defaults (box ⊂ workset — the
    # workset-tier read KEEPS box.*). File carries a box-scope override.
    f = _write(tmp_path / "workset.yaml", {"box": {"enable_vault": False}})

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
    assert _box_enable_vault(snap_dropped) is __MISSING__


def test_p6c_standalone_workset_scope_key_also_resolves(tmp_path: Path) -> None:
    # The unification's strict gain: a workset.* key set in the standalone file
    # (previously DROPPED as an upward write when the file was the BOX tier) now
    # survives, because the file is the WORKSET tier and workset.* is its own scope.
    f = _write(
        tmp_path / "workset.yaml",
        {"box": {"enable_vault": False}, "workset": {"template": "w"}},
    )
    snap = merge(
        assemble_levels(agent_name="claude", box_path=None, workset_path=f)
    )
    assert isinstance(dict.get(snap, "workset", __MISSING__), KeyStore)
    assert _marker_of(snap, "workset") == "w"
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
             "self": {"env": {"M": "opus"}}},
        )
        with caplog.at_level("WARNING"):
            levels = assemble_levels(agent_name="claude", agent_path=agentf)
        assert "pref" not in levels[AGENT_ACTIVE]
        assert levels[AGENT_ACTIVE].agent.claude.env["M"] == "opus"
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
        test makes a bind-shaped request a real bind value — the property that
        makes the NESTED-only spelling load-bearing (D5).

        ⚑ The request mirrors the TARGET's shape, and ``agent.claude.common`` is
        now a TERMINAL dest-keyed key: the request therefore carries the same
        ``{box_dest: [src[, opts]]}`` map and its entries are ``BindEntry``.
        """
        box = _write(
            tmp_path / "box.yaml",
            {"pref": {"agent": {"claude": {"common": {"~/d": ["/s"]}}}}},
        )
        levels = assemble_levels(agent_name="claude", box_path=box)
        node = levels[BOX].pref.agent.claude.common
        assert isinstance(node, KeyStore)
        # R-11 canonicalizes the dest on read, in a pref exactly as in its target.
        assert node["/home/agent/d"] == BindEntry("/s", None)


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

    def test_the_message_quotes_a_stored_bool_AS_YAML_SPELLS_IT(
        self, tmp_path,
    ) -> None:
        """DEFECT: the value came back through ``str()``, so a file saying
        ``auto_approve: true`` was quoted at its owner as ``auto_approve: True``
        — a spelling their file does not contain and the CLI does not parse.

        INVERT: drop the ``bool`` arm of ``_stored_spelling`` and this reddens.
        """
        for stored, spelled in ((True, "true"), (False, "false")):
            with pytest.raises(SettingsError) as exc:
                self._refuse(
                    {"agent": {"default": {"auto_approve": stored}}},
                    level="system", path=tmp_path / "s.yaml",
                )
            msg = str(exc.value)
            assert f"`auto_approve: {spelled}`" in msg, stored
            assert str(stored) not in msg, stored

    def test_the_message_cites_no_record_a_user_cannot_look_up(
        self, tmp_path,
    ) -> None:
        """The MAPPING is what helps the reader; the record number that ruled it
        is ours, and naming it in a message sends a user hunting for a document
        they have no access to.  Kept apart from the mapping pin above so the
        two can fail independently."""
        with pytest.raises(SettingsError) as exc:
            self._refuse(
                {"agent": {"default": {"auto_approve": True}}},
                level="system", path=tmp_path / "s.yaml",
            )
        msg = str(exc.value)
        assert "R-41" not in msg
        assert "true → full, false → restricted" in msg

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
        assert "kanibako box set <box> pref.agent.claude.access=full" in msg

    def test_the_cure_is_level_appropriate(self, tmp_path) -> None:
        """``access`` is an AGENT-scope key, so where it may be WRITTEN depends
        on the file it was found in (the same asymmetry the selection refusal
        handles): system writes it directly, box/workset must use the §2h
        request (a bare agent key there is an UPWARD write and is dropped).

        ⚑ *subject* names the AGENT; the verb's own SUBJECT POSITIONAL is a
        separate argument (*box_name*, not passed here), so the pref-legal cures
        carry the ``<box>`` / ``<workset>`` placeholder rather than dropping it.
        """
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
        assert (
            "kanibako workset set <workset> pref.agent.claude.access=full"
            in cures["workset"]
        )
        assert "kanibako box set <box> pref.agent.claude.access=full" in cures["box"]

    def test_the_workset_cure_names_the_workset_verb_AND_a_subject(
        self, tmp_path,
    ) -> None:
        """DEFECT: this cure used to emit ``workset set pref.agent.…`` with NO
        subject, and argparse does not reject it — it binds the KEY to the
        ``workset`` positional and leaves ``key_value`` empty, so the pasted
        line looks for a working set called ``pref.agent.claude.access=full``.

        INVERT: drop the subject from ``_retired_behavior_cure`` and this reddens.
        """
        with pytest.raises(SettingsError) as exc:
            self._refuse(
                {"agent": {"default": {"auto_approve": True}}},
                level="workset", path=tmp_path / "s.yaml", subject="claude",
            )
        cure = str(exc.value).split("Fix: ", 1)[1].splitlines()[0].strip()
        assert cure.startswith("kanibako workset set <workset> ")
        # The subject sits BETWEEN the verb and the key — never the key alone.
        assert "kanibako workset set pref." not in cure
        assert cure.endswith("pref.agent.claude.access=full")

    def test_the_box_cure_names_the_box_WHEN_THE_CALLER_KNOWS_IT(
        self, tmp_path,
    ) -> None:
        """The same threading the SELECTION refusal already does: ``box set``
        needs its positional to be copy-pasteable from outside that box's own
        cwd, and the launch seam (``start.py``) has the name.  Without it the
        line is pasted as-is and lands on whatever box the cwd resolves to.

        Both branches in one test: a caller that knows the box gets the box, and
        one that does not still gets the PLACEHOLDER, never nothing.
        """
        raw = {"pref": {"agent": {"claude": {"auto_approve": True}}}}
        with pytest.raises(SettingsError) as named:
            self._refuse(
                raw, level="box", path=tmp_path / "box.yaml", subject="claude",
                box_name="myproj",
            )
        assert (
            "kanibako box set myproj pref.agent.claude.access=full"
            in str(named.value)
        )
        with pytest.raises(SettingsError) as anon:
            self._refuse(
                raw, level="box", path=tmp_path / "box.yaml", subject="claude",
            )
        assert (
            "kanibako box set <box> pref.agent.claude.access=full"
            in str(anon.value)
        )

    def test_a_box_name_is_ignored_off_the_box_level(self, tmp_path) -> None:
        """No SINGLE box is being refused for at workset scope, so a *box_name*
        passed anyway must not reach the ``workset set`` positional."""
        with pytest.raises(SettingsError) as exc:
            self._refuse(
                {"agent": {"default": {"auto_approve": True}}},
                level="workset", path=tmp_path / "s.yaml", subject="claude",
                box_name="myproj",
            )
        msg = str(exc.value)
        assert "myproj" not in msg
        assert "kanibako workset set <workset> " in msg

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

    @pytest.mark.writes_undeclared(
        "agent.default.auto_approve",
        reason="the subject is that assembly does NOT refuse the RETIRED "
               "auto_approve key, so the retired (and therefore undeclared) key has "
               "to reach the assembled level for the test to mean anything.",
    )
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
    # R-11: the DEST key is canonicalized on read (the value never is).
    assert set(dict.keys(node)) == {"/home/agent/.claude", "/home/agent/v"}
    assert type(dict.__getitem__(node, "/home/agent/.claude")) is BindEntry
    assert dict.__getitem__(node, "/home/agent/.claude") == BindEntry("/h/claude")
    assert dict.__getitem__(node, "/home/agent/v") == BindEntry("/h/v", "ro")


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
    assert dict.__getitem__(node, "/home/agent/b") is None


def test_parse_bind_map_refuses_a_non_mapping_arm() -> None:
    with pytest.raises(SettingsError) as exc:
        parse_bind_map([["/h/a", "~/a"]])
    assert "must be a mapping" in str(exc.value)


def test_parse_bind_map_refuses_a_stale_three_element_entry() -> None:
    # A NAME-keyed arm fed to the dest-keyed parser: the 3-element value is the one
    # arity that CANNOT be a dest-keyed entry, so it is caught loudly.
    with pytest.raises(SettingsError):
        parse_bind_map({"home": ["/h/src", "~/home", "ro"]})


def test_the_file_path_now_parses_bindings_to_bind_entry(tmp_path: Path) -> None:
    # ⚑ P6 flipped the FILE reader: a settings file's bindings arm is DEST-keyed and
    # yields a 2-field ``BindEntry``. (P5 pinned the opposite; the flip is the
    # phase.) The user files had to flip WITH the floor — a merged arm must be
    # homogeneous, because a single arm node holds entries from both.
    path = _write(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"~/home": ["/h/src"]}}}},
    )
    levels = assemble_levels(agent_name="claude", box_path=path)
    entry = levels[BOX]["box"]["bindings"]["rw"]["/home/agent/home"]
    assert type(entry) is BindEntry
    assert entry == BindEntry("/h/src")


def test_the_other_four_categories_flipped_at_the_category_token(
    tmp_path: Path,
) -> None:
    # ⚑⚑ THE FLIP IS NOW UNIVERSAL, but the DEPTH is not. A ``bindings`` arm is
    # terminal at ``bindings.{ro,rw}``; ``caches``/``seeded``/``common``/``synced``
    # are terminal ONE SEGMENT SHALLOWER — the category token itself. Reading them
    # at the arm depth would descend one level too far and treat each DESTINATION
    # as a category token.
    # ⚑ ``seeded``/``synced`` are COPIES and remain copies: only the KEY SHAPE
    # moved. Nothing here turns one into a mount.
    path = _write(
        tmp_path / "box.yaml",
        {"box": {"seeded": {"/g/t": ["/h/t"]}, "caches": {"/g/c": ["/h/c", "z"]}}},
    )
    box_scope = assemble_levels(agent_name="claude", box_path=path)[BOX]["box"]
    assert type(box_scope["seeded"]["/g/t"]) is BindEntry
    assert box_scope["seeded"]["/g/t"] == BindEntry("/h/t", None)
    assert box_scope["caches"]["/g/c"] == BindEntry("/h/c", "z")


def test_a_stale_name_keyed_entry_in_the_four_is_refused_loudly(
    tmp_path: Path,
) -> None:
    # The stale ``{name: [src, dest, opts]}`` spelling is the one arity that cannot
    # be a dest-keyed entry, so a settings file still carrying it FAILS by name
    # rather than being re-read with the destination as mount options (spec §2a
    # "STALE SHAPES ARE REFUSED LOUDLY").
    path = _write(
        tmp_path / "box.yaml",
        {"box": {"seeded": {"t": ["/h/t", "/g/t", "ro"]}}},
    )
    with pytest.raises(SettingsError):
        assemble_levels(agent_name="claude", box_path=path)
    # And the retired sub-table form, at the category token depth.
    nested = _write(
        tmp_path / "box2.yaml",
        {"box": {"common": {"p": {"src": "/h/p", "dest": "/g/p"}}}},
    )
    with pytest.raises(SettingsError) as exc:
        assemble_levels(agent_name="claude", box_path=nested)
    assert "sub-table" in str(exc.value)


def test_a_floor_key_deeper_than_the_arm_is_refused() -> None:
    # The RETIRED producer spelling. It still type-checks all the way to launch, and
    # the ``<name>`` segment would land inside the arm as a sibling of real
    # destinations — a name where a path belongs, which nothing downstream can tell
    # apart. So the floor refuses it by name (R-5/R-10).
    with pytest.raises(SettingsError) as exc:
        assemble_levels(
            agent_name="claude",
            floor={"box.bindings.rw.home": ["/h/home", "~/"]},
        )
    assert "box.bindings.rw" in str(exc.value)
    assert "ENTRY NAME" in str(exc.value)


def test_a_name_keyed_sub_table_under_an_arm_is_refused() -> None:
    # The FILE-side twin of the above: a sub-table under an arm is the retired
    # ``{name: {...}}`` shape (spec §2a "STALE SHAPES ARE REFUSED LOUDLY").
    with pytest.raises(SettingsError) as exc:
        parse_bind_map({"home": {"src": "/h"}})
    assert "sub-table" in str(exc.value)


# --------------------------------------------------------------------------- #
# A file-parse refusal NAMES THE FILE — every non-agent level (§0)             #
# --------------------------------------------------------------------------- #
#
# ⚑ THE DEFECT WAS A SHAPE ASYMMETRY, NOT A MISSING STRING. ``_file_partial`` was the
# ONE partial builder in ``assemble_levels`` handed no ``path``, while every sibling
# (``_drop_upward_scopes``, ``_agent_partial``, ``refuse_pref_table``) took one — so a
# refusal raised UNDER it could name the offending KEY and nothing else, and a user with
# six cascade files was told what was illegal but not where it lived.
#
# BEFORE, at every one of the four scopes:
#   ReservedKeyError: key 'get' is reserved: ... Reserved names: [...]
#   SettingsError: common entry '~/dest' holds a sub-table, ... Re-key the entry ...
# AFTER: the same sentence with ``(in settings file /…/box.yaml)`` appended.
#
# ⚑ TWO refusals, ONE seam: the reserved name comes out of ``KeyStore.__setitem__`` and
# the retired §2a shape out of ``parse_bind_map``, but both are raised under the single
# ``_parse_node`` call, so both are pinned here together.

# The four levels ``_file_partial`` builds, each with the scope token its own file is
# legally spelled against. ``agent`` is absent on purpose — that tier goes through
# ``_agent_partial``, which was already handed a path.
_FILE_LEVELS = [
    pytest.param("base_path", "box", id="base"),
    pytest.param("system_path", "system", id="system"),
    pytest.param("workset_path", "workset", id="workset"),
    pytest.param("box_path", "box", id="box"),
]


@pytest.mark.parametrize("kwarg,scope", _FILE_LEVELS)
def test_a_reserved_leaf_name_refusal_NAMES_THE_FILE(
    tmp_path: Path, kwarg: str, scope: str
) -> None:
    # MUTATION-PROVED: drop the ``path=`` from the matching ``_file_partial`` call in
    # ``assemble_levels`` and this row goes red on the filename assert alone.
    path = _write(tmp_path / f"{kwarg}.yaml", {scope: {"get": "x"}})
    with pytest.raises(SettingsError) as exc:
        assemble_levels(agent_name="claude", **_clean_base(tmp_path, kwarg, path))
    msg = str(exc.value)
    assert "key 'get' is reserved" in msg      # the KEY, as before
    assert str(path) in msg                    # ...and now the FILE
    # ⚑ THE KEY STAYS FIRST. ``cli.main`` prints ``Error: {e}``, so a message re-worded
    # to lead with the filename would bury the defect behind an address.
    assert msg.startswith("key 'get' is reserved")


@pytest.mark.parametrize("kwarg,scope", _FILE_LEVELS)
def test_the_retired_shape_refusal_NAMES_THE_FILE(
    tmp_path: Path, kwarg: str, scope: str
) -> None:
    # The §2a name-keyed shape rides the SAME threaded path as the reserved name; it is
    # the same defect seen at the other refusal site, not a separate fix.
    path = _write(
        tmp_path / f"{kwarg}.yaml",
        {scope: {"common": {"~/dest": {"entryname": {"src": "/h/a"}}}}},
    )
    with pytest.raises(SettingsError) as exc:
        assemble_levels(agent_name="claude", **_clean_base(tmp_path, kwarg, path))
    msg = str(exc.value)
    assert "sub-table" in msg                  # the retired SHAPE, as before
    assert str(path) in msg                    # ...and now the FILE


@pytest.mark.parametrize("kwarg,scope", _FILE_LEVELS)
def test_a_clean_file_at_the_same_scope_assembles(
    tmp_path: Path, kwarg: str, scope: str
) -> None:
    """NON-VACUITY: the same call on the same scope succeeds with no reserved name, so
    the rows above pin the refusal and not a broken fixture."""
    marker = _MARKER_KEY.get(scope, "image")
    path = _write(tmp_path / f"{kwarg}.yaml", {scope: {marker: "ok"}})
    levels = assemble_levels(
        agent_name="claude", **_clean_base(tmp_path, kwarg, path)
    )
    assert len(levels) == 6


def _clean_base(tmp_path: Path, kwarg: str, path: Path) -> dict[str, Path]:
    """The ``assemble_levels`` path kwargs for a probe at ONE level.

    ⚑ ``base_path`` is ALWAYS pinned, to an empty file when it is not the level under
    test: left at ``None`` it falls back to ``settings_base_path()`` and the probe would
    read the real machine's base settings.
    """
    kwargs = {kwarg: path}
    if kwarg != "base_path":
        kwargs["base_path"] = _write(tmp_path / "clean_base.yaml", {})
    return kwargs


# --------------------------------------------------------------------------- #
# ...AND SO DOES THE AGENT TIER — the same wrap, the other partial builder      #
# --------------------------------------------------------------------------- #
#
# ⚑ ``_agent_partial`` ALREADY TOOK A PATH and forwarded it only to ``level_table``, so
# the BOUNDARY refusal (a nested ``self.<sub>:`` table) named the file while every refusal
# out of its OWN parse did not. Half a fix reads exactly like a whole one from the outside:
# one agent-file defect named the file and the next one did not.
#
# BEFORE:  ReservedKeyError: key 'get' is reserved: ... Reserved names: [...]
#          SettingsError: common entry '~/dest' holds a sub-table, ... Re-key the entry ...
# AFTER:   the same sentence with ``(in settings file /…/agent.yaml)`` appended.
#
# ⚑ The exception TYPE changes with it: the reserved name escaped as ``ReservedKeyError``
# here, where the file tiers re-raise as ``SettingsError``. Both are caught by the CLI, but
# the two tiers now agree — which is what sharing ONE wrap buys.

def _agent_probe(tmp_path: Path, doc: dict) -> Path:
    """One agent file, with ``base_path`` pinned clean by the caller (see :func:`_clean_base`)."""
    return _write(tmp_path / "agent.yaml", doc)


@pytest.mark.parametrize("doc,needle", [
    pytest.param(
        {"self": {"env": {"get": "x"}}}, "key 'get' is reserved", id="reserved-name",
    ),
    pytest.param(
        {"self": {"common": {"~/dest": {"entryname": {"src": "/h/a"}}}}},
        "sub-table",
        id="retired-shape",
    ),
])
def test_an_agent_file_parse_refusal_NAMES_THE_FILE(
    tmp_path: Path, doc: dict, needle: str
) -> None:
    # MUTATION-PROVED: drop ``file_path=path`` from ``_agent_partial``'s
    # ``_parse_naming_file`` call and both rows go red on the filename assert alone.
    agent = _agent_probe(tmp_path, doc)
    base = _write(tmp_path / "clean_base.yaml", {})
    with pytest.raises(SettingsError) as exc:
        assemble_levels(agent_name="claude", agent_path=agent, base_path=base)
    msg = str(exc.value)
    assert needle in msg                       # the DEFECT, as before
    assert str(agent) in msg                   # ...and now the FILE
    # ⚑ APPENDED, NEVER RE-WORDED, for the same reason it is at the file tiers:
    # ``cli.main`` prints ``Error: {e}`` and an address must not lead.
    assert msg.endswith(f"(in settings file {agent})")
    assert msg.index(needle) < msg.index("in settings file")


def test_a_clean_agent_file_still_assembles(tmp_path: Path) -> None:
    """NON-VACUITY: the same call with no illegal name builds the six levels, so the rows
    above pin the refusal and not a broken fixture."""
    agent = _agent_probe(tmp_path, {"self": {"env": {"OK": "v"}}})
    base = _write(tmp_path / "clean_base.yaml", {})
    levels = assemble_levels(agent_name="claude", agent_path=agent, base_path=base)
    assert dict.get(levels[AGENT_ACTIVE]["agent"]["claude"]["env"], "OK", __MISSING__) == "v"

"""Unit tests for block 2a — cascade level assembly (settings_assemble).

Covers the brief's checklist: the 6-level count + MOST-SPECIFIC-FIRST order;
``agent.default`` vs ``agent.<active>`` land in the RIGHT separate levels (NOT
pre-merged), each under its TRUE discriminated §2d key (``agent.default.<key>`` /
``agent.<active-name>.<key>`` — NO bare-``agent`` collapse, spec §0 L21/§2d);
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

from kanibako.settings_assemble import assemble_levels
from kanibako.settings_merge import merge
from kanibako.settings_resolve import SettingsError
from kanibako.settings_store import _MISSING, Bind, KeyStore

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
        {"agent": {"default": {"marker": "adef"}, "claude": {"marker": "aact"}}},
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


def test_cross_scope_key_in_box_file_preserved(tmp_path: Path) -> None:
    # §0: namespace is orthogonal to cascade — a box-LEVEL file may set a
    # system.*-scoped key. The partial must preserve it under its own scope.
    box = _write(
        tmp_path / "box.yaml",
        {"box": {"image": "img"}, "system": {"masks": {"/x": None}}},
    )
    box_level = assemble_levels(agent_name="claude", box_path=box)[BOX]
    assert dict.get(box_level["box"], "image", _MISSING) == "img"
    assert isinstance(dict.get(box_level, "system"), KeyStore)
    assert dict.get(box_level["system"]["masks"], "/x", _MISSING) is None


# --------------------------------------------------------------------------- #
# agent.default vs agent.<active> split — TRUE discriminated keys (§2 L138–142) #
# --------------------------------------------------------------------------- #


def test_agent_tiers_land_in_separate_levels_true_discriminated(tmp_path: Path) -> None:
    agent = _write(
        tmp_path / "agent.yaml",
        {
            "agent": {
                "default": {"auto_approve": True, "model": "dmodel"},
                "claude": {"model": "cmodel"},
            }
        },
    )
    levels = assemble_levels(agent_name="claude", agent_path=agent)
    # The active level keeps the §2d key agent.claude.*; the default level keeps
    # agent.default.* — NO bare-`agent` collapse (spec §0 L21/§2d).
    active = levels[AGENT_ACTIVE]["agent"]["claude"]
    default = levels[AGENT_DEFAULT]["agent"]["default"]
    # active carries ONLY what claude set (NOT pre-merged with default).
    assert dict.get(active, "model", _MISSING) == "cmodel"
    assert dict.get(active, "auto_approve", _MISSING) is _MISSING
    assert dict.get(default, "model", _MISSING) == "dmodel"
    assert dict.get(default, "auto_approve", _MISSING) is True
    # The discriminator is KEPT (the §2d key form), not collapsed to bare `agent`.
    assert dict.get(levels[AGENT_ACTIVE]["agent"], "claude", _MISSING) is not _MISSING
    assert dict.get(levels[AGENT_DEFAULT]["agent"], "default", _MISSING) is not _MISSING
    # No bare `agent.<key>` leaked (would be a §0 L21 violation).
    assert dict.get(levels[AGENT_ACTIVE]["agent"], "model", _MISSING) is _MISSING
    assert dict.get(levels[AGENT_DEFAULT]["agent"], "model", _MISSING) is _MISSING


def test_active_override_not_in_default_and_vice_versa(tmp_path: Path) -> None:
    agent = _write(
        tmp_path / "agent.yaml",
        {"agent": {"default": {"x": "d"}, "goose": {"y": "g"}}},
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
        {"agent": {"default": {"x": "d"}, "claude": {"y": "c"}}},
    )
    levels = assemble_levels(agent_name="codex", agent_path=agent)
    # An active agent absent from the file → empty active level (no agent.codex.*).
    assert len(levels[AGENT_ACTIVE]) == 0
    assert dict.get(levels[AGENT_DEFAULT]["agent"]["default"], "x", _MISSING) == "d"


def test_agent_categories_under_true_discriminated_name(tmp_path: Path) -> None:
    # An agent-tier bind key keeps the §2d form agent.<active-name>.bindings.*.
    agent = _write(
        tmp_path / "agent.yaml",
        {"agent": {"claude": {"bindings": {"ro": {"share": ["/h/s", "/g/s"]}}}}},
    )
    active = assemble_levels(agent_name="claude", agent_path=agent)[AGENT_ACTIVE]
    bind = active["agent"]["claude"]["bindings"]["ro"]["share"]
    assert bind == Bind("/h/s", "/g/s", None)


def test_per_agent_independence_other_agent_under_own_name(tmp_path: Path) -> None:
    # §0 L21: a settings file may set agent.<name>.* for an agent that is NOT the
    # active one. With claude active, the file's agent.goose.* must NOT leak into
    # the active level and must keep its own discriminated name (it only takes
    # effect when goose is active next launch).
    agent = _write(
        tmp_path / "agent.yaml",
        {
            "agent": {
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
                "shared": {"p": ["/h/p", "/g/p"]},
                "synced": {"cred": ["/h/cred", "/g/cred"]},
            }
        },
    )
    box_scope = assemble_levels(agent_name="claude", box_path=box)[BOX]["box"]
    rw_home = box_scope["bindings"]["rw"]["home"]
    assert rw_home == Bind("/host/home", "~/", None)
    assert isinstance(rw_home, Bind)
    assert box_scope["bindings"]["ro"]["sock"] == Bind("/h/s", "/g/s", "z")
    for cat, name in [("caches", "c"), ("seeded", "t"), ("shared", "p"), ("synced", "cred")]:
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
        floor={"agent.default.auto_approve": True, "agent.default.bootstrap": "tmux"},
    )
    base = levels[BASE]["agent"]["default"]
    assert dict.get(base, "auto_approve", _MISSING) is True
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


def test_no_machine_path_consulted(monkeypatch) -> None:
    # If anything read machine_config_path(), this would blow up.
    import kanibako.config as cfg

    def _boom() -> Path:
        raise AssertionError("machine_config_path() must NOT be consulted (S14)")

    monkeypatch.setattr(cfg, "machine_config_path", _boom)
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
# (spec §0 L21 per-agent independence + §2d discriminated keys)                 #
# --------------------------------------------------------------------------- #


def _merged(tmp_path: Path, *, agent_name: str, agent: dict, box: dict) -> KeyStore:
    """Assemble the agent + box files and run the block-2b merge — the real path
    a per-agent override or two-agent coexistence travels (cascade by name)."""
    agent_p = _write(tmp_path / "agent.yaml", agent)
    box_p = _write(tmp_path / "box.yaml", box)
    levels = assemble_levels(agent_name=agent_name, agent_path=agent_p, box_path=box_p)
    snap = merge(levels)
    return snap


def test_box_scope_agent_override_survives_and_wins_by_name(tmp_path: Path) -> None:
    # §0 L21: "a box file MAY set an agent.<agent>.* key — that's how a box
    # overrides a specific agent." With the discriminated form, a box-scope
    # agent.claude.model overrides the agent-level agent.claude.model BY NAME
    # (box > agent.<active>) — the capability the bare-`agent` collapse destroyed.
    snap = _merged(
        tmp_path,
        agent_name="claude",
        agent={"agent": {"default": {"model": "dm"}, "claude": {"model": "cm"}}},
        box={"agent": {"claude": {"model": "boxm"}}},
    )
    # The box override wins for claude, under the §2d key agent.claude.model.
    assert dict.get(snap["agent"]["claude"], "model", _MISSING) == "boxm"
    # agent.default.* survives by its own true name (NOT erased / collapsed).
    assert dict.get(snap["agent"]["default"], "model", _MISSING) == "dm"
    # No bare agent.model leaked (a §0 L21 violation).
    assert dict.get(snap["agent"], "model", _MISSING) is _MISSING


def test_two_agents_coexist_under_their_own_names(tmp_path: Path) -> None:
    # A box (or any scope) may carry settings for MULTIPLE agents independently;
    # the bare collapse made agent.claude.* and agent.goose.* indistinguishable.
    # With discriminated keys they coexist under their own §2d names through merge.
    snap = _merged(
        tmp_path,
        agent_name="claude",
        agent={"agent": {"default": {"model": "dm"}, "claude": {"model": "cm"}}},
        box={
            "agent": {
                "claude": {"model": "box_claude"},
                "goose": {"model": "box_goose"},
            }
        },
    )
    # Both per-agent overrides land, each under its own discriminated name.
    assert dict.get(snap["agent"]["claude"], "model", _MISSING) == "box_claude"
    assert dict.get(snap["agent"]["goose"], "model", _MISSING) == "box_goose"
    # agent.default.* also coexists, distinct from both.
    assert dict.get(snap["agent"]["default"], "model", _MISSING) == "dm"

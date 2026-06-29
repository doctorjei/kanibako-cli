"""Unit tests for settings_launch — the block-7b launch-time snapshot read-path.

These pin the PURE logic of the ONE-resolve-per-launch builder + adapter (no
launch I/O): the floor/category/agent-partial/override-bridge fold into the single
snapshot, the category adapter's shape + root-join + box-side box_dest resolution
(equivalent to the old resolve_categories ``space="guest"`` pass), the behavior
read, and the agent-delivery emitter's AGENT_CRITICAL exit-1 safe-fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings_categories import CategoryEntry, reconcile_categories
from kanibako.settings_launch import (
    agent_delivery_mounts,
    build_launch_snapshot,
    effective_behavior,
    snapshot_category_entries,
)
from kanibako.settings_resolve import GUEST_HOME, ResolveCtx
from kanibako.settings_store import Bind, KeyStore


def _ctx() -> ResolveCtx:
    return ResolveCtx(
        agent_name="claude",
        workset_name=None,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


# --------------------------------------------------------------------------- #
# build_launch_snapshot — the fold + assemble→merge→expand                    #
# --------------------------------------------------------------------------- #


def test_behavior_floor_maps_to_scope_qualified_agent_key():
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None,
        agent_path=None,
        workset_path=None,
        box_path=None,
        behavior_floor={"model": "opus", "auto_approve": "true"},
    )
    # OS1: bare floor → agent.default.<key> (the all-agents backstop, §2d/§0 L21 —
    # NO bare agent.<key>).
    assert snap.agent.default.model == "opus"
    assert snap.agent.default.auto_approve == "true"


def test_category_default_table_folds_into_snapshot():
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None,
        agent_path=None,
        workset_path=None,
        box_path=None,
        default_categories={
            "box.bindings.rw.home": ("/h/home", "/home/agent", "Z,U"),
            "box.env.FOO": "bar",
        },
    )
    bind = snap.box.bindings.rw.home
    assert isinstance(bind, Bind)
    assert bind == Bind("/h/home", "/home/agent", "Z,U")
    assert snap.box.env.FOO == "bar"


def test_empty_string_default_suppression_dropped():
    # A ""-suppressed DEFAULT means "disabled" → dropped from the floor (absent),
    # matching resolve_categories' terminal skip (no shipped default uses "").
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None,
        agent_path=None,
        workset_path=None,
        box_path=None,
        default_categories={"box.bindings.rw.home": ""},
    )
    box = snap.box if "box" in snap else KeyStore()
    bindings = box.bindings if "bindings" in box else KeyStore()
    rw = bindings.rw if "rw" in bindings else KeyStore()
    assert "home" not in rw


def test_agent_partial_inserted():
    # 7a partial supplies the default delivery bind under the active agent's
    # DISCRIMINATED slot (agent.<name>.bindings.*; §2d/§0 L21 — NO bare agent).
    agent_partial = KeyStore(
        {"agent": {"claude": {"bindings": {
            "ro": {"share": Bind("/orig", "/box/share", "ro")}}}}}
    )
    snap_default = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        agent_partial=agent_partial,
    )
    assert snap_default.agent.claude.bindings.ro.share.host == "/orig"


def test_override_bridge_repoints_agent_binding_by_name():
    from kanibako.targets.base import BindKind, BindScope, Binding, HostSrcOrigin

    binding = Binding(
        key="share",
        origin=HostSrcOrigin.INSTALL_DIR,
        box_dest="/box/share",
        kind=BindKind.DIR,
        scope=BindScope.AGENT_CRITICAL,
        ro=True,
    )
    agent_partial = KeyStore(
        {"agent": {"claude": {"bindings": {
            "ro": {"share": Bind("/orig", "/box/share", "ro")}}}}}
    )
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        agent_partial=agent_partial,
        binding_overrides={"share": "/user/repoint"},
        descriptor_bindings=[binding],
    )
    # The override bridge wins over 7a's origin default by name, under the SAME
    # discriminated active slot (agent.<active>.bindings.*).
    assert snap.agent.claude.bindings.ro.share.host == "/user/repoint"


# --------------------------------------------------------------------------- #
# snapshot_category_entries — the adapter                                      #
# --------------------------------------------------------------------------- #


def test_adapter_emits_bind_entry_with_box_side_resolution():
    snap = KeyStore(
        {"box": {"bindings": {"rw": {"home": Bind("/h/home", "~/", "Z,U")}}}}
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    assert len(entries) == 1
    e = entries[0]
    assert e.scope == "box"
    assert e.category == "bindings.rw"
    assert e.host_src == "/h/home"
    # box-side ~/ resolved exactly as today's space="guest" (trailing slash kept).
    assert e.box_dest == GUEST_HOME + "/"
    assert e.options == "Z,U"  # per-entry opts override carried.


def test_adapter_root_joins_relative_host_src():
    # The agent scope is DISCRIMINATED (agent.<active>.*); the adapter does the
    # §2d active-over-default pick and emits the BARE agent scope + group.
    snap = KeyStore(
        {"agent": {"claude": {"shared": {
            "plugins": Bind("plugins", "/box/plugins", None)}}}}
    )
    roots = {"agent.shared": "/data/agents/claude"}
    entries = snapshot_category_entries(
        snap, active_agent="claude", box_ctx=_ctx(), scope_roots=roots,
    )
    assert entries[0].scope == "agent"  # BARE scope token (not the discriminator).
    assert entries[0].host_src == "/data/agents/claude/plugins"
    # rw category default options (shared → Z,U) when opts is None.
    assert entries[0].options == "Z,U"


def test_adapter_absolute_host_src_not_joined():
    snap = KeyStore(
        {"agent": {"claude": {"shared": {"x": Bind("/abs/x", "/box/x", None)}}}}
    )
    roots = {"agent.shared": "/data/agents/claude"}
    entries = snapshot_category_entries(
        snap, active_agent="claude", box_ctx=_ctx(), scope_roots=roots,
    )
    assert entries[0].host_src == "/abs/x"


def test_adapter_active_over_default_pick():
    # §2d L368: the active slot wins a name; agent.default fills the gaps. Both an
    # active-only and a default-only shared bind survive (no sibling clobber).
    snap = KeyStore({"agent": {
        "default": {"shared": {
            "common": Bind("/abs/common", "/box/common", None),
            "plugins": Bind("/abs/default-plugins", "/box/plugins", None),
        }},
        "claude": {"shared": {
            "plugins": Bind("/abs/active-plugins", "/box/plugins", None),
        }},
    }})
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"common", "plugins"}
    assert by_name["plugins"].host_src == "/abs/active-plugins"  # active wins.
    assert by_name["common"].host_src == "/abs/common"           # default fills.
    assert all(e.scope == "agent" for e in entries)


def test_adapter_masks_and_env():
    snap = KeyStore(
        {"box": {"masks": {"/box/secret": True}, "env": {"FOO": "bar"}}}
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    masks = [e for e in entries if e.category == "masks"]
    envs = [e for e in entries if e.category == "env"]
    assert masks[0].box_dest == "/box/secret"
    assert masks[0].host_src is None and masks[0].options == "ro"
    assert envs[0].box_dest == "FOO" and envs[0].options == "bar"


def test_adapter_bind_with_none_leaf_raises():
    from kanibako.settings_resolve import SettingsError

    snap = KeyStore({"box": {"bindings": {"rw": {"bad": None}}}})
    with pytest.raises(SettingsError):
        snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())


def test_adapter_feeds_reconcile_unchanged():
    # End-to-end: adapter entries flow into reconcile_categories cleanly.
    snap = KeyStore(
        {"box": {"bindings": {"rw": {
            "home": Bind("/h/home", "/home/agent", "Z,U"),
            "ws": Bind("/h/ws", "/home/agent/workspace", "Z,U"),
        }}}}
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    rec = reconcile_categories(entries)
    dests = {m.box_dest for m in rec.mounts}
    assert dests == {"/home/agent", "/home/agent/workspace"}
    # depth-sort: shallower (/home/agent) first.
    assert rec.mounts[0].box_dest == "/home/agent"


# --------------------------------------------------------------------------- #
# effective_behavior                                                          #
# --------------------------------------------------------------------------- #


def test_effective_behavior_reads_agent_default_backstop():
    # No active-slot override → the agent.default backstop value is read (§2d L368).
    snap = KeyStore({"agent": {"default": {"model": "opus", "auto_approve": True}}})
    eff = effective_behavior(
        snap, active_agent="claude", keys=["model", "auto_approve", "missing"],
    )
    assert eff == {"model": "opus", "auto_approve": "True"}


def test_effective_behavior_active_over_default():
    # §2d L368: the active slot wins; agent.default fills a gap.
    snap = KeyStore({"agent": {
        "default": {"model": "sonnet", "auto_approve": True},
        "claude": {"model": "opus"},
    }})
    eff = effective_behavior(
        snap, active_agent="claude", keys=["model", "auto_approve"],
    )
    assert eff == {"model": "opus", "auto_approve": "True"}


def test_effective_behavior_omits_present_none():
    # A present-None reset in the WINNING (active) slot SETS + shadows default →
    # omitted (the consumer applies its own default, §3).
    snap = KeyStore({"agent": {
        "default": {"model": "sonnet"},
        "claude": {"model": None},
    }})
    eff = effective_behavior(snap, active_agent="claude", keys=["model"])
    assert eff == {}


def test_effective_behavior_discovers_all_keys_when_keys_none():
    # keys=None (the LIVE default): DISCOVER every scalar behavior leaf across both
    # slots (so undeclared pass-through keys survive), skipping category subtrees.
    snap = KeyStore({"agent": {
        "default": {"model": "sonnet", "auto_approve": True},
        "claude": {
            "model": "opus",            # active wins
            "start_mode": "fresh",      # undeclared pass-through (active only)
            "bindings": {"ro": {"x": Bind("/h", "/b", "ro")}},  # category → skip
            "meta": {"name": "claude"},  # subtree → skip
        },
    }})
    eff = effective_behavior(snap, active_agent="claude")
    assert eff == {
        "model": "opus",            # active over default
        "auto_approve": "True",     # default fills the gap
        "start_mode": "fresh",      # undeclared pass-through discovered
    }
    # category / meta subtrees are NOT behavior → never surface.
    assert "bindings" not in eff and "meta" not in eff


# --------------------------------------------------------------------------- #
# agent_delivery_mounts — the AGENT_CRITICAL exit-1 safe-fail                  #
# --------------------------------------------------------------------------- #


def _agent_mount(name: str, host: str, dest: str = "/box/x", opts: str = "ro"):
    return CategoryEntry(
        category="bindings.ro",
        scope="agent",
        box_dest=dest,
        host_src=host,
        delivery="MOUNT",
        options=opts,
        name=name,
    )


def test_delivery_emits_existing_critical(tmp_path: Path):
    src = tmp_path / "bin"
    src.write_text("x")
    mounts = agent_delivery_mounts(
        [_agent_mount("launcher", str(src))],
        critical_keys=frozenset({"launcher"}),
    )
    assert len(mounts) == 1
    assert mounts[0].destination == "/box/x"


def test_delivery_critical_missing_raises(tmp_path: Path):
    from kanibako.targets.assembly import BindingSourceError

    with pytest.raises(BindingSourceError):
        agent_delivery_mounts(
            [_agent_mount("launcher", str(tmp_path / "gone"))],
            critical_keys=frozenset({"launcher"}),
        )


def test_delivery_noncritical_missing_skipped(tmp_path: Path):
    mounts = agent_delivery_mounts(
        [_agent_mount("share", str(tmp_path / "gone"))],
        critical_keys=frozenset(),
    )
    assert mounts == []


def test_delivery_ignores_non_agent_entries(tmp_path: Path):
    src = tmp_path / "bin"
    src.write_text("x")
    box_entry = CategoryEntry(
        category="bindings.rw", scope="box", box_dest="/home/agent",
        host_src=str(src), delivery="MOUNT", options="Z,U", name="home",
    )
    mounts = agent_delivery_mounts([box_entry], critical_keys=frozenset())
    assert mounts == []


# --------------------------------------------------------------------------- #
# Group-auth capability chain (block #2 — ratified 2026-06-29)                #
# spec §2a L184 / §2b L282 / §2c L315-316,331-332,381 / §2d L399              #
# --------------------------------------------------------------------------- #

from kanibako.settings_launch import (  # noqa: E402
    effective_group_auth,
    group_auth_chain_floor,
)


def _chain_snapshot(mode: str, *, agent_name: str = "claude", **overrides):
    """Build a focused snapshot carrying ONLY the group-auth chain floor."""
    chain = group_auth_chain_floor(
        mode=mode, agent_name=agent_name, **overrides
    )
    snap = build_launch_snapshot(
        agent_name=agent_name,
        ctx=_ctx(),
        system_path=None,
        agent_path=None,
        workset_path=None,
        box_path=None,
        group_auth_chain=chain,
    )
    return snap


def test_chain_primary_resolves_on():
    """PRIMARY: agent.capable → meta.workset.available → workset.enabled →
    meta.box.available → effective True (default-on, the safety-swap baseline)."""
    snap = _chain_snapshot("primary")
    assert effective_group_auth(snap) is True


def test_chain_named_resolves_on():
    """NAMED resolves to on via the active agent's capability (default-on)."""
    assert effective_group_auth(_chain_snapshot("named")) is True


def test_chain_standalone_short_circuits_off():
    """STANDALONE: workset keys are the LITERAL False (spec §2c L315-316) — the
    @-ref RESOLVES (no dangling, closes the gap) and effective is False without
    traversing to the agent tier."""
    snap = _chain_snapshot("standalone")
    assert effective_group_auth(snap) is False
    # The chain keys RESOLVED (present), not dropped as dangling.
    import kanibako.settings_views as views
    box_meta = dict.get(dict.get(snap, "meta"), "box")
    assert views.as_bool(dict.get(box_meta, "group_auth_available")) is False


def test_chain_box_off_overrides_to_off():
    """box.group_auth_on=False over an available workset → effective off
    (effective = available AND on)."""
    snap = _chain_snapshot("primary", box_on_override=False)
    assert effective_group_auth(snap) is False


def test_chain_workset_policy_off_overrides_to_off():
    """A workset group_auth_enabled=False override → effective off for its boxes."""
    snap = _chain_snapshot("named", workset_enabled_override=False)
    assert effective_group_auth(snap) is False


def test_chain_noncapable_agent_off_everywhere():
    """A non-capable agent (agent.<x>.group_auth_capable=false) → off everywhere,
    with no special-casing (the chain handles it)."""
    chain = group_auth_chain_floor(mode="primary", agent_name="claude")
    chain["agent.claude.group_auth_capable"] = False  # a future non-capable agent
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(), system_path=None, agent_path=None,
        workset_path=None, box_path=None, group_auth_chain=chain,
    )
    assert effective_group_auth(snap) is False


def test_chain_default_capable_floor_present():
    """The universal floor agent.default.group_auth_capable=True is seeded (JC-1)."""
    snap = _chain_snapshot("primary")
    agent_default = dict.get(dict.get(snap, "agent"), "default")
    assert dict.get(agent_default, "group_auth_capable") is True


def test_effective_group_auth_no_box_node_fails_closed():
    """effective_group_auth fails CLOSED (False) if the chain floor was not
    injected (no box node) — never launders into True."""
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(), system_path=None, agent_path=None,
        workset_path=None, box_path=None,
    )
    assert effective_group_auth(snap) is False

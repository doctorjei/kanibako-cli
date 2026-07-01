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
# Auth 3-tier SHARING chain (2026-07-01 redesign)                             #
# spec §2a/§2b/§2c/§2d; design FINAL KEY MODEL                                 #
# --------------------------------------------------------------------------- #

from kanibako.settings_launch import (  # noqa: E402
    auth_chain_floor,
    meta_identity_floor as _mid_floor,
    meta_runtime_floor as _mr_floor,
    resolve_auth_source,
)


def _auth_snapshot(
    mode: str,
    *,
    agent_name: str = "claude",
    support: bool = True,
    box_file: dict | None = None,
):
    """Build a focused snapshot carrying the auth chain + the agent capability.

    The capability ``meta.agent.<agent>.auth.share_support`` rides the meta
    identity floor (as it does in the real launch); *box_file* is written to a
    temp settings file to exercise a box-scope override of the settable enables.
    """
    import tempfile
    from kanibako.config_io import dump_doc

    chain = auth_chain_floor(mode=mode, agent_name=agent_name)
    meta_id = _mid_floor(
        box_name="b", project_path="/p", inbox="/i", share_global="/sg",
        share_workset=None, workset_name="__PRIMARY__", agent_name=agent_name,
        agent_real_name=agent_name, agent_auth_share_support=support,
    )
    mr = _mr_floor(
        mode=mode, ws_root_literal=("/ws" if mode != "primary" else None)
    )
    bp = None
    if box_file is not None:
        bp = Path(tempfile.mktemp(suffix=".yaml"))
        dump_doc(bp, box_file)
    return build_launch_snapshot(
        agent_name=agent_name, ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=bp,
        auth_chain=chain, meta_runtime=mr, meta_identity=meta_id,
    )


def test_auth_primary_default_workset_tier():
    """PRIMARY, capable, all-default: both enables true → WORKSET wins
    (precedence workset>global) and global_sync is on."""
    a = resolve_auth_source(_auth_snapshot("primary"), mode="primary")
    assert a.tier == "workset"
    assert a.global_enabled and a.workset_enabled
    assert a.workset_source is not None and a.workset_source.endswith("/auth/claude")
    assert a.global_sync is True
    assert a.shares is True


def test_auth_named_default_workset_tier():
    """NAMED resolves the same as primary (all-default → workset tier)."""
    a = resolve_auth_source(_auth_snapshot("named"), mode="named")
    assert a.tier == "workset"


def test_auth_capability_gating_no_share_support():
    """share_support=False (a non-capable agent) → NO sharing at any tier
    (tier box), regardless of the allow flags (the hard capability floor)."""
    a = resolve_auth_source(_auth_snapshot("primary", support=False), mode="primary")
    assert a.tier == "box"
    assert not a.global_enabled and not a.workset_enabled
    assert a.shares is False


def test_auth_box_opts_out_of_workset_falls_to_global():
    """box.auth.workset_enabled=false → global tier (precedence still workset>global
    but workset is disabled, so global wins)."""
    a = resolve_auth_source(
        _auth_snapshot("primary", box_file={"box": {"auth": {"workset_enabled": False}}}),
        mode="primary",
    )
    assert a.tier == "global"
    assert a.global_enabled and not a.workset_enabled


def test_auth_box_opts_out_of_both_is_private():
    """A box disabling BOTH enables → private (tier box), the distinct-auth
    replacement (no flag, just the settable knobs)."""
    a = resolve_auth_source(
        _auth_snapshot(
            "primary",
            box_file={"box": {"auth": {"workset_enabled": False, "global_enabled": False}}},
        ),
        mode="primary",
    )
    assert a.tier == "box"
    assert a.shares is False


def test_auth_standalone_global_only():
    """STANDALONE (deliberate behavior change): the workset tier degenerates false
    (no workset group), but the GLOBAL tier still applies — a standalone box CAN
    use global/host creds."""
    a = resolve_auth_source(_auth_snapshot("standalone"), mode="standalone")
    assert a.tier == "global"
    assert a.global_enabled and not a.workset_enabled


def test_auth_system_disallow_is_private():
    """system.auth.share_allowed=false → the global gate is off; the workset allow
    defaults to @system so it is off too → private everywhere."""
    a = resolve_auth_source(
        _auth_snapshot(
            "primary", box_file={"system": {"auth": {"share_allowed": False}}}
        ),
        mode="primary",
    )
    assert a.tier == "box"


def test_auth_workset_allow_off_falls_to_global():
    """workset.auth.share_allowed=false (workset opts out) but the global gate is on
    → the box uses the global tier."""
    a = resolve_auth_source(
        _auth_snapshot(
            "primary", box_file={"workset": {"auth": {"share_allowed": False}}}
        ),
        mode="primary",
    )
    assert a.tier == "global"


def test_auth_no_box_node_fails_closed():
    """resolve_auth_source fails CLOSED (tier box) when the chain floor was not
    injected — never launders into sharing."""
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(), system_path=None, agent_path=None,
        workset_path=None, box_path=None,
    )
    a = resolve_auth_source(snap)
    assert a.tier == "box" and a.shares is False


def test_auth_clean_break_no_group_auth_keys():
    """CLEAN BREAK: the old group_auth chain keys are GONE — the floor emits only
    the new auth.* keys, no group_auth_capable / group_auth_on / group_auth_available."""
    chain = auth_chain_floor(mode="primary", agent_name="claude")
    joined = " ".join(chain.keys())
    assert "group_auth" not in joined
    assert "system.auth.share_allowed" in chain
    assert "box.auth.global_enabled" in chain
    assert "box.auth.workset_enabled" in chain
    assert "box.auth.workset_path" in chain
    assert chain["box.auth.workset_path"] == "@workset.auth.path/claude"


def test_auth_capability_mirror_is_ref_to_agent_slot():
    """The box mirror meta.box.agent.auth.share_support is an @-ref to the active
    agent's capability slot (the 29g box.agent mirror pattern)."""
    chain = auth_chain_floor(mode="primary", agent_name="goose")
    assert (
        chain["meta.box.agent.auth.share_support"]
        == "@meta.agent.goose.auth.share_support"
    )


# --------------------------------------------------------------------------- #
# meta.runtime.* materialization (block B1 — spec §1A L230-241)                #
# --------------------------------------------------------------------------- #

from kanibako.settings_launch import meta_runtime_floor  # noqa: E402
from kanibako.settings_resolve import SettingsError as _SettingsError  # noqa: E402


def _ctx_with_config(primary_workset: str = "/data/primary_workset") -> ResolveCtx:
    """A ctx carrying the Layer-1 config foundation so @config.primary_workset
    resolves (mirrors start.py _launch_snapshot_inputs, #3a)."""
    return ResolveCtx(
        agent_name="claude",
        workset_name=None,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
        config={
            "config.data": "/data",
            "config.primary_workset": primary_workset,
        },
    )


def _meta_snapshot(mode: str, *, ws_root_literal: str | None = None, ctx=None):
    """Build a focused snapshot carrying ONLY the meta.runtime floor."""
    meta = meta_runtime_floor(mode=mode, ws_root_literal=ws_root_literal)
    return build_launch_snapshot(
        agent_name="claude",
        ctx=ctx if ctx is not None else _ctx(),
        system_path=None,
        agent_path=None,
        workset_path=None,
        box_path=None,
        meta_runtime=meta,
    )


def _meta_node(snap, *path):
    node = snap
    for seg in path:
        node = dict.get(node, seg)
    return node


def test_meta_runtime_primary_ws_root_resolves_via_config_foundation():
    """PRIMARY: meta.runtime.ws_root = @config.primary_workset → the foundation
    literal (spec §1A L233); meta.workset.path single-sources from it."""
    snap = _meta_snapshot("primary", ctx=_ctx_with_config("/data/primary_workset"))
    runtime = _meta_node(snap, "meta", "runtime")
    assert dict.get(runtime, "ws_root") == "/data/primary_workset"
    assert dict.get(runtime, "project_type") == "primary"
    # ws_settings = @meta.runtime.ws_root/settings.yaml (embedded @-ref).
    assert dict.get(runtime, "ws_settings") == "/data/primary_workset/settings.yaml"


def test_meta_runtime_named_ws_root_is_detected_root_literal():
    """NAMED: meta.runtime.ws_root = the detected workset root literal (spec §1A
    L233); ws_settings derives under it."""
    snap = _meta_snapshot("named", ws_root_literal="/code/kento")
    runtime = _meta_node(snap, "meta", "runtime")
    assert dict.get(runtime, "ws_root") == "/code/kento"
    assert dict.get(runtime, "project_type") == "named"
    assert dict.get(runtime, "ws_settings") == "/code/kento/settings.yaml"


def test_meta_runtime_standalone_ws_root_dir_and_ws_settings_none():
    """STANDALONE: ws_root = the project dir literal; ws_settings = None — a
    whole-value None terminal (spec §1A L235-236 / §2c L415)."""
    snap = _meta_snapshot("standalone", ws_root_literal="/scratch/myproj")
    runtime = _meta_node(snap, "meta", "runtime")
    assert dict.get(runtime, "ws_root") == "/scratch/myproj"
    assert dict.get(runtime, "project_type") == "standalone"
    # None propagates as a real None terminal (the key is PRESENT with value None,
    # not dropped — a whole-value None ref).
    assert dict.__contains__(runtime, "ws_settings")
    assert dict.get(runtime, "ws_settings") is None


def test_meta_workset_path_single_sources_from_ws_root_all_modes():
    """meta.workset.path == meta.runtime.ws_root (UNIFORM all modes, spec §1A L239)."""
    # primary
    snap_p = _meta_snapshot("primary", ctx=_ctx_with_config("/data/pw"))
    assert dict.get(_meta_node(snap_p, "meta", "workset"), "path") == "/data/pw"
    assert (
        dict.get(_meta_node(snap_p, "meta", "workset"), "path")
        == dict.get(_meta_node(snap_p, "meta", "runtime"), "ws_root")
    )
    # named
    snap_n = _meta_snapshot("named", ws_root_literal="/code/kento")
    assert dict.get(_meta_node(snap_n, "meta", "workset"), "path") == "/code/kento"
    # standalone
    snap_s = _meta_snapshot("standalone", ws_root_literal="/scratch/myproj")
    assert dict.get(_meta_node(snap_s, "meta", "workset"), "path") == "/scratch/myproj"


def test_meta_workset_settings_single_sources_and_none_for_standalone():
    """meta.workset.settings == meta.runtime.ws_settings (spec §1A L240); None for
    standalone."""
    snap_n = _meta_snapshot("named", ws_root_literal="/code/kento")
    assert (
        dict.get(_meta_node(snap_n, "meta", "workset"), "settings")
        == "/code/kento/settings.yaml"
    )
    snap_s = _meta_snapshot("standalone", ws_root_literal="/scratch/myproj")
    assert dict.get(_meta_node(snap_s, "meta", "workset"), "settings") is None


def test_meta_box_mode_equals_project_type_all_modes():
    """meta.box.mode == meta.runtime.project_type (the RO identity anchor, spec
    §2b L486)."""
    for mode, lit, ctx in (
        ("primary", None, _ctx_with_config()),
        ("named", "/code/kento", None),
        ("standalone", "/scratch/myproj", None),
    ):
        snap = _meta_snapshot(mode, ws_root_literal=lit, ctx=ctx)
        assert dict.get(_meta_node(snap, "meta", "box"), "mode") == mode
        assert (
            dict.get(_meta_node(snap, "meta", "box"), "mode")
            == dict.get(_meta_node(snap, "meta", "runtime"), "project_type")
        )


def test_meta_views_read_runtime_typed():
    """MetaRuntimeView / MetaBoxView / MetaWorksetView read the materialized keys
    at their EXACT types (Path / Path|None / str)."""
    from pathlib import Path as _Path

    import kanibako.settings_views as views

    # named: every field present + typed.
    snap = _meta_snapshot("named", ws_root_literal="/code/kento")
    rt = views.MetaRuntimeView(_meta_node(snap, "meta", "runtime"))
    assert rt.ws_root == _Path("/code/kento")
    assert rt.ws_settings == _Path("/code/kento/settings.yaml")
    assert rt.project_type == "named"
    bx = views.MetaBoxView(_meta_node(snap, "meta", "box"))
    assert bx.mode == "named"
    ws = views.MetaWorksetView(_meta_node(snap, "meta", "workset"))
    assert ws.path == _Path("/code/kento")
    assert ws.settings == _Path("/code/kento/settings.yaml")

    # standalone: ws_settings / workset.settings are None (typed Path|None).
    snap_s = _meta_snapshot("standalone", ws_root_literal="/scratch/myproj")
    rt_s = views.MetaRuntimeView(_meta_node(snap_s, "meta", "runtime"))
    assert rt_s.ws_settings is None
    assert rt_s.ws_root == _Path("/scratch/myproj")
    ws_s = views.MetaWorksetView(_meta_node(snap_s, "meta", "workset"))
    assert ws_s.settings is None


def test_meta_runtime_floor_requires_literal_for_non_primary():
    """A named/standalone floor needs the resolved ws_root literal (only primary
    uses the @config.primary_workset @-ref)."""
    with pytest.raises(_SettingsError):
        meta_runtime_floor(mode="named")
    with pytest.raises(_SettingsError):
        meta_runtime_floor(mode="standalone")
    # primary ignores the literal.
    floor = meta_runtime_floor(mode="primary")
    assert floor["meta.runtime.ws_root"] == "@config.primary_workset"


def test_meta_runtime_coexists_with_auth_chain():
    """The B1 meta.runtime floor + the auth chain BOTH inject under meta.box.* —
    distinct leaves, no collision (the main launch path passes both)."""
    meta = meta_runtime_floor(mode="primary")
    chain = auth_chain_floor(mode="primary", agent_name="claude")
    meta_id = _mid_floor(
        box_name="b", project_path="/p", inbox="/i", share_global="/sg",
        share_workset=None, workset_name="__PRIMARY__", agent_name="claude",
        agent_real_name="claude", agent_auth_share_support=True,
    )
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx_with_config("/data/pw"),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        meta_runtime=meta, auth_chain=chain, meta_identity=meta_id,
    )
    box_meta = _meta_node(snap, "meta", "box")
    # B1 identity anchor present; the auth mirror capability resolved.
    assert dict.get(box_meta, "mode") == "primary"
    # auth still resolves to sharing (the chain is untouched by B1).
    a = resolve_auth_source(snap, mode="primary")
    assert a.shares is True


# --------------------------------------------------------------------------- #
# meta.* IDENTITY-anchor materialization + @meta.*-routed binds (block B2)     #
# spec §2c/§2d, §0                                                             #
# --------------------------------------------------------------------------- #

from kanibako.settings_launch import meta_identity_floor  # noqa: E402


def _identity_snapshot(
    *,
    box_name="droste",
    project_path="/code/droste",
    inbox="/data/channels/mailboxes/__PRIMARY__/droste",
    share_global="/data/channels/share/__PRIMARY__/droste",
    share_workset="/code/kento/channels/share/droste",
    workset_name="__PRIMARY__",
    agent_name="claude",
    default_categories=None,
    ctx=None,
):
    """Build a snapshot carrying the B2 identity floor + optional @meta.*-routed
    core-bind default tables (matching core-defaults.yaml's meta_ref entries)."""
    ident = meta_identity_floor(
        box_name=box_name,
        project_path=project_path,
        inbox=inbox,
        share_global=share_global,
        share_workset=share_workset,
        workset_name=workset_name,
        agent_name=agent_name,
        agent_real_name=agent_name,
    )
    return build_launch_snapshot(
        agent_name="claude",
        ctx=ctx if ctx is not None else _ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        meta_identity=ident,
        default_categories=default_categories,
    )


def test_meta_identity_box_keys_materialized():
    """meta.box.{name,workspace,inbox,share_global,share_workset} + meta.workset.name
    are REAL keys holding the resolved literals (spec §2c)."""
    snap = _identity_snapshot()
    mb = _meta_node(snap, "meta", "box")
    assert dict.get(mb, "name") == "droste"
    assert dict.get(mb, "workspace") == "/code/droste"
    assert dict.get(mb, "inbox") == "/data/channels/mailboxes/__PRIMARY__/droste"
    assert dict.get(mb, "share_global") == "/data/channels/share/__PRIMARY__/droste"
    assert dict.get(mb, "share_workset") == "/code/kento/channels/share/droste"
    assert dict.get(_meta_node(snap, "meta", "workset"), "name") == "__PRIMARY__"


def test_meta_identity_agent_name_under_discriminated_slot():
    """meta.agent.<a>.name is materialized under the agent's discriminated slot
    (spec §2d L514)."""
    snap = _identity_snapshot(agent_name="claude")
    ma = _meta_node(snap, "meta", "agent", "claude")
    assert dict.get(ma, "name") == "claude"


def test_meta_identity_no_agent_omits_agent_key():
    """A NO-AGENT box (agent_name=None) materializes NO meta.agent.* key."""
    floor = meta_identity_floor(
        box_name="x", project_path="/p", inbox="/i", share_global="/s",
        share_workset=None, workset_name="__STANDALONE__", agent_name=None,
    )
    assert not any(k.startswith("meta.agent.") for k in floor)


def test_meta_identity_standalone_share_workset_none_terminal():
    """STANDALONE: share_workset is a whole-value None terminal — PRESENT with
    value None (spec §2c L469), not dropped (mirrors meta.runtime.ws_settings)."""
    snap = _identity_snapshot(
        share_workset=None, workset_name="__STANDALONE__",
    )
    mb = _meta_node(snap, "meta", "box")
    assert dict.__contains__(mb, "share_workset")
    assert dict.get(mb, "share_workset") is None


def test_workspace_bind_routes_through_meta_box_workspace():
    """box.bindings.rw.workspace = (@meta.box.workspace, ~/workspace) expands to the
    SAME host_src as the proj-attr literal (byte-identical, JC-B2-4)."""
    snap = _identity_snapshot(
        project_path="/code/droste",
        default_categories={
            "box.bindings.rw.workspace": ("@meta.box.workspace", "~/workspace", "Z,U"),
        },
    )
    bind = _meta_node(snap, "box", "bindings", "rw", "workspace")
    assert isinstance(bind, Bind)
    # The @meta.box.workspace ref resolved to str(proj.project_path) — byte-identical
    # to the old `source: project_path` injection.
    assert bind.host == "/code/droste"
    assert bind.box == "~/workspace"
    assert bind.opts == "Z,U"


def test_inbox_bind_routes_through_meta_box_inbox():
    """box.bindings.rw.inbox = (@meta.box.inbox, ~/channels/inbox) expands to the
    SAME host_src as the channels.box_channel_addresses literal (JC-B2-4)."""
    snap = _identity_snapshot(
        inbox="/data/channels/mailboxes/__PRIMARY__/droste",
        default_categories={
            "box.bindings.rw.inbox": ("@meta.box.inbox", "~/channels/inbox"),
        },
    )
    bind = _meta_node(snap, "box", "bindings", "rw", "inbox")
    assert isinstance(bind, Bind)
    assert bind.host == "/data/channels/mailboxes/__PRIMARY__/droste"
    assert bind.box == "~/channels/inbox"


def test_meta_box_view_reads_b2_fields_typed():
    """MetaBoxView reads the B2 leaves at their EXACT types; MetaAgentView reads
    the agent name."""
    import kanibako.settings_views as views

    snap = _identity_snapshot(
        share_workset="/code/kento/channels/share/droste",
    )
    mb = views.MetaBoxView(_meta_node(snap, "meta", "box"))
    assert mb.name == "droste"
    assert mb.workspace == Path("/code/droste")
    assert mb.inbox == Path("/data/channels/mailboxes/__PRIMARY__/droste")
    assert mb.share_global == Path("/data/channels/share/__PRIMARY__/droste")
    assert mb.share_workset == Path("/code/kento/channels/share/droste")
    ma = views.MetaAgentView(_meta_node(snap, "meta", "agent", "claude"))
    assert ma.name == "claude"
    ws = views.MetaWorksetView(_meta_node(snap, "meta", "workset"))
    assert ws.name == "__PRIMARY__"


def test_meta_box_view_standalone_share_workset_none():
    """MetaBoxView.share_workset is None (typed Path|None) for standalone."""
    import kanibako.settings_views as views

    snap = _identity_snapshot(share_workset=None, workset_name="__STANDALONE__")
    mb = views.MetaBoxView(_meta_node(snap, "meta", "box"))
    assert mb.share_workset is None


def test_routed_bind_equivalence_vs_literal_injection():
    """EQUIVALENCE BAR (JC-B2-4): for workspace + inbox, the @meta.*-routed bind
    resolves to the IDENTICAL (host_src, box_dest, opts) the OLD literal-source
    injection produced — proving the route swap is byte-identical."""
    project_path = "/code/droste"
    inbox = "/data/channels/mailboxes/__PRIMARY__/droste"

    # NEW: @meta.* routed (what core-defaults.yaml now emits via meta_ref).
    routed = _identity_snapshot(
        project_path=project_path, inbox=inbox,
        default_categories={
            "box.bindings.rw.workspace": ("@meta.box.workspace", "~/workspace", "Z,U"),
            "box.bindings.rw.inbox": ("@meta.box.inbox", "~/channels/inbox"),
        },
    )
    # OLD: literal proj-attr source (pre-B2 form).
    literal = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories={
            "box.bindings.rw.workspace": (project_path, "~/workspace", "Z,U"),
            "box.bindings.rw.inbox": (inbox, "~/channels/inbox"),
        },
    )
    for key in ("workspace", "inbox"):
        rb = _meta_node(routed, "box", "bindings", "rw", key)
        lb = _meta_node(literal, "box", "bindings", "rw", key)
        assert (rb.host, rb.box, rb.opts) == (lb.host, lb.box, lb.opts), key


# --------------------------------------------------------------------------- #
# box.agent.* mirror (block B5 — spec §2b L380, §0 directional)               #
# --------------------------------------------------------------------------- #
import yaml  # noqa: E402


def _yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_box_agent_mirror_defaults_to_resolved_active_agent():
    # (a) box.agent.<key> with NO box override DEFAULTS to the resolved
    # agent.<active>.<key> (agent.default fallback included).
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus", "auto_approve": "true"},
        default_categories={
            "agent.shared.plugins": ("/store/plugins", "~/.claude/plugins"),
        },
    )
    # The resolved active-agent values (agent.default backstop for behavior; the
    # active slot for the re-rooted share default) are mirrored under box.agent.*.
    assert snap.box.agent.model == snap.agent.default.model == "opus"
    assert snap.box.agent.auto_approve == "true"
    # The whole subtree mirrors — including category subtrees (a Bind leaf).
    mirrored = snap.box.agent.shared.plugins
    assert isinstance(mirrored, Bind)
    assert mirrored == snap.agent.claude.shared.plugins


def test_box_agent_mirror_box_file_override_wins(tmp_path: Path):
    # (b) a box-file box.agent.<key> WINS over the mirrored default.
    box = _yaml(tmp_path / "box.yaml", {"box": {"agent": {"model": "sonnet"}}})
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        behavior_floor={"model": "opus", "auto_approve": "true"},
    )
    # The box override stands; the un-overridden sibling still mirrors the default.
    assert snap.box.agent.model == "sonnet"
    assert snap.box.agent.auto_approve == "true"


def test_box_agent_mirror_override_does_not_leak_to_shared_agent(tmp_path: Path):
    # (c) NO leak — a box.agent.<key> override does NOT mutate agent.<active>.<key>
    # / agent.default.<key> (the shared subtree).
    box = _yaml(tmp_path / "box.yaml", {"box": {"agent": {"model": "sonnet"}}})
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        behavior_floor={"model": "opus"},
    )
    assert snap.box.agent.model == "sonnet"  # box override
    # The shared agent subtree is UNTOUCHED — the mirror is a COPY, not a ref.
    assert snap.agent.default.model == "opus"
    assert "claude" not in snap.agent or "model" not in snap.agent.claude


def test_box_agent_mirror_copy_is_not_an_alias():
    # (c) the materialized box.agent subtree is a FRESH deep copy — mutating a
    # nested box.agent node never reaches the shared agent subtree (no alias).
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories={
            "agent.shared.plugins": ("/store/plugins", "~/.claude/plugins"),
        },
    )
    # The nested category subtree is copied, not aliased.
    assert snap.box.agent.shared is not snap.agent.claude.shared
    # A nested Bind leaf is equal (immutable) but the holding KeyStore is fresh.
    snap.box.agent.shared["plugins"] = Bind("/tweaked", "~/.claude/plugins")
    assert snap.agent.claude.shared.plugins.host == "/store/plugins"


def test_box_agent_mirror_repoints_on_agent_name_change():
    # Re-materialized when box.agent_name changes: agent_name IS the launch-resolved
    # active agent, so a different agent_name mirrors a different subtree.
    common = dict(
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus"},
        default_categories={
            "agent.shared.plugins": ("/claude/plugins", "~/.claude/plugins"),
        },
    )
    snap_claude = build_launch_snapshot(agent_name="claude", **common)
    snap_goose = build_launch_snapshot(agent_name="goose", **common)
    # Both mirror their OWN active agent's re-rooted share default.
    assert snap_claude.box.agent.shared.plugins.host == "/claude/plugins"
    assert snap_goose.box.agent.shared.plugins.host == "/claude/plugins"
    # And the mirror tracks the active slot each names (the goose snapshot's mirror
    # comes from agent.goose, the claude snapshot's from agent.claude).
    assert snap_claude.box.agent.shared.plugins == snap_claude.agent.claude.shared.plugins
    assert snap_goose.box.agent.shared.plugins == snap_goose.agent.goose.shared.plugins


def test_no_agent_box_has_no_box_agent_mirror():
    # NO-AGENT box (box.agent_name <None> → empty agent_name): box.agent.* is
    # empty/absent (no active agent subtree to mirror).
    snap = build_launch_snapshot(
        agent_name="",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus"},
    )
    box = snap.box if "box" in snap else KeyStore()
    assert "agent" not in box


def test_box_agent_mirror_deep_override_keeps_sibling_defaults(tmp_path: Path):
    # A box override of ONE deep category leaf coexists with the mirrored siblings
    # (the gap-fill recurses per-name; the box override of one leaf does not
    # suppress the mirrored sibling leaf at the same depth).
    box = _yaml(
        tmp_path / "box.yaml",
        {"box": {"agent": {"shared": {"plugins": ["/box/plugins", "~/.claude/plugins"]}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        default_categories={
            "agent.shared.plugins": ("/store/plugins", "~/.claude/plugins"),
            "agent.shared.cache": ("/store/cache", "~/.claude/cache"),
        },
    )
    # The box-overridden leaf wins; the sibling default leaf still mirrors.
    assert snap.box.agent.shared.plugins.host == "/box/plugins"
    assert snap.box.agent.shared.cache.host == "/store/cache"
    # No leak to the shared agent subtree.
    assert snap.agent.claude.shared.plugins.host == "/store/plugins"


# --------------------------------------------------------------------------- #
# box.agent.* mirror — EFFECT-LEVEL (the override must change RESOLUTION output) #
# --------------------------------------------------------------------------- #


def test_box_agent_override_changes_effective_behavior(tmp_path: Path):
    # A box.agent.model override must change effective_behavior OUTPUT (not just the
    # subtree) — the box's downward tweak takes EFFECT (§0 L38-40 / §2b L380).
    box = _yaml(tmp_path / "box.yaml", {"box": {"agent": {"model": "sonnet"}}})
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        behavior_floor={"model": "opus", "auto_approve": "true"},
    )
    eff = effective_behavior(snap, active_agent="claude")
    assert eff["model"] == "sonnet"  # box override WON the §2d pick.
    assert eff["auto_approve"] == "true"  # un-overridden sibling = mirrored default.


def test_box_agent_no_override_effective_behavior_identical_to_baseline():
    # EQUIVALENCE GUARD: with NO box.agent override the effective behavior is
    # byte-identical to the agent.default ⊕ agent.<active> pick (overlay is a no-op).
    common = dict(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus", "auto_approve": "true"},
    )
    snap = build_launch_snapshot(**common)
    eff = effective_behavior(snap, active_agent="claude")
    # The pick (agent.default backstop here) — box.agent mirrors it exactly.
    assert eff == {"model": "opus", "auto_approve": "true"}


def test_box_agent_bindings_override_changes_category_entries(tmp_path: Path):
    # A box.agent.bindings.* override must produce a category entry under the box's
    # effective agent (the override feeds category resolution).
    box = _yaml(
        tmp_path / "box.yaml",
        {"box": {"agent": {"shared": {"plugins": ["/box/plugins", "~/.claude/plugins"]}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        default_categories={
            "agent.shared.plugins": ("/store/plugins", "~/.claude/plugins"),
        },
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    plug = [e for e in entries if e.category == "shared" and e.name == "plugins"]
    assert len(plug) == 1, entries
    # The box.agent override host_src WON over the agent-tier default.
    assert plug[0].host_src == "/box/plugins"
    assert plug[0].scope == "agent"  # emitted under the (bare) agent scope token.


def test_box_agent_env_override_changes_category_entries(tmp_path: Path):
    # A box.agent.env.* override appears as an agent-scope env entry.
    box = _yaml(
        tmp_path / "box.yaml",
        {"box": {"agent": {"env": {"MY_VAR": "box_val"}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        default_categories={"agent.env.MY_VAR": "agent_val"},
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    envs = [e for e in entries if e.category == "env" and e.name == "MY_VAR"]
    assert len(envs) == 1, entries
    assert envs[0].options == "box_val"  # box override WON.


def test_box_agent_no_override_category_entries_identical_to_baseline():
    # EQUIVALENCE GUARD (category side): NO box.agent override → the category entry
    # set is byte-identical to the baseline (overlay is a no-op).
    common = dict(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories={
            "agent.shared.plugins": ("/store/plugins", "~/.claude/plugins"),
            "agent.env.MY_VAR": "agent_val",
        },
    )
    snap = build_launch_snapshot(**common)
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    agent_entries = sorted(
        (e.category, e.name, e.host_src, e.options)
        for e in entries if e.scope == "agent"
    )
    # Exactly the agent-tier defaults — the box.agent mirror reproduced them, so the
    # overlay added/changed nothing.
    assert agent_entries == [
        ("env", "MY_VAR", None, "agent_val"),
        ("shared", "plugins", "/store/plugins", "Z,U"),
    ]

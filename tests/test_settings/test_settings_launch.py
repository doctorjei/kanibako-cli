"""Unit tests for settings_launch — the block-7b launch-time snapshot read-path.

These pin the PURE logic of the ONE-resolve-per-launch builder + adapter (no
launch I/O): the floor/category/agent-partial fold into the single
snapshot, the category adapter's shape + root-join + box-side box_dest resolution
(equivalent to the retired by-name resolver's ``space="guest"`` pass), the behavior
read, and the agent-delivery emitter's AGENT_CRITICAL exit-1 safe-fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.settings_categories import CategoryEntry, reconcile_categories
from kanibako.settings.settings_launch import (
    agent_delivery_mounts,
    build_launch_snapshot,
    effective_behavior,
    snapshot_category_entries,
)
from kanibako.settings.settings_resolve import GUEST_HOME, ResolveCtx
from kanibako.settings.settings_store import Bind, BindEntry, KeyStore


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
        behavior_floor={"model": "opus", "allow_helpers": "true"},
    )
    # OS1: bare floor → agent.default.<key> (the all-agents backstop, §2d/§0 —
    # NO bare agent.<key>).
    assert snap.agent.default.model == "opus"
    assert snap.agent.default.allow_helpers == "true"


def test_category_default_table_folds_into_snapshot():
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None,
        agent_path=None,
        workset_path=None,
        box_path=None,
        default_categories={
            # DEST-KEYED arm (R-5): the floor key ENDS at the arm and its value is
            # the whole ``{box_dest: (src[, opts])}`` map.
            "box.bindings.rw": {"/home/agent": ("/h/home", "Z,U")},
            "box.env.FOO": "bar",
        },
    )
    bind = getattr(snap.box.bindings.rw, "/home/agent")
    assert isinstance(bind, BindEntry)
    assert bind == BindEntry("/h/home", "Z,U")
    assert snap.box.env.FOO == "bar"


def test_empty_string_default_suppression_dropped():
    # A ""-suppressed DEFAULT means "disabled" → dropped from the floor (absent),
    # matching the retired by-name resolver's terminal skip (no shipped default uses "").
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None,
        agent_path=None,
        workset_path=None,
        box_path=None,
        default_categories={"box.bindings.rw": {"/home/agent": ""}},
    )
    box = snap.box if "box" in snap else KeyStore()
    bindings = box.bindings if "bindings" in box else KeyStore()
    rw = bindings.rw if "rw" in bindings else KeyStore()
    assert "/home/agent" not in rw


def test_agent_partial_inserted():
    # 7a partial supplies the default delivery bind under the active agent's
    # DISCRIMINATED slot (agent.<name>.bindings.*; §2d/§0 — NO bare agent).
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


def test_agent_partial_surfaces_flat_secret_path():
    # REGRESSION (2026-07-15c): the 2026-07-14b flatten moved the per-agent file's
    # secret_path to the TOP-LEVEL ``self.secret_path`` (self IS agent.<node>), but
    # the cascade reader kept reading the nested ``self.<node>`` sub-table — so an
    # agent-scope secret_path became INVISIBLE to the launch cascade (which drives
    # the token ro-mount + $VAR export), silently breaking persona auth. The active
    # layer must surface the flat secret_path as ``agent.<node>.secret_path``; the
    # all-agents ``default`` layer must NOT (it is the file's own node's secret).
    from kanibako.settings.settings_assemble import _agent_partial

    raw = {"self": {
        "name": "OpenAI Codex CLI",
        "secret_path": {"API_KEY": "/home/agent/.config/personas/navigator/token"},
    }}
    active = _agent_partial(raw, sub_key="navigator℘codex")
    assert (active.agent["navigator℘codex"].secret_path["API_KEY"]
            == "/home/agent/.config/personas/navigator/token")
    # The default layer never carries the file's own secret_path.
    assert _agent_partial(raw, sub_key="default") == KeyStore()


def test_settings_file_repoints_delivery_bind_by_dest(tmp_path: Path):
    # A user-settable ``agent.<name>.bindings.{ro,rw}`` ENTRY written on a scope
    # FILE repoints the descriptor delivery bind's HOST SOURCE through the ORDINARY
    # cascade — the plural-arm route that REPLACED the retired singular
    # ``agent.<name>.binding.<key>`` override bridge. ⚑ The entry is matched BY
    # DESTINATION, not by the descriptor's ``binding.key`` (R-10 dropped the name
    # from the keyspace; the arm is a terminal dest-keyed map, R-5). Exercises the
    # FULL emit path
    # (build_launch_snapshot → snapshot_category_entries → reconcile_categories →
    # agent_delivery_mounts), so it proves the repoint reaches the emitted Mount.
    from kanibako.settings.agent_representation import agent_default_partial
    from kanibako.settings.config_io import dump_doc
    from kanibako.targets.base import (
        AgentInstall, BindKind, BindScope, Binding, HostSrcOrigin, PluginDescriptor,
    )

    # The shipped descriptor delivery bind (claude 'share' = INSTALL_DIR, ro,
    # AGENT_CRITICAL) + a real install whose install_dir EXISTS (so the origin
    # default would itself resolve — the mutation guard below is meaningful).
    orig_share = tmp_path / "orig-share"
    orig_share.mkdir()
    install = AgentInstall(
        name="claude",
        binary=tmp_path / "claude-bin",
        launcher=tmp_path / "claude-launcher",
        install_dir=orig_share,
    )
    binding = Binding(
        key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/box/share",
        kind=BindKind.DIR, scope=BindScope.AGENT_CRITICAL, ro=True,
    )
    desc = PluginDescriptor(command=("claude",), bindings=(binding,), mode={})
    # 7a delivers the descriptor default under agent.claude.bindings.ro.share.
    partial = agent_default_partial(desc, install, node_name="claude")

    # The user repoint: the agent-scope FILE sets an entry at the SAME box
    # DESTINATION with a DIFFERENT existing host source. The agent file sits ABOVE
    # the 7a descriptor-default rung, so it wins the host source at that dest.
    # ⚑ The entry is ONE element — ``[src]``. Under dest-keying the destination is
    # the map KEY and the value is ``[src[, options]]``, so the retired
    # ``{"share": [src, "/box/share"]}`` spelling would parse as an entry named
    # ``share`` whose OPTIONS are ``/box/share`` (R-9's accepted loss: both shapes
    # are 2-element lists, so only the reader's context tells them apart) and BOTH
    # entries would mount.
    repoint = tmp_path / "user-repoint"
    repoint.mkdir()
    agent_file = tmp_path / "agent-settings.yaml"
    dump_doc(
        agent_file,
        {"self": {"claude": {"bindings": {"ro": {
            "/box/share": [str(repoint)]}}}}},
    )

    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=agent_file, workset_path=None, box_path=None,
        agent_partial=partial,
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    rec = reconcile_categories(entries)
    # ⚑ critical_keys are DESTINATIONS now, not descriptor key names (H6).
    mounts = agent_delivery_mounts(
        rec.mounts, critical_keys=frozenset({"/box/share"}),
    )

    sources = {str(m.source) for m in mounts}
    # The file-set arm entry repoints the emitted delivery Mount's source.
    assert str(repoint) in sources
    # MUTATION guard (non-vacuous): the descriptor origin (install_dir) is REPLACED,
    # not carried alongside — if the repoint were ignored the emit would still carry
    # ``orig_share`` and this assert would go RED.
    assert str(orig_share) not in sources


# --------------------------------------------------------------------------- #
# P6a: workset LAYOUT-anchor keys are settable and a user override WINS over    #
# the floor default in the resolved snapshot (settings-conformance).           #
# --------------------------------------------------------------------------- #


def test_workset_anchor_floor_default_resolves_when_unset():
    # With NO workset file, the ``workset.*`` layout anchors resolve to the FLOOR
    # default (byte-identical to today — the floor is the ultimate fallback).
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        workset_anchor={
            "workset.boxes": "/floor/boxes",
            "workset.auth.path": "/floor/auth",
        },
    )
    assert snap.workset.boxes == "/floor/boxes"
    assert snap.workset.auth.path == "/floor/auth"


def test_workset_anchor_user_override_wins_over_floor(tmp_path: Path):
    # A user ``config set workset workset.boxes=…`` writes an EXPLICIT workset-level
    # value; it must OUT-PRECEDE the base floor default in the resolved snapshot.
    # MUTATION-PROOF: if the floor were a hard construct-set value that shadowed the
    # override (or the override were ignored), the snapshot would carry the FLOOR
    # value ``/floor/boxes`` and these asserts go RED.
    from kanibako.settings.config_io import dump_doc

    ws_file = tmp_path / "workset-settings.yaml"
    dump_doc(
        ws_file,
        {"workset": {"boxes": "/override/boxes", "auth": {"path": "/override/auth"}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=ws_file, box_path=None,
        workset_anchor={
            "workset.boxes": "/floor/boxes",
            "workset.auth.path": "/floor/auth",
        },
    )
    # The workset-scope EXPLICIT set wins over the base floor default (workset ⊐ base).
    assert snap.workset.boxes == "/override/boxes"
    assert snap.workset.auth.path == "/override/auth"
    # And it is NOT the floor value (mutation guard made explicit).
    assert snap.workset.boxes != "/floor/boxes"


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


def test_adapter_emits_the_stored_host_src_verbatim():
    """The adapter passes ``host_src`` through UNTOUCHED (spec §2a).

    Replaces the retired ``test_adapter_root_joins_relative_host_src``: the
    assembly-time prepend it asserted is the shape §2a calls FORBIDDEN,
    and the mechanism is gone. Sources are rooted at DECLARATION now, so the
    adapter's whole contract on this axis is "do not touch it".

    The agent scope is DISCRIMINATED (agent.<active>.*); the adapter does the §2d
    active-over-default pick and emits the BARE agent scope token.
    """
    snap = KeyStore(
        {"agent": {"claude": {"common": {
            "plugins": Bind("/data/agents/claude/common/plugins",
                            "/box/plugins", None)}}}}
    )
    entries = snapshot_category_entries(
        snap, active_agent="claude", box_ctx=_ctx(),
    )
    assert entries[0].scope == "agent"  # BARE scope token (not the discriminator).
    assert entries[0].host_src == "/data/agents/claude/common/plugins"
    # rw category default options (common → Z,U) when opts is None.
    assert entries[0].options == "Z,U"


def test_adapter_absolute_host_src_not_joined():
    """The surviving control from the retired pair: an absolute source passes
    through — which is now the ONLY behaviour, for every source shape."""
    snap = KeyStore(
        {"agent": {"claude": {"common": {"x": Bind("/abs/x", "/box/x", None)}}}}
    )
    entries = snapshot_category_entries(
        snap, active_agent="claude", box_ctx=_ctx(),
    )
    assert entries[0].host_src == "/abs/x"


def test_adapter_does_not_root_a_relative_host_src():
    """⚑ A bare-relative source is emitted AS-IS — it is NOT silently rooted.

    Such a value should never reach here (the declaration loaders root it, and
    both write surfaces refuse or absolutise it), but if one does, the adapter must
    not invent a root: an invented root is exactly the silent-wrong-path failure
    §2a exists to prevent. Pinning the pass-through is what makes a
    re-introduced prepend RED.
    """
    snap = KeyStore(
        {"agent": {"claude": {"common": {"p": Bind("plugins", "/box/p", None)}}}}
    )
    entries = snapshot_category_entries(
        snap, active_agent="claude", box_ctx=_ctx(),
    )
    assert entries[0].host_src == "plugins"


def test_adapter_active_over_default_pick():
    # §2d: the active slot wins a name; agent.default fills the gaps. Both an
    # active-only and a default-only common bind survive (no sibling clobber).
    snap = KeyStore({"agent": {
        "default": {"common": {
            "common": Bind("/abs/common", "/box/common", None),
            "plugins": Bind("/abs/default-plugins", "/box/plugins", None),
        }},
        "claude": {"common": {
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
    from kanibako.settings.settings_resolve import SettingsError

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
    # No active-slot override → the agent.default backstop value is read (§2d).
    snap = KeyStore({"agent": {"default": {"model": "opus", "allow_helpers": True}}})
    eff = effective_behavior(
        snap, active_agent="claude", keys=["model", "allow_helpers", "missing"],
    )
    assert eff == {"model": "opus", "allow_helpers": "True"}


def test_effective_behavior_active_over_default():
    # §2d: the active slot wins; agent.default fills a gap.
    snap = KeyStore({"agent": {
        "default": {"model": "sonnet", "allow_helpers": True},
        "claude": {"model": "opus"},
    }})
    eff = effective_behavior(
        snap, active_agent="claude", keys=["model", "allow_helpers"],
    )
    assert eff == {"model": "opus", "allow_helpers": "True"}


def test_effective_behavior_resolves_allow_helpers_default_tier():
    # allow_helpers moved to the AGENT keyspace (spec §2d): with only the
    # agent.default backstop set, that value resolves (the launch gate reads it).
    snap = KeyStore({"agent": {"default": {"allow_helpers": "false"}}})
    eff = effective_behavior(snap, active_agent="claude", keys=["allow_helpers"])
    assert eff == {"allow_helpers": "false"}


def test_effective_behavior_allow_helpers_per_agent_override_wins():
    # §2d: a per-agent allow_helpers overrides the agent.default backstop.
    snap = KeyStore({"agent": {
        "default": {"allow_helpers": "false"},
        "claude": {"allow_helpers": "true"},
    }})
    eff = effective_behavior(snap, active_agent="claude", keys=["allow_helpers"])
    assert eff == {"allow_helpers": "true"}
    # A DIFFERENT active agent that has no override falls back to the default.
    eff_other = effective_behavior(
        snap, active_agent="goose", keys=["allow_helpers"],
    )
    assert eff_other == {"allow_helpers": "false"}


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
        "default": {"model": "sonnet", "allow_helpers": True},
        "claude": {
            "model": "opus",            # active wins
            "custom_leaf": "fresh",     # undeclared pass-through (active only)
            "bindings": {"ro": {"x": Bind("/h", "/b", "ro")}},  # category → skip
            "meta": {"name": "claude"},  # subtree → skip
        },
    }})
    eff = effective_behavior(snap, active_agent="claude")
    assert eff == {
        "model": "opus",            # active over default
        "allow_helpers": "True",    # default fills the gap
        "custom_leaf": "fresh",     # undeclared pass-through discovered
    }
    # category / meta subtrees are NOT behavior → never surface.
    assert "bindings" not in eff and "meta" not in eff


def test_effective_behavior_endpoint_resolves_per_active_node():
    # Block B (persona): endpoint is a per-node scalar read by the SAME §2d pick as
    # model. The active NODE slot wins; a sibling node's endpoint does NOT leak.
    snap = KeyStore({"agent": {
        "default": {"endpoint": ""},  # <None> floor (empty)
        "navigator℘claude": {"endpoint": "http://gemma:9000"},
        "claude": {"endpoint": ""},   # bare node stays unset
    }})
    eff_persona = effective_behavior(snap, active_agent="navigator℘claude")
    assert eff_persona["endpoint"] == "http://gemma:9000"
    # Bare node: the empty floor/slot yields NO usable endpoint (falsy) → the
    # assembler emits no ANTHROPIC_BASE_URL. Non-vacuous vs the persona above.
    eff_bare = effective_behavior(snap, active_agent="claude")
    assert eff_bare.get("endpoint", "") == ""


def test_effective_behavior_endpoint_default_none_omits_emission():
    # <None> = the empty floor default: present but falsy, so assemble_env's
    # `if value:` gate emits nothing (bare/harness-default). Byte-identical to
    # today for a box with no endpoint set.
    snap = KeyStore({"agent": {"default": {"endpoint": "", "model": "opus"}}})
    eff = effective_behavior(snap, active_agent="claude", keys=["endpoint", "model"])
    assert eff.get("endpoint", "") == ""
    assert eff["model"] == "opus"


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
        key=f"agent.claude.bindings.ro.{name}",
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
        key="box.bindings.rw.home",
    )
    mounts = agent_delivery_mounts([box_entry], critical_keys=frozenset())
    assert mounts == []


# --------------------------------------------------------------------------- #
# Auth 3-tier SHARING chain (2026-07-01 redesign)                             #
# spec §2a/§2b/§2c/§2d; design FINAL KEY MODEL                                 #
# --------------------------------------------------------------------------- #

from kanibako.settings.settings_launch import (  # noqa: E402
    auth_chain_floor,
    meta_identity_floor as _mid_floor,
    meta_runtime_floor as _mr_floor,
    resolve_auth_source,
)


def _auth_snapshot(
    mode: str,
    *,
    tmp_path: Path,
    agent_name: str = "claude",
    support: bool = True,
    box_file: dict | None = None,
    system_file: dict | None = None,
    workset_file: dict | None = None,
):
    """Build a focused snapshot carrying the auth chain + the agent capability.

    The capability ``meta.agent.<agent>.auth.share_support`` rides the meta
    identity floor (as it does in the real launch). Each of *box_file*,
    *workset_file*, *system_file* is written to a temp settings file at ITS OWN
    scope path — a settable key must be injected at the scope that OWNS it (spec
    §0 directional enforcement drops a containing-scope key from a lower file, so
    a ``system.*`` gate must ride the SYSTEM file, a ``workset.*`` opt-out the
    WORKSET file; only genuine ``box.*`` settings ride *box_file*).
    """
    from itertools import count

    from kanibako.settings.config_io import dump_doc

    chain = auth_chain_floor(mode=mode, agent_name=agent_name)
    meta_id = _mid_floor(
        box_name="b", project_path="/p", inbox="/i", share_global="/sg",
        share_workset=None, agent_name=agent_name,
        agent_real_name=agent_name, agent_auth_share_support=support,
    )
    mr = _mr_floor(
        mode=mode,
        ws_name={
            "primary": "__PRIMARY__", "standalone": "__STANDALONE__",
        }.get(mode, "ws"),
        ws_root_literal=("/ws" if mode != "primary" else None),
    )

    counter = count()

    def _to_path(data: dict | None) -> Path | None:
        if data is None:
            return None
        p = tmp_path / f"settings-{next(counter)}.yaml"
        dump_doc(p, data)
        return p

    return build_launch_snapshot(
        agent_name=agent_name, ctx=_ctx(),
        system_path=_to_path(system_file), agent_path=None,
        workset_path=_to_path(workset_file), box_path=_to_path(box_file),
        auth_chain=chain, meta_runtime=mr, meta_identity=meta_id,
        # ⚑ P7: ``meta.box.auth.workset_path`` is now the spec's
        # ``@workset.auth.path/@system.agent`` (§2c) rather than a
        # Python-interpolated name, so the §1A SELECTION LEVEL must carry the
        # resolved agent — exactly as the launch does. A blank agent_name is the
        # NO-AGENT box and installs nothing (the embedded ref then coerces to "").
        cli_level=({"system.agent": agent_name} if agent_name else None),
    )


def test_auth_primary_default_workset_tier(tmp_path):
    """PRIMARY, capable, all-default: both enables true → WORKSET wins
    (precedence workset>global) and global_sync is on."""
    a = resolve_auth_source(_auth_snapshot("primary", tmp_path=tmp_path), mode="primary")
    assert a.tier == "workset"
    assert a.global_enabled and a.workset_enabled
    assert a.workset_source is not None and a.workset_source.endswith("/auth/claude")
    assert a.global_sync is True
    assert a.creds_shared is True


def test_auth_named_default_workset_tier(tmp_path):
    """NAMED resolves the same as primary (all-default → workset tier)."""
    a = resolve_auth_source(_auth_snapshot("named", tmp_path=tmp_path), mode="named")
    assert a.tier == "workset"


@pytest.mark.parametrize("agent_name", ["navigator℘claude", "kimi-k3℘claude"])
def test_auth_persona_node_name_with_hyphen_resolves(tmp_path, agent_name):
    """A persona node-name containing ``-`` must resolve the capability MIRROR.

    ``meta.box.agent.auth.share_support`` is the interpolated @-ref
    ``@meta.agent.<node>.auth.share_support``.  When the ref grammar omitted
    ``-`` the name truncated to ``meta.agent.kimi``, the leftover ``-k3℘claude.
    auth.share_support`` survived as a literal, and ``as_bool`` raised
    "expected bool, got str" — i.e. every ``--agent kimi-k3+claude`` launch
    crashed.  ``agent_ref._SAFE_EXTRA`` allows ``-`` in a persona segment, so
    the grammar must too.
    """
    a = resolve_auth_source(
        _auth_snapshot("primary", tmp_path=tmp_path, agent_name=agent_name),
        mode="primary",
    )
    assert isinstance(a.creds_shared, bool)
    assert a.tier == "workset"
    assert a.workset_source is not None
    assert a.workset_source.endswith(f"/auth/{agent_name}")


def test_auth_capability_gating_no_share_support(tmp_path):
    """share_support=False (a non-capable agent) → NO sharing at any tier
    (tier box), regardless of the allow flags (the hard capability floor)."""
    a = resolve_auth_source(
        _auth_snapshot("primary", tmp_path=tmp_path, support=False), mode="primary"
    )
    assert a.tier == "box"
    assert not a.global_enabled and not a.workset_enabled
    assert a.creds_shared is False


def test_auth_box_opts_out_of_workset_falls_to_global(tmp_path):
    """box.auth.workset_enabled=false → global tier (precedence still workset>global
    but workset is disabled, so global wins)."""
    a = resolve_auth_source(
        _auth_snapshot(
            "primary", tmp_path=tmp_path,
            box_file={"box": {"auth": {"workset_enabled": False}}},
        ),
        mode="primary",
    )
    assert a.tier == "global"
    assert a.global_enabled and not a.workset_enabled


def test_auth_box_opts_out_of_both_is_private(tmp_path):
    """A box disabling BOTH enables → private (tier box), the distinct-auth
    replacement (no flag, just the settable knobs)."""
    a = resolve_auth_source(
        _auth_snapshot(
            "primary", tmp_path=tmp_path,
            box_file={"box": {"auth": {"workset_enabled": False, "global_enabled": False}}},
        ),
        mode="primary",
    )
    assert a.tier == "box"
    assert a.creds_shared is False


def test_auth_standalone_global_only(tmp_path):
    """STANDALONE (deliberate behavior change): the workset tier degenerates false
    (no workset group), but the GLOBAL tier still applies — a standalone box CAN
    use global/host creds."""
    a = resolve_auth_source(
        _auth_snapshot("standalone", tmp_path=tmp_path), mode="standalone"
    )
    assert a.tier == "global"
    assert a.global_enabled and not a.workset_enabled


def test_auth_system_disallow_is_private(tmp_path):
    """system.auth.share_allowed=false → the global gate is off; the workset allow
    defaults to @system so it is off too → private everywhere. The gate is set at
    the SYSTEM scope (spec §0: system.* is settable ONLY from the system file —
    injecting it via a box file would be an upward write and be dropped)."""
    a = resolve_auth_source(
        _auth_snapshot(
            "primary", tmp_path=tmp_path,
            system_file={"system": {"auth": {"share_allowed": False}}},
        ),
        mode="primary",
    )
    assert a.tier == "box"


def test_auth_workset_allow_off_falls_to_global(tmp_path):
    """workset.auth.share_allowed=false (workset opts out) but the global gate is on
    → the box uses the global tier. The opt-out is set at the WORKSET scope (spec
    §0: workset.* is settable from the workset file, not a lower box file)."""
    a = resolve_auth_source(
        _auth_snapshot(
            "primary", tmp_path=tmp_path,
            workset_file={"workset": {"auth": {"share_allowed": False}}},
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
    assert a.tier == "box" and a.creds_shared is False


def test_auth_clean_break_no_group_auth_keys():
    """CLEAN BREAK: the old group_auth chain keys are GONE — the floor emits only
    the new auth.* keys, no group_auth_capable / group_auth_on / group_auth_available."""
    chain = auth_chain_floor(mode="primary", agent_name="claude")
    joined = " ".join(chain.keys())
    assert "group_auth" not in joined
    assert "system.auth.share_allowed" in chain
    assert "box.auth.global_enabled" in chain
    assert "box.auth.workset_enabled" in chain
    # change 8: the per-box source root moved to the RO meta anchor; the settable
    # box.auth node keeps ONLY the two enable knobs (no workset_path leaks back).
    assert "box.auth.workset_path" not in chain
    assert "meta.box.auth.workset_path" in chain
    # ⮕ P7: SPELLED as the spec writes it (§2c) rather than interpolated; the
    # per-box variation now arrives through @system.agent (the pref layer + the §1A
    # selection level), which resolves strictly EARLIER than this L4.1 anchor.
    # INVERT: interpolate the name again and the F2 incoherence returns (a --agent
    # launch would resolve the WRONG per-agent credential dir).
    assert chain["meta.box.auth.workset_path"] == "@workset.auth.path/@system.agent"


def test_auth_capability_mirror_is_ref_to_agent_slot():
    """The box mirror meta.box.agent.auth.share_support is an @-ref to the active
    agent's capability slot (the 29g box.agent mirror pattern)."""
    chain = auth_chain_floor(mode="primary", agent_name="goose")
    assert (
        chain["meta.box.agent.auth.share_support"]
        == "@meta.agent.goose.auth.share_support"
    )


# --------------------------------------------------------------------------- #
# change 8 (P6d2): box.auth.workset_path → RO meta.box.auth.workset_path        #
# --------------------------------------------------------------------------- #


def _snap_meta_box_auth_workset_path(snap):
    """Navigate snapshot → meta → box → auth → workset_path, or None if absent."""
    meta = snap.meta if "meta" in snap else None
    if meta is None or "box" not in meta:
        return None
    mbox = meta.box
    if "auth" not in mbox:
        return None
    mauth = mbox.auth
    return mauth.workset_path if "workset_path" in mauth else None


def test_p6d2_meta_anchor_resolves_and_box_auth_has_no_workset_path(tmp_path):
    """change 8: meta.box.auth.workset_path resolves to @workset.auth.path/<agent>
    in the snapshot; the settable box.auth node carries ONLY the enable knobs (the
    source path no longer lives under box.auth)."""
    snap = _auth_snapshot("primary", tmp_path=tmp_path)
    wp = _snap_meta_box_auth_workset_path(snap)
    assert isinstance(wp, str) and wp.endswith("/auth/claude")
    # The settable box.auth node has the two knobs but NOT workset_path (moved to meta).
    box_auth = snap.box.auth
    assert "global_enabled" in box_auth and "workset_enabled" in box_auth
    assert "workset_path" not in box_auth


def test_p6d2_resolved_workset_source_reads_meta_node(tmp_path):
    """EQUIVALENCE BAR: the resolved workset_source is byte-identical to the meta
    anchor's resolved value (the consumer reads the meta node, not box.auth).
    Mutation: pointing the consumer at box.auth (which no longer holds the key)
    would yield None → tier global, breaking this."""
    snap = _auth_snapshot("primary", tmp_path=tmp_path)
    a = resolve_auth_source(snap, mode="primary")
    assert a.tier == "workset"
    assert a.workset_source == _snap_meta_box_auth_workset_path(snap)
    assert a.workset_source is not None and a.workset_source.endswith("/auth/claude")


def test_p6d2_meta_anchor_none_for_standalone(tmp_path):
    """change 7/8: standalone pins meta.box.auth.workset_path = None (the meta-
    anchor-is-None-for-standalone pattern). Mutation: dropping the None pin would
    let @workset.auth.path/<agent> resolve to garbage /<agent>."""
    snap = _auth_snapshot("standalone", tmp_path=tmp_path)
    assert _snap_meta_box_auth_workset_path(snap) is None


def test_p6d2_safety_meta_anchor_dropped_from_settings_file(tmp_path):
    """SAFETY WIN: meta.box.auth.workset_path is meta.* → a box/workset FILE trying
    to set it is DROPPED in assembly (never reaches the resolved source). Mutation:
    if the meta drop leaked, workset_source would become the /evil garbage."""
    # A box file trying to plant a top-level meta table (the only way to reach the
    # dotted key) — dropped by assemble's meta-RO guard.
    snap = _auth_snapshot(
        "primary", tmp_path=tmp_path,
        box_file={"meta": {"box": {"auth": {"workset_path": "/evil"}}}},
    )
    wp = _snap_meta_box_auth_workset_path(snap)
    # The floor's resolved value survives; the /evil injection did NOT.
    assert wp is not None and wp.endswith("/auth/claude")
    assert wp != "/evil"
    a = resolve_auth_source(snap, mode="primary")
    assert a.workset_source is not None and not a.workset_source.startswith("/evil")


def test_p6d2_workset_auth_path_settable_and_overrides_default(tmp_path):
    """change 8: workset.auth.path is the ONLY settable auth-location surface. A
    workset FILE value OVERRIDES the @meta.workset.path/auth floor default, and the
    derived meta.box.auth.workset_path re-resolves against it. Mutation: unregister
    the route / drop the override handling → the custom root is not honored."""
    from kanibako.settings.config_keys import KNOWN_CONFIG_KEYS, _KEY_ROUTES
    # (a) it IS registered settable (P6a) and routes to the workset:auth nested slot.
    assert "workset.auth.path" in KNOWN_CONFIG_KEYS
    assert _KEY_ROUTES["workset.auth.path"] == (("workset", "auth"), "path")
    # (b) a workset-file override is honored end-to-end (out-precedes the base floor).
    snap = _auth_snapshot(
        "primary", tmp_path=tmp_path,
        workset_file={"workset": {"auth": {"path": "/custom/store"}}},
    )
    a = resolve_auth_source(snap, mode="primary")
    assert a.tier == "workset"
    assert a.workset_source == "/custom/store/claude"


def test_p6d2_standalone_scrub_no_agent_garbage(tmp_path):
    """change 7: standalone → workset.auth.path None + meta.box.auth.workset_path
    None + workset tier disabled → workset_source None (NO /<agent> garbage that
    credsync would mkdir against host root). The None pins + resolver scrub are
    belt-and-braces; both are load-bearing."""
    a = resolve_auth_source(
        _auth_snapshot("standalone", tmp_path=tmp_path), mode="standalone"
    )
    assert a.workset_source is None
    assert a.tier != "workset"
    # SCRUB isolation (independent of the standalone None pin): a PRIMARY box that
    # opts OUT of the workset tier resolves the meta anchor to a REAL /auth/claude
    # root, but — tier != "workset" — the resolver MUST scrub workset_source to None
    # so no non-workset AuthSource carries a stray source. Mutation: dropping the
    # ``if tier != "workset": workset_source = None`` scrub surfaces /auth/claude here.
    g = resolve_auth_source(
        _auth_snapshot(
            "primary", tmp_path=tmp_path,
            box_file={"box": {"auth": {"workset_enabled": False}}},
        ),
        mode="primary",
    )
    assert g.tier == "global"
    assert g.workset_source is None


# --------------------------------------------------------------------------- #
# meta.runtime.* materialization (block B1 — spec §1A)                #
# --------------------------------------------------------------------------- #

from kanibako.settings.settings_launch import meta_runtime_floor  # noqa: E402
from kanibako.settings.settings_resolve import SettingsError as _SettingsError  # noqa: E402


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
            "config.agents": "/data/agents",
            "config.primary_workset": primary_workset,
        },
    )


_WS_TOKEN_BY_MODE = {"primary": "__PRIMARY__", "standalone": "__STANDALONE__"}


def _meta_snapshot(
    mode: str, *, ws_root_literal: str | None = None, ws_name: str | None = None,
    ctx=None,
):
    """Build a focused snapshot carrying ONLY the meta.runtime floor.

    *ws_name* defaults to the reserved token for primary/standalone, or the
    named-workset stand-in ``"kento"`` for named — the partition token threaded
    into ``meta_runtime_floor`` (spec §1A ws_name).
    """
    if ws_name is None:
        ws_name = _WS_TOKEN_BY_MODE.get(mode, "kento")
    meta = meta_runtime_floor(
        mode=mode, ws_name=ws_name, ws_root_literal=ws_root_literal,
    )
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
    literal (spec §1A); meta.workset.path single-sources from it."""
    snap = _meta_snapshot("primary", ctx=_ctx_with_config("/data/primary_workset"))
    runtime = _meta_node(snap, "meta", "runtime")
    assert dict.get(runtime, "ws_root") == "/data/primary_workset"
    assert dict.get(runtime, "project_type") == "primary"


def test_meta_runtime_named_ws_root_is_detected_root_literal():
    """NAMED: meta.runtime.ws_root = the detected workset root literal (spec §1A
    ); meta.workset.settings derives under it."""
    snap = _meta_snapshot("named", ws_root_literal="/code/kento")
    runtime = _meta_node(snap, "meta", "runtime")
    assert dict.get(runtime, "ws_root") == "/code/kento"
    assert dict.get(runtime, "project_type") == "named"


def test_meta_runtime_standalone_ws_root_is_the_project_dir():
    """STANDALONE: ws_root = the project dir literal, so the workset tier resolves
    to <root>/settings.yaml (the ROOT file plays the WORKSET tier)."""
    snap = _meta_snapshot("standalone", ws_root_literal="/scratch/myproj")
    runtime = _meta_node(snap, "meta", "runtime")
    assert dict.get(runtime, "ws_root") == "/scratch/myproj"
    assert dict.get(runtime, "project_type") == "standalone"


def test_meta_runtime_has_no_ws_settings_key_in_any_mode():
    """⚑ ``meta.runtime.ws_settings`` is CUT from the keyspace (spec §1A,
    "no longer needed (unified path)").  Under §0's CLOSED KEYSPACE an undeclared key
    is NOT a key, so it must be ABSENT — not present-with-a-value, not an alias.
    (Mutation: re-adding the floor line → RED.)"""
    for snap in (
        _meta_snapshot("primary", ctx=_ctx_with_config("/data/primary_workset")),
        _meta_snapshot("named", ws_root_literal="/code/kento"),
        _meta_snapshot("standalone", ws_root_literal="/scratch/myproj"),
    ):
        runtime = _meta_node(snap, "meta", "runtime")
        assert not dict.__contains__(runtime, "ws_settings")


def test_meta_workset_path_single_sources_from_ws_root_all_modes():
    """meta.workset.path == meta.runtime.ws_root (UNIFORM all modes, spec §1A)."""
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


def test_meta_workset_settings_single_sources_all_modes():
    """meta.workset.settings = @meta.runtime.ws_root/settings.yaml, UNIFORM across ALL
    modes incl. standalone (whose ROOT file plays the workset tier).

    ⚑ EQUIVALENCE BAR for the ``meta.runtime.ws_settings`` CUT: these EXPECTED VALUES
    are UNCHANGED from before the cut.  The cut substituted a one-consumer alias's
    definition into its single consumer, so it removed a hop WITHOUT moving a resolved
    value — and this test, untouched across that change, is the proof."""
    snap_p = _meta_snapshot("primary", ctx=_ctx_with_config("/data/primary_workset"))
    assert (
        dict.get(_meta_node(snap_p, "meta", "workset"), "settings")
        == "/data/primary_workset/settings.yaml"
    )
    snap_n = _meta_snapshot("named", ws_root_literal="/code/kento")
    assert (
        dict.get(_meta_node(snap_n, "meta", "workset"), "settings")
        == "/code/kento/settings.yaml"
    )
    snap_s = _meta_snapshot("standalone", ws_root_literal="/scratch/myproj")
    assert (
        dict.get(_meta_node(snap_s, "meta", "workset"), "settings")
        == "/scratch/myproj/settings.yaml"
    )


def test_meta_runtime_ws_name_per_mode():
    """meta.runtime.ws_name holds the workset partition TOKEN per mode (spec §1A,
    2026-07-04): primary=__PRIMARY__ · named=<detected name> · standalone=
    __STANDALONE__ (P6b — the token threaded in by the caller)."""
    snap_p = _meta_snapshot(
        "primary", ws_name="__PRIMARY__", ctx=_ctx_with_config()
    )
    assert dict.get(_meta_node(snap_p, "meta", "runtime"), "ws_name") == "__PRIMARY__"
    snap_n = _meta_snapshot("named", ws_root_literal="/code/kento", ws_name="kento")
    assert dict.get(_meta_node(snap_n, "meta", "runtime"), "ws_name") == "kento"
    snap_s = _meta_snapshot(
        "standalone", ws_root_literal="/scratch/myproj", ws_name="__STANDALONE__"
    )
    assert (
        dict.get(_meta_node(snap_s, "meta", "runtime"), "ws_name")
        == "__STANDALONE__"
    )


def test_meta_workset_name_single_sources_from_ws_name_all_modes():
    """meta.workset.name resolves VIA the @meta.runtime.ws_name anchor (spec §2c
    , 2026-07-04) — the SAME token per mode B2 formerly set directly.
    Proving it is the ANCHOR: the resolved meta.workset.name EQUALS the resolved
    meta.runtime.ws_name for every mode (would go RED if the anchor were absent /
    the key reverted to a direct literal that could drift)."""
    for mode, kw in (
        ("primary", {"ws_name": "__PRIMARY__", "ctx": _ctx_with_config()}),
        ("named", {"ws_root_literal": "/code/kento", "ws_name": "kento"}),
        (
            "standalone",
            {"ws_root_literal": "/scratch/myproj", "ws_name": "__STANDALONE__"},
        ),
    ):
        snap = _meta_snapshot(mode, **kw)
        ws_name_resolved = dict.get(_meta_node(snap, "meta", "runtime"), "ws_name")
        name = dict.get(_meta_node(snap, "meta", "workset"), "name")
        # The anchor resolved to the runtime token (single source) …
        assert name == ws_name_resolved
        # … and to the exact expected literal for this mode.
        assert name == kw["ws_name"]


def test_meta_workset_name_view_typed():
    """MetaWorksetView.name reads the anchored partition token (str) — the anchor
    surfaces at the view layer for every mode."""
    import kanibako.settings.settings_views as views

    snap = _meta_snapshot("named", ws_root_literal="/code/kento", ws_name="kento")
    ws = views.MetaWorksetView(_meta_node(snap, "meta", "workset"))
    assert ws.name == "kento"


def test_meta_box_mode_equals_project_type_all_modes():
    """meta.box.mode == meta.runtime.project_type (the RO identity anchor, spec
    §2b)."""
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


def test_hostile_box_file_meta_table_cannot_override_snapshot_anchors(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """END-TO-END (spec §0 / clause 4 — the brief's snapshot-level gate): a
    hostile box settings file carrying a TOP-LEVEL ``meta:`` table
    (``meta.box.mode`` / ``meta.workset.path``) must NOT reach the resolved
    launch snapshot — the drop at assembly strips it, so the snapshot's meta
    anchors are the FLOOR values (byte-identical), and a RO warning fires.

    Baseline-RED at 4b3083b: the top-level ``meta:`` table was NOT dropped, so
    the box file's ``meta.box.mode`` flowed into the snapshot and OVERRODE the
    ``meta.runtime.project_type`` identity anchor. GREEN here — the floor's
    ``standalone`` mode + ``/scratch/myproj`` workset path stand; the hostile
    ``named`` / ``/evil`` values are gone. Both directions asserted
    unconditionally (the meta table is a real present top-level table, so the
    "anchor == floor value" assert is NON-vacuous — it fails if the drop is
    removed). This pins the anchors against a hostile FILE end-to-end, which the
    assembly-level pins (no snapshot) do not reach.
    """
    from kanibako.settings.config_io import dump_doc

    # A box file that tries to forge the identity anchors via a top-level meta:.
    hostile = tmp_path / "hostile.yaml"
    dump_doc(
        hostile,
        {
            "box": {"image": "img"},  # a legitimate same-scope key — must survive
            "meta": {
                "box": {"mode": "named"},          # forge the RO mode anchor
                "workset": {"path": "/evil"},      # forge the RO workset path
            },
        },
    )
    meta = meta_runtime_floor(
        mode="standalone", ws_name="__STANDALONE__",
        ws_root_literal="/scratch/myproj",
    )
    with caplog.at_level("WARNING"):
        snap = build_launch_snapshot(
            agent_name="claude",
            ctx=_ctx(),
            system_path=None,
            agent_path=None,
            workset_path=None,
            box_path=hostile,
            meta_runtime=meta,
        )
    # The FLOOR anchors stand byte-identical — the hostile values never landed.
    assert dict.get(_meta_node(snap, "meta", "box"), "mode") == "standalone"
    assert dict.get(_meta_node(snap, "meta", "workset"), "path") == "/scratch/myproj"
    assert (
        dict.get(_meta_node(snap, "meta", "box"), "mode")
        == dict.get(_meta_node(snap, "meta", "runtime"), "project_type")
    )
    # The box's legitimate same-scope key still flows (the drop is surgical).
    assert dict.get(_meta_node(snap, "box"), "image") == "img"
    # The RO-drop warning fired, naming the hostile file + the meta token.
    meta_warns = [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING"
        and "meta" in r.getMessage()
        and str(hostile) in r.getMessage()
    ]
    assert meta_warns, [r.getMessage() for r in caplog.records]


def test_meta_views_read_runtime_typed():
    """MetaRuntimeView / MetaBoxView / MetaWorksetView read the materialized keys
    at their EXACT types (Path / Path|None / str)."""
    from pathlib import Path as _Path

    import kanibako.settings.settings_views as views

    # named: every field present + typed.
    snap = _meta_snapshot("named", ws_root_literal="/code/kento")
    rt = views.MetaRuntimeView(_meta_node(snap, "meta", "runtime"))
    assert rt.ws_root == _Path("/code/kento")
    assert rt.project_type == "named"
    # The view carries NO ws_settings field — the key is CUT (spec §1A);
    # the workset-tier FILE is MetaWorksetView.settings, below.
    assert not hasattr(views.MetaRuntimeView, "ws_settings")
    bx = views.MetaBoxView(_meta_node(snap, "meta", "box"))
    assert bx.mode == "named"
    ws = views.MetaWorksetView(_meta_node(snap, "meta", "workset"))
    assert ws.path == _Path("/code/kento")
    assert ws.settings == _Path("/code/kento/settings.yaml")

    # standalone: workset.settings is the <root>/settings.yaml the ROOT file plays as
    # the WORKSET tier, NOT None.
    snap_s = _meta_snapshot("standalone", ws_root_literal="/scratch/myproj")
    rt_s = views.MetaRuntimeView(_meta_node(snap_s, "meta", "runtime"))
    assert rt_s.ws_root == _Path("/scratch/myproj")
    ws_s = views.MetaWorksetView(_meta_node(snap_s, "meta", "workset"))
    assert ws_s.settings == _Path("/scratch/myproj/settings.yaml")


def test_meta_runtime_floor_requires_literal_for_non_primary():
    """A named/standalone floor needs the resolved ws_root literal (only primary
    uses the @config.primary_workset @-ref)."""
    with pytest.raises(_SettingsError):
        meta_runtime_floor(mode="named", ws_name="kento")
    with pytest.raises(_SettingsError):
        meta_runtime_floor(mode="standalone", ws_name="__STANDALONE__")
    # primary ignores the literal.
    floor = meta_runtime_floor(mode="primary", ws_name="__PRIMARY__")
    assert floor["meta.runtime.ws_root"] == "@config.primary_workset"


def test_meta_runtime_coexists_with_auth_chain():
    """The B1 meta.runtime floor + the auth chain BOTH inject under meta.box.* —
    distinct leaves, no collision (the main launch path passes both)."""
    meta = meta_runtime_floor(mode="primary", ws_name="__PRIMARY__")
    chain = auth_chain_floor(mode="primary", agent_name="claude")
    meta_id = _mid_floor(
        box_name="b", project_path="/p", inbox="/i", share_global="/sg",
        share_workset=None, agent_name="claude",
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
    assert a.creds_shared is True


# --------------------------------------------------------------------------- #
# meta.* IDENTITY-anchor materialization + @meta.*-routed binds (block B2)     #
# spec §2c/§2d, §0                                                             #
# --------------------------------------------------------------------------- #

from kanibako.settings.settings_launch import meta_identity_floor  # noqa: E402


def _identity_snapshot(
    *,
    box_name="droste",
    project_path="/code/droste",
    inbox="/data/channels/mailboxes/__PRIMARY__/droste",
    share_global="/data/channels/share/__PRIMARY__/droste",
    share_workset="/code/kento/channels/share/droste",
    box_settings=None,
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
        box_settings=box_settings,
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
    """meta.box.{name,workspace,inbox,share_global,share_workset} are REAL keys
    holding the resolved literals (spec §2c). (meta.workset.name is now a
    meta_runtime_floor anchor — covered in the B1 block.)"""
    snap = _identity_snapshot()
    mb = _meta_node(snap, "meta", "box")
    assert dict.get(mb, "name") == "droste"
    assert dict.get(mb, "workspace") == "/code/droste"
    assert dict.get(mb, "inbox") == "/data/channels/mailboxes/__PRIMARY__/droste"
    assert dict.get(mb, "share_global") == "/data/channels/share/__PRIMARY__/droste"
    assert dict.get(mb, "share_workset") == "/code/kento/channels/share/droste"
    # B2 no longer sets meta.workset.name (it anchors into meta.runtime.ws_name,
    # which this identity-only snapshot does not carry) — so there is no
    # meta.workset.name in an identity-only snapshot.
    workset_node = dict.get(_meta_node(snap, "meta"), "workset")
    assert workset_node is None or not dict.__contains__(workset_node, "name")


def test_meta_identity_agent_name_under_discriminated_slot():
    """meta.agent.<a>.name is materialized under the agent's discriminated slot
    (spec §2d)."""
    snap = _identity_snapshot(agent_name="claude")
    ma = _meta_node(snap, "meta", "agent", "claude")
    assert dict.get(ma, "name") == "claude"


class TestMetaAgentPath:
    """``meta.agent.<a>.path`` — the agent STORE ROOT (spec §2d) that is also
    §2a's agent DECLARATION ROOT: an abstract-category source stores
    ``@meta.agent.<a>.path/<category>/<leaf>``, so this key MUST resolve or every
    such source dangles."""

    def test_meta_agent_path_materialised_for_node_and_harness(self):
        """A PERSONA materializes BOTH slots. ``load_common`` keys its entries on
        the plugin's own ``Target.name`` (the HARNESS), while this floor is built
        with the ACTIVE NODE — so a node-only materialization would leave the
        harness-keyed refs dangling.

        (Mutation: dropping the harness from the loop → the harness key is absent
        → RED, and a persona's ``common`` ref would resolve to nothing.)"""
        floor = meta_identity_floor(
            box_name="x", project_path="/p", inbox="/i", share_global="/s",
            share_workset=None, agent_name="navigator℘claude",
        )
        assert floor["meta.agent.navigator℘claude.path"] == (
            "@config.agents/navigator℘claude"
        )
        assert floor["meta.agent.claude.path"] == "@config.agents/claude"

    def test_bare_agent_materialises_one_slot(self):
        """node == harness for a bare agent → ONE entry, byte-identical to the
        pre-P3 single-slot shape."""
        floor = meta_identity_floor(
            box_name="x", project_path="/p", inbox="/i", share_global="/s",
            share_workset=None, agent_name="claude",
        )
        paths = {k for k in floor if k.endswith(".path")}
        assert paths == {"meta.agent.claude.path"}

    def test_meta_agent_path_resolves_to_the_store_dir(self):
        """It RESOLVES (the whole point): the @config.agents chain expands to the
        real per-agent store dir in the built snapshot.  The ctx carries the
        Layer-1 ``config.agents`` foundation, as every live caller's does
        (``_launch_snapshot_inputs`` / ``_print_effective_shares``)."""
        snap = _identity_snapshot(agent_name="claude", ctx=_ctx_with_config())
        ma = _meta_node(snap, "meta", "agent", "claude")
        assert dict.get(ma, "path") == "/data/agents/claude"

    def test_view_exposes_path(self):
        """``MetaAgentView.path`` is materialized now — its docstring no longer
        defers it."""
        from kanibako.settings import settings_views as views

        snap = _identity_snapshot(agent_name="claude", ctx=_ctx_with_config())
        ma = views.MetaAgentView(_meta_node(snap, "meta", "agent", "claude"))
        assert ma.path == "/data/agents/claude"


# --------------------------------------------------------------------------- #
# B5 — meta.agent.<a>.{mode, exec, settings} materialization + the reader      #
# spec §2d; the §3.3 rulings ("keep and use" / "it should exist and be used" / #
# "we should be using this"); R-37 (shape in the manifest, members from        #
# descriptors)                                                                 #
# --------------------------------------------------------------------------- #

from kanibako.settings.settings_launch import (  # noqa: E402
    meta_agent_grammar,
    meta_agent_grammar_floor,
)


class TestMetaAgentGrammarFloor:
    """The SINGLE descriptor→keyspace seam materializes each shipped plugin's
    launch grammar EXACTLY as its descriptor (⟵ its ``*-defaults.yaml``)
    declares it — values cross-checked against the descriptor so a yaml edit
    that changes the grammar changes this floor with it (one seam, no drift)."""

    def test_claude_mode_and_exec(self):
        from kanibako.plugins.claude.target import ClaudeTarget

        desc = ClaudeTarget().descriptor
        floor = meta_agent_grammar_floor("claude", desc)
        assert floor["meta.agent.claude.mode"] == {
            "start": [], "continue": ["--continue"],
        }
        assert floor["meta.agent.claude.exec"] == ["-p"]
        # The values ARE the descriptor's (tuples normalized to lists).
        assert floor["meta.agent.claude.mode"] == {
            k: list(v) for k, v in desc.mode.items()
        }
        assert floor["meta.agent.claude.exec"] == list(
            desc.operations["exec"].fragment
        )

    def test_codex_mode_and_exec(self):
        from kanibako.plugins.codex.target import CodexTarget

        floor = meta_agent_grammar_floor("codex", CodexTarget().descriptor)
        assert floor["meta.agent.codex.mode"] == {
            "start": [], "continue": ["resume", "--last"],
        }
        assert floor["meta.agent.codex.exec"] == ["exec"]

    def test_goose_mode_and_exec(self):
        from kanibako.plugins.goose.target import GooseTarget

        floor = meta_agent_grammar_floor("goose", GooseTarget().descriptor)
        assert floor["meta.agent.goose.mode"] == {
            "start": ["session"], "continue": ["session", "--resume"],
        }
        assert floor["meta.agent.goose.exec"] == ["run", "--no-session", "-t"]

    def test_descriptor_less_agent_materializes_nothing(self):
        assert meta_agent_grammar_floor("whatever", None) == {}

    def test_exec_omitted_when_no_exec_operation(self):
        from kanibako.targets.base import PluginDescriptor

        d = PluginDescriptor(command=("a",), bindings=(), mode={"start": ()})
        floor = meta_agent_grammar_floor("a", d)
        assert floor == {"meta.agent.a.mode": {"start": []}}
        assert "meta.agent.a.exec" not in floor


def _grammar_snapshot(agent_name="claude", *, with_grammar=True):
    """An identity snapshot that ALSO carries the B5 grammar floor, folded in
    exactly as ``_launch_snapshot_inputs`` does (identity dict + grammar dict →
    one ``meta_identity``)."""
    from kanibako.plugins.claude.target import ClaudeTarget

    ident = meta_identity_floor(
        box_name="droste", project_path="/code/droste", inbox="/i",
        share_global="/s", share_workset=None, agent_name=agent_name,
        agent_real_name=agent_name,
    )
    if with_grammar:
        ident.update(
            meta_agent_grammar_floor(agent_name, ClaudeTarget().descriptor)
        )
    return build_launch_snapshot(
        agent_name=agent_name,
        ctx=_ctx_with_config(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        meta_identity=ident,
    )


class TestMetaAgentGrammarSnapshot:
    """The three B5 leaves land in the EXPANDED snapshot and the launch reader
    returns them typed — the keyspace is the argv-fragment source of truth."""

    def test_mode_and_exec_leaves_materialized(self):
        snap = _grammar_snapshot()
        ma = _meta_node(snap, "meta", "agent", "claude")
        mode = dict.get(ma, "mode")
        assert isinstance(mode, KeyStore)
        assert dict.get(mode, "start") == []
        assert dict.get(mode, "continue") == ["--continue"]
        assert dict.get(ma, "exec") == ["-p"]

    def test_settings_leaf_resolves_through_the_path_anchor(self):
        """``meta.agent.<a>.settings`` = @meta.agent.<a>.path/settings.yaml —
        the @-ref chain resolves through the sibling ``path`` anchor to the SAME
        file ``agent_settings_path`` composes (agents/<a>/settings.yaml)."""
        from kanibako.settings.agent_config import agent_settings_path

        snap = _grammar_snapshot()
        ma = _meta_node(snap, "meta", "agent", "claude")
        assert dict.get(ma, "settings") == "/data/agents/claude/settings.yaml"
        assert dict.get(ma, "settings") == str(
            agent_settings_path(Path("/data/agents"), "claude")
        )

    def test_reader_returns_the_grammar(self):
        snap = _grammar_snapshot()
        g = meta_agent_grammar(snap, active_agent="claude")
        assert g.mode == {"start": [], "continue": ["--continue"]}
        assert g.exec_fragment == ["-p"]

    def test_reader_refuses_an_unmaterialized_snapshot(self):
        """NO descriptor fallback (single source): a snapshot without the
        grammar is a build bug and the reader raises, naming the key —
        falling back to the descriptor would silently regrow the second path."""
        snap = _grammar_snapshot(with_grammar=False)
        with pytest.raises(_SettingsError, match="meta.agent.claude.mode"):
            meta_agent_grammar(snap, active_agent="claude")

    def test_reader_exec_none_when_absent(self):
        from kanibako.targets.base import PluginDescriptor

        ident = meta_identity_floor(
            box_name="x", project_path="/p", inbox="/i", share_global="/s",
            share_workset=None, agent_name="a", agent_real_name="a",
        )
        ident.update(meta_agent_grammar_floor(
            "a", PluginDescriptor(command=("a",), bindings=(), mode={"start": ()}),
        ))
        snap = build_launch_snapshot(
            agent_name="a", ctx=_ctx_with_config(),
            system_path=None, agent_path=None, workset_path=None, box_path=None,
            meta_identity=ident,
        )
        g = meta_agent_grammar(snap, active_agent="a")
        assert g.mode == {"start": []}
        assert g.exec_fragment is None

    def test_view_exposes_the_trio(self):
        """MetaAgentView displays settings/mode/exec beside name/path — the
        'settings unmaterialized' deferral note is dead (B5, step 3)."""
        from kanibako.settings import settings_views as views

        snap = _grammar_snapshot()
        ma = views.MetaAgentView(_meta_node(snap, "meta", "agent", "claude"))
        assert ma.settings == Path("/data/agents/claude/settings.yaml")
        assert ma.mode == {"start": [], "continue": ["--continue"]}
        assert ma.exec == ["-p"]

    def test_view_docstring_no_longer_defers_settings(self):
        """The ~:578-579 'still unmaterialized' note died honestly."""
        from kanibako.settings import settings_views as views

        assert "unmaterialized" not in (views.MetaAgentView.__doc__ or "")


def test_meta_identity_no_agent_omits_agent_key():
    """A NO-AGENT box (agent_name=None) materializes NO meta.agent.* key."""
    floor = meta_identity_floor(
        box_name="x", project_path="/p", inbox="/i", share_global="/s",
        share_workset=None, agent_name=None,
    )
    assert not any(k.startswith("meta.agent.") for k in floor)
    # And B2 no longer emits meta.workset.name at all.
    assert "meta.workset.name" not in floor


def test_meta_identity_standalone_share_workset_none_terminal():
    """STANDALONE: share_workset is a whole-value None terminal — PRESENT with
    value None (spec §2c), not dropped.  It is now the ONLY standalone None
    terminal in this floor: a lone box genuinely has no workset-LOCAL channel dir,
    whereas it DOES have a box settings tier (cf. meta.box.settings below)."""
    snap = _identity_snapshot(
        share_workset=None,
    )
    mb = _meta_node(snap, "meta", "box")
    assert dict.__contains__(mb, "share_workset")
    assert dict.get(mb, "share_workset") is None


def test_meta_box_settings_anchor_primary_named_and_standalone():
    """meta.box.settings is the RO box-TIER file anchor, materialized VERBATIM from
    the box-tier path the cascade uses — and it is a real path in EVERY mode now
    (spec §2c ALL PROJECTS), standalone's being <root>/box_data/settings.yaml.
    Mutation-guard: dropping the floor dict entry → the path asserts → RED."""
    # primary/named: the box's own settings.yaml path is materialized verbatim.
    floor_pn = meta_identity_floor(
        box_name="droste", project_path="/code/droste", inbox="/i",
        share_global="/s", share_workset=None,
        box_settings="/data/pw/boxes/droste/settings.yaml",
    )
    assert floor_pn["meta.box.settings"] == "/data/pw/boxes/droste/settings.yaml"
    # STANDALONE: NOT a None terminal — the box tier is a real path under box_data/.
    floor_std = meta_identity_floor(
        box_name="x", project_path="/p", inbox="/i", share_global="/s",
        share_workset=None,
        box_settings="/scratch/myproj/box_data/settings.yaml",
    )
    assert floor_std["meta.box.settings"] == "/scratch/myproj/box_data/settings.yaml"
    # And it survives into the resolved snapshot as a real meta.box leaf.
    snap_std = _identity_snapshot(
        share_workset=None, box_settings="/scratch/myproj/box_data/settings.yaml",
    )
    mb = _meta_node(snap_std, "meta", "box")
    assert dict.get(mb, "settings") == "/scratch/myproj/box_data/settings.yaml"


def test_standalone_box_tier_is_the_LAST_cascade_level(tmp_path):
    """The standalone box tier is the BOX level (L4.2) — it BEATS the workset tier
    (L3.2), exactly as in primary/named.  It is not a new level and needs no new
    ordering code: it is passed in ``build_launch_snapshot``'s ``box_path`` slot.

    ⚑ The two files are written at LITERAL spec positions and only the SNAPSHOT
    ARGUMENTS come from ``_box_settings_files``.  Sourcing both from the function
    under test would make the test self-consistent and therefore BLIND to a swapped
    pair — confirmed by mutation: an earlier version of this test stayed GREEN when
    the pair was reversed.

    (Mutations: swapping the pair returned by ``_box_settings_files`` → the ROOT
    value wins → RED; reverting the standalone arm to a ``None`` box tier → the box
    value is never read → RED.)"""
    from kanibako.settings.config_io import dump_doc
    from kanibako.settings.paths import _STANDALONE_META_DIR, BoxMode, _box_settings_files

    root = tmp_path / "myproj"
    (root / _STANDALONE_META_DIR).mkdir(parents=True)
    # LITERAL positions (spec §5), independent of the code under test.
    literal_ws = root / "settings.yaml"
    literal_box = root / _STANDALONE_META_DIR / "settings.yaml"
    dump_doc(literal_ws, {"box": {"image": "root/img:1"}})
    dump_doc(literal_box, {"box": {"image": "box/img:2"}})

    def _image():
        box_tier, ws_tier = _box_settings_files(BoxMode.standalone, root, None)
        snap = build_launch_snapshot(
            agent_name="claude", ctx=_ctx(), system_path=None, agent_path=None,
            workset_path=ws_tier, box_path=box_tier,
        )
        return dict.get(_meta_node(snap, "box"), "image")

    assert _image() == "box/img:2"
    # Remove the box file: the tier is EMPTY, and the workset value resolves as the
    # R2 downward-default — the "absent by default is byte-identical" claim.
    literal_box.unlink()
    assert _image() == "root/img:1"


def test_meta_box_settings_anchor_tolerates_a_narrow_resolve():
    """The ``box_settings`` PARAMETER stays optional for a narrow/partial resolve that
    materializes no box tier — the key is then a PRESENT-key None terminal, never a
    dropped key.  ⚑ This is NOT the standalone case any more (see above); it is the
    no-box-tier-supplied case."""
    floor = meta_identity_floor(
        box_name="x", project_path="/p", inbox="/i", share_global="/s",
        share_workset=None,
    )
    assert "meta.box.settings" in floor
    assert floor["meta.box.settings"] is None


def test_workspace_bind_routes_through_meta_box_workspace():
    """box.bindings.rw.workspace = (@meta.box.workspace, ~/workspace) expands to the
    SAME host_src as the proj-attr literal (byte-identical, JC-B2-4)."""
    snap = _identity_snapshot(
        project_path="/code/droste",
        default_categories={
            "box.bindings.rw": {"~/workspace": ("@meta.box.workspace", "Z,U")},
        },
    )
    # The DEST is now the map KEY, canonicalized by R-11 (``~/workspace`` →
    # ``/home/agent/workspace``); the entry carries (src, opts) only.
    bind = _meta_node(snap, "box", "bindings", "rw", "/home/agent/workspace")
    assert isinstance(bind, BindEntry)
    # The @meta.box.workspace ref resolved to str(proj.project_path) — byte-identical
    # to the old `source: project_path` injection.
    assert bind.src == "/code/droste"
    assert bind.opts == "Z,U"


def test_inbox_bind_routes_through_meta_box_inbox():
    """box.bindings.rw.inbox = (@meta.box.inbox, ~/channels/inbox) expands to the
    SAME host_src as the channels.box_channel_addresses literal (JC-B2-4)."""
    snap = _identity_snapshot(
        inbox="/data/channels/mailboxes/__PRIMARY__/droste",
        default_categories={
            "box.bindings.rw": {"~/channels/inbox": ("@meta.box.inbox",)},
        },
    )
    bind = _meta_node(snap, "box", "bindings", "rw", "/home/agent/channels/inbox")
    assert isinstance(bind, BindEntry)
    assert bind.src == "/data/channels/mailboxes/__PRIMARY__/droste"


def test_meta_box_view_reads_b2_fields_typed():
    """MetaBoxView reads the B2 leaves at their EXACT types; MetaAgentView reads
    the agent name."""
    import kanibako.settings.settings_views as views

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
    # meta.workset.name is now a meta_runtime_floor anchor (B1) — not part of this
    # identity-only snapshot; MetaWorksetView.name coverage lives in the B1 block.


def test_meta_box_view_standalone_share_workset_none():
    """MetaBoxView.share_workset is None (typed Path|None) for standalone."""
    import kanibako.settings.settings_views as views

    snap = _identity_snapshot(share_workset=None)
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
            "box.bindings.rw": {
                "~/workspace": ("@meta.box.workspace", "Z,U"),
                "~/channels/inbox": ("@meta.box.inbox",),
            },
        },
    )
    # OLD: literal proj-attr source (pre-B2 form).
    literal = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories={
            "box.bindings.rw": {
                "~/workspace": (project_path, "Z,U"),
                "~/channels/inbox": (inbox,),
            },
        },
    )
    # Keyed by the CANONICALIZED destination (R-11) on both sides.
    for key in ("/home/agent/workspace", "/home/agent/channels/inbox"):
        rb = _meta_node(routed, "box", "bindings", "rw", key)
        lb = _meta_node(literal, "box", "bindings", "rw", key)
        assert (rb.src, rb.opts) == (lb.src, lb.opts), key


# --------------------------------------------------------------------------- #
# box.agent.* mirror (block B5 — spec §2b, §0 directional)               #
# --------------------------------------------------------------------------- #
import yaml  # noqa: E402


def _yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_meta_box_agent_mirror_defaults_to_resolved_active_agent():
    """(a) ``meta.box.agent.<key>`` READS BACK the resolved active-agent subtree.

    ⮕ P7: the mirror moved from the SETTABLE ``box.agent.*`` to the RO
    ``meta.box.agent.*`` (spec §2b). Values are still readable; they are no
    longer settable.
    """
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus", "allow_helpers": "true"},
        default_categories={
            "agent.claude.common.plugins": ("/store/plugins", "~/.claude/plugins"),
        },
    )
    assert snap.meta.box.agent.model == snap.agent.default.model == "opus"
    assert snap.meta.box.agent.allow_helpers == "true"
    # The whole subtree mirrors — including category subtrees (a Bind leaf).
    mirrored = snap.meta.box.agent.common.plugins
    assert isinstance(mirrored, Bind)
    assert mirrored == snap.agent.claude.common.plugins
    # …and the RETIRED settable location is NOT materialized.
    assert "box" not in snap or "agent" not in snap.box


def test_a_box_file_box_agent_table_is_inert(tmp_path: Path):
    """⮕ P7 FLIP: the settable ``box.agent.*`` mirror is RETIRED (spec §2b).

    A box file that still carries the old table contributes NOTHING — not to the
    agent tier, not to the RO read-back, not to effective behavior. INVERT: restore
    the pre-merge fold / the ``box.agent`` overlay in ``effective_behavior`` and
    this reddens.
    """
    box = _yaml(tmp_path / "box.yaml", {"box": {"agent": {"model": "sonnet"}}})
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        behavior_floor={"model": "opus", "allow_helpers": "true"},
    )
    assert snap.meta.box.agent.model == "opus"        # the read-back, not the file.
    assert snap.agent.default.model == "opus"         # the agent tier is untouched.
    assert effective_behavior(snap, active_agent="claude")["model"] == "opus"


def test_meta_box_agent_mirror_keeps_the_auth_capability_floor_key():
    """The auth floor materializes ``meta.box.agent.auth.share_support`` BEFORE the
    mirror copies; the copy must not clobber it (it gap-fills, per name)."""
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus"},
        auth_chain=auth_chain_floor(mode="primary", agent_name="claude"),
        meta_identity=_mid_floor(
            box_name="b", project_path="/p", inbox="/i", share_global="/sg",
            share_workset=None, agent_name="claude", agent_real_name="claude",
            agent_auth_share_support=True,
        ),
        cli_level={"system.agent": "claude"},
    )
    assert snap.meta.box.agent.auth.share_support is True
    assert snap.meta.box.agent.model == "opus"


def test_meta_box_agent_mirror_copy_is_not_an_alias():
    # (c) the materialized meta.box.agent subtree is a FRESH deep copy — mutating a
    # nested node never reaches the shared agent subtree (no alias).
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories={
            "agent.claude.common.plugins": ("/store/plugins", "~/.claude/plugins"),
        },
    )
    assert snap.meta.box.agent.common is not snap.agent.claude.common
    snap.meta.box.agent.common["plugins"] = Bind("/tweaked", "~/.claude/plugins")
    assert snap.agent.claude.common.plugins.host == "/store/plugins"


def test_meta_box_agent_mirror_repoints_on_agent_change():
    # Re-materialized when the SELECTED agent changes: agent_name IS the resolved
    # active agent (``@system.agent`` — stored key, pref, --agent or autopick), so a
    # different agent_name mirrors a different subtree.
    common = dict(
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus"},
    )
    snap_claude = build_launch_snapshot(
        agent_name="claude",
        default_categories={
            "agent.claude.common.plugins": ("/claude/plugins", "~/.claude/plugins"),
        },
        **common,
    )
    snap_goose = build_launch_snapshot(
        agent_name="goose",
        default_categories={
            "agent.goose.common.plugins": ("/goose/plugins", "~/.goose/plugins"),
        },
        **common,
    )
    assert snap_claude.meta.box.agent.common.plugins.host == "/claude/plugins"
    assert snap_goose.meta.box.agent.common.plugins.host == "/goose/plugins"
    assert (
        snap_claude.meta.box.agent.common.plugins
        == snap_claude.agent.claude.common.plugins
    )
    assert (
        snap_goose.meta.box.agent.common.plugins
        == snap_goose.agent.goose.common.plugins
    )


def test_a_blank_active_agent_has_no_meta_box_agent_mirror():
    # A BLANK active agent → no subtree to mirror → meta.box.agent.* absent.
    snap = build_launch_snapshot(
        agent_name="",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus"},
    )
    meta = snap.meta if "meta" in snap else KeyStore()
    box = meta.box if "box" in meta else KeyStore()
    assert "agent" not in box


def test_the_no_agent_LAUNCH_shape_mirrors_the_default_backstop():
    """⚑ THE MEASURED LAUNCH SHAPE, not the docstring's.

    A no-agent/shell launch passes ``agent_name="general"`` (start.py:
    ``agent_id = with_harness(...) if target else "general"``), NOT a blank — so the
    blank short-circuit above does NOT fire and the mirror holds the
    ``agent.default`` backstop. This is the shape a reader of ``meta.box.agent`` on
    a real shell box will find; pinning it stops the module note from drifting back
    to the (false) "empty for a no-agent box" claim.

    The auth capability key is materialized by the FLOOR (pre-expand) and must
    survive the copy either way.
    """
    snap = build_launch_snapshot(
        agent_name="general",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus", "allow_helpers": "true"},
        auth_chain=auth_chain_floor(mode="primary", agent_name=""),
    )
    mirror = snap.meta.box.agent
    assert sorted(dict.keys(mirror)) == ["allow_helpers", "auth", "model"]
    assert mirror.model == "opus"          # the agent.default backstop
    # NOTHING consumes these leaves: the only runtime reader under
    # meta.box.agent is auth.share_support, which the FLOOR supplies.
    assert "share_support" in snap.meta.box.agent.auth


# --------------------------------------------------------------------------- #
# The BOX→AGENT tweak is now the §2h REQUEST pref.agent.<agent>.<key> (P7)     #
# --------------------------------------------------------------------------- #


def test_box_pref_category_merges_into_active_agent_slot(tmp_path: Path):
    # A box ``pref.agent.<a>.<category>`` tweak merges into the active agent slot as
    # an ORDINARY cascade level (§2h), so a box override of ONE deep leaf coexists
    # with the sibling default (per-name merge) and shows in the RO read-back.
    box = _yaml(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"common": {
            "plugins": ["/box/plugins", "~/.claude/plugins"],
        }}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        default_categories={
            "agent.claude.common.plugins": ("/store/plugins", "~/.claude/plugins"),
            "agent.claude.common.cache": ("/store/cache", "~/.claude/cache"),
        },
    )
    assert snap.meta.box.agent.common.plugins.host == "/box/plugins"
    assert snap.meta.box.agent.common.cache.host == "/store/cache"
    assert snap.agent.claude.common.plugins.host == "/box/plugins"
    assert snap.agent.claude.common.cache.host == "/store/cache"


def test_box_pref_category_present_none_suppresses_through_adapter(tmp_path: Path):
    # A box pref CATEGORY present-None (``null``) SUPPRESSES the inherited default
    # bind AT MERGE (the §3 type-split), so it never reaches the category adapter.
    # ⚑ §2h: present-None installs VERBATIM — ``if value is None: continue`` in the
    # pref loop would silently delete this capability.
    box = _yaml(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"seeded": {"x": None}}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        default_categories={"agent.claude.seeded.x": ("/store/x", "~/x")},
    )
    names = {
        e.name
        for e in snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
        if e.category == "seeded"
    }
    assert "x" not in names
    agent_node = snap.agent.claude if "claude" in snap.agent else KeyStore()
    seeded = agent_node.seeded if "seeded" in agent_node else KeyStore()
    assert "x" not in seeded


def test_box_pref_category_positive_tweak_delivers_through_adapter(tmp_path: Path):
    # A POSITIVE box pref category entry DELIVERS as an agent-scope entry.
    box = _yaml(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"seeded": {"bx": ["/box/src", "~/bx"]}}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        default_categories={"agent.claude.seeded.x": ("/store/x", "~/x")},
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    seeded = {e.name: e for e in entries if e.category == "seeded"}
    assert seeded["bx"].scope == "agent"
    assert seeded["bx"].host_src == "/box/src"
    assert seeded["x"].host_src == "/store/x"


def test_workset_pref_delivers_and_suppresses(tmp_path: Path):
    # A WORKSET file's pref applies to its boxes (§2h: prefs are legal in the
    # workset and box files only), delivering a positive entry and suppressing with
    # a present-None.
    ws_pos = _yaml(
        tmp_path / "ws_pos.yaml",
        {"pref": {"agent": {"claude": {"seeded": {"wy": ["/ws/src", "~/wy"]}}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=ws_pos, box_path=None,
        default_categories={"agent.claude.seeded.x": ("/store/x", "~/x")},
    )
    seeded = {
        e.name: e.host_src
        for e in snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
        if e.category == "seeded"
    }
    assert seeded == {"wy": "/ws/src", "x": "/store/x"}

    ws_null = _yaml(
        tmp_path / "ws_null.yaml",
        {"pref": {"agent": {"claude": {"seeded": {"x": None}}}}},
    )
    snap2 = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=ws_null, box_path=None,
        default_categories={"agent.claude.seeded.x": ("/store/x", "~/x")},
    )
    names = {
        e.name
        for e in snapshot_category_entries(snap2, active_agent="claude", box_ctx=_ctx())
        if e.category == "seeded"
    }
    assert "x" not in names


def test_box_pref_beats_workset_pref_for_a_category(tmp_path: Path):
    # §1A: box beats workset by ASSIGNMENT ORDER, for a CATEGORY as well
    # as a scalar. ⮕ P7 FLIP: while the retired ``box.agent.<category>`` fold
    # existed it out-ranked a box pref (P6's transitional pin); the fold is gone.
    ws = _yaml(
        tmp_path / "ws.yaml",
        {"pref": {"agent": {"claude": {"seeded": {"k": ["/ws/k", "~/k"]}}}}},
    )
    box = _yaml(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"seeded": {"k": ["/box/k", "~/k"]}}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=ws, box_path=box,
        default_categories={},
    )
    seeded = {
        e.name: e.host_src
        for e in snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
        if e.category == "seeded"
    }
    assert seeded == {"k": "/box/k"}


# --------------------------------------------------------------------------- #
# EFFECT-LEVEL (the box's tweak must change RESOLUTION output)                 #
# --------------------------------------------------------------------------- #


def test_box_pref_changes_effective_behavior(tmp_path: Path):
    # A box ``pref.agent.claude.model`` must change effective_behavior OUTPUT.
    box = _yaml(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"model": "sonnet"}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        behavior_floor={"model": "opus", "allow_helpers": "true"},
    )
    eff = effective_behavior(snap, active_agent="claude")
    assert eff["model"] == "sonnet"
    assert eff["allow_helpers"] == "true"


def test_box_agent_no_override_effective_behavior_identical_to_baseline():
    # EQUIVALENCE GUARD: with NO box tweak the effective behavior is byte-identical
    # to the agent.default ⊕ agent.<active> pick.
    common = dict(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        behavior_floor={"model": "opus", "allow_helpers": "true"},
    )
    snap = build_launch_snapshot(**common)
    eff = effective_behavior(snap, active_agent="claude")
    assert eff == {"model": "opus", "allow_helpers": "true"}


def test_box_pref_bindings_override_changes_category_entries(tmp_path: Path):
    # A box pref on a category must produce a category entry under the box's
    # effective agent (the request feeds category resolution).
    box = _yaml(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"common": {
            "plugins": ["/box/plugins", "~/.claude/plugins"],
        }}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        default_categories={
            "agent.claude.common.plugins": ("/store/plugins", "~/.claude/plugins"),
        },
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    plug = [e for e in entries if e.category == "common" and e.name == "plugins"]
    assert len(plug) == 1, entries
    assert plug[0].host_src == "/box/plugins"
    assert plug[0].scope == "agent"


def test_box_pref_env_override_changes_category_entries(tmp_path: Path):
    # A box pref on env.* appears as an agent-scope env entry.
    box = _yaml(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"env": {"MY_VAR": "box_val"}}}}},
    )
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=box,
        default_categories={"agent.claude.env.MY_VAR": "agent_val"},
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    envs = [e for e in entries if e.category == "env" and e.name == "MY_VAR"]
    assert len(envs) == 1, entries
    assert envs[0].options == "box_val"


def test_box_agent_no_override_category_entries_identical_to_baseline():
    # EQUIVALENCE GUARD (category side): NO box tweak → the category entry set is
    # byte-identical to the baseline.
    common = dict(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories={
            "agent.claude.common.plugins": ("/store/plugins", "~/.claude/plugins"),
            "agent.claude.env.MY_VAR": "agent_val",
        },
    )
    snap = build_launch_snapshot(**common)
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    agent_entries = sorted(
        (e.category, e.name, e.host_src, e.options)
        for e in entries if e.scope == "agent"
    )
    assert agent_entries == [
        ("common", "plugins", "/store/plugins", "Z,U"),
        ("env", "MY_VAR", None, "agent_val"),
    ]


# --------------------------------------------------------------------------- #
# P1: workset_anchor_floor — the layout anchors + the RO BOX ROOT              #
# (spec §2c per-mode; §2a "Declaration roots")                  #
# --------------------------------------------------------------------------- #


def test_workset_anchor_floor_rejects_unknown_mode():
    """An undeclared mode is REFUSED, never silently given the primary/named arm.

    The floor picks a per-mode arm for ``workset.boxes`` / ``workset.logs`` /
    ``meta.box.path``; a typo'd or new mode taking the wrong arm would relocate the
    box root SILENTLY, so the variant is checked rather than defaulted.
    """
    from kanibako.settings.settings_launch import workset_anchor_floor

    with pytest.raises(_SettingsError) as exc:
        workset_anchor_floor(mode="local")
    assert "local" in str(exc.value)
    # The three real modes are accepted.
    for mode in ("primary", "named", "standalone"):
        assert workset_anchor_floor(mode=mode)


def test_workset_anchor_floor_refuses_an_undeclared_channel_leaf():
    """The floor MANUFACTURES ``workset.channels.<leaf>`` from a caller mapping.

    Without a check that is a free-form passthrough: whatever leaf the caller
    invents becomes a key, which is exactly what spec §0's CLOSED keyspace
    forbids — an undeclared key is not a key and must be REFUSED, not quietly
    accepted. The error names the leaf so the fabrication is visible.
    """
    from kanibako.settings.settings_launch import workset_anchor_floor

    with pytest.raises(_SettingsError) as exc:
        workset_anchor_floor(
            mode="primary", workset_channels={"scratchpad": "/ws/scratchpad"},
        )
    assert "scratchpad" in str(exc.value)
    assert "not a declared key" in str(exc.value)

    # The leaves the live caller passes all survive, spelled as spec §2c keys.
    floor = workset_anchor_floor(
        mode="primary",
        workset_channels={
            "common": "/ws/channels/common",
            "chat": "/ws/channels/chat",
            "share": "/ws/channels/share",
        },
    )
    assert floor["workset.channels.common"] == "/ws/channels/common"
    assert floor["workset.channels.chat"] == "/ws/channels/chat"
    assert floor["workset.channels.share"] == "/ws/channels/share"


def test_workset_anchor_floor_allows_every_spec_declared_channel_leaf():
    """The allowlist is the SPEC's family, not the subset today's caller passes.

    Pinning it to the live call would refuse a declared key the moment a second
    caller supplied one; the check exists to stop FABRICATION.
    """
    from kanibako.settings.settings_launch import workset_anchor_floor

    floor = workset_anchor_floor(
        mode="named",
        workset_channels={
            leaf: f"/ws/{leaf}"
            for leaf in (
                "common", "chat", "broadcast", "share", "mailboxes",
                "share_global",
            )
        },
    )
    assert floor["workset.channels.broadcast"] == "/ws/broadcast"
    assert floor["workset.channels.mailboxes"] == "/ws/mailboxes"
    assert floor["workset.channels.share_global"] == "/ws/share_global"


def test_workset_anchor_floor_meta_box_path_per_mode():
    """``meta.box.path`` carries the per-mode box-root formula — and ONLY here.

    primary/named append the box-name leaf; standalone is the EMPTY LEAF (a BARE
    whole-value ref: ``workset.boxes`` IS the box root), so no separator is emitted
    and nothing downstream needs a per-mode arm.
    """
    from kanibako.settings.settings_launch import workset_anchor_floor

    for mode in ("primary", "named"):
        floor = workset_anchor_floor(mode=mode)
        assert floor["meta.box.path"] == "@workset.boxes/@meta.box.name"
        assert floor["workset.boxes"] == "@meta.workset.path/boxes"
        assert floor["workset.logs"] == "@meta.workset.path/logs"

    floor = workset_anchor_floor(mode="standalone")
    assert floor["meta.box.path"] == "@workset.boxes"
    assert not floor["meta.box.path"].endswith("/")
    assert floor["workset.boxes"] == "@meta.workset.path/box_data"
    assert floor["workset.logs"] == "@meta.box.path"

    # The vault roots are UNIFORM in every mode (spec §2c ALL PROJECTS) — only the
    # BOX BIND differs (the per-box subdir a lone box does not need).
    for mode in ("primary", "named", "standalone"):
        floor = workset_anchor_floor(mode=mode)
        assert floor["workset.vault_ro"] == "@meta.workset.path/vault/ro"
        assert floor["workset.vault_rw"] == "@meta.workset.path/vault/rw"


def test_box_root_that_does_not_resolve_is_a_named_error(tmp_path: Path):
    """A box root that resolves to nothing RAISES, naming the key.

    ⚑ WHY THIS EXISTS. ``box.bindings.rw.home`` is ``@meta.box.path/home`` — an
    EMBEDDED ref, and the embedded rule coerces an absent / present-None referent
    to ``""``. So a box root that fails to resolve does not error: it yields the
    host_src ``/home``, which the L7 guarantee-create then mkdir's and mounts OVER
    the box home. Silent, catastrophic, and user-reachable — a workset settings
    file may set ``workset.boxes: null``, which the cascade honours as a
    present-None terminal.

    MUTATION-PROOF: without the assertion in ``build_launch_snapshot`` this test
    observes a successfully-built snapshot whose home host_src is ``/home`` (that
    was confirmed RED before the guard was added), so it cannot pass vacuously.
    """
    from kanibako.settings.config_io import dump_doc
    from kanibako.settings.settings_launch import workset_anchor_floor

    ws_file = tmp_path / "workset-settings.yaml"
    dump_doc(ws_file, {"workset": {"boxes": None}})
    # BOTH arms are covered because they fail DIFFERENTLY: primary/named dereference
    # the box root through an EMBEDDED ref (-> "/mybox"), standalone through a
    # WHOLE-VALUE ref (-> the host "/home"). Confirmed pre-guard.
    for mode in ("primary", "standalone"):
        floor: dict[str, object] = {
            "box.bindings.rw": {"~": ("@meta.box.path/home", "Z,U")},
            "meta.box.name": "mybox",
            "meta.workset.path": "/data/ws",
        }
        floor.update(workset_anchor_floor(mode=mode))
        with pytest.raises(_SettingsError) as exc:
            build_launch_snapshot(
                agent_name="claude", ctx=_ctx(),
                system_path=None, agent_path=None, workset_path=ws_file,
                box_path=None,
                default_categories=floor,
                workset_anchor=floor,
            )
        assert "meta.box.path" in str(exc.value), mode
        assert "workset.boxes" in str(exc.value), mode


def test_box_root_with_a_vanished_name_leaf_is_a_named_error():
    """An EMPTY ``meta.box.name`` must not silently yield the SHARED box store.

    primary/named spell the root ``@workset.boxes/@meta.box.name``, so an empty or
    None name leaves ``<…>/boxes/`` — and the home host_src ``<…>/boxes//home``,
    which is the box STORE's home rather than this box's. Every box in the workset
    would resolve the same home directory.

    Reachable today: ``paths._resolve_local_dir``'s unregistered-primary fallback
    returns an empty name and the launch passes ``proj.name`` through unexamined.
    The value is a perfectly good non-empty string, so only the trailing-separator
    shape distinguishes it (confirmed: pre-fix this produced
    ``/data/ws/boxes//home`` with no error).
    """
    from kanibako.settings.settings_launch import workset_anchor_floor

    for name in ("", None):
        floor: dict[str, object] = {
            "box.bindings.rw": {"~": ("@meta.box.path/home", "Z,U")},
            "meta.box.name": name,
            "meta.workset.path": "/data/ws",
        }
        floor.update(workset_anchor_floor(mode="primary"))
        with pytest.raises(_SettingsError) as exc:
            build_launch_snapshot(
                agent_name="claude", ctx=_ctx(),
                system_path=None, agent_path=None, workset_path=None, box_path=None,
                default_categories=floor,
                workset_anchor=floor,
            )
        assert "meta.box.path" in str(exc.value), name
        assert "trailing separator" in str(exc.value), name


def test_box_root_assertion_is_skipped_for_a_partial_floor():
    """A caller that does not supply the box root is NOT forced to.

    Narrow resolves and focused tests fold only part of the floor; the check keys
    on the anchor being SUPPLIED, so those callers are unaffected.
    """
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        workset_anchor={"workset.boxes": "/floor/boxes"},
    )
    assert snap.workset.boxes == "/floor/boxes"


def test_hostile_box_file_cannot_forge_the_box_root(tmp_path: Path) -> None:
    """A settings file's top-level ``meta:`` cannot repoint the RO box root.

    ``meta.box.path`` is RO by contract (§0 meta ⟺ not-settable). Relocating box
    data is done one level up, through the SETTABLE ``workset.boxes``. This pins
    the file half of that contract for the box root specifically: the anchor is a
    mount SOURCE, so a file that could forge it could redirect the box home to any
    host directory. (The CLI half — ``config set`` refusing every ``meta.*`` key —
    is pinned in ``tests/test_settings/test_config_interface.py``.)
    """
    from kanibako.settings.config_io import dump_doc
    from kanibako.settings.settings_launch import workset_anchor_floor

    hostile = tmp_path / "hostile.yaml"
    dump_doc(
        hostile,
        {
            "box": {"image": "img"},  # a legitimate same-scope key — must survive
            "meta": {"box": {"path": "/evil"}},  # forge the RO box root
        },
    )
    floor: dict[str, object] = {"meta.workset.path": "/data/ws", "meta.box.name": "b"}
    floor.update(workset_anchor_floor(mode="primary"))
    snap = build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=hostile,
        default_categories=floor,
        workset_anchor=floor,
    )
    # The floor's derivation stands; the forged value is gone.
    assert snap.meta.box.path == "/data/ws/boxes/b"
    assert snap.box.image == "img"  # the legitimate key survived the meta drop


# --------------------------------------------------------------------------- #
# CATEGORY-ROOT refusal — a value where a namespace belongs (spec §2d) #
# --------------------------------------------------------------------------- #


class TestCategoryRootRefusal:
    """T8 — a VALUE at a category root is an UNDECLARED shape and ERRORS.

    ⚑ Before P3 every one of these was SILENTLY DROPPED by an
    ``isinstance(x, KeyStore)`` guard: the user's binding simply never appeared,
    with nothing said anywhere.  Silent acceptance of an undeclared shape is
    exactly what the closed-keyspace rule (spec §0) forbids — an undeclared key is
    an ERROR that names itself.

    The refusal lives in ``_emit_scope_node`` because that is the ONE site that
    sees the MERGED snapshot, so it catches such a value from ANY origin (a plugin
    defaults table, a workset/box YAML, a ``config set``) without duplication.
    """

    @staticmethod
    def _entries(node: dict):
        return snapshot_category_entries(
            KeyStore(node), active_agent="claude", box_ctx=_ctx(),
        )

    def test_scalar_at_bindings_root_errors(self):
        with pytest.raises(_SettingsError) as e:
            self._entries({"agent": {"default": {"bindings": "/some/path"}}})
        msg = str(e.value)
        assert "CATEGORY ROOT" in msg
        # It names the per-arm declaration form the user actually wants.
        assert "agent.default.bindings.{ro,rw}.<name>" in msg

    @pytest.mark.parametrize("tier", ["default", "claude"])
    def test_agent_message_names_the_DISCRIMINATED_tier(self, tier):
        """⚑ The message must name ``agent.<tier>.bindings``, NEVER the bare
        ``agent.bindings``.

        The bare form is not a key (spec §0: the agent tier is DISCRIMINATED
        everywhere). An error naming it would be telling the reader to go look at —
        or worse, write — a shape the keyspace forbids, which is exactly the
        confusion the closed-keyspace rule exists to prevent. It also has to say
        WHICH tier holds the bad value, since ``agent.default`` and
        ``agent.<active>`` are different files.

        This is why the refusal runs on the RAW tiers rather than on the merged
        agent node: after ``_agent_pick_node`` the tier of origin is gone and only
        the bare token remains.
        """
        with pytest.raises(_SettingsError) as e:
            self._entries({"agent": {tier: {"common": "/some/path"}}})
        msg = str(e.value)
        assert f"agent.{tier}.common" in msg
        # The forbidden bare form must not appear ANYWHERE in the message.
        assert "agent.common" not in msg

    def test_arm_less_agent_message_names_the_discriminated_tier(self):
        with pytest.raises(_SettingsError) as e:
            self._entries({"agent": {"claude": {"bindings": {
                "mydir": Bind("/h", "/b", None),
            }}}})
        msg = str(e.value)
        assert "agent.claude.bindings.mydir" in msg
        # The prescribed arms carry the DISCRIMINATOR — the point of this test.
        # (They are the bare arms since R-5: the arm is the whole key.)
        assert "agent.claude.bindings.ro" in msg
        assert "agent.bindings" not in msg

    def test_a_NON_ACTIVE_agent_tier_is_still_checked(self):
        """Every discriminated tier present in the snapshot is validated, not just
        the active one — a malformed ``agent.goose.*`` is undeclared whether or not
        goose is the agent being launched."""
        with pytest.raises(_SettingsError) as e:
            self._entries({"agent": {"goose": {"caches": "/x"}}})
        assert "agent.goose.caches" in str(e.value)

    def test_bind_at_a_leaf_category_root_errors(self):
        with pytest.raises(_SettingsError) as e:
            self._entries({"workset": {"common": Bind("/h", "/b", None)}})
        msg = str(e.value)
        assert "workset.common" in msg
        assert "workset.common.<name>" in msg

    def test_list_at_a_category_root_errors(self):
        with pytest.raises(_SettingsError):
            self._entries({"box": {"caches": ["/a", "/b"]}})

    def test_none_at_a_category_root_errors(self):
        """``null`` is a VALUE too — a whole-category present-None is not a
        declared reset shape (per-NAME present-None is, and is handled at merge)."""
        with pytest.raises(_SettingsError):
            self._entries({"box": {"seeded": None}})

    def test_arm_less_binding_errors(self):
        """A ``bindings`` child outside ``{ro, rw}`` is an arm-less bind.

        ⚑ The CURE the message prescribes changed with R-5/R-10: the arm IS the
        key now, so it points at ``box.bindings.ro`` / ``box.bindings.rw`` and
        says to re-key the entry by its destination — it must NOT prescribe the
        retired ``box.bindings.ro.mydir``, which is no longer a key at all.
        """
        with pytest.raises(_SettingsError) as e:
            self._entries({"box": {"bindings": {
                "mydir": Bind("/h", "/b", None),
            }}})
        msg = str(e.value)
        assert "box.bindings.mydir" in msg
        assert "box.bindings.ro" in msg and "box.bindings.rw" in msg
        assert "keyed by its destination" in msg
        assert "box.bindings.ro.mydir" not in msg

    def test_value_at_an_arm_root_errors(self):
        """A value AT an arm (``bindings.ro = "/x"``) is an undeclared shape: the
        arm's value must be a MAP node.

        ⚑ Under R-5 that is a map of DESTINATIONS rather than of names, but the
        assertion is unchanged — a scalar there was wrong before and is wrong now.

        ⚑ Slightly WIDER than the boundary the P3 plan spelled out (which named the
        category root and the arm-less child).  Left silent it would be the one
        remaining hole in the function whose entire purpose is to close silent
        drops; it is a separate branch so it can be removed on its own.
        """
        with pytest.raises(_SettingsError) as e:
            self._entries({"box": {"bindings": {"ro": "/x"}}})
        assert "box.bindings.ro" in str(e.value)

    def test_present_but_empty_is_not_an_error(self):
        """An empty node is byte-indistinguishable from an absent one after
        ``assemble``, so erroring would trap a no-op.  §2d itself calls the
        ``agent.default.bindings | {}`` row "documentation of intent"."""
        assert self._entries({"agent": {"default": {"bindings": KeyStore({})}}}) == []
        assert self._entries({"workset": {"common": KeyStore({})}}) == []
        assert self._entries(
            {"box": {"bindings": KeyStore({"ro": KeyStore({})})}}
        ) == []

    def test_a_valid_declaration_still_emits(self):
        """Control: the refusal does not eat well-formed declarations."""
        entries = self._entries({"box": {"bindings": {"rw": {
            "home": Bind("/h/home", "/box/home", None),
        }}}})
        assert [(e.scope, e.category, e.name) for e in entries] == [
            ("box", "bindings.rw", "home"),
        ]


# --------------------------------------------------------------------------- #
# STRUCTURAL: the implicit-root-prepend mechanism must not come back           #
# --------------------------------------------------------------------------- #


class TestNoImplicitRootPrepend:
    """T10 — the DELETION instrument.

    ⚑ THE BEHAVIOURAL GATE CANNOT SEE THIS CHANGE, and saying so out loud is the
    point. Once every source is rooted at DECLARATION, an assembly-time join
    no-ops on the (now absolute) inputs — so leaving the mechanism in place
    produces a byte-identical mount map. A gate that silently cannot see half the
    change is worse than no gate, so the deletion gets a STRUCTURAL instrument
    instead: the identifiers must appear NOWHERE in the shipped source, comments
    included (the prose truth pass is part of the change — a comment describing a
    mechanism that no longer exists is worse than no comment).

    Spec §2a does not merely prefer this; it names ``scope_roots`` and
    says it MUST BE DELETED. Re-adding an inert, defaulted parameter would already
    be the invitation to re-populate it — which is why this scan is not limited to
    live call sites.
    """

    _FORBIDDEN = ("scope_roots", "_root_join")

    @staticmethod
    def _shipped_sources():
        from tests.support.repo import REPO_ROOT as root

        files = list((root / "src" / "kanibako").rglob("*.py"))
        for pkg in sorted((root / "packages").glob("*")):
            src = pkg / "src"
            if src.is_dir():
                files.extend(src.rglob("*.py"))
        # ``build/`` holds stale wheel-build copies of the plugin trees, and
        # ``.claude/worktrees/`` may hold another agent's live worktree — neither
        # is shipped source and neither is ours to edit.
        return [
            f for f in files
            if "build" not in f.parts and ".claude" not in f.parts
        ]

    def test_scan_covers_a_representative_source_set(self):
        """Guard the guard: an empty or tiny file list would make the scan below
        pass vacuously."""
        files = self._shipped_sources()
        names = {f.name for f in files}
        assert len(files) > 50, len(files)
        assert {"settings_launch.py", "settings_categories.py", "start.py"} <= names
        # The plugin packages are in scope too (they declare category defaults).
        assert any("plugins" in f.parts for f in files), sorted(names)[:5]

    def test_scope_roots_mechanism_is_absent(self):
        hits = []
        for path in self._shipped_sources():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if any(token in line for token in self._FORBIDDEN):
                    hits.append(f"{path}:{lineno}: {line.strip()}")
        assert not hits, (
            "the implicit root-prepend mechanism reappeared in shipped source "
            "(spec §2a L474-486 requires its deletion):\n" + "\n".join(hits)
        )


# --------------------------------------------------------------------------- #
# ``pref.*`` at the LAUNCH seam (spec §2h) — the behavioural half of P6        #
# --------------------------------------------------------------------------- #

from kanibako.settings.settings_prefs import AgentNames as _AgentNames  # noqa: E402
from kanibako.settings.settings_resolve import SettingsError  # noqa: E402

_PREF_AGENTS = _AgentNames({"claude", "goose", "codex"})


def _write_yaml(path, doc):
    """Write *doc* as YAML, creating parents (the module-level ``_yaml`` helper
    above assumes the parent exists)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return _yaml(path, doc)


def _pref_snap(tmp_path, *, box=None, workset=None, floor=None, **kw):
    """A launch snapshot over hand-written box / workset files.

    *floor* injects declared defaults through ``default_categories`` — the floor
    folds UNDER ``base`` (``assemble_levels(floor=…)``), which is exactly the
    "inherited value a pref overrides or suppresses" position. ``build_launch_
    snapshot`` takes no ``base_path``, and widening a production signature for a
    test affordance would be the wrong trade; the base FILE path is exercised
    where it belongs, against ``assemble_levels`` in test_settings_assemble.py.
    """
    box_p = _write_yaml(tmp_path / "box.yaml", box) if box is not None else None
    ws_p = _write_yaml(tmp_path / "ws.yaml", workset) if workset is not None else None
    return build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None,
        agent_path=None,
        workset_path=ws_p,
        box_path=box_p,
        default_categories=floor,
        valid_agents=_PREF_AGENTS,
        **kw,
    )


class TestPrefRecomputeNotDelta:
    """spec §2h — 'RECOMPUTE, not delta.'"""

    def test_a_key_derived_from_a_prefd_value_updates(self, tmp_path):
        """⚑ THE discriminator for the delta implementation.

        ``box.bindings.rw.probe``'s host is ``@agent.claude.template/sub``. If
        prefs were installed by patching the EXPANDED snapshot (the delta), the
        target key would be right but this DERIVED value would still carry the
        old root — here, an empty substitution yielding ``/sub``.
        INVERT: install prefs after ``expand`` -> this reddens.
        """
        snap = _pref_snap(
            tmp_path,
            box={
                "pref": {"agent": {"claude": {"template": "/custom/tpl"}}},
                "box": {"bindings": {"rw": {
                    "~/probe": ["@agent.claude.template/sub"],
                }}},
            },
        )
        assert snap.agent.claude.template == "/custom/tpl"
        probe = getattr(snap.box.bindings.rw, "/home/agent/probe")
        assert probe.src == "/custom/tpl/sub"

    def test_a_key_derived_from_a_prefd_system_agent_updates(self, tmp_path):
        """The P7-critical key: ``@system.agent`` resolves to the REQUESTED
        value everywhere it is referenced, not just at its own key."""
        snap = _pref_snap(
            tmp_path,
            box={
                "pref": {"system": {"agent": "goose"}},
                "box": {"bindings": {"rw": {
                    "~/probe": ["/src/@system.agent"],
                }}},
            },
        )
        assert snap.system.agent == "goose"
        probe = getattr(snap.box.bindings.rw, "/home/agent/probe")
        assert probe.src == "/src/goose"

    def test_a_pref_need_not_be_a_literal(self, tmp_path):
        """§2h — the value is installed as an ordinary (possibly
        UNRESOLVED) value and resolution handles it like any other key."""
        snap = _pref_snap(
            tmp_path,
            box={"pref": {"agent": {"claude": {"template": "@workset.template/x"}}}},
            workset={"workset": {"template": "/ws/tpl"}},
        )
        assert snap.agent.claude.template == "/ws/tpl/x"


class TestPrefNullSuppression:
    """spec §2h — values install VERBATIM, including ``None``."""

    def test_a_null_pref_suppresses_an_inherited_agent_bind(self, tmp_path):
        """⚑ The named silent hazard. ``if value is None: continue`` in the
        install loop deletes a box's ONLY suppression channel with no error and
        no diff. INVERT: add that guard -> this reddens."""
        snap = _pref_snap(
            tmp_path,
            box={"pref": {"agent": {"claude": {"common": {"plugins": None}}}}},
            floor={"agent.claude.common.plugins": ("/host/plugins", "~/.claude/plugins")},
        )
        # The inherited bind is GONE (present-None on a category leaf -> OMIT).
        common = snap.agent.claude.common if "common" in snap.agent.claude else None
        assert common is None or "plugins" not in dict.keys(common)

    def test_the_request_itself_stays_visible_after_resolution(self, tmp_path):
        """§2h read verbs — prefs are KEPT IN MEMORY, so ``--effective`` can
        show the request beside the result."""
        snap = _pref_snap(
            tmp_path,
            box={"pref": {"agent": {"claude": {"common": {"plugins": None}}}}},
        )
        node = snap["pref"]["agent"]["claude"]["common"]
        assert dict.__getitem__(node, "plugins") is None

    def test_a_null_pref_on_a_SCALAR_leaf_is_kept_as_none(self, tmp_path):
        """§2b — ``pref.system.agent: null`` is how the NO-AGENT
        plain-shell box is expressed, and it is a capability GAIN. Present-None
        on a SCALAR leaf is KEPT."""
        snap = _pref_snap(
            tmp_path,
            box={"pref": {"system": {"agent": None}}},
            floor={"system.agent": "claude"},
        )
        assert "agent" in dict.keys(snap.system)
        assert dict.__getitem__(snap.system, "agent") is None


class TestPrefLevelPrecedence:
    def test_box_pref_beats_workset_pref(self, tmp_path):
        """§1A — box beats workset by ASSIGNMENT ORDER.
        INVERT: swap the two overlays' splice positions -> this reddens."""
        snap = _pref_snap(
            tmp_path,
            box={"pref": {"system": {"agent": "goose"}}},
            workset={"pref": {"system": {"agent": "codex"}}},
        )
        assert snap.system.agent == "goose"

    def test_a_workset_pref_applies_when_the_box_is_silent(self, tmp_path):
        snap = _pref_snap(
            tmp_path,
            box={"box": {"image": "x"}},
            workset={"pref": {"system": {"agent": "codex"}}},
        )
        assert snap.system.agent == "codex"

    def test_a_pref_beats_the_base_floor_value(self, tmp_path):
        snap = _pref_snap(
            tmp_path,
            box={"pref": {"agent": {"claude": {"model": "opus"}}}},
            floor={"agent.claude.model": "sonnet"},
        )
        assert snap.agent.claude.model == "opus"

    def test_a_box_pref_wins_now_that_the_box_agent_fold_is_gone(self, tmp_path):
        """⮕ **THE P6 PIN, FLIPPED BY P7 — deliberately.**

        P6 recorded a TRANSITIONAL contest: ``_box_agent_category_fold`` spliced
        the retiring ``box.agent.<category>`` tweak ABOVE ``box``, while a pref
        sits BELOW its own level's partial (§2h expands prefs before the level
        resolves), so for a CATEGORY the legacy mirror won. P7 RETIRES settable
        ``box.agent.*`` (spec §2b) and deletes the fold, which removes the
        contender — the pref now wins a CATEGORY exactly as it already won a
        SCALAR (the test below, unchanged and still green, is the discriminator
        proving only the category half of the contest was ever real).

        INVERT: restore the fold and this reddens.
        """
        snap = _pref_snap(
            tmp_path,
            box={
                "pref": {"agent": {"claude": {"common": {
                    "plugins": ["/from/pref", "~/.claude/plugins"],
                }}}},
                # The RETIRED table, left here on purpose: it must be INERT.
                "box": {"agent": {"common": {
                    "plugins": ["/from/mirror", "~/.claude/plugins"],
                }}},
            },
        )
        assert snap.agent.claude.common.plugins.host == "/from/pref"
        # …and the retired table contributes nothing anywhere else either.
        assert snap.meta.box.agent.common.plugins.host == "/from/pref"

    def test_a_box_pref_wins_for_a_SCALAR_agent_key(self, tmp_path):
        """⚑ MEASURED, and it corrects the brief's prediction.

        The fold is CATEGORY-only (``_box_agent_category_fold``); a SCALAR
        ``box.agent.<key>`` is not spliced into ``agent.<active>`` at all — the
        scalar mirror is a post-expand READ-BACK
        (``_materialize_box_agent_mirror``). So there is no scalar contest and
        the pref, which really does target the agent tier, wins. The brief
        predicted the mirror would win here; it does not, and only the category
        half of the transitional contest is real.
        """
        snap = _pref_snap(
            tmp_path,
            box={
                "pref": {"agent": {"claude": {"model": "from-pref"}}},
                "box": {"agent": {"model": "from-mirror"}},
            },
        )
        assert snap.agent.claude.model == "from-pref"


class TestPrefRejectionAtLaunch:
    def test_a_bad_pref_fails_the_launch(self, tmp_path):
        """§2h — the launch FAILS rather than proceeding with a
        partially-applied request. INVERT: warn-and-continue -> reddens."""
        box_p = _write_yaml(
            tmp_path / "box.yaml",
            {"pref": {"agent": {"zippity": {"model": "x"}}}},
        )
        with pytest.raises(_SettingsError) as exc:
            build_launch_snapshot(
                agent_name="claude", ctx=_ctx(),
                system_path=None, agent_path=None,
                workset_path=None, box_path=box_p,
                valid_agents=_PREF_AGENTS,
            )
        msg = str(exc.value)
        assert "pref.agent.zippity.model" in msg
        assert "at the box level" in msg
        assert str(box_p) in msg

class TestPrefIsInertWhereItMustBe:
    def test_a_pref_subtree_yields_no_category_entries(self, tmp_path):
        """``snapshot_category_entries`` walks ``_SCOPES`` only, so ``pref`` is
        never read as a category. Asserted because a future scope-loop edit
        would break it silently."""
        snap = _pref_snap(
            tmp_path,
            box={"pref": {"agent": {"claude": {"common": {"x": ["/s", "~/d"]}}}}},
        )
        entries = snapshot_category_entries(
            snap, active_agent="claude", box_ctx=_ctx(),
        )
        assert all(not e.key.startswith("pref.") for e in entries)
        # ...but the INSTALLED target IS emitted.
        assert any(e.key == "agent.claude.common.x" for e in entries)


class TestPrefFreeByteIdentity:
    """GATE HALF A — a pref-free config must resolve EXACTLY as before.

    Compares the ordinary path against an explicitly pref-disabled build over
    the same inputs, across modes x agent shapes. The delivery goldens
    (test_delivery_manifest / test_defaults_golden) are the cross-check against
    the pre-P6 world; this is the in-suite proof that the splice is inert when
    there is nothing to splice.
    """

    @pytest.mark.parametrize("agent_name", ["claude", "general", "navigator℘claude"])
    def test_identical_when_no_pref_table_exists(self, tmp_path, agent_name):
        box = {"box": {"image": "img", "bindings": {"rw": {
            "home": ["/host/home", "~/"],
        }}}}
        ws = {"workset": {"boxes": "/ws/boxes"}}
        box_p = _write_yaml(tmp_path / "box.yaml", box)
        ws_p = _write_yaml(tmp_path / "ws.yaml", ws)

        def build(prefs):
            return build_launch_snapshot(
                agent_name=agent_name, ctx=_ctx(),
                system_path=None, agent_path=None,
                workset_path=ws_p, box_path=box_p,
                default_categories={"agent.default.model": "sonnet"},
                prefs=prefs, valid_agents=_PREF_AGENTS,
            )

        assert build(None) == build([])
        assert "pref" not in build(None)


# --------------------------------------------------------------------------- #
# P8 — the §1A CLI LEVEL: precedence + the guard that cannot be bypassed       #
# --------------------------------------------------------------------------- #


class TestCliLevelPrecedence:
    """spec §1A — *"its OWN LEVEL — the highest, above everything"*.

    The unit-level shape of the level lives in ``test_settings_cli_level.py``;
    these pin what it BEATS once spliced, which is the only thing that makes it a
    level rather than a dict.
    """

    def _agent_file_snap(self, tmp_path, *, stored, cli_level):
        """A snapshot over an AGENT-tier settings file.

        ⚑ Not a BOX file: §0 directional enforcement DROPS an ``agent.*`` table
        from a box file (a file contributes keys of its OWN scope), so the box
        tier cannot hold the contender. The agent FILE is where a stored
        ``agent.<active>.model`` legitimately lives — and a box that wants one
        writes ``pref.agent.<agent>.model``, covered separately below.
        """
        agent_file = _yaml(
            tmp_path / "agent.yaml", {"self": {"claude": {"model": stored}}},
        )
        return build_launch_snapshot(
            agent_name="claude",
            ctx=_ctx(),
            system_path=None, agent_path=agent_file,
            workset_path=None, box_path=None,
            cli_level=cli_level,
        )

    def test_the_cli_level_beats_an_agent_file(self, tmp_path):
        """INVERT: drop ``cli_level`` and the agent file's value stands."""
        snap = self._agent_file_snap(
            tmp_path, stored="from-file",
            cli_level={"agent.claude.model": "from-cli"},
        )
        assert snap.agent.claude.model == "from-cli"
        assert effective_behavior(snap, active_agent="claude")["model"] == "from-cli"

        without = self._agent_file_snap(
            tmp_path, stored="from-file", cli_level=None,
        )
        assert without.agent.claude.model == "from-file"

    def test_the_cli_level_beats_a_box_pref(self, tmp_path):
        """Above every settings file AND every pref (§1A) — the pref is the
        strongest thing a FILE can say about another scope's key."""
        snap = _pref_snap(
            tmp_path,
            box={"pref": {"agent": {"claude": {"model": "from-pref"}}}},
            cli_level={"agent.claude.model": "from-cli"},
        )
        assert snap.agent.claude.model == "from-cli"

    def test_the_cli_level_beats_a_pref_on_the_selection_key(self, tmp_path):
        """The P7 case, restated at the generalised seam: ``--agent`` over
        ``pref.system.agent`` (spec §2h precedence chain)."""
        snap = _pref_snap(
            tmp_path,
            box={"pref": {"system": {"agent": "goose"}}},
            cli_level={"system.agent": "codex"},
        )
        assert snap.system.agent == "codex"

    def test_the_active_slot_is_why_the_default_slot_is_not_used(self, tmp_path):
        """⚑ R1, proven rather than asserted.

        ``effective_behavior`` picks active-over-default AFTER the merge, so a CLI
        value at ``agent.default.model`` would LOSE to a file's
        ``agent.claude.model`` even from level index 0. That is why
        ``build_cli_level`` spells the ACTIVE slot; this is the failure it avoids.
        """
        snap = self._agent_file_snap(
            tmp_path, stored="from-file",
            cli_level={"agent.default.model": "from-cli"},
        )
        assert effective_behavior(snap, active_agent="claude")["model"] == "from-file"


class TestCliLevelGuardIsNotBypassable:
    """spec §1A — the guard lives INSIDE ``build_launch_snapshot``.

    A guard a caller can forget to run is not a guard, so it is asserted at the
    production seam and not only against ``guard_cli_level`` directly.
    """

    def test_a_locator_key_from_the_cli_is_refused(self, tmp_path):
        with pytest.raises(SettingsError) as exc:
            _pref_snap(tmp_path, cli_level={"workset.boxes": "/tmp/elsewhere"})
        assert "workset.boxes" in str(exc.value)

    def test_an_undeclared_key_from_the_cli_is_refused(self, tmp_path):
        with pytest.raises(SettingsError) as exc:
            _pref_snap(tmp_path, cli_level={"box.wibble": "x"})
        assert "box.wibble" in str(exc.value)

    def test_a_meta_key_from_the_cli_is_refused(self, tmp_path):
        with pytest.raises(SettingsError) as exc:
            _pref_snap(tmp_path, cli_level={"meta.box.path": "/tmp/x"})
        assert "meta.box.path" in str(exc.value)

    def test_the_wired_keys_pass_the_seam(self, tmp_path):
        snap = _pref_snap(
            tmp_path,
            cli_level={
                "system.agent": "claude",
                "agent.claude.model": "opus",
                "agent.claude.continue_mode": False,
            },
        )
        assert snap.agent.claude.continue_mode is False


# --------------------------------------------------------------------------- #
# The PERSONA rung — the persona store's LIVE, never-persisted tier            #
# --------------------------------------------------------------------------- #
#
# ``persona_values`` is threaded IN MEMORY, not written to any file: the persona
# store's rendered host config is a live resolution input, and ``build_launch_
# snapshot`` rebuilds from FILES several times per launch, so a never-written
# layer has no file to ride. Its rung is BELOW the per-agent FILE (both the flat
# ``[agent]`` state channel and the discriminated ``agent.<active>`` tables) and
# ABOVE ``agent.default`` — the agent file stores only NON-default values, so a
# file value that beats the persona can only be a deliberate user edit.

#: One persona key per VALUE CLASS, with the snapshot path it must land on under
#: ``agent.<active>``. The two bare keys are behavior scalars; the two dotted ones
#: are the open categories, whose ``<VAR>`` half is arbitrary user text.
_PERSONA_CLASSES = [
    ("endpoint", ("endpoint",)),
    ("model", ("model",)),
    ("secret_path.ANTHROPIC_AUTH_TOKEN", ("secret_path", "ANTHROPIC_AUTH_TOKEN")),
    ("env.PERSONA_FLAG", ("env", "PERSONA_FLAG")),
]

#: Marker for "no leaf at that path" — distinct from a stored ``None``.
_NO_LEAF = object()


def _leaf(snap, path, *, agent_name="claude"):
    """Read the leaf at ``agent.<agent_name>.<path>`` off *snap*, or ``_NO_LEAF``.

    Unbound ``dict`` ops (S3) throughout, and tolerant of a missing intermediate
    node, so an ABSENCE assertion cannot be satisfied by an exception instead.
    """
    node = dict.get(snap, "agent", _NO_LEAF)
    for seg in (agent_name, *path):
        if not isinstance(node, KeyStore):
            return _NO_LEAF
        node = dict.get(node, seg, _NO_LEAF)
    return node


def _nested(key, value):
    """Spell one persona key as the nested FILE shape it corresponds to.

    ``"model"`` → ``{"model": v}``; ``"env.FOO"`` → ``{"env": {"FOO": v}}``. First
    dot only — the same split the production helper does, for the same reason.
    """
    category, sep, var = key.partition(".")
    return {category: {var: value}} if sep else {key: value}


def _persona_snap(
    tmp_path,
    *,
    persona_values,
    agent_file=None,
    agent_state=None,
    system=None,
    workset=None,
    box=None,
):
    """A launch snapshot with the persona tier and any subset of its contenders."""
    return build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=(
            _write_yaml(tmp_path / "system.yaml", system) if system else None
        ),
        agent_path=(
            _write_yaml(tmp_path / "agent.yaml", agent_file) if agent_file else None
        ),
        workset_path=(
            _write_yaml(tmp_path / "ws.yaml", workset) if workset else None
        ),
        box_path=_write_yaml(tmp_path / "box.yaml", box) if box else None,
        agent_state=agent_state,
        persona_values=persona_values,
        valid_agents=_PREF_AGENTS,
    )


@pytest.mark.parametrize("key,path", _PERSONA_CLASSES, ids=lambda v: str(v))
class TestPersonaRungOrdering:
    """The ruled precedence, asserted once per persona VALUE CLASS.

    Each test is MUTATION-PROOF by construction: the contender and the persona
    carry different values, so a rung spliced on the wrong side of a neighbour
    flips exactly one of these red.
    """

    def test_persona_beats_the_descriptor_default_partial(self, tmp_path, key, path):
        """The 7a descriptor DEFAULT (``agent_partial``) is the LEAST-specific level
        that spells the ACTIVE slot, and it sits below ``agent.default``.

        ⚑ This — not ``agent.default`` — is the nearest level the persona rung
        actually CONTENDS with going down. ``agent.default.*`` is a DISJOINT name
        from ``agent.<active>.*`` (§2d keeps the two slots distinct), so the merge
        never puts them in the same contest; see the sibling test below for where
        that comparison really happens.
        """
        category, sep, var = key.partition(".")
        node = {category: {var: "from-descriptor"}} if sep else {key: "from-descriptor"}
        snap = build_launch_snapshot(
            agent_name="claude", ctx=_ctx(),
            system_path=None, agent_path=None, workset_path=None, box_path=None,
            agent_partial=KeyStore({"agent": {"claude": node}}),
            persona_values={key: "from-persona"},
        )
        assert _leaf(snap, path) == "from-persona"
        # CONTROL: without the persona the descriptor default lands, so the assert
        # above is a contest and not an absence.
        control = build_launch_snapshot(
            agent_name="claude", ctx=_ctx(),
            system_path=None, agent_path=None, workset_path=None, box_path=None,
            agent_partial=KeyStore({"agent": {"claude": node}}),
        )
        assert _leaf(control, path) == "from-descriptor"

    def test_the_agent_default_backstop_is_a_disjoint_name_not_a_contest(
        self, tmp_path, key, path,
    ):
        """⚑ Recorded rather than papered over.

        The ruling puts the persona rung ABOVE ``agent.default``, and semantically
        it is: the §2d active-over-default pick (``effective_behavior``) takes the
        active slot unconditionally, and the persona writes the active slot. But
        the two are DIFFERENT KEYS, so the MERGE never contends them — both survive
        side by side, and the level order between them is unobservable. A test that
        asserted an ordering there would be asserting nothing.
        """
        snap = _persona_snap(
            tmp_path,
            persona_values={key: "from-persona"},
            agent_file={"self": {"default": _nested(key, "from-agent-default")}},
        )
        assert _leaf(snap, path) == "from-persona"
        # The backstop is untouched under its OWN true name — no clobber, no merge.
        assert _leaf(snap, path, agent_name="default") == "from-agent-default"

    def test_persona_beats_the_system_file(self, tmp_path, key, path):
        """``system`` CONTAINS ``agent``, so a system file may legally set
        ``agent.<node>.*`` as a machine-wide default — and the persona outranks it."""
        snap = _persona_snap(
            tmp_path,
            persona_values={key: "from-persona"},
            system={"agent": {"claude": _nested(key, "from-system")}},
        )
        assert _leaf(snap, path) == "from-persona"

    def test_persona_loses_to_the_agent_file_active_table(self, tmp_path, key, path):
        """FILE-BEATS-PERSONA. The agent file holds only non-default values, so an
        ``agent.<active>.<key>`` in it can only be a deliberate user edit."""
        self._contended(
            tmp_path, key, path, "from-agent-file",
            agent_file={"self": {"claude": _nested(key, "from-agent-file")}},
        )

    def test_persona_loses_to_a_workset_pref(self, tmp_path, key, path):
        """A workset/box FILE may not write ``agent.*`` directly (§0 directional
        enforcement drops the upward table); ``pref.agent.<agent>.*`` (§2h) is how
        those scopes reach the agent tier — and both pref overlays sit above the
        persona rung."""
        self._contended(
            tmp_path, key, path, "from-workset",
            workset={"pref": {"agent": {"claude": _nested(key, "from-workset")}}},
        )

    def test_persona_loses_to_a_box_pref(self, tmp_path, key, path):
        self._contended(
            tmp_path, key, path, "from-box",
            box={"pref": {"agent": {"claude": _nested(key, "from-box")}}},
        )

    @staticmethod
    def _contended(tmp_path, key, path, expected, **contender):
        """Assert the *contender* wins the key — and that it had to.

        ⚑ The CONTROL is what makes a "persona loses" assertion non-vacuous: on its
        own it would also pass if the persona tier did nothing whatsoever. So the
        same build is repeated WITHOUT the contender and must yield the persona
        value; only then does the first assert pin an ORDERING rather than an
        absence.
        """
        snap = _persona_snap(
            tmp_path / "contended",
            persona_values={key: "from-persona"},
            **contender,
        )
        assert _leaf(snap, path) == expected
        control = _persona_snap(
            tmp_path / "control", persona_values={key: "from-persona"},
        )
        assert _leaf(control, path) == "from-persona"


@pytest.mark.parametrize("key,path", _PERSONA_CLASSES[:2], ids=lambda v: str(v))
def test_persona_loses_to_the_agent_file_flat_state(tmp_path, key, path):
    """The agent file's FLAT ``[agent]`` state rung also beats the persona.

    ⚑ Only the two BARE classes are exercised, and that is structural, not an
    omission: ``read_agent_settings`` builds ``cfg.state`` from the file's SCALAR
    entries only — a dict-valued ``env:`` / ``secret_path:`` table is explicitly
    excluded there and rides ``_agent_partial`` (the ``agent.<active>`` table,
    covered above) instead. So the flat channel cannot carry a dotted class at all.
    """
    snap = _persona_snap(
        tmp_path / "contended",
        persona_values={key: "from-persona"},
        agent_state={key: "from-flat-state"},
    )
    assert _leaf(snap, path) == "from-flat-state"
    # CONTROL (see TestPersonaRungOrdering._contended): without the flat state the
    # persona value lands, so the assert above pins an ORDERING, not an absence.
    control = _persona_snap(tmp_path / "control", persona_values={key: "from-persona"})
    assert _leaf(control, path) == "from-persona"


@pytest.mark.parametrize("key", ["endpoint", "model"])
def test_persona_beats_the_agent_default_backstop_at_the_behavior_read(tmp_path, key):
    """Where persona-beats-``agent.default`` is REALLY decided: the §2d
    active-over-default pick, AFTER the merge.

    ⚑ Bare behavior classes only, and that is structural: ``env`` / ``secret_path``
    are CATEGORIES, read off the discriminated active node by the category adapter,
    with no default-slot pick to win in the first place.
    """
    snap = _persona_snap(
        tmp_path,
        persona_values={key: "from-persona"},
        agent_file={"self": {"default": {key: "from-agent-default"}}},
    )
    assert effective_behavior(snap, active_agent="claude")[key] == "from-persona"
    # CONTROL: the backstop is what a persona-free launch reads.
    control = _persona_snap(
        tmp_path / "control",
        persona_values=None,
        agent_file={"self": {"default": {key: "from-agent-default"}}},
    )
    assert (effective_behavior(control, active_agent="claude")[key]
            == "from-agent-default")


class TestPersonaTierIsInertWhenEmpty:
    """No persona values ⇒ the snapshot is what it was before the tier existed."""

    @staticmethod
    def _rich(tmp_path, **persona_kw):
        # Deliberately NOT a bare snapshot: floor + agent file + both pref-legal
        # files, so an injected empty ``agent.claude`` node (or a shifted level
        # index) has somewhere to show up. ``persona_kw`` is passed through so the
        # ARGUMENT-ABSENT case really omits the parameter (exercising its default).
        return build_launch_snapshot(
            agent_name="claude",
            ctx=_ctx(),
            system_path=None,
            agent_path=_write_yaml(
                tmp_path / "agent.yaml", {"self": {"claude": {"model": "stored"}}},
            ),
            workset_path=_write_yaml(
                tmp_path / "ws.yaml", {"workset": {"boxes": "/ws/boxes"}},
            ),
            box_path=_write_yaml(
                tmp_path / "box.yaml",
                {"pref": {"agent": {"claude": {"access": "full"}}}},
            ),
            behavior_floor={"model": "opus", "allow_helpers": "true"},
            default_categories={"box.bindings.rw": {"~/": ("/h/home", "Z,U")}},
            agent_state={"endpoint": "stored-endpoint"},
            valid_agents=_PREF_AGENTS,
            **persona_kw,
        )

    def test_none_and_empty_and_absent_all_agree(self, tmp_path):
        absent = self._rich(tmp_path / "a")
        explicit_none = self._rich(tmp_path / "b", persona_values=None)
        empty = self._rich(tmp_path / "c", persona_values={})
        assert absent == explicit_none
        assert absent == empty

    def test_no_empty_node_is_injected_for_the_active_agent(self, tmp_path):
        """``{}`` must add NOTHING — not even the ``agent.<active>`` scaffolding."""
        snap = build_launch_snapshot(
            agent_name="ghost", ctx=_ctx(),
            system_path=None, agent_path=None, workset_path=None, box_path=None,
            persona_values={},
        )
        agent_node = dict.get(snap, "agent")
        assert agent_node is None or "ghost" not in agent_node


def test_a_persona_env_var_name_with_a_dot_is_ONE_literal_leaf(tmp_path):
    """⚑ REGRESSION PIN for the divergence from ``dotted_partial``.

    A ``<VAR>`` is arbitrary user-supplied text out of a JSON file. Routing it
    through the dotted exploder would split on EVERY dot and turn one leaf into a
    nested subtree — the var would then never be exported, silently. Split on the
    FIRST dot only: everything after it is a literal leaf key.
    """
    snap = _persona_snap(
        tmp_path, persona_values={"env.WEIRD.VAR": "v", "env.PLAIN": "p"},
    )
    env = _leaf(snap, ("env",))
    assert isinstance(env, KeyStore)
    assert dict.get(env, "WEIRD.VAR") == "v"
    # MUTATION guard: the exploded shape must NOT exist in any form.
    assert "WEIRD" not in env
    assert _leaf(snap, ("env", "WEIRD", "VAR")) is _NO_LEAF
    # The sibling ordinary var is unaffected by the neighbour's spelling.
    assert dict.get(env, "PLAIN") == "p"


def test_a_persona_secret_path_discriminates_onto_the_active_agent(tmp_path):
    """``secret_path.<VAR>`` arrives UN-discriminated and must land under the
    ACTIVE node — the launch SECRET export reads ``agent.<node>.secret_path.*``,
    so a bare or mis-discriminated landing is an invisible auth break."""
    token = "/home/host/.config/personas/navigator/token"
    snap = build_launch_snapshot(
        agent_name="navigator℘claude", ctx=_ctx(),
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        persona_values={"secret_path.ANTHROPIC_AUTH_TOKEN": token},
    )
    assert _leaf(
        snap, ("secret_path", "ANTHROPIC_AUTH_TOKEN"), agent_name="navigator℘claude",
    ) == token
    # Not bare — §0 forbids a bare ``agent.<key>``, so the category may not sit
    # directly on the ``agent`` node, and must not have landed on the ALL-AGENTS
    # ``default`` slot either.
    agent_node = dict.get(snap, "agent")
    assert "secret_path" not in agent_node
    assert _leaf(
        snap, ("secret_path", "ANTHROPIC_AUTH_TOKEN"), agent_name="default",
    ) is _NO_LEAF

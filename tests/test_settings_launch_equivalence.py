"""Behavior-EQUIVALENCE: the block-7b snapshot category path vs the retired
per-family LevelView path produce the SAME reconciled mount/copy/env set.

The Editor's gate (bar #3): for a representative category config the NEW
``build_launch_snapshot → snapshot_category_entries → reconcile_categories`` path
yields the IDENTICAL reconciled (source, dest, options) SET — AND depth-order — as
the OLD ``resolve_categories → reconcile_categories`` path did, so the live swap is
drift-free. Parametrised over the scope-root shapes that distinguish the launch
MODES (standalone has no workset root; named/primary add the workset binding roots)
and the AGENT (the agent-store root differs by agent name), since those are the only
launch-variant inputs the category resolution depends on.
"""

from __future__ import annotations

import pytest

from kanibako.settings_categories import (
    ReconciledCategories,
    reconcile_categories,
    resolve_categories,
)
from kanibako.settings_launch import (
    build_launch_snapshot,
    snapshot_category_entries,
)
from kanibako.settings_resolve import LevelView, ResolveCtx


def _ctx(agent: str, workset: str | None) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent,
        workset_name=workset,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


def _scope_roots(agent: str, ws_root: str | None) -> dict[str, str]:
    share = f"/data/agents/{agent}/share"
    store = f"/data/agents/{agent}"
    roots = {
        "agent.bindings.ro": share,
        "agent.bindings.rw": share,
        "agent.shared": store,
        "agent.caches": store,
    }
    if ws_root is not None:
        roots["workset.bindings.ro"] = ws_root
        roots["workset.bindings.rw"] = ws_root
    return roots


# The resolved system.* tier (what the old _lookup map carried / the new path folds
# into the floor so @-refs resolve from the snapshot).
_RESOLVED_SYS = {
    "system.data": "/data/kanibako",
    "system.agents": "/data/agents",
    "system.channelroot": "/data/channels",
}


def _entry_set(rec: ReconciledCategories) -> dict:
    """A comparable signature of a reconciled result: the ordered MOUNT tuples
    (depth-order load-bearing), and the COPY/ENV sets (order not load-bearing)."""
    return {
        "mounts": [
            (e.category, e.scope, e.host_src, e.box_dest, e.options)
            for e in rec.mounts
        ],
        "copies": sorted(
            (e.category, e.scope, e.host_src, e.box_dest) for e in rec.copies
        ),
        "envs": sorted((e.box_dest, e.options) for e in rec.envs),
    }


# A representative category config exercising: rw/ro binds, a root-joined relative
# agent share, a box-side ``~`` dest, an embedded ``@``-ref host_src, a mask, an env
# var, and a per-entry options override (3rd tuple slot).
def _default_categories() -> dict:
    return {
        "box.bindings.rw.home": ("/h/home", "~/", "Z,U"),
        "box.bindings.ro.kani": ("/opt/k", "/opt/kanibako", "ro"),
        "agent.shared.plugins": ("plugins", "~/.claude/plugins"),  # relative → join
        "box.bindings.rw.data": ("@system.data/x", "/home/agent/x"),  # @-ref host
        "box.masks": ["/home/agent/secret"],
        "box.env.FOO": "bar",
    }


@pytest.mark.parametrize(
    "agent,workset,ws_root",
    [
        ("claude", None, None),                 # standalone (no workset root)
        ("claude", "myws", "/ws/myws"),         # named/primary
        ("goose", None, None),                  # different agent store root
        ("codex", "w2", "/ws/w2"),
    ],
)
def test_snapshot_path_matches_legacy_path(agent, workset, ws_root):
    cats = _default_categories()
    ctx = _ctx(agent, workset)
    roots = _scope_roots(agent, ws_root)

    # --- OLD path: resolve_categories over a single AGENT-level LevelView whose
    # defaults carry the tables + the resolved system.* lookup map.
    old_levels = [
        LevelView("box", {}),
        LevelView("workset", {}),
        LevelView("agent", {}, defaults=dict(cats)),
        LevelView("system", {}),
    ]

    def _lookup(ref, chain):
        if ref in _RESOLVED_SYS:
            return _RESOLVED_SYS[ref]
        raise AssertionError(f"unexpected @-ref {ref}")

    old_entries = resolve_categories(
        levels=old_levels, ctx=ctx, lookup=_lookup, scope_roots=roots,
    )
    old_rec = reconcile_categories(old_entries)

    # --- NEW path: fold the tables + system.* into the floor, build the ONE
    # snapshot, adapt + reconcile.
    floor = dict(cats)
    floor.update(_RESOLVED_SYS)
    snap = build_launch_snapshot(
        agent_name=agent, ctx=ctx,
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories=floor,
    )
    new_entries = snapshot_category_entries(
        snap, active_agent=agent, box_ctx=ctx, scope_roots=roots,
    )
    new_rec = reconcile_categories(new_entries)

    assert _entry_set(new_rec) == _entry_set(old_rec)


# --------------------------------------------------------------------------- #
# DELIVERY equivalence — the riskiest swap: the 7a agent delivery binds + the   #
# override bridge through reconcile + agent_delivery_mounts vs the OLD          #
# descriptor_mounts, per mode×agent, incl. the AGENT_CRITICAL exit-1 parity.    #
# --------------------------------------------------------------------------- #


def _install(agent: str, tmp_path):
    """A real AgentInstall whose binary/launcher/install_dir EXIST (so the
    AGENT_CRITICAL must-exist branch resolves on the existing-source path)."""
    from kanibako.targets.base import AgentInstall

    binary = tmp_path / f"{agent}-bin"
    binary.write_text("x")
    launcher = tmp_path / f"{agent}-launcher"
    launcher.write_text("x")
    install_dir = tmp_path / f"{agent}-share"
    install_dir.mkdir()
    return AgentInstall(
        name=agent, binary=binary, launcher=launcher, install_dir=install_dir,
    )


def _shipped_descriptor(agent: str):
    """The shipped delivery bindings per agent (claude = share+launcher, both
    AGENT_CRITICAL ro; goose/codex = one binary, AGENT_CRITICAL ro) — the
    descriptor-load shape (box_dest already $GUEST_HOME-expanded to absolute)."""
    from kanibako.targets.base import (
        BindKind, Binding, BindScope, HostSrcOrigin, PluginDescriptor,
    )

    def b(key, origin, dest, kind):
        return Binding(
            key=key, origin=origin, box_dest=dest, kind=kind,
            scope=BindScope.AGENT_CRITICAL, ro=True,
        )

    if agent == "claude":
        binds = (
            b("share", HostSrcOrigin.INSTALL_DIR,
              "/home/agent/.local/share/claude", BindKind.DIR),
            b("launcher", HostSrcOrigin.LAUNCHER,
              "/home/agent/.local/bin/claude", BindKind.FILE),
        )
    else:
        binds = (
            b("binary", HostSrcOrigin.BINARY,
              f"/home/agent/.local/bin/{agent}", BindKind.FILE),
        )
    return PluginDescriptor(command=(agent,), bindings=binds, mode={})


def _new_delivery_mounts(agent, install, desc, ctx, *, overrides=None, node_name=None):
    """The NEW single-route delivery: 7a partial (+ override bridge) → snapshot →
    adapter → reconcile → agent_delivery_mounts (critical-set exit-1).

    *node_name* (Block E fix 2a) is the ACTIVE node the read path (active_agent)
    walks; defaults to *agent* (the harness == install.name for a bare agent). For
    a PERSONA (node ≠ harness) the partial MUST root under the node, else the binds
    orphan at agent.<harness>.* and vanish from the emit."""
    from kanibako.agent_representation import agent_default_partial
    from kanibako.settings_launch import agent_delivery_mounts
    from kanibako.targets.base import BindScope

    active = node_name if node_name is not None else agent
    partial = agent_default_partial(desc, install, node_name=active)
    snap = build_launch_snapshot(
        agent_name=active, ctx=ctx,
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        agent_partial=partial,
        binding_overrides=overrides,
        descriptor_bindings=list(desc.bindings),
    )
    rec = reconcile_categories(
        snapshot_category_entries(snap, active_agent=active, box_ctx=ctx)
    )
    critical = frozenset(
        bd.key for bd in desc.bindings if bd.scope is BindScope.AGENT_CRITICAL
    )
    return agent_delivery_mounts(rec.mounts, critical_keys=critical)


def _mount_sig(mounts):
    return sorted(
        (str(m.source), m.destination, m.options) for m in mounts
    )


@pytest.mark.parametrize("agent", ["claude", "goose", "codex"])
def test_delivery_matches_descriptor_mounts(agent, tmp_path):
    # The riskiest swap: the NEW snapshot delivery route must yield the SAME
    # Mount SET (source, dest, options) as the OLD descriptor_mounts, per agent.
    from kanibako.targets.assembly import descriptor_mounts

    install = _install(agent, tmp_path)
    desc = _shipped_descriptor(agent)
    ctx = _ctx(agent, None)

    old = descriptor_mounts(desc, install, overrides={})
    new = _new_delivery_mounts(agent, install, desc, ctx)
    assert _mount_sig(new) == _mount_sig(old)
    # Every shipped delivery bind is ro → options "ro" both paths (no rw drift).
    assert all(m.options == "ro" for m in new)


@pytest.mark.parametrize("agent", ["claude", "goose", "codex"])
def test_delivery_override_bridge_matches(agent, tmp_path):
    # An override repoint must take effect IDENTICALLY through the bridge (NEW)
    # and descriptor_mounts(overrides=) (OLD), per agent.
    from kanibako.targets.assembly import descriptor_mounts

    install = _install(agent, tmp_path)
    desc = _shipped_descriptor(agent)
    ctx = _ctx(agent, None)
    # Repoint the FIRST binding's host source to a different existing file.
    key = desc.bindings[0].key
    repoint = tmp_path / "repointed"
    repoint.write_text("x")
    overrides = {key: str(repoint)}

    old = descriptor_mounts(desc, install, overrides=overrides)
    new = _new_delivery_mounts(agent, install, desc, ctx, overrides=overrides)
    assert _mount_sig(new) == _mount_sig(old)
    assert str(repoint) in {str(m.source) for m in new}  # the repoint took.


# ----------------------------------------------------------------------------- #
# PERSONA FULL-LAUNCH delivery (Block E fix 2a) — the test that WOULD have caught #
# the e2e defect: a ℘ NODE resolves through the FULL snapshot → reconcile →       #
# agent_delivery_mounts path with the descriptor's install.name = HARNESS.        #
# ----------------------------------------------------------------------------- #


def test_persona_node_delivery_binds_emitted_under_node(tmp_path):
    # A persona: the CLAUDE harness (install.name == "claude", claude's share +
    # launcher descriptor) driven at the active NODE "navigator℘claude". The read
    # side (snapshot_category_entries active_agent=node) walks agent.default ∪
    # agent.<node>, so the 7a partial MUST root under the NODE — else the launcher
    # + share (AGENT_CRITICAL) binds orphan at agent.claude.* and are NEVER emitted
    # (the e2e symptom: no -v .../.local/bin/claude → container exits immediately).
    #
    # MUTATION-CHECK: revert fix 2a (agent_representation.py roots the partial under
    # install.name instead of node_name) → these binds vanish from `new` → the
    # non-empty + launcher/share asserts below FAIL. This is the coverage gap that
    # let the defect through (Block A's units never ran a ℘ node through the emit).
    node = "navigator℘claude"
    install = _install("claude", tmp_path)         # install.name == harness "claude"
    desc = _shipped_descriptor("claude")           # share (dir) + launcher (file)
    ctx = _ctx(node, None)                          # ctx keyed by the NODE

    new = _new_delivery_mounts("claude", install, desc, ctx, node_name=node)

    # The launcher + install-dir (share) binds ARE emitted (not orphaned/vanished).
    sources = {str(m.source) for m in new}
    assert new, "persona delivery binds must NOT vanish (fix 2a)"
    assert str(install.launcher) in sources
    assert str(install.install_dir) in sources
    # Byte-identical to what the SAME descriptor emits — the node keys the slot,
    # the bind SET is exactly claude's (the harness plugin's) delivery mounts.
    bare = _new_delivery_mounts("claude", install, desc, _ctx("claude", None))
    assert _mount_sig(new) == _mount_sig(bare)


def test_bare_delivery_byte_identical_before_after_node_threading(tmp_path):
    # Backward-compat (load-bearing): for a BARE agent node == harness == install.name,
    # so threading the node-name is a no-op. Passing node_name explicitly must equal
    # omitting it (which falls back to install.name).
    for agent in ("claude", "goose", "codex"):
        sub = tmp_path / agent
        sub.mkdir()
        install = _install(agent, sub)
        desc = _shipped_descriptor(agent)
        ctx = _ctx(agent, None)
        threaded = _new_delivery_mounts(agent, install, desc, ctx, node_name=agent)
        default = _new_delivery_mounts(agent, install, desc, ctx)  # node_name=None
        assert _mount_sig(threaded) == _mount_sig(default)


@pytest.mark.parametrize("agent", ["claude", "goose", "codex"])
def test_delivery_critical_missing_exit1_parity(agent, tmp_path):
    # AGENT_CRITICAL must-exist exit-1: a missing critical source raises
    # BindingSourceError on BOTH paths (the safe-fail relocated, not dropped).
    from kanibako.targets.assembly import BindingSourceError, descriptor_mounts

    install = _install(agent, tmp_path)
    desc = _shipped_descriptor(agent)
    ctx = _ctx(agent, None)
    # Repoint the first critical binding to a NON-existent source.
    key = desc.bindings[0].key
    gone = {key: str(tmp_path / "gone")}

    with pytest.raises(BindingSourceError):
        descriptor_mounts(desc, install, overrides=gone)
    with pytest.raises(BindingSourceError):
        _new_delivery_mounts(agent, install, desc, ctx, overrides=gone)


def test_depth_order_preserved_across_families():
    # Nested dests must keep depth-order (shallow first) so podman's last-wins
    # resolves the deepest mount on top — the Editor's depth-order condition.
    cats = {
        "box.bindings.rw.home": ("/h", "/home/agent", "Z,U"),
        "box.bindings.rw.ws": ("/h/ws", "/home/agent/workspace", "Z,U"),
        "box.bindings.rw.deep": ("/h/d", "/home/agent/workspace/sub", "Z,U"),
    }
    ctx = _ctx("claude", None)
    snap = build_launch_snapshot(
        agent_name="claude", ctx=ctx,
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories=cats,
    )
    rec = reconcile_categories(
        snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
    )
    dests = [e.box_dest for e in rec.mounts]
    assert dests == ["/home/agent", "/home/agent/workspace", "/home/agent/workspace/sub"]


# --------------------------------------------------------------------------- #
# BEHAVIOR equivalence (ruling A — the FULL read-path swap): the NEW snapshot   #
# behavior read (build_launch_snapshot + effective_behavior) vs the OLD         #
# _build_effective_state, per agent — AND the Jei-noted resolution-order edge.  #
# --------------------------------------------------------------------------- #


from kanibako.settings_launch import effective_behavior  # noqa: E402


def _behavior_snapshot(agent, *, floor, agent_state, box_path, system_path):
    """Build the launch snapshot the LIVE behavior read consumes: the descriptor
    floor (→ agent.default.*), the per-agent FILE state (→ agent.<active>), and any
    box/system settings files (discriminated agent tables, read via assemble)."""
    snap = build_launch_snapshot(
        agent_name=agent, ctx=_ctx(agent, None),
        system_path=system_path, agent_path=None,
        workset_path=None, box_path=box_path,
        behavior_floor=floor, agent_state=agent_state,
    )
    return snap


def _write_yaml(path, data):
    import yaml
    path.write_text(yaml.safe_dump(data))
    return path


# NOTE (block 7c): the OLD oracle ``_build_effective_state`` was RETIRED — the
# behavior read is now snapshot-only on BOTH the launch and the ``config
# --effective`` display, so these tests, which once compared NEW-vs-OLD, are now
# DIRECT spec-property pins of the snapshot behavior read (the equivalence was
# proven; these are the lasting invariants).


@pytest.mark.parametrize("agent", ["claude", "goose", "codex"])
def test_behavior_floor_and_per_agent_state(agent, tmp_path):
    # The common case: a descriptor floor + the per-agent file's flat state, no
    # box/system override. The snapshot read surfaces the per-agent overrides over
    # the declared floor (a None floor default shadowed by a set state value).
    floor = {"model": None, "auto_approve": "true", "continue_mode": "true"}
    state = {"model": "opus", "access": "permissive"}  # the per-agent file (flat)

    snap = _behavior_snapshot(
        agent, floor=floor, agent_state=state, box_path=None, system_path=None,
    )
    eff = effective_behavior(snap, active_agent=agent)

    # The per-agent overrides win; the non-None floor defaults fill the rest; a
    # None floor default (model) is shadowed by the state's set value.
    assert eff == {
        "model": "opus",
        "access": "permissive",
        "auto_approve": "true",
        "continue_mode": "true",
    }


def test_behavior_box_override_beats_agent_file(tmp_path):
    # A box file's agent.<active>.model (more-specific scope) beats the per-agent
    # file's model — standard cascade.
    agent = "claude"
    floor = {"model": None}
    state = {"model": "opus"}
    box = _write_yaml(
        tmp_path / "box.yaml", {"agent": {"claude": {"model": "haiku"}}},
    )

    snap = _behavior_snapshot(
        agent, floor=floor, agent_state=state, box_path=box, system_path=None,
    )
    eff = effective_behavior(snap, active_agent=agent)
    assert eff.get("model") == "haiku"  # box's agent.<active>.model wins.


def test_behavior_resolution_order_edge_is_spec_correction(tmp_path):
    # ⚑ The Jei-NOTED spec-CORRECTION edge (§2d L368): an AGENT-file
    # agent.<active>.model vs a BOX-file agent.DEFAULT.model. The spec model =
    # cascade THEN active-over-default → the agent file's agent.<active>.model WINS
    # (active beats default regardless of scope). The retired OLD reader did
    # per-file-active-over-default THEN cascade → it would have picked the box's
    # agent.default.model ("haiku"); this PINS the spec-correct NEW result.
    agent = "claude"
    floor = {"model": None}
    state = {"model": "opus"}  # the per-agent file = agent.<active>.model = opus
    # A box file that sets only agent.DEFAULT.model (NOT agent.claude.model).
    box = _write_yaml(
        tmp_path / "box.yaml", {"agent": {"default": {"model": "haiku"}}},
    )

    snap = _behavior_snapshot(
        agent, floor=floor, agent_state=state, box_path=box, system_path=None,
    )
    eff = effective_behavior(snap, active_agent=agent)
    # The agent file's agent.<active>.model (opus) wins the §2d L368
    # active-over-default pick over the box's agent.default.model ("haiku") — the
    # CORRECTION the old per-file-then-cascade reader did NOT do.
    assert eff.get("model") == "opus"


def test_higher_scope_present_none_suppresses_floor(tmp_path):
    """A higher-scope present-None (the KeyStore suppression idiom, §3/§6e —
    successor to the old terminal-``""``) over a declared-default floor value
    SUPPRESSES it: the behavior read OMITs the key (the consumer applies its own
    default), and the floor is NOT consulted. A sibling floor default with no
    suppression still shows through.

    This is the STEP-4 (7c) replacement coverage for the retired
    ``test_settings_loader`` ``test_terminal_empty_suppresses_floor`` /
    ``test_floor_is_ultimate_fallback`` — pinned on the LIVE behavior path
    (``build_launch_snapshot`` + ``effective_behavior``)."""
    snap = _behavior_snapshot(
        "claude",
        floor={"auto_approve": "true", "model": "sonnet"},
        agent_state={"auto_approve": None},  # present-None resets the floor key
        box_path=None,
        system_path=None,
    )
    eff = effective_behavior(snap, active_agent="claude")
    # The suppressed key is OMITted — the floor "true" was NOT consulted.
    assert "auto_approve" not in eff
    # A non-suppressed floor default still shows through (floor IS the fallback).
    assert eff.get("model") == "sonnet"


def test_box_config_effective_display_matches_launch_behavior_read(tmp_path):
    """`box config --effective` (the DISPLAY, via
    ``start._effective_behavior_for_display``) reads behavior off the SAME
    KeyStore snapshot the LIVE launch does — so the displayed effective state
    MATCHES what the launch will actually apply.

    Block 7c: the OLD display resolver (``_build_effective_state`` — machine-tier
    + per-file-active-over-default-THEN-cascade order) was retired. Since 7b CUT
    the machine tier at launch (S14), the old display could MISREPRESENT the
    launch; this pins that the display and the launch now agree. The §2d
    active-over-default edge (agent-file active beats box agent.default) is the
    sharpest case — the display must show the launch-correct ``opus``."""
    from types import SimpleNamespace

    from kanibako.commands.start import _effective_behavior_for_display

    agent = "claude"
    floor = {"model": None, "auto_approve": "true"}
    state = {"model": "opus", "access": "permissive"}
    # A box file exercising the §2d edge: agent.default.model set, NOT agent.claude.
    box = _write_yaml(
        tmp_path / "settings.yaml", {"agent": {"default": {"model": "haiku"}}},
    )

    # LAUNCH behavior read: the snapshot + effective_behavior, as start.py does.
    launch_snap = _behavior_snapshot(
        agent, floor=floor, agent_state=state, box_path=box, system_path=None,
    )
    launch_read = effective_behavior(launch_snap, active_agent=agent)

    # DISPLAY read: box config --effective's helper over the same inputs.
    descriptors = [
        SimpleNamespace(key=k, default=v) for k, v in floor.items()
    ]
    target = SimpleNamespace(name=agent, setting_descriptors=lambda: descriptors)
    agent_cfg = SimpleNamespace(state=dict(state))
    display = _effective_behavior_for_display(
        target, agent_cfg, box, system_settings_path=None, workset_config_path=None,
    )

    # The display equals an INDEPENDENT spec-correct expected dict (NOT merely
    # "== the launch read"): model = the agent-file active "opus" (§2d active beats
    # the box agent.default "haiku" — the correction the retired old resolver did
    # NOT do), access = the per-agent passthrough, auto_approve = the floor default.
    assert display == {
        "model": "opus",
        "access": "permissive",
        "auto_approve": "true",
    }
    # ...and it MATCHES the live launch behavior read over the same inputs.
    assert display == launch_read

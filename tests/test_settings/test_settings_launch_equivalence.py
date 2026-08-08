"""DRIFT TRIPWIRE: the live snapshot category path vs the FROZEN legacy by-name
resolver (``flawed_oracle_categories``, quarantined in
``tests/test_flawed_oracle.py``) reconcile to the SAME mount/copy/env set.

The live ``build_launch_snapshot → snapshot_category_entries →
reconcile_categories`` path is compared, for a representative category config,
against the frozen ``flawed_oracle_categories → reconcile_categories`` baseline —
the recorded (source, dest, options) SET AND depth-order.

⚑ The frozen baseline is NOT a correctness authority (the product REPLACED that
resolver because it was wrong in a number of cases). A mismatch therefore means
the live path DRIFTED from the recorded baseline — NOT that the live path is
wrong. On a divergence a HUMAN adjudicates the correct value against the SPEC
(``reference/settings-keyspace-1.8.0.md``); the frozen side may itself be
the buggy one.

⚑ SCOPE NARROWED BY P3. The two paths agreed on ROOT-JOINING a relative source,
and that is precisely where they now differ BY DESIGN: the live path no longer
joins anything (sources are rooted at DECLARATION, spec §2a) while the
frozen oracle keeps its own join forever. Re-basing the comparison by feeding the
oracle pre-rooted values would make it assert a tautology, so instead the fixture
carries NO bare-relative source and the parametrisation no longer varies a root
table. What survives is a real tripwire over everything else the two share
(rw/ro binds, ``@``-ref sources, box-side ``~``, masks, env, per-entry options).
The root-join drift instrument is now the P3 gate probe + the structural
no-resurrection scan in ``test_settings_launch.py``.
"""

from __future__ import annotations

import pytest

from kanibako.settings.settings_categories import (
    ReconciledCategories,
    reconcile_categories,
)
from kanibako.settings.settings_launch import (
    build_launch_snapshot,
    snapshot_category_entries,
)
from kanibako.settings.settings_resolve import LevelView, ResolveCtx

# The FROZEN legacy by-name resolver (retired from the product). It is a DRIFT
# TRIPWIRE, NOT a correctness authority: a mismatch below means the live snapshot
# path DRIFTED from this recorded baseline, and a human adjudicates the correct
# value against the SPEC (reference/settings-keyspace-1.8.0.md) — the OLD
# resolver here may itself be the wrong side.
from tests.test_flawed_oracle import flawed_oracle_categories


def _ctx(agent: str, workset: str | None) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent,
        workset_name=workset,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


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


# A representative category config exercising: rw/ro binds, an agent-scope common
# with a self-resolving ``@``-ref source, a box-side ``~`` dest, an embedded
# ``@``-ref host_src, a mask, an env var, and a per-entry options override. NO
# bare-relative source — see the module docstring.
#
# ⚑ THE TWO SIDES ARE SPELLED DIFFERENTLY ON PURPOSE, and the difference is exactly
# one ruling. The LIVE floor is dest-keyed (R-3/R-5): ``box.bindings.{ro,rw}`` and —
# since 2026-08-08c — ``<scope>.{caches,seeded,common,synced}`` are TERMINAL keys
# whose value is the whole ``{box_dest: (src[, options])}`` map, and the entry NAME
# is gone (R-10). The FROZEN oracle predates all of that and is
# quarantined in the retired name-keyed ``<scope>.<category>.<name> = (src, dest
# [, options])`` shape forever — it is a drift tripwire, not an authority, so it is
# NOT migrated. Keeping both tables written out, rather than deriving one from the
# other, is what keeps the comparison a real one instead of a tautology.
#
# ⚑⚑ THE COMPARISON'S PREMISE SURVIVED THE FLIP UNCHANGED, and that is worth
# saying plainly because it is what makes the respell below safe: ``_entry_set``
# compares ``(category, scope, host_src, box_dest, options)`` and has NEVER
# compared the entry NAME. Dest-keying moved where the destination is WRITTEN, not
# what it resolves to, so both sides still have to agree on the same five facts.
def _default_categories(agent: str) -> dict:
    """The LIVE production-shaped default table: dest-keyed throughout, agent-scope
    keys DISCRIMINATED.

    A plugin builds its own ``agent.<agent>.<category>`` keys (there is no bare
    ``agent.<category>`` anywhere), so the table is agent-specific.
    """
    return {
        # ⚑ ``~/`` is deliberate: R-11 normalizes a dest, so this key IS
        # ``/home/agent`` and matches the oracle's ``~`` below. Before R-11 the two
        # spellings expanded to ``/home/agent/`` and ``/home/agent`` — two dict
        # entries at ONE destination, which is the collision R-11 was ruled to end.
        "box.bindings.rw": {
            "~/": ("/h/home", "Z,U"),
            "/home/agent/x": ("@system.data/x",),  # @-ref host_src, no options
        },
        "box.bindings.ro": {
            "/opt/kanibako": ("/opt/k", "ro"),
        },
        # ⚑ ``common`` is TERMINAL too since 2026-08-08c: the dest moved out of the
        # value and into the map key. The oracle's ``agent.common.plugins`` row
        # below still spells the same delivery the retired way.
        f"agent.{agent}.common": {
            "~/.claude/plugins": (f"@system.agents/{agent}/common/plugins",),
        },
        "box.masks": ["/home/agent/secret"],
        "box.env.FOO": "bar",
    }


def _legacy_default_categories(agent: str) -> dict:
    """The SAME configuration in the FROZEN oracle's retired name-keyed shape.

    ⚑ Do NOT "modernize" this table — the oracle it feeds is quarantined and only
    speaks this shape. The one spelling that had to move is the home dest: the
    oracle expands ``~/`` to ``/home/agent/`` (trailing slash intact, it has no
    dest normalization), so it is spelled ``~`` here — the spelling on which the
    pre-R-11 and post-R-11 destinations agree. That is the whole of the divergence
    between the two tables.
    """
    return {
        "box.bindings.rw.home": ("/h/home", "~", "Z,U"),
        "box.bindings.ro.kani": ("/opt/k", "/opt/kanibako", "ro"),
        f"agent.{agent}.common.plugins": (
            f"@system.agents/{agent}/common/plugins", "~/.claude/plugins",
        ),
        "box.bindings.rw.data": ("@system.data/x", "/home/agent/x"),  # @-ref host
        "box.masks": ["/home/agent/secret"],
        "box.env.FOO": "bar",
    }


@pytest.mark.parametrize(
    "agent,workset",
    [
        ("claude", None),        # standalone (no workset tier)
        ("claude", "myws"),      # named/primary
        ("goose", None),         # a different agent store
        ("codex", "w2"),
    ],
)
def test_snapshot_path_matches_legacy_path(agent, workset):
    cats = _default_categories(agent)
    ctx = _ctx(agent, workset)

    # The FROZEN oracle is the retired BY-NAME resolver: it reads a LevelView keyed
    # at the reconcile shape ``<scope>.<category>.<name>``, which never carried the
    # agent discriminator. Strip it for the baseline side ONLY — the undiscriminated
    # ``agent.<category>`` form is QUARANTINED to this frozen retired-model baseline
    # and exists NOWHERE else (spec §0). The new path takes the production
    # (discriminated) table as-is.
    def _undiscriminate(key: str) -> str:
        prefix = f"agent.{agent}."
        return f"agent.{key[len(prefix):]}" if key.startswith(prefix) else key

    legacy_cats = {
        _undiscriminate(k): v
        for k, v in _legacy_default_categories(agent).items()
    }

    # --- FROZEN BASELINE path: the retired by-name resolver over a single
    # AGENT-level LevelView whose defaults carry the tables + the resolved
    # system.* lookup map. (Drift tripwire, NOT an authority — see module docstring.)
    old_levels = [
        LevelView("box", {}),
        LevelView("workset", {}),
        LevelView("agent", {}, defaults=legacy_cats),
        LevelView("system", {}),
    ]

    def _lookup(ref, chain):
        if ref in _RESOLVED_SYS:
            return _RESOLVED_SYS[ref]
        raise AssertionError(f"unexpected @-ref {ref}")

    # No root table on either side: the fixture has no bare-relative source, so
    # the oracle's own (retained) join has nothing to act on and the comparison is
    # about everything ELSE the two paths share.
    old_entries = flawed_oracle_categories(
        levels=old_levels, ctx=ctx, lookup=_lookup,
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
        snap, active_agent=agent, box_ctx=ctx,
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


def _new_delivery_mounts(agent, install, desc, ctx, *, node_name=None):
    """The NEW single-route delivery: 7a partial → snapshot → adapter → reconcile
    → agent_delivery_mounts (critical-set exit-1).

    *node_name* (Block E fix 2a) is the ACTIVE node the read path (active_agent)
    walks; defaults to *agent* (the harness == install.name for a bare agent). For
    a PERSONA (node ≠ harness) the partial MUST root under the node, else the binds
    orphan at agent.<harness>.* and vanish from the emit."""
    from kanibako.settings.agent_representation import agent_default_partial
    from kanibako.settings.settings_launch import agent_delivery_mounts
    from kanibako.settings.settings_resolve import normalize_bind_dest
    from kanibako.targets.base import BindScope

    active = node_name if node_name is not None else agent
    partial = agent_default_partial(desc, install, node_name=active)
    snap = build_launch_snapshot(
        agent_name=active, ctx=ctx,
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        agent_partial=partial,
    )
    rec = reconcile_categories(
        snapshot_category_entries(snap, active_agent=active, box_ctx=ctx)
    )
    # ⚑ Mirrors ``commands.start`` EXACTLY, and that is the point: the critical set
    # is keyed by NORMALIZED BOX DEST, because a dest-keyed arm's entry name IS the
    # destination (R-10/H6). Building it from ``bd.key`` matches nothing, so every
    # AGENT_CRITICAL bind silently drops to the best-effort branch and the
    # must-exist exit-1 stops firing — which is precisely what
    # ``test_delivery_critical_missing_exit1_parity`` below detects.
    critical = frozenset(
        normalize_bind_dest(bd.box_dest) for bd in desc.bindings
        if bd.scope is BindScope.AGENT_CRITICAL
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


# The user-repoint equivalence (a settable ``agent.<name>.bindings.*`` host-source
# override reaching the emitted Mount through the ordinary cascade) is covered by
# ``test_settings_launch.test_settings_file_repoints_delivery_bind_by_plural_key``
# — the plural-key route that replaced the retired singular override bridge.


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
    from kanibako.targets.base import AgentInstall

    # An install whose delivery sources do NOT exist on the host (no overrides —
    # the origin itself is missing), so the first AGENT_CRITICAL binding fails the
    # must-exist check on both the OLD (descriptor_mounts) and NEW (snapshot →
    # agent_delivery_mounts) paths.
    gone = tmp_path / "gone"
    install = AgentInstall(
        name=agent,
        binary=gone / "bin",
        launcher=gone / "launcher",
        install_dir=gone / "share",
    )
    desc = _shipped_descriptor(agent)
    ctx = _ctx(agent, None)

    with pytest.raises(BindingSourceError):
        descriptor_mounts(desc, install)
    with pytest.raises(BindingSourceError):
        _new_delivery_mounts(agent, install, desc, ctx)


def test_depth_order_preserved_across_families():
    # Nested dests must keep depth-order (shallow first) so podman's last-wins
    # resolves the deepest mount on top — the Editor's depth-order condition.
    #
    # ⚑ The ASSERTION below is UNCHANGED by the dest-key flip, and that is the
    # finding worth recording: the emitted mount order was NEVER the by-name emit
    # order. ``reconcile_categories`` re-sorts the winners by
    # ``(_path_depth(box_dest), box_dest)`` (``settings_categories.py`` — the
    # depth-sort), so the upstream ``(order, category, name)`` emit key only ever
    # broke ties BETWEEN ENTRIES AT ONE DEST — which is now unrepresentable inside
    # an arm anyway. The retired fixture's names (``deep`` < ``home`` < ``ws``)
    # would have emitted the deepest FIRST; the dest keys below are already in
    # depth order. So the flip makes the depth-order property hold twice over
    # instead of once, and changes no expectation.
    #
    # ⚑ The arm is written DEEPEST-FIRST on purpose. A dict preserves insertion
    # order and the arm is walked in that order, so an emit that merely echoed its
    # input would fail this assert — the test proves the depth-SORT, not the
    # fixture's own layout.
    cats = {
        "box.bindings.rw": {
            "/home/agent/workspace/sub": ("/h/d", "Z,U"),
            "/home/agent": ("/h", "Z,U"),
            "/home/agent/workspace": ("/h/ws", "Z,U"),
        },
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


from kanibako.settings.settings_launch import effective_behavior  # noqa: E402


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
    floor = {"model": None, "allow_helpers": "true", "continue_mode": "true"}
    state = {"model": "opus", "access": "editing"}  # the per-agent file (flat)

    snap = _behavior_snapshot(
        agent, floor=floor, agent_state=state, box_path=None, system_path=None,
    )
    eff = effective_behavior(snap, active_agent=agent)

    # The per-agent overrides win; the non-None floor defaults fill the rest; a
    # None floor default (model) is shadowed by the state's set value.
    assert eff == {
        "model": "opus",
        "access": "editing",
        "allow_helpers": "true",
        "continue_mode": "true",
    }


def test_behavior_box_override_beats_agent_file(tmp_path):
    # A box's downward agent tweak beats the per-agent file's model — the box
    # overrides its active agent's behavior. (The box sets the §2h REQUEST, NOT
    # agent.claude.model directly — the latter is an upward write dropped at
    # RESOLVE, spec §0. ⮕ P7: was the ``box.agent.model`` mirror, retired by §2b.)
    agent = "claude"
    floor = {"model": None}
    state = {"model": "opus"}
    box = _write_yaml(
        tmp_path / "box.yaml", {"pref": {"agent": {"claude": {"model": "haiku"}}}},
    )

    snap = _behavior_snapshot(
        agent, floor=floor, agent_state=state, box_path=box, system_path=None,
    )
    eff = effective_behavior(snap, active_agent=agent)
    assert eff.get("model") == "haiku"  # the box's §2h request wins.


def test_behavior_resolution_order_edge_is_spec_correction(tmp_path):
    # ⚑ The Jei-NOTED spec-CORRECTION edge (§2d): an AGENT-file
    # agent.<active>.model vs the agent.DEFAULT.model layer. The spec model =
    # cascade THEN active-over-default → the agent file's agent.<active>.model WINS
    # (active beats default regardless of level). The retired OLD reader did
    # per-file-active-over-default THEN cascade → it would have picked
    # agent.default.model ("haiku"); this PINS the spec-correct NEW result.
    # (The default layer rides the FLOOR — behavior_floor maps to agent.default.*,
    # settings_launch OS1 — NOT a box file: a box may not set agent.default.* per
    # spec §0, that is an upward write dropped at RESOLVE.)
    agent = "claude"
    floor = {"model": "haiku"}  # → agent.default.model (the all-agents default)
    state = {"model": "opus"}  # the per-agent file = agent.<active>.model = opus

    snap = _behavior_snapshot(
        agent, floor=floor, agent_state=state, box_path=None, system_path=None,
    )
    eff = effective_behavior(snap, active_agent=agent)
    # agent.<active>.model (opus) wins the §2d active-over-default pick over
    # agent.default.model ("haiku") — the CORRECTION the old per-file-then-cascade
    # reader did NOT do.
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
        floor={"allow_helpers": "true", "model": "sonnet"},
        agent_state={"allow_helpers": None},  # present-None resets the floor key
        box_path=None,
        system_path=None,
    )
    eff = effective_behavior(snap, active_agent="claude")
    # The suppressed key is OMITted — the floor "true" was NOT consulted.
    assert "allow_helpers" not in eff
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
    active-over-default edge (agent-file active beats the agent.default layer) is
    the sharpest case — the display must show the launch-correct ``opus``. The
    default layer rides the FLOOR (descriptors → agent.default.*, OS1) — NOT a box
    file: a box may not set agent.default.* per spec §0 (upward, dropped at
    RESOLVE). The box file carries only a legal box.* key here."""
    from types import SimpleNamespace

    from kanibako.commands.start import _effective_behavior_for_display

    agent = "claude"
    # agent.default.model rides the floor ("haiku") — the all-agents default the
    # §2d active-over-default pick must be beaten by the active "opus".
    floor = {"model": "haiku", "allow_helpers": "true"}
    state = {"model": "opus", "access": "editing"}
    # A box settings file with only a legal box.* key (no upward agent.* table).
    box = _write_yaml(
        tmp_path / "settings.yaml", {"box": {"image": "img"}},
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
    # NOT do), access = the per-agent passthrough, allow_helpers = the floor default.
    assert display == {
        "model": "opus",
        "access": "editing",
        "allow_helpers": "true",
    }
    # ...and it MATCHES the live launch behavior read over the same inputs.
    assert display == launch_read

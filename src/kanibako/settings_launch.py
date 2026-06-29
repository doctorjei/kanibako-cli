"""Launch-time settings snapshot — the ONE resolve per launch (block 7b).

This module is the LIVE read-path: ``commands/start.py`` builds ONE resolved
:class:`~kanibako.settings_store.KeyStore` snapshot per launch here, via the
committed KeyStore pipeline (``assemble_levels`` → ``merge`` → ``expand``), and
BOTH the behavior reads AND the category :func:`reconcile_categories` pass read
from that SINGLE snapshot (S12 WRITE-ONCE — resolve ONCE, read many). It replaces
the two inline ``LevelView`` cascades start.py used to hand-build per launch (the
behavior cascade and the per-mount-family category cascade) and the ``machine``
(``/etc/kanibako.yaml``) reads (S14).

It is the block-7b consumer SWAP: it IMPORTS the pipeline (single-source — never
re-implements assemble/merge/expand) and the by-dest reconcile pass
(:func:`~kanibako.settings_categories.reconcile_categories`, §6g — kept the
by-dest consumer, fed from the snapshot's category subtrees).

What lands in the one snapshot
------------------------------
The launch has SEVERAL runtime-computed ``default_categories`` tables (channel /
core / kani / helper / image binds, agent shares/seeds, masks) and a behavior
floor — today each rode a per-family ``LevelView``'s ``defaults=`` (the cascade
FLOOR). They ALL fold into ONE ``floor`` dict passed to
``assemble_levels(floor=…)`` (2a folds it UNDER the base file, so a file at any
scope still overrides by name — precedence-equivalent to the old AGENT-level
``defaults=``, verified against ``resolve_value``'s two-pass order). Plus:

* **OS1** — the bare behavior floor (``{d.key: d.default}``) is mapped to the
  SCOPE-QUALIFIED ``agent.default.<key>`` before folding: the declared behavior
  defaults are the ALL-AGENTS backstop (spec §2d lists them under ``agent.default.*``).
  There is NO bare ``agent.<key>`` (spec §0 L21); the §2d L368 active-over-default
  READ layers a per-agent ``agent.<active>.<key>`` over this default.
* **7a** — :func:`~kanibako.agent_representation.agent_default_partial` is an
  ADDITIONAL agent-level partial (S27): the descriptor delivery binds become
  ``agent.<active>.bindings.{ro,rw}.<key>`` in the cascade (the active agent's
  DISCRIMINATED slot, ``install.name``; §2d / §0 L21 — NO bare ``agent`` token), so
  agent binary/launcher/share delivery flows through the ONE category keyspace
  (single-route), NOT a parallel ``descriptor_mounts`` route.
* **override bridge** — the transitional ``agent.<name>.binding.<key>`` repoint
  (read by ``config.read_binding_overrides``) is injected as a host_src repoint of
  ``agent.<active>.bindings.{ro,rw}.<key>`` (the SAME active slot 7a delivers into)
  so it merges over 7a BY NAME — zero-drift preservation of today's
  ``descriptor_mounts(override=…)``. (The singular key's eventual retirement in
  favour of the plural ``agent.<agent>.bindings.*`` is a deferred, Jei-noted
  breaking change — NOT silent here.)

The DISCRIMINATED agent read (§2d L368)
---------------------------------------
The snapshot keeps the agent tier discriminated — ``agent.default.*`` (the
all-agents backstop) and ``agent.<active>.*`` (the active slot, where 7a / the
override bridge / a per-agent file land). Both the behavior read
(:func:`effective_behavior`) and the category adapter
(:func:`snapshot_category_entries`) do the active-over-default value-pick PER NAME
HERE — the consumer's job, since 2a/7a / the merge deliberately keep both slots'
keys discriminated. Emitted ``CategoryEntry``\\ s carry the BARE ``agent`` scope
token (load-bearing for ``scope_roots`` + reconcile, NOT the discriminator).

box_dest deferral (S17 / B6)
----------------------------
The snapshot keeps box-side ``$XDG`` / ``~`` in a ``Bind.box`` RAW (deferred —
host ≠ box). The category ADAPTER (:func:`snapshot_category_entries`) is a
``box_dest`` consumer: it resolves box-side ``~`` → ``GUEST_HOME`` and ``$XDG``
against the BOX ctx (matching today's ``resolve_categories`` ``space="guest"``)
BEFORE building each :class:`CategoryEntry`, so reconcile keys on the SAME
absolute ``box_dest`` it did pre-swap (depth-sort + dest-collision unchanged). The
S20 escape contract (backslash-escaped ``$`` / ``~`` / ``\\`` carried literal) is
honored by the shared ``expand_expr`` scanner.

Authority: ``~/vault/rw/keystore-design.md`` §1/§2/§4/§6g; SEAMS
S7/S8/S9/S12/S14/S17/S20/S26/S27 + OS1; spec ``settings-keyspace-1.6.0-target.md``
§0/§1/§2/§2a/§2c.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from kanibako.settings_assemble import assemble_levels
from kanibako.settings_categories import (
    _DELIVERY,
    CategoryEntry,
    _bind_options,
)
from kanibako.settings_expand import expand
from kanibako.settings_merge import merge
from kanibako.settings_resolve import ResolveCtx, SettingsError, expand_expr
from kanibako.settings_store import _MISSING, Bind, KeyStore

if TYPE_CHECKING:
    from kanibako.targets.base import Binding


# The category tokens that hold bind-shaped (``Bind``) leaves in the snapshot's
# ``<scope>.<category>`` subtrees. ``bindings`` carries ``ro`` / ``rw`` sub-nodes;
# the rest are flat ``<category>.<name>`` bind maps.
_BIND_LEAF_CATEGORIES: frozenset[str] = frozenset(
    {"caches", "seeded", "shared", "synced"}
)
_SCOPES: tuple[str, ...] = ("system", "agent", "workset", "box")


# --------------------------------------------------------------------------- #
# Snapshot build — the ONE resolve per launch                                 #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Group-auth capability chain (block #2 — ratified 2026-06-29)                 #
# --------------------------------------------------------------------------- #
#
# The spec's CAPABILITY CHAIN rooted at the agent tier (spec §2a L184 / §2b L282 /
# §2c L315-316,331-332,381 / §2d L399; design L689-719). These keys are injected
# into the launch snapshot floor so ``expand`` resolves the @-ref chain ONCE
# (single-route — NO second resolver). A scope FILE may still override a key by
# name (the floor sits under ``base``), preserving the cascade.
#
# Chain (the WHAT — the spec is the authority):
#   agent.default.group_auth_capable  = True   (UNIVERSAL floor; every agent
#                                               inherits via agent.<agent>.<key> |
#                                               agent.default.<key>)
#   workset.meta.group_auth_available = @agent.<agent>.group_auth_capable  (ceiling)
#   workset.group_auth_enabled        = @workset.meta.group_auth_available  (policy)
#   box.meta.group_auth_available     = @workset.group_auth_enabled         (box ceiling)
#   box.group_auth_on                 = True   (box's CHOICE; settable)
#   effective_group_auth = box.meta.group_auth_available AND box.group_auth_on
#
# STANDALONE (degenerate lone box, spec §2c L315-316): workset.meta.group_auth_
# available AND workset.group_auth_enabled are the LITERAL False (NOT the @-ref) —
# so box.meta.group_auth_available short-circuits to False WITHOUT traversing to
# the agent tier (and the @-ref still RESOLVES — closes the standalone gap).

#: The agent-tier capability FLOOR. JC-1 (ruling pending): the single universal
#: source — one literal, every target (incl. NoAgent / future) inherits it via the
#: generic ``agent.<agent>.<key> | agent.default.<key>`` rule. NOT duplicated per
#: plugin (single-source principle). A future non-capable agent overrides
#: ``agent.<x>.group_auth_capable = False`` in its own settings.
_GROUP_AUTH_CAPABLE_KEY = "agent.default.group_auth_capable"
#: The box's settable CHOICE default (spec §2b L282) — replaces the per-box
#: ``[project].group_auth`` narrowing. A box file / read-compat may override it.
_BOX_GROUP_AUTH_ON_KEY = "box.group_auth_on"


def group_auth_chain_floor(
    *,
    mode: str,
    agent_name: str,
    workset_enabled_override: bool | None = None,
    box_on_override: bool | None = None,
) -> dict[str, object]:
    """Build the group-auth capability-chain floor keys for *mode* (JC-2 seam).

    Returns the ``{dotted_key: value}`` floor fragment for the spec's group-auth
    chain (spec §2a/§2b/§2c/§2d; design L689-719). The caller folds it into
    ``build_launch_snapshot``'s floor so ``expand`` resolves the @-ref chain ONCE
    (single-route). *mode* is the box's :class:`~kanibako.paths.BoxMode` value
    (``"primary"`` / ``"named"`` / ``"standalone"``), passed as a plain string to
    avoid a paths import.

    PRIMARY / NAMED use the @-ref forms (the policy derives from the active
    agent's capability via the meta ceiling). STANDALONE pins the two workset keys
    to the LITERAL ``False`` so ``box.meta.group_auth_available`` short-circuits
    without traversing to the agent tier (spec §2c L315-316) — the @-ref still
    RESOLVES (the gap this chain closes).

    *workset_enabled_override* / *box_on_override* are the JC-3 read-compat inputs:
    an existing on-disk ``[project].group_auth=false`` (workset policy or per-box
    choice) maps HERE to ``workset.group_auth_enabled=False`` /
    ``box.group_auth_on=False`` so a distinct-auth box keeps working — WITHOUT ever
    writing the old form. ``None`` (the common case) leaves the spec default. An
    override is layered as a literal in the FLOOR; a scope FILE that sets the new
    key (a post-reshape config) still wins by name through the cascade.
    """
    floor: dict[str, object] = {
        # The universal agent-tier capability floor (JC-1): the all-agents
        # backstop, spec §2d L399. Recorded under ``agent.default`` so the agent
        # tier is consistent with the spec's ``agent.default.<key>`` shape.
        _GROUP_AUTH_CAPABLE_KEY: True,
        # The ACTIVE agent's capability slot, MATERIALIZED from the floor so the
        # whole-value @-ref ``@agent.<agent>.group_auth_capable`` RESOLVES at
        # expand time (``expand`` follows the literal dotted path — it does NOT
        # apply the active-over-default consumer pick that ``effective_behavior``
        # does). Seeding the active slot to the same floor default means every
        # shipped agent inherits ``True``; a future NON-capable agent's settings
        # file sets ``agent.<agent>.group_auth_capable=false``, which WINS by name
        # through the cascade (the floor sits under ``base``). This is the §2d
        # ``agent.<agent>.<key> | agent.default.<key>`` rule made concrete for the
        # @-ref's literal-path resolution.
        f"agent.{agent_name}.group_auth_capable": True,
        # The box ceiling @-ref — identical in EVERY mode (it resolves to the
        # workset literal under standalone, or down the chain otherwise).
        "box.meta.group_auth_available": "@workset.group_auth_enabled",
        # The box's settable choice — default ON (spec §2b L282).
        _BOX_GROUP_AUTH_ON_KEY: True,
    }
    if mode == "standalone":
        # STANDALONE short-circuit (spec §2c L315-316): a lone box has no group →
        # the workset keys are the LITERAL False, NOT the agent-tier @-ref.
        floor["workset.meta.group_auth_available"] = False
        floor["workset.group_auth_enabled"] = False
    else:
        # PRIMARY / NAMED (ALL WORKSETS, spec §2c L331-332): availability = the
        # ACTIVE agent's capability; policy defaults to availability.
        floor["workset.meta.group_auth_available"] = (
            f"@agent.{agent_name}.group_auth_capable"
        )
        floor["workset.group_auth_enabled"] = "@workset.meta.group_auth_available"
        # JC-3 read-compat: an existing on-disk workset-level group_auth=false
        # overrides the policy key (workset → group_auth_enabled). Only False is
        # carried (True is the spec default already); never written back.
        if workset_enabled_override is False:
            floor["workset.group_auth_enabled"] = False
    # JC-3 read-compat: an existing on-disk per-box [project].group_auth=false
    # maps to box.group_auth_on (the box's choice). Mode-independent.
    if box_on_override is False:
        floor[_BOX_GROUP_AUTH_ON_KEY] = False
    return floor


def effective_group_auth(snapshot: KeyStore, *, mode: str | None = None) -> bool:
    """Read ``effective_group_auth`` off the expanded snapshot (spec §2b L282).

    ``effective = box.meta.group_auth_available AND box.group_auth_on`` — the
    SINGLE bool that feeds the existing gates (``reconcile_categories``, credsync,
    auto-auth, writeback, the ``kanibako agent`` display) UNCHANGED. Reads the
    box ``meta`` node via the typed :class:`~kanibako.settings_views.MetaView`
    (``group_auth_available`` — the demo view, now wired) and ``box.group_auth_on``
    via the typed bool view — NOT a hand-parse (design §5 typed access). Both are
    resolved (block 7) to real ``bool`` terminals by ``expand`` (a whole-value
    @-ref inherits its referent's type), so :func:`as_bool` does not launder.

    Raises :class:`~kanibako.settings_views.ViewError` only on a BUILD-invariant
    breach (a chain key absent / mistyped) — which would mean the floor injection
    failed; that is a programming error, surfaced loudly, never a launder.
    """
    from kanibako.settings_views import as_bool

    box_node = dict.get(snapshot, "box", _MISSING)
    if not isinstance(box_node, KeyStore):
        # The chain floor always seeds box.* — an absent box node means the floor
        # was not injected. Fail closed (no group auth) rather than launder.
        return False
    meta_node = dict.get(box_node, "meta", _MISSING)
    available = False
    if isinstance(meta_node, KeyStore):
        available = as_bool(dict.get(meta_node, "group_auth_available", False))
    on = as_bool(dict.get(box_node, "group_auth_on", False))
    return bool(available and on)


def build_launch_snapshot(
    *,
    agent_name: str,
    ctx: ResolveCtx,
    system_path: Path | None,
    agent_path: Path | None,
    workset_path: Path | None,
    box_path: Path | None,
    behavior_floor: Mapping[str, object] | None = None,
    default_categories: Mapping[str, object] | None = None,
    agent_partial: KeyStore | None = None,
    agent_state: Mapping[str, str] | None = None,
    binding_overrides: Mapping[str, str] | None = None,
    descriptor_bindings: "list[Binding] | None" = None,
    group_auth_chain: Mapping[str, object] | None = None,
) -> tuple[KeyStore, list[str]]:
    """Build the ONE expanded launch snapshot + the required-override warnings.

    Folds the behavior floor (mapped to ``agent.default.<key>`` — OS1, the
    all-agents backstop) and every runtime ``default_categories`` table (a bare
    ``agent.<cat>.*`` table key re-rooted to the active slot ``agent.<agent_name>.
    <cat>.*``) into ONE base-level floor, assembles the 7-level cascade (S8) with
    7a's *agent_partial* inserted as an additional agent-level source (S27), merges
    (S15), and expands (S17/S19) with *ctx*. There is NO bare ``agent.<key>`` in the
    snapshot (spec §2d / §0 L21) — the agent tier is DISCRIMINATED throughout.

    *behavior_floor* is the BARE behavior-default dict (``{d.key: d.default}``);
    *default_categories* are the already-scope-qualified category default tables
    (``{"box.bindings.rw.home": (h, d, o), ...}``) unioned across every mount
    family. *binding_overrides* are the transitional ``{binding_key: host_src}``
    repoints (bridge), placed via *descriptor_bindings* (each ``Binding`` supplies
    the ``ro`` flag selecting ``bindings.ro`` vs ``bindings.rw``).

    *group_auth_chain* is the group-auth capability-chain floor fragment (block
    #2) built by :func:`group_auth_chain_floor` per box mode — the spec's @-ref /
    literal chain keys (spec §2a/§2b/§2c/§2d; design L689-719). Folded into the
    SAME floor so ``expand`` resolves the chain ONCE (single-route). ``None`` for a
    NARROW resolve that does not need the chain (the seed/synced/image/helper
    sub-resolves), so those snapshots simply lack the chain keys.

    Returns ``(snapshot, warnings)``; *warnings* is the ``required``-override
    diagnostics channel (S10) the caller surfaces.
    """
    floor: dict[str, object] = {}
    # OS1: bare behavior keys → scope-qualified agent.default.<key>. The declared
    # behavior defaults are the ALL-AGENTS backstop (spec §2d lists them under
    # ``agent.default.*`` — auto_approve / allow_helpers / model / …). The §2d
    # L368 active-over-default READ (effective_behavior) then layers a per-agent
    # ``agent.<active>.<key>`` over this default. There is NO bare ``agent.<key>``
    # (spec §0 L21).
    if behavior_floor:
        for key, val in behavior_floor.items():
            floor[f"agent.default.{key}"] = val
    # Category default tables are already scope-qualified dotted keys. A live
    # ""-suppression of a DEFAULT means "this default is disabled" → just DROP it
    # (absent ≡ no default), matching resolve_categories' terminal skip. (A box/
    # workset FILE ""-suppression of an inherited default is a separate path —
    # see the module note; no shipped default table uses "".)
    #
    # OS1 (agent scope): the default tables emit BARE ``agent.<category>.<name>``
    # keys (``default_shares()`` → ``agent.shared.plugins``, ``default_seeds()`` →
    # ``agent.seeded.*``, the agent ``scope_roots`` families). The snapshot agent
    # tier is DISCRIMINATED (§2d / §0 L21 — NO bare ``agent.<key>``); these are the
    # ACTIVE agent's own declared defaults (they come from the active target), so
    # re-root them to the active slot ``agent.<agent_name>.<category>.<name>``. A
    # box/workset/agent file STILL overrides them by name through the merge, and the
    # adapter's active-over-default pick reads them. (``agent.default.*`` from the
    # behavior floor above + any explicitly-default-keyed table entry are left as-is.)
    if default_categories:
        for key, val in default_categories.items():
            if val == "":
                continue
            key = _agent_scope_qualify(key, agent_name)
            # masks BRIDGE: a live ``<scope>.masks`` value is a LIST[box_dest]
            # (the shipped/file form); the KeyStore model is a keyed
            # ``dict[box_dest → bool]`` (S5/§6f). Convert here so the snapshot's
            # masks node is the keyed shape the adapter reads (present = mask).
            if (key == "masks" or key.endswith(".masks")) and isinstance(
                val, (list, tuple)
            ):
                floor[key] = {str(dest): True for dest in val}
                continue
            floor[key] = val

    # Group-auth capability chain (block #2): the @-ref / literal chain keys
    # (spec §2a/§2b/§2c/§2d; design L689-719) fold into the SAME floor so
    # ``expand`` resolves the chain ONCE (single-route). Built per mode by
    # :func:`group_auth_chain_floor`; a scope FILE still overrides a key by name
    # (the floor sits under ``base``). Injected AFTER the category tables so the
    # dotted chain keys (``box.group_auth_on`` / ``box.meta.group_auth_available``
    # / ``workset.*``) land in the floor unconditionally.
    if group_auth_chain:
        for key, val in group_auth_chain.items():
            floor[key] = val

    base_levels = assemble_levels(
        agent_name=agent_name,
        system_path=system_path,
        agent_path=agent_path,
        workset_path=workset_path,
        box_path=box_path,
        floor=floor,
    )
    # ``assemble_levels`` ALWAYS returns the 7 levels MOST-SPECIFIC-FIRST (S8):
    #   [required, box, workset, agent.<active>, agent.default, system, base]
    #    idx 0       1     2        3              4              5       6
    # Build the FINAL ordered level list by splicing the optional extra partials at
    # their PRECISE precedence rungs, computed from these FIXED base indices (doing
    # all splices in one pass keeps the math robust — no chained index drift):
    #
    #   override bridge  — just below box (above workset): wins 7a's origin default
    #                      by name, loses to a box-file / ``required`` set.
    #   agent_state      — the per-agent FILE's behavior, wrapped under the active
    #                      slot, at the AGENT-FILE rung (above the empty assemble
    #                      agent.<active> level, below workset): the OLD
    #                      ``LevelView("agent", agent_cfg.state)`` precedence.
    #   agent_partial    — 7a descriptor DEFAULT delivery, the LEAST-specific agent
    #                      rung (just below agent.default) so any
    #                      agent.<active>/workset/box repoint wins.
    bridge = _override_bridge_partial(
        agent_name, binding_overrides, descriptor_bindings
    )
    state_partial = _agent_state_partial(agent_name, agent_state)

    levels: list[KeyStore] = []
    levels.append(base_levels[0])                       # required
    levels.append(base_levels[1])                       # box
    if bridge is not None:
        levels.append(bridge)                           # override bridge (below box)
    levels.append(base_levels[2])                       # workset
    if state_partial is not None:
        levels.append(state_partial)                    # per-agent FILE behavior
    levels.append(base_levels[3])                       # agent.<active> (file tables)
    levels.append(base_levels[4])                       # agent.default
    if agent_partial is not None:
        levels.append(agent_partial)                    # 7a descriptor default
    levels.append(base_levels[5])                       # system
    levels.append(base_levels[6])                       # base (+ folded floor)

    snapshot, warnings = merge(levels)
    expanded = expand(snapshot, ctx)
    return expanded, warnings


def _agent_state_partial(
    agent_name: str, agent_state: Mapping[str, str] | None
) -> KeyStore | None:
    """Wrap the per-agent FILE's FLAT ``[agent]`` behavior state under the active
    slot — ``{agent: {<agent_name>: {<key>: <val>}}}`` — or ``None`` if empty.

    The per-agent file (``agents/<active>/settings.yaml``, loaded as
    ``agent_cfg.state``) stores behavior FLAT (``agent.model`` — already per-agent),
    NOT the discriminated ``agent.<active>.*`` / ``agent.default.*`` sub-tables that
    ``assemble_levels``' ``_agent_partial`` reads (it treats a flat ``[agent]`` table
    as UNSET). So passing the file raw as ``agent_path`` DROPS its behavior. This
    wraps it into the DISCRIMINATED active slot (the §2d / §0 L21 form) so it merges
    by name; undeclared keys (``start_mode`` / ``access``) ride through verbatim.
    """
    if not agent_state:
        return None
    active_node = KeyStore()
    for key, val in agent_state.items():
        active_node[key] = val
    agent_node = KeyStore()
    agent_node[agent_name] = active_node
    partial = KeyStore()
    partial["agent"] = agent_node
    return partial


def _override_bridge_partial(
    agent_name: str,
    binding_overrides: Mapping[str, str] | None,
    descriptor_bindings: "list[Binding] | None",
) -> KeyStore | None:
    """Build the override-bridge partial, or ``None`` if no override applies.

    A transitional ``agent.<name>.binding.<key>`` repoint (today's
    ``descriptor_mounts(override=…)``, which ALWAYS wins a binding's host source)
    becomes a host_src repoint of the DISCRIMINATED
    ``agent.<agent_name>.bindings.{ro,rw}.<key>`` (the §2d key form, §0 L21 — NO bare
    ``agent``), under the SAME active-agent slot 7a delivers the descriptor binds
    into (``install.name`` == *agent_name*). The caller splices it just BELOW box so
    it beats 7a's origin default by name yet loses to a box-file / ``required`` set.
    """
    if not binding_overrides or not descriptor_bindings:
        return None
    ro_by_key = {b.key: bool(b.ro) for b in descriptor_bindings}
    dest_by_key = {b.key: b.box_dest for b in descriptor_bindings}
    bridge = KeyStore()
    for key, host_src in binding_overrides.items():
        if key not in ro_by_key:
            continue  # not a descriptor binding key → nothing to repoint.
        mode = "ro" if ro_by_key[key] else "rw"
        opts = "ro" if ro_by_key[key] else None
        _insert_bind(
            bridge, ("agent", agent_name, "bindings", mode, key),
            Bind(host_src, dest_by_key[key], opts),
        )
    return bridge if dict.__len__(bridge) else None


def _agent_scope_qualify(key: str, agent_name: str) -> str:
    """Re-root a BARE ``agent.<category>.*`` default-table key onto the active slot.

    A category default table emits bare ``agent.<category>.<name>`` keys (no
    discriminator). The snapshot agent tier is DISCRIMINATED (§2d / §0 L21 — NO bare
    ``agent.<key>``), so a bare ``agent.*`` key is re-rooted to
    ``agent.<agent_name>.<category>.<name>`` (the active agent's own declared
    default). A key that is ALREADY discriminated (``agent.default.*`` or
    ``agent.<agent_name>.*``) or is not agent-scoped is returned unchanged.
    """
    if not key.startswith("agent."):
        return key
    second = key.split(".", 2)[1]
    # Already discriminated (a real agent slot) → leave as-is. The discriminators in
    # play here are ``default`` and the active agent name; a bare category token
    # (``shared`` / ``caches`` / ``bindings`` / ``seeded`` / ``synced`` / ``masks`` /
    # ``env``) means UN-discriminated and must be re-rooted to the active slot.
    if second in ("default", agent_name):
        return key
    return f"agent.{agent_name}.{key[len('agent.'):]}"


def _insert_bind(store: KeyStore, path: tuple[str, ...], bind: Bind) -> None:
    """Insert *bind* at *path* into *store*, creating nested KeyStore nodes.

    Used to build the override-bridge partial. Uses the UNBOUND ``dict`` probe
    (S3) so a path segment named ``get`` cannot shadow the protocol.
    """
    node: KeyStore = store
    for seg in path[:-1]:
        existing = dict.get(node, seg, _MISSING)
        if not isinstance(existing, KeyStore):
            existing = KeyStore()
            node[seg] = existing
        node = existing
    node[path[-1]] = bind


# --------------------------------------------------------------------------- #
# Behavior read — typed off the ONE snapshot                                  #
# --------------------------------------------------------------------------- #


def effective_behavior(
    snapshot: KeyStore, *, active_agent: str, keys: "list[str] | None" = None
) -> dict[str, str]:
    """Read the resolved BEHAVIOR values off the snapshot's DISCRIMINATED agent
    subtree, as the ``{key: str}`` dict the descriptor assembler consumes.

    This is the LIVE launch behavior reader (block 7b — ruling A, the FULL swap):
    it replaces ``start.py``'s retired ``_build_effective_state`` LAUNCH read. The
    behavior cascade now flows through the ONE snapshot — each scope file's
    ``agent.default.*`` / ``agent.<active>.*`` tables merge by NAME (block 2b /
    assemble_levels S8), the declared-default floor folds in under ``base`` as
    ``agent.default.*`` (OS1) — and THIS function does the §2d L368
    active-over-default value-pick over that merged result.

    Resolution order (the SPEC model, S8 + §2d L368): cascade FIRST, THEN
    active-over-default. The merge already resolved ``agent.<active>.<key>`` and
    ``agent.default.<key>`` across ALL scopes by name (a box-file
    ``agent.<active>.model`` beats the agent-file one — box is more specific); this
    pick then takes the active slot's winner over the default slot's winner. So an
    agent-file ``agent.<active>.model`` BEATS a box-file ``agent.default.model``
    (active wins the pick regardless of scope) — the one place this differs from the
    old per-file-active-over-default-THEN-cascade reader, a Jei-NOTED spec-CORRECTION
    (covered by a behavior-equivalence test), NOT silent.

    *keys*: when given, read exactly those keys; when ``None`` (the live default),
    DISCOVER every scalar behavior leaf present under ``agent.<active>`` ∪
    ``agent.default`` (so undeclared pass-through keys like ``start_mode`` /
    ``access`` survive, matching the old reader's key union). Category subtrees
    (``bindings`` / ``meta`` / ``shared`` / …) and ``Bind`` leaves are NOT behavior
    and are skipped.

    A key absent from BOTH slots is omitted. A present-``None`` scalar
    (reset-to-default) in the WINNING slot is omitted (the consumer applies its own
    default, §3) — and, since present-None SETS the name, it shadows the
    ``agent.default`` value below it (the active slot reset it). Values are
    stringified (behavior settings are scalars). Reads via the UNBOUND ``dict``
    probe (S3).
    """
    agent_node = dict.get(snapshot, "agent", _MISSING)
    out: dict[str, str] = {}
    if not isinstance(agent_node, KeyStore):
        return out
    active_node = dict.get(agent_node, active_agent, _MISSING)
    default_node = dict.get(agent_node, "default", _MISSING)

    if keys is None:
        # DISCOVER: the union of scalar-leaf names under both slots (active first,
        # so the active set order leads; absence elsewhere is harmless). Category
        # subtrees / Bind leaves are filtered out per-key below.
        discovered: dict[str, None] = {}
        for node in (active_node, default_node):
            if isinstance(node, KeyStore):
                for name in dict.keys(node):
                    discovered.setdefault(name, None)
        key_iter: "list[str]" = list(discovered)
    else:
        key_iter = keys

    for key in key_iter:
        # Active-over-default pick (§2d L368): probe the active slot first; a
        # present value (incl. present-None) SETS the key and shadows default.
        if isinstance(active_node, KeyStore):
            val = dict.get(active_node, key, _MISSING)
        else:
            val = _MISSING
        if val is _MISSING:
            # Active did not set it → fall back to the agent.default backstop.
            if isinstance(default_node, KeyStore):
                val = dict.get(default_node, key, _MISSING)
        if val is _MISSING or val is None:
            continue
        # Behavior leaves are scalars; a category subtree / Bind is NOT behavior.
        if isinstance(val, (KeyStore, Bind)):
            continue
        out[key] = val if isinstance(val, str) else str(val)
    return out


# --------------------------------------------------------------------------- #
# Category adapter — snapshot subtrees → the list reconcile_categories eats    #
# --------------------------------------------------------------------------- #


def agent_delivery_mounts(
    reconciled_mounts: "list[CategoryEntry]",
    *,
    critical_keys: "frozenset[str]",
):
    """Emit the AGENT delivery :class:`~kanibako.targets.base.Mount`s from the
    reconciled ``agent.bindings.{ro,rw}`` winners — the single-route replacement
    for ``descriptor_mounts``' MOUNT role (S27).

    *reconciled_mounts* is the full MOUNT winner list from
    :func:`reconcile_categories`; this picks the ``scope == "agent"`` /
    ``category in bindings.ro|rw`` entries (the descriptor delivery binds, now in
    the cascade via 7a's partial + the override bridge). For each:

    * **AGENT_CRITICAL** (``name`` in *critical_keys*): the host_src MUST exist,
      else :class:`~kanibako.targets.assembly.BindingSourceError` is raised — the
      clean exit-1 safe-fail (must-exist), preserved from ``descriptor_mounts``.
    * **AGENT** (best-effort): a missing host_src is SKIPPED (a missing/suppressed
      agent share is fine) — matching ``descriptor_mounts``' AGENT branch.

    Returns the ordered ``list[Mount]``. (Symlink-clearing at critical dests stays
    the caller's ``_precreate_mount_stubs`` job, unchanged.)
    """
    from kanibako.targets.assembly import BindingSourceError
    from kanibako.targets.base import Mount

    mounts: list = []
    for e in reconciled_mounts:
        if e.scope != "agent" or e.category not in ("bindings.ro", "bindings.rw"):
            continue
        assert e.host_src is not None  # bind-shaped entries always have a source.
        src = Path(e.host_src)
        if e.name in critical_keys:
            if not src.exists():
                raise BindingSourceError(
                    f"binding {e.name!r} source missing: {src}"
                )
            mounts.append(Mount(src, e.box_dest, e.options))
        elif src.exists():
            mounts.append(Mount(src, e.box_dest, e.options))
        # else: best-effort AGENT bind, source missing → skip.
    return mounts


def snapshot_category_entries(
    snapshot: KeyStore,
    *,
    active_agent: str,
    box_ctx: ResolveCtx,
    scope_roots: Mapping[str, str] | None = None,
) -> list[CategoryEntry]:
    """Walk the snapshot's category subtrees → the ``list[CategoryEntry]``
    :func:`reconcile_categories` consumes (the SAME shape ``resolve_categories``
    produced), so the by-dest reconcile pass is unchanged (§6g).

    For every ``<scope>.<category>`` subtree present it emits one entry per leaf.
    The four scopes are the SAME ``system, agent, workset, box`` apply order the
    old ``resolve_categories`` used (so a reconcile tie breaks identically), and
    every emitted entry's ``scope`` / root-join ``group`` is the BARE scope token
    (``agent`` / ``agent.<category>``) — the load-bearing scope identity (§7 /
    ``scope_roots``), NOT the snapshot's agent discriminator.

    The AGENT scope is DISCRIMINATED in the snapshot (``agent.default.*`` /
    ``agent.<active>.*``, spec §2d / §0 L21 — NO bare ``agent.<key>``). This
    consumer does the §2d L368 active-over-default value-pick PER NAME: it builds an
    EFFECTIVE agent node = ``agent.default`` overlaid by ``agent.<active_agent>``
    (the active slot wins each name it sets; ``agent.default`` fills the gaps), then
    walks that one effective node as the (bare) ``agent`` scope. The descriptor
    delivery binds (7a) + the override bridge live under the active slot; the
    all-agents declared defaults live under ``agent.default`` — so this pick is the
    delivery-side analog of :func:`effective_behavior`'s read.

    host_src is read from the expanded ``Bind`` (already host-resolved at build),
    then ROOT-JOINED: a RELATIVE host_src under a group that has a *scope_roots*
    entry (``agent.shared`` → the per-agent store dir, ``agent.bindings.ro`` → the
    share root, etc.) is prefixed with that root — replicating the old
    ``resolve_categories`` join EXACTLY (relative-only, root absolute). box_dest is
    resolved BOX-side here (this is a ``box_dest`` consumer, B6): ``~`` →
    ``GUEST_HOME`` and ``$XDG`` against *box_ctx* — matching the old
    ``space="guest"`` pass — so reconcile keys on the SAME absolute dest. ``env``
    carries its VAR name in ``box_dest`` and its value in ``options``; ``masks`` is
    value-less (one entry per masked dest). Reads via the UNBOUND ``dict`` protocol
    (S3).
    """
    collected: list[tuple[tuple[int, str, str], CategoryEntry]] = []
    scope_order = {"system": 0, "agent": 1, "workset": 2, "box": 3}
    roots = scope_roots or {}

    def _box_dest(raw: str) -> str:
        return expand_expr(raw, space="guest", ctx=box_ctx, lookup=_no_lookup)

    def _root_join(group: str, host_src: str) -> str:
        # Replicate resolve_categories' root-join: a RELATIVE host_src under a
        # group with a root is prefixed with that (absolute) root; else as-is.
        root = roots.get(group)
        if root and not host_src.startswith("/"):
            return f"{root.rstrip('/')}/{host_src}"
        return host_src

    for scope in _SCOPES:
        if scope == "agent":
            # §2d active-over-default: effective agent node = default overlaid by
            # active. The emitted scope/group are the BARE ``agent`` token.
            scope_node = _effective_agent_node(snapshot, active_agent)
        else:
            scope_node = dict.get(snapshot, scope, _MISSING)
        if not isinstance(scope_node, KeyStore):
            continue
        order = scope_order[scope]
        _emit_scope_node(
            collected, scope_node, order=order, scope=scope,
            box_dest_fn=_box_dest, root_join_fn=_root_join,
        )

    collected.sort(key=lambda pair: pair[0])
    return [entry for _, entry in collected]


def _effective_agent_node(snapshot: KeyStore, active_agent: str) -> KeyStore:
    """The effective AGENT-scope category node = ``agent.default`` overlaid by
    ``agent.<active_agent>`` (the §2d L368 active-over-default pick, delivery side).

    Returns a FRESH ``KeyStore`` shaped like a single (bare) agent scope node —
    its ``bindings.{ro,rw}`` / ``caches`` / ``seeded`` / ``shared`` / ``synced`` /
    ``masks`` / ``env`` subtrees holding the per-name winner: the active slot's
    leaf wherever it set that name, else the ``agent.default`` leaf. The overlay is
    PER NAME (deep) so an active ``agent.<active>.shared.cache`` and a default-only
    ``agent.default.shared.plugins`` BOTH survive (active does not clobber a sibling
    default leaf). A present-``None`` reset was already OMITted by the merge (§3 /
    §6e), so it never reaches here; the active slot simply lacks that name and the
    default (if any) shows through — matching the snapshot's resolved state.

    Reads via the UNBOUND ``dict`` protocol (S3); never mutates the snapshot.
    """
    agent_node = dict.get(snapshot, "agent", _MISSING)
    if not isinstance(agent_node, KeyStore):
        return KeyStore()
    default_node = dict.get(agent_node, "default", _MISSING)
    active_node = dict.get(agent_node, active_agent, _MISSING)
    out = KeyStore()
    if isinstance(default_node, KeyStore):
        _overlay_into(out, default_node)
    if isinstance(active_node, KeyStore):
        _overlay_into(out, active_node)
    return out


def _overlay_into(base: KeyStore, top: KeyStore) -> None:
    """Deep-overlay *top*'s leaves onto *base*, in place (per-name, S3).

    Matching :class:`KeyStore` subtrees recurse (so a deep ``top`` leaf overlays the
    same deep ``base`` leaf without clobbering a sibling ``base`` leaf); any other
    ``top`` leaf replaces ``base``'s same key wholesale (the active slot wins that
    name). Builds into a fresh tree — never aliases the snapshot.
    """
    for key in dict.keys(top):
        top_val = dict.__getitem__(top, key)
        base_val = dict.get(base, key, _MISSING)
        if isinstance(top_val, KeyStore) and isinstance(base_val, KeyStore):
            _overlay_into(base_val, top_val)
        elif isinstance(top_val, KeyStore):
            fresh = KeyStore()
            _overlay_into(fresh, top_val)
            base[key] = fresh
        else:
            base[key] = top_val


def _emit_scope_node(
    collected: list[tuple[tuple[int, str, str], CategoryEntry]],
    scope_node: KeyStore,
    *,
    order: int,
    scope: str,
    box_dest_fn,
    root_join_fn,
) -> None:
    """Emit every category entry under ONE (bare) scope NODE.

    *scope_node* is a single scope's category subtree (``snapshot.<scope>`` for a
    non-agent scope; the effective agent node for the agent scope). *scope* is the
    BARE scope token used for the emitted ``CategoryEntry.scope`` and the
    ``scope_roots`` group prefix (``agent.<category>``) — the load-bearing scope
    identity, NOT the snapshot's agent discriminator. Reads via unbound ``dict``
    ops (S3).
    """
    # bindings.{ro,rw}
    bindings = dict.get(scope_node, "bindings", _MISSING)
    if isinstance(bindings, KeyStore):
        for mode in ("ro", "rw"):
            mode_node = dict.get(bindings, mode, _MISSING)
            if not isinstance(mode_node, KeyStore):
                continue
            category = f"bindings.{mode}"
            group = f"{scope}.{category}"
            for name in dict.keys(mode_node):
                bind = dict.__getitem__(mode_node, name)
                _emit_bind(
                    collected, order, scope, category, name, bind,
                    box_dest_fn, root_join_fn, group,
                )

    # caches / seeded / shared / synced
    for category in _BIND_LEAF_CATEGORIES:
        cat_node = dict.get(scope_node, category, _MISSING)
        if not isinstance(cat_node, KeyStore):
            continue
        group = f"{scope}.{category}"
        for name in dict.keys(cat_node):
            bind = dict.__getitem__(cat_node, name)
            _emit_bind(
                collected, order, scope, category, name, bind,
                box_dest_fn, root_join_fn, group,
            )

    # masks — a keyed dict[box_dest → bool] (present-None unmasks were dropped
    # at build, §6f); each surviving key is a masked dest.
    masks = dict.get(scope_node, "masks", _MISSING)
    if isinstance(masks, KeyStore):
        for raw_dest in dict.keys(masks):
            box_dest = box_dest_fn(raw_dest)
            sort_key = (order, "masks", box_dest)
            collected.append((
                sort_key,
                CategoryEntry(
                    category="masks",
                    scope=scope,
                    box_dest=box_dest,
                    host_src=None,
                    delivery="MOUNT",
                    options="ro",
                    name=box_dest,
                ),
            ))

    # env — scalar VAR → value.
    env = dict.get(scope_node, "env", _MISSING)
    if isinstance(env, KeyStore):
        for var in dict.keys(env):
            value = dict.__getitem__(env, var)
            if value is None:
                continue  # a reset env var has no value to export.
            sort_key = (order, "env", var)
            collected.append((
                sort_key,
                CategoryEntry(
                    category="env",
                    scope=scope,
                    box_dest=var,
                    host_src=None,
                    delivery="ENV",
                    options=value if isinstance(value, str) else str(value),
                    name=var,
                ),
            ))


def _emit_bind(
    collected: list[tuple[tuple[int, str, str], CategoryEntry]],
    order: int,
    scope: str,
    category: str,
    name: str,
    bind: object,
    box_dest_fn,
    root_join_fn,
    group: str,
) -> None:
    """Append one bind-shaped :class:`CategoryEntry` (MOUNT or COPY) for *bind*.

    *bind* is the expanded :class:`Bind` leaf (host already resolved). A
    present-``None`` / mistyped leaf cannot reach here — the merge OMITs a
    present-None bind (§3/§6e) and the views' S22 contract holds — so a non-Bind
    leaf is a build-invariant breach; raise loudly (never type-launder).
    *root_join_fn* prefixes a RELATIVE host_src with *group*'s scope-root
    (replicating ``resolve_categories``); *box_dest_fn* resolves box-side.
    """
    if not isinstance(bind, Bind):
        raise SettingsError(
            f"category {scope}.{category}.{name} is {type(bind).__name__}, "
            f"expected a Bind (present-None binds are omitted at build, §3/§6e)"
        )
    delivery = _DELIVERY[category]
    host_src = root_join_fn(group, bind.host)
    box_dest = box_dest_fn(bind.box)
    if delivery == "MOUNT":
        # opts: the per-entry override (bind.opts) wins; else the category default.
        # For an agent DELIVERY bind this matches OLD descriptor_mounts EXACTLY for
        # an ro bind (opts "ro" → "ro"). ⚑ LATENT EDGE (unreachable for shipped
        # agents — every descriptor binding is ro): an rw descriptor binding would
        # get the category default ``Z,U`` here vs descriptor_mounts' ``""`` — a
        # benign relabel-add, but flagged. No shipped plugin declares an rw bind.
        options = bind.opts if bind.opts is not None else _bind_options(category)
    else:
        options = ""
    sort_key = (order, category, name)
    collected.append((
        sort_key,
        CategoryEntry(
            category=category,
            scope=scope,
            box_dest=box_dest,
            host_src=host_src,
            delivery=delivery,
            options=options,
            name=name,
        ),
    ))


def _no_lookup(ref: str, chain: tuple[str, ...]) -> str:
    """``expand_expr`` lookup for the box-side box_dest pass: the snapshot's
    ``@``-refs are ALREADY resolved at build (host-side), so a surviving ``@``-ref
    in a box_dest is a build/config error — raise rather than silently emit ``""``.
    """
    raise SettingsError(
        f"unexpected unresolved @-reference in a box_dest: {ref!r} "
        f"(box_dest @-refs are resolved at build; only $XDG/~ are deferred)"
    )

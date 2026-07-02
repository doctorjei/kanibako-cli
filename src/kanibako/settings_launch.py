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

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Mapping

from kanibako.settings_assemble import assemble_levels
from kanibako.settings_categories import (
    _DELIVERY,
    CategoryEntry,
    _bind_options,
)
from kanibako.settings_expand import expand
from kanibako.settings_merge import merge
from kanibako.settings_resolve import ResolveCtx, SettingsError, expand_expr
from kanibako.settings_store import _MISSING, SCOPE_CONTAINMENT, Bind, KeyStore

if TYPE_CHECKING:
    from kanibako.targets.base import Binding


# The category tokens that hold bind-shaped (``Bind``) leaves in the snapshot's
# ``<scope>.<category>`` subtrees. ``bindings`` carries ``ro`` / ``rw`` sub-nodes;
# the rest are flat ``<category>.<name>`` bind maps.
_BIND_LEAF_CATEGORIES: frozenset[str] = frozenset(
    {"caches", "seeded", "shared", "synced"}
)
# Aliases the single-source scope-containment tuple (settings_store) so this
# consumer never re-declares the scope set — the old byte-identical literal was a
# drift foot-gun. Order is NOT load-bearing here: the L1334 emit loop re-sorts by
# its own ``scope_order`` map, so the containment order is safe to reuse verbatim.
_SCOPES: tuple[str, ...] = SCOPE_CONTAINMENT


# --------------------------------------------------------------------------- #
# Snapshot build — the ONE resolve per launch                                 #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Auth 3-tier SHARING chain (spec §2a/§2b/§2c/§2d — 2026-07-01 redesign)        #
# --------------------------------------------------------------------------- #
#
# REPLACES the boolean group_auth chain with a global/workset/box SHARING model
# that COMPOSES: a box can be global-shared AND/OR workset-shared. The keys are
# injected into the launch snapshot floor so ``expand`` resolves the @-ref chain
# ONCE (single-route — NO second resolver). A settable scope FILE may still
# override a settable key by name (the floor sits under ``base``); the ``meta.*``
# capability keys are RO / construct-set (a scope file cannot fake them).
#
# The FINAL KEY MODEL (design FINAL — the authority):
#   meta.agent.<agent>.auth.share_support   <plugin-set>   shared creds supported?
#   system.auth.share_allowed             = true           global share allowed?
#   workset.auth.share_allowed            = @system.auth.share_allowed
#   workset.auth.global_sync              = @system.auth.share_allowed
#   meta.box.agent.auth.share_support     = @meta.agent.<@box.agent_name>.auth.share_support
#                                           (mirror, materialized when box.agent_name set)
#   box.auth.global_enabled  = %@meta.box.agent.auth.share_support && @system.auth.share_allowed%
#   box.auth.workset_enabled = %@meta.box.agent.auth.share_support && @workset.auth.share_allowed%
#   workset.auth.path        = @meta.workset.path/auth        (workset auth dir, per workset)
#   box.auth.workset_path    = @workset.auth.path/@box.agent_name  (this box's per-agent source root)
#
# STORES: GLOBAL = host home (host_rel, NOT managed) · WORKSET = @workset.auth.path/<agent>/
# (layout MIRRORS the in-guest mount = home_rel) · BOX = private (no source).
# Precedence: workset > global.
#
# ‼ ENABLE COMPUTATION (impl note): the spec writes the two box enables as
# ``%@support && @allow%`` expressions, but the launch ``expand`` engine resolves
# ONLY @-refs / $VAR / ~ — it does NOT evaluate ``&&`` boolean expressions (the
# spec's ``%…%`` conditionals are computed in Python today, cf. the images bind in
# core_defaults). So the floor materializes the settable box ENABLE as a plain
# per-tier bool DEFAULT (``True``, the box's own volitional knob a box may override
# to ``false`` to opt out of a tier) and the resolvable INPUTS (the capability
# mirror + the system/workset allow flags); :func:`resolve_auth_source` then ANDs
# support && allow && box_enable in PYTHON — exactly as the old effective_group_auth
# did ``available AND on``. The box's ``*_enabled`` key thus IS its opt-out knob;
# the composed gate is the Python AND. (Folding the ``&&`` into the expand engine is
# a deferred generalization — flagged for the Editor.)
#
# STANDALONE (degenerate lone box, spec §2c): no workset group → workset_enabled
# degenerates false (the workset keys pin to the LITERAL False, so the Python AND
# for the workset tier is false regardless of the box knob), but global_enabled =
# support && system.auth.share_allowed && box_knob STILL applies — a standalone box
# CAN use global/host creds (deliberate change, IMPL-arc noted).

#: The GLOBAL-share gate default (spec §2g / §2b). The single host-wide allow flag.
_SYSTEM_SHARE_ALLOWED_KEY = "system.auth.share_allowed"


def auth_chain_floor(
    *,
    mode: str,
    agent_name: str,
) -> dict[str, object]:
    """Build the auth 3-tier SHARING chain floor keys for *mode*.

    Returns the ``{dotted_key: value}`` floor fragment for the spec's auth.*
    sharing chain (spec §2a/§2b/§2c/§2d; design FINAL KEY MODEL). The caller folds
    it into ``build_launch_snapshot``'s floor so ``expand`` resolves the @-ref
    chain ONCE (single-route). *mode* is the box's
    :class:`~kanibako.paths.BoxMode` value (``"primary"`` / ``"named"`` /
    ``"standalone"``), passed as a plain string to avoid a paths import.

    The ``meta.agent.<agent>.auth.share_support`` CAPABILITY is set by the PLUGIN
    (``*-defaults.yaml``) — NOT here (it rides the meta identity floor). This floor
    materializes the ``meta.box.agent.auth.share_support`` MIRROR
    (=``@meta.agent.<agent>.auth.share_support``, the 29g box.agent mirror pattern
    made concrete for the @-ref's literal-path resolution), the system/workset
    allow knobs, the two settable box ENABLE knobs (per-tier opt-out defaults), and
    the workset store path anchors. :func:`resolve_auth_source` computes the
    effective enable = ``support && allow && knob`` in Python (see the module note).

    PRIMARY / NAMED use the @-ref forms. STANDALONE pins the two workset keys
    (``workset.auth.share_allowed`` / ``workset.auth.global_sync``) to the LITERAL
    ``False`` so the workset tier degenerates false without a workset group;
    ``box.auth.global_enabled`` still derives from the global gate (a standalone box
    CAN use global/host creds).
    """
    floor: dict[str, object] = {
        # The GLOBAL-share gate — the single host-wide allow flag (settable).
        _SYSTEM_SHARE_ALLOWED_KEY: True,
        # The box-scoped MIRROR of the active agent's capability (RO / meta). The
        # 29g box.agent mirror pattern: rather than re-do the nested dynamic lookup
        # ``@meta.agent.<@box.agent_name>.auth.share_support`` at every reference,
        # materialize a box-scoped @-ref to the ACTIVE agent's capability slot,
        # which ``expand`` follows by literal path. The plugin/agent capability
        # ``meta.agent.<agent>.auth.share_support`` rides the meta identity floor.
        "meta.box.agent.auth.share_support": (
            f"@meta.agent.{agent_name}.auth.share_support"
        ),
        # The two INDEPENDENT box ENABLE knobs (COMPOSE — a box can be global-
        # AND/OR workset-shared). Settable per-tier opt-out defaults (True = opt
        # in). resolve_auth_source ANDs each with the mirrored capability + the
        # relevant allow flag (support && allow && knob) — the Python AND that
        # stands in for the spec's ``%… && …%`` (see module note).
        "box.auth.global_enabled": True,
        "box.auth.workset_enabled": True,
        # This box's per-agent WORKSET source root (@workset.auth.path/<agent>).
        # The workset auth dir mirrors the in-guest layout (home_rel); this is the
        # box's own root within it, keyed by the active agent name.
        "box.auth.workset_path": f"@workset.auth.path/{agent_name}",
    }
    if mode == "standalone":
        # STANDALONE: a lone box has no workset group → the workset allow keys are
        # the LITERAL False, so the workset tier's Python AND is false regardless
        # of the box knob. global still derives from the global gate (standalone
        # CAN use global/host creds).
        floor["workset.auth.share_allowed"] = False
        floor["workset.auth.global_sync"] = False
        # No workset store for a lone box — the workset auth dir path anchor is
        # absent (present-None), and the box's per-agent source root is pinned None
        # too (defensive root-cause fix): otherwise ``box.auth.workset_path`` =
        # ``@workset.auth.path/<agent>`` would resolve against the absent
        # ``workset.auth.path`` and expand to the literal ``/<agent>`` (an @-ref to
        # an absent key renders ``""``, not a drop) — garbage the credsync
        # dir-creation would mkdir against the host ROOT. The workset enable is
        # false anyway, so this source is never consulted; pinning None makes that
        # explicit at the floor, belt-and-braces with the resolver's scrub.
        floor["workset.auth.path"] = None
        floor["box.auth.workset_path"] = None
    else:
        # PRIMARY / NAMED (ALL WORKSETS): workset allow defaults to the system
        # gate; the workset dir syncs UP to global by default.
        floor["workset.auth.share_allowed"] = "@system.auth.share_allowed"
        floor["workset.auth.global_sync"] = "@system.auth.share_allowed"
        # The workset auth dir (one per workset) — a sibling to boxes/vault/logs
        # off the workset root, mirroring the in-guest mount layout underneath.
        floor["workset.auth.path"] = "@meta.workset.path/auth"
    return floor


# --------------------------------------------------------------------------- #
# meta.runtime.* materialization (block B1 — spec §1A L230-241, 2026-06-29h)   #
# --------------------------------------------------------------------------- #
#
# The spec's RUNTIME-RESOLVED identity anchors (spec §1A L230-241; §0 meta.* is a
# TOP-LEVEL protected RO group). The per-mode treewalk values are ALREADY computed
# today (``proj.mode`` / ``proj.group.root`` / the resolved project dir); this
# surfaces them as REAL ``@``-referenceable keys via the SAME floor-injection
# pattern block #2 uses for the group-auth chain — they are injected into the
# launch snapshot floor so ``expand`` resolves the @-ref chain ONCE (single-route,
# NO second resolver). They are ``meta.*`` keys (NOT ``config.*``), so they ride
# the FLOOR alongside ``system.*`` / the group-auth chain.
#
# The keys (spec §1A L230-241):
#   meta.runtime.ws_root      | primary    = "@config.primary_workset"  (@-ref → #3a foundation)
#                             | named      = str(proj.group.root)        (resolved literal)
#                             | standalone = str(proj.metadata_path)     (the project ROOT <root>;
#                                            B2b fixed this from the B1 <root>/workspace defect)
#   meta.runtime.ws_settings  | primary/named = "@meta.runtime.ws_root/settings.yaml" (@-ref)
#                             | standalone    = None                      (whole-value None terminal)
#   meta.runtime.project_type | proj.mode.value  ("primary"|"named"|"standalone")
#
# Then the SINGLE-SOURCE re-root (spec §1A L239-241; §2c L397/406/414/432):
#   meta.workset.path     = "@meta.runtime.ws_root"        (UNIFORM all modes)
#   meta.workset.settings = "@meta.runtime.ws_settings"
#   meta.box.mode         = "@meta.runtime.project_type"   (RO identity anchor; spec §2b L486)
#
# These resolve transitively in the ONE expand pass (e.g. primary:
# meta.workset.path → @meta.runtime.ws_root → @config.primary_workset → foundation;
# standalone: meta.workset.settings → @meta.runtime.ws_settings → None terminal).
#
# This block is ADDITIVE (B1): the keys appear in the snapshot but NO consumer
# reads them yet (binds move to @meta.* in a later block). The only behavioral
# change is meta.box.mode (an RO identity anchor replacing the formerly settable
# ``box.mode`` config-set key — dropped in config_interface this block).


def meta_runtime_floor(
    *,
    mode: str,
    ws_root_literal: str | None = None,
) -> dict[str, object]:
    """Build the ``meta.runtime.*`` + re-rooted ``meta.*`` floor keys (block B1).

    Returns the ``{dotted_key: value}`` floor fragment for the spec's runtime
    identity anchors (spec §1A L230-241; §0 meta-RO). The caller folds it into
    ``build_launch_snapshot``'s floor so ``expand`` resolves the @-ref chain ONCE
    (single-route — NO second resolver). *mode* is the box's
    :class:`~kanibako.paths.BoxMode` value (``"primary"`` / ``"named"`` /
    ``"standalone"``), passed as a plain string to avoid a paths import.

    *ws_root_literal* is the resolved workset-root path STRING for the NAMED and
    STANDALONE modes (``str(proj.group.root)`` / ``str(project dir)`` — a runtime
    treewalk result, no key form, JC-B1-2: an in-memory floor literal, NOT a
    file value, so §0's unresolved-FILES rule does not apply). It MUST be given
    for ``named`` / ``standalone`` and is IGNORED for ``primary`` (which uses the
    ``@config.primary_workset`` @-ref so the value live-propagates from the
    Layer-1 foundation, spec §1A L233).

    The re-rooted keys (``meta.workset.path`` / ``meta.workset.settings`` /
    ``meta.box.mode``) are UNIFORM across modes — each is the SAME @-ref into
    ``meta.runtime.*`` (the single-source, spec §1A L239-241). A scope FILE cannot
    set them (they are construct-set RO per §0 — and ``meta.*`` is not in the
    config-set settable known-key list); the floor is their sole source here.
    """
    floor: dict[str, object] = {}

    # meta.runtime.project_type — the resolved mode token (spec §1A L237).
    floor["meta.runtime.project_type"] = mode

    # meta.runtime.ws_root (spec §1A L233):
    #   primary    → the @config.primary_workset @-ref STRING (foundation, #3a);
    #   named      → the detected workset root literal;
    #   standalone → the runtime project dir literal.
    if mode == "primary":
        floor["meta.runtime.ws_root"] = "@config.primary_workset"
    else:
        if ws_root_literal is None:
            raise SettingsError(
                f"meta_runtime_floor: ws_root_literal is required for mode "
                f"{mode!r} (only 'primary' uses the @config.primary_workset @-ref)"
            )
        floor["meta.runtime.ws_root"] = ws_root_literal

    # meta.runtime.ws_settings (spec §1A L235-236):
    #   primary/named → @meta.runtime.ws_root/settings.yaml (embedded @-ref);
    #   standalone    → None (a whole-value None terminal — spec §2c L415).
    if mode == "standalone":
        floor["meta.runtime.ws_settings"] = None
    else:
        floor["meta.runtime.ws_settings"] = "@meta.runtime.ws_root/settings.yaml"

    # Single-source re-root (spec §1A L239-241; §2c) — UNIFORM all modes.
    floor["meta.workset.path"] = "@meta.runtime.ws_root"
    floor["meta.workset.settings"] = "@meta.runtime.ws_settings"
    # meta.box.mode — the RO identity anchor surfacing the runtime mode (spec §2b
    # L486; was the settable box.mode config-set key, dropped this block).
    floor["meta.box.mode"] = "@meta.runtime.project_type"

    return floor


# --------------------------------------------------------------------------- #
# meta.* IDENTITY-ANCHOR materialization (block B2 — spec §2c/§2d, §0)          #
# --------------------------------------------------------------------------- #
#
# B1 materialized meta.runtime.* + the single-source re-root of meta.workset.path
# / meta.workset.settings / meta.box.mode (and block #2 added meta.box/workset.
# group_auth_available). B2 materializes the REMAINING construct-time IDENTITY
# anchors as RO floor keys and ROUTES the eligible core binds through @meta.* refs
# so the bind host_src RESOLVES via the snapshot instead of being injected as a
# proj-attr literal at the assembly seam (the single-route payoff, spec §0).
#
# ⚑ EQUIVALENCE IS THE BAR (JC-B2-4). Each materialized identity key is the
# RESOLVED LITERAL the launch already computes today (``str(proj.project_path)``,
# the channel partition addresses, the plugin agent name, …) — NOT a re-derivation
# via the spec's nested @workset.* chain. Holding the resolved literal guarantees
# the @meta.*-routed bind expands to the byte-identical host_src the proj-attr
# injection produced. This mirrors B1, where meta.runtime.ws_root for named/
# standalone is the ``str(proj.group.root)`` / project-dir LITERAL (JC-B1-2: an
# in-memory floor literal, NOT a file value — §0's unresolved-FILES rule does not
# apply). They are ``meta.*`` keys (construct-set RO, §0) — NO scope FILE may
# override them (meta.* is not in the config-set settable known-key list).
#
# The keys (spec §2c/§2d):
#   meta.box.name           | the box name (proj.name; primary/named=box name,
#                             standalone=<random24>_%leaf% — already computed and
#                             carried on proj.name, JC-B2-2: reuse, do not regen)
#   meta.box.workspace      | the resolved in-box workspace SOURCE (str(proj.
#                             project_path)) — routed to box.bindings.rw.workspace
#   meta.box.inbox          | this box's own mailbox dir (str(addr.inbox)) —
#                             routed to box.bindings.rw.inbox
#   meta.box.share_global   | this box's system-scope share dir (str(addr.share_global))
#   meta.box.share_workset  | this box's workset-local share dir (str | None standalone)
#   meta.workset.name       | __PRIMARY__ | <named> | __STANDALONE__ (the partition token)
#   meta.agent.<a>.name     | the plugin-set agent name (REQUIRED when an agent exists)
#
# meta.box.{settings,workspace(named),container_name,helper_num} per the spec are
# either deeper @workset.*-chained values (settings) or non-bind RENDER targets
# (container_name from name+helper_num); B2 materializes the IDENTITY leaves the
# eligible BINDS reference + meta.workset.name + the agent name. The container_name
# / helper_num RENDER and the home/vault binds stay on attrs / @workset.* (JC-B2-3
# / JC-B2-4 — see the return docstring), a tracked follow-up.


def meta_identity_floor(
    *,
    box_name: str,
    project_path: str,
    inbox: str,
    share_global: str,
    share_workset: str | None,
    workset_name: str,
    agent_name: str | None = None,
    agent_real_name: str | None = None,
    agent_auth_share_support: bool = False,
) -> dict[str, object]:
    """Build the construct-time ``meta.*`` IDENTITY-anchor floor keys (block B2).

    Returns the ``{dotted_key: value}`` floor fragment for the spec's remaining
    construct-time identity anchors (spec §2c/§2d; §0 meta-RO). The caller folds it
    into ``build_launch_snapshot``'s floor so ``expand`` resolves the @meta.* binds
    ONCE (single-route — NO second resolver), exactly like
    :func:`meta_runtime_floor` / :func:`auth_chain_floor`.

    Every value is the RESOLVED LITERAL the launch already computes (the box name
    on ``proj.name``, the workspace source ``str(proj.project_path)``, the channel
    partition addresses from :func:`kanibako.channels.box_channel_addresses`, the
    plugin-set agent name) — so a bind re-pointed to ``@meta.box.workspace`` /
    ``@meta.box.inbox`` expands to the byte-identical host_src the old proj-attr
    injection produced (JC-B2-4 equivalence bar).

    *share_workset* is ``None`` for STANDALONE (no workset-local channels, spec
    §2c L469) → materialized as a whole-value ``None`` terminal (the key is PRESENT
    with value ``None``, matching ``meta.runtime.ws_settings`` for standalone).

    *agent_name* / *agent_real_name*: when an agent exists, ``meta.agent.<a>.name``
    is the plugin-set agent name (spec §2d L514, REQUIRED). ``agent_name`` is the
    cascade discriminator (``install.name``); ``agent_real_name`` is the value
    (the plugin's ``meta.agent.<agent>.name`` — normally the same string). Both
    ``None`` for a NO-AGENT box (skips the agent identity key).
    """
    floor: dict[str, object] = {
        # Box identity (spec §2c). The box name is carried on ``proj.name``
        # (JC-B2-2: reuse — standalone's <random24>_%leaf% is generated at
        # creation and stored on proj.name; B2 does NOT regenerate it).
        "meta.box.name": box_name,
        # The in-box workspace SOURCE literal (routed to box.bindings.rw.workspace).
        "meta.box.workspace": project_path,
        # This box's own channel partition addresses (routed to box.bindings.rw.inbox;
        # share_global / share_workset are materialized identity anchors for parity
        # and future routing — share_workset is None for standalone).
        "meta.box.inbox": inbox,
        "meta.box.share_global": share_global,
        "meta.box.share_workset": share_workset,
        # The workset partition token (spec §2c — __PRIMARY__ | <named> |
        # __STANDALONE__).
        "meta.workset.name": workset_name,
    }
    # The agent identity key (spec §2d L514) — REQUIRED when an agent exists, under
    # the agent's discriminated slot. A NO-AGENT box omits it.
    if agent_name is not None:
        floor[f"meta.agent.{agent_name}.name"] = (
            agent_real_name if agent_real_name is not None else agent_name
        )
        # The agent's credential-SHARING CAPABILITY (spec §2d; design step 2):
        # plugin-set, RO — the hard floor a user can't fake. The auth chain's
        # meta.box.agent.auth.share_support mirror views UP to this key, so it must
        # be present in the snapshot whenever an agent exists. A NO-AGENT box omits
        # it (no agent capability to mirror → the mirror @-ref resolves to <None>
        # and the box enables degenerate false).
        floor[f"meta.agent.{agent_name}.auth.share_support"] = bool(
            agent_auth_share_support
        )
    return floor


# --------------------------------------------------------------------------- #
# WORKSET path-anchor materialization (block B2b — spec §2c §1, §2g)           #
# --------------------------------------------------------------------------- #
#
# B2b completes the single-route: it materializes the workset-scope PATH anchors
# the spec's §2c per-mode binds reference (workset.{boxes,vault_ro,vault_rw,logs}
# + the workset-local channels), plus meta.box.helper_log, as REAL @-referenceable
# floor keys. The core home/vault/helper_log binds then route through these anchors
# (@workset.boxes/@meta.box.name/home, …) so the bind host_src RESOLVES via the
# snapshot instead of a proj-attr literal injected at the seam (the payoff).
#
# JC-B2b-1: these workset.* keys do NOT exist as resolvable snapshot keys today —
# resolve_system_paths derives only the PRIMARY pseudo-keys (system._boxes /
# system._primary_vault_* / system._primary_logs) into StandardPaths; there is no
# workset.* tier in the snapshot. B2b MATERIALIZES them here.
#
# ⚑ EQUIVALENCE IS THE BAR. Every value is the RESOLVED LITERAL the launch already
# computes — derived DIRECTLY off the ProjectPaths the seam holds (the box-home
# parent dir, the vault parent dirs, the logs dir) — so an @workset.*-routed bind
# expands BYTE-IDENTICALLY to the proj-attr host_src it replaces, regardless of the
# per-mode path-helper internals. PRIMARY/NAMED root under @meta.workset.path
# (spec §2c ALL WORKSETS); STANDALONE's workset path anchors are <None> (spec §2c
# L416) — its home/vault route through @meta.workset.path/box_data|vault/* directly
# (the core-defaults `mode_meta_ref` standalone arm), now byte-identical because the
# B2b ws_root fix made meta.workset.path = the project ROOT (<root>).


def workset_anchor_floor(
    *,
    mode: str,
    boxes: str | None,
    vault_ro: str | None,
    vault_rw: str | None,
    logs: str | None,
    helper_log: str,
    workset_channels: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build the workset PATH-anchor floor keys (block B2b — spec §2c/§2g).

    Returns the ``{dotted_key: value}`` floor fragment for the spec's workset-scope
    path anchors the @-ref-routed core binds reference. Folded into
    ``build_launch_snapshot``'s floor (like :func:`meta_identity_floor`) so
    ``expand`` resolves the @workset.* binds ONCE (single-route).

    PRIMARY/NAMED (spec §2c ALL WORKSETS L436-439): ``workset.{boxes,vault_ro,
    vault_rw,logs}`` are the RESOLVED LITERAL roots the launch computes
    (``proj.shell_path``'s box-parent, the vault parent dirs, the logs dir) — so a
    bind re-pointed to ``@workset.boxes/@meta.box.name/home`` expands byte-identically
    to the old ``str(proj.shell_path)`` injection.

    STANDALONE (spec §2c L416): the workset path anchors are ``<None>`` — its home/
    vault route through the TRUE spec ``@meta.workset.path/{box_data/home,vault/ro,
    vault/rw}`` chains directly (the core-defaults standalone ``mode_meta_ref`` arm).
    These resolve byte-identically because the B2b fix made the standalone
    ``meta.runtime.ws_root`` (→ ``meta.workset.path``) the project ROOT (``<root>``,
    via ``str(proj.metadata_path)``), so ``@meta.workset.path/box_data/home`` =
    ``<root>/box_data/home`` = ``proj.shell_path``. So these keys carry ``None`` and
    are not referenced — no invented resolved-literal anchor is needed.

    ``meta.box.helper_log`` is materialized in EVERY mode to the resolved helper-log
    path (= ``str(helper_log_path(std, proj))``) so the helper_log bind routes a
    SINGLE whole-value @-ref (the spec's ``@workset.logs/@meta.box.name.jsonl``
    suffix-after-ref form is not expand-parseable — the greedy ref regex would
    swallow ``.jsonl`` into the key name; this is independent of ws_root).

    *workset_channels* (PRIMARY/NAMED only) maps ``commons``/``chat``/``share`` to
    the resolved workset-local channel roots (= ``workset_channel_paths(proj, std)``),
    materialized as ``workset.channels.*`` so the workset-channel binds (spec §2c
    L452-454) route through them. ``None`` for STANDALONE (no workset channels).
    """
    floor: dict[str, object] = {
        # The workset path anchors (spec §2c ALL WORKSETS L436-439). None for
        # STANDALONE (spec §2c L416) — present-None whole-value terminals.
        "workset.boxes": boxes,
        "workset.vault_ro": vault_ro,
        "workset.vault_rw": vault_rw,
        "workset.logs": logs,
        # The resolved helper-log path anchor (every mode) — the single-route
        # target for the helper_log bind (see the docstring's parse-limitation note).
        "meta.box.helper_log": helper_log,
    }
    if workset_channels is not None:
        for leaf, path in workset_channels.items():
            floor[f"workset.channels.{leaf}"] = path
    return floor


#: The auth SHARING tier a box resolves to (design §3, precedence workset>global).
AuthTier = Literal["workset", "global", "box"]


@dataclass(frozen=True)
class AuthSource:
    """The resolved credential-SHARING decision for one box (spec §2b; design §3).

    Replaces the single ``effective_group_auth`` bool. Carries the per-box tier
    the credsync engine syncs against, the two box enables (for diagnostics /
    display), and the workset↔global up-sync flag. The two enables COMPOSE — a
    box can be global-shared AND/OR workset-shared — but the SELECTED source obeys
    precedence workset>global (design PRECEDENCE): when the workset tier is enabled
    AND its store is present, WORKSET wins; else global (if enabled); else private.

    * *tier* — the SELECTED source tier: ``"workset"`` / ``"global"`` / ``"box"``
      (``"box"`` = private, no source — today's distinct-auth). The credsync gate
      keys off this: ``box`` drops synced / credential-seeded deliveries.
    * *global_enabled* / *workset_enabled* — the resolved box enables (both may be
      true; the enables are what COMPOSE, the *tier* is the precedence winner).
    * *global_sync* — the workset auth dir syncs UP to global (design SYNC): when
      true and the box syncs the WORKSET tier, the workset store is first refreshed
      from / written back to global (the uniform primitive at the second level).
    * *workset_source* — the resolved workset per-agent source root
      (``box.auth.workset_path``), or ``None`` for standalone / when absent. The
      GLOBAL source is the host home (``host_rel``), not carried here (implicit).
    """

    tier: AuthTier
    global_enabled: bool
    workset_enabled: bool
    global_sync: bool
    workset_source: str | None

    @property
    def shares(self) -> bool:
        """True when the box shares creds at ANY tier (not private/box).

        The single-bool analog of the old ``effective_group_auth`` for the gates
        that only care "is this box sharing at all" (auto-auth, the host-source
        credsync hops, the reconcile drop). ``False`` ≡ the old distinct-auth.
        """
        return self.tier != "box"


def resolve_auth_source(
    snapshot: KeyStore, *, mode: str | None = None
) -> AuthSource:
    """Resolve the box's credential-SHARING SOURCE off the expanded snapshot.

    Reads the auth 3-tier chain (spec §2b; design §3) from the ONE expanded
    snapshot and returns the :class:`AuthSource` the credsync engine consumes.
    Computes each tier's EFFECTIVE enable in Python (the spec's ``%support && allow
    && knob%`` — the expand engine does not evaluate ``&&``, see the module note):

    * GLOBAL:  ``meta.box.agent.auth.share_support && system.auth.share_allowed &&
      box.auth.global_enabled``
    * WORKSET: ``meta.box.agent.auth.share_support && workset.auth.share_allowed &&
      box.auth.workset_enabled`` (AND a present ``box.auth.workset_path`` store)

    Selection (design PRECEDENCE workset>global):

    * workset ENABLED (+ store present) → tier ``"workset"`` (the more specific);
    * else global ENABLED → tier ``"global"`` (host home source);
    * else tier ``"box"`` (private, no source — distinct auth).

    Each input is resolved to a real ``bool`` terminal by ``expand``;
    :func:`as_bool` does not launder. ``box.auth.workset_path`` is a resolved string
    (or ``None`` for standalone). ``workset.auth.global_sync`` is the workset↔global
    up-sync flag.

    An absent ``box`` node means the floor was not injected → fail CLOSED (tier
    ``"box"``, no sharing) rather than launder.
    """
    from kanibako.settings_views import as_bool

    box_node = dict.get(snapshot, "box", _MISSING)
    if not isinstance(box_node, KeyStore):
        return AuthSource(
            tier="box",
            global_enabled=False,
            workset_enabled=False,
            global_sync=False,
            workset_source=None,
        )

    # The box-scoped capability mirror (RO): meta.box.agent.auth.share_support.
    meta_node = dict.get(snapshot, "meta", _MISSING)
    support = False
    if isinstance(meta_node, KeyStore):
        meta_box = dict.get(meta_node, "box", _MISSING)
        if isinstance(meta_box, KeyStore):
            meta_box_agent = dict.get(meta_box, "agent", _MISSING)
            if isinstance(meta_box_agent, KeyStore):
                mba_auth = dict.get(meta_box_agent, "auth", _MISSING)
                if isinstance(mba_auth, KeyStore):
                    support = as_bool(
                        dict.get(mba_auth, "share_support", False)
                    )

    # The system + workset allow flags.
    system_node = dict.get(snapshot, "system", _MISSING)
    system_allow = False
    if isinstance(system_node, KeyStore):
        sys_auth = dict.get(system_node, "auth", _MISSING)
        if isinstance(sys_auth, KeyStore):
            system_allow = as_bool(dict.get(sys_auth, "share_allowed", False))

    workset_node = dict.get(snapshot, "workset", _MISSING)
    workset_auth = (
        dict.get(workset_node, "auth", _MISSING)
        if isinstance(workset_node, KeyStore)
        else _MISSING
    )
    workset_allow = False
    global_sync = False
    if isinstance(workset_auth, KeyStore):
        workset_allow = as_bool(dict.get(workset_auth, "share_allowed", False))
        global_sync = as_bool(dict.get(workset_auth, "global_sync", False))

    # The two settable box ENABLE knobs + the workset source path.
    box_auth = dict.get(box_node, "auth", _MISSING)
    global_knob = True
    workset_knob = True
    workset_source: str | None = None
    if isinstance(box_auth, KeyStore):
        global_knob = as_bool(dict.get(box_auth, "global_enabled", True))
        workset_knob = as_bool(dict.get(box_auth, "workset_enabled", True))
        wp = dict.get(box_auth, "workset_path", _MISSING)
        if isinstance(wp, str) and wp:
            workset_source = wp

    # Effective enables (the Python AND standing in for the spec's %… && …%).
    global_enabled = bool(support and system_allow and global_knob)
    workset_enabled = bool(support and workset_allow and workset_knob)

    # Precedence workset>global: the workset tier wins when enabled AND its store
    # path is present (a lone/standalone box has no workset store → degenerate to
    # global/box, as distinct-auth did for the workset level).
    if workset_enabled and workset_source is not None:
        tier: AuthTier = "workset"
    elif global_enabled:
        tier = "global"
    else:
        tier = "box"

    # Null out the workset source UNLESS the workset tier was selected. Otherwise a
    # standalone/global/private box carries the resolved ``box.auth.workset_path``,
    # which — for standalone — is the GARBAGE ``@workset.auth.path/<agent>`` with
    # ``workset.auth.path=None``: expand renders an @-ref to an absent/None key as
    # ``""`` (NOT a drop), so it collapses to the literal ``/<agent>``. Leaving that
    # live makes the credsync dir-creation mkdir against the host ROOT. Only the
    # workset tier ever consults ``workset_source``, so scrub it for every other
    # tier — no ``/<agent>`` escapes onto the AuthSource.
    if tier != "workset":
        workset_source = None

    return AuthSource(
        tier=tier,
        global_enabled=global_enabled,
        workset_enabled=workset_enabled,
        global_sync=global_sync,
        workset_source=workset_source,
    )


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
    auth_chain: Mapping[str, object] | None = None,
    meta_runtime: Mapping[str, object] | None = None,
    meta_identity: Mapping[str, object] | None = None,
    workset_anchor: Mapping[str, object] | None = None,
) -> KeyStore:
    """Build the ONE expanded launch snapshot.

    Folds the behavior floor (mapped to ``agent.default.<key>`` — OS1, the
    all-agents backstop) and every runtime ``default_categories`` table (a bare
    ``agent.<cat>.*`` table key re-rooted to the active slot ``agent.<agent_name>.
    <cat>.*``) into ONE base-level floor, assembles the 6-level cascade (S8) with
    7a's *agent_partial* inserted as an additional agent-level source (S27), merges
    (S15), and expands (S17/S19) with *ctx*. There is NO bare ``agent.<key>`` in the
    snapshot (spec §2d / §0 L21) — the agent tier is DISCRIMINATED throughout.

    *behavior_floor* is the BARE behavior-default dict (``{d.key: d.default}``);
    *default_categories* are the already-scope-qualified category default tables
    (``{"box.bindings.rw.home": (h, d, o), ...}``) unioned across every mount
    family. *binding_overrides* are the transitional ``{binding_key: host_src}``
    repoints (bridge), placed via *descriptor_bindings* (each ``Binding`` supplies
    the ``ro`` flag selecting ``bindings.ro`` vs ``bindings.rw``).

    *auth_chain* is the auth 3-tier SHARING chain floor fragment built by
    :func:`auth_chain_floor` per box mode — the spec's @-ref / literal ``auth.*``
    chain keys (spec §2a/§2b/§2c/§2d; design FINAL KEY MODEL). Folded into the
    SAME floor so ``expand`` resolves the chain ONCE (single-route). ``None`` for a
    NARROW resolve that does not need the chain (the seed/synced/image/helper
    sub-resolves), so those snapshots simply lack the chain keys.

    *meta_runtime* is the runtime identity-anchor floor fragment (block B1) built
    by :func:`meta_runtime_floor` per box mode — the spec's ``meta.runtime.*`` keys
    + the single-source re-root of ``meta.workset.path`` / ``meta.workset.settings``
    / ``meta.box.mode`` (spec §1A L230-241). Folded into the SAME floor so ``expand``
    resolves the @-ref chain ONCE (single-route). ``None`` for a narrow resolve.

    *meta_identity* is the construct-time IDENTITY-anchor floor fragment (block B2)
    built by :func:`meta_identity_floor` — the remaining ``meta.box.*`` /
    ``meta.workset.name`` / ``meta.agent.<a>.name`` keys that the @meta.*-routed
    core binds (workspace / inbox) reference (spec §2c/§2d). Folded into the SAME
    floor so ``expand`` resolves the @meta.* binds ONCE (single-route). ``None`` for
    a narrow resolve.

    Returns the expanded ``snapshot``.
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

    # Auth 3-tier SHARING chain: the @-ref / literal ``auth.*`` chain keys (spec
    # §2a/§2b/§2c/§2d; design FINAL KEY MODEL) fold into the SAME floor so
    # ``expand`` resolves the chain ONCE (single-route). Built per mode by
    # :func:`auth_chain_floor`; a settable scope FILE still overrides a settable
    # key by name (the floor sits under ``base``); the ``meta.*`` capability keys
    # are RO. Injected AFTER the category tables so the dotted chain keys
    # (``box.auth.*`` / ``workset.auth.*`` / ``system.auth.*``) land in the floor
    # unconditionally.
    if auth_chain:
        for key, val in auth_chain.items():
            floor[key] = val

    # meta.runtime.* materialization (block B1): the runtime identity anchors +
    # the single-source re-root (spec §1A L230-241) fold into the SAME floor so
    # ``expand`` resolves the @-ref chain ONCE (single-route). Built per mode by
    # :func:`meta_runtime_floor`. These are construct-set RO (§0) — NO scope FILE
    # may override them (meta.* is not in the config-set settable known-key list);
    # the floor is their sole source. Injected here so the dotted ``meta.*`` keys
    # land unconditionally for the modes that supply them.
    if meta_runtime:
        for key, val in meta_runtime.items():
            floor[key] = val

    # meta.* IDENTITY-anchor materialization (block B2): the remaining construct-
    # time identity keys (spec §2c/§2d) the @meta.*-routed binds reference, folded
    # into the SAME floor so ``expand`` resolves the @meta.* binds ONCE (single-
    # route). Built per box by :func:`meta_identity_floor`. Construct-set RO (§0) —
    # NO scope FILE may override them (meta.* is not in the config-set settable
    # known-key list); the floor is their sole source. ``None`` for a narrow resolve.
    if meta_identity:
        for key, val in meta_identity.items():
            floor[key] = val

    # workset PATH-anchor materialization (block B2b): the workset-scope path
    # anchors (workset.{boxes,vault_ro,vault_rw,logs} + workset.channels.* +
    # meta.box.helper_log) the @-ref-routed core home/vault/helper_log/workset-
    # channel binds reference (spec §2c/§2g). Folded into the SAME floor so
    # ``expand`` resolves the @workset.* binds ONCE (single-route). Built per box by
    # :func:`workset_anchor_floor`. ``None`` for a narrow resolve. A scope FILE MAY
    # legitimately override a workset.* key (workset.* is a settable settings tier),
    # so these sit at the floor (base) and a workset/box file still wins by name.
    if workset_anchor:
        for key, val in workset_anchor.items():
            floor[key] = val

    base_levels = assemble_levels(
        agent_name=agent_name,
        system_path=system_path,
        agent_path=agent_path,
        workset_path=workset_path,
        box_path=box_path,
        floor=floor,
    )
    # ``assemble_levels`` ALWAYS returns the 6 levels MOST-SPECIFIC-FIRST (S8):
    #   [box, workset, agent.<active>, agent.default, system, base]
    #    idx 0    1        2              3              4       5
    # Build the FINAL ordered level list by splicing the optional extra partials at
    # their PRECISE precedence rungs, computed from these FIXED base indices (doing
    # all splices in one pass keeps the math robust — no chained index drift):
    #
    #   override bridge  — just below box (above workset): wins 7a's origin default
    #                      by name, loses to a box-file set.
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
    levels.append(base_levels[0])                       # box
    if bridge is not None:
        levels.append(bridge)                           # override bridge (below box)
    levels.append(base_levels[1])                       # workset
    if state_partial is not None:
        levels.append(state_partial)                    # per-agent FILE behavior
    levels.append(base_levels[2])                       # agent.<active> (file tables)
    levels.append(base_levels[3])                       # agent.default
    if agent_partial is not None:
        levels.append(agent_partial)                    # 7a descriptor default
    levels.append(base_levels[4])                       # system
    levels.append(base_levels[5])                       # base (+ folded floor)

    snapshot = merge(levels)
    expanded = expand(snapshot, ctx)
    # box.agent.* mirror (block B5, spec §2b L380 / §0 directional). Materialize the
    # box-scoped mirror of the active agent's WHOLE resolved settings subtree as a
    # COPY-on-current-engine step, AFTER expand so the values are resolved terminals.
    _materialize_box_agent_mirror(expanded, active_agent=agent_name)
    return expanded


# --------------------------------------------------------------------------- #
# box.agent.* mirror materialization (block B5 — spec §2b L380, §0 directional) #
# --------------------------------------------------------------------------- #
#
# Spec §2b L380: ``box.agent.<key>`` is the box's box-scoped mirror of its active
# agent's WHOLE settings subtree — it DEFAULTS (views up) to the resolved
# ``agent.<box.agent_name>.<key>`` (with the ``agent.default`` fallback), and the
# box overriding any ``box.agent.<key>`` is an ORDINARY same-scope (box) write
# (§0: the no-special-case R2-legal downward tweak). Re-materialized when
# ``box.agent_name`` changes. Spec impl note: COPY (pure) or shared REF (equivalent
# — one box per process). Keystone D-D: COPY is conceptually purer; REF equivalent
# since one box per process; the spec defines the SEMANTICS.
#
# MECHANISM (JC-B5-1 — COPY, materialized on the current engine, no resolver
# inversion). The resolved active-agent subtree only EXISTS post-merge/expand
# (the cascade keeps ``agent.default`` and ``agent.<active>`` DISCRIMINATED and the
# active-over-default value-pick is a CONSUMER step — :func:`_effective_agent_node`).
# So box.agent.* is materialized AFTER ``expand`` as a deep COPY of that resolved
# effective-agent node into ``snapshot["box"]["agent"]``, filling ONLY names the
# box did not already set. This satisfies the three requirements:
#   (a) box.agent.<key> with NO box override DEFAULTS to the resolved
#       agent.<active>.<key> (agent.default fallback included) — the effective node
#       IS ``agent.default`` overlaid by ``agent.<active>`` (§2d L368 pick), so each
#       copied leaf is exactly what ``agent.<box.agent_name>.<key>`` resolves to.
#   (b) a box-file box.agent.<key> WINS — the box settings file's ``box.agent.*``
#       entries merge in at BOX scope (``_file_partial`` keeps the scope token, so
#       they land under ``snapshot["box"]["agent"]`` BEFORE this step). The copy is
#       gap-filling (per-name ``_MISSING`` probe): a box-set name is left untouched,
#       so the box override stands. This is the ORDINARY same-scope box write — the
#       cascade already gave it box precedence; we never overwrite it.
#   (c) NO leak — the materialized subtree is a FRESH deep COPY (``_deep_copy_store``
#       leaves immutable Bind/scalar/None leaves shared but never aliases a nested
#       KeyStore), written ONLY under ``box.agent.*``. ``snapshot["agent"]`` is never
#       mutated, so a box.agent tweak (a box-file override, or a later in-place edit)
#       cannot escape into the shared agent subtree or to another box. (One box per
#       process anyway — but the COPY makes no-leak hold structurally, not by luck.)
# Re-materialization on box.agent_name change is AUTOMATIC: ``agent_name`` is the
# launch-resolved active agent (``box_agent_name`` → ``system.default_agent``
# fallback), threaded into every snapshot build; change box.agent_name → a different
# ``agent_name`` → a different effective node copied next launch.
#
# NO-AGENT box (box.agent_name <None> → empty ``agent_name``): there is NO agent
# subtree to mirror, so box.agent.* is left empty/absent (spec requirement). The
# guard is the empty/blank ``active_agent`` short-circuit below — even though
# ``agent.default`` exists in the snapshot, a NO-AGENT box has no ACTIVE agent whose
# subtree the spec mirrors, so nothing is materialized.


def _materialize_box_agent_mirror(snapshot: KeyStore, *, active_agent: str) -> None:
    """Materialize ``box.agent.*`` = the resolved active-agent subtree (block B5).

    The box's box-scoped mirror of its active agent's WHOLE resolved settings
    subtree (spec §2b L380): a deep COPY of the resolved effective-agent node
    (``agent.default`` overlaid by ``agent.<active_agent>`` — the §2d L368 pick) is
    written under ``snapshot["box"]["agent"]``, filling ONLY names the box did NOT
    already set (the box's own ``box.agent.*`` overrides, merged in at box scope,
    are LEFT INTACT — they win, the ordinary same-scope box write of §0). Mutates
    *snapshot* in place (it is the launch-local expanded tree, owned by the caller).

    *active_agent* is the launch-resolved active agent name. A NO-AGENT box has a
    blank name → NO active-agent subtree to mirror → nothing materialized (spec:
    box.agent.* empty/absent for a NO-AGENT box). The mirror tracks ``box.agent_name``
    because *active_agent* IS the resolved active agent for this launch.

    COPY (not REF) — keystone D-D / JC-B5-1: a fresh deep copy guarantees no box
    tweak leaks into the shared ``agent.*`` subtree or across boxes (no-leak holds
    STRUCTURALLY, not just because one box runs per process). Reads/writes via the
    UNBOUND ``dict`` protocol (S3) so a key named ``get`` / ``agent`` cannot shadow.
    """
    if not active_agent or not active_agent.strip():
        # NO-AGENT box — no active agent subtree to mirror (spec). Leave box.agent.*
        # absent; do NOT fall back to agent.default (that is the all-agents backstop,
        # not an ACTIVE agent the box runs).
        return
    # The PURE pick (agent.default ⊕ agent.<active>) — NOT _effective_agent_node,
    # which would overlay box.agent.* and double-count (chicken-and-egg). The mirror
    # IS this pick gap-filled under the box's own box.agent overrides.
    effective = _agent_pick_node(snapshot, active_agent)
    if not dict.__len__(effective):
        # The active agent set NO category/behavior leaves (and no default backstop
        # either) — nothing to mirror; leave box.agent.* absent.
        return
    box_node = dict.get(snapshot, "box", _MISSING)
    if not isinstance(box_node, KeyStore):
        box_node = KeyStore()
        snapshot["box"] = box_node
    box_agent = dict.get(box_node, "agent", _MISSING)
    if not isinstance(box_agent, KeyStore):
        box_agent = KeyStore()
        box_node["agent"] = box_agent
    # Gap-fill: copy each resolved effective-agent name the box did NOT already set.
    # A box-set name (the box-file ``box.agent.<key>`` override, present under
    # ``box.agent`` from the box-scope merge) is LEFT UNTOUCHED — it wins (b). A
    # nested KeyStore (e.g. ``bindings``) is filled PER NAME so a box override of one
    # leaf (``box.agent.bindings.ro.share``) coexists with mirrored sibling leaves.
    _mirror_fill(box_agent, effective)


def _mirror_fill(box_node: KeyStore, agent_node: KeyStore) -> None:
    """Deep gap-fill *box_node* from *agent_node*: copy each *agent_node* name the
    *box_node* does NOT already set; recurse into matching KeyStore subtrees so a
    box override of ONE leaf does not suppress mirrored siblings (block B5).

    A box-set leaf (the box-file override) is LEFT INTACT (it wins — spec §2b L380
    ORDINARY same-scope write). A name absent from *box_node* is set to a FRESH deep
    COPY of the agent value (``_deep_copy_store`` for a subtree; an immutable Bind /
    scalar / None / a fresh ``list`` for a leaf) so no box edit aliases the shared
    ``agent.*`` subtree (no-leak, (c)). Unbound ``dict`` protocol (S3).
    """
    from kanibako.settings_merge import _deep_copy_store

    for name in dict.keys(agent_node):
        agent_val = dict.__getitem__(agent_node, name)
        box_val = dict.get(box_node, name, _MISSING)
        if box_val is _MISSING:
            # The box did not set this name → mirror a fresh deep copy of the
            # resolved agent value (no alias of the shared agent subtree).
            if isinstance(agent_val, KeyStore):
                box_node[name] = _deep_copy_store(agent_val)
            elif isinstance(agent_val, list):
                box_node[name] = list(agent_val)
            else:
                box_node[name] = agent_val  # Bind / scalar / None — immutable.
            continue
        # The box set this name. Recurse to gap-fill DEEPER only when BOTH sides are
        # subtrees (a box override of one leaf keeps mirrored siblings); a box leaf
        # vs an agent subtree (or vice versa) means the box wholesale-overrode that
        # name — leave the box value, do NOT merge across the type boundary.
        if isinstance(box_val, KeyStore) and isinstance(agent_val, KeyStore):
            _mirror_fill(box_val, agent_val)


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
    it beats 7a's origin default by name yet loses to a box-file set.
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
    # box.agent.* mirror (block B5, spec §2b L380 / §0): the box's box-scoped agent
    # override is the HIGHEST-precedence behavior source — it WINS the §2d pick (the
    # box's downward tweak takes EFFECT). With NO override the mirror leaf EQUALS the
    # pick (gap-filled), so this overlay is a NO-OP for default boxes (the
    # equivalence guard). A NO-AGENT box has no box.agent node → absent.
    box_node = dict.get(snapshot, "box", _MISSING)
    box_agent_node = (
        dict.get(box_node, "agent", _MISSING)
        if isinstance(box_node, KeyStore)
        else _MISSING
    )

    if keys is None:
        # DISCOVER: the union of scalar-leaf names across the box.agent override,
        # the active slot, and the default backstop (box.agent first so a box-only
        # behavior key is discovered too). Category subtrees / Bind leaves are
        # filtered out per-key below.
        discovered: dict[str, None] = {}
        for node in (box_agent_node, active_node, default_node):
            if isinstance(node, KeyStore):
                for name in dict.keys(node):
                    discovered.setdefault(name, None)
        key_iter: "list[str]" = list(discovered)
    else:
        key_iter = keys

    for key in key_iter:
        # box.agent override WINS (block B5) → active-over-default pick (§2d L368).
        # Probe the box.agent mirror first; then the active slot; then default. A
        # present value (incl. present-None) SETS the key and shadows lower sources.
        val: object = _MISSING
        if isinstance(box_agent_node, KeyStore):
            val = dict.get(box_agent_node, key, _MISSING)
        if val is _MISSING and isinstance(active_node, KeyStore):
            val = dict.get(active_node, key, _MISSING)
        if val is _MISSING and isinstance(default_node, KeyStore):
            # Neither box nor active set it → fall back to the agent.default backstop.
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


def _agent_pick_node(snapshot: KeyStore, active_agent: str) -> KeyStore:
    """The PURE active-over-default agent pick = ``agent.default`` overlaid by
    ``agent.<active_agent>`` (the §2d L368 value-pick), WITHOUT the box.agent.*
    overlay.

    Returns a FRESH ``KeyStore`` shaped like a single (bare) agent scope node — its
    ``bindings.{ro,rw}`` / ``caches`` / ``seeded`` / ``shared`` / ``synced`` /
    ``masks`` / ``env`` subtrees + behavior leaves holding the per-name winner: the
    active slot's leaf wherever it set that name, else the ``agent.default`` leaf.
    The overlay is PER NAME (deep) so an active ``agent.<active>.shared.cache`` and a
    default-only ``agent.default.shared.plugins`` BOTH survive. A present-``None``
    reset was already OMITted by the merge (§3 / §6e), so it never reaches here.

    This is the subtree the box.agent.* mirror is MATERIALIZED from (block B5,
    ``_materialize_box_agent_mirror``) — it must NOT itself read box.agent.* (no
    chicken-and-egg). The CONSUMER-facing :func:`_effective_agent_node` then overlays
    box.agent.* on top of this (box WINS). Reads via the UNBOUND ``dict`` protocol
    (S3); never mutates the snapshot.
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


def _effective_agent_node(snapshot: KeyStore, active_agent: str) -> KeyStore:
    """The effective AGENT-scope category node the box's resolution USES = the
    active-over-default pick (:func:`_agent_pick_node`) overlaid by the box's
    box-scoped ``box.agent.*`` mirror (box WINS — block B5, spec §2b L380 / §0).

    The box.agent.* overlay is what makes the box's downward-tweak TAKE EFFECT
    (§0 L38-40: the box tweaks its one thing — its agent — through box.agent.*; an
    ordinary same-scope box write). The mirror node (``snapshot["box"]["agent"]``)
    is the gap-filled ``agent.default ⊕ agent.<active>`` with any box-file override
    on top, so:

    * **with a box.agent override** the override leaf WINS over the pick (the box's
      downward tweak is live in category resolution);
    * **with NO override** the mirror leaf EQUALS the pick leaf, so the overlay is a
      NO-OP — the effective node is byte-identical to today's pick (the EQUIVALENCE
      guard: default boxes are unchanged).

    A NO-AGENT box has no ``box.agent`` mirror (the materializer skipped it), so the
    overlay is absent and this reduces to the pick. Reads via the UNBOUND ``dict``
    protocol (S3); never mutates the snapshot.
    """
    out = _agent_pick_node(snapshot, active_agent)
    box_node = dict.get(snapshot, "box", _MISSING)
    if isinstance(box_node, KeyStore):
        box_agent = dict.get(box_node, "agent", _MISSING)
        if isinstance(box_agent, KeyStore):
            _overlay_into(out, box_agent)  # box.agent WINS (per-name deep overlay).
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

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
core / kani / helper / image binds, agent common/seeded, masks) and a behavior
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
  (single-route), NOT a parallel ``descriptor_mounts`` route. A user repoint of a
  descriptor delivery bind's host source is an ordinary settable
  ``agent.<agent>.bindings.{ro,rw}.<key>`` set on a scope FILE (the SAME active
  slot 7a delivers into); it merges over 7a BY NAME through the normal cascade —
  no parallel override route.

The DISCRIMINATED agent read (§2d L368)
---------------------------------------
The snapshot keeps the agent tier discriminated — ``agent.default.*`` (the
all-agents backstop) and ``agent.<active>.*`` (the active slot, where 7a or
a per-agent file land). Both the behavior read
(:func:`effective_behavior`) and the category adapter
(:func:`snapshot_category_entries`) do the active-over-default value-pick PER NAME
HERE — the consumer's job, since 2a/7a / the merge deliberately keep both slots'
keys discriminated. Emitted ``CategoryEntry``\\ s carry the BARE ``agent`` scope
token (load-bearing for reconcile precedence, NOT the discriminator).

box_dest deferral (S17 / B6)
----------------------------
The snapshot keeps box-side ``$XDG`` / ``~`` in a ``Bind.box`` RAW (deferred —
host ≠ box). The category ADAPTER (:func:`snapshot_category_entries`) is a
``box_dest`` consumer: it resolves box-side ``~`` → ``GUEST_HOME`` and ``$XDG``
against the BOX ctx (matching the retired by-name resolver's ``space="guest"``)
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
from typing import Collection, Literal, Mapping, Sequence

from kanibako.agent_ref import harness_of
from kanibako.settings_assemble import assemble_levels, dotted_partial
from kanibako.settings_categories import (
    _DELIVERY,
    SECRET_MOUNT_DIR,
    CategoryEntry,
    _bind_options,
)
from kanibako.settings_expand import expand
from kanibako.settings_merge import merge
from kanibako.settings_prefs import PrefRequest, apply_prefs, collect_prefs
from kanibako.settings_resolve import ResolveCtx, SettingsError, expand_expr
from kanibako.settings_store import _MISSING, SCOPE_CONTAINMENT, Bind, KeyStore


# The category tokens that hold bind-shaped (``Bind``) leaves in the snapshot's
# ``<scope>.<category>`` subtrees. ``bindings`` carries ``ro`` / ``rw`` sub-nodes;
# the rest are flat ``<category>.<name>`` bind maps.
_BIND_LEAF_CATEGORIES: frozenset[str] = frozenset(
    {"caches", "seeded", "common", "synced"}
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
#   meta.box.agent.auth.share_support     = @meta.agent.<@system.agent>.auth.share_support
#                                           (mirror, materialized from the SELECTED node —
#                                            the <...> node selector is not expressible; see
#                                            auth_chain_floor)
#   box.auth.global_enabled  = %@meta.box.agent.auth.share_support && @system.auth.share_allowed%
#   box.auth.workset_enabled = %@meta.box.agent.auth.share_support && @workset.auth.share_allowed%
#   workset.auth.path        = @meta.workset.path/auth        (workset auth dir, per workset; SETTABLE)
#   meta.box.auth.workset_path = @workset.auth.path/@system.agent  (this box's per-agent source
#                                           root — RO DERIVED meta anchor, change 8: NOT settable, so a
#                                           user can't repoint it to garbage; the ONLY settable auth-
#                                           location surface is ``workset.auth.path``)
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
    allow knobs, the two settable box ENABLE knobs (per-tier opt-out defaults), the
    settable ``workset.auth.path`` store anchor, and the RO DERIVED per-box source
    root ``meta.box.auth.workset_path`` (change 8 — a ``meta.*`` anchor, NOT settable:
    a scope FILE cannot repoint it, so the ONLY settable auth-location surface is
    ``workset.auth.path``). :func:`resolve_auth_source` computes the effective enable
    = ``support && allow && knob`` in Python (see the module note).

    PRIMARY / NAMED use the @-ref forms. STANDALONE pins the two workset keys
    (``workset.auth.share_allowed`` / ``workset.auth.global_sync``) to the LITERAL
    ``False`` so the workset tier degenerates false without a workset group;
    ``box.auth.global_enabled`` still derives from the global gate (a standalone box
    CAN use global/host creds).
    """
    floor: dict[str, object] = {
        # The GLOBAL-share gate — the single host-wide allow flag (settable).
        _SYSTEM_SHARE_ALLOWED_KEY: True,
        # The box-scoped MIRROR of the active agent's capability (RO / meta).
        #
        # ⚑ The spec (§2c L936) writes this as
        # ``@meta.agent.<@system.agent>.auth.share_support``. That is NODE-SELECTOR
        # NOTATION, and it is NOT EXPRESSIBLE by the resolver — a reference name is
        # a literal, there is no second pass, and PHASE R's ``@{name}`` DELIMITS a
        # name so a literal SUFFIX may follow it; it does not nest a reference
        # INSIDE a name. So the shipped mechanism is the documented equivalent: the
        # selected node is interpolated HERE and the resulting @-ref is followed by
        # ``expand`` on a literal path. What P7 changed is only the PROVENANCE of
        # *agent_name* — it is now the value the §1A selection level installs at
        # ``system.agent``, i.e. the same string the spelled anchor above resolves.
        # (Spec follow-up, queued for Jei: either §2c gains a note that this anchor
        # is MATERIALIZED, or a future grammar phase adds node selection.)
        # The plugin/agent capability ``meta.agent.<agent>.auth.share_support``
        # rides the meta identity floor.
        # ⚑ A BLANK agent is pinned to the LITERAL ``False``, not spelled as a ref.
        # ``f"@meta.agent.{''}.auth.share_support"`` is ``@meta.agent..auth.share_support``
        # — a MALFORMED ref with an empty segment, which resolves to a leftover
        # string and then crashes ``resolve_auth_source``'s strict ``as_bool``
        # ("expected bool, got str"). No caller passes blank today (the launch uses
        # the ``"general"`` slot for a shell box; stop/creds-watch return early on an
        # absent ``KANIBAKO_AGENT`` stamp), but P7 made ``""`` a MEANINGFUL value —
        # the D-M6 suppression — so the trap is one careless caller away. ``False``
        # is also the semantically right answer: no agent ⇒ no sharing capability,
        # which is exactly the hard RO floor §2c describes.
        "meta.box.agent.auth.share_support": (
            f"@meta.agent.{agent_name}.auth.share_support"
            if agent_name and agent_name.strip()
            else False
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
        # box's own root within it, keyed by the active agent name. RECLASSIFIED
        # (change 8) to the RO DERIVED ``meta.box.auth.workset_path`` anchor — DIRECTLY
        # under ``meta.box.auth.*`` (NOT ``meta.box.agent.auth.*``: the agent sub-
        # namespace is the capability MIRROR, each key @-refs a
        # ``meta.agent.<agent>.*`` source; this is a per-box LOCATION, not an agent
        # capability). Being ``meta.*`` it is dropped from every settings FILE in
        # assembly, so a user CANNOT repoint it to a dangling @-ref / garbage — the
        # only settable auth-location surface is ``workset.auth.path``.
        #
        # ⚑ P7: SPELLED EXACTLY AS THE SPEC (§2c L792) — ``@workset.auth.path/
        # @system.agent`` — instead of interpolating the active agent in Python.
        # It is a CONSTANT now: the per-box variation arrives through
        # ``pref.system.agent`` (§2h) and the §1A selection level, both of which
        # are applied BEFORE this level resolves. NO braces are needed: the first
        # ref is terminated by ``/`` (not segment-legal) and the second ends the
        # value, so ``_REF_NAME_RE``'s greed cannot swallow anything (PHASE R's
        # ``@{name}`` exists for a LITERAL SUFFIX after a ref — there is none here).
        # Both refs are EMBEDDED, so an absent ``system.agent`` coerces to ``""``
        # (§6b) rather than dropping the key: a NO-AGENT box resolves this to
        # ``<auth.path>/``. That is INERT — ``resolve_auth_source`` scrubs
        # ``workset_source`` for every non-workset tier and the workset tier needs
        # ``share_support``, which no-agent cannot have.
        "meta.box.auth.workset_path": "@workset.auth.path/@system.agent",
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
        # too (defensive root-cause fix): otherwise ``meta.box.auth.workset_path`` =
        # ``@workset.auth.path/<agent>`` would resolve against the absent
        # ``workset.auth.path`` and expand to the literal ``/<agent>`` (an @-ref to
        # an absent key renders ``""``, not a drop) — garbage the credsync
        # dir-creation would mkdir against the host ROOT. The workset enable is
        # false anyway, so this source is never consulted; pinning None makes that
        # explicit at the floor, belt-and-braces with the resolver's scrub.
        floor["workset.auth.path"] = None
        # The RO DERIVED per-box source root (change 8: was ``box.auth.workset_path``)
        # — a ``meta.*`` anchor pinned None for standalone, the established meta-anchor-
        # is-None-for-standalone pattern; resolve_auth_source reads this meta node.
        floor["meta.box.auth.workset_path"] = None
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
#   meta.runtime.project_type | proj.mode.value  ("primary"|"named"|"standalone")
#
# (There is NO meta.runtime.ws_settings: spec §1A L367-368 CUT it — "no longer needed
#  (unified path)". It was a one-consumer alias for the value string below.)
#
# Then the SINGLE-SOURCE re-root (spec §1A L239-241; §2c L397/406/414/432):
#   meta.workset.path     = "@meta.runtime.ws_root"                (UNIFORM all modes)
#   meta.workset.settings = "@meta.workset.path/settings.yaml"     (UNIFORM; spec 2c L804)
#   meta.box.mode         = "@meta.runtime.project_type"   (RO identity anchor; spec §2b L486)
#
# These resolve transitively in the ONE expand pass (e.g. primary:
# meta.workset.path → @meta.runtime.ws_root → @config.primary_workset → foundation;
# standalone: meta.workset.settings → @meta.workset.path/settings.yaml →
# @meta.runtime.ws_root/settings.yaml → <root>/settings.yaml, the workset tier).
#
# This block is ADDITIVE (B1): the keys appear in the snapshot but NO consumer
# reads them yet (binds move to @meta.* in a later block). The only behavioral
# change is meta.box.mode (an RO identity anchor replacing the formerly settable
# ``box.mode`` config-set key — dropped in config_interface this block).


def meta_runtime_floor(
    *,
    mode: str,
    ws_name: str,
    ws_root_literal: str | None = None,
) -> dict[str, object]:
    """Build the ``meta.runtime.*`` + re-rooted ``meta.*`` floor keys (block B1).

    Returns the ``{dotted_key: value}`` floor fragment for the spec's runtime
    identity anchors (spec §1A L230-241; §0 meta-RO). The caller folds it into
    ``build_launch_snapshot``'s floor so ``expand`` resolves the @-ref chain ONCE
    (single-route — NO second resolver). *mode* is the box's
    :class:`~kanibako.paths.BoxMode` value (``"primary"`` / ``"named"`` /
    ``"standalone"``), passed as a plain string to avoid a paths import.

    *ws_name* is the workset partition TOKEN (spec §1A ``meta.runtime.ws_name``,
    2026-07-04): ``__PRIMARY__`` for primary, ``__STANDALONE__`` for standalone,
    the detected workset name for named. It is SINGLE-SOURCED on
    :func:`kanibako.channels.workset_name_token` and threaded in by the caller (the
    SAME token that drives the channel partition, so the two cannot drift). It is
    surfaced as ``meta.runtime.ws_name`` and ``meta.workset.name`` anchors into it
    (see below), replacing the direct ``meta.workset.name`` literal block B2 set.

    *ws_root_literal* is the resolved workset-root path STRING for the NAMED and
    STANDALONE modes (``str(proj.group.root)`` / ``str(project dir)`` — a runtime
    treewalk result, no key form, JC-B1-2: an in-memory floor literal, NOT a
    file value, so §0's unresolved-FILES rule does not apply). It MUST be given
    for ``named`` / ``standalone`` and is IGNORED for ``primary`` (which uses the
    ``@config.primary_workset`` @-ref so the value live-propagates from the
    Layer-1 foundation, spec §1A L233).

    The re-rooted keys (``meta.workset.path`` / ``meta.workset.settings`` /
    ``meta.workset.name`` / ``meta.box.mode``) are UNIFORM across modes — each is
    the SAME @-ref into ``meta.runtime.*`` (the single-source, spec §1A L239-241).
    A scope FILE cannot
    set them (they are construct-set RO per §0 — and ``meta.*`` is not in the
    config-set settable known-key list); the floor is their sole source here.
    """
    floor: dict[str, object] = {}

    # meta.runtime.project_type — the resolved mode token (spec §1A L237).
    floor["meta.runtime.project_type"] = mode

    # meta.runtime.ws_name — the workset partition TOKEN (spec §1A, 2026-07-04):
    #   primary → __PRIMARY__ · named → <detected name> · standalone → __STANDALONE__.
    # SINGLE-SOURCED on channels.workset_name_token (threaded in by the caller — the
    # same token that keys the channel partition, so the two cannot drift).
    floor["meta.runtime.ws_name"] = ws_name

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

    # Single-source re-root (spec §1A L239-241; §2c) — UNIFORM all modes.
    floor["meta.workset.path"] = "@meta.runtime.ws_root"
    # meta.workset.settings — the SPEC's own spelling, §2c L804:
    # ``@meta.workset.path/settings.yaml``. That chains through the anchor set two
    # lines up (meta.workset.path = @meta.runtime.ws_root), which the ONE expand pass
    # resolves transitively — the same chained-floor-ref pattern the box-root anchors
    # use. Spelling it off @meta.runtime.ws_root instead would resolve to the
    # byte-identical value but DIVERGE from the spec, and the spec is authority.
    # ⚑ The intermediate ``meta.runtime.ws_settings`` key is CUT from the keyspace
    # (spec §1A L367-368, "no longer needed (unified path)"): it held exactly this
    # value and had exactly ONE consumer — this key — so substituting its definition
    # removes a hop without changing a single resolved value. Under §0's CLOSED
    # KEYSPACE an undeclared key is not a key, so it does not linger as an alias;
    # ``@meta.workset.settings`` is the only spelling.
    floor["meta.workset.settings"] = "@meta.workset.path/settings.yaml"
    # meta.workset.name anchors into meta.runtime.ws_name (spec §2c L442/449/457,
    # 2026-07-04) — the SINGLE SOURCE for the partition token; block B2 no longer
    # sets it directly.
    floor["meta.workset.name"] = "@meta.runtime.ws_name"
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
#                             standalone=<kuid>_%leaf% — already composed LIVE and
#                             carried on proj.name, JC-B2-2: reuse, do not regen)
#   meta.box.workspace      | the resolved in-box workspace SOURCE (str(proj.
#                             project_path)) — routed to box.bindings.rw.workspace
#   meta.box.inbox          | this box's own mailbox dir (str(addr.inbox)) —
#                             routed to box.bindings.rw.inbox
#   meta.box.share_global   | this box's system-scope share dir (str(addr.share_global))
#   meta.box.share_workset  | this box's workset-local share dir (str | None standalone)
#   meta.agent.<a>.name     | the plugin-set agent name (REQUIRED when an agent exists)
# (meta.workset.name is now a meta_runtime_floor anchor into meta.runtime.ws_name —
#  the single source for the partition token, spec §2c 2026-07-04 — NOT a B2 key.)
#
# meta.box.settings is the RO box-TIER settings-file anchor, and it is UNIFORM in
# EVERY mode (spec §2c ALL PROJECTS L817: @meta.box.path/settings.yaml). Standalone is
# NOT a <None> terminal: its box tier is <root>/box_data/settings.yaml — a real path,
# merely ABSENT BY DEFAULT (§5 L1407), an absent file being an empty tier. The WRITE
# target moved with the READ in the same change (M-8), so a `config set box.*` lands
# in exactly the file this anchor names. Note it still cannot simply BE the
# @meta.box.path/settings.yaml @-ref: a bootstrap anchor may not derive from a key at
# the scope it bootstraps, and this one resolves BEFORE the cascade exists (unlike the
# launch-time home bind, which is why THAT one may root at @meta.box.path).
# Materialized here as the RESOLVED LITERAL the launch computes — the SAME value the
# cascade uses as its box-tier file path (single-sourced through
# paths.box_workset_settings_paths in start.py's _launch_snapshot_inputs), so the
# anchor and the cascade cannot drift. meta.box.{workspace(named),container_name,
# helper_num} per the spec are non-bind RENDER targets (container_name from
# name+helper_num); B2 materializes the IDENTITY leaves the eligible BINDS reference
# + the agent name (meta.workset.name moved to meta_runtime_floor as an anchor). The
# container_name / helper_num RENDER and the home/vault binds stay on attrs /
# @workset.* (JC-B2-3 / JC-B2-4 — see the return docstring), a tracked follow-up.


def meta_agent_path_floor(agent_name: str) -> dict[str, object]:
    """The agent STORE-ROOT anchors ``meta.agent.<a>.path`` for *agent_name*.

    THE single builder for this key, used by BOTH the launch floor
    (:func:`meta_identity_floor`) and the ``config set`` SET-TIME validation
    snapshot. That sharing is load-bearing, not tidiness: ``config set``'s refusal
    message tells a user to spell an abstract-category source as
    ``@meta.agent.<agent>.path/<category>/<name>``, and if the set-time snapshot did
    not carry the key, the very value the tool just recommended would be rejected as
    a dangling ``@``-reference. A hint that cannot be accepted is worse than none.

    Spelled ``@config.agents/<name>`` rather than the spec's
    ``@config.agents/@meta.agent.<a>.name`` chain (§2d L515): ``<name>`` IS the value
    of ``meta.agent.<a>.name`` at both seams, and the flat form avoids a floor entry
    that references its own sibling. Both resolve identically (verified).

    ⚑ NODE **and** HARNESS. ``load_common`` keys its entries on the plugin's own
    ``Target.name`` (the HARNESS, e.g. ``claude``) while callers pass the ACTIVE
    NODE (``navigator℘claude`` for a persona). On a persona box those differ, so
    materializing only the node would leave the harness-keyed refs DANGLING. Both are
    materialized; for a bare agent node == harness and this is a single entry.

    ⚑ The harness entry is INTENTIONALLY PARTIAL on a persona box: it gets a
    ``path`` but no ``name`` / ``auth.share_support`` (those stay the ACTIVE agent's,
    which is what every consumer of them means). Nothing reads
    ``meta.agent.<harness>.name`` — the partial node exists solely so a
    harness-keyed store ref resolves — so the asymmetry is inert, and making it
    symmetric would invent a second identity for one agent.
    """
    return {
        f"meta.agent.{store_agent}.path": f"@config.agents/{store_agent}"
        for store_agent in {agent_name, harness_of(agent_name)}
    }


def meta_identity_floor(
    *,
    box_name: str,
    project_path: str,
    inbox: str,
    share_global: str,
    share_workset: str | None,
    box_settings: str | None = None,
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
    with value ``None``). It is now the ONLY standalone ``None`` terminal here: a lone
    box genuinely has no workset-LOCAL channel dir, whereas it DOES have a box
    settings tier (see *box_settings*).

    *box_settings* is the RO box-TIER settings-file anchor ``meta.box.settings``
    (spec §2c ALL PROJECTS L817): the box tier's ``settings.yaml`` path STRING, in
    EVERY mode — the box's own ``<metadata_path>/settings.yaml`` for primary/named and
    ``<root>/box_data/settings.yaml`` for standalone (ABSENT BY DEFAULT, §5 L1407; an
    absent file is an empty tier, and box-scope values then resolve from the workset
    tier ``@meta.workset.settings`` as R2 downward-defaults). It is single-sourced with
    the cascade's box-tier file path (both come from ``box_workset_settings_paths`` in
    ``_launch_snapshot_inputs``), so the anchor and the cascade cannot drift. The
    parameter stays optional for narrow/partial resolves that materialize no box tier;
    the launch always passes a real path.

    *agent_name* / *agent_real_name*: when an agent exists, ``meta.agent.<a>.name``
    is the plugin-set agent name (spec §2d L514, REQUIRED). ``agent_name`` is the
    cascade discriminator (``install.name``); ``agent_real_name`` is the value
    (the plugin's ``meta.agent.<agent>.name`` — normally the same string). Both
    ``None`` for a NO-AGENT box (skips the agent identity key).

    ⚑ The STORE-ROOT anchor ``meta.agent.<a>.path`` is keyed on the DISCRIMINATOR
    (*agent_name*), not on *agent_real_name* — the store dir is
    ``agents/<discriminator>/``, which is what ``agent_settings_path`` and the
    persona shim use. The two are the same string for every shipped plugin; if a
    plugin ever returned a different ``name``, the path anchor would still (rightly)
    follow the store, while ``meta.agent.<a>.name`` reported the plugin's value.
    """
    floor: dict[str, object] = {
        # Box identity (spec §2c). The box name is carried on ``proj.name``
        # (JC-B2-2: reuse — standalone's <kuid>_%leaf% is composed LIVE in
        # ``resolve_standalone_project`` from the stored ``workset.kuid`` + the
        # current-dir leaf, P6d; B2 does NOT re-compose or regenerate it).
        "meta.box.name": box_name,
        # The in-box workspace SOURCE literal (routed to box.bindings.rw.workspace).
        "meta.box.workspace": project_path,
        # This box's own channel partition addresses (routed to box.bindings.rw.inbox;
        # share_global / share_workset are materialized identity anchors for parity
        # and future routing — share_workset is None for standalone).
        "meta.box.inbox": inbox,
        "meta.box.share_global": share_global,
        "meta.box.share_workset": share_workset,
        # The RO box-TIER settings-file anchor (spec §2c ALL PROJECTS L817). UNIFORM:
        # the box tier's settings.yaml in EVERY mode (standalone = box_data/, absent by
        # default). Single-sourced with the cascade box-tier path in
        # _launch_snapshot_inputs, so the anchor names the file the cascade reads and
        # `config set` writes.
        "meta.box.settings": box_settings,
        # NOTE: meta.workset.name is NO LONGER set here — it anchors into
        # meta.runtime.ws_name (the single source, spec §2c 2026-07-04), set by
        # :func:`meta_runtime_floor`.
    }
    # The agent identity key (spec §2d L514) — REQUIRED when an agent exists, under
    # the agent's discriminated slot. A NO-AGENT box omits it.
    if agent_name is not None:
        floor[f"meta.agent.{agent_name}.name"] = (
            agent_real_name if agent_real_name is not None else agent_name
        )
        # The agent's STORE ROOT — see :func:`meta_agent_path_floor` (shared with
        # the SET-TIME validation snapshot, so a value that validates resolves).
        floor.update(meta_agent_path_floor(agent_name))
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
# LAYOUT-anchor materialization: workset roots + the RO BOX ROOT (spec §2a/§2c) #
# --------------------------------------------------------------------------- #
#
# This is the single-route payoff: it materializes the workset-scope PATH anchors
# the spec's §2c binds reference (workset.{boxes,vault_ro,vault_rw,logs} + the
# workset-local channels) and the RO per-mode BOX ROOT ``meta.box.path``, as REAL
# @-referenceable floor keys. The core home/vault/helper_log binds route through
# these anchors so the bind host_src RESOLVES via the snapshot instead of a
# proj-attr literal injected at the seam.
#
# JC-B2b-1: these workset.* keys do NOT exist as resolvable snapshot keys otherwise —
# resolve_system_paths derives only the PRIMARY pseudo-keys (system._boxes /
# system._primary_vault_* / system._primary_logs) into StandardPaths; there is no
# workset.* tier in the snapshot. They are MATERIALIZED here.
#
# ⚑ WHERE THE PER-MODE VARIATION LIVES (spec §2c L740). It lives HERE and nowhere
# downstream, so every rooted key + the box home spell themselves ONCE against
# ``@meta.box.path`` / ``@workset.*``. Each anchor is the spec's own self-resolving
# @-ref FORMULA (not a proj-attr literal): the formulas were verified equal to the
# layout helpers they replace in every mode —
#   workset.boxes      primary  @meta.workset.path/boxes    == std.boxes (paths.py)
#                      named    @meta.workset.path/boxes    == ws.projects_dir
#                      s'alone  @meta.workset.path/box_data == _standalone_box_paths
#   workset.vault_*    ALL      @meta.workset.path/vault/{ro,rw}  (ALL PROJECTS)
#   workset.logs       p/n      @meta.workset.path/logs     == helper_log_path parent
#                      s'alone  @meta.box.path              == helper_log_path parent
# — and the whole chain is gated by a launch-path byte-identity check on the
# resolved home/vault/helper_log mounts.
#
# ⚑ A BOX ROOT THAT DOES NOT RESOLVE IS CATASTROPHIC, NOT COSMETIC. The consumer
# ``box.bindings.rw.home = (@meta.box.path/home, ~)`` is an EMBEDDED ref, and the
# embedded rule (§6b) coerces an absent / present-None referent to ``""`` — so a
# box root that fails to resolve yields the host_src ``/home``, which the L7
# guarantee-create then mkdir's and mounts OVER the box home, silently. The floor
# values below are constants and cannot be None; a user's ``workset.boxes: null``
# in a settings file still can, which is why ``build_launch_snapshot`` asserts the
# resolved ``meta.box.path`` is a non-empty absolute path.


#: The box modes this floor knows how to root. An undeclared variant is NOT a mode
#: and is REFUSED rather than silently taking the primary/named arm.
_BOX_MODES: frozenset[str] = frozenset({"primary", "named", "standalone"})

#: The DECLARED ``workset.channels.*`` leaves (spec §2c L802-806, L861). The floor
#: MANUFACTURES these keys from a caller-supplied mapping, so without this set it
#: was a free-form passthrough: any leaf the caller invented became a key, which is
#: precisely what the CLOSED keyspace (spec §0) forbids — an undeclared key is not
#: a key, and code must REFUSE it rather than quietly accept it.
#:
#: ⚑ It is the SPEC's declared family, not the subset the one live caller happens
#: to pass (``{common, chat, share}``). Pinning it to today's caller would refuse a
#: declared key the moment a second caller supplied one — the check exists to stop
#: FABRICATION, not to freeze the current call.
_WORKSET_CHANNEL_LEAVES: frozenset[str] = frozenset(
    {"common", "chat", "broadcast", "share", "mailboxes"}
)


def workset_anchor_floor(
    *,
    mode: str,
    workset_channels: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build the LAYOUT-anchor floor keys — workset roots + the box root (spec §2c).

    Returns the ``{dotted_key: value}`` floor fragment for the spec's workset-scope
    path anchors and the RO per-mode box root. Folded into
    ``build_launch_snapshot``'s floor (like :func:`meta_identity_floor`) so
    ``expand`` resolves the @workset.* / @meta.box.path binds ONCE (single-route).

    Every anchor is the spec's self-resolving @-ref FORMULA, so the per-mode
    variation is spelled HERE and nowhere downstream (spec §2c L740, §2a
    "Declaration roots"):

    * ``workset.boxes`` — PER-MODE: ``@meta.workset.path/boxes`` (primary/named,
      §2c L760) · ``@meta.workset.path/box_data`` (standalone, §2c L727).
    * ``workset.vault_{ro,rw}`` — UNIFORM in every mode (§2c ALL PROJECTS L786-787):
      ``@meta.workset.path/vault/{ro,rw}``. Only the BOX BIND differs per mode (the
      per-box ``/@meta.box.name`` subdir a lone box does not need).
    * ``workset.logs`` — PER-MODE: ``@meta.workset.path/logs`` (primary/named, §2c
      L761) · ``@meta.box.path`` (standalone, §2c L728).
    * ``meta.box.path`` — the RO BOX ROOT: ``@workset.boxes/@meta.box.name``
      (primary/named, §2c L770) · ``@workset.boxes`` (standalone, §2c L740). The
      standalone form is the EMPTY LEAF: a BARE whole-value @-ref, so the resolver
      inherits ``@workset.boxes`` verbatim (``_is_whole_value_ref`` decides the shape
      by PARSE) — there is no join, hence no trailing separator and no empty path
      segment. Being ``meta.*`` it is RO by contract (§0 meta ⟺ not-settable): the
      CLI refuses a ``meta.*`` set and ``assemble_levels`` drops a top-level ``meta:``
      table from every settings file. Relocating box data is done one level up, via
      the settable ``workset.boxes``.

    ⚑ ``workset.logs`` is what makes the helper-log bind a SINGLE row for all modes:
    the bind is the spec's own ``@workset.logs/@{meta.box.name}.jsonl`` (§2c ALL
    PROJECTS), so the per-mode variation is this anchor and nothing downstream. The
    braced ``@{...}`` delimits the ref so the ``.jsonl`` suffix survives — the bare
    form would swallow it into the ref name (PHASE R; ``settings_resolve.match_ref``).
    There is deliberately NO ``meta.box.helper_log`` anchor: that construct-time
    LITERAL existed only because the spec's spelling did not parse, and it is not a
    spec-declared key (§2c declares ``meta.box.{path,name,workspace,inbox,share_*,
    auth.*}`` and never it), so under §0's closed keyspace it was not a key at all.
    Do not reintroduce it — one bind, one spelling.

    *workset_channels* (PRIMARY/NAMED only) maps ``common``/``chat``/``share`` to
    the resolved workset-local channel roots (= ``workset_channel_paths(proj, std)``),
    materialized as ``workset.channels.*`` so the workset-channel binds (spec §2c
    L452-454) route through them. ``None`` for STANDALONE (no workset channels).
    ⚑ Each leaf is checked against :data:`_WORKSET_CHANNEL_LEAVES` and an
    undeclared one is REFUSED: this is the one place a floor builds a key from a
    caller-supplied NAME, and a free-form passthrough there would open the closed
    keyspace (spec §0) from inside the floor.
    """
    if mode not in _BOX_MODES:
        raise SettingsError(
            f"workset_anchor_floor: unknown box mode {mode!r} (expected one of "
            f"{', '.join(sorted(_BOX_MODES))})"
        )
    standalone = mode == "standalone"
    floor: dict[str, object] = {
        # The workset path anchors. boxes/logs are PER-MODE; the vault roots are
        # UNIFORM (spec §2c ALL PROJECTS) — only the box BIND differs per mode.
        "workset.boxes": (
            "@meta.workset.path/box_data" if standalone else "@meta.workset.path/boxes"
        ),
        "workset.vault_ro": "@meta.workset.path/vault/ro",
        "workset.vault_rw": "@meta.workset.path/vault/rw",
        "workset.logs": "@meta.box.path" if standalone else "@meta.workset.path/logs",
        # The RO per-mode BOX ROOT — the anchor every rooted box key spells itself
        # against. STANDALONE is the EMPTY LEAF (a bare whole-value ref).
        "meta.box.path": (
            "@workset.boxes" if standalone else "@workset.boxes/@meta.box.name"
        ),
    }
    if workset_channels is not None:
        for leaf, path in workset_channels.items():
            if leaf not in _WORKSET_CHANNEL_LEAVES:
                raise SettingsError(
                    f"workset_anchor_floor: workset.channels.{leaf} is not a "
                    f"declared key; the declared channel type-roots are "
                    f"{', '.join(sorted(_WORKSET_CHANNEL_LEAVES))} (spec §2c). "
                    f"The keyspace is CLOSED (spec §0) — a floor may not "
                    f"manufacture a key from a caller-supplied name."
                )
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
      (``meta.box.auth.workset_path`` — the RO DERIVED meta anchor, change 8), or
      ``None`` for standalone / when absent. The GLOBAL source is the host home
      (``host_rel``), not carried here (implicit).
    """

    tier: AuthTier
    global_enabled: bool
    workset_enabled: bool
    global_sync: bool
    workset_source: str | None

    @property
    def creds_shared(self) -> bool:
        """True when the box receives shared creds at ANY tier (not private/box).

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
      box.auth.workset_enabled`` (AND a present ``meta.box.auth.workset_path`` store)

    Selection (design PRECEDENCE workset>global):

    * workset ENABLED (+ store present) → tier ``"workset"`` (the more specific);
    * else global ENABLED → tier ``"global"`` (host home source);
    * else tier ``"box"`` (private, no source — distinct auth).

    Each input is resolved to a real ``bool`` terminal by ``expand``;
    :func:`as_bool` does not launder. ``meta.box.auth.workset_path`` is a resolved
    string (or ``None`` for standalone). ``workset.auth.global_sync`` is the
    workset↔global up-sync flag.

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

    # The box-scoped RO meta anchors under ``meta.box``: the capability MIRROR
    # ``meta.box.agent.auth.share_support`` AND the DERIVED per-box source root
    # ``meta.box.auth.workset_path`` (change 8 — reclassified from the old settable
    # ``box.auth.workset_path``; being ``meta.*`` a scope FILE cannot repoint it).
    meta_node = dict.get(snapshot, "meta", _MISSING)
    support = False
    workset_source: str | None = None
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
            # meta.box.auth.workset_path (sibling of meta.box.agent) — the RO
            # DERIVED per-box workset source root. A resolved string (or None /
            # absent for standalone); absent/None/"" all coerce to None below.
            meta_box_auth = dict.get(meta_box, "auth", _MISSING)
            if isinstance(meta_box_auth, KeyStore):
                wp = dict.get(meta_box_auth, "workset_path", _MISSING)
                if isinstance(wp, str) and wp:
                    workset_source = wp

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

    # The two settable box ENABLE knobs (per-tier opt-out; STAY in ``box.auth``).
    # The workset SOURCE path is read above from the RO ``meta.box.auth`` node
    # (change 8), NOT here — only the settable knobs remain in ``box.auth``.
    box_auth = dict.get(box_node, "auth", _MISSING)
    global_knob = True
    workset_knob = True
    if isinstance(box_auth, KeyStore):
        global_knob = as_bool(dict.get(box_auth, "global_enabled", True))
        workset_knob = as_bool(dict.get(box_auth, "workset_enabled", True))

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
    # standalone/global/private box carries the resolved ``meta.box.auth.workset_path``,
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
    auth_chain: Mapping[str, object] | None = None,
    meta_runtime: Mapping[str, object] | None = None,
    meta_identity: Mapping[str, object] | None = None,
    workset_anchor: Mapping[str, object] | None = None,
    prefs: "Sequence[PrefRequest] | None" = None,
    valid_agents: "Collection[str] | None" = None,
    selection_level: Mapping[str, object] | None = None,
) -> KeyStore:
    """Build the ONE expanded launch snapshot.

    Folds the behavior floor (mapped to ``agent.default.<key>`` — OS1, the
    all-agents backstop) and every runtime ``default_categories`` table (agent-scope
    tables arrive ALREADY DISCRIMINATED as ``agent.<agent_name>.<cat>.*``, built by
    the declaring plugin) into ONE base-level floor, assembles the 6-level cascade (S8) with
    7a's *agent_partial* inserted as an additional agent-level source (S27), merges
    (S15), and expands (S17/S19) with *ctx*. There is NO bare ``agent.<key>`` in the
    snapshot (spec §2d / §0 L21) — the agent tier is DISCRIMINATED throughout.

    *behavior_floor* is the BARE behavior-default dict (``{d.key: d.default}``);
    *default_categories* are the already-scope-qualified category default tables
    (``{"box.bindings.rw.home": (h, d, o), ...}``) unioned across every mount
    family.

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
    ``meta.agent.<a>.name`` keys that the @meta.*-routed
    core binds (workspace / inbox) reference (spec §2c/§2d). Folded into the SAME
    floor so ``expand`` resolves the @meta.* binds ONCE (single-route). ``None`` for
    a narrow resolve.

    *prefs* are the ``pref.*`` REQUESTS (spec §2h) of the workset + box files, in
    application order. ``None`` (the default) means COLLECT THEM HERE from
    *workset_path* / *box_path* — the fail-safe default, so a caller cannot omit
    them by accident. Supplying them is a CACHE, not a second source: the one
    collector is ``settings_prefs.collect_prefs`` either way, and validation runs
    here regardless. *valid_agents* injects the agent-validity set (defaults to
    plugin discovery); tests supply their own.

    *selection_level* is the §1A **top-most input level** — above every settings
    file AND every pref (*"The COMMAND LINE is its OWN LEVEL — the highest… a
    GENERAL rule, not a carve-out"*). P7 uses it for exactly one key,
    ``system.agent``, carrying the RESOLVED selection
    (:func:`kanibako.agent_select.select_agent`) whichever of its three sources
    won — ``--agent``, the cascade, or the installed-count rule. Installing it
    ALWAYS (not only for ``--agent``) is what keeps ``@system.agent`` equal to the
    node that actually runs, which the two re-pointed §2c anchors depend on. P8
    generalises this level to every key-shadowing flag.

    ⚑ WHO MUST PASS IT, precisely — "the narrow resolves can skip it" is NOT the
    rule, and reading it that way is what cost the credential path once already:
      * **REQUIRED** by every resolve that carries the ``auth_chain`` floor
        (``_resolve_box_auth_source`` / ``_resolve_box_launch_decisions``, on the
        launch AND on stop / creds-watch / reauth / the ``--effective`` display),
        because ``meta.box.auth.workset_path`` = ``@workset.auth.path/@system.agent``
        — omit it and the per-agent credential dir collapses to the workset auth
        ROOT. Those two functions take it as a REQUIRED keyword for that reason.
      * **Not needed** by the seed / synced / image / helper narrow resolves: they
        carry no auth chain and no shipped declaration references ``@system.agent``.
      * ``None`` for a NO-AGENT box — ``system.agent`` must stay absent/``None``
        there, not be pinned to the ``"general"`` template slot.

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
    # (absent ≡ no default), matching the retired by-name resolver's terminal
    # skip. (A box/
    # workset FILE ""-suppression of an inherited default is a separate path —
    # see the module note; no shipped default table uses "".)
    #
    # OS1 (agent scope): the agent-scope default tables arrive ALREADY DISCRIMINATED
    # (``default_common()`` → ``agent.<agent>.common.plugins``, ``default_seeds()`` →
    # ``agent.<agent>.seeded.*``) — the declaring plugin builds the discriminated key
    # in :mod:`kanibako.agent_defaults`, because the snapshot agent tier is
    # DISCRIMINATED (§2d / §0 L21 — NO bare ``agent.<key>``) and the bare form must
    # not exist anywhere. A box/workset/agent file STILL overrides them by name
    # through the merge, and the adapter's active-over-default pick reads them.
    # (``agent.default.*`` from the behavior floor above is the agent tier's
    # FALLBACK — a legitimate discriminator, left as-is.)
    if default_categories:
        for key, val in default_categories.items():
            if val == "":
                continue
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
    # anchors (workset.{boxes,vault_ro,vault_rw,logs} + workset.channels.*) and the
    # RO box root the @-ref-routed core home/vault/helper_log/workset-channel binds
    # reference (spec §2c/§2g). Folded into the SAME floor so
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
    #   agent_state      — the per-agent FILE's behavior, wrapped under the active
    #                      slot, at the AGENT-FILE rung (above the empty assemble
    #                      agent.<active> level, below workset): the OLD
    #                      ``LevelView("agent", agent_cfg.state)`` precedence.
    #   agent_partial    — 7a descriptor DEFAULT delivery, the LEAST-specific agent
    #                      rung (just below agent.default) so any
    #                      agent.<active>/workset/box repoint wins.
    #   pref overlays    — the ``pref.*`` REQUESTS of the workset and box files
    #                      (spec §2h), each installed IMMEDIATELY BELOW its own
    #                      level's partial. §2h expands a level's prefs "at the
    #                      START of that level, BEFORE the level resolves", so the
    #                      level's own keys are applied AFTER and win; and the BOX
    #                      overlay precedes the WORKSET overlay, which IS
    #                      box-beats-workset by assignment order (§1A L364-367).
    #                      Nothing LEGAL contends with either: a box/workset file
    #                      may not set system.agent or agent.<a>.* at all (upward
    #                      writes, dropped by _drop_upward_scopes).
    #                      ⚑ CONSEQUENCE, recorded deliberately: because nothing
    #                      legal contends, the BELOW-vs-ABOVE placement is
    #                      currently UNOBSERVABLE — moving an overlay above its
    #                      level's partial breaks no test. It is spelled this way
    #                      because §2h says prefs expand "at the START of that
    #                      level, BEFORE the level resolves", and it becomes
    #                      observable the moment the allowlist grows to a target a
    #                      lower file may also set. Left unpinned on purpose: a
    #                      test asserting an unobservable ordering would be
    #                      asserting the implementation, not the behaviour.
    #   selection_level  — the §1A CLI-class level: ABOVE EVERYTHING (index 0).
    #                      P7 puts the RESOLVED ``system.agent`` there.
    state_partial = _agent_state_partial(agent_name, agent_state)
    # box.agent.* CATEGORY fold (spec §2b L411 / §0 L32-42): the box's same-scope
    # ⚑ THE ``box.agent.*`` CATEGORY FOLD IS GONE (P7). It existed to give a box's
    # SETTABLE ``box.agent.<category>`` tweak box-precedence inside the active
    # agent's slot. Spec §2b retires the settable mirror wholesale: ``box.agent.*``
    # is now the RO read-back ``meta.box.agent.*``, and a box tweaks its agent with
    # ``pref.agent.<agent>.<key>`` (§2h) — which is a pref overlay, already spliced
    # below. So the fold has no settable input left to fold, and removing it FLIPS
    # the transitional contest P6 pinned (a box pref now wins a CATEGORY, as it
    # already won a SCALAR): tests/test_settings_launch.py TestPrefLevelPrecedence.
    #
    # ``pref.*`` REQUESTS (spec §2h). Collected from the two pref-LEGAL files, and
    # collected HERE when the caller did not supply them, so no call path can
    # silently skip them (the seed / synced / image / helper narrow resolves must
    # see a pref on ``agent.<a>.seeded.*`` too). ``_resolve_launch_snapshot``, which
    # runs several resolves per launch, collects ONCE and passes the SAME list to
    # all of them. Validation runs in ``apply_prefs`` regardless of who collected,
    # so supplying the list cannot bypass a filter.
    #
    # ⚑ P7's SELECTION pass (``resolve_selected_agent``) is a SEPARATE read, not a
    # share: it runs BEFORE the agent is known, so there is no launch-side list to
    # hand it. That is two reads of the same two files per launch, and they cannot
    # disagree — the pair comes from ``paths._box_settings_files`` (a runtime
    # treewalk consulting no settings key) and nothing writes between them.
    requests = list(prefs) if prefs is not None else collect_prefs(
        workset_path, box_path,
    )
    # ``valid_agents`` is passed through UNRESOLVED (``None`` = "decide inside"):
    # apply_prefs reaches plugin discovery only when a request actually names
    # ``agent.*``, so a pref-free launch pays nothing. ``is None``, not falsy — an
    # empty AgentNames is a legitimate caller-supplied value.
    ws_prefs, box_prefs = apply_prefs(requests, valid_agents=valid_agents)

    levels: list[KeyStore] = []
    if selection_level:
        # §1A: the CLI-class level, ABOVE EVERYTHING (settings files AND prefs).
        levels.append(dotted_partial(dict(selection_level)))
    levels.append(base_levels[0])                       # box
    if box_prefs:
        levels.append(box_prefs)                        # box pref REQUESTS
    levels.append(base_levels[1])                       # workset
    if ws_prefs:
        levels.append(ws_prefs)                         # workset pref REQUESTS
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
    # meta.box.agent.* RO mirror (block B5, spec §2b L709). Materialize the
    # box-scoped READ-BACK of the active agent's WHOLE resolved settings subtree as
    # a COPY-on-current-engine step, AFTER expand so the values are resolved
    # terminals.
    _materialize_box_agent_mirror(expanded, active_agent=agent_name)
    if workset_anchor and _BOX_ROOT_KEY in workset_anchor:
        _assert_box_root_resolved(expanded)
    return expanded


# --------------------------------------------------------------------------- #
# Agent SELECTION — the narrow resolve that precedes the launch snapshot (P7)  #
# --------------------------------------------------------------------------- #

#: The key that names the agent a box runs (spec §2g L1187).
SELECTION_KEY = "system.agent"


def resolve_selected_agent(
    *,
    ctx: ResolveCtx,
    system_path: Path | None,
    workset_path: Path | None,
    box_path: Path | None,
    prefs: "Sequence[PrefRequest] | None" = None,
    valid_agents: "Collection[str] | None" = None,
) -> object:
    """Resolve ``system.agent`` as the settings files + their prefs give it.

    Returns the resolved value in THREE distinguishable states (the caller MUST
    keep them apart — see :mod:`kanibako.agent_select`):

    * ``str``      — a name: the stored ``system.agent``, or a ``pref.system.agent``
      request from the box (beats) or workset file (§2h);
    * ``None``     — PRESENT-``None``: an explicit ``pref.system.agent: null``
      SUPPRESSION ⇒ the NO-AGENT plain-shell box (spec §2b L703-708, D-M6). A
      present-``None`` on a SCALAR leaf is KEPT by ``_resolve_present_none``, which
      is exactly what makes this reachable — ``if value is None: continue`` anywhere
      on this path silently deletes the capability;
    * ``_MISSING`` — nothing ever set it ⇒ the caller falls through to the
      installed-count rule.

    **Why this is a SEPARATE resolve.** ``build_launch_snapshot`` needs the active
    agent BEFORE it assembles (it discriminates the agent tier, wraps the per-agent
    file's state, builds the meta-agent floor), and the active agent is now a key
    INSIDE that cascade. The pass is safe to run first because the pref-legal file
    pair comes from the runtime TREEWALK (``paths._box_settings_files``), which
    consults no settings key — see the termination note in
    :mod:`kanibako.settings_prefs`.

    **Why LENIENT expand.** ``expand`` is whole-tree, and in STRICT mode a defect
    anywhere would abort selection — e.g. a perfectly legitimate ``$AGENT`` in some
    unrelated bind source raises here, because this pass has no active agent yet
    (``ctx.agent_name`` is ``None``) and ``_resolve_var`` refuses an unset
    ``$AGENT``. LENIENT mode (``collect_errors=True``, the same arm set-time
    validation uses) RECORDS each defective leaf and omits it, so unrelated defects
    cannot decide which agent runs — while a defect ON ``system.agent`` itself is in
    the error map and is RAISED here, naming the key and the reason (§2h: *"We don't
    want to just moving on with bad settings"*). Never a silent fall-through to
    no-agent.

    No ``agent_path`` is passed: the agent-tier FILE is selected BY this key, so
    reading it here would be the chicken-and-egg this function exists to break.
    """
    requests = list(prefs) if prefs is not None else collect_prefs(
        workset_path, box_path,
    )
    ws_prefs, box_prefs = apply_prefs(requests, valid_agents=valid_agents)
    base_levels = assemble_levels(
        agent_name="",
        system_path=system_path,
        agent_path=None,
        workset_path=workset_path,
        box_path=box_path,
        floor={},
    )
    levels: list[KeyStore] = [base_levels[0]]              # box
    if box_prefs:
        levels.append(box_prefs)                          # box pref REQUESTS
    levels.append(base_levels[1])                         # workset
    if ws_prefs:
        levels.append(ws_prefs)                           # workset pref REQUESTS
    levels.extend([base_levels[4], base_levels[5]])       # system, base
    expanded, errors = expand(merge(levels), ctx, collect_errors=True)
    if SELECTION_KEY in errors:
        raise SettingsError(
            f"The agent selection key '{SELECTION_KEY}' did not resolve: "
            f"{errors[SELECTION_KEY]}. Refusing to launch rather than falling back "
            f"to a different agent — set it with `kanibako system config set "
            f"{SELECTION_KEY}=<name>`, or request one per box with "
            f"`kanibako box set pref.{SELECTION_KEY}=<name>` (spec §2h)."
        )
    return _snapshot_leaf(expanded, SELECTION_KEY)


#: The RO per-mode box-root anchor (spec §2c). Every rooted box key spells itself
#: against it, so it is the one anchor whose failure to resolve is unsurvivable.
_BOX_ROOT_KEY = "meta.box.path"
#: The SETTABLE key the box root dereferences. It is validated ALONGSIDE the root
#: because a broken source does not always produce a broken-LOOKING root — see
#: :func:`_assert_box_root_resolved`.
_BOX_STORE_KEY = "workset.boxes"


def _snapshot_leaf(snapshot: KeyStore, dotted: str) -> object:
    """Read the resolved leaf at *dotted*, or ``_MISSING``. UNBOUND protocol (S3)."""
    node: object = snapshot
    for seg in dotted.split("."):
        if not isinstance(node, KeyStore):
            return _MISSING
        node = dict.get(node, seg, _MISSING)
        if node is _MISSING:
            return _MISSING
    return node


def _assert_box_root_resolved(snapshot: KeyStore) -> None:
    """Fail LOUDLY when the box root, or the store it derives from, did not resolve.

    ⚑ A box root that resolves to nothing does NOT surface as an error on its own.
    ``box.bindings.rw.home`` is ``(@meta.box.path/home, ~)`` — an EMBEDDED ``@``-ref,
    and the embedded rule (§6b) coerces an absent / present-``None`` referent to
    ``""``. The L7 guarantee-create then ``mkdir``\\ s whatever that produced and
    mounts it OVER the box home, so the box comes up with the wrong host directory
    as its home and nothing anywhere reports an error.

    ⚑ AND THE RESULT CAN LOOK PERFECTLY VALID, which is why BOTH keys are checked.
    With ``workset.boxes`` present-``None``:

    * standalone — ``meta.box.path`` is the bare ``@workset.boxes`` (whole-value), so
      the root inherits ``None`` and the home host_src becomes the host's ``/home``.
      A root-level check catches this.
    * primary/named — ``meta.box.path`` is ``@workset.boxes/@meta.box.name``
      (embedded), so the empty substitution yields ``/mybox``: a syntactically
      perfect absolute path that no shape check would reject, pointing at a
      top-level directory that is not the box's. Only validating the SOURCE catches
      it.

    The floor values themselves are constants and cannot be ``None``, but the anchor
    dereferences the SETTABLE ``workset.boxes``, so a settings file carrying
    ``workset: {boxes: null}`` (or an empty-string terminal) still reaches this.

    Checked ONLY when the caller actually supplied the anchor in its floor fragment,
    so narrow resolves and partial-floor callers are unaffected.

    ⚑ AND A THIRD SHAPE: a root ending in ``/`` means the LEAF vanished, not the
    root. With ``meta.box.name`` empty or ``None``, the primary/named formula
    ``@workset.boxes/@meta.box.name`` yields ``<…>/boxes/`` and the home host_src
    becomes ``<…>/boxes//home`` — the BOXES DIRECTORY's home, which every box in the
    workset would then share. That is reachable today: the unregistered-primary
    fallback in ``paths._resolve_local_dir`` returns an empty name, and the launch
    passes ``proj.name`` through unexamined.

    ⚑ THE TEST IS EXISTENCE + LEAF, NOT ABSOLUTENESS — deliberately, and please do
    not "tighten" it to require a leading ``/``. That was tried: it reddens 131 tests
    in ``tests/test_commands/test_start.py``, which mock ``load_std_paths()``
    wholesale, so ``config.primary_workset`` stringifies to ``"<MagicMock …>"`` and
    the resolved root is legitimately not a real path in that context. No production
    path reaches this check non-absolute (the foundation is a real ``Path``), so the
    absoluteness arm bought nothing and cost a harness-wide false positive. What
    catches the real hazard is the §3 three-state (absent / present-``None`` /
    ``""``-terminal) plus the vanished-leaf shape below.
    """
    for key in (_BOX_STORE_KEY, _BOX_ROOT_KEY):
        value = _snapshot_leaf(snapshot, key)
        if isinstance(value, str) and value != "" and not value.endswith("/"):
            continue
        got = "absent" if value is _MISSING else repr(value)
        trailing = isinstance(value, str) and value.endswith("/")
        why = (
            "its trailing separator means the final path segment resolved to "
            "nothing (an empty @meta.box.name leaves the box root pointing at the "
            "SHARED box store, so every box in the workset would resolve the same "
            "home)"
            if trailing
            else (
                f"the box root '{_BOX_ROOT_KEY}' derives from '@{_BOX_STORE_KEY}', "
                f'so a settings file that sets workset.boxes to null / "" — or '
                f"removes it — leaves every key rooted at the box root pointing "
                f"somewhere at the filesystem root"
            )
        )
        raise SettingsError(
            f"The box store/root key '{key}' did not resolve to a usable path (got "
            f"{got}): {why}. Refusing to continue: the box home bind would otherwise "
            f"be silently mounted from the wrong host directory."
        )


# --------------------------------------------------------------------------- #
# meta.box.agent.* RO mirror materialization (block B5 — spec §2b L709)        #
# --------------------------------------------------------------------------- #
#
# Spec §2b L709: ``meta.box.agent.<key>`` is the box-scoped READ-BACK of its
# active agent's WHOLE resolved settings subtree — ``agent.<@system.agent>.<key>``
# with the ``agent.default`` fallback. Values are still READABLE here; they are
# no longer SETTABLE. Being ``meta.*`` it is RO BY CONTRACT (§0: meta ⟺ not
# settable), so it cannot be set to a dangling @-ref and no settings file can
# contribute to it. Re-materialized whenever the effective agent changes.
#
# ⮕ P7 RETIRED THE SETTABLE ``box.agent.*`` MIRROR that used to live here. It was
#   one of the three devices §2h replaced (*"Every solution is a hack/exception,
#   so I'm biting the bullet"*): it made a BOX-scope key the input to the agent
#   tier, and it made the L4.1 anchors derive from a settable key at their own
#   level. A box now tweaks its agent with ``pref.agent.<agent>.<key>`` (§2h),
#   which targets the AGENT tier properly and needs no mirror. TWO consequences,
#   both deliberate:
#     * there is no gap-FILL any more — nothing can pre-set a name under
#       ``meta.box.agent`` — so the materialization is a straight COPY;
#     * the ``box.agent.<category>`` → ``agent.<active>`` pre-merge FOLD is gone
#       with it (see build_launch_snapshot), which flips P6's transitional pin.
#
# MECHANISM (JC-B5-1 — COPY, materialized on the current engine, no resolver
# inversion). The resolved active-agent subtree only EXISTS post-merge/expand
# (the cascade keeps ``agent.default`` and ``agent.<active>`` DISCRIMINATED and the
# active-over-default value-pick is a CONSUMER step — :func:`_agent_pick_node`).
# So ``meta.box.agent.*`` is materialized AFTER ``expand`` as a deep COPY of that
# resolved effective-agent node into ``snapshot["meta"]["box"]["agent"]``:
#   (a) ``meta.box.agent.<key>`` reads back exactly what
#       ``agent.<@system.agent>.<key>`` resolved to (the effective node IS
#       ``agent.default`` overlaid by ``agent.<active>``, §2d L368 pick) — including
#       any ``pref.agent.<agent>.<key>`` the box requested, since a pref is an INPUT
#       to that resolution rather than a patch on top of it;
#   (b) NO LEAK — the materialized subtree is a FRESH deep COPY
#       (``_deep_copy_store`` leaves immutable Bind/scalar/None leaves shared but
#       never aliases a nested KeyStore), written ONLY under ``meta.box.agent.*``.
#       ``snapshot["agent"]`` is never mutated, so a later in-place edit of the
#       read-back cannot escape into the shared agent subtree.
# Re-materialization on an agent change is AUTOMATIC: ``agent_name`` is the
# launch-resolved active agent (``@system.agent`` — the stored key, a
# ``pref.system.agent`` request, ``--agent``, or the installed-count rule; see
# :mod:`kanibako.agent_select`), threaded into every snapshot build.
#
# ⚑ NO-AGENT box — WHAT ACTUALLY HAPPENS, measured, because the inherited comment
# here was WRONG twice over (it claimed a spec requirement that §2b does not state,
# and a caller behaviour no caller has):
#   * The LAUNCH passes ``agent_name="general"`` for a no-agent/shell box
#     (``start.py``: ``agent_id = with_harness(...) if target else "general"``), so
#     the blank short-circuit below does NOT fire and the mirror is materialized
#     from the §2d pick — which for ``"general"`` is the ``agent.default`` backstop
#     alone (no ``agent.general`` table exists anywhere). MEASURED on the launch
#     shape: ``meta.box.agent`` holds ``auth`` (the floor's capability key) PLUS the
#     agent.default behavior leaves (``auto_approve`` / ``bootstrap`` / ``model``).
#     That is a defensible read-back — it IS the effective subtree when the
#     effective agent is the default backstop — and NOTHING consumes those leaves
#     (the only runtime reader under ``meta.box.agent`` is ``auth.share_support``,
#     which the auth FLOOR materializes pre-expand, not this copy).
#   * A caller that passes a BLANK ``active_agent`` (tests, and any future caller
#     that wants the strict reading) gets an EMPTY mirror — the short-circuit below.
# Both shapes are PINNED (tests/test_settings_launch.py). If the strict reading is
# ever wanted at the launch too, the change belongs at the ``agent_id`` seam in
# start.py, not here — this function mirrors whatever active agent it is given.


def _materialize_box_agent_mirror(snapshot: KeyStore, *, active_agent: str) -> None:
    """Materialize ``meta.box.agent.*`` = the resolved active-agent subtree (B5).

    The box-scoped RO read-back of its active agent's WHOLE resolved settings
    subtree (spec §2b L709): a deep COPY of the resolved effective-agent node
    (``agent.default`` overlaid by ``agent.<active_agent>`` — the §2d L368 pick) is
    written under ``snapshot["meta"]["box"]["agent"]``. Mutates *snapshot* in place
    (it is the launch-local expanded tree, owned by the caller).

    *active_agent* is the launch-resolved active agent name. A BLANK name → NO
    active-agent subtree to mirror → nothing materialized. ⚑ The LAUNCH does not
    pass blank for a no-agent box; it passes ``"general"`` — see the module note
    above for the measured shape and why it is harmless.

    ⚑ The auth floor separately materializes ``meta.box.agent.auth.share_support``
    (the capability mirror, a PRE-expand floor key), so this copy must not clobber
    it: an existing name under ``meta.box.agent`` is LEFT INTACT.

    COPY (not REF) — keystone D-D / JC-B5-1: a fresh deep copy guarantees no edit
    of the read-back leaks into the shared ``agent.*`` subtree or across boxes.
    Reads/writes via the UNBOUND ``dict`` protocol (S3) so a key named ``get`` /
    ``agent`` cannot shadow.
    """
    if not active_agent or not active_agent.strip():
        # NO-AGENT box — no active agent subtree to mirror (spec). Leave
        # meta.box.agent.* absent; do NOT fall back to agent.default (that is the
        # all-agents backstop, not an ACTIVE agent the box runs).
        return
    # The PURE pick (agent.default ⊕ agent.<active>), which already carries any
    # ``pref.agent.<agent>.*`` the box requested (a pref is a cascade INPUT).
    effective = _agent_pick_node(snapshot, active_agent)
    if not dict.__len__(effective):
        # The active agent set NO category/behavior leaves (and no default backstop
        # either) — nothing to mirror; leave meta.box.agent.* absent.
        return
    meta_node = dict.get(snapshot, "meta", _MISSING)
    if not isinstance(meta_node, KeyStore):
        meta_node = KeyStore()
        snapshot["meta"] = meta_node
    meta_box = dict.get(meta_node, "box", _MISSING)
    if not isinstance(meta_box, KeyStore):
        meta_box = KeyStore()
        meta_node["box"] = meta_box
    box_agent = dict.get(meta_box, "agent", _MISSING)
    if not isinstance(box_agent, KeyStore):
        box_agent = KeyStore()
        meta_box["agent"] = box_agent
    # Copy each resolved effective-agent name, leaving intact anything already
    # there (the auth floor's ``auth.share_support`` capability mirror). A nested
    # KeyStore is filled PER NAME so the auth subtree coexists with mirrored
    # siblings.
    _mirror_fill(box_agent, effective)


def _mirror_fill(box_node: KeyStore, agent_node: KeyStore) -> None:
    """Deep gap-fill *box_node* from *agent_node*: copy each *agent_node* name the
    *box_node* does NOT already set; recurse into matching KeyStore subtrees so a
    pre-set leaf does not suppress mirrored siblings (block B5).

    A leaf already present (the auth floor's capability mirror) is LEFT INTACT.
    A name absent from *box_node* is set to a FRESH deep
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
    by name; any undeclared agent-scope scalar keys ride through verbatim (forward-compat).
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
    ``agent.default`` (so any undeclared agent-scope scalar keys survive as
    pass-through, matching the old reader's key union). Category subtrees
    (``bindings`` / ``meta`` / ``common`` / …) and ``Bind`` leaves are NOT behavior
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
    # ⚑ NO ``box.agent.*`` OVERLAY (P7). The settable box-scoped agent mirror is
    # RETIRED (spec §2b L709 — it is now the RO read-back ``meta.box.agent.*``), so
    # there is no box-scope behavior source to overlay here: a box tweaks its
    # agent's behavior with ``pref.agent.<agent>.<key>`` (§2h), which is an ordinary
    # cascade level and is therefore ALREADY resolved into the active slot below.
    # Reading ``meta.box.agent`` here instead would be a cycle — that node is
    # MATERIALIZED FROM this pick.

    if keys is None:
        # DISCOVER: the union of scalar-leaf names across the active slot and the
        # default backstop. Category subtrees / Bind leaves are filtered out
        # per-key below.
        discovered: dict[str, None] = {}
        for node in (active_node, default_node):
            if isinstance(node, KeyStore):
                for name in dict.keys(node):
                    discovered.setdefault(name, None)
        key_iter: "list[str]" = list(discovered)
    else:
        key_iter = keys

    for key in key_iter:
        # The §2d L368 active-over-default pick. A present value (incl.
        # present-None) SETS the key and shadows the default backstop below it.
        val: object = _MISSING
        if isinstance(active_node, KeyStore):
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
    reconciled ``agent.<agent>.bindings.{ro,rw}`` winners — the single-route replacement
    for ``descriptor_mounts``' MOUNT role (S27).

    *reconciled_mounts* is the full MOUNT winner list from
    :func:`reconcile_categories`; this picks the ``scope == "agent"`` /
    ``category in bindings.ro|rw`` entries (the descriptor delivery binds, now in
    the cascade via 7a's partial). For each:

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
) -> list[CategoryEntry]:
    """Walk the snapshot's category subtrees → the ``list[CategoryEntry]``
    :func:`reconcile_categories` consumes (the SAME shape the retired by-name
    resolver produced), so the by-dest reconcile pass is unchanged (§6g).

    For every ``<scope>.<category>`` subtree present it emits one entry per leaf.
    The four scopes are the SAME ``system, agent, workset, box`` apply order the
    old by-name resolver used (so a reconcile tie breaks identically), and
    every emitted entry's ``scope`` is the BARE scope token (``agent``) — the
    load-bearing scope identity (§7), NOT the snapshot's agent discriminator.

    The AGENT scope is DISCRIMINATED in the snapshot (``agent.default.*`` /
    ``agent.<active>.*``, spec §2d / §0 L21 — NO bare ``agent.<key>``). This
    consumer does the §2d L368 active-over-default value-pick PER NAME: it builds an
    EFFECTIVE agent node = ``agent.default`` overlaid by ``agent.<active_agent>``
    (the active slot wins each name it sets; ``agent.default`` fills the gaps), then
    walks that one effective node as the (bare) ``agent`` scope. The descriptor
    delivery binds (7a) live under the active slot; the
    all-agents declared defaults live under ``agent.default`` — so this pick is the
    delivery-side analog of :func:`effective_behavior`'s read.

    host_src is read from the expanded ``Bind`` (already host-resolved at build)
    and used AS-IS: NOTHING is prefixed here, ever. A stored source resolves ON ITS
    OWN (spec §2a L474-486); the abstract categories are rooted at DECLARATION, and
    an assembly-time root-prepend is the shape §2a L487-517 calls FORBIDDEN. Do not
    reintroduce a per-scope root table here — a structural test scans for it. box_dest is
    resolved BOX-side here (this is a ``box_dest`` consumer, B6): ``~`` →
    ``GUEST_HOME`` and ``$XDG`` against *box_ctx* — matching the old
    ``space="guest"`` pass — so reconcile keys on the SAME absolute dest. ``env``
    carries its VAR name in ``box_dest`` and its value in ``options``; ``masks`` is
    value-less (one entry per masked dest). Reads via the UNBOUND ``dict`` protocol
    (S3).
    """
    collected: list[tuple[tuple[int, str, str], CategoryEntry]] = []
    scope_order = {"system": 0, "agent": 1, "workset": 2, "box": 3}

    def _box_dest(raw: str) -> str:
        return expand_expr(raw, space="guest", ctx=box_ctx, lookup=_no_lookup)

    for scope in _SCOPES:
        if scope == "agent":
            # §2d active-over-default pick: effective agent node = agent.default
            # overlaid by agent.<active>. The emitted ``CategoryEntry.scope`` stays the
            # BARE ``agent`` token (it is the precedence identity); the snapshot's
            # own agent tier is DISCRIMINATED (``agent.<active>``) — there is no bare
            # ``agent.*`` anywhere outside an explicit agent name or
            # ``default``. A box's ``pref.agent.<agent>.<category>`` requests (§2h)
            # merged INTO agent.<active> as an ordinary cascade level, so the PURE
            # pick already carries them — NO post-expand overlay (single-route).
            # (P7: this used to say the same of the retired settable ``box.agent.*``
            # mirror and its pre-merge fold; both are gone.)
            #
            # ⚑ The undeclared-shape REFUSAL runs on the RAW TIERS, before the pick,
            # so its message can name the DISCRIMINATED key the user actually wrote
            # (``agent.default.bindings`` vs ``agent.<active>.bindings``). Checking
            # the merged node instead would only be able to say ``agent.bindings``
            # — a bare form that is NOT a key (§0 L21), i.e. an error message
            # instructing the reader toward a shape the keyspace forbids.
            agent_node = dict.get(snapshot, "agent", _MISSING)
            if isinstance(agent_node, KeyStore):
                for tier in dict.keys(agent_node):
                    tier_node = dict.__getitem__(agent_node, tier)
                    if isinstance(tier_node, KeyStore):
                        _assert_declared_categories(f"agent.{tier}", tier_node)
            scope_node = _agent_pick_node(snapshot, active_agent)
            decl_scope_fn = _agent_decl_scope_fn(agent_node, active_agent)
        else:
            scope_node = dict.get(snapshot, scope, _MISSING)
            if isinstance(scope_node, KeyStore):
                _assert_declared_categories(scope, scope_node)
            decl_scope_fn = _fixed_decl_scope_fn(scope)
        if not isinstance(scope_node, KeyStore):
            continue
        order = scope_order[scope]
        _emit_scope_node(
            collected, scope_node, order=order, scope=scope,
            box_dest_fn=_box_dest, decl_scope_fn=decl_scope_fn,
        )

    collected.sort(key=lambda pair: pair[0])
    return [entry for _, entry in collected]


def _fixed_decl_scope_fn(scope: str):
    """The DECLARATION-scope resolver for a non-agent scope: always *scope*.

    A ``system`` / ``workset`` / ``box`` key is spelled with its bare scope token,
    so the declaration scope and the precedence scope are the same string. The
    agent tier is the only one where they can differ — see
    :func:`_agent_decl_scope_fn`.
    """
    def decl(category: str, name: str) -> str:
        return scope
    return decl


def _agent_decl_scope_fn(agent_node: object, active_agent: str):
    """The DECLARATION-scope resolver for the agent tier: which TIER declared it.

    :func:`_agent_pick_node` collapses ``agent.default`` and ``agent.<active>``
    into ONE effective node per NAME before emission, and the emitted
    ``CategoryEntry.scope`` is the BARE ``agent`` precedence token. But an entry's
    declared KEY must be DISCRIMINATED: a bare ``agent.<category>.<name>`` is not
    a key at all (spec §0 L21), so a message or a ``meta.derived.*`` key spelled
    that way would name a shape the keyspace forbids and point a reader at
    something they cannot write.

    So recover the tier the same way the pick decides it, and from the same RAW
    tiers ``snapshot_category_entries`` already walks: a leaf declared by the
    ACTIVE slot came from ``agent.<active>``; otherwise it came from
    ``agent.default``, which is the only other tier that can have contributed it.
    This reads the raw tiers directly and does NOT thread per-leaf provenance
    through :func:`_overlay_into` — the pick's own rule is enough to answer it.
    """
    active_tier = (
        dict.get(agent_node, active_agent, _MISSING)
        if isinstance(agent_node, KeyStore) else _MISSING
    )

    def decl(category: str, name: str) -> str:
        node: object = active_tier
        for seg in (*category.split("."), name):
            if not isinstance(node, KeyStore):
                return "agent.default"
            node = dict.get(node, seg, _MISSING)
        if node is _MISSING:
            return "agent.default"
        return f"agent.{active_agent}"

    return decl


def _agent_pick_node(snapshot: KeyStore, active_agent: str) -> KeyStore:
    """The PURE active-over-default agent pick = ``agent.default`` overlaid by
    ``agent.<active_agent>`` (the §2d L368 value-pick), WITHOUT the box.agent.*
    overlay.

    Returns a FRESH ``KeyStore`` shaped like a single (bare) agent scope node — its
    ``bindings.{ro,rw}`` / ``caches`` / ``seeded`` / ``common`` / ``synced`` /
    ``masks`` / ``env`` subtrees + behavior leaves holding the per-name winner: the
    active slot's leaf wherever it set that name, else the ``agent.default`` leaf.
    The overlay is PER NAME (deep) so an active ``agent.<active>.common.cache`` and a
    default-only ``agent.default.common.plugins`` BOTH survive. A present-``None``
    reset was already OMITted by the merge (§3 / §6e), so it never reaches here.

    This is the subtree the ``meta.box.agent.*`` RO mirror is MATERIALIZED from
    (block B5, ``_materialize_box_agent_mirror``) — it must NOT itself read
    ``meta.box.agent.*`` (no chicken-and-egg). It is ALSO the effective agent node
    the category adapter (:func:`snapshot_category_entries`) walks: a box's
    ``pref.agent.<agent>.<category>`` requests (§2h) merged INTO ``agent.<active>``
    as an ordinary cascade level, so this PURE pick already carries them — the box's
    tweak is live in category resolution with NO post-expand overlay (single-route).
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


def _assert_declared_categories(key_prefix: str, node: KeyStore) -> None:
    """Refuse every UNDECLARED category shape under ONE scope node (spec §2d
    L906-910), naming the key with the prefix it is really written under.

    *key_prefix* is the DISCRIMINATED key prefix — a bare scope token for
    system/workset/box, and ``agent.default`` / ``agent.<active>`` for the agent
    tier. That is the whole reason this runs on the RAW tiers rather than on the
    merged agent node: an error that said ``agent.bindings`` would be naming a
    shape §0 L21 forbids, and would point the reader at a key they cannot write.

    ⚑ COVERAGE IS THE THREE BIND FAMILIES ONLY — ``bindings.{ro,rw}`` and the
    ``caches``/``seeded``/``common``/``synced`` leaf categories. ``env``, ``masks``
    and ``secret_path`` keep their pre-existing SILENT SKIP of a non-``KeyStore``
    node: they were outside the boundary approved for this change, and widening
    them is a decision (their shapes differ — ``masks`` is keyed-by-dest, ``env`` /
    ``secret_path`` are scalar-valued), not an omission to fix in passing. Tracked
    for P5 with the rest of the undeclared-shape sweep.
    """
    bindings = dict.get(node, "bindings", _MISSING)
    if bindings is not _MISSING:
        _require_category_node(key_prefix, "bindings", bindings)
        for name in dict.keys(bindings):
            if name not in ("ro", "rw"):
                raise SettingsError(
                    f"{key_prefix}.bindings.{name} is an ARM-LESS binding, which is "
                    f"not a declared key; bindings are declared per arm — "
                    f"{key_prefix}.bindings.ro.{name} / "
                    f"{key_prefix}.bindings.rw.{name} (spec §2d L906-910)"
                )
        for mode in ("ro", "rw"):
            mode_node = dict.get(bindings, mode, _MISSING)
            if mode_node is not _MISSING:
                _require_category_node(key_prefix, f"bindings.{mode}", mode_node)
    for category in _BIND_LEAF_CATEGORIES:
        cat_node = dict.get(node, category, _MISSING)
        if cat_node is not _MISSING:
            _require_category_node(key_prefix, category, cat_node)


def _require_category_node(key_prefix: str, category: str, node: object) -> None:
    """Refuse a VALUE sitting at a CATEGORY ROOT (spec §2d L906-910).

    A category token names a NAMESPACE of per-name entries; it is not itself a
    declared key, so a scalar / :class:`Bind` / list there is an UNDECLARED shape.
    Under the closed-keyspace rule (spec §0) an undeclared key is an ERROR that
    names itself — never a silent accept. The check runs against the ASSEMBLED
    snapshot, so it catches such a value from any origin that reaches it — a plugin
    defaults table, a workset or box YAML, a ``config set`` — in ONE place.

    ⚑ ONE ROUTE DOES NOT REACH IT: ``workset share list --effective`` returns
    "No bindings configured" and never resolves when a workset file's ONLY
    ``bindings`` content is the malformed value, because its raw-shares reader
    walks for per-name leaves and finds none. The launch path still refuses.

    Before P3 these shapes were SILENTLY DROPPED by ``isinstance(x, KeyStore)``
    guards — the user's binding simply never appeared, with nothing said.

    PRESENT-BUT-EMPTY (``bindings: {}`` / ``common: {}``) is NOT an error: an empty
    node is byte-indistinguishable from an absent one after ``assemble``, so
    erroring would trap a no-op. §2d itself calls the ``agent.default.bindings |
    {}`` row "documentation of intent, not a required default".
    """
    if isinstance(node, KeyStore):
        return
    if "." in category:
        # An ARM (``bindings.ro``) — names live under it, values do not.
        declared = f"{key_prefix}.{category}.<name>"
    else:
        declared = (
            f"{key_prefix}.bindings.{{ro,rw}}.<name>" if category == "bindings"
            else f"{key_prefix}.{category}.<name>"
        )
    raise SettingsError(
        f"{key_prefix}.{category} is a value at a CATEGORY ROOT "
        f"({type(node).__name__}: {node!r}), which is not a declared key; "
        f"declare {declared} (spec §2d L906-910)"
    )


def _emit_scope_node(
    collected: list[tuple[tuple[int, str, str], CategoryEntry]],
    scope_node: KeyStore,
    *,
    order: int,
    scope: str,
    box_dest_fn,
    decl_scope_fn,
) -> None:
    """Emit every category entry under ONE (bare) scope NODE.

    *scope_node* is a single scope's category subtree (``snapshot.<scope>`` for a
    non-agent scope; the effective agent node for the agent scope). *scope* is the
    BARE scope token used for the emitted ``CategoryEntry.scope`` — the load-bearing
    precedence identity. Reads via unbound ``dict`` ops (S3).

    *decl_scope_fn* ``(category, name)`` answers the OTHER scope question: which
    DISCRIMINATED scope the entry was DECLARED under, for
    ``CategoryEntry.key``. The two coincide everywhere but the agent tier
    (``_agent_decl_scope_fn``), and they are different facts — collapsing them
    would either lose the precedence token or emit a bare ``agent.<category>``
    key, which is not a key (spec §0 L21).

    EMISSION ONLY. The undeclared-shape refusal ran earlier, in
    :func:`snapshot_category_entries`, against the RAW tiers — so it could name the
    discriminated key. By the time a node reaches here every category token it
    carries is a ``KeyStore``, and the ``isinstance`` skips below are unreachable
    guards rather than the silent drops they were before P3.
    """
    # bindings.{ro,rw}
    bindings = dict.get(scope_node, "bindings", _MISSING)
    if isinstance(bindings, KeyStore):
        for mode in ("ro", "rw"):
            mode_node = dict.get(bindings, mode, _MISSING)
            if not isinstance(mode_node, KeyStore):
                continue
            category = f"bindings.{mode}"
            for name in dict.keys(mode_node):
                bind = dict.__getitem__(mode_node, name)
                _emit_bind(
                    collected, order, scope, category, name, bind, box_dest_fn,
                    key=f"{decl_scope_fn(category, name)}.{category}.{name}",
                )

    # caches / seeded / common / synced
    for category in _BIND_LEAF_CATEGORIES:
        cat_node = dict.get(scope_node, category, _MISSING)
        if not isinstance(cat_node, KeyStore):
            continue
        for name in dict.keys(cat_node):
            bind = dict.__getitem__(cat_node, name)
            _emit_bind(
                collected, order, scope, category, name, bind, box_dest_fn,
                key=f"{decl_scope_fn(category, name)}.{category}.{name}",
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
                    key=f"{decl_scope_fn('masks', raw_dest)}.masks.{raw_dest}",
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
                    key=f"{decl_scope_fn('env', var)}.env.{var}",
                ),
            ))

    # secret_path — the SECRET category (spec §2a, 2026-07-06): a scalar host PATH
    # keyed by VAR, delivered as a ro MOUNT to SECRET_MOUNT_DIR/{VAR}. Modeled on the
    # env branch but MOUNT: host_src = the scalar path (already host-expanded by the
    # expand pass — a scalar leaf is expanded host-side, ``~``/``$VAR``/@-refs), and
    # box_dest = the fixed in-box secrets path. The reconcile pass picks the per-VAR
    # winner (box over workset) by identical box_dest; start.py emits the ro Mount +
    # the box-side export shim — kanibako NEVER reads the file VALUE. options="ro"
    # (NO ``:U`` chown of the host secret). name = VAR (the shim exports it).
    secret = dict.get(scope_node, "secret_path", _MISSING)
    if isinstance(secret, KeyStore):
        for var in dict.keys(secret):
            path_val = dict.__getitem__(secret, var)
            if path_val is None:
                continue  # a reset secret_path has no path to mount.
            box_dest = f"{SECRET_MOUNT_DIR}/{var}"
            sort_key = (order, "secret_path", var)
            collected.append((
                sort_key,
                CategoryEntry(
                    category="secret_path",
                    scope=scope,
                    box_dest=box_dest,
                    host_src=(
                        path_val if isinstance(path_val, str) else str(path_val)
                    ),
                    delivery="MOUNT",
                    options="ro",
                    name=var,
                    key=f"{decl_scope_fn('secret_path', var)}.secret_path.{var}",
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
    *,
    key: str,
) -> None:
    """Append one bind-shaped :class:`CategoryEntry` (MOUNT or COPY) for *bind*.

    *bind* is the expanded :class:`Bind` leaf (host already resolved). A
    present-``None`` / mistyped leaf cannot reach here — the merge OMITs a
    present-None bind (§3/§6e) and the views' S22 contract holds — so a non-Bind
    leaf is a build-invariant breach; raise loudly (never type-launder).
    The host_src is used AS-IS — a stored source resolves on its own (spec §2a
    L474-486) and NOTHING is prefixed here; *box_dest_fn* resolves box-side.

    *key* is the DISCRIMINATED declaration key the caller built from
    ``decl_scope_fn`` — carried on the entry for the collision messages and the
    ``meta.derived.*`` materialisation, and used here so even the malformed-leaf
    raise names the key the user actually wrote.
    """
    if not isinstance(bind, Bind):
        raise SettingsError(
            f"category {key} is {type(bind).__name__}, "
            f"expected a Bind (present-None binds are omitted at build, §3/§6e)"
        )
    delivery = _DELIVERY[category]
    host_src = bind.host
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
            key=key,
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

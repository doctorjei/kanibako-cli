"""Launch-time settings snapshot — the ONE resolve per launch (block 7b).

The LIVE read-path: ``commands/start.py`` builds ONE resolved
:class:`~kanibako.settings.keystore.KeyStore` snapshot per launch here, via the
committed KeyStore pipeline (``assemble_levels`` → ``merge`` → ``expand``), and
BOTH the behavior reads AND the CATEGORY delivery read from that SINGLE snapshot
(S12 WRITE-ONCE — resolve ONCE, read many).

Two halves. The first builds FLOORS — ``{dotted_key: value}`` fragments the caller
folds into :func:`build_launch_snapshot`'s one floor, so ``expand`` resolves every
``@``-ref chain ONCE (single-route, NO second resolver). The second READS the
expanded snapshot: behavior, launch grammar, auth source, and the category adapter
that turns the snapshot's category subtrees into the one ``list[CategoryEntry]``
every delivery seam consumes (§6g).

**Authority:** ``specs/settings-keyspace-1.8.0.md`` — §0 (the CLOSED keyspace), §1,
§2 (the cascade), §2a (the categories), §2c (worksets + box bindings per mode).
⚑ **The spec is the LIVE authority; read it first.** SEAMS
S7/S8/S9/S12/S14/S17/S20/S26/S27 + OS1.

Prose: ``llm-docs/kanibako/settings/settings_launch.py.md`` — the key models, the
per-mode anchor tables, the level-splice rungs, and the archived
``keystore-design.md`` caveat.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Collection,
    Final,
    Literal,
    Mapping,
    NamedTuple,
    Sequence,
)

if TYPE_CHECKING:
    from kanibako.targets.base import PluginDescriptor

from kanibako.agent_ref import harness_of
from kanibako.settings.agent_file import AgentFileLevel
from kanibako.settings.config import AGENT_META_FILE, WORKSET_META_FILE
from kanibako.settings.kb_store import SCOPE_CONTAINMENT, Bind, BindEntry
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_assemble import assemble_levels, dotted_partial
from kanibako.settings.settings_categories import (
    _DELIVERY,
    SECRET_MOUNT_DIR,
    CategoryEntry,
    _bind_options,
)
from kanibako.settings.settings_cli_level import guard_cli_level
from kanibako.settings.settings_expand import expand
from kanibako.settings.settings_keyspace import (
    render_store_path,
    undeclared_store_paths,
)
from kanibako.settings.settings_keyspace_probe import keyspace_verdict
from kanibako.settings.settings_keyspace_probe import observe as observe_keyspace
from kanibako.settings.settings_merge import merge
from kanibako.settings.settings_prefs import PrefRequest, apply_prefs, collect_prefs
from kanibako.settings.settings_resolve import ResolveCtx, SettingsError, expand_expr


# The bind-shaped category tokens that ARE the terminal key — the snapshot's
# ``<scope>.<category>`` node IS the dest-keyed ``BindMap``. ⚑ ``bindings`` is the
# odd one out (its map sits under an ``ro`` / ``rw`` ARM) and is deliberately NOT
# folded in: the difference is the DEPTH of the node, which a shared set would hide.
_BIND_LEAF_CATEGORIES: frozenset[str] = frozenset(
    {"caches", "seeded", "common", "synced"}
)
# Aliases the single-source scope-containment tuple (kb_store) so this consumer
# never re-declares the scope set. Order is not load-bearing: the emit loop re-sorts
# by its own ``scope_order`` map.
_SCOPES: tuple[str, ...] = SCOPE_CONTAINMENT

#: The dotted-key TAILS a DEST-KEYED bind map can sit at in a default-category floor
#: table — the ``bindings`` ARMS plus each of the four terminal categories. ONE tuple,
#: so the per-entry ``""``-suppression in the floor fold cannot drift from the reader.
_BIND_FLOOR_TAILS: tuple[str, ...] = (".bindings.ro", ".bindings.rw") + tuple(
    f".{c}" for c in sorted(_BIND_LEAF_CATEGORIES)
)


def _is_bind_floor_key(key: str) -> bool:
    """Does the floor key *key* address a whole DEST-KEYED bind map?

    A floor key is always scope-qualified, so the TAIL test cannot match a bare
    ``common``.
    """
    return key.endswith(_BIND_FLOOR_TAILS)


# --------------------------------------------------------------------------- #
# Snapshot build — the ONE resolve per launch                                 #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Auth 3-tier SHARING chain (spec §2a/§2b/§2c/§2d — 2026-07-01 redesign)      #
# --------------------------------------------------------------------------- #
#
# A global/workset/box SHARING model that COMPOSES: a box can be global-shared
# AND/OR workset-shared. The FINAL KEY MODEL, the stores, and the standalone
# degeneration are in the llm-doc.
#
# ‼ ENABLE COMPUTATION: the spec writes the two box enables as ``%@support &&
# @allow%``, but ``expand`` resolves ONLY @-refs / $VAR / ~ — it does NOT evaluate
# ``&&``. So the floor materializes the box ENABLE as a plain per-tier bool DEFAULT
# plus the resolvable INPUTS, and :func:`resolve_auth_source` ANDs support && allow
# && box_enable in PYTHON. Folding ``&&`` into the engine is a deferred
# generalization.

#: The GLOBAL-share gate default (spec §2g / §2b). The single host-wide allow flag.
_SYSTEM_SHARE_ALLOWED_KEY = "system.auth.share_allowed"


def auth_chain_floor(
    *,
    mode: str,
    agent_name: str,
) -> dict[str, object]:
    """Build the auth 3-tier SHARING chain floor keys for *mode*.

    The ``{dotted_key: value}`` floor fragment for the spec's ``auth.*`` sharing
    chain (§2a/§2b/§2c/§2d), folded into ``build_launch_snapshot``'s floor so
    ``expand`` resolves the chain ONCE (single-route). *mode* is the box's
    :class:`~kanibako.settings.paths.BoxMode` value, passed as a plain string to
    avoid a paths import.

    ⚑ The ``meta.agent.<agent>.auth.share_support`` CAPABILITY is PLUGIN-set and
    rides the meta identity floor, NOT this one. Key-by-key notes: the llm-doc.
    """
    floor: dict[str, object] = {
        # The GLOBAL-share gate — the single host-wide allow flag (settable).
        _SYSTEM_SHARE_ALLOWED_KEY: True,
        # The box-scoped MIRROR of the active agent's capability (RO / meta). The
        # spec's ``@meta.agent.<@system.agent>...`` NODE-SELECTOR notation is not
        # expressible by the resolver, so the selected node is interpolated here and
        # the resulting @-ref is followed on a literal path (llm-doc).
        #
        # ⚑ A BLANK agent is pinned to the LITERAL ``False``, never spelled as a ref:
        # ``@meta.agent..auth.share_support`` is MALFORMED, resolves to a leftover
        # string, and crashes ``resolve_auth_source``'s strict ``as_bool``. No caller
        # passes blank today, but P7 made ``""`` a MEANINGFUL value (the D-M6
        # suppression), so the trap is one careless caller away.
        "meta.box.agent.auth.share_support": (
            f"@meta.agent.{agent_name}.auth.share_support"
            if agent_name and agent_name.strip()
            else False
        ),
        # The two INDEPENDENT box ENABLE knobs — settable per-tier opt-out defaults
        # (True = opt in). resolve_auth_source ANDs each with the mirrored capability
        # and the relevant allow flag.
        "box.auth.global_enabled": True,
        "box.auth.workset_enabled": True,
        # This box's per-agent WORKSET source root — the RO DERIVED anchor (change 8),
        # ⚑ SPELLED EXACTLY AS THE SPEC (§2c) rather than interpolated in Python; it
        # is a CONSTANT, the per-box variation arriving through the §1A selection
        # level applied BEFORE this one. No braces needed, both refs EMBEDDED.
        "meta.box.auth.workset_path": "@workset.auth.path/@system.agent",
    }
    if mode == "standalone":
        # A lone box has no workset group → the workset allow keys are the LITERAL
        # False, so the workset tier's Python AND is false regardless of the knob.
        floor["workset.auth.share_allowed"] = False
        floor["workset.auth.global_sync"] = False
        # ⚑ Both anchors pinned None (defensive root-cause fix): otherwise
        # ``@workset.auth.path/<agent>`` resolves against the absent path key and
        # expands to the literal ``/<agent>`` — an @-ref to an absent key renders
        # ``""``, not a drop — garbage the credsync dir-creation would mkdir against
        # the host ROOT.
        floor["workset.auth.path"] = None
        floor["meta.box.auth.workset_path"] = None
    else:
        # PRIMARY / NAMED (ALL WORKSETS): workset allow defaults to the system
        # gate; the workset dir syncs UP to global by default.
        floor["workset.auth.share_allowed"] = "@system.auth.share_allowed"
        floor["workset.auth.global_sync"] = "@system.auth.share_allowed"
        floor["workset.auth.path"] = "@meta.workset.path/auth"
    return floor


# --------------------------------------------------------------------------- #
# meta.runtime.* materialization (block B1 — spec §1A, 2026-06-29h)           #
# --------------------------------------------------------------------------- #
#
# The spec's RUNTIME-RESOLVED identity anchors (§1A; §0 meta.* is a TOP-LEVEL
# protected RO group), surfaced as REAL ``@``-referenceable keys via the same
# floor-injection pattern the auth chain uses. The per-mode values are ALREADY
# computed at launch (``proj.mode`` / ``proj.group.root`` / the resolved project
# dir). Then the SINGLE-SOURCE re-root of meta.workset.{path,settings,name} and
# meta.box.mode, which resolve transitively in the ONE expand pass.
# The key table, the cut ``ws_settings`` alias, and the chains: the llm-doc.


def meta_runtime_floor(
    *,
    mode: str,
    ws_name: str,
    ws_root_literal: str | None = None,
) -> dict[str, object]:
    """Build the ``meta.runtime.*`` + re-rooted ``meta.*`` floor keys (block B1).

    *mode* is the box's :class:`~kanibako.settings.paths.BoxMode` value, as a plain
    string to avoid a paths import. *ws_name* is the workset partition TOKEN (§1A),
    SINGLE-SOURCED on :func:`kanibako.channels.channels.workset_name_token` and
    threaded in by the caller — the SAME token that drives the channel partition, so
    the two cannot drift. *ws_root_literal* is the resolved workset-root path STRING,
    REQUIRED for ``named`` / ``standalone`` and IGNORED for ``primary`` (which uses
    the ``@config.primary_workset`` @-ref so the value live-propagates from the
    Layer-1 foundation).

    The re-rooted keys are UNIFORM across modes and construct-set RO per §0, so the
    floor is their sole source. Per-key detail: the llm-doc.
    """
    floor: dict[str, object] = {}

    # meta.runtime.project_type — the resolved mode token (spec §1A).
    floor["meta.runtime.project_type"] = mode

    # meta.runtime.ws_name — the workset partition TOKEN (spec §1A):
    #   primary → __PRIMARY__ · named → <detected name> · standalone → __STANDALONE__.
    floor["meta.runtime.ws_name"] = ws_name

    # meta.runtime.ws_root (spec §1A):
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

    # Single-source re-root (spec §1A; §2c) — UNIFORM all modes.
    floor["meta.workset.path"] = "@meta.runtime.ws_root"
    # ⚑ The SPEC's own spelling (§2c), chaining through the anchor set one line up.
    # Spelling it off @meta.runtime.ws_root would resolve to the byte-identical value
    # but DIVERGE from the spec, and the spec is authority.  ⚑ The FILENAME is drawn
    # from its one carrier, exactly as the agent-tier formula below does — the spec
    # fixes the @-anchor, not a hand-typed leaf.
    floor["meta.workset.settings"] = f"@meta.workset.path/{WORKSET_META_FILE}"
    # The SINGLE SOURCE for the partition token; block B2 no longer sets it directly.
    floor["meta.workset.name"] = "@meta.runtime.ws_name"
    # The RO identity anchor surfacing the runtime mode (spec §2b; was the settable
    # box.mode config-set key, dropped this block).
    floor["meta.box.mode"] = "@meta.runtime.project_type"

    return floor


# --------------------------------------------------------------------------- #
# meta.* IDENTITY-ANCHOR materialization (block B2 — spec §2c/§2d, §0)        #
# --------------------------------------------------------------------------- #
#
# B2 materializes the REMAINING construct-time IDENTITY anchors as RO floor keys
# and ROUTES the eligible core binds through @meta.* refs, so a bind host_src
# RESOLVES via the snapshot instead of being injected as a proj-attr literal at the
# assembly seam (the single-route payoff, spec §0). The key table is in the llm-doc.
#
# ⚑ EQUIVALENCE IS THE BAR (JC-B2-4). Each materialized identity key is the RESOLVED
# LITERAL the launch already computes — NOT a re-derivation via the spec's nested
# @workset.* chain. Holding the resolved literal guarantees the @meta.*-routed bind
# expands to the byte-identical host_src the proj-attr injection produced.


def meta_agent_path_floor(agent_name: str) -> dict[str, object]:
    """The agent STORE-ROOT anchors ``meta.agent.<a>.path`` for *agent_name*.

    ⚑ THE single builder for this key, shared by the launch floor
    (:func:`meta_identity_floor`) and the ``config set`` SET-TIME validation
    snapshot. That sharing is load-bearing: ``config set``'s refusal message tells a
    user to spell an abstract-category source as
    ``@meta.agent.<agent>.path/<category>/<name>``, and if the set-time snapshot did
    not carry the key, the very value the tool just recommended would be rejected as
    a dangling ``@``-reference.

    ⚑ NODE **and** HARNESS are both materialized: ``load_common`` keys its entries on
    the plugin's ``Target.name`` (the HARNESS) while callers pass the ACTIVE NODE, and
    on a persona box those differ — materializing only the node leaves the
    harness-keyed refs DANGLING. The harness entry is INTENTIONALLY PARTIAL (a
    ``path``, no ``name`` / ``auth.share_support``); the llm-doc says why that
    asymmetry is inert and must not be "fixed".
    """
    return {
        f"meta.agent.{store_agent}.path": f"@config.agents/{store_agent}"
        for store_agent in {agent_name, harness_of(agent_name)}
    }


def meta_agent_grammar_floor(
    agent_name: str, descriptor: "PluginDescriptor | None"
) -> dict[str, object]:
    """The plugin-set LAUNCH-GRAMMAR anchors ``meta.agent.<a>.{mode,exec}`` (B5).

    THE single descriptor→keyspace seam for the invocation grammar (spec §2d): the
    INTERACTIVE ``mode`` map plus the STANDALONE one-shot ``exec`` fragment, the
    latter omitted when the descriptor declares no ``exec`` operation.

    ⚑ REPLACEMENT, not a second path: after B5 NOTHING reads ``descriptor.mode`` /
    ``descriptor.operations`` at argv-assembly time — the descriptor feeds the
    keyspace HERE and nowhere else. Two sources for one argv fragment is the drift
    shape this arc exists to kill.

    Keyed on the DISCRIMINATOR (the ACTIVE node); a descriptor-less agent
    materializes nothing and the launch takes the no-agent path.
    """
    if descriptor is None:
        return {}
    floor: dict[str, object] = {
        f"meta.agent.{agent_name}.mode": {
            key: list(fragment) for key, fragment in descriptor.mode.items()
        },
    }
    exec_op = descriptor.operations.get("exec")
    if exec_op is not None:
        floor[f"meta.agent.{agent_name}.exec"] = list(exec_op.fragment)
    return floor


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

    Every value is the RESOLVED LITERAL the launch already computes (the box name on
    ``proj.name``, the workspace source, the channel partition addresses from
    :func:`kanibako.channels.channels.box_channel_addresses`, the plugin-set agent
    name), so a bind re-pointed to ``@meta.box.workspace`` / ``@meta.box.inbox``
    expands to the byte-identical host_src the old proj-attr injection produced
    (JC-B2-4 equivalence bar).

    *share_workset* is ``None`` for STANDALONE (no workset-local channels, §2c) →
    a whole-value ``None`` terminal, and the ONLY standalone ``None`` terminal here.
    *box_settings* is the RO box-TIER settings-file anchor, UNIFORM in EVERY mode and
    single-sourced with the cascade's own box-tier path so the two cannot drift; it
    stays optional for narrow resolves that materialize no box tier.

    *agent_name* is the cascade discriminator (``install.name``); *agent_real_name*
    is the plugin's own value. ⚑ The STORE-ROOT anchor is keyed on the DISCRIMINATOR,
    not the real name — the store dir is ``agents/<discriminator>/``, which is what
    ``agent_settings_path`` and the persona shim use. Both ``None`` for a NO-AGENT
    box. Per-key detail: the llm-doc.
    """
    floor: dict[str, object] = {
        # Box identity (spec §2c). ⚑ The box name is REUSED from ``proj.name``
        # (JC-B2-2): standalone's <kuid>_%leaf% is composed LIVE in
        # ``resolve_standalone_project``, and B2 does NOT re-compose or regenerate it.
        "meta.box.name": box_name,
        # The in-box workspace SOURCE literal (routed to box.bindings.rw.workspace).
        "meta.box.workspace": project_path,
        # This box's own channel partition addresses (inbox routed to
        # box.bindings.rw.inbox; the two share dirs are anchors for parity).
        "meta.box.inbox": inbox,
        "meta.box.share_global": share_global,
        "meta.box.share_workset": share_workset,
        # The RO box-TIER settings-file anchor — the file the cascade reads and
        # `config set` writes.
        "meta.box.settings": box_settings,
        # meta.workset.name is NOT set here: it anchors into meta.runtime.ws_name.
    }
    # The agent identity key (spec §2d) — REQUIRED when an agent exists, under
    # the agent's discriminated slot. A NO-AGENT box omits it.
    if agent_name is not None:
        floor[f"meta.agent.{agent_name}.name"] = (
            agent_real_name if agent_real_name is not None else agent_name
        )
        # The agent's STORE ROOT — see :func:`meta_agent_path_floor`.
        floor.update(meta_agent_path_floor(agent_name))
        # The agent-tier SETTINGS cascade FILE anchor (spec §2d): the spec's own
        # formula, resolved transitively through the sibling ``path`` anchor — the
        # SAME file ``agent_settings_path`` composes.
        floor[f"meta.agent.{agent_name}.settings"] = (
            f"@meta.agent.{agent_name}.path/{AGENT_META_FILE}"
        )
        # ⚑ The agent's credential-SHARING CAPABILITY: plugin-set, RO — the hard
        # floor a user can't fake. The auth chain's mirror views UP to this key, so
        # it must be present whenever an agent exists.
        floor[f"meta.agent.{agent_name}.auth.share_support"] = bool(
            agent_auth_share_support
        )
    return floor


# --------------------------------------------------------------------------- #
# LAYOUT-anchor materialization: workset roots + RO BOX ROOT (spec §2a/§2c)   #
# --------------------------------------------------------------------------- #
#
# The other half of the single-route payoff: it materializes the workset-scope PATH
# anchors the spec's §2c binds reference (workset.{boxes,vault_ro,vault_rw,logs} +
# the workset-local channels) and the RO per-mode BOX ROOT ``meta.box.path``, as REAL
# @-referenceable floor keys. JC-B2b-1: they do NOT exist as resolvable snapshot keys
# otherwise — resolve_system_paths derives only the PRIMARY pseudo-keys into
# StandardPaths, and there is no workset.* tier in the snapshot.
#
# ⚑ WHERE THE PER-MODE VARIATION LIVES (spec §2c): HERE and nowhere downstream, so
# every rooted key and the box home spell themselves ONCE against ``@meta.box.path``
# / ``@workset.*``. The per-mode formula table, verified equal to the layout helpers
# it replaced, is in the llm-doc.
#
# ⚑ A BOX ROOT THAT DOES NOT RESOLVE IS CATASTROPHIC, NOT COSMETIC — the foundation
# bind's src derefs it EMBEDDED, so a failure yields the host_src ``/home``, which L7
# then mkdir's and mounts OVER the box home, silently. See
# :func:`_assert_box_root_resolved`.


#: The box modes this floor knows how to root. An undeclared variant is NOT a mode
#: and is REFUSED rather than silently taking the primary/named arm.
_BOX_MODES: frozenset[str] = frozenset({"primary", "named", "standalone"})

#: The DECLARED ``workset.channels.*`` leaves (spec §2c) — the FULL family: the
#: workset-LOCAL type roots plus the ALL-PROJECTS system-rooted addresses. The floor
#: MANUFACTURES these keys from a caller-supplied mapping, so without this set it was
#: a free-form passthrough — exactly what the CLOSED keyspace (§0) forbids.
#:
#: ⚑ It is the SPEC's declared family, not the subset the one live caller happens to
#: pass: the check exists to stop FABRICATION, not to freeze the current call.
#:
#: ⚑ It must EQUAL ``settings_keyspace.DECLARED_WORKSET_CHANNEL_LEAVES``. The two
#: answer the SAME question from different seams, and R-35's bug was exactly their
#: disagreement — ``mailboxes`` accepted here, refused there. A test pins the
#: agreement so neither set can drift alone.
_WORKSET_CHANNEL_LEAVES: frozenset[str] = frozenset(
    {"common", "chat", "broadcast", "share", "mailboxes", "share_global"}
)

#: The RO DERIVED box-home SOURCE (spec ``:1015``) — the pid-0 FOUNDATION bind's src.
#: ⚑ NAMED, unlike its sibling floor keys, because it has readers OUTSIDE this module:
#: the assembly seam (``commands/start.py._install_assembly_collapse``) and
#: ``box show --effective``. One spelling for the producer below and both consumers.
BOX_HOME_KEY: Final[str] = "meta.box.home"


def workset_anchor_floor(
    *,
    mode: str,
    workset_channels: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build the LAYOUT-anchor floor keys — workset roots + the box root (spec §2c).

    Every anchor is the spec's self-resolving @-ref FORMULA, so the per-mode
    variation is spelled HERE and nowhere downstream (§2c, §2a "Declaration roots").
    ``workset.boxes`` and ``workset.logs`` are PER-MODE; the vault roots, the canon
    contribution roots, and ``meta.box.home`` are UNIFORM. The formulas and what each
    one feeds: the llm-doc.

    ⚑ ``workset.logs`` is what makes the helper-log bind a SINGLE row for all modes.
    There is deliberately NO ``meta.box.helper_log`` anchor: that construct-time
    LITERAL existed only because the spec's spelling did not parse, and it is not a
    spec-declared key, so under §0's closed keyspace it was not a key at all. Do not
    reintroduce it — one bind, one spelling.

    *workset_channels* (PRIMARY/NAMED only) maps the resolved workset-local channel
    roots into ``workset.channels.*``; ``None`` for STANDALONE. ⚑ Each leaf is
    checked against :data:`_WORKSET_CHANNEL_LEAVES` and an undeclared one is REFUSED:
    this is the one place a floor builds a key from a caller-supplied NAME, and a
    free-form passthrough would open the closed keyspace (§0) from inside the floor.
    """
    if mode not in _BOX_MODES:
        raise SettingsError(
            f"workset_anchor_floor: unknown box mode {mode!r} (expected one of "
            f"{', '.join(sorted(_BOX_MODES))})"
        )
    standalone = mode == "standalone"
    floor: dict[str, object] = {
        # boxes/logs are PER-MODE; the vault roots are UNIFORM (§2c ALL PROJECTS) —
        # only the box BIND differs per mode.
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
        # ⚑ THE ONLY SPELLING of the box home: it does NOT route through
        # ``bindings.rw`` (spec ``:1015``) — the assembly seam READS THIS KEY to build
        # the pid-0 foundation bind, so this line is what every launch's home mount
        # resolves through. Do not re-inline the formula anywhere downstream, and do
        # not re-derive it from ``proj.shell_path``.
        BOX_HOME_KEY: "@meta.box.path/home",
        # The per-scope CANON CONTRIBUTION roots (spec §2c/§2b). UNIFORM in every mode
        # with no ``<None>`` carve-out, which is only safe because the chapter binds
        # they feed are SKIP-IF-ABSENT.
        #
        # ⚑⚑ ``@box.canon`` IS NOT ``~/canon``. It is the box's CONTRIBUTION root on
        # the HOST (``<box_dir>/canon``), whose ``handbook/`` is ONE CHAPTER bound RO
        # into the assembled ``~/canon/handbook/box``. The box's assembled guest view
        # lives at ``<box_dir>/home/canon`` and arrives through the home bind. Same
        # word, adjacent paths, opposite directions of travel.
        "workset.canon": "@meta.workset.path/canon",
        "box.canon": "@meta.box.path/canon",
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

    The two enables COMPOSE — a box can be global-shared AND/OR workset-shared — but
    the SELECTED *tier* obeys precedence workset>global. Field-by-field notes are in
    the llm-doc.
    """

    tier: AuthTier
    global_enabled: bool
    workset_enabled: bool
    global_sync: bool
    workset_source: str | None

    @property
    def creds_shared(self) -> bool:
        """True when the box receives shared creds at ANY tier (not private/box)."""
        return self.tier != "box"


def resolve_auth_source(
    snapshot: KeyStore, *, mode: str | None = None
) -> AuthSource:
    """Resolve the box's credential-SHARING SOURCE off the expanded snapshot.

    Computes each tier's EFFECTIVE enable in Python — the spec's ``%support && allow
    && knob%``, since the expand engine does not evaluate ``&&`` (module note) — then
    selects by precedence workset>global: workset ENABLED with its store present, else
    global ENABLED, else ``"box"`` (private, no source).

    ⚑ An absent ``box`` node means the floor was not injected → fail CLOSED (tier
    ``"box"``, no sharing) rather than launder. Each input is a real ``bool`` terminal
    resolved by ``expand``; :func:`as_bool` does not launder either.
    """
    from kanibako.settings.settings_views import as_bool

    box_node = dict.get(snapshot, "box", __MISSING__)
    if not isinstance(box_node, KeyStore):
        return AuthSource(
            tier="box",
            global_enabled=False,
            workset_enabled=False,
            global_sync=False,
            workset_source=None,
        )

    # The box-scoped RO meta anchors: the capability MIRROR and the DERIVED per-box
    # source root (change 8 — being ``meta.*``, a scope FILE cannot repoint it).
    meta_node = dict.get(snapshot, "meta", __MISSING__)
    support = False
    workset_source: str | None = None
    if isinstance(meta_node, KeyStore):
        meta_box = dict.get(meta_node, "box", __MISSING__)
        if isinstance(meta_box, KeyStore):
            meta_box_agent = dict.get(meta_box, "agent", __MISSING__)
            if isinstance(meta_box_agent, KeyStore):
                mba_auth = dict.get(meta_box_agent, "auth", __MISSING__)
                if isinstance(mba_auth, KeyStore):
                    support = as_bool(
                        dict.get(mba_auth, "share_support", False)
                    )
            # The RO DERIVED per-box workset source root, sibling of meta.box.agent:
            # a resolved string; absent / None / "" all coerce to None.
            meta_box_auth = dict.get(meta_box, "auth", __MISSING__)
            if isinstance(meta_box_auth, KeyStore):
                wp = dict.get(meta_box_auth, "workset_path", __MISSING__)
                if isinstance(wp, str) and wp:
                    workset_source = wp

    # The system + workset allow flags.
    system_node = dict.get(snapshot, "system", __MISSING__)
    system_allow = False
    if isinstance(system_node, KeyStore):
        sys_auth = dict.get(system_node, "auth", __MISSING__)
        if isinstance(sys_auth, KeyStore):
            system_allow = as_bool(dict.get(sys_auth, "share_allowed", False))

    workset_node = dict.get(snapshot, "workset", __MISSING__)
    workset_auth = (
        dict.get(workset_node, "auth", __MISSING__)
        if isinstance(workset_node, KeyStore)
        else __MISSING__
    )
    workset_allow = False
    global_sync = False
    if isinstance(workset_auth, KeyStore):
        workset_allow = as_bool(dict.get(workset_auth, "share_allowed", False))
        global_sync = as_bool(dict.get(workset_auth, "global_sync", False))

    # The two settable box ENABLE knobs — all that remains in ``box.auth`` since the
    # workset SOURCE path moved to the RO ``meta.box.auth`` node (change 8).
    box_auth = dict.get(box_node, "auth", __MISSING__)
    global_knob = True
    workset_knob = True
    if isinstance(box_auth, KeyStore):
        global_knob = as_bool(dict.get(box_auth, "global_enabled", True))
        workset_knob = as_bool(dict.get(box_auth, "workset_enabled", True))

    # Effective enables (the Python AND standing in for the spec's %… && …%).
    global_enabled = bool(support and system_allow and global_knob)
    workset_enabled = bool(support and workset_allow and workset_knob)

    # Precedence workset>global: the workset tier wins when enabled AND its store
    # path is present (a lone box has no workset store → degenerate to global/box).
    if workset_enabled and workset_source is not None:
        tier: AuthTier = "workset"
    elif global_enabled:
        tier = "global"
    else:
        tier = "box"

    # ⚑ Null out the workset source UNLESS the workset tier was selected. Otherwise a
    # standalone box carries the GARBAGE literal ``/<agent>`` (see auth_chain_floor),
    # and the credsync dir-creation would mkdir against the host ROOT.
    if tier != "workset":
        workset_source = None

    return AuthSource(
        tier=tier,
        global_enabled=global_enabled,
        workset_enabled=workset_enabled,
        global_sync=global_sync,
        workset_source=workset_source,
    )


#: Where to look when the resolve loaded NO settings file — a narrow resolve, or one
#: whose offending entry arrived on a floor or a partial. The four tier-named files
#: (R140), so the message still points somewhere rather than trailing off.
_SETTINGS_FILE_NAMES: Final[str] = (
    "the box's box.yaml, the workset's workset.yaml, the agent's agent.yaml, "
    "or the system settings.yaml"
)


def _refuse_undeclared_snapshot(
    store: KeyStore, *, files: Sequence[Path | None],
) -> None:
    """RAISE naming EVERY resolved path the CLOSED keyspace does not declare (§0).

    Spec §0: *"reading, setting, or resolving an undeclared key is an ERROR that
    NAMES the offending key — never a silent accept, never a fabricated default,
    never a free-form passthrough."* This is the RESOLVE third of that sentence;
    ``config_keys`` holds the read/set thirds.

    ⚑ EVERY offending path, not the first. A user hand-edits the cure, and a
    refusal that names one entry per attempt turns one edit into N launches.
    (``agent_file._refuse_undeclared_state`` names one because it judges a FLAT
    table of at most a handful of behaviour keys; a resolved snapshot is the whole
    cascade.)

    ⚑ THE CURE IS A HAND-EDIT AND THE MESSAGE MUST SAY SO. ``config unset`` cannot
    remove what is not a key, and ``config show`` resolves through this very seam,
    so it refuses too — leaving a user who is told "unset it" with no working move.
    *files* are the settings files THIS resolve loaded, so the message points at
    real paths instead of a generic list; which of them carried the entry is not
    knowable here, because the snapshot is the MERGE of all of them.

    ⚑ NO BYPASS — no env var, no exemption list, no origin discriminator. A
    name-keyed escape is the carve-out the closed keyspace exists to refuse, and it
    would hide the next finding behind itself.
    """
    findings = undeclared_store_paths(store, oracle=keyspace_verdict)
    if not findings:
        return
    named = "\n".join(
        f"  - {render_store_path(segments, judgement.key_len)}: {judgement.note}"
        for segments, judgement in findings
    )
    loaded = [str(path) for path in files if path is not None]
    where = (
        "\n".join(f"    - {path}" for path in loaded) if loaded
        else f"    - {_SETTINGS_FILE_NAMES}"
    )
    count = len(findings)
    subject = (
        "1 entry that is not a settings key" if count == 1
        else f"{count} entries that are not settings keys"
    )
    them = "it" if count == 1 else "them"
    raise SettingsError(
        f"the settings resolved for this box carry {subject} "
        f"(spec §0 — the keyspace is CLOSED):\n"
        f"{named}\n"
        f"kanibako will not resolve settings that carry {them}: an undeclared key "
        f"has no meaning to give the box, and passing it through would be the very "
        f"'anything goes' behaviour the closed keyspace replaces.\n"
        f"  Fix: remove {them} BY HAND from the settings file that carries {them} — "
        f"this resolve loaded:\n{where}\n"
        f"  'kanibako config unset' cannot remove what is not a key, and 'kanibako "
        f"config show' resolves through this same seam, so it refuses too."
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
    agent_state: AgentFileLevel | None = None,
    persona_values: Mapping[str, str] | None = None,
    auth_chain: Mapping[str, object] | None = None,
    meta_runtime: Mapping[str, object] | None = None,
    meta_identity: Mapping[str, object] | None = None,
    workset_anchor: Mapping[str, object] | None = None,
    prefs: "Sequence[PrefRequest] | None" = None,
    valid_agents: "Collection[str] | None" = None,
    cli_level: Mapping[str, object] | None = None,
) -> KeyStore:
    """Build the ONE expanded launch snapshot.

    Folds the behavior floor (mapped to ``agent.default.<key>`` — OS1) and every
    runtime ``default_categories`` table into ONE base-level floor, assembles the
    6-level cascade (S8) with 7a's *agent_partial* as an additional agent-level
    source (S27), merges (S15), and expands (S17/S19) with *ctx*. There is NO bare
    ``agent.<key>`` in the snapshot (spec §2d / §0) — the agent tier is DISCRIMINATED
    throughout. Returns the expanded snapshot.

    *behavior_floor* is the BARE behavior-default dict; *default_categories* the
    already-scope-qualified category default tables, each KEY a whole category ARM and
    each VALUE the whole DEST-KEYED map under it (the shape ``core_defaults.add_bind``
    builds; R-5 / 2026-08-08c — TERMINAL, no entry-name segment, no dest in the value).

    *persona_values* are the PERSONA STORE's rendered values for the ACTIVE agent.
    ⚑ They are threaded in as an IN-MEMORY level because they are NEVER persisted to
    any settings file, and because ``_resolve_launch_snapshot`` re-reads the files
    several times per launch — a never-written layer has no file to be read from.
    ``None`` means NO persona tier at all.

    *auth_chain* / *meta_runtime* / *meta_identity* / *workset_anchor* are the floor
    fragments the four builders above produce, each folded into the SAME floor so
    ``expand`` resolves its @-ref chain ONCE (single-route). ``None`` for a NARROW
    resolve that does not need it (seed / synced / image / helper).

    *prefs* are the ``pref.*`` REQUESTS (spec §2h) of the workset + box files, in
    application order. ⚑ ``None`` means COLLECT THEM HERE — the fail-safe default, so
    a caller cannot omit them by accident. Supplying them is a CACHE, not a second
    source. *valid_agents* injects the agent-validity set (defaults to plugin
    discovery); tests supply their own.

    *cli_level* is the §1A **top-most input level** — above every settings file AND
    every pref. :func:`~kanibako.settings.settings_cli_level.guard_cli_level` is
    applied HERE, before the splice, so no call site can bypass it (P8). It always
    carries the RESOLVED agent selection, which is what keeps ``@system.agent`` equal
    to the node that actually runs.

    ⚑ WHO MUST PASS IT — "the narrow resolves can skip it" is NOT the rule, and
    reading it that way cost the credential path once already. It is REQUIRED by every
    resolve carrying the ``auth_chain`` floor, because
    ``meta.box.auth.workset_path`` = ``@workset.auth.path/@system.agent``: omit it and
    the per-agent credential dir collapses to the workset auth ROOT.

    ⚑ WHICH RESOLVES SEE THE EPHEMERAL FLAGS (P8, §1A): the SELECTION rides every
    resolve that needs it; the FLAGS ride only the resolve that decides THIS launch's
    runtime. No resolve whose output is WRITTEN TO DISK may see a flag. Both lists,
    caller by caller: the llm-doc.
    """
    floor: dict[str, object] = {}
    # OS1: bare behavior keys → scope-qualified agent.default.<key>, the ALL-AGENTS
    # backstop. There is NO bare ``agent.<key>`` (spec §0).
    if behavior_floor:
        for key, val in behavior_floor.items():
            floor[f"agent.default.{key}"] = val
    # Category default tables are already scope-qualified dotted keys, and the
    # agent-scope ones arrive ALREADY DISCRIMINATED from the declaring plugin. A live
    # ""-suppression of a DEFAULT means "this default is disabled" → DROP it
    # (absent ≡ no default).
    if default_categories:
        for key, val in default_categories.items():
            if val == "":
                continue
            # masks BRIDGE: the shipped/file form is a LIST[box_dest]; the KeyStore
            # model is a keyed ``dict[box_dest → bool]`` (S5/§6f). ⚑ This CONVERTS,
            # it does not filter — a different thing from the suppression below.
            if (key == "masks" or key.endswith(".masks")) and isinstance(
                val, (list, tuple)
            ):
                floor[key] = {str(dest): True for dest in val}
                continue
            # ⚑ The suppression applies PER ENTRY too. A bind-shaped category is one
            # TERMINAL dest-keyed map (R-5), so category-level suppression alone would
            # coarsen the smallest suppressible unit from an entry to a whole
            # category — a behaviour change nobody ruled.
            if _is_bind_floor_key(key) and isinstance(val, dict):
                floor[key] = {d: v for d, v in val.items() if v != ""}
                continue
            floor[key] = val

    # The four floor fragments, each folded into the SAME floor so ``expand``
    # resolves its @-ref chain ONCE (single-route). The auth chain goes in AFTER the
    # category tables so its dotted keys land unconditionally. The ``meta.*``
    # fragments are construct-set RO (§0), so the floor is their sole source; a scope
    # FILE MAY legitimately override a ``workset.*`` key, so those sit at the floor
    # (base) and a workset/box file still wins by name.
    if auth_chain:
        for key, val in auth_chain.items():
            floor[key] = val

    if meta_runtime:
        for key, val in meta_runtime.items():
            floor[key] = val

    if meta_identity:
        for key, val in meta_identity.items():
            floor[key] = val

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
    # their PRECISE precedence rungs, computed from these FIXED base indices. Doing
    # all splices in one pass keeps the math robust — no chained index drift. Each
    # rung and the reason it sits where it does: the llm-doc.
    state_partial = _agent_state_partial(agent_state)
    persona_partial = _persona_partial(agent_name, persona_values)
    # ⚑ THE ``box.agent.*`` CATEGORY FOLD IS GONE (P7) — §2b retired the settable
    # mirror, so the fold has no settable input left. Removing it FLIPS the
    # transitional contest P6 pinned: tests/test_settings/test_settings_launch.py
    # TestPrefLevelPrecedence.
    #
    # ⚑ Prefs are collected HERE when the caller did not supply them, so no call path
    # can silently skip them — the seed / synced / image / helper narrow resolves must
    # see a pref on ``agent.<a>.seeded.*`` too.
    requests = list(prefs) if prefs is not None else collect_prefs(
        workset_path, box_path,
    )
    # ``valid_agents`` is passed through UNRESOLVED (``None`` = "decide inside"), so a
    # pref-free launch pays nothing for discovery. ⚑ ``is None``, not falsy — an empty
    # AgentNames is a legitimate caller-supplied value.
    ws_prefs, box_prefs = apply_prefs(requests, valid_agents=valid_agents)

    levels: list[KeyStore] = []
    if cli_level:
        # §1A: the CLI LEVEL, ABOVE EVERYTHING (settings files AND prefs).
        # ⚑ GUARDED HERE, not at the call site: §1A says the §2h forbidden tiers do
        # NOT cover the CLI, so a flag that could set a LOCATOR-class value needs its
        # own guard — and a guard a caller can forget to run is not a guard.
        guard_cli_level(
            cli_level, active_agent=agent_name, valid_agents=valid_agents,
        )
        levels.append(dotted_partial(dict(cli_level)))
    levels.append(base_levels[0])                       # box
    if box_prefs:
        levels.append(box_prefs)                        # box pref REQUESTS
    levels.append(base_levels[1])                       # workset
    if ws_prefs:
        levels.append(ws_prefs)                         # workset pref REQUESTS
    if state_partial is not None:
        levels.append(state_partial)                    # per-agent FILE behavior
    levels.append(base_levels[2])                       # agent.<active> (file tables)
    if persona_partial is not None:
        # persona store — LIVE, never persisted. BELOW the per-agent FILE and ABOVE
        # ``agent.default``. ⚑ The ordering is semantically FORCED: the agent file
        # stores ONLY non-default values, so a value present in it can only be a
        # DELIBERATE user edit, and a user edit must outrank one the store re-renders
        # every launch. (The rung is UNOBSERVABLE in the merge — llm-doc.)
        levels.append(persona_partial)                  # persona store (live)
    levels.append(base_levels[3])                       # agent.default
    if agent_partial is not None:
        levels.append(agent_partial)                    # 7a descriptor default
    levels.append(base_levels[4])                       # system
    levels.append(base_levels[5])                       # base (+ folded floor)

    snapshot = merge(levels)
    expanded = expand(snapshot, ctx)
    # The meta.box.agent.* RO mirror (B5) — a COPY step, AFTER expand so the values
    # are resolved terminals.
    _materialize_box_agent_mirror(expanded, active_agent=agent_name)
    if workset_anchor and _BOX_ROOT_KEY in workset_anchor:
        _assert_box_root_resolved(expanded)
    # ⚑ MEASUREMENT FIRST, THEN ENFORCEMENT, AND THE ORDER IS LOAD-BEARING. The probe
    # is DISARMED unless ``KANI_KEYSPACE_PROBE`` names it and cannot fail a run; it
    # sized the blast radius of the refusal below, which behind ``load_merged_config``
    # is nearly every kanibako command. Raising BEFORE it would blind the instrument to
    # exactly the resolves that matter, so a future re-measurement would see only the
    # snapshots that already conform.
    observe_keyspace(expanded, origin="build_launch_snapshot")
    # Spec §0's RESOLVE clause, enforced. ⚑ A SIBLING of the probe, never a mode of
    # it: the probe is REPORT-ONLY by its own module contract, and the two share the
    # ORACLE so the refusal arms exactly what was measured.
    _refuse_undeclared_snapshot(
        expanded, files=(box_path, workset_path, agent_path, system_path),
    )
    return expanded


# --------------------------------------------------------------------------- #
# Agent SELECTION — the narrow resolve that precedes the launch snapshot (P7) #
# --------------------------------------------------------------------------- #

#: The key that names the agent a box runs (spec §2g).
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

    Returns the resolved value in THREE states the caller MUST keep apart (see
    :mod:`kanibako.settings.agent_select`): a ``str`` name · present-``None``, the
    explicit ``pref.system.agent: null`` SUPPRESSION ⇒ the NO-AGENT plain-shell box
    (spec §2b, D-M6) · ``__MISSING__``, nothing ever set it ⇒ the caller falls through
    to the installed-count rule.

    ⚑ The present-``None`` arm is only reachable because ``_resolve_present_none``
    KEEPS a present-``None`` on a SCALAR leaf — an ``if value is None: continue``
    anywhere on this path silently deletes the capability.

    ⚑ **LENIENT expand, deliberately.** ``expand`` is whole-tree, so in STRICT mode an
    unrelated defect would abort selection — a legitimate ``$AGENT`` in some other
    bind source raises here, this pass having no active agent yet. LENIENT records
    each defective leaf and omits it, while a defect ON ``system.agent`` itself is
    RAISED below, naming the key. Never a silent fall-through to no-agent.

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
    result = expand(merge(levels), ctx, collect_errors=True)
    # ⚑ The lenient overload is typed ``KeyStore | tuple[KeyStore, dict]`` (because
    # ``collect_errors`` is a plain ``bool``), so the pair must be narrowed at the call
    # site. A ``KeyStore`` unpacks into two ``str``s without complaint — it IS a
    # ``dict[str, …]`` — which is exactly what this assert stops.
    assert isinstance(result, tuple)  # lenient mode → (snapshot, errors)
    expanded, errors = result
    if SELECTION_KEY in errors:
        raise SettingsError(
            f"The agent selection key '{SELECTION_KEY}' did not resolve: "
            f"{errors[SELECTION_KEY]}. Refusing to launch rather than falling back "
            f"to a different agent — set it with `kanibako system set "
            f"{SELECTION_KEY}=<name>`, or request one per box with "
            f"`kanibako box set pref.{SELECTION_KEY}=<name>` (spec §2h)."
        )
    return snapshot_leaf(expanded, SELECTION_KEY)


#: The RO per-mode box-root anchor (spec §2c). Every rooted box key spells itself
#: against it, so it is the one anchor whose failure to resolve is unsurvivable.
_BOX_ROOT_KEY = "meta.box.path"
#: The SETTABLE key the box root dereferences. It is validated ALONGSIDE the root
#: because a broken source does not always produce a broken-LOOKING root — see
#: :func:`_assert_box_root_resolved`.
_BOX_STORE_KEY = "workset.boxes"


def snapshot_leaf(snapshot: KeyStore, dotted: str) -> object:
    """Read the resolved leaf at *dotted*, or ``__MISSING__``. UNBOUND protocol (S3).

    ⚑ PUBLIC because the assembly seam reads ``meta.box.home`` through it. One reader,
    so a dotted read off a resolved snapshot cannot acquire a second spelling with its
    own idea of what absence looks like.
    """
    node: object = snapshot
    for seg in dotted.split("."):
        if not isinstance(node, KeyStore):
            return __MISSING__
        node = dict.get(node, seg, __MISSING__)
        if node is __MISSING__:
            return __MISSING__
    return node


def _assert_box_root_resolved(snapshot: KeyStore) -> None:
    """Fail LOUDLY when the box root, or the store it derives from, did not resolve.

    ⚑ A box root that resolves to nothing does NOT surface as an error on its own.
    The pid-0 foundation bind's src IS ``meta.box.home`` = ``@meta.box.path/home``, an
    EMBEDDED ``@``-ref, and the embedded rule (§6b) coerces an absent / present-``None``
    referent to ``""``. The L7 guarantee-create then ``mkdir``\\ s whatever that
    produced and mounts it OVER the box home, so the box comes up with the wrong host
    directory as its home and nothing anywhere reports an error.

    ⚑ AND THE RESULT CAN LOOK PERFECTLY VALID, which is why BOTH keys are checked —
    primary/named yields the syntactically perfect ``/mybox`` that no shape check
    would reject. ⚑ AND A THIRD SHAPE: a root ending in ``/`` means the LEAF vanished,
    so every box in the workset would share the BOXES DIRECTORY's home. Each shape,
    and how it is reached, is worked through in the llm-doc.

    ⚑ THE TEST IS EXISTENCE + LEAF, NOT ABSOLUTENESS — deliberately, and please do not
    "tighten" it to require a leading ``/``. That was tried: it reddens 131 tests in
    ``tests/test_commands/test_start.py``, which mock ``load_std_paths()`` wholesale,
    so the resolved root is legitimately not a real path there. No production path
    reaches this check non-absolute.

    Checked ONLY when the caller actually supplied the anchor in its floor fragment,
    so narrow resolves and partial-floor callers are unaffected.
    """
    for key in (_BOX_STORE_KEY, _BOX_ROOT_KEY):
        value = snapshot_leaf(snapshot, key)
        if isinstance(value, str) and value != "" and not value.endswith("/"):
            continue
        got = "absent" if value is __MISSING__ else repr(value)
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
# meta.box.agent.* RO mirror materialization (block B5 — spec §2b)            #
# --------------------------------------------------------------------------- #
#
# Spec §2b: ``meta.box.agent.<key>`` is the box-scoped READ-BACK of its active
# agent's WHOLE resolved settings subtree. Values are still READABLE; they are no
# longer SETTABLE. Being ``meta.*`` it is RO BY CONTRACT (§0), so no settings file can
# contribute to it. ⮕ P7 RETIRED the settable ``box.agent.*`` mirror that used to live
# here; a box now tweaks its agent with ``pref.agent.<agent>.<key>`` (§2h).
#
# MECHANISM (JC-B5-1 — a COPY on the current engine, no resolver inversion). The
# resolved active-agent subtree only EXISTS post-merge/expand, because the cascade
# keeps the two agent slots DISCRIMINATED and the value-pick is a CONSUMER step
# (:func:`_agent_pick_node`). So the mirror is a deep COPY of that node, taken AFTER
# ``expand``. ⚑ NO LEAK: a FRESH deep copy is written ONLY under ``meta.box.agent.*``
# and ``snapshot["agent"]`` is never mutated, so a later in-place edit of the
# read-back cannot escape into the shared agent subtree.
#
# ⚑ The NO-AGENT box does NOT take the blank short-circuit — the launch passes
# ``"general"``, so the mirror holds the ``agent.default`` backstop. That is measured,
# harmless, and PINNED (tests/test_settings/test_settings_launch.py); the llm-doc has
# the shape and why the inherited comment here was wrong twice over.


def _materialize_box_agent_mirror(snapshot: KeyStore, *, active_agent: str) -> None:
    """Materialize ``meta.box.agent.*`` = the resolved active-agent subtree (B5).

    Mutates *snapshot* in place — it is the launch-local expanded tree, owned by the
    caller. A BLANK *active_agent* → NO subtree to mirror → nothing materialized.

    ⚑ The auth floor separately materializes ``meta.box.agent.auth.share_support``
    (a PRE-expand floor key), so this copy must not clobber it: an existing name under
    ``meta.box.agent`` is LEFT INTACT. Reads/writes via the UNBOUND ``dict`` protocol
    (S3) so a key named ``get`` / ``agent`` cannot shadow.
    """
    if not active_agent or not active_agent.strip():
        # ⚑ Leave meta.box.agent.* absent; do NOT fall back to agent.default — that is
        # the all-agents backstop, not an ACTIVE agent the box runs.
        return
    # The PURE pick (agent.default ⊕ agent.<active>), which already carries any
    # ``pref.agent.<agent>.*`` the box requested (a pref is a cascade INPUT).
    effective = _agent_pick_node(snapshot, active_agent)
    if not dict.__len__(effective):
        return  # no leaves anywhere — nothing to mirror.
    meta_node = dict.get(snapshot, "meta", __MISSING__)
    if not isinstance(meta_node, KeyStore):
        meta_node = KeyStore()
        snapshot["meta"] = meta_node
    meta_box = dict.get(meta_node, "box", __MISSING__)
    if not isinstance(meta_box, KeyStore):
        meta_box = KeyStore()
        meta_node["box"] = meta_box
    box_agent = dict.get(meta_box, "agent", __MISSING__)
    if not isinstance(box_agent, KeyStore):
        box_agent = KeyStore()
        meta_box["agent"] = box_agent
    _mirror_fill(box_agent, effective)


def _mirror_fill(box_node: KeyStore, agent_node: KeyStore) -> None:
    """Deep gap-fill *box_node* from *agent_node*: copy each *agent_node* name the
    *box_node* does NOT already set; recurse into matching KeyStore subtrees so a
    pre-set leaf does not suppress mirrored siblings (block B5).

    ⚑ A name absent from *box_node* is set to a FRESH deep COPY of the agent value, so
    no box edit aliases the shared ``agent.*`` subtree. Unbound ``dict`` protocol (S3).
    """
    from kanibako.settings.settings_merge import _deep_copy_store

    for name in dict.keys(agent_node):
        agent_val = dict.__getitem__(agent_node, name)
        box_val = dict.get(box_node, name, __MISSING__)
        if box_val is __MISSING__:
            if isinstance(agent_val, KeyStore):
                box_node[name] = _deep_copy_store(agent_val)
            elif isinstance(agent_val, list):
                box_node[name] = list(agent_val)
            else:
                box_node[name] = agent_val  # Bind / scalar / None — immutable.
            continue
        # ⚑ Recurse only when BOTH sides are subtrees. A box leaf vs an agent subtree
        # (or the reverse) means the box wholesale-overrode that name — leave the box
        # value, do NOT merge across the type boundary.
        if isinstance(box_val, KeyStore) and isinstance(agent_val, KeyStore):
            _mirror_fill(box_val, agent_val)


def _agent_state_partial(level: AgentFileLevel | None) -> KeyStore | None:
    """Wrap one agent-file behavior LEVEL under its own slot —
    ``{agent: {<level.node>: {<key>: <val>}}}`` — or ``None`` if there is nothing.

    The per-agent file stores behavior FLAT (``agent.model`` — already per-agent), NOT
    the discriminated sub-tables ``assemble_levels``' ``_agent_partial`` reads, which
    treats a flat ``[agent]`` table as UNSET. So passing the file raw as ``agent_path``
    DROPS its behavior; this wraps it into the DISCRIMINATED slot (§2d / §0).

    ⚑ IT NEEDS NO GATE OF ITS OWN, AND THAT IS DELIBERATE (P4).  The undeclared keys it
    used to ride through verbatim are refused at the BOUNDARY that builds the level
    (``agent_file.state_level``), so nothing undeclared can reach this function to be
    gated.  A second check here would be a rule spelled twice, and the one downstream
    would be the one that rots.

    ⚑⚑ THE DISCRIMINATOR ARRIVES WITH THE DATA (C-2; [spec:15-21, "self"]).  The node
    the table came FROM and the node it merged UNDER used to be two independent facts
    that nothing cross-checked; the pair now travels as one :class:`AgentFileLevel`,
    and there is no longer a parameter to pass the wrong node in.
    """
    if level is None or not level.table:
        return None
    active_node = KeyStore()
    for key, val in level.table.items():
        active_node[key] = val
    agent_node = KeyStore()
    agent_node[level.node] = active_node
    partial = KeyStore()
    partial["agent"] = agent_node
    return partial


def _persona_partial(
    agent_name: str, persona_values: Mapping[str, str] | None
) -> KeyStore | None:
    """Wrap the PERSONA STORE's live values under the active slot —
    ``{agent: {<agent_name>: {...}}}`` — or ``None`` if there is nothing to add.

    The store hands over UN-DISCRIMINATED keys (it knows a persona, not a cascade):
    the bare behavior names ``endpoint`` / ``model``, and the two open categories
    ``secret_path.<VAR>`` / ``env.<VAR>``. This discriminates them onto *agent_name*,
    the §2d / §0 form, so they merge by name at the persona rung. No value is
    bind-shaped, so every leaf is stored verbatim.

    ⚑ DELIBERATE DIVERGENCE from the sibling ``dotted_partial`` / ``_insert_dotted``
    route, which this must NOT use: those split on EVERY dot. A ``<VAR>`` here is
    arbitrary user-supplied text, so a var spelled ``FOO.BAR`` would silently become
    the subtree ``env.FOO.BAR`` instead of the ONE leaf the user wrote, and would then
    never be exported. So: split on the FIRST dot ONLY, and the ``<VAR>`` goes in as a
    LITERAL leaf key however it is spelled.
    """
    if not persona_values:
        return None
    active_node = KeyStore()
    for key, val in persona_values.items():
        category, sep, var = key.partition(".")  # FIRST dot only — see above.
        if not sep:
            active_node[key] = val
            continue
        # UNBOUND dict.get (S3): never the bound ``node.get`` — a category named
        # ``get`` would shadow the method into a crash.
        node = dict.get(active_node, category)
        if not isinstance(node, KeyStore):
            node = KeyStore()
            active_node[category] = node
        node[var] = val
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

    ⚑ Resolution order (the SPEC model, S8 + §2d): cascade FIRST, THEN
    active-over-default. The merge already resolved both slots across ALL scopes by
    name; this pick then takes the active slot's winner over the default slot's. So an
    agent-file ``agent.<active>.model`` BEATS a box-file ``agent.default.model`` —
    active wins regardless of scope. That is the one place this differs from the old
    per-file-active-over-default-THEN-cascade reader: a Jei-NOTED spec-CORRECTION,
    covered by a behavior-equivalence test, NOT silent.

    *keys*: when given, read exactly those; when ``None``, DISCOVER every scalar
    behavior leaf under ``agent.<active>`` ∪ ``agent.default``. ⚑ DISCOVERY EXISTS
    BECAUSE THE AGENT-LEAF SET IS PLUGIN-DECLARED (spec §0, "Agent specifics are
    PLUGIN-declared") — a leaf a shipped plugin declares and this reader has never
    heard of must still surface. It is NOT a pass-through for UNDECLARED keys: the
    keyspace is CLOSED (§0), and a name that reaches this node has already been
    judged at the boundary that admitted it. Category subtrees and ``Bind`` leaves
    are NOT behavior and are skipped.

    A key absent from BOTH slots is omitted. A present-``None`` scalar in the WINNING
    slot is omitted (the consumer applies its own default, §3) — and, since
    present-``None`` SETS the name, it shadows the ``agent.default`` value below it.
    Values are stringified. Reads via the UNBOUND ``dict`` probe (S3).
    """
    agent_node = dict.get(snapshot, "agent", __MISSING__)
    out: dict[str, str] = {}
    if not isinstance(agent_node, KeyStore):
        return out
    active_node = dict.get(agent_node, active_agent, __MISSING__)
    default_node = dict.get(agent_node, "default", __MISSING__)
    # ⚑ NO ``box.agent.*`` OVERLAY (P7). The settable box-scoped mirror is RETIRED
    # (§2b), so there is no box-scope behavior source to overlay: a box's
    # ``pref.agent.<agent>.<key>`` (§2h) is an ordinary cascade level and is ALREADY
    # resolved into the active slot below. Reading ``meta.box.agent`` here instead
    # would be a cycle — that node is MATERIALIZED FROM this pick.

    if keys is None:
        # DISCOVER: the union of leaf names across both slots — by NAME, so a
        # PLUGIN-declared leaf this module does not enumerate still surfaces. The
        # category subtrees and Bind leaves are filtered out per-key below.
        discovered: dict[str, None] = {}
        for node in (active_node, default_node):
            if isinstance(node, KeyStore):
                for name in dict.keys(node):
                    discovered.setdefault(name, None)
        key_iter: "list[str]" = list(discovered)
    else:
        key_iter = keys

    for key in key_iter:
        # The §2d active-over-default pick. A present value (incl. present-None) SETS
        # the key and shadows the default backstop below it.
        val: object = __MISSING__
        if isinstance(active_node, KeyStore):
            val = dict.get(active_node, key, __MISSING__)
        if val is __MISSING__ and isinstance(default_node, KeyStore):
            val = dict.get(default_node, key, __MISSING__)
        if val is __MISSING__ or val is None:
            continue
        # Behavior leaves are scalars; a category subtree / Bind is NOT behavior.
        if isinstance(val, (KeyStore, Bind)):
            continue
        out[key] = val if isinstance(val, str) else str(val)
    return out


class AgentGrammar(NamedTuple):
    """The resolved launch-grammar pair read off the snapshot (B5, spec §2d)."""

    #: ``meta.agent.<a>.mode`` — mode_key → the interactive argv fragment.
    mode: dict[str, list[str]]
    #: ``meta.agent.<a>.exec`` — the standalone one-shot fragment; ``None`` when
    #: the agent declares no ``exec`` operation.
    exec_fragment: "list[str] | None"


def meta_agent_grammar(snapshot: KeyStore, *, active_agent: str) -> AgentGrammar:
    """Read ``meta.agent.<a>.{mode,exec}`` off the ONE launch snapshot (B5).

    The LIVE launch-grammar reader: the composition seam takes its argv fragments from
    HERE, the keyspace being the single source. ⚑ There is deliberately NO fallback to
    the descriptor — a descriptor-bearing launch whose snapshot lacks the grammar is a
    BUILD BUG, and falling back would silently reintroduce the second source. Raises
    :class:`SettingsError` naming the key instead.
    """
    key = f"meta.agent.{active_agent}.mode"
    meta_node = dict.get(snapshot, "meta", __MISSING__)
    agent_root = (
        dict.get(meta_node, "agent", __MISSING__)
        if isinstance(meta_node, KeyStore) else __MISSING__
    )
    slot = (
        dict.get(agent_root, active_agent, __MISSING__)
        if isinstance(agent_root, KeyStore) else __MISSING__
    )
    if not isinstance(slot, KeyStore):
        raise SettingsError(
            f"'{key}' is not materialized in this snapshot (no "
            f"meta.agent.{active_agent} node) — the launch grammar composes from "
            f"the keyspace, so the resolve must carry meta_agent_grammar_floor()"
        )
    mode_node = dict.get(slot, "mode", __MISSING__)
    if not isinstance(mode_node, KeyStore):
        raise SettingsError(
            f"'{key}' is not materialized in this snapshot — the launch grammar "
            f"composes from the keyspace, so the resolve must carry "
            f"meta_agent_grammar_floor()"
        )
    mode: dict[str, list[str]] = {}
    for mode_key in dict.keys(mode_node):
        fragment = dict.__getitem__(mode_node, mode_key)
        if not isinstance(fragment, (list, tuple)) or not all(
            isinstance(part, str) for part in fragment
        ):
            raise SettingsError(
                f"'{key}.{mode_key}' is not an argv fragment "
                f"(expected a list of strings, got {type(fragment).__name__})"
            )
        mode[str(mode_key)] = list(fragment)
    exec_raw = dict.get(slot, "exec", __MISSING__)
    exec_fragment: "list[str] | None" = None
    if exec_raw is not __MISSING__ and exec_raw is not None:
        if not isinstance(exec_raw, (list, tuple)) or not all(
            isinstance(part, str) for part in exec_raw
        ):
            raise SettingsError(
                f"'meta.agent.{active_agent}.exec' is not an argv fragment "
                f"(expected a list of strings, got {type(exec_raw).__name__})"
            )
        exec_fragment = list(exec_raw)
    return AgentGrammar(mode=mode, exec_fragment=exec_fragment)


# --------------------------------------------------------------------------- #
# Category adapter — snapshot subtrees → the ONE list every delivery seam eats #
# --------------------------------------------------------------------------- #


# ⚑ ``agent_delivery_mounts`` LIVED HERE and is GONE (cutover 2a-3) — it was the
# SECOND mount emitter, filtering the same resolved list to the ``scope == "agent"``
# half. What survived is a per-dest missing-source POLICY, now applied by
# ``commands.start._emit_category_mounts``.
# 🛑 Do not reintroduce a second emitter: the L7 guarantee-create / ro-drop rules
# exist ONCE precisely so two copies cannot drift apart in silence.


def snapshot_category_entries(
    snapshot: KeyStore,
    *,
    active_agent: str,
    box_ctx: ResolveCtx,
    optional_keys: frozenset[str] = frozenset(),
) -> list[CategoryEntry]:
    """Walk the snapshot's category subtrees → the ONE ``list[CategoryEntry]``.

    Every delivery seam downstream reads THIS list and no other: the per-scope
    ``store_shape`` producer, the assembly collapse, and the launch seam's
    ``LaunchDeliveries``. The shape is the one the retired by-name resolver produced,
    unchanged (§6g). The four scopes are walked in the SAME ``system, agent, workset,
    box`` apply order, so a same-scope tie breaks identically, and every emitted
    entry's ``scope`` is the BARE scope token — the load-bearing scope identity (§7),
    NOT the snapshot's agent discriminator. The agent tier is picked
    active-over-default per name (§2d), the delivery-side analog of
    :func:`effective_behavior`'s read.

    🛑 host_src is read from the expanded ``Bind`` and used AS-IS: NOTHING is prefixed
    here, ever. A stored source resolves ON ITS OWN (spec §2a); an assembly-time
    root-prepend is the shape §2a calls FORBIDDEN. Do not reintroduce a per-scope root
    table here — a structural test scans for it. box_dest IS resolved box-side here
    (this is a ``box_dest`` consumer, B6) against *box_ctx*, so every seam keys on the
    SAME absolute dest. Reads via the UNBOUND ``dict`` protocol (S3).

    *optional_keys* is matched against the FULL DISCRIMINATED ``CategoryEntry.key``
    and sets :attr:`~kanibako.settings.settings_categories.CategoryEntry.optional`. It
    defaults EMPTY, so every caller that does not pass it gets byte-identical output.
    ⚑ It is a DECLARATION fact, never a heuristic on the VALUE. 🛑 DECLARATION-ONLY
    since cutover step 3 — the emitter now takes the same policy as a DEST SET,
    because a dest is the one thing the collapsed bind map keeps.

    ⚑ THE ``host_dest_keys`` COMPANION IS GONE (2026-08-08c). Every destination is
    GUEST-spelled now, copies included (spec §0 "ONE DEST SPACE, TWO DELIVERIES"), so
    there is no second namespace for a key set to select. Do not reintroduce one.
    """
    collected: list[tuple[tuple[int, str, str], CategoryEntry]] = []
    scope_order = {"system": 0, "agent": 1, "workset": 2, "box": 3}

    def _box_dest(raw: str) -> str:
        return expand_expr(raw, space="guest", ctx=box_ctx, lookup=_no_lookup)

    for scope in _SCOPES:
        # ⚑ Two producers, two shapes: the agent arm always yields a node, the plain
        # arm yields the ABSENT sentinel. Declared ``object`` so the ``isinstance``
        # gate below stays the ONE thing that tells them apart.
        scope_node: object
        if scope == "agent":
            # §2d active-over-default pick, with the emitted ``CategoryEntry.scope``
            # staying the BARE ``agent`` precedence token. A box's
            # ``pref.agent.<agent>.<category>`` requests (§2h) merged INTO
            # agent.<active> as an ordinary cascade level, so the PURE pick already
            # carries them — NO post-expand overlay (single-route).
            #
            # ⚑ The undeclared-shape REFUSAL runs on the RAW TIERS, before the pick,
            # so its message can name the DISCRIMINATED key the user actually wrote.
            # Checking the merged node could only say ``agent.bindings`` — a bare form
            # that is NOT a key (§0), i.e. a message pointing the reader at a shape
            # the keyspace forbids.
            agent_node = dict.get(snapshot, "agent", __MISSING__)
            if isinstance(agent_node, KeyStore):
                for tier in dict.keys(agent_node):
                    tier_node = dict.__getitem__(agent_node, tier)
                    if isinstance(tier_node, KeyStore):
                        _assert_declared_categories(f"agent.{tier}", tier_node)
            scope_node = _agent_pick_node(snapshot, active_agent)
            decl_scope_fn = _agent_decl_scope_fn(agent_node, active_agent)
        else:
            scope_node = dict.get(snapshot, scope, __MISSING__)
            if isinstance(scope_node, KeyStore):
                _assert_declared_categories(scope, scope_node)
            decl_scope_fn = _fixed_decl_scope_fn(scope)
        if not isinstance(scope_node, KeyStore):
            continue
        order = scope_order[scope]
        _emit_scope_node(
            collected, scope_node, order=order, scope=scope,
            box_dest_fn=_box_dest, decl_scope_fn=decl_scope_fn,
            optional_keys=optional_keys,
        )

    collected.sort(key=lambda pair: pair[0])
    return [entry for _, entry in collected]


def _fixed_decl_scope_fn(scope: str):
    """The DECLARATION-scope resolver for a non-agent scope: always *scope*.

    Such a key is spelled with its bare scope token, so declaration scope and
    precedence scope are the same string. The agent tier is the only one where they
    can differ — see :func:`_agent_decl_scope_fn`.
    """
    def decl(category: str, name: str) -> str:
        return scope
    return decl


def _agent_decl_scope_fn(agent_node: object, active_agent: str):
    """The DECLARATION-scope resolver for the agent tier: which TIER declared it.

    ⚑ The emitted ``CategoryEntry.scope`` is the BARE ``agent`` precedence token, but
    an entry's declared KEY must be DISCRIMINATED: a bare ``agent.<category>.<name>``
    is not a key at all (spec §0), so a message or a ``binding_derivations.*`` entry
    spelled that way would point a reader at something they cannot write.

    So recover the tier the same way the pick decides it, from the same RAW tiers: a
    leaf declared by the ACTIVE slot came from ``agent.<active>``; otherwise from
    ``agent.default``, the only other tier that can have contributed it. No per-leaf
    provenance is threaded through :func:`_overlay_into` — the pick's own rule answers
    it.
    """
    active_tier = (
        dict.get(agent_node, active_agent, __MISSING__)
        if isinstance(agent_node, KeyStore) else __MISSING__
    )

    def decl(category: str, name: str) -> str:
        node: object = active_tier
        for seg in (*category.split("."), name):
            if not isinstance(node, KeyStore):
                return "agent.default"
            node = dict.get(node, seg, __MISSING__)
        if node is __MISSING__:
            return "agent.default"
        return f"agent.{active_agent}"

    return decl


def _agent_pick_node(snapshot: KeyStore, active_agent: str) -> KeyStore:
    """The PURE active-over-default agent pick = ``agent.default`` overlaid by
    ``agent.<active_agent>`` (the §2d value-pick), WITHOUT the box.agent.*
    overlay.

    Returns a FRESH ``KeyStore`` shaped like a single (bare) agent scope node, each
    name holding the active slot's leaf where it set that name, else the
    ``agent.default`` leaf. The overlay is PER NAME (deep), so an active
    ``common.cache`` and a default-only ``common.plugins`` BOTH survive. A
    present-``None`` reset was already OMITted by the merge (§3 / §6e).

    ⚑ This is the subtree the ``meta.box.agent.*`` RO mirror is MATERIALIZED from, so
    it must NOT itself read ``meta.box.agent.*`` — no chicken-and-egg. Reads via the
    UNBOUND ``dict`` protocol (S3); never mutates the snapshot.
    """
    agent_node = dict.get(snapshot, "agent", __MISSING__)
    if not isinstance(agent_node, KeyStore):
        return KeyStore()
    default_node = dict.get(agent_node, "default", __MISSING__)
    active_node = dict.get(agent_node, active_agent, __MISSING__)
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
        base_val = dict.get(base, key, __MISSING__)
        if isinstance(top_val, KeyStore) and isinstance(base_val, KeyStore):
            _overlay_into(base_val, top_val)
        elif isinstance(top_val, KeyStore):
            fresh = KeyStore()
            _overlay_into(fresh, top_val)
            base[key] = fresh
        else:
            base[key] = top_val


def _assert_declared_categories(key_prefix: str, node: KeyStore) -> None:
    """Refuse every UNDECLARED category shape under ONE scope node (spec §2d),
    naming the key with the prefix it is really written under.

    *key_prefix* is the DISCRIMINATED key prefix — a bare scope token for
    system/workset/box, ``agent.default`` / ``agent.<active>`` for the agent tier.
    That is the whole reason this runs on the RAW tiers rather than the merged agent
    node: an error saying ``agent.bindings`` would name a shape §0 forbids.

    ⚑ COVERAGE IS THE FOUR CATEGORY FAMILIES — ``bindings.{ro,rw}``, the four leaf
    categories, and ``masks``. ``masks`` joined them on 2026-08-10, its silent skip
    having been the last route by which a user-written category could vanish without a
    word: a ``masks`` LIST stayed a plain ``list`` through the merge, missed the emit's
    ``isinstance`` guard, and left the path the user asked to HIDE plainly readable
    inside the box — no mount, no warning.

    ⚑ ``env`` and ``secret_path`` still keep their SILENT SKIP of a non-``KeyStore``
    node: they are the scalar-valued pair, outside the boundary approved for the bind
    pass, and widening them is a decision, not an omission to fix in passing.

    ⚑ The FLOOR's list→keyed-dict bridge for ``<scope>.masks`` is NOT the same
    permission and stays: a floor table is written by kanibako or a plugin, never by a
    user, and it runs BEFORE assembly, so what reaches here is already the keyed shape.
    A settings FILE has no such adapter, and is refused.
    """
    bindings = dict.get(node, "bindings", __MISSING__)
    if bindings is not __MISSING__:
        bindings = _require_category_node(key_prefix, "bindings", bindings)
        for name in dict.keys(bindings):
            if name not in ("ro", "rw"):
                raise SettingsError(
                    f"{key_prefix}.bindings.{name} is an ARM-LESS binding, which is "
                    f"not a declared key; bindings are declared per arm and the arm "
                    f"is the WHOLE key — {key_prefix}.bindings.ro / "
                    f"{key_prefix}.bindings.rw, each a TERMINAL map keyed by box "
                    f"destination (spec §2a / §2d). Move the entry under one of the "
                    f"two arms, keyed by its destination"
                )
        for mode in ("ro", "rw"):
            mode_node = dict.get(bindings, mode, __MISSING__)
            if mode_node is not __MISSING__:
                _require_category_node(key_prefix, f"bindings.{mode}", mode_node)
    for category in _BIND_LEAF_CATEGORIES:
        cat_node = dict.get(node, category, __MISSING__)
        if cat_node is not __MISSING__:
            _require_category_node(key_prefix, category, cat_node)
    # ⚑ ``masks`` is checked on its own line rather than folded into
    # ``_BIND_LEAF_CATEGORIES``: that set is what the EMIT walks, and a mask has no
    # source to unpack.
    masks = dict.get(node, "masks", __MISSING__)
    if masks is not __MISSING__:
        _require_category_node(key_prefix, "masks", masks)


def _require_category_node(key_prefix: str, category: str, node: object) -> KeyStore:
    """Refuse a VALUE sitting at a CATEGORY ROOT (spec §2d); return the node itself.

    Returning the node rather than ``None`` is what lets a caller keep reading it: the
    refusal is the only thing standing between an ``object`` and a :class:`KeyStore`,
    so handing the narrowed node back means no caller has to restate the check.

    A category token names a NAMESPACE of per-name entries; it is not itself a declared
    key, so a scalar / :class:`Bind` / list there is an UNDECLARED shape, and under §0
    that is an ERROR that names itself. Running against the ASSEMBLED snapshot catches
    it from any origin — a plugin defaults table, a workset or box YAML, a ``config
    set`` — in ONE place. Before P3 these shapes were SILENTLY DROPPED.

    ⚑ PRESENT-BUT-EMPTY (``bindings: {}``) is NOT an error: an empty node is
    byte-indistinguishable from an absent one after ``assemble``, so erroring would
    trap a no-op. ⚑ And ONE route does not reach this check at all — see the llm-doc.
    """
    if isinstance(node, KeyStore):
        return node
    # ⚑ Every bind-shaped category is a TERMINAL dest-keyed map (2026-08-08c), so what
    # a user must declare is the MAP, keyed by destination — never a ``.<name>`` entry,
    # which is no longer a key at any scope. ``masks`` is dest-keyed like the rest but
    # its VALUE is the 3-state marker, not a source, so only the example differs.
    declared = (
        f"{key_prefix}.bindings.{{ro,rw}}" if category == "bindings"
        else f"{key_prefix}.{category}"
    )
    shape = (
        "{box_dest: true}" if category == "masks"
        else "{box_dest: [src[, options]]}"
    )
    raise SettingsError(
        f"{key_prefix}.{category} is a value at a CATEGORY ROOT "
        f"({type(node).__name__}: {node!r}), which is not a declared key; "
        f"declare {declared} as a map keyed by box destination, "
        f"{shape} (spec §2a / §2d L906-910)"
    )


def _emit_scope_node(
    collected: list[tuple[tuple[int, str, str], CategoryEntry]],
    scope_node: KeyStore,
    *,
    order: int,
    scope: str,
    box_dest_fn,
    decl_scope_fn,
    optional_keys: frozenset[str] = frozenset(),
) -> None:
    """Emit every category entry under ONE (bare) scope NODE.

    *scope_node* is a single scope's category subtree; *scope* is the BARE scope token
    used for the emitted ``CategoryEntry.scope`` — the load-bearing precedence
    identity. *decl_scope_fn* ``(category, name)`` answers the OTHER scope question:
    which DISCRIMINATED scope the entry was DECLARED under, for ``CategoryEntry.key``.
    ⚑ The two are different facts — collapsing them would either lose the precedence
    token or emit a bare ``agent.<category>`` key, which is not a key (§0). Reads via
    unbound ``dict`` ops (S3).

    EMISSION ONLY. The undeclared-shape refusal ran earlier, against the RAW tiers, so
    every ``isinstance`` skip below is an unreachable guard rather than the silent drop
    it was before P3. ⚑ The bind LEAF-TYPE rulings are a different thing: they tell the
    dest-keyed and name-keyed shapes apart, and they RAISE rather than skip.
    """
    # bindings.{ro,rw} — the ARMED category: the map is one level under the token.
    bindings = dict.get(scope_node, "bindings", __MISSING__)
    if isinstance(bindings, KeyStore):
        for mode in ("ro", "rw"):
            mode_node = dict.get(bindings, mode, __MISSING__)
            if isinstance(mode_node, KeyStore):
                _emit_bind_map(
                    collected, mode_node, order=order, scope=scope,
                    category=f"bindings.{mode}", box_dest_fn=box_dest_fn,
                    decl_scope_fn=decl_scope_fn, optional_keys=optional_keys,
                )

    # caches / seeded / common / synced — the map is AT the category token.
    for category in _BIND_LEAF_CATEGORIES:
        cat_node = dict.get(scope_node, category, __MISSING__)
        if isinstance(cat_node, KeyStore):
            _emit_bind_map(
                collected, cat_node, order=order, scope=scope,
                category=category, box_dest_fn=box_dest_fn,
                decl_scope_fn=decl_scope_fn, optional_keys=optional_keys,
            )

    # masks — a keyed dict[box_dest → bool] (present-None unmasks were dropped at
    # build, §6f); each surviving key is a masked dest. ⚑ The isinstance is a TYPE
    # NARROW, not a filter: every other shape was already refused by name.
    masks = dict.get(scope_node, "masks", __MISSING__)
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
                    key_segments=(
                        *decl_scope_fn("masks", raw_dest).split("."),
                        "masks", raw_dest,
                    ),
                ),
            ))

    # env — scalar VAR → value.
    env = dict.get(scope_node, "env", __MISSING__)
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
                    key_segments=(
                        *decl_scope_fn("env", var).split("."), "env", var,
                    ),
                ),
            ))

    # secret_path — the SECRET category (spec §2a): a scalar host PATH keyed by VAR,
    # delivered as a ro MOUNT to SECRET_MOUNT_DIR/{VAR}. Modeled on the env branch but
    # MOUNT. ⚑ start.py emits the ro Mount plus the box-side export shim — kanibako
    # NEVER reads the file VALUE — and options stays ``ro`` with NO ``:U`` chown of
    # the host secret.
    secret = dict.get(scope_node, "secret_path", __MISSING__)
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
                    key_segments=(
                        *decl_scope_fn("secret_path", var).split("."),
                        "secret_path", var,
                    ),
                ),
            ))


def _emit_bind_map(
    collected: list[tuple[tuple[int, str, str], CategoryEntry]],
    map_node: KeyStore,
    *,
    order: int,
    scope: str,
    category: str,
    box_dest_fn,
    decl_scope_fn,
    optional_keys: frozenset[str] = frozenset(),
) -> None:
    """Emit every entry of ONE terminal DEST-KEYED category map.

    The single loop behind all six bind-shaped categories. *map_node* is the
    ``BindMap`` node itself — at the ARM for ``bindings.{ro,rw}``, at the CATEGORY
    TOKEN for the four leaf categories. The two differ only in WHERE the caller found
    it, so this is written once (2026-08-08c collapsed two near-identical loops that
    had already drifted in their error text).

    ⚑ THE DEST-KEYED TYPE SEAM (R-5/R-6). The map KEY *is* the (unresolved) box
    destination and the leaf is a 2-element ``BindEntry(src, opts)`` carrying no
    destination at all. The type is ruled in HERE, and the destination handed to
    :func:`_emit_bind` is the map key — never a value field. That is what makes "mount
    at the destination stored in the value" UNREPRESENTABLE rather than merely guarded
    against (R-8).

    ⚑ ``name`` is the DESTINATION for every category now: there is no entry name in the
    keyspace, so the collision messages and the ``binding_derivations.*``
    materialisation identify an entry by where it lands (R-10).
    """
    for dest in dict.keys(map_node):
        entry = dict.__getitem__(map_node, dest)
        # ⚑ The DEST is the LAST segment and stays whole: it is data, and a dest
        # such as ``~/.cache/uv`` carries dots of its own (see CategoryEntry).
        key_segments = (
            *decl_scope_fn(category, dest).split("."),
            *category.split("."), dest,
        )
        if not isinstance(entry, BindEntry):
            raise SettingsError(
                f"category {'.'.join(key_segments)} is {type(entry).__name__}, "
                f"expected a BindEntry ({category} is dest-keyed: the map key is "
                f"the destination; present-None binds are omitted at build, "
                f"§3/§6e)"
            )
        _emit_bind(
            collected, order, scope, category, dest,
            entry.src, dest, entry.opts, box_dest_fn,
            key_segments=key_segments, optional_keys=optional_keys,
        )


def _emit_bind(
    collected: list[tuple[tuple[int, str, str], CategoryEntry]],
    order: int,
    scope: str,
    category: str,
    name: str,
    host_src: str,
    box_dest_raw: str,
    opts: str | None,
    box_dest_fn,
    *,
    key_segments: tuple[str, ...],
    optional_keys: frozenset[str] = frozenset(),
) -> None:
    """Append one bind-shaped :class:`CategoryEntry` (MOUNT or COPY).

    ⚑ This function takes PRIMITIVES, not a bind object, and that is the point (P7
    ruling). Its one caller has already ruled in the leaf TYPE at the seam that knows
    the shape, so by the time anything gets here there is only ONE unpacked triple and
    no second place a destination could come from. A leaf type check inside here would
    put two shapes in one function (CONVENTIONS §0) and would leave "take the dest from
    the value" expressible.

    *host_src* is used AS-IS — a stored source resolves on its own (§2a) and NOTHING is
    prefixed here. *box_dest_raw* is the UNRESOLVED destination, which *box_dest_fn*
    resolves box-side. *opts* is the per-entry options override. *key_segments* is the
    DISCRIMINATED declaration key plus the entry's DEST as the last segment;
    *optional_keys* is matched on its DOTTED spelling.

    ⚑⚑ EVERY DEST IS GUEST-SPELLED, COPIES INCLUDED (spec §0 "ONE DEST SPACE, TWO
    DELIVERIES", 2026-08-08c) — so there is ONE resolution here and no space
    discriminator. A COPY's guest dest is resolved to a host path later, when the copy
    runs; neither resolution happens here.
    """
    delivery = _DELIVERY[category]
    box_dest = box_dest_fn(box_dest_raw)
    if delivery == "MOUNT":
        # ⚑⚑ THREE STATES, NOT TWO, AND ``is not None`` IS WHAT KEEPS THEM APART:
        #   None -> UNSET: take the category default (``ro`` / ``Z,U``);
        #   ""   -> EXPLICITLY NO OPTIONS, a declared value like any other;
        #   any other string -> that value.
        # 🛑 ``opts or _bind_options(category)`` collapses the first two and is WRONG.
        # The live case is the ``helper_sock`` entry in ``core-defaults.yaml``
        # (``bindings.rw``, ``options: ""``): a unix SOCKET the hub listens on, whose
        # shared topology a ``Z``/``U`` relabel/chown breaks. The truthiness spelling
        # hands it ``Z,U``, the mount is still emitted at the same arity, nothing
        # fails, and the socket quietly stops working. Pinned by
        # ``tests/test_settings/test_mount_options.py``.
        # ⚑⚑ THIS LINE FEEDS BOTH ROUTES — it is UPSTREAM of the collapse, never a
        # peer of it, so the category default is ALREADY CONCRETE when
        # ``store_collapse.fold_opt`` folds the ARM token onto it.
        # 🛑 DO NOT read ``fold_opt`` as taking the STORED opts. It takes THIS value;
        # the stored ``None`` never reaches it. Reading that call in isolation
        # manufactures a phantom regression in which an options-less rw bind collapses
        # to a bare ``rw`` and silently loses its relabel and chown.
        options = opts if opts is not None else _bind_options(category)
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
            key_segments=key_segments,
            optional=".".join(key_segments) in optional_keys,
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

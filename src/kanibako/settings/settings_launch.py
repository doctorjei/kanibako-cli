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

import logging
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Callable,
    Collection,
    Final,
    Literal,
    Mapping,
    NamedTuple,
    Sequence,
    TypedDict,
    overload,
)

if TYPE_CHECKING:
    from kanibako.channels.channels import (
        BoxChannelAddresses, WorksetChannels, WorksetPartition,
    )
    from kanibako.project.workset import Workset
    from kanibako.settings.workset_dirkeys import EarlyScope
    from kanibako.targets.base import PluginDescriptor

from kanibako import kuid
from kanibako.agent_ref import (
    ADDRESSABLE_PSEUDO_AGENTS, GENERAL_SLOT, display_agent_ref, harness_of, with_harness,
)
from kanibako.settings.agent_config import (
    ambiguous_path_value_error,
    is_unambiguous_path_value,
    store_dirname,
)
from kanibako.settings.bootstrap import SPAWN_BUDGET_DEFAULTS
from kanibako.settings.agent_file import AgentFileLevel, stored_leaf_text
from kanibako.settings import core_defaults
from kanibako.settings.config import (
    _BOX_SCALAR_FIELDS,
    AGENT_META_FILE,
    WORKSET_META_FILE,
    KanibakoConfig,
    _typed_box_scalar,
    box_scalar_defaults_floor,
    load_config,
    config_base_path,
    key_owner,
    null_path_keys_error,
    reaches_identity,
    refuses_null_box_scalar,
    refuses_null_path_key,
    settings_base_path,
    uniform_anchor,
    usable_box_store_value,
    user_config_file,
)
from kanibako.settings.kb_store import SCOPE_CONTAINMENT, Bind, BindEntry
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.keystore import KeyStore
from kanibako.settings.messages import (
    ERR_BOX_STORE_EMPTY_REASON,
    ERR_BOX_STORE_TRAILING_REASON,
    ERR_PER_OWNER_LAUNCH,
    PER_OWNER_SET_WORDS,
    PER_OWNER_SHARE_TAIL,
)
from kanibako.settings.paths import (
    BoxMode,
    ProjectError,
    box_workset_settings_paths,
    host_xdg_map,
    load_std_paths,
    system_path_floor,
    workset_settings_path,
)
from kanibako.settings.config_keys import is_path_valued_key, path_key_anchor
from kanibako.settings.settings_assemble import (
    ReadPurpose,
    SettingsFile,
    assemble_levels,
    cascade_files,
    dotted_partial,
    refuse_undeclared_per_file,
    retired_cure,
    undeclared_listing,
)
from kanibako.settings.settings_categories import (
    _DELIVERY,
    BARE_RELATIVE_SOURCE_HAZARD,
    SECRET_MOUNT_DIR,
    CategoryEntry,
    _bind_options,
    refuse_non_scalar_family_value,
)
from kanibako.settings.settings_cli_level import build_cli_level, guard_cli_level
from kanibako.settings.settings_expand import DestKeys, NullSources, RefsRead, expand
from kanibako.settings.settings_keyspace import (
    BIND_LEAF_CATEGORIES,
    Judgment,
    KeyClass,
    entry_label,
    is_terminal_category_key,
    display_store_path,
    pseudo_agent_fence,
    shown_key,
    undeclared_store_paths,
    walk_store_paths,
)
from kanibako.settings.settings_keyspace_probe import keyspace_verdict
from kanibako.settings.settings_keyspace_probe import observe as observe_keyspace
from kanibako.settings.settings_merge import merge
from kanibako.settings.settings_prefs import PrefRequest, apply_prefs, collect_prefs
from kanibako.settings.settings_resolve import (
    ResolveCtx,
    SettingsError,
    expand_expr,
    is_verbatim_text,
    literal_expr,
    normalize_bind_dest,
    unpack_bind_entry,
)
from kanibako.settings.messages import (
    ERR_BOX_SCALAR_NULL_CURE,
    ERR_BOX_SCALAR_NULL_HEAD,
)


# Aliases the single-source scope-containment tuple (kb_store) so this consumer
# never re-declares the scope set. This alias is the single source within this
# module: the emit loop's ``scope_order`` map is DERIVED from it, not re-declared.
# ⚑ ORDER IS LOAD-BEARING HERE, which it was not while the map was hand-written:
# ``scope_order`` now takes its ranks from this tuple's POSITIONS, so reordering
# ``SCOPE_CONTAINMENT`` reorders the emit and changes how a same-scope tie breaks.
_SCOPES: tuple[str, ...] = SCOPE_CONTAINMENT

#: The dotted-key TAILS a DEST-KEYED bind map can sit at in a default-category floor
#: table — the ``bindings`` ARMS plus each of the four terminal categories. ONE tuple,
#: so the per-entry ``""``-suppression in the floor fold cannot drift from the reader.
_BIND_FLOOR_TAILS: tuple[str, ...] = (".bindings.ro", ".bindings.rw") + tuple(
    f".{c}" for c in sorted(BIND_LEAF_CATEGORIES)
)


_log = logging.getLogger(__name__)


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


#: The shipped ``system.*`` SCALAR floor — installed at the BASE rung of EVERY resolve,
#: unconditionally, so a whole-value ``@``-ref to one of these keys ANSWERS ([R143]; the
#: ``workset.channelroot`` defect R-35, "fix the CODE").  Every settings scope still
#: outranks it by merge level, so a user's ``system:`` table wins by name.
#:
#: ⚑ UNCONDITIONAL, unlike the four floor FRAGMENTS folded in below: those are optional
#: because a narrow resolve legitimately has no auth chain or no workset anchor. A
#: shipped scalar default has no such story — a resolve that skipped it would answer
#: ``__MISSING__`` for a key the manifest promises a value for.
#: ⚑ ONLY the helper SPAWN BUDGET today, and the emptiness of the rest is the reason:
#: the other §2g scalars either declare NO default (``system.agent``,
#: ``system.setup_completed``), are PATHS floored by ``paths.resolve_system_paths``, or
#: ride the ``auth_chain`` fragment. A new non-path ``system.*`` default belongs here.
#: ⚑ VALUES DERIVED from ``bootstrap.SPAWN_BUDGET_DEFAULTS`` (P13) — the same table the
#: in-box spawn fallback reads, so the two cannot disagree.
SYSTEM_SCALAR_FLOOR: dict[str, object] = {
    f"system.helpers.{leaf}": value
    for leaf, value in SPAWN_BUDGET_DEFAULTS.items()
}


def auth_chain_floor(
    *,
    mode: str | None,
    agent_name: str,
) -> dict[str, object]:
    """Build the auth 3-tier SHARING chain floor keys for *mode*.

    The ``{dotted_key: value}`` floor fragment for the spec's ``auth.*`` sharing
    chain (§2a/§2b/§2c/§2d), folded into ``build_launch_snapshot``'s floor so
    ``expand`` resolves the chain ONCE (single-route). *mode* is the box's
    :class:`~kanibako.settings.paths.BoxMode` value, passed as a plain string to
    avoid a paths import; ``None`` is a resolve in NO working set (the SYSTEM
    subject), which gets no ``workset.auth.*`` key at all rather than a mode's.

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
        # ⚑ Both anchors are the spec's standalone ``<None>`` rows (§2c), SUPPLIED
        # as a present None, never omitted: an embedded @-ref to an ABSENT key
        # renders ``""``, so ``@workset.auth.path/@system.agent`` would expand to
        # ``/<agent>`` — garbage the credsync dir-creation would mkdir against the
        # host ROOT.  A present None instead makes the derived value None (§0).
        floor["workset.auth.path"] = None
        floor["meta.box.auth.workset_path"] = None
    elif mode is not None:
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


def meta_runtime_host_floor() -> dict[str, object]:
    """Build the mode-free ``meta.runtime.{user,admin}.*`` floor keys (spec §1A): the HOST files.

    Each value is the path the Layer-1 / ``base`` reads open, off the same helper
    (``$XDG_CONFIG_HOME`` honored as :func:`~kanibako.settings.config.user_config_file`
    honors it), so the key and the read cannot disagree. Every subject has these files.
    Each enters as :func:`~kanibako.settings.settings_resolve.literal_expr`, never as an
    expression.
    """
    return {
        "meta.runtime.user.config": literal_expr(str(user_config_file())),
        "meta.runtime.admin.config": literal_expr(str(config_base_path())),
        "meta.runtime.admin.settings": literal_expr(str(settings_base_path())),
    }


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
    Layer-1 foundation); it enters as :func:`literal_expr`.

    The re-rooted keys are UNIFORM across modes and construct-set RO per §0, so the
    floor is their sole source. Per-key detail: the llm-doc.
    """
    # meta.runtime.{user,admin}.* — the host files, the same in every mode (spec §1A).
    floor = meta_runtime_host_floor()

    # meta.runtime.project_type — the resolved mode token (spec §1A).
    floor["meta.runtime.project_type"] = mode

    # meta.runtime.ws_name — the workset partition TOKEN (spec §1A):
    #   primary → __PRIMARY__ · named → <detected name> · standalone → __STANDALONE__.
    floor["meta.runtime.ws_name"] = literal_expr(ws_name)

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
        floor["meta.runtime.ws_root"] = literal_expr(ws_root_literal)

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
    snapshot. That sharing is load-bearing: an abstract-category source is ROOTED AT
    ASSEMBLY into ``@meta.agent.<agent>.path/<category>/<name>``
    (``settings_assemble._declaration_root_ref`` → ``parse_bind_map``, spec §2a), so
    that spelling is what the walk PRODUCES and what the set-time snapshot then has to
    resolve — without the key, the bare leaf a user just wrote is refused as a
    dangling ``@``-reference.

    ⚑ NODE **and** HARNESS are both materialized — but NOT for the plugin's own
    commons: ``agent_categories_for_node`` re-keys AND re-roots those to the NODE before
    the ref ever reaches ``expand``, so removing the harness anchor leaves them
    BYTE-IDENTICAL (mutation-proved). What needs it is a HARNESS-KEYED ref
    the USER wrote: at SET time the snapshot above must carry it or the value is
    refused as a dangling dependency, and at LAUNCH a missing anchor does not raise at
    all — §2a's embedded-ref rule renders the dangling ``@``-ref as ``""``, so the box
    binds a garbage host source and NOTHING reports an error. The harness entry is
    INTENTIONALLY PARTIAL (a ``path``, no ``name`` / ``auth.share_support``); the
    llm-doc says why that asymmetry is inert and must not be "fixed".
    """
    # ⚑ THE TWO HALVES ARE SPELLED DIFFERENTLY ON PURPOSE: the KEY is a key path, so
    # its node segment stays CANONICAL (``℘``); the VALUE is a DIRECTORY, so it takes
    # the ``+`` store spelling (``agent_config.store_dirname``).
    #
    # ⚑ WHERE §2d's ``%tolower(…)%`` IS: in the NODE this composes from, not in a
    # fold applied here. The node's harness segment IS the declared name lowercased
    # — ``targets._register`` derives it, ``config.resolve_agent`` substitutes it —
    # so a path built from the node has already had the fold applied, and
    # ``agent_settings_path`` / the persona shim build the SAME string from the SAME
    # node. 🛑 Folding again HERE would not be a no-op: a node's PERSONA segment
    # keeps the user's case (neither [R172] nor [R173] reaches it, and ``Q35`` is
    # open), so a ``tolower`` over the whole value would name ``agents/nav+claude/``
    # while every other composer named ``agents/Nav+claude/``. The node is what holds
    # this key and the rest of the tree in agreement.
    return {
        f"meta.agent.{store_agent}.path": f"@config.agents/{store_dirname(store_agent)}"
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
    materializes nothing and the launch takes the no-agent path — EXCEPT the
    no-agent slot itself, whose EMPTY grammar the spec declares POSITIVELY
    (``meta.agent.shell.mode | {}``, §2d fence).
    """
    if descriptor is None:
        if agent_name in ADDRESSABLE_PSEUDO_AGENTS:
            # ⚑ D2/D4: absence would say "no grammar was materialized" (and the
            # reader raises on it); ``{}`` says "the grammar IS empty".  The
            # shell box takes the plain-shell path either way — the difference
            # is conformance, not behavior.
            return {f"meta.agent.{agent_name}.mode": {}}
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


class BoxAddressArgs(TypedDict):
    """The three channel-address arguments of :func:`meta_identity_floor`."""

    # ⚑ ``None`` rides the same terms as the addresses themselves: a null partition key
    # nulls the address, and the floor's job is to carry that, not to spell it "None".
    inbox: str | None
    share_global: str | None
    share_workset: str | None


def box_address_args(addr: "BoxChannelAddresses") -> BoxAddressArgs:
    """:func:`meta_identity_floor`'s channel-address arguments from what
    ``channels.box_channel_addresses`` derived — the ONE spelling of that wiring.

    Each address goes in as the RESOLVED literal; a standalone box's ``share_workset``
    stays ``None`` (no workset-local channels, §2c).  The launch unpacks this into the
    floor call, and the kinemata ``box-*`` views unpack the same answer, so a slot
    wired to the wrong address reds there.
    """
    # ⚑ A NULL ADDRESS STAYS ``None``: the inbox row's source is ``@meta.box.inbox``,
    # so the bind is omitted when this is null (spec §2a), and ``str(None)`` would
    # instead reach the expander as the four-character path ``"None"``.
    return BoxAddressArgs(
        inbox=None if addr.inbox is None else str(addr.inbox),
        share_global=(
            None if addr.share_global is None else str(addr.share_global)
        ),
        share_workset=(
            str(addr.share_workset) if addr.share_workset is not None else None
        ),
    )


def meta_identity_floor(
    *,
    box_name: str,
    project_path: str | None,
    # ⚑ A NULL ADDRESS IS A REAL ``None`` IN THE FLOOR, on the terms
    # :func:`box_address_args` states: §0 collapses the sourced bind rather than
    # naming a directory called ``None``.
    inbox: str | None,
    share_global: str | None,
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
    name), entered as :func:`literal_expr`, so a bind re-pointed to
    ``@meta.box.workspace`` / ``@meta.box.inbox`` expands to the byte-identical
    host_src (JC-B2-4 equivalence bar).

    *share_workset* is ``None`` for STANDALONE (no workset-local channels, §2c) →
    a whole-value ``None`` terminal.  *project_path* is ``None`` for a standalone box
    whose root nulls ``workset.workspaces`` (Q106) → ``meta.box.workspace`` is ``<None>``.
    *box_settings* is the RO box-TIER settings-file anchor, UNIFORM in EVERY mode and
    single-sourced with the cascade's own box-tier path so the two cannot drift; it
    stays optional for narrow resolves that materialize no box tier.

    *agent_name* is the cascade discriminator — the NODE, lowercase by construction
    (``[R173]``). *agent_real_name* carries the plugin's DECLARED harness NAME in the
    plugin's own case (``Target.name``); its HARNESS SEGMENT is spliced into the
    node's harness slot to build the case-carrying ``meta.agent.<a>.name`` VALUE and
    is used for nothing else, so a caller may hand over a bare name or a whole ref.
    ⚑ The STORE-ROOT anchor is keyed on the DISCRIMINATOR, not the real name — the
    store dir is ``agents/<store_dirname(discriminator)>/``, which is what
    ``agent_settings_path`` and the persona shim use, and it is the node that keeps
    the two in agreement. Both ``None`` for a NO-AGENT box. Per-key detail: the llm-doc.
    """
    def literal(text: str | None) -> str | None:
        return None if text is None else literal_expr(text)

    floor: dict[str, object] = {
        # Box identity (spec §2c). ⚑ The box name is REUSED from ``proj.name``
        # (JC-B2-2): standalone's <kuid>_%leaf% is composed LIVE in
        # ``resolve_standalone_project``, and B2 does NOT re-compose or regenerate it.
        "meta.box.name": literal_expr(box_name),
        # The in-box workspace SOURCE literal (routed to box.bindings.rw.workspace).
        "meta.box.workspace": literal(project_path),
        # This box's own channel partition addresses (inbox routed to
        # box.bindings.rw.inbox; the two share dirs are anchors for parity).
        "meta.box.inbox": literal(inbox),
        "meta.box.share_global": literal(share_global),
        "meta.box.share_workset": literal(share_workset),
        # The RO box-TIER settings-file anchor — the file the cascade reads and
        # `config set` writes.
        "meta.box.settings": literal(box_settings),
        # meta.workset.name is NOT set here: it anchors into meta.runtime.ws_name.
    }
    # The agent identity key (spec §2d) — REQUIRED when an agent exists, under
    # the agent's discriminated slot. A NO-AGENT box omits it.
    if agent_name is not None:
        floor.update(meta_agent_identity_floor(
            agent_name, agent_real_name, agent_auth_share_support,
        ))
    return floor


def meta_agent_identity_floor(
    agent_name: str, agent_real_name: str | None, agent_auth_share_support: bool,
) -> dict[str, object]:
    """The ``meta.agent.<agent_name>.*`` half of :func:`meta_identity_floor`.

    Separate because it names no box: a resolve with no box (a working set, the
    system scope) floors these and nothing else of the identity set. A pseudo-agent
    gets only the leaves its §2d fence declares (``default``: ``name``, ``path``).
    """
    floor: dict[str, object] = {}
    # ⚑ THE KEY DISCRIMINATOR AND THE VALUE ARE SPELLED DIFFERENTLY, and §2d's
    # own formula is why: ``meta.agent.<a>.path`` IS
    # ``@config.agents/%tolower(@meta.agent.<a>.name)%``, so this VALUE is what
    # the store DIRECTORY is spelled FROM and must be the ``+`` spelling
    # ``store_dirname`` produces. The discriminator segment stays CANONICAL
    # (``℘``) because it is a key path. Spell the value with ``℘`` and the
    # spec's formula stops composing.
    # ⚑ A BARE AGENT IS UNAFFECTED BY CONSTRUCTION — ``store_dirname`` is
    # identity on a name with no separator, so only personas move.
    #
    # 🛑🛑 THE HARNESS SEGMENT CARRIES THE PLUGIN'S DECLARED CASE, AND THAT IS
    # THE WHOLE POINT OF THE KEY ([R173], [R176]). An agent's canonical case
    # lives in its NAME; the node is the fold OF that name. Substituting the
    # already-folded discriminator here would leave the declared spelling in no
    # stored carrier at all, and would satisfy §2d's ``%tolower(…)%`` — his own
    # adjustment, written precisely to keep the name case-carrying — with a
    # NO-OP. That is [R171]'s retired fold-to-store cure, re-spelled.
    # ⚑ ``with_harness``, so only the HARNESS moves: a persona segment is the
    # user's and neither ruling reaches it.
    # ⚑ ``harness_of`` on the real name too, so the splice is IDEMPOTENT: a
    # caller handing over a whole ref (the node, or ``persona℘Kirobo``) gets the
    # same answer as one handing over the bare declared name. Without it,
    # ``with_harness(nav℘claude, nav℘claude)`` would compose ``nav℘nav℘claude``.
    floor[f"meta.agent.{agent_name}.name"] = store_dirname(
        with_harness(agent_name, harness_of(agent_real_name))
        if agent_real_name is not None else agent_name
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
    # it must be present whenever an agent exists — except for a pseudo-agent
    # whose fence does not declare it (``default``), filtered out below.
    floor[f"meta.agent.{agent_name}.auth.share_support"] = bool(
        agent_auth_share_support
    )
    fence = pseudo_agent_fence(agent_name)
    if fence is None:
        return floor
    declared = {*fence.meta_leaves, *(f"auth.{a}" for a in fence.meta_auth_leaves)}
    node = f"meta.agent.{agent_name}."
    return {k: v for k, v in floor.items() if k.removeprefix(node) in declared}


# --------------------------------------------------------------------------- #
# LAYOUT-anchor materialization: workset roots + RO BOX ROOT (spec §2a/§2c)   #
# --------------------------------------------------------------------------- #
#
# The other half of the single-route payoff: it materializes the workset-scope PATH
# anchors the spec's §2c binds reference (workset.{boxes,vault_ro,vault_rw,logs} +
# the workset-local channels) and the RO per-mode BOX ROOT ``meta.box.path``, as REAL
# @-referenceable floor keys. JC-B2b-1: they do NOT exist as resolvable snapshot keys
# otherwise — resolve_system_paths derives only the PRIMARY-workset roots (non-key
# entries) into StandardPaths, and there is no workset.* tier in the snapshot.
#
# ⚑ WHERE THE PER-MODE VARIATION LIVES (spec §2c): HERE and nowhere downstream, so
# every rooted key and the box home spell themselves ONCE against ``@meta.box.path``
# / ``@workset.*``. The per-mode formula table is in the llm-doc.
#
# ⚑ A BOX ROOT THAT DOES NOT RESOLVE IS CATASTROPHIC, NOT COSMETIC — the foundation
# bind's src derefs it EMBEDDED, so a failure yields the host_src ``/home``, which L7
# then mkdir's and mounts OVER the box home, silently. See
# :func:`_assert_box_root_resolved`.


#: The box modes this floor knows how to root, as plain strings. An undeclared variant
#: is NOT a mode and is REFUSED rather than silently taking the primary/named arm.
#: Derived from :class:`~kanibako.settings.paths.BoxMode`, the one carrier of the set.
_BOX_MODES: frozenset[str] = frozenset(mode.value for mode in BoxMode)

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

#: The workset-LOCAL channel leaves: PRIMARY/NAMED carry a path, STANDALONE carries
#: the ``<None>`` spec §2c declares (no workset-local channels, so no
#: ``~/channels/workset`` mount).  The two leaves left over, ``mailboxes`` and
#: ``share_global``, are ALL PROJECTS and carry a path in every mode.
_WORKSET_LOCAL_CHANNEL_LEAVES: frozenset[str] = _WORKSET_CHANNEL_LEAVES - {
    "mailboxes", "share_global",
}

#: The RO DERIVED box-home SOURCE (spec ``:1015``) — the pid-0 FOUNDATION bind's src.
#: ⚑ NAMED, unlike its sibling floor keys, because it has readers OUTSIDE this module:
#: the assembly seam (``commands/start.py._install_assembly_collapse``) and
#: ``box show --effective``. One spelling for the producer below and both consumers.
BOX_HOME_KEY: Final[str] = "meta.box.home"


def workset_anchor_floor(
    *,
    mode: str,
    channelroot: str | None = None,
    workspaces: str | None = None,
    workset_channels: Mapping[str, str | None] | None = None,
) -> dict[str, object]:
    """Build the LAYOUT-anchor floor keys — workset roots + the box root (spec §2c).

    Every anchor is the spec's self-resolving @-ref FORMULA, so the per-mode
    variation is spelled HERE and nowhere downstream (§2c, §2a "Declaration roots").
    ``workset.boxes`` and ``workset.logs`` are PER-MODE; the vault roots, the canon
    contribution roots, and ``meta.box.home`` are UNIFORM. The formulas and what each
    one feeds: the llm-doc.

    ⚑ It also carries the NON-LAYOUT ``workset.*`` scalars — ``skip_kuid_check``,
    ``registry``, ``kuid``, ``workset.template``: each is a manifest row with a declared
    default that no floor emitted, so a whole-value ``@``-ref to it would resolve to
    ``__MISSING__``. A builder named for the anchors is the honest cost of one floor.

    ⚑ ``workset.logs`` is what makes the helper-log bind a SINGLE row for all modes, and
    there is deliberately NO ``meta.box.helper_log`` anchor: it is not a spec-declared
    key, so under §0's closed keyspace it is not one. Do not reintroduce it — one bind,
    one spelling.

    The three path arguments are RESOLVED host paths and enter as :func:`literal_expr`.

    *workset_channels* maps the RESOLVED channel paths into ``workset.channels.*``.  ⚑
    Each leaf is checked against :data:`_WORKSET_CHANNEL_LEAVES` and an undeclared one is
    REFUSED: this is the one place a floor builds a key from a caller-supplied NAME, and a
    free-form passthrough would open the closed keyspace (§0) from inside the floor.  ⚑
    ``mailboxes`` and ``share_global`` are ALL PROJECTS (§2c), so this argument is NOT
    ``None`` for a standalone box, which supplies the four LOCAL leaves itself — a
    caller-supplied value for one of those is REFUSED.

    *channelroot* is the resolved ``workset.channelroot`` — PRIMARY/NAMED only. For
    STANDALONE the floor supplies the ``None`` §2c declares, and a caller-supplied value
    is REFUSED. ⚑ A LITERAL, not the spec's ``@meta.workset.path/channels`` formula: the
    key is read on the DETECTION side before any snapshot exists, so the floor must carry
    the answer that pass already reached. ⚑ A nulled root in either mode leaves the four
    workset-LOCAL ``workset.channels.*`` leaves SUPPLIED as ``<None>``, and a present
    ``<None>`` is what lets §2a name the key.

    *workspaces* is the resolved ``workset.workspaces`` — NAMED/STANDALONE only. For
    PRIMARY the floor supplies the ``None`` §2c declares, and a caller-supplied value is
    REFUSED. ⚑⚑ A LITERAL FOR THE SAME REASON AS ``channelroot``, ONLY MORE SO: it is
    read pre-snapshot to decide WHAT KIND OF BOX THIS EVEN IS, so a floor that spelled
    the formula instead would let detection and the keyspace answer that question two
    ways. The launch cascade does NOT root a relative workset-tier value — only
    ``resolve_workset_dir_key`` does, and only pre-snapshot.

    ⚑ Every ``<None>`` arm is SUPPLIED as a present ``None``, never omitted: PRIMARY's
    ``workset.workspaces``, STANDALONE's ``workset.registry``, ``workset.template``,
    ``workset.channelroot`` and the four workset-LOCAL ``workset.channels.*`` leaves.
    A user value reaches the key through the settings cascade, which outranks this floor
    by merge level. A supplied ``<None>`` is a value and a default is a fallback that
    applies only where nothing was supplied ([R177]); an OMITTED key instead renders
    ``""`` inside an embedded ``@``-ref (a present ``None`` makes the whole value
    ``None``, spec §0). ``workset.kuid`` stays absent: its standalone arm is the PROSE
    ``<generated at creation>``, not ``<None>``.
    """
    if mode not in _BOX_MODES:
        raise SettingsError(
            f"workset_anchor_floor: unknown box mode {mode!r} (expected one of "
            f"{', '.join(sorted(_BOX_MODES))})"
        )
    # ⚑ The ``template`` LEAF is not re-typed here: ``launch.templates`` owns the one
    # spelling, and the whitelist entry that permits the leaf reads the same constant.
    # LOCAL import — ``settings`` does not depend on ``launch`` at module level.
    from kanibako.launch.templates import AGENT_TEMPLATE_STORE_REL

    standalone = mode == "standalone"
    floor: dict[str, object] = {
        # boxes/logs are PER-MODE; the vault roots are UNIFORM (§2c ALL PROJECTS) —
        # only the box BIND differs per mode.
        "workset.boxes": (
            "@meta.workset.path/box_data" if standalone else "@meta.workset.path/boxes"
        ),
        "workset.vault_ro": "@meta.workset.path/vault/ro",
        "workset.vault_rw": "@meta.workset.path/vault/rw",
        "workset.logs": "@workset.boxes" if standalone else "@meta.workset.path/logs",
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
        # The advisory kuid-CHECK knob, UNIFORM (the manifest declares one bool, not a
        # per-mode map). ⚑ Same defect as ``workset.channelroot`` below: declared with a
        # default and emitted by no floor, so ``@workset.skip_kuid_check`` dangled in
        # every launch snapshot. The value is the one the pre-snapshot reader
        # ``config.read_workset_skip_kuid_check`` already returns with no file — a
        # conformance case pins the two equal so they cannot drift apart.
        "workset.skip_kuid_check": True,
    }
    if standalone:
        # STANDALONE declares ``<None>`` for all seven (spec §2c): a lone box has no
        # registry tier, no template tier (a workset template seeds FUTURE boxes, of
        # which a standalone root has none; ``launch.templates`` omits the layer off the
        # SAME predicate — ``channels.has_workset_channels`` IS ``mode is not
        # standalone``), and no workset-local channels. ⚑ SUPPLIED, not omitted — the
        # docstring says why ([R177]).
        floor["workset.registry"] = None
        floor["workset.template"] = None
        floor["workset.channelroot"] = None
        for leaf in sorted(_WORKSET_LOCAL_CHANNEL_LEAVES):
            floor[f"workset.channels.{leaf}"] = None
    else:
        # PRIMARY/NAMED ONLY. ``workset.kuid`` has NO standalone line above, and the
        # absence is the point: its standalone arm "<generated at creation>" is PROSE,
        # not a value — ``paths.establish_standalone`` MINTS the kuid into the box's
        # own ``workset.yaml`` at create, so a floor literal would shadow nothing on a
        # real box and FABRICATE an id on a half-created one.
        # ⚑ The registry is spelled as the spec's own @-ref FORMULA, like every anchor
        # above — not as the resolved literal ``project/workset_registry.py`` joins at
        # use, which would make this a second carrier of one path.
        floor["workset.registry"] = "@meta.workset.path/registry.yaml"
        # The LAYER-3 SEED SOURCE (spec §2c/§2a) — same defect, same fix. Its value was
        # spelled ONLY by ``launch.templates.template_seed_defaults``, and that table
        # feeds the CREATE-time seed resolve alone, so for a box that ALREADY EXISTS
        # ``@workset.template`` resolved to ``__MISSING__`` at every launch. The seed
        # table now REFERENCES the key instead of declaring it, so this line is the one
        # spelling — and the ``template`` leaf itself still comes from the constant
        # ``launch.templates`` owns.
        floor["workset.template"] = f"@meta.workset.path/{AGENT_TEMPLATE_STORE_REL}"
        # ⚑ ``kuid.SENTINEL``, never a re-typed "00000": the sentinel's unmintable even
        # parity is what makes PRESENT-SENTINEL ("no kuid stored") distinguishable from
        # a wrong one, and that property lives with the codec.
        floor["workset.kuid"] = kuid.SENTINEL
    # ⚑ The CHANNEL ROOT the six leaves default off: a key the manifest promises must be
    # in the floor, or ``@workset.channelroot`` dangles and a default primary box has no
    # ``channelroot`` under its ``workset`` node.
    if channelroot is not None:
        if standalone:
            raise SettingsError(
                "workset_anchor_floor: workset.channelroot declares <None> for "
                "standalone (the manifest default is {standalone: null}); the floor "
                "supplies that None itself, so no caller may emit a path for it."
            )
        floor["workset.channelroot"] = literal_expr(channelroot)
    elif not standalone:
        # ⚑ A NULL CHANNEL ROOT LEAVES THE FOUR LOCAL LEAVES PRESENT AND ``<None>``,
        # exactly as STANDALONE supplies them above.  A ``<None>`` is not a MISSING
        # key: each STANDARD channel row emits an ``@workset.channels.<leaf>`` source,
        # so an absent leaf would omit the three ``~/channels/workset/*`` binds with
        # no file to name.  Supplied, the collapse omits the same three and the launch
        # names the key the user set.
        for leaf in sorted(_WORKSET_LOCAL_CHANNEL_LEAVES):
            floor[f"workset.channels.{leaf}"] = None
    # ⚑ The MEMBER-WORKSPACE root, same defect and same fix: a manifest row with real
    # NAMED and STANDALONE arms that no floor emitted, so ``@workset.workspaces`` was
    # ``__MISSING__`` in every launch snapshot and its dependent ``meta.box.workspace``
    # demanded a key that answered nowhere. The value is the RESOLVED literal — see the
    # docstring for why the formula would be wrong here more sharply than anywhere else.
    # PRIMARY declares ``<None>`` (spec §2c), supplied like standalone's arms above.
    if mode == "primary":
        floor["workset.workspaces"] = None
    if workspaces is not None:
        if mode == "primary":
            raise SettingsError(
                "workset_anchor_floor: workset.workspaces declares <None> for primary "
                "(the manifest default is {primary: null}); the floor supplies that "
                "None itself, so no caller may emit a path for it. A user's value "
                "reaches the key through the settings cascade."
            )
        floor["workset.workspaces"] = literal_expr(workspaces)
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
            if standalone and leaf in _WORKSET_LOCAL_CHANNEL_LEAVES:
                raise SettingsError(
                    f"workset_anchor_floor: workset.channels.{leaf} declares <None> "
                    "for standalone (spec §2c: a lone box has no workset-local "
                    "channels); the floor supplies that None itself, so no caller "
                    "may emit a path for it."
                )
            # ⚑ A NULL LEAF IS SUPPLIED AS A PRESENT ``None``, like every other
            # ``<None>`` arm above — the caller reached it through the keys, and a
            # stored ``<None>`` is a value, not a missing key.  ``literal_expr`` takes
            # a path, so a null arm must not be spelled through it.
            floor[f"workset.channels.{leaf}"] = (
                None if path is None else literal_expr(path)
            )
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


@dataclass(frozen=True)
class _AuthInputs:
    """The six resolved bools the auth chain decides from (Q61 sketch inputs).

    Read ONCE here so :func:`resolve_auth_source` (the tier decision) and
    :func:`_materialize_auth_active` (the three computed ``meta.*`` keys) cannot
    drift apart — two readers of one shape, not two shapes.
    """

    support: bool
    system_allow: bool
    workset_allow: bool
    global_sync: bool
    global_knob: bool
    workset_knob: bool


def _read_auth_inputs(snapshot: KeyStore) -> _AuthInputs:
    """Read the six auth-chain bools off the expanded snapshot.

    An absent ``box`` node means the floor was not injected → all False (fail
    CLOSED, never laundered into sharing). Each input is a real ``bool``
    terminal resolved by ``expand``; :func:`as_bool` does not launder either.
    """
    from kanibako.settings.settings_views import as_bool

    box_node = dict.get(snapshot, "box", __MISSING__)
    if not isinstance(box_node, KeyStore):
        return _AuthInputs(
            support=False,
            system_allow=False,
            workset_allow=False,
            global_sync=False,
            global_knob=True,
            workset_knob=True,
        )

    # The box-scoped RO capability MIRROR (change 8 — being ``meta.*``, a scope
    # FILE cannot repoint it).
    meta_node = dict.get(snapshot, "meta", __MISSING__)
    support = False
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

    return _AuthInputs(
        support=support,
        system_allow=system_allow,
        workset_allow=workset_allow,
        global_sync=global_sync,
        global_knob=global_knob,
        workset_knob=workset_knob,
    )


def _materialize_auth_active(snapshot: KeyStore) -> None:
    """Materialize the three computed sharing-state keys (Q61, ratified).

    Mutates *snapshot* in place — it is the launch-local expanded tree, owned by
    the caller. The sketch, verbatim in intent: ``meta.workset.auth.global_active``
    is system-allow AND workset-sync; ``meta.box.auth.global_active`` is
    system-allow AND the box global knob; ``meta.box.auth.workset_active`` is
    false unless the workset allows AND the box workset knob is on, then true
    when the workset globally syncs, else the negation of box-global-active. All
    three are false when the agent does not support sharing.

    ⚑ Post-expand, beside the B5 mirror below: ``expand`` resolves ONLY @-refs /
    $VAR / ~ and does NOT evaluate ``&&`` (module note), so — like the effective
    enables in :func:`resolve_auth_source` — these ANDs exist in PYTHON, never as
    floor expressions. Reads via :func:`_read_auth_inputs`, the same six bools
    the tier decision reads, so the keys and the tier cannot disagree.
    Reads/writes via the UNBOUND ``dict`` protocol (S3) so a key named ``get`` /
    ``auth`` cannot shadow.
    """
    inputs = _read_auth_inputs(snapshot)
    if inputs.support:
        box_global_active = bool(inputs.system_allow and inputs.global_knob)
        workset_global_active = bool(inputs.system_allow and inputs.global_sync)
        if inputs.workset_allow and inputs.workset_knob:
            if inputs.global_sync:
                box_workset_active = True
            else:
                box_workset_active = not box_global_active
        else:
            box_workset_active = False
    else:
        box_global_active = False
        workset_global_active = False
        box_workset_active = False
    meta_node = dict.get(snapshot, "meta", __MISSING__)
    if not isinstance(meta_node, KeyStore):
        meta_node = KeyStore()
        snapshot["meta"] = meta_node
    meta_workset = dict.get(meta_node, "workset", __MISSING__)
    if not isinstance(meta_workset, KeyStore):
        meta_workset = KeyStore()
        meta_node["workset"] = meta_workset
    workset_auth = dict.get(meta_workset, "auth", __MISSING__)
    if not isinstance(workset_auth, KeyStore):
        workset_auth = KeyStore()
        meta_workset["auth"] = workset_auth
    workset_auth["global_active"] = workset_global_active
    meta_box = dict.get(meta_node, "box", __MISSING__)
    if not isinstance(meta_box, KeyStore):
        meta_box = KeyStore()
        meta_node["box"] = meta_box
    box_auth = dict.get(meta_box, "auth", __MISSING__)
    if not isinstance(box_auth, KeyStore):
        box_auth = KeyStore()
        meta_box["auth"] = box_auth
    box_auth["global_active"] = box_global_active
    box_auth["workset_active"] = box_workset_active


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
    inputs = _read_auth_inputs(snapshot)

    box_node = dict.get(snapshot, "box", __MISSING__)
    if not isinstance(box_node, KeyStore):
        return AuthSource(
            tier="box",
            global_enabled=False,
            workset_enabled=False,
            global_sync=False,
            workset_source=None,
        )

    # The RO DERIVED per-box workset source root, sibling of meta.box.agent:
    # a resolved string; absent / None / "" all coerce to None.
    meta_node = dict.get(snapshot, "meta", __MISSING__)
    workset_source: str | None = None
    if isinstance(meta_node, KeyStore):
        meta_box = dict.get(meta_node, "box", __MISSING__)
        if isinstance(meta_box, KeyStore):
            meta_box_auth = dict.get(meta_box, "auth", __MISSING__)
            if isinstance(meta_box_auth, KeyStore):
                wp = dict.get(meta_box_auth, "workset_path", __MISSING__)
                if isinstance(wp, str) and wp:
                    workset_source = wp

    # Effective enables (the Python AND standing in for the spec's %… && …%).
    global_enabled = bool(inputs.support and inputs.system_allow and inputs.global_knob)
    workset_enabled = bool(inputs.support and inputs.workset_allow and inputs.workset_knob)

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
        global_sync=inputs.global_sync,
        workset_source=workset_source,
    )



class ResolveSubject(Enum):
    """WHAT a resolve is FOR — the words its §0 refusal speaks in.

    :func:`refuse_read_time_faults` takes one. ⚑ A CLOSED CHOICE, NOT A STRING: each
    member carries the whole wording, so a caller picks a subject and cannot compose a
    cure line nobody measured. Both cure lines ARE measured — a per-key ``reset``
    refuses an undeclared name (``unknown config key``), and every verb named as
    refusing too resolves through this seam.
    ⚑ THE DISCLAIMER IS THE PER-KEY FORM, AND ONLY THAT FORM. ``reset --all --force``
    at either noun DOES remove an undeclared entry inside that noun's own table — it
    drops the whole table — so an unqualified "``reset`` cannot remove it" is a false
    claim printed to the user. MEASURED both ways on a scratch HOME.
    ⚑ ``BOX``'s wording is the launch's; the pins that assert it live in
    ``tests/test_settings/test_settings_launch.py``.
    """

    BOX = (
        "this box",
        "'kanibako box reset <key>' cannot remove what is not a key, and 'kanibako "
        "box show --effective' resolves through this same seam, so it refuses too.",
    )
    WORKSET = (
        "this working set",
        "'kanibako workset reset <workset> <key>' cannot remove what is not a key, "
        "and 'kanibako workset show --effective' and 'kanibako workset share list "
        "--effective' resolve through this same seam, so they refuse too.",
    )
    SYSTEM = (
        "the system scope",
        "'kanibako system reset <key>' cannot remove what is not a key, and "
        "'kanibako system show --effective' resolves through this same seam, so it "
        "refuses too.",
    )

    def __init__(self, what: str, cure_note: str) -> None:
        #: The resolve's subject as the refusal's first line names it.
        self.what = what
        #: The last line: which verbs cannot help, and which refuse too.
        self.cure_note = cure_note


def _loaded_tiers(files: Sequence[SettingsFile]) -> tuple[SettingsFile, ...]:
    """The settings files this resolve ACTUALLY read: *files*, minus every tier with
    nothing on disk.

    ⚑⚑ ONE LIST, AND BOTH HALVES OF THE REFUSAL READ IT (P10). The two halves ask
    different questions of the same tiers — ``settings_assemble.retired_cure`` judges
    them, and the generic message NAMES them — so a tier known to one and not the
    other produces a refusal that judges a file it never mentions. Measured before
    this existed: an undeclared key in ``settings_base.yaml`` alone printed the
    box's ``box.yaml``, a file that does not carry it, while disclaiming ``box
    reset`` in the same breath — no working move for the user at all.

    ⚑ AND A TIER WITH NO FILE IS NOT A TIER THAT WAS LOADED. Naming an absent
    ``/etc/kanibako/settings_base.yaml`` as a file to hand-edit sends a user to a
    file that is not there. ``base`` is absent on most machines, so this is the
    common case, not the corner one.
    """
    return tuple(f for f in files if f.loaded)


def _refuse_undeclared_snapshot(
    store: KeyStore,
    *,
    files: Sequence[SettingsFile],
    written: Sequence[_WrittenLevel],
    subject: ResolveSubject,
) -> None:
    """RAISE naming EVERY resolved path the CLOSED keyspace does not declare (§0).

    Spec §0: *"reading, setting, or resolving an undeclared key is an ERROR that
    NAMES the offending key — never a silent accept, never a fabricated default,
    never a free-form passthrough."* This is the RESOLVE third of that sentence;
    ``config_keys`` holds the read/set thirds.

    ⚑ EVERY offending path, not the first. A user hand-edits the cure, and a
    refusal that names one entry per attempt turns one edit into N launches.
    (``agent_file._refuse_undeclared_state`` does the same for ONE agent file, before
    any snapshot exists.)

    ⚑ THE CURE IS A HAND-EDIT AND THE MESSAGE MUST SAY SO. ``box reset <key>``
    cannot remove what is not a key, and ``box show --effective`` resolves through
    this very seam, so it refuses too — leaving a user who is told "reset it" with
    no working move. *subject* picks the noun those verbs carry
    (:class:`ResolveSubject`, which says why the per-key form is the one named).
    ⚑ BOTH SPELLINGS ARE MEASURED: there is no ``config`` noun
    (``config_keys._SCOPE_READ_COMMAND``), and a cure a user cannot type is worse
    than no cure.
    *files* are the tiers the resolve READ, MOST-SPECIFIC FIRST; :func:`_loaded_tiers`
    turns them into the list the retirement choice judges. ⚑ EACH ENTRY IS FILED UNDER
    THE FILE THAT CARRIES IT (:func:`_carrying_files`, over the per-file *written*
    levels), never under every file the resolve loaded: a list of innocent files is a
    hand-edit the user cannot aim. An entry no file carries came from a non-file input,
    and the message says so instead of naming files.

    ⚑ A RETIRED SPELLING GETS ITS OWN MESSAGE, NOT THIS ONE
    (``settings_assemble.retired_cure``) — the generic text is the FALLBACK for an
    entry nothing more specific is known about. It is consulted only once there is
    something to refuse, so an ordinary key pays nothing for it.

    ⚑ NO BYPASS — no env var, no exemption list, no origin discriminator. A
    name-keyed escape is the carve-out the closed keyspace exists to refuse, and it
    would hide the next finding behind itself.
    """
    findings = undeclared_store_paths(store, oracle=keyspace_verdict)
    if not findings:
        return
    retired_cure(_loaded_tiers(files))
    named, entries, them = undeclared_listing(findings)
    carriers = _carrying_files(findings, written, files)
    filed = {seg for keys in carriers.values() for seg in keys}
    where = "\n".join(
        f"    - {path}: " + ", ".join(_finding_name(seg, findings) for seg in keys)
        for path, keys in carriers.items()
    )
    stray = [seg for seg, _ in findings if seg not in filed]
    if stray:
        where += ("\n" if where else "") + (
            "    - " + ", ".join(_finding_name(seg, findings) for seg in stray)
            + ": in no settings file this resolve read — it came from an input that is "
            "not a settings file (the command line, the persona store, or an agent "
            "plugin's or kanibako's own defaults)"
        )
    raise SettingsError(
        f"the settings resolved for {subject.what} carry {entries} "
        f"(spec §0 — the keyspace is CLOSED):\n"
        f"{named}\n"
        f"kanibako will not resolve settings that carry {them}: an undeclared key "
        f"has no meaning to give the box, and passing it through would be the very "
        f"'anything goes' behavior the closed keyspace replaces.\n"
        f"  Fix: remove {them} BY HAND, each from the file listed with it:\n"
        f"{where}\n"
        f"  {subject.cure_note}"
    )


def _finding_name(
    segments: tuple[str, ...], findings: Sequence[tuple[tuple[str, ...], Judgment]],
) -> str:
    """*segments* rendered as :func:`undeclared_listing` renders that finding."""
    key_len = next(j.key_len for seg, j in findings if seg == segments)
    return display_store_path(segments, key_len)


def _carrying_files(
    findings: Sequence[tuple[tuple[str, ...], Judgment]],
    written: Sequence[_WrittenLevel],
    files: Sequence[SettingsFile],
) -> dict[str, list[tuple[str, ...]]]:
    """``{file: [finding, ...]}`` — each finding under EVERY written file that holds its path.

    *written* is most-specific-first and so is the result. The ``base`` level has the
    floor folded in, so the base FILE's own view (from *files*) decides what it holds:
    a floor-only entry is never filed there, and an entry the file writes at a path the
    floor also holds still is.
    """
    base_view: object = next((f.view for f in files if f.level == "base" and f.loaded), {})
    carriers: dict[str, list[tuple[str, ...]]] = {}
    for level, path, floor_store in written:
        if path is None:
            continue
        own = base_view if floor_store is not None else level
        held = {seg for seg, _ in walk_store_paths(own)} if isinstance(own, dict) else set()
        keys = carriers.setdefault(str(path), [])
        keys.extend(seg for seg, _ in findings if seg in held and seg not in keys)
    return {path: keys for path, keys in carriers.items() if keys}


def _path_key_leaves(store: KeyStore) -> list[tuple[str, object]]:
    """``(key, value)`` for every leaf of *store* that is a PATH key, in walk order.

    ⚑ THE PATH-KEY SET IS :func:`~kanibako.settings.config_keys.is_path_valued_key`,
    asked of every leaf — the registry's ``type: path`` rows plus the parametric
    families it recognizes by parser. No list is kept here, so a key added to the
    registry is swept the day it lands (P13).
    ⚑ A LEAF IS SWEPT ONLY IF THE §0 ORACLE CALLS IT A KEY. The predicate parses CLI
    spellings, so it also accepts the bare ``template`` / ``canon`` and nested shapes
    such as ``agent.claude.nav.template``; in a store those are undeclared entries
    (§0), and a BARE RELATIVE refusal naming one would send the user to fix a key
    that does not exist. :func:`keyspace_verdict` is the oracle
    :func:`_refuse_undeclared_snapshot` refuses on, so the two cannot disagree (P10).
    ⚑ A SEGMENT WITH A DOT STOPS THE KEY: it is data (a bind destination), never key
    path — the same rule ``settings_keyspace.classify_store_path`` applies. The
    oracle cannot catch it: ``("box", "secret_path.X")`` joins to a declared key.
    """
    leaves: list[tuple[str, object]] = []
    for segments, is_node in walk_store_paths(store):
        if is_node or any("." in seg for seg in segments):
            continue
        key = ".".join(segments)
        if is_path_valued_key(key) and keyspace_verdict(key).cls is KeyClass.KEY:
            leaves.append((key, snapshot_leaf(store, key)))
    return leaves


#: One settings level a USER wrote, the file it was read from (``None`` when the caller
#: supplied none), and the FLOOR folded into it as a store (the ``base`` level only;
#: ``None`` everywhere else). See :func:`_refuse_ambiguous_path_values`.
_WrittenLevel = tuple[KeyStore, Path | None, KeyStore | None]


def _refuse_ambiguous_path_values(
    written: Sequence[_WrittenLevel], expanded: KeyStore, *, ctx: ResolveCtx,
) -> None:
    """RAISE naming EVERY path key a settings file stores as a BARE RELATIVE ([R147], read time).

    The generic read-time sweep: ``paths._refuse_bare_relative`` covers the
    Layer-1/Layer-2 keys and ``workset_dirkeys.resolve_workset_dir_key`` the workset
    dir keys, but a key consumed some other way — ``workset.auth.path``, read by
    credsync through ``meta.box.auth.workset_path`` as a copy-route source root — met
    neither, so a hand-edited ``workset.yaml`` carried one through. This asks the one
    question of every path key in every level a user writes, so a new consumer cannot
    open that gap again.

    ⚑ SAME PREDICATE, SAME ANCHOR, SAME WORDING as the set-time guard and both
    read-time seams: :func:`path_key_anchor` names the other reading and
    :func:`ambiguous_path_value_error` writes it. The anchor is resolved against
    *expanded*, which is why this runs after the expand; an anchor that does not
    resolve here is named by its own ref spelling, as the helper documents.
    ⚑ EVERY offender, not the first, each with the FILE that carries it — the cure is
    a hand-edit, and one name per attempt turns one edit into N launches.

    ⚑⚑ IT JUDGES THE *written* LEVELS, NEVER THE MERGE, because [R147] governs STORED
    key values. The merge also carries what kanibako supplies itself — the floor
    (``system.*`` paths already resolved, and refused if bare, by ``paths.py``), the
    plugin descriptor defaults, the live persona values and the CLI level — and a
    refusal telling a user to fix a value no file of theirs holds is a false message.
    That is also why the ``base`` level passes its folded floor: the site file
    overlays the floor into ONE partial, and a leaf still holding the floor's own
    value is the floor's, not the file's.
    ⚑ A SHADOWED value is judged too: it is still a stored value, and it wins the
    moment the level above it is removed.
    ⚑ The test is on the STORED spelling: ``$XDG_DATA_HOME/x`` is legal even where that
    variable answers something odd, and the message quotes what the user typed.
    ⚑ ONLY A NON-EMPTY STRING IS JUDGED. ``None`` is a reset or a standalone pin. A
    non-scalar is not judged here: ``refuse_non_scalar_family_value`` refuses one by name
    in the ``env`` and ``secret_path`` families; the Layer-1/Layer-2 read stringifies
    one (``config._flatten_dotted``) and ``paths._refuse_bare_relative`` refuses the
    string as a bare relative; at any other path key (``workset.auth.path``,
    ``box.canon``) this resolve passes it through and nothing refuses it by name.
    """
    offenders = [
        (key, value, path)
        for level, path, floor_store in written
        for key, value in _path_key_leaves(level)
        if isinstance(value, str) and value and not is_unambiguous_path_value(value)
        and (floor_store is None or snapshot_leaf(floor_store, key) != value)
    ]
    if not offenders:
        return

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        del chain  # *expanded* holds terminals: nothing is resolved transitively.
        value = snapshot_leaf(expanded, ref)
        if isinstance(value, str) and value:
            return value
        raise SettingsError(f"'@{ref}' has no resolved value in this snapshot")

    refusals = []
    for key, value, path in offenders:
        anchor_ref, anchor_label = path_key_anchor(key)
        try:
            anchor: str | None = expand_expr(
                anchor_ref, space="host", ctx=ctx, lookup=lookup,
            )
        except SettingsError:
            anchor = None
        refusals.append(ambiguous_path_value_error(
            key, value,
            anchor=anchor or anchor_ref,
            anchor_ref=anchor_ref if anchor else None,
            where=str(path) if path is not None else None,
            anchor_label=anchor_label,
        ))
    raise SettingsError("\n\n".join(refusals))


def refuse_read_time_faults(
    written: Sequence[_WrittenLevel],
    expanded: KeyStore,
    *,
    ctx: ResolveCtx,
    files: Sequence[SettingsFile],
    subject: ResolveSubject,
    tiers: Sequence[str],
) -> None:
    """RAISE for any stored value a resolve may not proceed with.

    The READ-TIME refusals, IN ORDER. Runs after ``expand``: [R147]'s bare-relative sweep
    (:func:`_refuse_ambiguous_path_values`, over *written*), then §0's undeclared-key
    refusal (:func:`_refuse_undeclared_snapshot`, over *expanded*), then §2c's entry at
    an internal bind's dest (:func:`_refuse_internal_bind_entries`, over *written*), then
    §0's per-owner values (:func:`_refuse_inherited_per_owner`, over *written*, each level's
    scope in *tiers*).
    ⚑ ONE CARRIER OF THE ORDER: the launch (:func:`build_launch_snapshot`) and the workset preview
    (``commands/workset_cmd._workset_preview_entries``) both call this, so a resolve
    route cannot run one refusal and skip the other.
    *files* are the tiers the caller READ, most-specific first
    (``settings_assemble.read_settings_files``); *subject* is who the
    resolve is for (:class:`ResolveSubject`).
    ⚑ Each raises on its own, so a file with both faults reports the path value first
    and the undeclared entry on the next run.
    """
    _refuse_ambiguous_path_values(written, expanded, ctx=ctx)
    _refuse_undeclared_snapshot(
        expanded, files=files, written=written, subject=subject,
    )
    _refuse_internal_bind_entries(written)
    _refuse_inherited_per_owner(written, tiers, expanded)


def _refuse_internal_bind_entries(written: Sequence[_WrittenLevel]) -> None:
    """RAISE naming EVERY settings-file entry that would repoint or remove an INTERNAL bind.

    The internal binds (:func:`core_defaults.internal_bind_keys`) are not user keys and
    not repointable (spec §2c).  Two shapes reach one, and both refuse:
    an entry AT an internal dest — a source list or a ``None``, in ANY dest-keyed
    category — which the post-merge re-impose would otherwise override silently; and a
    MASK (present, not ``None``) AT or ABOVE one, which the collapse would let swallow it.
    A mount or copy entry at a PARENT dir is not refused: the collapse folds binds
    parent-first, so the internal bind still mounts on top (measured, every category).
    ⚑ Judged per WRITTEN level, like the path sweep: a ``base`` entry still equal to the
    folded floor's is the floor's own internal bind, not the file's.
    ⚑ A dest spelled through an ``@``-ref or ``$VAR`` is compared as written, unexpanded.
    The per-entry judgment is :func:`internal_bind_refusals`, which the write verbs share.
    """
    refusals = []
    for level, path, floor_store in written:
        for segments, is_node in walk_store_paths(level):
            if not is_node or any("." in seg for seg in segments):
                continue
            arm = ".".join(segments)
            if not is_terminal_category_key(arm):
                continue
            entries = snapshot_leaf(level, arm)
            if not isinstance(entries, dict):
                continue
            floor_entries = (
                snapshot_leaf(floor_store, arm) if floor_store is not None else None
            )
            refusals.extend(internal_bind_refusals(
                arm, entries,
                where=str(path) if path is not None else "a settings file",
                when="stored",
                floor_entries=floor_entries if isinstance(floor_entries, dict) else None,
            ))
    if refusals:
        raise SettingsError("\n".join(refusals))


#: The containment scope each ``written`` tier's file speaks for.
_TIER_SCOPE: Final[dict[str, str]] = {
    "box": "box", "workset": "workset", "agent": "agent", "agent.default": "agent",
    "system": "system", "base": "system",
}
#: The owner of an UNDECLARED entry, by its category key's scope (keyspec §0).
_CATEGORY_OWNER: Final[dict[str, str]] = {"box": "box", "workset": "workset", "system": "shared"}


def _refuse_inherited_per_owner(
    written: Sequence[_WrittenLevel], tiers: Sequence[str], expanded: KeyStore,
) -> None:
    """RAISE naming every per-owner key and ``bindings.ro``/``bindings.rw`` entry whose WINNING
    value a containing scope's file stores and which reaches no owner identity (keyspec §0
    "Per-owner resources").

    The set door's judgment, :func:`~kanibako.settings.config.reaches_identity`, through the
    written levels' raw values. A file judges every mode it feeds: a workset file this box's
    mode, any other file all three. A declared entry's owner is its row's
    (:func:`core_defaults.bind_dest_owners`); an undeclared one takes its category key's. An
    entry under ``agent.<agent>.*`` is not judged: undeclared ones are exempt, and every
    declared plugin bind row is ``shared``.
    """
    def stored(ref: str) -> object:
        for level, _path, _floor in written:
            value = snapshot_leaf(level, ref)
            if value is not __MISSING__:
                return value
        return None

    mode = snapshot_leaf(expanded, "meta.box.mode")
    declared = core_defaults.bind_dest_owners()
    rank = SCOPE_CONTAINMENT.index
    seen: set[object] = set()
    refusals: list[str] = []

    def judge(name: str, value: object, owner: str, agent: "str | None", *,
              tier: str, path: Path | None, key: str, tail: str = "") -> None:
        scope = _TIER_SCOPE[tier]
        if owner == "shared" or not isinstance(value, str) or not value:
            return
        if rank(scope) >= rank("workset" if owner == "partition" else owner):
            return
        modes = (BoxMode(mode),) if scope == "workset" and isinstance(mode, str) else (
            (BoxMode.primary, BoxMode.named) if scope == "workset" else tuple(BoxMode))
        if all(reaches_identity(value, owner, m, key=key, stored=stored) for m in modes):
            return
        noun, identity, shared_by, file_owner = PER_OWNER_SET_WORDS[owner]
        refusals.append(ERR_PER_OWNER_LAUNCH % (
            name, value, path if path is not None else "a settings file", noun, identity,
            shared_by, f"{value.rstrip('/')}/{uniform_anchor(owner, agent)}", file_owner,
            tail,
        ))

    for (level, path, floor_store), tier in zip(written, tiers):
        for segments, is_node in walk_store_paths(level):
            dotted = ".".join(segments)
            if not is_node and not any("." in seg for seg in segments) and dotted not in seen:
                seen.add(dotted)
                value = snapshot_leaf(level, dotted)
                if floor_store is None or snapshot_leaf(floor_store, dotted) != value:
                    owner, agent = key_owner(dotted)
                    judge(dotted, value, owner, agent, tier=tier, path=path, key=dotted)
            if not (is_node and dotted.endswith((".bindings.ro", ".bindings.rw"))
                    and is_terminal_category_key(dotted)):
                continue
            entries = snapshot_leaf(level, dotted)
            floor_entries = snapshot_leaf(floor_store, dotted) if floor_store else None
            for dest, entry in dict.items(entries) if isinstance(entries, dict) else ():
                norm = normalize_bind_dest(dest)
                if (dotted, norm) in seen:
                    continue
                seen.add((dotted, norm))
                if segments[0] == "agent" or entry is None or (
                    isinstance(floor_entries, dict)
                    and dict.get(floor_entries, dest, __MISSING__) == entry
                ):
                    continue
                try:
                    src, _opts = unpack_bind_entry(entry)
                except SettingsError:
                    continue  # malformed: its own refusal names it
                owner = declared.get(norm, _CATEGORY_OWNER.get(segments[0], "shared"))
                tail = "" if norm in declared else PER_OWNER_SHARE_TAIL % (
                    f"system.{dotted.partition('.')[2]}")
                judge(entry_label(shown_key(dotted), dest), src, owner, None,
                      tier=tier, path=path, key=dotted, tail=tail)
    if refusals:
        raise SettingsError("\n\n".join(refusals))


def internal_bind_refusals(
    arm: str, entries: dict[str, object], *, where: str,
    when: Literal["write", "stored"],
    floor_entries: dict[str, object] | None = None,
) -> list[str]:
    """One refusal line per entry of the dest-keyed map *entries* at key *arm* that would
    repoint or remove an INTERNAL bind (spec §2c); empty when none does.

    THE ONE CARRIER of that judgment and its wording: the resolve
    (:func:`_refuse_internal_bind_entries`) and a WRITE verb (``workset share add``) both call
    it, so a verb cannot store what the next resolve refuses.  *where* names the file.  An
    entry equal to its *floor_entries* counterpart is the floor's own bind and is skipped.

    ⚑ *when* is REQUIRED and has NO DEFAULT: it says whether *entries* are being WRITTEN NOW
    or are ALREADY STORED, which decides the WORDING and nothing else — the judgment and the
    set of refusing entries are identical either way.  ``"stored"`` is the resolve's text,
    byte for byte.  ``"write"`` cannot repeat it: ``workset share add`` runs here BEFORE it
    writes, so "remove the entry" would name an entry that was never written, in a file that
    may not exist, and a user cannot follow it.  The write form says nothing was written
    instead, and names the choice the user still has.
    """
    from kanibako.settings.store_collapse import is_within

    internal = sorted({dest for _arm, dest in core_defaults.internal_bind_keys()})
    is_mask = arm.split(".")[-1] == "masks"
    refusals: list[str] = []
    for dest, value in dict.items(entries):
        if floor_entries is not None and (
            dict.get(floor_entries, dest, __MISSING__) == value
        ):
            continue
        norm = normalize_bind_dest(dest)
        if norm in internal:
            if when == "write":
                refusals.append(
                    f"{entry_label(shown_key(arm), dest)} cannot be added to {where}: its "
                    f"destination is that of an internal kanibako bind (spec §2c), which "
                    f"is not repointable. Nothing was written; choose another destination."
                )
                continue
            refusals.append(
                f"{entry_label(shown_key(arm), dest)} in {where} is at the "
                f"destination of an internal kanibako bind (spec §2c), not repointable; "
                f"remove the entry."
            )
            continue
        if not is_mask or value is None:
            continue
        # The write form here has no caller yet: ``share add`` writes only ``bindings``.
        if when == "write":
            refusals.extend(
                f"{entry_label(shown_key(arm), dest)} cannot be added to {where}: "
                f"it would remove the internal kanibako bind at {hidden} (spec §2c), "
                f"which is not suppressible. Nothing was written; mask a narrower path."
                for hidden in internal if is_within(hidden, norm)
            )
            continue
        refusals.extend(
            f"{entry_label(shown_key(arm), dest)} in {where} would remove the internal "
            f"kanibako bind at {hidden} (spec §2c), which is not suppressible; mask a "
            f"narrower path."
            for hidden in internal if is_within(hidden, norm)
        )
    return refusals


def _workset_channel_floor_values(
    part: "WorksetPartition", wch: "WorksetChannels | None",
) -> "tuple[str | None, dict[str, str | None]]":
    """The ``(workset.channelroot, workset.channels.*)`` floor values from the two
    resolved channel sets.

    ⚑ TWO DIFFERENT MODE GATES, which is why this takes two sets and not one.
    The four workset-LOCAL leaves and the channel root are PRIMARY/NAMED only (*wch*
    is ``None`` for standalone); the two partition leaves (``mailboxes`` /
    ``share_global``) are ALL PROJECTS (§2c) and a standalone box installs them like
    anyone else.  Reading the whole family off a single ``None``-for-standalone
    helper is how three of the six ended up installed by no floor in any mode.

    ⚑ A NULL LEAF IS HANDED OVER AS ``None``, not stringified.  ``str(None)`` is the
    four-character path ``"None"``, which the expander accepts and the mount then
    reads as a relative source (or, for a MOUNT, a named volume).  A present ``None``
    is a value the floor already knows how to carry — :func:`workset_anchor_floor`
    SUPPLIES ``<None>`` for every arm §2c declares null.
    """
    leaves: "dict[str, str | None]" = {
        "mailboxes": None if part.mailboxes is None else str(part.mailboxes),
        "share_global": (
            None if part.share_global is None else str(part.share_global)
        ),
    }
    if wch is None:
        return None, leaves
    leaves.update({
        "common": None if wch.common is None else str(wch.common),
        "chat": None if wch.chat is None else str(wch.chat),
        "broadcast": (
            None if wch.chat_broadcast is None else str(wch.chat_broadcast)
        ),
        "share": None if wch.share is None else str(wch.share),
    })
    return str(wch.root), leaves


def _workset_workspaces_floor_value(
    mode: str, ws_root_literal: "str | None", *, early: EarlyScope,
) -> "str | None":
    """The resolved ``workset.workspaces`` the caller hands the launch floor — ⚑ NOT primary.

    ⚑ THIS IS THE VALUE THE PRE-SNAPSHOT PASS ALREADY REACHED, not a second answer to
    the same question.  It is the same ``project.workset.resolve_workset_workspaces``
    call, on the same root, that ``paths.resolve_standalone_project`` makes for
    standalone and that ``WorksetSpec.workspaces_dir`` (via ``Workset.workspaces_dir``)
    makes for named — one function, one repoint read, one grammar
    (``settings/workset_dirkeys.py``).  Composing ``<root>/workspaces`` here instead
    would be a second carrier and would lose every repoint.

    ⚑ PRIMARY RETURNS ``None`` — nothing to hand over: the manifest declares
    ``{primary: null, …}``, and :func:`workset_anchor_floor` supplies that present
    ``None`` itself and REFUSES a caller's primary value, so the arm has one carrier.
    A root that NULLS the key also returns ``None`` (no floor value): the root's own
    workset tier carries that ``<None>`` into the snapshot, and the default leaf would
    be a path the user said does not exist.

    *ws_root_literal* is the SAME string ``meta.runtime.ws_root`` is built from
    (``proj.group.root`` named / ``proj.metadata_path`` standalone), so the workspaces
    dir and the workset root cannot be resolved against two different roots.
    """
    if mode == "primary":
        return None
    from kanibako.project.workset import (
        load_workset_settings_doc, resolve_workset_workspaces,
    )

    if ws_root_literal is None:  # pragma: no cover - guarded by the caller's mode split
        raise ValueError(
            f"workset.workspaces floor: mode {mode!r} has no workset root literal"
        )
    root = Path(ws_root_literal)
    workspaces = resolve_workset_workspaces(
        root, load_workset_settings_doc(root), standalone=(mode == "standalone"), early=early,
    )
    return str(workspaces) if workspaces is not None else None


class _LaunchInputKwargs(TypedDict):
    """The :func:`build_launch_snapshot` keywords :meth:`LaunchInputs.as_kwargs` supplies."""

    subject: ResolveSubject
    ctx: ResolveCtx
    system_path: Path | None
    box_path: Path | None
    workset_path: Path | None
    auth_chain: Mapping[str, object] | None
    meta_runtime: Mapping[str, object] | None
    meta_identity: Mapping[str, object] | None
    workset_anchor: Mapping[str, object] | None
    prefs: Sequence[PrefRequest] | None


@dataclass(frozen=True)
class LaunchInputs:
    """The inputs every :func:`build_launch_snapshot` call of one resolve shares.

    Built by :func:`resolve_inputs`, the ONE builder; a resolver passes
    :meth:`as_kwargs` plus its own extras, so no two resolves of one target can
    spell the context, the floors or the file pair differently.

    ⚑ BUILT PER AGENT: every field but *subject*, the file pair and *prefs* depends
    on *agent_name*, so an instance is never reused across an agent change.

    *subject* is the resolve's target, and the builder's §0 refusal speaks in its
    words. *cascade_box_path* is ``None`` for a resolve with no box, and
    *cascade_workset_path* for one in no working set.

    *system_floor* is the resolved ``system.*`` path tier
    (``paths.system_path_floor``); it is not a :func:`build_launch_snapshot`
    keyword, so each caller folds it into its own ``default_categories``.
    """

    subject: ResolveSubject
    ctx: ResolveCtx
    system_path: Path | None
    system_floor: Mapping[str, str | None]
    meta_runtime: Mapping[str, object]
    meta_identity: Mapping[str, object]
    workset_anchor: Mapping[str, object]
    auth_chain: Mapping[str, object]
    cascade_box_path: Path | None
    cascade_workset_path: Path | None
    prefs: tuple[PrefRequest, ...]

    def as_kwargs(self) -> _LaunchInputKwargs:
        """The :func:`build_launch_snapshot` keywords these inputs supply."""
        return {
            "subject": self.subject,
            "ctx": self.ctx,
            "system_path": self.system_path,
            "box_path": self.cascade_box_path,
            "workset_path": self.cascade_workset_path,
            "auth_chain": self.auth_chain,
            "meta_runtime": self.meta_runtime,
            "meta_identity": self.meta_identity,
            "workset_anchor": self.workset_anchor,
            "prefs": self.prefs,
        }


#: The identity keys whose value IS the box name or a channel address spelled from it.
_BOX_NAME_KEYS: Final = (
    "meta.box.name", "meta.box.inbox", "meta.box.share_global", "meta.box.share_workset",
)

#: The key prefixes only a BOX gives a value to (the box's own identity anchors).
_BOX_ONLY_PREFIXES: Final = ("meta.box.",)

#: The key prefixes only a WORKING SET (or a box in one) gives a value to.
_WORKSET_ONLY_PREFIXES: Final = ("meta.workset.", "workset.")

#: The spec §1A ``meta.runtime.*`` keys resolved PER WORKING SET. The rest of that
#: block (``meta.runtime.{user,admin}.*``) names HOST files, which a resolve with
#: no working set still has.
_WORKSET_RUNTIME_KEYS: Final = frozenset({
    "meta.runtime.ws_root", "meta.runtime.ws_name", "meta.runtime.project_type",
})


#: What a box-less preview prints for a value that reads a key only a box can answer.
DEPENDS_ON_THE_BOX: Final = "(depends on the box)"


def depends_on_the_box(refs: Collection[str], *, in_workset: bool) -> bool:
    """True if a box-less resolve cannot know a value that read *refs* (design 1C, N-e).

    A key the box-less resolve OMITS has no value here, and any ``box.*`` key may be set by
    a box's own file, so a value reading either is the box's to decide.
    """
    return any(
        ref.startswith("box.") or _box_less_omits(ref, in_workset=in_workset) for ref in refs
    )


def _box_less_omits(key: str, *, in_workset: bool) -> bool:
    """True if a resolve with no box (in a working set, or not) has no value for *key*."""
    if key.startswith(_BOX_ONLY_PREFIXES):
        return True
    return not in_workset and (
        key.startswith(_WORKSET_ONLY_PREFIXES) or key in _WORKSET_RUNTIME_KEYS
    )


def _omit_derived(
    ctx: ResolveCtx, is_seed: Callable[[str], bool], *floors: dict[str, object],
) -> None:
    """Drop from *floors* every key *is_seed* names, and every key derived from one.

    Spec §0, *"never a fabricated default"*: a resolve whose target lacks what a key
    names (a box with no name yet, a working set with no box, the system scope)
    OMITS that key rather than guess it. Derived = the value ``@``-refers to a seed
    or to a key already dropped, followed to a fixed point (primary / named
    ``meta.box.path`` = ``@workset.boxes/@meta.box.name``, and through it
    ``meta.box.home``, ``box.canon``, …). A reader that needs one sees it ABSENT.
    The refs are read by the one parser, ``expand_expr``, with a recording lookup.
    """
    dropped: set[str] = set()
    for floor in floors:
        for key in list(floor):
            if is_seed(key):
                del floor[key]
                dropped.add(key)

    def refs(value: str) -> set[str]:
        names: set[str] = set()

        def record(name: str, _chain: tuple[str, ...]) -> str:
            names.add(name)
            return ""

        expand_expr(value, space="host", ctx=ctx, lookup=record, defer_env=True)
        return names

    changed = True
    while changed:
        changed = False
        for floor in floors:
            for key, value in list(floor.items()):
                if isinstance(value, str) and any(
                    name in dropped or is_seed(name) for name in refs(value)
                ):
                    del floor[key]
                    dropped.add(key)
                    changed = True


def _agent_identity(agent_name: str, project_path: Path | None) -> dict[str, object]:
    """The active agent's ``meta.agent.<a>.*`` identity + launch-grammar floor, off
    its plugin descriptor; EMPTY for a no-agent resolve.

    The credential-SHARING capability comes off the same descriptor: absent / no
    agent → non-capable.
    """
    if not agent_name:
        return {}
    from kanibako.log import get_logger
    from kanibako.targets import resolve_target

    agent_desc = None
    # The plugin's DECLARED harness name, in its own case ([R173]); *agent_name* is
    # the NODE, already folded, so it cannot supply this.
    agent_declared_name: str | None = None
    agent_auth_support = False
    try:
        agent_target = resolve_target(harness_of(agent_name), project_path)
        agent_declared_name = agent_target.name
        agent_desc = agent_target.descriptor
        agent_auth_support = bool(
            agent_desc.auth_share_support if agent_desc is not None else False
        )
    except (KeyError, ValueError):
        # GENUINELY ABSENT: no matching target (KeyError) or one lacking a
        # ``meta.agent.<agent>.name`` (ValueError) — non-capable. Nothing else is
        # swallowed: a transient error must not silently disable sharing.
        get_logger("start").debug(
            "auth capability: no descriptor for agent %r → non-capable",
            agent_name,
        )
        agent_desc = None
        agent_declared_name = None
        agent_auth_support = False
    # ⚑ THE DISCRIMINATOR AND THE VALUE ARE TWO SPELLINGS OF ONE AGENT ([R173]):
    # the key's segment is the NODE, the value keeps the DECLARED case.
    floor = meta_agent_identity_floor(
        agent_name, agent_declared_name, agent_auth_support,
    )
    # B5: the plugin-set LAUNCH GRAMMAR ``meta.agent.<a>.{mode,exec}`` (spec §2d),
    # from the SAME descriptor — the single descriptor→keyspace seam.
    floor.update(meta_agent_grammar_floor(agent_name, agent_desc))
    return floor


@overload
def resolve_inputs(
    *, subject: Literal[ResolveSubject.BOX], std, agent_name: str,
    system_path: Path | None, proj, ws: None = None,
) -> LaunchInputs: ...
@overload
def resolve_inputs(
    *, subject: Literal[ResolveSubject.WORKSET], std, agent_name: str,
    system_path: Path | None, proj: None = None, ws: Workset,
) -> LaunchInputs: ...
@overload
def resolve_inputs(
    *, subject: Literal[ResolveSubject.SYSTEM], std, agent_name: str,
    system_path: Path | None, proj: None = None, ws: None = None,
) -> LaunchInputs: ...
def resolve_inputs(
    *,
    subject: ResolveSubject,
    std,
    agent_name: str,
    system_path: Path | None,
    proj=None,
    ws: Workset | None = None,
) -> LaunchInputs:
    """Build the :class:`LaunchInputs` for one resolve of *subject* under *agent_name*.

    The subject is the resolve's TARGET: ``BOX`` takes the box's *proj*,
    ``WORKSET`` a working set *ws* and no box, ``SYSTEM`` neither; any other pairing
    raises ``ValueError``. *system_path* is the system SETTINGS file the resolve
    reads. A key the target has no value for is OMITTED, never fabricated
    (:func:`_omit_derived`): a box with no name yet loses its name-derived keys, a
    working set every ``meta.box.*`` anchor, the system scope every box and
    working-set anchor.

    See ``llm-docs/kanibako/settings/settings_launch.py.md``, "``resolve_inputs``",
    for what each floor carries and why it is built here.
    """
    from kanibako.settings.agent_select import host_resolve_ctx

    takes = {
        ResolveSubject.BOX: (True, False),
        ResolveSubject.WORKSET: (False, True),
        ResolveSubject.SYSTEM: (False, False),
    }[subject]
    if (proj is not None, ws is not None) != takes:
        raise ValueError(
            f"resolve_inputs: subject {subject.name} takes "
            + ("a box (proj)" if takes[0] else "a working set (ws)" if takes[1]
               else "neither a box nor a working set")
        )
    if subject is ResolveSubject.BOX:
        return _box_inputs(
            std=std, proj=proj, agent_name=agent_name, system_path=system_path,
        )

    # ONE ctx builder (P7), the box-less arm of the one the BOX subject uses.
    ctx = host_resolve_ctx(std, ws, agent_name)
    meta_identity = _agent_identity(agent_name, None)
    meta_runtime = meta_runtime_host_floor()
    workset_anchor: dict[str, object] = {}
    cascade_workset_path: Path | None = None
    if ws is None:
        auth_chain = auth_chain_floor(mode=None, agent_name=agent_name)
    else:
        from kanibako.channels import channels as _channels

        # A working set is PRIMARY or NAMED; ``workset create --standalone`` is
        # refused, so no working set is standalone.
        mode = BoxMode.primary if ws.is_default else BoxMode.named
        meta_runtime, workset_anchor, auth_chain = _workset_floors(
            std,
            mode=mode.value,
            ws_token=_channels.workset_token(mode, ws.name),
            ws_root=ws.root,
            local_channels=_channels.workset_channels_at(ws.root, early=ws.early_scope),
            agent_name=agent_name,
        )
        cascade_workset_path = workset_settings_path(ws)
    _omit_derived(
        ctx, lambda key: _box_less_omits(key, in_workset=ws is not None),
        meta_runtime, meta_identity, workset_anchor, auth_chain,
    )
    return LaunchInputs(
        subject=subject,
        ctx=ctx,
        system_path=system_path,
        system_floor=system_path_floor(std),
        meta_runtime=meta_runtime,
        meta_identity=meta_identity,
        workset_anchor=workset_anchor,
        auth_chain=auth_chain,
        cascade_box_path=None,
        cascade_workset_path=cascade_workset_path,
        prefs=tuple(collect_prefs(cascade_workset_path, None)),
    )


def _workset_floors(
    std,
    *,
    mode: str,
    ws_token: str,
    ws_root: Path,
    local_channels: "WorksetChannels | None",
    agent_name: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """The ``(meta_runtime, workset_anchor, auth_chain)`` floors of a resolve in the
    working set rooted at *ws_root* — the BOX and WORKSET subjects' ONE sequence.

    *mode* is the box mode a member box has; *ws_token* the partition token
    (``channels.workset_token``); *local_channels* the workset-local channel set,
    ``None`` for standalone (the caller's gate). ``meta.runtime.ws_root`` is the
    ``@config.primary_workset`` ref for primary and *ws_root* otherwise, and
    ``workset.workspaces`` is resolved off that same root.
    """
    from kanibako.channels import channels as _channels
    from kanibako.settings.workset_dirkeys import EarlyScope

    ws_root_literal = None if mode == "primary" else str(ws_root)
    meta_runtime = meta_runtime_floor(
        mode=mode, ws_name=ws_token, ws_root_literal=ws_root_literal,
    )
    # LAYOUT anchors (spec §2c/§2g): the per-mode variation lives in
    # ``workset_anchor_floor``; only the channel roots and ``workset.workspaces``
    # are resolved here.
    channelroot, ws_channels = _workset_channel_floor_values(
        _channels.partition_key_paths(std, ws_token, ws_root), local_channels,
    )
    workset_anchor = workset_anchor_floor(
        mode=mode,
        channelroot=channelroot,
        workspaces=_workset_workspaces_floor_value(
            mode, ws_root_literal, early=EarlyScope(std.early_system, ws_token),
        ),
        workset_channels=ws_channels,
    )
    return meta_runtime, workset_anchor, auth_chain_floor(mode=mode, agent_name=agent_name)


def _box_workset_floors(
    std, proj, agent_name: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """:func:`_workset_floors` for the box *proj*: the working set it is in, read off it.

    ``channels.workset_root`` is the root the anchors hang off (``proj.metadata_path``
    for standalone, NOT ``project_path``, the ``<root>/workspace`` subdir — spec §2c,
    §4 example). The kinemata ``workset-channels-*`` / ``workset-partition`` views
    call this, so they compare the launch's own per-mode root choice.
    """
    from kanibako.channels import channels as _channels

    mode = proj.mode.value
    if mode == "named" and proj.group is None:
        raise ProjectError(
            "named-mode project has no workset group (meta.runtime.ws_root)"
        )
    return _workset_floors(
        std,
        mode=mode,
        ws_token=_channels.workset_name_token(proj),
        ws_root=_channels.workset_root(proj, std),
        local_channels=_channels.workset_channel_paths(proj, std),
        agent_name=agent_name,
    )


def _box_inputs(*, std, proj, agent_name: str, system_path: Path | None) -> LaunchInputs:
    """:func:`resolve_inputs` for the ``BOX`` subject: the box *proj*."""
    from kanibako.channels import channels as _channels
    from kanibako.settings.agent_select import launch_resolve_ctx

    # ONE ctx builder (P7): the SELECTION pre-pass resolves against the identical
    # host-side namespace, so the two passes cannot disagree about what
    # ``@config.*`` / ``$XDG_*`` / ``~`` mean.
    ctx = launch_resolve_ctx(std, proj, agent_name)
    # The Layer-2 ``system.*`` path tier the category @-refs resolve against — ONE
    # map (``paths.system_path_floor``), shared with the workset preview, so "both
    # carry the same keys" is structural rather than a promise two hand lists made.
    system_floor = system_path_floor(std)

    # ``meta.runtime.*`` identity anchors (spec §1A). PRIMARY → the
    # ``@config.primary_workset`` @-ref; NAMED → the detected workset root literal;
    # STANDALONE → the project ROOT (``proj.metadata_path``, NOT ``project_path``,
    # which is the ``<root>/workspace`` subdir — spec §2c and the §4 worked example).
    mode = proj.mode.value
    meta_runtime, workset_anchor, auth_chain = _box_workset_floors(
        std, proj, agent_name,
    )

    # ``meta.*`` IDENTITY anchors (spec §2c/§2d): the resolved literals the launch
    # already computes, so an @meta.box.workspace / @meta.box.inbox bind expands
    # byte-identically. The agent's credential-SHARING capability and its declared
    # name come off the ACTIVE agent's descriptor; absent / no agent → non-capable.
    # ⚑ A box whose name is not decided yet (the ``run_start`` pre-flight, before
    # create) has no channel addresses; the blanks below are dropped with every
    # name-derived key before anything reads them (:func:`_omit_derived`).
    address: BoxAddressArgs = (
        box_address_args(_channels.box_channel_addresses(proj, std)) if proj.name
        else BoxAddressArgs(inbox="", share_global="", share_workset=None)
    )
    # The SINGLE-SOURCE (box tier, workset tier) settings-file pair (M-8): it feeds
    # BOTH the ``meta.box.settings`` anchor and the cascade the resolvers read, so
    # the anchor and the cascade cannot drift.
    cascade_box_path, cascade_workset_path = box_workset_settings_paths(proj)
    meta_identity = meta_identity_floor(
        box_name=proj.name or "",
        project_path=str(proj.project_path) if proj.project_path is not None else None,
        **address,
        box_settings=str(cascade_box_path),
    )
    # The agent half (identity + launch grammar); omitted for a NO-AGENT box.
    meta_identity.update(_agent_identity(agent_name, proj.project_path))

    if not proj.name:
        # Primary / named ``meta.box.settings`` is ``@meta.box.path/box.yaml`` (§2c): name-derived,
        # but passed here as a LITERAL (``<boxes>/__unregistered__/box.yaml`` before create), so the
        # ref walk cannot see it. Standalone's hangs off ``@workset.boxes`` and stays.
        if mode != "standalone":
            meta_identity.pop("meta.box.settings", None)
        _omit_derived(
            ctx, _BOX_NAME_KEYS.__contains__,
            meta_runtime, meta_identity, workset_anchor, auth_chain,
        )
    return LaunchInputs(
        subject=ResolveSubject.BOX,
        ctx=ctx,
        system_path=system_path,
        system_floor=system_floor,
        meta_runtime=meta_runtime,
        meta_identity=meta_identity,
        workset_anchor=workset_anchor,
        # The auth 3-tier SHARING chain (spec §2a–§2c), per mode. Every resolve
        # folds it, so every caller must pass the §1A selection it has.
        auth_chain=auth_chain,
        cascade_box_path=cascade_box_path,
        cascade_workset_path=cascade_workset_path,
        # ``pref.*`` REQUESTS (spec §2h), collected ONCE per inputs, so the resolves
        # sharing them cannot disagree about what was requested.
        prefs=tuple(collect_prefs(cascade_workset_path, cascade_box_path)),
    )


def fold_floor(
    *,
    subject: ResolveSubject,
    agent_name: str,
    behavior_floor: Mapping[str, object] | None = None,
    agent_behavior_floor: Mapping[str, object] | None = None,
    default_categories: Mapping[str, object] | None = None,
    auth_chain: Mapping[str, object] | None = None,
    meta_runtime: Mapping[str, object] | None = None,
    meta_identity: Mapping[str, object] | None = None,
    workset_anchor: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """:func:`build_launch_snapshot`'s first phase, shared with set time: the one floor fold."""
    # ⚑ SEEDED, not empty: the shipped ``system.*`` scalar defaults are installed by
    # EVERY resolve (see :data:`SYSTEM_SCALAR_FLOOR`). Everything below may overwrite
    # them by name, and every settings scope outranks them by merge level.
    floor: dict[str, object] = dict(SYSTEM_SCALAR_FLOOR)
    # The box scalars' declared defaults (spec §2b; ``box.shell``'s ``<None>`` as a
    # present ``None``, [R177]), so an ``@box.shell`` / ``@box.image``
    # a user writes resolves (spec §0) instead of rendering ``""`` or dropping. ONE carrier,
    # :func:`~kanibako.settings.config.box_scalar_defaults_floor`. Every subject: they are
    # DECLARED keys a system or workset file may set downward, and a declared key resolves.
    floor.update(box_scalar_defaults_floor())
    # OS1: bare behavior keys → their scope-qualified §2d spelling. There is NO bare
    # ``agent.<key>`` (spec §0).
    #
    # ⚑⚑ THE TIER IS CHOSEN BY WHO SUPPLIED THE VALUE ([Q91]: ``agent.default``
    # builtin < ``agent.default`` setting < ``agent.<a>`` builtin < ``agent.<a>``
    # setting). Core's floor is the all-agents backstop at ``agent.default.<key>``; a
    # plugin's declared row is ``agent.<a>`` builtin, so it lands at
    # ``agent.<active>.<key>``, where the §2d pick in :func:`effective_behavior` reads
    # it before any ``agent.default`` value, a user's included. A plugin row that
    # inherits (``UNSET``, Q105) is not in *agent_behavior_floor* at all, so the pick
    # falls through to ``agent.default``.
    # A plugin-only leaf (goose's ``provider``) lands at the only tier declaring it.
    # A non-core leaf in *behavior_floor* would write an undeclared
    # ``agent.default.<leaf>``, which the §0 audit below refuses by name.
    #
    # ⚑ WITHIN A SLOT, EVERY SCOPE WINS: both tiers ride this ONE floor into ``base_levels[5]``,
    # the LOWEST rung, so every settings scope — box, workset, agent file, system —
    # still outranks a floored value by merge level alone. That is also why the
    # per-agent descriptor rung ``agent_partial`` is NOT the carrier for this: it sits
    # ABOVE ``system``, and routing behavior floors through it would promote a plugin
    # default over a user's system-tier setting.
    for tier, tier_floor in (
        ("default", behavior_floor), (agent_name, agent_behavior_floor),
    ):
        for key, val in (tier_floor or {}).items():
            floor[f"agent.{tier}.{key}"] = val
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
            # category — a behavior change nobody ruled.
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
    return floor


@dataclass(frozen=True)
class Cascade:
    """:func:`assemble_cascade`'s merged, unexpanded snapshot and labeled ``written`` levels."""

    snapshot: KeyStore
    written: tuple[_WrittenLevel, ...]
    #: Each ``written`` level's scope name, parallel to it.
    tiers: tuple[str, ...]


def assemble_cascade(
    *,
    agent_name: str,
    floor: dict[str, object],
    files: Sequence[SettingsFile],
    agent_partial: KeyStore | None = None,
    agent_state: AgentFileLevel | None = None,
    persona_values: Mapping[str, str] | None = None,
    prefs: "Sequence[PrefRequest] | None" = None,
    valid_agents: "Collection[str] | None" = None,
    cli_level: Mapping[str, object] | None = None,
) -> Cascade:
    """:func:`build_launch_snapshot`'s second phase, shared with set time: assemble and merge.

    *files* are the cascade files as read (``settings_assemble.cascade_files``) for the
    caller's purpose.
    """
    paths = {f.level: f.path for f in files}
    system_path, agent_path = paths.get("system"), paths.get("agent")
    workset_path, box_path, base_path = paths.get("workset"), paths.get("box"), paths.get("base")
    base_levels = assemble_levels(agent_name=agent_name, files=files, floor=floor)
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
    # The INTERNAL binds (spec §2c: not user keys, not repointable) sit OUTSIDE every
    # user-resettable arm: re-imposed from the floor AFTER the merge, so a user's null or
    # entry in the same arm can neither drop nor repoint kanibako's own delivery.
    internal = _internal_floor_binds(floor)
    if internal:
        snapshot = merge([dotted_partial(internal), snapshot])
    # The agent file's flat state names the file it was read from — the path travels
    # WITH the level — and ``agent_path`` answers only when the level carries none.
    state_path = (agent_state.path if agent_state is not None else None) or agent_path
    labeled = [
        (level, path, tier)
        for level, path, tier in (
            (base_levels[0], box_path, "box"),
            (box_prefs, box_path, "box"),
            (base_levels[1], workset_path, "workset"),
            (ws_prefs, workset_path, "workset"),
            (state_partial, state_path, "agent"),
            (base_levels[2], agent_path, "agent"),
            (base_levels[3], agent_path, "agent.default"),
            (base_levels[4], system_path, "system"),
        )
        if level is not None
    ]
    written: list[_WrittenLevel] = [(level, path, None) for level, path, _ in labeled]
    written.append((base_levels[5], base_path, dotted_partial(floor)))
    tiers = (*(tier for _, _, tier in labeled), "base")
    return Cascade(snapshot=snapshot, written=tuple(written), tiers=tiers)


def build_launch_snapshot(
    *,
    agent_name: str,
    ctx: ResolveCtx,
    system_path: Path | None,
    agent_path: Path | None,
    workset_path: Path | None,
    box_path: Path | None,
    behavior_floor: Mapping[str, object] | None = None,
    agent_behavior_floor: Mapping[str, object] | None = None,
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
    subject: ResolveSubject = ResolveSubject.BOX,
    refs_read: RefsRead | None = None,
    dest_keys: DestKeys | None = None,
    written_out: "list[_WrittenLevel] | None" = None,
) -> KeyStore:
    """Build the ONE expanded launch snapshot.

    *subject* names the resolve's target in the §0 refusal's words;
    :meth:`LaunchInputs.as_kwargs` supplies it, and a caller without inputs
    resolves a box.

    Folds the two behavior floors (core at ``agent.default.<key>``, the active
    plugin's at ``agent.<active>.<key>`` — OS1) and every
    runtime ``default_categories`` table into ONE base-level floor, assembles the
    6-level cascade (S8) with 7a's *agent_partial* as an additional agent-level
    source (S27), merges (S15), and expands (S17/S19) with *ctx*. There is NO bare
    ``agent.<key>`` in the snapshot (spec §2d / §0) — the agent tier is DISCRIMINATED
    throughout. Returns the expanded snapshot.

    *behavior_floor* is the CORE bare behavior-default dict
    (``core_defaults.behavior_defaults``); *agent_behavior_floor* the ACTIVE plugin's
    (its descriptors' ``{key: default}``).  Kept apart because the tier is decided by
    SOURCE ([Q91]); *default_categories* the
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
    ``expand`` resolves its @-ref chain ONCE (single-route). ``None`` for any resolve
    that does not need that fragment.
    ⚑ WHO FOLDS ``auth_chain``: the two auth resolves and every
    ``commands.start._resolve_launch_snapshot`` that delivers a user row, so the §2c
    keys answer in every launch resolve that delivers one. Only the image / helper resolves,
    which emit nothing but their injected table, omit it.

    *prefs* are the ``pref.*`` REQUESTS (spec §2h) of the workset + box files, in
    application order. ⚑ ``None`` means COLLECT THEM HERE — the fail-safe default, so
    a caller cannot omit them by accident. Supplying them is a CACHE, not a second
    source. *valid_agents* injects the agent-validity set (defaults to plugin
    discovery); tests supply their own.

    *dest_keys*, when given, receives ``expand``'s record of the key each bind entry was
    filed under (:data:`~kanibako.settings.settings_expand.DestKeys`).

    *written_out*, when given, receives the cascade's labeled levels, most-specific-first,
    so a caller can name the file whose value won (:func:`_none_setter`).

    *cli_level* is the §1A **top-most input level** — above every settings file AND
    every pref. :func:`~kanibako.settings.settings_cli_level.guard_cli_level` is
    applied HERE, before the splice, so no call site can bypass it (P8). It always
    carries the RESOLVED agent selection, which is what keeps ``@system.agent`` equal
    to the node that actually runs.

    ⚑ WHO MUST PASS IT — "the narrow resolves can skip it" is NOT the rule, and
    reading it that way cost the credential path once already. It is REQUIRED by every
    resolve carrying the ``auth_chain`` floor, because
    ``meta.box.auth.workset_path`` = ``@workset.auth.path/@system.agent``: omit it and
    the per-agent credential dir names the agent the STORED settings select, not the
    one running — or, with none stored, collapses to the workset auth ROOT.

    ⚑ WHICH RESOLVES SEE THE EPHEMERAL FLAGS (P8, §1A): the SELECTION rides every
    resolve that needs it; the FLAGS ride only the resolve that decides THIS launch's
    runtime. No resolve whose output is WRITTEN TO DISK may see a flag. Both lists,
    caller by caller: the llm-doc.
    """
    floor = fold_floor(
        subject=subject,
        agent_name=agent_name,
        behavior_floor=behavior_floor,
        agent_behavior_floor=agent_behavior_floor,
        default_categories=default_categories,
        auth_chain=auth_chain,
        meta_runtime=meta_runtime,
        meta_identity=meta_identity,
        workset_anchor=workset_anchor,
    )

    # The settings files this resolve reads, MOST-SPECIFIC-FIRST, read ONCE for the launch
    # (``ReadPurpose.RESOLVE``): a ``config:`` table (spec §1) and a retired behavior spelling
    # refuse here, before the resolve. The cure names the agent, except under ``GENERAL_SLOT``:
    # that name is also the agent-less system, workset, and box-scalar resolves
    # (``resolve_box_scalars``, ``workset_cmd``), so it cannot tell a selected ``shell`` from
    # no agent, and ``<agent>`` is right for both; at the box tier the subject is the box.
    box_name = (meta_identity or {}).get("meta.box.name")
    files = cascade_files(
        purpose=ReadPurpose.RESOLVE,
        system_path=system_path,
        agent_path=agent_path,
        workset_path=workset_path,
        box_path=box_path,
        base_path=settings_base_path(),
        subject=agent_name if agent_name and agent_name != GENERAL_SLOT else None,
        box_name=box_name if isinstance(box_name, str) else None,
    )

    cascade = assemble_cascade(
        agent_name=agent_name,
        floor=floor,
        files=files,
        agent_partial=agent_partial,
        agent_state=agent_state,
        persona_values=persona_values,
        prefs=prefs,
        valid_agents=valid_agents,
        cli_level=cli_level,
    )
    snapshot, written = cascade.snapshot, cascade.written
    if written_out is not None:
        written_out.extend(written)
    null_sources: NullSources = {}
    expanded = expand(
        snapshot, ctx, null_sources=null_sources, refs_read=refs_read, dest_keys=dest_keys,
    )
    # The meta.box.agent.* RO mirror (B5) — a COPY step, AFTER expand so the values
    # are resolved terminals.
    _materialize_box_agent_mirror(expanded, active_agent=agent_name)
    # The three computed sharing-state keys (Q61) — a COMPUTE step, AFTER expand
    # for the same reason: expand does not evaluate &&, so the ANDs only exist in
    # Python. Before the §0 refusal below, which must see the finished tree.
    _materialize_auth_active(expanded)
    if workset_anchor and _BOX_ROOT_KEY in workset_anchor:
        _assert_box_root_resolved(expanded)
    # ⚑ MEASUREMENT FIRST, THEN ENFORCEMENT, AND THE ORDER IS LOAD-BEARING. The probe
    # is DISARMED unless ``KANI_KEYSPACE_PROBE`` names it and cannot fail a run; it
    # sized the blast radius of the refusal below, which behind ``load_merged_config``
    # is nearly every kanibako command. Raising BEFORE it would blind the instrument to
    # exactly the resolves that matter, so a future re-measurement would see only the
    # snapshots that already conform.
    observe_keyspace(expanded, origin="build_launch_snapshot")
    # [R147] at read time, for EVERY path key a settings file stores. It JUDGES the
    # levels a user writes — each beside its file — and RESOLVES its anchors off the
    # expanded snapshot. The persona, descriptor, CLI and floor rungs are kanibako's
    # own and are deliberately absent (see the function). AFTER the probe, for the
    # reason the probe states.
    # Then spec §0's RESOLVE clause, enforced. ⚑ A SIBLING of the probe, never a mode
    # of it: the probe is REPORT-ONLY by its own module contract, and the two share
    # the ORACLE so the refusal arms exactly what was measured.
    refuse_read_time_faults(
        written, expanded, ctx=ctx, files=files, subject=subject, tiers=cascade.tiers,
    )
    refuse_undeclared_per_file(files)
    _warn_lone_none_standard_binds(
        floor, snapshot, expanded, null_sources=null_sources, written=written, ctx=ctx,
    )
    _warn_null_ref_secrets(
        snapshot, expanded, active_agent=agent_name, written=written, ctx=ctx,
    )
    return expanded


def _internal_floor_binds(floor: Mapping[str, object]) -> dict[str, object]:
    """The *floor*'s INTERNAL bind entries (:func:`core_defaults.internal_bind_keys`), by arm."""
    keys = core_defaults.internal_bind_keys()
    out: dict[str, object] = {}
    for arm, entries in floor.items():
        if not (_is_bind_floor_key(arm) and isinstance(entries, dict)):
            continue
        picked = {d: v for d, v in entries.items() if (arm, d) in keys}
        if picked:
            out[arm] = picked
    return out


#: The ``<None>`` warnings already given in this process ([R185] and the secret one), by
#: their text: one command runs several resolves over the same files, and one ``<None>``
#: is ONE fact. Module-level on the footing of ``settings_assemble._DROP_WARNED``: it
#: changes no resolution.
_NONE_WARNED: "set[str]" = set()


def reset_none_warnings() -> None:
    """Clear the per-process ``<None>`` warning memo (test seam)."""
    _NONE_WARNED.clear()


def _warn_once(message: str) -> None:
    """Log *message* as a warning unless this process already has."""
    if message not in _NONE_WARNED:
        _NONE_WARNED.add(message)
        _log.warning("%s", message)


def _none_setter(written: Sequence[_WrittenLevel], key: str, dest: str | None) -> str | None:
    """The settings file whose value wins at *key* (``[dest]``), or ``None`` for the floor.

    *written* is most-specific-first, so the first level holding the key is the winner.
    With *dest*, *key* is a bind ARM and a ``None`` arm counts as setting the entry
    ([Q94] 1).  A ``base`` leaf still equal to the folded floor's is the floor's.
    """
    for level, path, floor_store in written:
        value = snapshot_leaf(level, key)
        if dest is not None and isinstance(value, KeyStore):
            value = dict.get(value, dest, __MISSING__)
        if value is __MISSING__:
            continue
        if floor_store is not None and snapshot_leaf(floor_store, key) == value:
            return None
        return str(path) if path is not None else "<settings>"
    return None


def _source_refs(src: str, expanded: KeyStore, ctx: ResolveCtx) -> list[str]:
    """Every ``@``-ref in the source expression *src*, via the ONE scanner."""
    refs: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        del chain  # only the names are wanted; *expanded* holds terminals.
        refs.append(ref)
        value = snapshot_leaf(expanded, ref)
        return value if isinstance(value, str) else ""

    try:
        expand_expr(src, space="host", ctx=ctx, lookup=lookup, defer_env=True)
    except SettingsError:
        pass  # a malformed source is refused where it is expanded, not here.
    return refs


#: A floor-supplied ``<None>`` source key, and the FILE keys whose ``<None>`` is what
#: produces it — in the order the derivation reads them (spec §Channels table:
#: ``workset.channels.mailboxes`` is ``{system.channels.mailboxes}/{meta.workset.name}``,
#: and ``meta.box.inbox`` is ``{workset.channels.mailboxes}/{meta.box.name}``).
#: ⚑ THE FLOOR KEY IS NOT WHAT A USER SET, so naming it is naming something they cannot
#: act on; these are the keys ``config set`` and ``workset set`` write.  ``meta.*`` is
#: read-only (§0), so there is no spelling of "null the inbox" to offer as a cure.
_META_NULL_ORIGIN: dict[str, tuple[str, ...]] = {
    "meta.box.inbox": ("workset.channels.mailboxes", "system.channels.mailboxes"),
}


def _null_origin(written: Sequence[_WrittenLevel], ref: str) -> "tuple[str, str] | None":
    """The ``(file key, file)`` whose ``<None>`` FLOORED *ref* at ``<None>``, or ``None``.

    ⚑ Walks :data:`_META_NULL_ORIGIN` in DERIVATION order and returns the FIRST key a
    settings file actually wrote as ``<None>``: the workset-local repoint shadows the
    system default, so the first writer is the one the user set.  Returns ``None`` when
    no file nulled any of them, which is the DEFAULT (a root that resolved) — nothing to
    report, since §2a judges what was SET.
    """
    for key in _META_NULL_ORIGIN.get(ref, ()):
        where = _none_setter(written, key, None)
        if where is not None:
            return key, where
    return None


def _warn_lone_none_standard_binds(
    floor: Mapping[str, object],
    merged: KeyStore,
    expanded: KeyStore,
    *,
    null_sources: NullSources,
    written: Sequence[_WrittenLevel],
    ctx: ResolveCtx,
) -> None:
    """Warn when a settings file makes only ONE of a STANDARD bind's entry & source ``<None>``.

    Spec §2a / companion "Delivery at launch" ([R185]): a standard bind is omitted by
    setting BOTH to ``<None>``; if a file sets only one, the launch warns, naming both
    keys and that file.  Both, or neither, is silent.
    ⚑ STANDARD = a floor entry whose source carries an ``@``-ref (its source key): the
    core-defaults tables, plugin binds ([Q95] 2) and the helper log alike.  A
    literal-source entry is INTERNAL; a user-added entry is not in the floor ([Q94] 2).
    ⚑ ``seeded`` is out: §2a skips a ``<None>`` layer.  ⚑ A ``<None>`` the floor itself
    supplies is not SET by anyone, so only a settings file's value warns — but the key
    such a floor null must still be TRACED to the file key behind it (:func:`_null_origin`),
    or the warning names a read-only ``meta.*`` address nobody can set.  The workset-LOCAL
    ``workset.channels.*`` leaves under a null channel root are the ONE case the key a user
    acts on is a DIFFERENT one (``workset.channelroot``), and ONE message answers for every
    bind it took (:func:`_warn_rootless_channel_binds`).
    """
    # (label, source key) for each bind a FLOOR-SUPPLIED ``<None>`` source omits.
    rootless: list[tuple[str, str]] = []
    for arm, entries in floor.items():
        if not (_is_bind_floor_key(arm) and isinstance(entries, dict)):
            continue
        if arm.endswith(".seeded"):
            continue
        for dest, value in entries.items():
            src = str(value[0]) if isinstance(value, (tuple, list)) and value else ""
            refs = _source_refs(src, expanded, ctx)
            if not refs:
                continue  # INTERNAL: no source key.
            label = entry_label(shown_key(arm), dest)
            merged_arm = snapshot_leaf(merged, arm)
            if isinstance(merged_arm, KeyStore) and dict.__contains__(merged_arm, dest):
                expanded_arm = snapshot_leaf(expanded, arm)
                if not (isinstance(expanded_arm, KeyStore)
                        and dict.get(expanded_arm, dest, __MISSING__) is None):
                    continue  # the entry and its source both stand.
                set_refs = [
                    (ref, where)
                    for ref in null_sources.get((*arm.split("."), dest), ())
                    if (where := _none_setter(written, ref, None)) is not None
                ]
                if not set_refs:
                    # ⚑ A source the FLOOR nulled.  Two shapes reach here and they want
                    # DIFFERENT keys named.  (1) A floor ``meta.*`` address — ``meta.box.inbox``
                    # is null because the mailboxes key a user CAN set is; the file key is
                    # the one they wrote, and :func:`_null_origin` finds it.  (2) The
                    # workset-LOCAL ``workset.channels.*`` leaves under a null channel root;
                    # there the key a user acts on IS ``workset.channelroot``, and ONE
                    # message answers for every bind it took — :func:`_warn_rootless_channel_binds`.
                    # A merely MISSING source says nothing: no file nulled it, and §2a
                    # judges what was SET.
                    origins = {
                        origin for ref in refs
                        if (origin := _null_origin(written, ref)) is not None
                    }
                    if origins:
                        named = ", ".join(
                            f"{shown_key(key)} (in {where})"
                            for key, where in sorted(origins)
                        )
                        _warn_once(
                            f"The standard bind {label} is omitted: its source "
                            f"references {named}, which is null, but the entry itself "
                            f"is not. Set {label} to null as well to omit it without "
                            f"this warning."
                        )
                        continue
                    if any(snapshot_leaf(expanded, ref) is None for ref in refs):
                        rootless.append((label, ", ".join(dict.fromkeys(refs))))
                    continue
                named = ", ".join(
                    f"{shown_key(ref)} (in {where})" for ref, where in set_refs
                )
                message = (
                    f"The standard bind {label} is omitted: its source references "
                    f"{named}, which is null, but the entry itself is not. Set {label} "
                    f"to null as well to omit it without this warning."
                )
            else:
                where = _none_setter(written, arm, dest)
                if where is None:
                    continue  # not set by a settings file.
                if any(snapshot_leaf(expanded, ref) is None for ref in refs):
                    continue  # its source is <None> too: both, silent.
                # Read-only ``meta.*`` (§0), and any key the launch refuses a null at, are
                # named but never offered as the cure — the latter by the doors' own
                # membership, :func:`config.refuses_null_path_key`.
                settable = [
                    r for r in dict.fromkeys(refs)
                    if not r.startswith("meta.") and not refuses_null_path_key(r)
                ]
                keys = ", ".join(map(shown_key, settable or dict.fromkeys(refs)))
                message = (
                    f"The standard bind {label} is set to null in {where}, so it is "
                    f"omitted, but its source key ({keys}) is not null."
                )
                if settable:
                    message += (
                        f" Set {keys} to null as well to omit it without this warning."
                    )
            _warn_once(message)
    _warn_rootless_channel_binds(rootless, expanded, written=written)


def _warn_rootless_channel_binds(
    rootless: Sequence[tuple[str, str]],
    expanded: KeyStore,
    *,
    written: Sequence[_WrittenLevel],
) -> None:
    """Warn ONCE that a null ``workset.channelroot`` omitted the channel binds.

    *rootless* pairs each omitted bind's label with its ``<None>`` source key.  One
    null root is ONE fact that took three binds with it, so the message names the key
    and the file that set it — once — and lists the binds it omitted.  It fires only
    for a root a settings file wrote: a root the floor left absent is the DEFAULT and
    every leaf resolved, which is why there is nothing to report.
    ⚑ An EMPTY *rootless* says nothing: every one of the root's binds was either
    omitted with its entry or resolved, so none of them is lone-null.
    """
    if not rootless:
        return
    where = _none_setter(written, "workset.channelroot", None)
    if where is None or snapshot_leaf(expanded, "workset.channelroot") is not None:
        return
    labels = ", ".join(dict.fromkeys(label for label, _ in rootless))
    _warn_once(
        f"The standard binds {labels} are omitted: their source key "
        f"workset.channelroot is null in {where}, but each entry itself is not. Set "
        f"them to null as well to omit them without this warning."
    )


#: Where a ``<None>`` the floor supplies was set, as a message names it.
_FLOOR_WHERE = "kanibako's defaults for this box"


def _warn_null_ref_secrets(
    merged: KeyStore,
    expanded: KeyStore,
    *,
    active_agent: str,
    written: Sequence[_WrittenLevel],
    ctx: ResolveCtx,
) -> None:
    """Warn when a ``secret_path`` value is ``<None>`` because a key it references is.

    Spec §0 makes the whole value ``<None>`` ([R186]), so the entry mounts nothing and
    the box starts without that secret ([Q94] 3, fail-soft).  The warning names the
    ``secret_path`` key, each ``<None>`` key it references, and where that ``<None>``
    was set, the floor included: a standalone box's ``workset.auth.path`` is the case.
    ⚑ A ``secret_path`` set to ``null`` itself is a reset, and silent.
    """
    for scope in _SCOPES:
        if scope == "agent":
            node: object = _agent_pick_node(expanded, active_agent)
            decl_scope_fn = _agent_decl_scope_fn(
                dict.get(expanded, "agent", __MISSING__), active_agent,
            )
        else:
            node = dict.get(expanded, scope, __MISSING__)
            decl_scope_fn = _fixed_decl_scope_fn(scope)
        secret = dict.get(node, "secret_path", __MISSING__) if isinstance(
            node, KeyStore) else __MISSING__
        if not isinstance(secret, KeyStore):
            continue
        for var in dict.keys(secret):
            if dict.__getitem__(secret, var) is not None:
                continue
            key = f"{decl_scope_fn('secret_path', var)}.secret_path.{var}"
            raw = snapshot_leaf(merged, key)
            if not isinstance(raw, str):
                continue  # set to null itself: a reset.
            null_refs = [
                ref for ref in dict.fromkeys(_source_refs(raw, expanded, ctx))
                if snapshot_leaf(expanded, ref) is None
            ]
            if not null_refs:
                continue
            named = ", ".join(
                f"{shown_key(ref)} "
                f"(null in {_none_setter(written, ref, None) or _FLOOR_WHERE})"
                for ref in null_refs
            )
            _warn_once(
                f"{shown_key(key)} is {raw!r}, which references {named}, so it is "
                f"null and "
                f"the secret {var} is not mounted from it."
            )


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
    :mod:`kanibako.settings.agent_select`): a ``str`` name · present-``None``, an
    explicit ``pref.system.agent: null`` ⇒ NO DEFAULT IS SET, so the caller REFUSES
    unless an agent was named explicitly (spec §2b) · ``__MISSING__``, nothing ever
    set it ⇒ setup has never chosen one, and the caller REFUSES directing the user
    to ``kanibako setup``. 🛑 **Both refusals, never an implicit pick — the
    installed-agent count decides nothing** (retired 2026-09-19).

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
        files=cascade_files(
            purpose=ReadPurpose.NARROW, system_path=system_path, agent_path=None,
            workset_path=workset_path, box_path=box_path,
        ),
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

    ⚑ NOTHING SURFACES IT. The foundation bind's src IS ``meta.box.home`` =
    ``@meta.box.path/home``, an EMBEDDED ``@``-ref, and the embedded rule (§6b) coerces
    an absent referent to ``""``. The L7 guarantee-create ``mkdir``\\s that and mounts
    it OVER the box home: the wrong host directory, silently.

    ⚑ THREE SHAPES, so BOTH keys are read: absent / ``""``, primary-named's
    perfect ``/mybox``, and a root ending in ``/`` — the LEAF vanished, so every box
    shares the BOXES DIRECTORY's home (llm-doc has each).

    ⚑ THE TEST IS EXISTENCE + LEAF, NOT ABSOLUTENESS — please do not "tighten" it to
    require a leading ``/``: that reddens 131 tests in ``test_commands/test_start.py``,
    which mock ``load_std_paths()`` wholesale. ⚑ Test ``config.usable_box_store_value``,
    reasons ``settings.messages``.

    Called ONLY on an anchored floor (the call site's guard).
    """
    for key in (_BOX_STORE_KEY, _BOX_ROOT_KEY):
        value = snapshot_leaf(snapshot, key)
        if usable_box_store_value(value):
            continue
        got = "absent" if value is __MISSING__ else repr(value)
        why = (
            ERR_BOX_STORE_TRAILING_REASON
            if isinstance(value, str) and value.endswith("/")
            else ERR_BOX_STORE_EMPTY_REASON
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
# ``"shell"``, so the mirror holds the shell tier's own leaves and no ``agent.default``
# value (§2d: only true agents inherit from ``agent.default``; :func:`_fallback_node`).
# PINNED in tests/test_settings/test_settings_launch.py; the llm-doc has the shape.


def _materialize_box_agent_mirror(snapshot: KeyStore, *, active_agent: str) -> None:
    """Materialize ``meta.box.agent.*`` = the resolved active-agent subtree (B5).

    Mutates *snapshot* in place — it is the launch-local expanded tree, owned by the
    caller. A BLANK *active_agent* → NO subtree to mirror → nothing materialized.
    Only its KEYS are copied (:func:`_drop_non_mirror_keys`).

    ⚑ The auth floor separately materializes ``meta.box.agent.auth.share_support``
    (a PRE-expand floor key), so this copy must not clobber it: an existing name under
    ``meta.box.agent`` is LEFT INTACT. Reads/writes via the UNBOUND ``dict`` protocol
    (S3) so a key named ``get`` / ``agent`` cannot shadow.
    """
    if not active_agent or not active_agent.strip():
        # ⚑ Leave meta.box.agent.* absent; do NOT fall back to agent.default — that is
        # the all-agents backstop, not an ACTIVE agent the box runs.
        return
    # The PURE pick (agent.default ⊕ agent.<active>; the active tier alone for a
    # pseudo-agent), which already carries any ``pref.agent.<agent>.*`` the box
    # requested (a pref is a cascade INPUT).
    effective = _agent_pick_node(snapshot, active_agent)
    _drop_non_mirror_keys(effective)
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


#: Where the mirror hangs, as SEGMENTS: the path the §0 oracle judges a copied entry at.
_MIRROR_SEGMENTS: tuple[str, ...] = ("meta", "box", "agent")


def _drop_non_mirror_keys(effective: KeyStore) -> None:
    """Remove from *effective*, in place, every entry the §0 oracle refuses AT the mirror.

    Spec §2b declares ``meta.box.agent.<key>`` for a KEY of the active agent's
    subtree, and §0 allows no free-form passthrough, so an undeclared entry a user
    wrote under ``agent.<active>`` or ``agent.default`` has no mirror row. Copying it
    anyway made the §0 refusal name it twice — once where the user wrote it, once as a
    ``meta.box.agent.*`` entry no file carries, under a hand-edit cure.
    ⚑ The refusal is unchanged: it still judges the whole snapshot, mirror included,
    and still names the user's entry at the tier that carries it. The oracle is the
    refusal's own (:func:`keyspace_verdict`), so the two cannot disagree.
    *effective* must be the fresh pick (:func:`_agent_pick_node`), never the snapshot.
    ⚑ Judged IN PLACE (``prefix=``), never by writing it under ``meta.box.agent`` first:
    that write is the fabrication this removes, and the test-suite census counts it.
    """
    findings = undeclared_store_paths(
        effective, oracle=keyspace_verdict, prefix=_MIRROR_SEGMENTS,
    )
    # Sorted by path: a refused node goes before its children, which then are gone.
    for segments, _judgment in findings:
        node: object = effective
        for seg in segments[len(_MIRROR_SEGMENTS):-1]:
            node = dict.get(node, seg, None) if isinstance(node, KeyStore) else None
        if isinstance(node, KeyStore):
            dict.pop(node, segments[-1], None)


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
    used to ride through verbatim are refused as the file is read (``agent_record``),
    and every record reaching this function comes from there or from a plugin's
    generated config, whose state is empty — so nothing undeclared can reach it to be
    gated.  A second check here would be a rule spelled twice, and the one downstream
    would be the one that rots.

    ⚑⚑ THE DISCRIMINATOR ARRIVES WITH THE DATA (C-2; [spec:15-21, "self"]).  The node
    the table came FROM and the node it merged UNDER travel as one
    :class:`AgentFileLevel`, so the pair cannot be crossed.
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
    bind-shaped, so every leaf is a plain scalar.

    ⚑ EVERY VALUE IS DATA, never an expression: the store's values come from a harness
    config, so a ``@``, ``$``, ``~`` or ``\\`` in one is literal.  Each enters as
    :func:`~kanibako.settings.settings_resolve.literal_expr` — except the endpoint, which
    :func:`~kanibako.settings.settings_resolve.is_verbatim_text` makes text for EVERY
    source, so the expander keeps it as written and an escape here would be delivered.

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
        if not is_verbatim_text(("agent", agent_name, key)):  # the expander keeps it as text.
            val = literal_expr(val)  # store values are data, never expressions — see above.
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


def behavior_pick(
    snapshot: KeyStore, *, active_agent: str, key: str,
) -> "tuple[str | None, object]":
    """The §2d active-over-default pick for ONE *key*, RAW: ``(slot, value)``.

    *slot* is ``"active"`` (``agent.<active_agent>.<key>``) or ``"default"``
    (``agent.default.<key>``) — the slot that HELD *key* — or ``None`` with
    ``__MISSING__`` when neither did. A present value (incl. present-``None``) SETS
    the key and shadows the default backstop below it, and comes back as stored:
    this pick keeps ABSENT (``__MISSING__``) apart from PRESENT-``None``. The one
    carrier of the per-key SCALAR pick: :func:`effective_behavior` reads the value
    (and collapses a present-``None``), :func:`behavior_slot` the slot, and a caller
    that must keep the two states apart (``start._persona_model_state``) reads it
    raw. The subtree counterpart is :func:`_agent_pick_node`, which deep-overlays
    the default slot, then the active slot, and materializes ``meta.box.agent.*``.
    A pseudo-agent (``shell``) has no default slot, so its pick is ``"active"`` or
    ``None`` (:func:`_fallback_node`).
    """
    agent_node = dict.get(snapshot, "agent", __MISSING__)
    if not isinstance(agent_node, KeyStore):
        return None, __MISSING__
    active_node = dict.get(agent_node, active_agent, __MISSING__)
    if isinstance(active_node, KeyStore):
        val = dict.get(active_node, key, __MISSING__)
        if val is not __MISSING__:
            return "active", val
    default_node = _fallback_node(agent_node, active_agent)
    if isinstance(default_node, KeyStore):
        val = dict.get(default_node, key, __MISSING__)
        if val is not __MISSING__:
            return "default", val
    return None, __MISSING__


def behavior_slot(
    snapshot: KeyStore, *, active_agent: str, key: str,
) -> "str | None":
    """WHICH slot answered *key* in :func:`effective_behavior`'s pick.

    ``"active"`` = ``agent.<active_agent>.<key>``, ``"default"`` =
    ``agent.default.<key>``, ``None`` = neither holds it. For a caller that must NAME
    the setting behind a value (a refusal's cure): the value alone cannot tell
    ``agent.default.bootstrap: none`` from ``agent.claude.bootstrap: none``.
    """
    return behavior_pick(snapshot, active_agent=active_agent, key=key)[0]


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
    behavior leaf under ``agent.<active>`` ∪ ``agent.default`` (``agent.<active>``
    alone for a pseudo-agent, :func:`_fallback_node`). ⚑ DISCOVERY EXISTS
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

    ⚑ A LEAF WHOSE STORED SHAPE IS NOT A SCALAR IS STRINGIFIED BY THE MODULE THAT OWNS THE
    SHAPE, not by ``str()`` — :func:`~kanibako.settings.agent_file.stored_leaf_text`.  Today
    that is the argv list ``run_args``, which arrives here as the command-line string it was
    split from; a bare ``str()`` handed the Python repr ``['--a', '--b']`` to every reader of
    ``box show --effective``.  The table stays ``dict[str, str]`` deliberately: every consumer
    coerces from a string, and the argv round-trip is lossless under the shipped whitespace
    split, so widening it would touch every consumer to carry nothing.
    """
    agent_node = dict.get(snapshot, "agent", __MISSING__)
    out: dict[str, str] = {}
    if not isinstance(agent_node, KeyStore):
        return out
    active_node = dict.get(agent_node, active_agent, __MISSING__)
    default_node = _fallback_node(agent_node, active_agent)
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
        _slot, val = behavior_pick(snapshot, active_agent=active_agent, key=key)
        if val is __MISSING__ or val is None:
            continue
        # Behavior leaves are scalars; a category subtree / Bind is NOT behavior.
        if isinstance(val, (KeyStore, Bind)):
            continue
        # The non-scalar shapes render through their owner (see the docstring); everything
        # else keeps the raw ``str()`` its consumers already coerce back from.
        text = stored_leaf_text(key, val)
        out[key] = text if text is not None else (val if isinstance(val, str) else str(val))
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
            f"'{shown_key(key)}' is not materialized in this snapshot (no "
            f"meta.agent.{display_agent_ref(active_agent)} node) — the launch grammar "
            f"composes from the keyspace, so the resolve must carry meta_agent_grammar_floor()"
        )
    mode_node = dict.get(slot, "mode", __MISSING__)
    if not isinstance(mode_node, KeyStore):
        raise SettingsError(
            f"'{shown_key(key)}' is not materialized in this snapshot — the "
            f"launch grammar "
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
                f"'{shown_key(key)}.{mode_key}' is not an argv fragment "
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


def resolve_box_dest(raw: str, box_ctx: ResolveCtx) -> str:
    """Expand a stored ``box_dest`` expression to the ABSOLUTE GUEST PATH consumers key on.

    ⚑⚑ THE ONE RESOLUTION OF A ``box_dest``, and every consumer that needs one calls
    THIS. The eager build defers ``~`` and ``$VAR`` in a destination
    (``settings_expand._expand_dest_key``), so an arm KEY still spells
    ``$XDG_DATA_HOME/z`` while the collapsed bind map is keyed by the expansion — two
    strings for one destination, and anything comparing them must expand first.

    Env vars resolve from the HOST perspective (keyspec ``:344``): a mount list is
    built before any box exists, so there is no box environment to resolve against
    and the host's is the deterministic answer. ``~`` is not an env var — it is the
    box's home, fixed machinery.

    An escaped ``\\$`` survives the deferral verbatim and unescapes here, so a literal
    dollar the user meant stays one. Every ``@`` is literal here: the build resolved
    the ``@``-refs, and the box never processes ``@`` (keyspec "References").
    """
    return expand_expr(
        raw.replace("@", "\\@"), space="guest", ctx=box_ctx, lookup=_no_lookup,
    )


def snapshot_category_entries(
    snapshot: KeyStore,
    *,
    active_agent: str,
    box_ctx: ResolveCtx,
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
    table here — a structural test scans for it. Each box_dest is resolved through
    :func:`resolve_box_dest` against *box_ctx*, so every seam keys on the SAME
    absolute guest path. Reads via the UNBOUND ``dict`` protocol (S3).

    ⚑ THE ``host_dest_keys`` COMPANION IS GONE (2026-08-08c). Every destination is
    GUEST-spelled now, copies included (spec §0 "ONE DEST SPACE, TWO DELIVERIES"), so
    there is no second namespace for a key set to select. Do not reintroduce one.
    """
    collected: list[tuple[tuple[int, str, str], CategoryEntry]] = []
    scope_order = {s: i for i, s in enumerate(_SCOPES)}

    def _box_dest(raw: str) -> str:
        return resolve_box_dest(raw, box_ctx)

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
    it. A pseudo-agent's pick reads no default tier (:func:`_fallback_node`), so every
    leaf it emits is ``agent.<active>``'s.
    """
    active_tier = (
        dict.get(agent_node, active_agent, __MISSING__)
        if isinstance(agent_node, KeyStore) else __MISSING__
    )
    if pseudo_agent_fence(active_agent) is not None:
        return _fixed_decl_scope_fn(f"agent.{active_agent}")

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
    overlay. A pseudo-agent's pick is its own tier alone (:func:`_fallback_node`).

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
    default_node = _fallback_node(agent_node, active_agent)
    active_node = dict.get(agent_node, active_agent, __MISSING__)
    out = KeyStore()
    if isinstance(default_node, KeyStore):
        _overlay_into(out, default_node)
    if isinstance(active_node, KeyStore):
        _overlay_into(out, active_node)
    return out


def _fallback_node(agent_node: KeyStore, active_agent: str) -> object:
    """The tier the §2d pick falls back to for *active_agent*: ``agent.default``, or none.

    ⚑ ONLY TRUE AGENTS INHERIT FROM ``agent.default`` (keyspec §2d, *"Pseudo-agent(s)"*).
    A pseudo-agent's block declares a value for every universal key and its other keys are
    unset, so for ``shell`` this answers ``__MISSING__`` and the pick reads the shell tier
    alone — no ``agent.default`` value, scalar or category entry, reaches a plain-shell box.
    The one carrier of that rule for :func:`behavior_pick`, :func:`effective_behavior` and
    :func:`_agent_pick_node`; the pseudo-agent set is ``settings_keyspace``'s fence table.
    """
    if pseudo_agent_fence(active_agent) is not None:
        return __MISSING__
    return dict.get(agent_node, "default", __MISSING__)


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
    node — the CATEGORY-ROOT case, ``box.env: "foo"``, where a VALUE sits where the
    family's map belongs: they are the scalar-valued pair, outside the boundary
    approved for the bind pass, and widening them is a decision, not an omission to fix
    in passing.
    🛑 THAT IS THE ROOT CASE ALONE, AND IT IS NO LONGER THE WHOLE STORY.  A non-scalar
    at a LEAF of either family (``box.env.FOO: ['--x', '--w']``) is REFUSED, by name, at
    the emit below (``settings_categories.refuse_non_scalar_family_value``, §2a) — so
    "silent skip" describes what happens ABOVE the leaves and nothing else.

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
                shown = shown_key(key_prefix)
                raise SettingsError(
                    f"{shown}.bindings.{name} is an ARM-LESS binding, which is "
                    f"not a declared key; bindings are declared per arm and the arm "
                    f"is the WHOLE key — {shown}.bindings.ro / "
                    f"{shown}.bindings.rw, each a TERMINAL map keyed by box "
                    f"destination (spec §2a / §2d). Move the entry under one of the "
                    f"two arms, keyed by its destination"
                )
        for mode in ("ro", "rw"):
            mode_node = dict.get(bindings, mode, __MISSING__)
            if mode_node is not __MISSING__:
                _require_category_node(key_prefix, f"bindings.{mode}", mode_node)
    for category in BIND_LEAF_CATEGORIES:
        cat_node = dict.get(node, category, __MISSING__)
        if cat_node is not __MISSING__:
            _require_category_node(key_prefix, category, cat_node)
    # ⚑ ``masks`` is checked on its own line rather than folded into
    # ``BIND_LEAF_CATEGORIES``: that set is what the EMIT walks, and a mask has no
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
    ⚑ A value out of a SETTINGS FILE is refused before it gets here, at the parse that
    names the file (:func:`~kanibako.settings.settings_resolve.refuse_scalar_at_table_key`),
    so what reaches this check is the FLOOR and the ``bindings`` namespace.
    """
    if isinstance(node, KeyStore):
        return node
    # ⚑ Every bind-shaped category is a TERMINAL dest-keyed map (2026-08-08c), so what
    # a user must declare is the MAP, keyed by destination — never a ``.<name>`` entry,
    # which is no longer a key at any scope. ``masks`` is dest-keyed like the rest but
    # its VALUE is the 3-state marker, not a source, so only the example differs.
    shown = shown_key(key_prefix)
    declared = (
        f"{shown}.bindings.{{ro,rw}}" if category == "bindings"
        else f"{shown}.{category}"
    )
    shape = (
        "{box_dest: true}" if category == "masks"
        else "{box_dest: [src[, options]]}"
    )
    raise SettingsError(
        f"{shown}.{category} is a value at a CATEGORY ROOT "
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
                    decl_scope_fn=decl_scope_fn,
                )

    # caches / seeded / common / synced — the map is AT the category token.
    for category in BIND_LEAF_CATEGORIES:
        cat_node = dict.get(scope_node, category, __MISSING__)
        if isinstance(cat_node, KeyStore):
            _emit_bind_map(
                collected, cat_node, order=order, scope=scope,
                category=category, box_dest_fn=box_dest_fn,
                decl_scope_fn=decl_scope_fn,
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
            # ⚑ THE SCALAR REFUSAL (§2a), and it is not a display rule: the coercion
            # below used to hand ``str(['--x', '--w'])`` to the guest as the variable's
            # literal VALUE. This is the ONE site that sees the MERGED snapshot, so it
            # covers the collapse, ``LaunchDeliveries``, the launch env map and
            # ``box show --effective`` from a single raise.
            refuse_non_scalar_family_value(
                f"{decl_scope_fn('env', var)}.env.{var}", "env", value,
            )
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
            # ⚑ A PRESENT ``None`` IS EMITTED, with no source: it is the keyless
            # declaration (§2a) and a VALUE in the per-VAR cascade (§2h), so a lower
            # scope's pointer must not win past it. ``secret_path_winners`` picks it
            # and ``secret_path_deliveries`` mounts nothing for it.
            if path_val is None:
                collected.append((
                    (order, "secret_path", var),
                    CategoryEntry(
                        category="secret_path", scope=scope,
                        box_dest=f"{SECRET_MOUNT_DIR}/{var}", host_src=None,
                        delivery="MOUNT", options="ro", name=var,
                        key_segments=(
                            *decl_scope_fn("secret_path", var).split("."),
                            "secret_path", var,
                        ),
                    ),
                ))
                continue
            # ⚑ THE SCALAR REFUSAL (§2a) — the env branch's twin, and here the coercion
            # it replaces produced a MOUNT SOURCE spelled as a Python repr, which the
            # bare-relative refusal below then reported as the wrong defect.
            refuse_non_scalar_family_value(
                f"{decl_scope_fn('secret_path', var)}.secret_path.{var}",
                "secret_path", path_val,
            )
            host_src = path_val if isinstance(path_val, str) else str(path_val)
            if host_src and not host_src.startswith("/"):
                # ⚑ [R147] REACHES THIS FAMILY, and a ``type: path`` grep MISSES it:
                # the manifest declares ``secret_path`` as ``value: path``, parametric
                # on VAR, so the VALUE is a path key like any other.
                # ⚑ TWO REFUSALS, SPLIT BY WHERE THE VALUE CAME FROM. A value a SETTINGS
                # FILE stores is judged first by ``_refuse_ambiguous_path_values`` in
                # ``build_launch_snapshot``, and gets the two-readings message, its
                # second reading anchored at the scope root by ``path_key_anchor``.
                # This §2a SOURCE refusal is for what the sweep does not judge: a value
                # kanibako supplies itself (the persona, descriptor, CLI and floor
                # rungs), and a snapshot ``build_launch_snapshot`` did not build, such
                # as the one ``workset_cmd``'s preview expands on its own.
                # 🛑 NOT SOFTENED BY ``fail_soft``: that covers a path that is missing
                # or unreadable, and this path is neither. podman would MAKE the named
                # volume, so the mount "succeeds" and the box gets an empty directory
                # where its credential should be — the exact silence fail-soft's WARN
                # exists to break.
                raise SettingsError(
                    f"{decl_scope_fn('secret_path', var)}.secret_path.{var} is set "
                    f"to {host_src!r}, a BARE RELATIVE path. A secret's host path must "
                    f"resolve on its own — absolute, '~/...', '$XDG_*/...' or an "
                    f"'@'-ref: {BARE_RELATIVE_SOURCE_HAZARD}."
                )
            box_dest = f"{SECRET_MOUNT_DIR}/{var}"
            sort_key = (order, "secret_path", var)
            collected.append((
                sort_key,
                CategoryEntry(
                    category="secret_path",
                    scope=scope,
                    box_dest=box_dest,
                    host_src=host_src,
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
    materialization identify an entry by where it lands (R-10).
    """
    for dest in dict.keys(map_node):
        entry = dict.__getitem__(map_node, dest)
        # ⚑ The DEST is the LAST segment and stays whole: it is data, and a dest
        # such as ``~/.cache/uv`` carries dots of its own (see CategoryEntry).
        key_segments = (
            *decl_scope_fn(category, dest).split("."),
            *category.split("."), dest,
        )
        if entry is None:
            # A ``<None>`` SOURCE, whole-value or embedded (spec §0): the entry is
            # SKIPPED in every category, never refused ([Q80] (a)) — a seeded layer
            # (§2a) and a bind alike.  ``settings_expand`` hands it up as ``None``, not
            # absent, so it still overrides a fallback arm in the §2d pick ([R177]).
            # A STANDARD bind's lone ``<None>`` is WARNED once, by
            # :func:`_warn_lone_none_standard_binds`; a user-added one is silent ([R185]).
            continue
        if not isinstance(entry, BindEntry):
            raise SettingsError(
                f"category "
                f"{entry_label(shown_key('.'.join(key_segments[:-1])), dest)} is "
                f"{type(entry).__name__}, "
                f"expected a BindEntry ({category} is dest-keyed: the map key is "
                f"the destination)"
            )
        _emit_bind(
            collected, order, scope, category, dest,
            entry.src, dest, entry.opts, box_dest_fn,
            key_segments=key_segments,
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
) -> None:
    """Append one bind-shaped :class:`CategoryEntry` (MOUNT or COPY).

    ⚑ This function takes PRIMITIVES, not a bind object, and that is the point (P7
    ruling). Its one caller has already ruled in the leaf TYPE at the seam that knows
    the shape, so by the time anything gets here there is only ONE unpacked triple and
    no second place a destination could come from. A leaf type check inside here would
    put two shapes in one function (CONVENTIONS §0) and would leave "take the dest from
    the value" expressible.

    *host_src* is used AS-IS — a stored source resolves on its own (§2a) and NOTHING is
    prefixed here. ⚑ That is now GUARANTEED rather than assumed: every declaration loader
    roots an abstract category and refuses a bare-relative concrete source AT THE PARSE
    (``settings_assemble.parse_bind_map`` for a settings file or a floor key,
    ``agent_defaults.load_common`` / ``load_category_binds`` for a plugin's own file), so a
    relative source cannot reach here to tempt anyone into prefixing one.
    *box_dest_raw* is the UNRESOLVED destination, which *box_dest_fn*
    resolves box-side. *opts* is the per-entry options override. *key_segments* is the
    DISCRIMINATED declaration key plus the entry's DEST as the last segment.

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
        ),
    ))


def _no_lookup(ref: str, chain: tuple[str, ...]) -> str:
    """``expand_expr`` lookup for :func:`resolve_box_dest`, which escapes every ``@``
    first, so no ``@``-ref reaches it; it raises rather than silently emit ``""``.
    """
    raise SettingsError(f"unexpected @-reference in a box_dest: {ref!r}")


def resolve_box_scalars(
    *,
    workset_path: Path | None,
    box_path: Path | None,
    cli_overrides: "dict[str, object] | None",
    inputs: LaunchInputs | None = None,
    agent_name: str = GENERAL_SLOT,
    agent_path: Path | None = None,
    refuse_null_scalars: bool = True,
) -> dict[str, object]:
    """The box scalars resolved through the keyspace, as ``{dotted key: value}``.

    With *inputs*, the resolve is theirs (its subject, files and anchors), under
    *agent_name* and its settings file *agent_path*. Without, a box or working-set
    file is read as a BOX resolve over those paths, and neither means SYSTEM.

    ⚑ A present ``None`` that WINS the cascade is KEPT (spec §2h: "the consumer reads
    None, never the key's default").  Where the key gives a null no meaning
    (:func:`config.refuses_null_box_scalar`) the resolve REFUSES it, naming the file of
    the tier that supplied it — decided HERE, on the resolved value, so a null at any
    tier is judged and a null a more authoritative tier or the CLI overrides is not.
    *refuse_null_scalars* off is the DISPLAY's read: the ``None`` is returned, to be
    printed ``null``.  A caller that ACTS on these values must not ask for it.
    """

    overrides = cli_overrides or {}
    image_val = overrides.get("box_image")
    cli_level = build_cli_level(
        image=str(image_val) if image_val else None,
        share_images=bool(overrides.get("box_share_images", False)),
    )
    std = load_std_paths(load_config(user_config_file()))
    written: list[_WrittenLevel] = []
    if inputs is None and box_path is None and workset_path is None:
        inputs = resolve_inputs(
            subject=ResolveSubject.SYSTEM, std=std, agent_name=GENERAL_SLOT,
            system_path=std.settings,
        )
    if inputs is not None:
        snapshot = build_launch_snapshot(
            **inputs.as_kwargs(), agent_name=agent_name, agent_path=agent_path,
            cli_level=cli_level, written_out=written,
        )
    else:
        snapshot = build_launch_snapshot(
            agent_name=agent_name,
            ctx=ResolveCtx(
                agent_name=agent_name, workset_name=None,
                host_home=str(Path.home()), xdg=host_xdg_map(),
            ),
            system_path=std.settings if std.settings.exists() else None,
            agent_path=agent_path,
            workset_path=workset_path,
            box_path=box_path,
            cli_level=cli_level,
            written_out=written,
        )
    resolved: dict[str, object] = {}
    for dotted in _BOX_SCALAR_FIELDS:
        node = snapshot_leaf(snapshot, dotted)
        if node is not __MISSING__:
            resolved[dotted] = node
    if refuse_null_scalars:
        _refuse_null_box_scalars(resolved, written)
    return resolved


def _refuse_null_box_scalars(
    resolved: "Mapping[str, object]", written: "Sequence[_WrittenLevel]",
) -> None:
    """Refuse every RESOLVED box scalar that is ``None`` where its declared default is a VALUE.

    ⚑ NAMED, NEVER SUBSTITUTED.  The read that answered such a ``None`` with the key's own
    default made ``box show --effective`` print an image no tier held.  The membership is
    :func:`config.refuses_null_box_scalar` and the text is the shared
    :func:`config.null_path_keys_error` builder, so this door and the ``set`` door cannot
    drift apart on the sentence they share.  The file named is the WINNING tier's
    (:func:`_none_setter` over *written*), one block per file.
    """
    by_file: dict[str, list[str]] = {}
    for dotted, value in resolved.items():
        if value is None and refuses_null_box_scalar(dotted):
            source = _none_setter(written, dotted, None) or "<defaults>"
            by_file.setdefault(source, []).append(dotted)
    if not by_file:
        return
    errors = [
        null_path_keys_error(
            Path(source), keys, read_head=ERR_BOX_SCALAR_NULL_HEAD, cure=ERR_BOX_SCALAR_NULL_CURE,
        )
        for source, keys in sorted(by_file.items())
    ]
    raise SettingsError("\n".join(e for e in errors if e is not None))


def load_merged_config(
    project_path: Path | None = None,
    *,
    workset_path: Path | None = None,
    cli_overrides: "dict[str, object] | None" = None,
    inputs: LaunchInputs | None = None,
    agent_name: str = GENERAL_SLOT,
    agent_path: Path | None = None,
    refuse_null_scalars: bool = True,
) -> KanibakoConfig:
    """The box scalars as a :class:`KanibakoConfig`: each file's present values, then the keyspace resolve.

    *agent_path* is the settings file *agent_name*'s, read in BOTH resolve shapes
    (:func:`resolve_box_scalars`), so an agent file's ``box:`` table reaches the
    display that claims to show what a launch runs.

    *refuse_null_scalars* is :func:`resolve_box_scalars`'s: off, a ``null`` the launch
    refuses stays ``None`` on the field so the display prints it ``null`` (spec §2h).
    """
    if inputs is not None:
        workset_path, project_path = inputs.cascade_workset_path, inputs.cascade_box_path
    defaults = KanibakoConfig()
    cfg = KanibakoConfig()
    if cli_overrides:
        valid_keys = {fld.name for fld in fields(cfg)}
        for k, v in cli_overrides.items():
            if k in valid_keys:
                setattr(cfg, k, v)
    # ⚑ The files reach the fields ONLY through the resolve, so every tier's value — a
    # present ``None`` included — is judged once, after the whole cascade.
    resolved = resolve_box_scalars(
        workset_path=workset_path, box_path=project_path,
        cli_overrides=cli_overrides, inputs=inputs,
        agent_name=agent_name, agent_path=agent_path,
        refuse_null_scalars=refuse_null_scalars,
    )
    for dotted, field_name in _BOX_SCALAR_FIELDS.items():
        if dotted in resolved:
            value = resolved[dotted]
            setattr(cfg, field_name,
                    None if value is None else _typed_box_scalar(defaults, field_name, value))
    return cfg

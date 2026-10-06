"""Cascade level assembly — per-scope settings files → ordered ``KeyStore`` partials.

**_Terminology_**
- _level_: one cascade tier, identified by its FILE. The 6, least→most authoritative:
  ``base < system < agent.default < agent.<active> < workset < box`` (spec §2)
- _partial_: that level's whole file content as a nested :class:`~kanibako.settings.keystore.KeyStore`
- _scope token_: the ``system``/``agent``/``workset``/``box`` root a file is spelled against — KEPT
  in the partial (§0: namespace is ORTHOGONAL to cascade)

READS + structural parsing ONLY: no merge/precedence, no ``@``-ref / ``$var`` / ``~`` expansion, no
typed views, no ``config set``. Binds keep their tokens RAW (spec §0). Authority, the seams realized
here (S3/S7/S8/S9/S13/S14) & the dest-keying depth rule: llm-docs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from kanibako.agent_ref import agent_address_node, agent_segment_case, display_agent_ref
from kanibako.errors import ConfigError
from kanibako.settings.agent_config import (
    AgentConfig,
    category_root_ref,
    root_relative_source,
)
from kanibako.settings.agent_file import (
    FILE_SCOPE,
    ROOT_SECTIONS,
    contributed_tables,
    level_table,
    record,
    refuse_node_spelled_twice,
)
from kanibako.settings.bootstrap import CONFIG_PATH_DEFAULTS
from kanibako.settings.config import (
    _LAYER1_TABLE,
    _flatten_leaves,
    config_base_path,
    settings_base_path,
    user_config_file,
)
from kanibako.settings.config_io import load_doc
from kanibako.settings.kb_store import (
    BINDING_DERIVATIONS_NODE,
    Bind,
    BindEntry,
    SCOPE_CONTAINMENT,
)
from kanibako.settings.keystore import KeyStore, ReservedKeyError
from kanibako.settings.settings_categories import (
    ABSTRACT_CATEGORIES,
    DECLARATION_ROOT_REF,
)
from kanibako.settings.settings_drops import cascade_drop_set, upward_scope_drop_set
from kanibako.settings.settings_keyspace import (
    BIND_LEAF_CATEGORIES,
    SCALAR_AGENT_LEAVES,
    TABLE_VALUED_AGENT_LEAVES,
    TERMINAL_CATEGORY_TAILS,
    Judgment,
    is_terminal_category_key,
    pref_allowlist_entry,
    display_store_path,
    undeclared_store_paths,
)
from kanibako.settings.settings_keyspace_probe import keyspace_verdict
from kanibako.settings.settings_prefs import PREF_LEGAL_LEVELS, PREF_ROOT, refuse_pref_table
from kanibako.settings.settings_resolve import (
    SettingsError,
    check_bind_map,
    normalize_bind_dest,
    refuse_dest_spelled_twice,
    refuse_scalar_at_table_key,
    refuse_unrooted_source,
    unpack_bind,
    unpack_bind_entry,
)

_log = logging.getLogger(__name__)

# ⚑ The ARMED bind-shaped category: ``bindings`` is the one whose category token
# is NOT the whole key — its two ARMS are (``bindings.ro`` / ``bindings.rw``), and
# each arm holds a ``BindMap``.
_DEST_KEYED_CATEGORY = "bindings"
#: The two arms a dest-keyed ``bindings`` node carries; each holds a ``BindMap``.
_BIND_ARMS: tuple[str, str] = ("ro", "rw")
# The bind-shaped categories whose CATEGORY TOKEN IS THE WHOLE KEY are
# ``settings_keyspace.BIND_LEAF_CATEGORIES``.
# ⚑⚑ That set says WHERE the map sits, not WHETHER there is one: read at the wrong
# depth, a destination is taken for an arm name. Why a blanket ``dest_keyed=True``
# cannot replace it (the 2-element arity trap): llm-docs.

#: The dest-keyed TERMINAL categories whose token is the whole key but whose value is the
#: 3-state MARKER, not a ``BindMap`` (``masks``). DERIVED from ``TERMINAL_CATEGORY_TAILS``:
#: every one-segment terminal tail that is not a bind leaf. Its keys are canonicalized like
#: every other guest dest (spec §2a "A MOUNT DESTINATION IS CANONICALIZED").
_MARKER_LEAF_CATEGORIES: frozenset[str] = frozenset(
    tail[0] for tail in TERMINAL_CATEGORY_TAILS if len(tail) == 1
) - BIND_LEAF_CATEGORIES

# The bind-shaped category tokens; every one holds dest-keyed ``BindMap``(s). ``masks`` (a keyed
# 3-state, S5; dests canonicalized by :func:`_parse_marker_map`) and the scalar ``env`` /
# ``secret_path`` families are NOT bind-parsed. ``bindings`` carries the ``ro`` / ``rw`` sub-tables, each holding a map.
# ⚑ These are path SEGMENTS met on a tree walk, so ``bindings`` is UNSPLIT: the walk meets that
# segment before it can see the arm. DERIVED (P13) from the two constants above — the ARMED
# category plus the terminal leaves ARE the tokens, so the two spellings cannot drift.
# ⚑ The ``frozenset(...)`` wrap is load-bearing: ``{x} | frozenset(...)`` evaluates to a ``set``.
BIND_CATEGORY_TOKENS: frozenset[str] = frozenset(
    {_DEST_KEYED_CATEGORY} | BIND_LEAF_CATEGORIES
)

# The agent sub-table that supplies the all-agents ``agent.default`` cascade level.
_AGENT_DEFAULT_SUB = "default"
_BASE_TO_BOX_LEVELS: tuple[str, ...] = ("base", *SCOPE_CONTAINMENT)


# ---------------------------------------------------------------------------
# DECLARATION ROOTS for a SETTINGS FILE (spec §2a)
# ---------------------------------------------------------------------------
# ⚑⚑ THIS IS WHERE "DECLARATION-LOAD TIME" IS for a user-authored file: the parse
# below is the ONE place a YAML entry becomes a stored ``BindEntry``, so it is the
# one place the root may be supplied. Rooting anywhere downstream — the merge, the
# expand, the emit — is "rooted at ASSEMBLY", which §2a names FORBIDDEN.
#
# ⚑ The SCOPE comes off the KEY PATH the parse is walking, never a caller flag: the
# scope token is kept in the partial (§0), so the walk already carries the one fact
# the DECLARATION-ROOT table is keyed on.


def _declaration_root_ref(path: tuple[str, ...], category: str) -> str | None:
    """The §2a DECLARATION ROOT for an ABSTRACT *category* declared at key *path*.

    *path* is the key path ABOVE the category token, doc-root first. ``None`` means
    "no root applies" — a CONCRETE category (which takes no root at any scope), or a
    path whose head is not a scope, which is an UNDECLARED key that the §0 refusals
    downstream must name as such rather than have this function guess a root for.

    ⚑ A ``pref.`` head is STRIPPED, not refused: a pref's value is installed AT its
    target key (spec §2h), so it must be stored exactly as that key's own file would
    store it — an unrooted pref would reintroduce the divergence one level up.
    """
    if category not in ABSTRACT_CATEGORIES:
        return None
    segments = path[1:] if path[:1] == (PREF_ROOT,) else path
    if not segments:
        return None
    scope = segments[0]
    if scope not in DECLARATION_ROOT_REF:
        return None
    if scope == "agent":
        # The agent tier is DISCRIMINATED (§2d): without the node there is no row.
        if len(segments) != 2:
            return None
        return category_root_ref(scope, category, agent=segments[1])
    return category_root_ref(scope, category) if len(segments) == 1 else None


# ---------------------------------------------------------------------------
# RETIRED agent-selection spellings — refuse by name (P7, spec §0 / §2b / §2g)
# ---------------------------------------------------------------------------
# ⚑ NOT migration machinery (which is documentation-only for this arc): it is §0's CLOSED-KEYSPACE
# rule — an undeclared key is an ERROR that NAMES it — applied to three retired spellings. Scope is
# deliberately TIGHT: these three keys, nothing else. Why, and the M-7 precedent: llm-docs.

#: The NESTED FILE path of each retired leaf → the retired KEY name. The cure is LEVEL-DEPENDENT
#: (:func:`_retired_key_cure`) — a pref is legal only in a workset or box file (§2h). Record: M-4.
#: ⚑⚑ ``box.agent`` is ONE path carrying TWO retired spellings, told apart by the VALUE SHAPE the
#: file holds (both are manifest ``renamed`` rows): a SCALAR is the agent-NAME spelling
#: (``box.crab`` → ``box.agent`` → ``box.agent_name``, all → ``pref.system.agent``), a TABLE is the
#: settable agent MIRROR ``box.agent.<key>`` (R-4 → ``pref.agent.<agent>.<key>``). Two stories and
#: two cures — telling one story for both sends half of these users at the wrong key.
RETIRED_FILE_KEYS: "dict[tuple[str, ...], str]" = {
    ("box", "agent"): "box.agent",
    ("box", "agent_name"): "box.agent_name",
    ("agent", "default", "default_agent"): "system.default_agent",
}

def _stored_spelling(raw: Any) -> str:
    """A stored leaf AS THE USER'S FILE SPELLS IT — ONE derivation, shared by every message and
    cure this module quotes a stored value back into.

    ⚑ A ``bool`` takes YAML's ``true``/``false``, NEVER Python's ``True``/``False``. Both readings
    depend on it: the message is compared against the file the reader is looking at, and a cure is
    pasted into a CLI that parses the YAML spelling. A present-``None`` leaf has nothing to quote
    and renders EMPTY — each caller supplies its own shape for that (``<name>``, ``(empty)``).

    ⚑ The parameter is ``raw``, not ``value``: this lowercases a BOOL's YAML spelling, and
    ``value`` is an identifier name to ``tests/test_identifier_case_enforcement.py``, whose
    doctrine is that a non-identifier bearing an identifier's name gets RENAMED, not exempted.
    """
    if isinstance(raw, bool):
        return str(raw).lower()
    return "" if raw is None else str(raw).strip()


def _cure_assignment(sub: str, value: Any) -> str:
    """The ``<key>=<value>`` tail a cure can be COPY-PASTED with, for ONE retired mirror leaf.

    ⚑ TWO shapes, because the file's leaf has two. A SCALAR is quoted verbatim
    (:func:`_stored_spelling`) so the command runs as printed. A nested TABLE has no single-token
    spelling at all, so the tail stays a PLACEHOLDER one level deeper rather than a repr that
    cannot work.
    """
    if value is None or isinstance(value, (dict, list, tuple)):
        return f"{sub}.<key>=<value>"
    spelled = _stored_spelling(value)
    return f"{sub}={spelled}" if spelled else f"{sub}=<value>"


def _cure_subject(level: str, box_name: str | None) -> str:
    """The REQUIRED subject positional for ``kanibako <level> set`` — ONE derivation, shared by
    every cure this module emits.

    ⚑ BOTH pref-legal verbs take a subject — ``box set <box>`` and ``workset set <workset>`` — and
    the verb is the LEVEL, never a hardcoded ``box``. Only a box-level refusal whose CALLER knows
    the box has a subject to name (*box_name*, threaded by both retired-key cures); anything else
    is a PLACEHOLDER, never nothing.

    ⚑ MEASURED, and it is why this cannot be checked by reading: dropping the positional does NOT
    fail loudly at either level. ``workset set`` binds the KEY to its ``workset`` positional and
    leaves ``key_value`` empty, so the pasted line hunts for a working set named after the key;
    ``box set`` takes its arguments as a LIST, so the key alone parses and the write lands on
    whatever box the reader's cwd resolves to — a different box, silently.
    """
    return box_name if level == "box" and box_name else f"<{level}>"


def _retired_mirror_cure(
    *, level: str, box_name: str | None, table: "dict[Any, Any]",
) -> str:
    """The LEVEL-APPROPRIATE fix for a TABLE-valued ``box.agent`` — the RETIRED settable agent
    MIRROR ``box.agent.<key>`` (R-4), whose replacement is the §2h per-agent request.

    ⚑ The agent renders as the ``<agent>`` PLACEHOLDER, the :func:`_retired_behavior_cure` shape:
    this seam runs BEFORE selection, so naming an agent here would be a guess — and the guess a
    box carrying this table most needs kanibako not to make.
    """
    tails = [_cure_assignment(str(sub), val) for sub, val in table.items()] or ["<key>=<value>"]
    if level in PREF_LEGAL_LEVELS:
        subject = _cure_subject(level, box_name)
        return "; ".join(
            f"kanibako {level} set {subject} pref.agent.<agent>.{tail}" for tail in tails
        )
    # Same §2h gate the scalar cure applies: no request may be written here at all.
    return (
        f"REMOVE it — a request may be written ONLY in a workset or box settings "
        f"file (spec §2h), so this table has NO equivalent at {level} scope. If "
        f"you meant to tweak the agent everywhere, set it on the AGENT itself: "
        + "; ".join(f"kanibako agent set <agent> {tail}" for tail in tails)
    )


def _retired_key_cure(
    key: str, *, level: str, value: str, box_name: str | None = None,
    mirror: "dict[Any, Any] | None" = None,
) -> str:
    """The LEVEL-APPROPRIATE fix for a retired key (M-4).

    *box_name* is the addressable box the cure is FOR — ``kanibako box set`` needs it as its
    ``[project]`` positional (spec: the box argument is REQUIRED unless the caller's cwd already
    resolves to that box; Jei hit exactly that gap live). Threaded ONLY for a box-level refusal
    (``None`` at any other *level*, where no single box is being refused for) — a WORKSET-level
    refusal takes the ``workset set`` verb and its own placeholder (:func:`_cure_subject`).

    *mirror* is the retired ``box.agent`` TABLE when that is the shape the file holds — the one
    discriminator between this key's two spellings (:data:`RETIRED_FILE_KEYS`); ``None`` means the
    SCALAR agent-name spelling, and every other retired key.
    """
    if mirror is not None:
        return _retired_mirror_cure(level=level, box_name=box_name, table=mirror)
    if key == "system.default_agent":
        # Always the same cure: the replacement is a SYSTEM-scope key wherever the stale leaf was.
        return f"kanibako system set system.agent={value}"
    # box.agent / box.agent_name → the §2h request, but ONLY where a request may be written.
    # ⚑ The VERB IS THE LEVEL (:func:`_cure_subject`) — a workset file's cure is
    # ``workset set``, the same fork :func:`_retired_mirror_cure` makes.
    # 🛑 THE CURE NAMES AN AGENT AND NOTHING ELSE. It used to offer `--null
    # pref.system.agent` "for a no-agent box"; since the 2026-09-19 ruling a null
    # selection REFUSES to launch (spec §2b), so that half sent the user from one
    # error to another.
    if level in PREF_LEGAL_LEVELS:
        subject = _cure_subject(level, box_name)
        return f"kanibako {level} set {subject} pref.system.agent={value}"
    # M-4: no legal pref equivalent at base/system/agent — FLAG it, never silently relocate it.
    # No single box is in scope here, so the box arm takes the placeholder.
    return (
        f"REMOVE it — a request may be written ONLY in a workset or box settings "
        f"file (spec §2h), so this key has NO equivalent at {level} scope. If you "
        f"meant the host-wide default, set it: kanibako system set "
        f"system.agent={value}. If you meant one box, set the request in THAT "
        f"box's settings file: kanibako box set <box> pref.system.agent={value}"
    )


#: The "no such leaf" sentinel for :func:`_nested_present`. ⚑ NOT ``None``: a
#: ``box: {agent_name:}`` leaf is PRESENT with the value ``None`` and is still the retired key —
#: conflating present-null with absent would let the exact config this catches slip by (llm-docs).
_NO_LEAF: Any = object()


def _nested_present(raw: Any, parts: "tuple[str, ...]") -> Any:
    """Read *raw* at the nested *parts* path, or :data:`_NO_LEAF` when ABSENT."""
    node: Any = raw
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return _NO_LEAF
        node = node[part]
    return node


#: WHY the SCALAR spelling is refused — the agent-SELECTION story (§2g / §2h).
_SELECTION_STORY = (
    "The RULE CHANGED in kanibako 1.8.0: a box no longer names its agent with a "
    "key of its own — it REQUESTS one at the key that resolves earlier "
    "(`pref.system.agent`, spec §2h), and the system default is now "
    "`system.agent` (§2g). Refusing rather than running: kanibako cannot tell "
    "which agent you meant, and guessing would launch a DIFFERENT agent and seed "
    "that agent's credentials into this box."
)

#: WHY a TABLE-valued ``box.agent`` is refused — a DIFFERENT retired thing, so a different story:
#: the user was not naming an agent, they were tweaking one (R-4, spec §2b).
_MIRROR_STORY = (
    "The RULE CHANGED in kanibako 1.8.0: a box no longer carries a SETTABLE "
    "mirror of its agent's settings — it REQUESTS a tweak with "
    "`pref.agent.<agent>.<key>` (spec §2h) and reads the effective value back at "
    "the read-only `meta.box.agent.<key>` (§2b). Refusing rather than running: an "
    "undeclared key is not read at all, so this box would come up on the agent's "
    "UNTWEAKED settings and every override in this table would silently vanish."
)


def refuse_retired_keys(
    raw: Any, *, level: str, path: Path | None, box_name: str | None = None,
) -> None:
    """RAISE when *raw* still carries a RETIRED agent-selection or agent-mirror key (P7); the
    three are :data:`RETIRED_FILE_KEYS`.

    Never a warning and never a silent drop; called at the SELECTION seam
    (:mod:`kanibako.settings.agent_select`), NOT inside :func:`assemble_levels`. Why both: llm-docs.

    *box_name* is passed straight through to :func:`_retired_key_cure` — see its docstring for why
    it is only meaningful at ``level="box"``.
    """
    if not isinstance(raw, dict):
        return
    for parts, key in RETIRED_FILE_KEYS.items():
        found = _nested_present(raw, parts)
        if found is _NO_LEAF:
            continue
        # ⚑ THE ONE DISCRIMINATOR between ``box.agent``'s two retired spellings: a TABLE is the
        # settable MIRROR, anything else is the agent NAME (:data:`RETIRED_FILE_KEYS`).
        mirror = found if key == "box.agent" and isinstance(found, dict) else None
        # The cure quotes the value the user ACTUALLY has, so it is copy-pasteable; a
        # present-``None`` has no value to quote → the shape.
        value = _stored_spelling(found)
        cure = _retired_key_cure(
            key, level=level, value=value or "<name>", box_name=box_name, mirror=mirror,
        )
        where = path if path is not None else "<settings>"
        raise SettingsError(
            f"'{key}' is RETIRED and is still set in the {level} settings file "
            f"{where} "
            f"(as `{' '.join(parts)}:`).\n"
            f"{_MIRROR_STORY if mirror is not None else _SELECTION_STORY}\n"
            f"  Delete the `{' '.join(parts)}` entry from {where} FIRST — the `set` "
            f"below reads that file, and the stale entry refuses the write.\n"
            f"  Fix: {cure}"
        )


# ---------------------------------------------------------------------------
# RETIRED BEHAVIOR spellings — the permission axis (R-41, spec §2d)
# ---------------------------------------------------------------------------
# ⚑ SAME shape, SAME reason, DIFFERENT seam as :data:`RETIRED_FILE_KEYS` above: R-41 replaced the
# boolean ``auto_approve`` with the enum ``access``, and an undeclared stored key is SILENT — which
# on a PERMISSION axis is a safety-class regression in the UNSAFE direction (RQ-2). Scope is TIGHT:
# this one key. The seam is the LAUNCH's behavior tier (``commands/start.py``), NOT
# :func:`assemble_levels`. Record: M-22. Full reasoning: llm-docs.

#: The RETIRED behavior leaf → its successor key (R-41).
RETIRED_BEHAVIOR_KEYS: "dict[str, str]" = {"auto_approve": "access"}

#: The RULED value mapping for the retired boolean (R-41). Keys are the ``coerce_bool`` results; an
#: UNPARSEABLE stored value maps to nothing and the cure names the legal tiers (never guess a tier).
_RETIRED_BEHAVIOR_VALUE_MAP: "dict[str, dict[bool, str]]" = {
    "auto_approve": {True: "full", False: "restricted"},
}

#: The nested TABLES a behavior leaf can live under, per settings-file shape.
#: Each entry is (prefix, depth-of-<sub>); the shapes themselves: llm-docs.
_BEHAVIOR_TABLE_SHAPES: "tuple[tuple[tuple[str, ...], int], ...]" = (
    (("agent",), 1),          # scope file:  agent.<sub>.<leaf>
    (("pref", "agent"), 1),   # §2h request: pref.agent.<node>.<leaf>
    # ⚑ THE AGENT FILE's own root, taken from the boundary — this walk is raw, so it needs the
    # PREFIX rather than a slot, and it is the one site that does (``agent_file.ROOT_SECTIONS``).
    (ROOT_SECTIONS, 0),       # agent file:  <root>.<leaf>
)


def _behavior_leaf_sites(
    raw: Any, leaf: str
) -> "list[tuple[tuple[str, ...], Any]]":
    """Every (nested path, value) where *leaf* is present in *raw*."""
    sites: "list[tuple[tuple[str, ...], Any]]" = []
    if not isinstance(raw, dict):
        return sites
    # ⚑ Walks EXACTLY the declared shapes — no free-form recursion, so an unrelated user key spelled
    # ``auto_approve`` deeper in some other table is not swept up. Do not generalize this loop.
    for prefix, sub_depth in _BEHAVIOR_TABLE_SHAPES:
        node = _nested_present(raw, prefix)
        if node is _NO_LEAF or not isinstance(node, dict):
            continue
        if sub_depth == 0:
            found = _nested_present(node, (leaf,))
            if found is not _NO_LEAF:
                sites.append(((*prefix, leaf), found))
            continue
        for sub, sub_node in node.items():
            if not isinstance(sub_node, dict):
                continue
            found = _nested_present(sub_node, (leaf,))
            if found is not _NO_LEAF:
                sites.append(((*prefix, str(sub), leaf), found))
    return sites


def _retired_behavior_cure(
    successor: str, *, level: str, tier: str, subject: str | None,
    box_name: str | None = None, node: str | None = None,
) -> str:
    """The LEVEL-APPROPRIATE fix for a retired BEHAVIOR key (M-22); per-level reasons in llm-docs.

    ⚑ THE CURE'S AGENT IS THE NODE THE ENTRY IS STORED UNDER (*node*, read from the
    entry's own path), never the agent being RESOLVED (*subject*) — a file may store
    another agent's. Only the agent file's root stores no node and falls back to
    *subject*. ``access`` is an AGENT-scope key (spec §2d), so every level writes it
    under that node.
    """
    named = node or subject
    agent = display_agent_ref(named) if named else "<agent>"
    if agent == _AGENT_DEFAULT_SUB and level not in PREF_LEGAL_LEVELS:
        # ⚑ The reserved any-agent tier owns NO persona store, so the cure must not
        # address it as one: `agent set default access=…` is refused rc 1. The tier's
        # default is written at the SYSTEM scope as the BARE key (spec §2d).
        return f"kanibako system set {successor}={tier}"
    if level == "agent":
        return f"kanibako agent set {agent} {successor}={tier}"
    if level in PREF_LEGAL_LEVELS:
        # ⚑ *agent* is the AGENT inside the key; the verb's own required positional is
        # the BOX or WORKSET — *box_name*, or a PLACEHOLDER (:func:`_cure_subject`).
        return (
            f"kanibako {level} set {_cure_subject(level, box_name)} "
            f"pref.agent.{agent}.{successor}={tier}"
        )
    return f"kanibako system set agent.{agent}.{successor}={tier}"


def refuse_retired_behavior_keys(
    raw: Any, *, level: str, path: Path | None, subject: str | None = None,
    box_name: str | None = None,
) -> None:
    """RAISE when *raw* still carries a RETIRED behavior key (R-41 / RQ-2) —
    :data:`RETIRED_BEHAVIOR_KEYS`.

    *subject* is the agent being RESOLVED; only the agent file's root stores no node, and
    it is the one shape cured under *subject*. *box_name* is the DIFFERENT subject the
    ``box set`` verb needs as its positional, passed straight through to
    :func:`_retired_behavior_cure` → :func:`_cure_subject` — meaningful only at
    ``level="box"``, and deliberately omitted by the identity-free resolve seam
    (``settings_launch._refuse_retired_spelling``), which has no name to give.
    """
    from kanibako.settings.config import coerce_bool

    if not isinstance(raw, dict):
        return
    for leaf, successor in RETIRED_BEHAVIOR_KEYS.items():
        for parts, found in _behavior_leaf_sites(raw, leaf):
            spelling = ".".join(parts)
            # ⚑ Every shape but the root ends ``(..., <node>, leaf)``; the root stores no node.
            node = None if parts[: len(ROOT_SECTIONS)] == ROOT_SECTIONS else parts[-2]
            raw_value = _stored_spelling(found)
            mapped = _RETIRED_BEHAVIOR_VALUE_MAP.get(leaf, {})
            as_bool = coerce_bool(raw_value) if raw_value else None
            tier = mapped.get(as_bool) if as_bool is not None else None
            # ⚑ The value line only ever states a translation the RULING makes; an unparseable
            # stored value gets the legal tiers instead of a guess.
            if tier is not None:
                value_line = (
                    f"Your stored `{leaf}: {raw_value}` means "
                    f"`{successor}: {tier}` (true → full, false → restricted)."
                )
            else:
                tier = "<restricted|editing|full>"
                value_line = (
                    f"Your stored `{leaf}: {raw_value or '(empty)'}` is not a "
                    f"boolean, so it maps to no tier — choose one of "
                    f"restricted | editing | full."
                )
            cure = _retired_behavior_cure(
                successor, level=level, tier=tier, subject=subject, box_name=box_name,
                node=node,
            )
            raise SettingsError(
                f"'{leaf}' is RETIRED and is still set in the {level} settings "
                f"file {path if path is not None else '<settings>'} "
                f"(as `{spelling}`).\n"
                f"The RULE CHANGED in kanibako 1.8.0: the permission axis is no "
                f"longer a boolean — it is the TIER key `{successor}` "
                f"(restricted | editing | full, default full). Refusing rather "
                f"than running: an undeclared key is not read at all, so this "
                f"box would come up at the DEFAULT tier and a deliberately "
                f"restricted box would silently run permissive.\n"
                f"  {value_line}\n"
                f"  Delete the `{spelling}` entry from {path} FIRST — the `set` "
                f"below reads that file, and the stale entry refuses the write.\n"
                f"  Fix: {cure}"
            )


def stored_config_entries(raw: Any) -> dict[str, object]:
    """The top-level ``config:`` table a SETTINGS document carries, as ``config.<key> → value``;
    empty ⇒ the document carries none.

    ⚑ READ AS THE LAYER-1 READER READS IT — the same walk (``config._flatten_leaves``, which
    that reader's ``_flatten_dotted`` stringifies), so the entries named here are the ones
    ``kanibako.cfg`` would accept or refuse if moved there. The values are the leaves AS
    STORED, for the stored view to render as it renders any stored value. A table that
    flattens to nothing — ``config:``,
    ``config: {}``, a ``config:`` whose only leaves are empty tables — or a non-table ``config:``
    is named by the TABLE name, ``config``, carrying the value as stored.

    ONE reading, two consumers: :func:`refuse_config_table` (the resolve) and the stored view
    (``config_interface``), so the two cannot disagree about which lines are meant.
    """
    if not isinstance(raw, dict) or _LAYER1_TABLE not in raw:
        return {}
    table = raw[_LAYER1_TABLE]
    entries: dict[str, object] = {}
    if isinstance(table, dict):
        entries.update(_flatten_leaves(table, _LAYER1_TABLE))
    return entries or {_LAYER1_TABLE: table}


def config_entry_groups(keys: Iterable[str]) -> list[tuple[str, list[str]]]:
    """*keys* — ``config.*`` entries a SETTINGS file carries — grouped by their CURE, as
    ``(cure, sorted keys)`` pairs; the refusal and the stored view both print these.

    The split is the Layer-1 reader's own (:data:`~kanibako.settings.bootstrap.
    CONFIG_PATH_DEFAULTS`, the set ``config.bootstrap_config_paths`` accepts): a DECLARED key
    is cured by moving it to ``kanibako.cfg`` — named by its resolved path, which callers read
    and never compose ([R154]) — and anything else by deleting it: an undeclared or nested
    entry, or a ``config:`` holding a value that is not a table, the ``.cfg`` file would refuse
    too, and a ``config:`` that flattens to nothing (:func:`stored_config_entries`) means
    nothing in either file. A moved key is machine-wide, not this file's: the cure says so.
    """
    move = (
        f"move under 'config:' in {user_config_file()} (site-wide: {config_base_path()}) "
        f"and delete from this settings file; there it relocates that path for every box, "
        f"not only this one"
    )
    delete = (
        f"not a key anywhere (spec §1 declares: {', '.join(sorted(CONFIG_PATH_DEFAULTS))}) "
        f"— delete from this settings file"
    )
    ordered = sorted(keys)
    groups = [
        (move, [k for k in ordered if k in CONFIG_PATH_DEFAULTS]),
        (delete, [k for k in ordered if k not in CONFIG_PATH_DEFAULTS]),
    ]
    return [(cure, group) for cure, group in groups if group]


def refuse_config_table(raw: Any, *, level: str, path: Path | None) -> None:
    """REFUSE a settings file that carries a top-level ``config:`` table, naming the file, the
    entries and each one's cure (:func:`config_entry_groups`).

    Spec §1: the ``config.*`` keys are Layer 1 — they *"Live ONLY in the two ``.cfg`` files"*
    and are *"NOT a settings tier"*. §0's directional DROP does not reach them: it governs a
    key of a CONTAINING scope found in a lower file of the cascade, and ``config`` is not a
    cascade scope at all. A drop would also leave the owner believing the file moved the store
    when it did not; the mirror case — a settings table inside the ``.cfg`` file — refuses the
    same way (``config.bootstrap_config_paths``).
    ⚑ ``config.*`` stays a KEY to the keyspace (``config set`` / ``get`` address it); what is
    refused is the FILE carrying it.
    ⚑ CALLED FROM THE LAUNCH SEAM (``settings_launch.build_launch_snapshot``), NOT from
    :func:`assemble_levels`: that also serves the narrow, non-refusing ``box.enable_vault``
    resolve every box verb runs (``paths.resolve_box_enable_vault``), and a raise there
    would stop ``box show`` — the one surface that shows the user the line to delete.
    """
    entries = stored_config_entries(raw)
    if not entries:
        return
    where = str(path) if path is not None else "<settings>"
    body = "".join(
        "".join(f"  {k}\n" for k in group) + f"    Fix: {cure}.\n"
        for cure, group in config_entry_groups(entries)
    )
    raise SettingsError(
        f"the {level} settings file {where} carries config.* entries, which a settings "
        f"file cannot hold: they live only in the .cfg config files, never in a settings "
        f"tier (spec §1), so kanibako will not read them here.\n{body.rstrip()}"
    )


#: Process-scoped DISPLAY state for :func:`_warn_upward_drops`: the ``(file, key)`` pairs
#: already announced.  It changes no resolution outcome — the drop itself runs on every read —
#: which is why it may be module-level at all, the same footing as
#: ``commands.start._COLLISION_WARNED``.
#: ⚑ WHY IT EXISTS: one command reads one settings file through several resolves, and each
#: announced the drop again — ``box show --effective`` printed one dropped key four times.  One dropped key in one
#: file is ONE fact (spec §0: *"with a warning naming the file and key"*), and it lives for the
#: process, never beyond it: the next command warns again until the file is fixed.
_DROP_WARNED: "set[tuple[str, str]]" = set()


def reset_drop_warnings() -> None:
    """Clear the per-process drop-warning memo (test seam)."""
    _DROP_WARNED.clear()


def announce_drop_once(path: Path | None, token: str) -> bool:
    """Is this the FIRST time this process drops top-level *token* from the file at *path*?

    ⚑ THE ONE GUARD every settings-file read warning asks before it speaks — the three §0 drops
    here (:func:`_warn_upward_drops`), the §2h ``pref:`` one
    (:func:`~kanibako.settings.settings_prefs.refuse_pref_table`) and the capital-node fold
    (:func:`fold_agent_nodes`) — so one fact about one file is named once per command whichever
    reader, and however many resolves, meet it.
    Records the pair; the caller warns only on ``True``.
    """
    memo = (str(path) if path is not None else "<settings>", token)
    if memo in _DROP_WARNED:
        return False
    _DROP_WARNED.add(memo)
    return True


def _warn_upward_drops(raw: Any, *, file_scope: str, path: Path | None) -> None:
    """Warn ONCE per ``(file, key)`` for each top-level table *raw* loses to spec §0.

    ⚑ THE ONE GUARD for every §0 drop announcement: every read of a file
    (:func:`read_settings_files`) warns through here, so a file read several times in one
    command still names each dropped key once.  THREE dropped tokens, THREE distinct
    rationales, one warning each (llm-docs).
    """
    if not isinstance(raw, dict):
        return
    drop_set = upward_scope_drop_set(file_scope)
    where = str(path) if path is not None else "<settings>"
    for token in (str(k) for k in raw if str(k) in drop_set):
        if not announce_drop_once(path, token):
            continue
        if token == "meta":
            # meta is NOT a containing scope — a DISTINCT rationale, hence its own warning.
            # ⚑ TOP-LEVEL ONLY: this loop never descends, so a nested ``<scope>.meta`` table
            # rides untouched, and the FLOOR's meta is inserted separately, never routed here.
            # ⚑⚑ NO WORKSET-SCOPE CARVE-OUT: a workset root has NO identity table — it
            # is named by the global registry's ``worksets:`` section — and its settings
            # file carries SETTINGS ONLY, so a ``meta.workset`` table here is a RETIRED
            # shape that ``refuse_retired_workset_identity`` hard-refuses upstream of
            # this warning; it gets the same treatment as any other scope, never the
            # silence a sanctioned marker used to earn.
            _log.warning(
                "Dropping top-level 'meta' table from %s settings file %s: "
                "meta.* is a read-only namespace set by the "
                "construct-time/bootstrap layer and remains RO everywhere (spec "
                "§0 meta-RO / clause 4); the key is ignored.",
                file_scope, where,
            )
        elif token == BINDING_DERIVATIONS_NODE:
            # Neither a containing scope nor meta — a THIRD rationale: the RESERVED INTERNAL
            # derivations node (R-8), machinery output, never file input. SCOPE TIGHT: this ONE
            # name; any other unknown top-level entry rides on, to be REFUSED by name at the
            # launch's §0 audit (``settings_launch._refuse_undeclared_snapshot``; llm-docs).
            # Not in the per-agent file, whose partial reads only ``self:`` and ``agent:``
            # (Q92): there ``agent_file.level_table`` REFUSES by name whatever the drops leave
            # (this one takes ``system:``, ``meta:``, ``binding_derivations:``;
            # ``settings_prefs`` takes ``pref:``), except a contained scope's table
            # (``workset:`` / ``box:``), read as defaults (Q85).
            _log.warning(
                "Dropping top-level %r table from %s settings file %s: "
                "'%s' is the RESERVED INTERNAL derivations node (R-8; manifest "
                "not_keys.reserved_internal) — machinery output regenerated at "
                "every launch, not a settable key, so it never enters the merge "
                "(spec §0). Delete the table from the file; to change a "
                "binding, change the DECLARATION it derives from (spec §0).",
                token, file_scope, where, token,
            )
        else:
            _log.warning(
                "Dropping upward-scope key %r from %s settings file %s: a file at "
                "the %s scope may not set a containing (%s) scope's keys (spec §0 "
                "directional enforcement); the key is ignored.",
                token, file_scope, where, file_scope, token,
            )


#: The ``agent`` node table's two addresses in a settings file: the scope table itself, and
#: the §2h request table ``pref.agent`` (a workset or box file).
_AGENT_NODE_TABLES: tuple[tuple[str, ...], ...] = (("agent",), (PREF_ROOT, "agent"))


def fold_agent_nodes(raw: Any, *, path: Path | None) -> Any:
    """*raw* with every ``agent.<Node>`` (and ``pref.agent.<Node>``) segment folded to its node's case.

    Q87: a user-written capital node is ACCEPTED with a loud warning naming the file and both
    spellings, once per ``(file, key)`` (:func:`announce_drop_once`); code gets no such relief,
    because the keyspace verdict does not fold. Two spellings of ONE node in one file are
    REFUSED, naming both: neither may silently win. A ``persona+harness`` node is CANONICALIZED
    SILENTLY. A table where a node's SCALAR leaf goes (:func:`_refuse_table_at_scalar_leaf`)
    is REFUSED too. Copies only what it changes.
    """
    if not isinstance(raw, dict):
        return raw
    out = raw
    for address in _AGENT_NODE_TABLES:
        table = _node_table(out, address)
        if table is None:
            continue
        folded = _fold_node_table(table, prefix=".".join(address), path=path)
        _refuse_table_at_scalar_leaf(folded, prefix=".".join(address), path=path)
        if folded is table:
            continue
        if len(address) == 1:
            out = {**out, address[0]: folded}
        else:
            out = {**out, address[0]: {**out[address[0]], address[1]: folded}}
    return out


def refuse_doubled_agent_nodes(raw: Any, *, path: Path | None) -> None:
    """RAISE when a node table of the settings document *raw* spells one node twice.

    The check :func:`fold_agent_nodes` makes (:func:`refuse_node_spelled_twice`), for a reader
    that walks the file without folding it (``get``).
    """
    if not isinstance(raw, dict):
        return
    for address in _AGENT_NODE_TABLES:
        table = _node_table(raw, address)
        if table is not None:
            refuse_node_spelled_twice(table, prefix=".".join(address), path=path)


def _node_table(raw: dict, address: tuple[str, ...]) -> dict | None:
    """The node table at *address* in *raw*, or ``None`` when no table is there."""
    parent: Any = raw
    for token in address[:-1]:
        parent = parent.get(token) if isinstance(parent, dict) else None
    table = parent.get(address[-1]) if isinstance(parent, dict) else None
    return table if isinstance(table, dict) else None


def _refuse_table_at_scalar_leaf(table: dict, *, prefix: str, path: Path | None) -> None:
    """RAISE naming every ``<prefix>.<node>.<leaf>`` holding a table where a SCALAR goes (spec §0).

    ⚑ The undeclared audit judges key NAMES, and ``model`` is one, so a table under it rode in
    silently; the leaf's SHAPE is the fault. Every node, ``default`` included: a category is a
    table in any node, a scalar leaf (:data:`SCALAR_AGENT_LEAVES`) is one in none.
    """
    found = sorted(
        f"{prefix}.{display_agent_ref(str(node))}.{leaf}"
        for node, sub in table.items() if isinstance(sub, dict)
        for leaf, value in sub.items()
        if leaf in SCALAR_AGENT_LEAVES and isinstance(value, dict)
    )
    if not found:
        return
    where = path if path is not None else "<settings>"
    named = "\n".join(f"  - {key}" for key in found)
    raise SettingsError(
        f"the settings file {where} holds a table where a single value belongs "
        f"(spec §0 — the keyspace is CLOSED):\n{named}\n"
        f"  Fix: give each key one value BY HAND in {where}, or delete it."
    )


def _canonical_node(seg: Any) -> Any:
    """The NODE *seg* names -- a user's ``persona+harness``, canonicalized; *seg* if it names none.

    ⚑ THE ORDER IS LOAD-BEARING: :func:`agent_segment_case` folds the CASE first, because
    :func:`agent_address_node` REFUSES a reserved name -- reaching for it alone would leave
    ``Default`` unread and un-warned.
    """
    if not isinstance(seg, str):
        return seg
    folded = agent_segment_case(seg)
    try:
        return agent_address_node(folded)
    except ConfigError:
        return folded


def _fold_node_table(table: dict, *, prefix: str, path: Path | None) -> dict:
    """One node table's keys folded (:func:`fold_agent_nodes`); *table* itself when none changes."""
    refuse_node_spelled_twice(table, prefix=prefix, path=path)
    where = str(path) if path is not None else "<settings>"
    spelled: dict[Any, Any] = {}
    for seg in table:
        node = _canonical_node(seg)
        spelled[node] = seg
    if all(node == seg for node, seg in spelled.items()):
        return table
    for node, seg in spelled.items():
        # ⚑ The warning keys off the CASE, and spells the node as the CLI does.
        folded = agent_segment_case(seg) if isinstance(seg, str) else seg
        if folded != seg and announce_drop_once(path, f"{prefix}.{seg}"):
            _log.warning(
                "Settings file %s spells '%s.%s', but an agent's node is lowercase (spec "
                "§0): it is read as '%s.%s' for now. Rename it in the file; kanibako "
                "accepts this spelling only with this warning.",
                where, prefix, seg, prefix, display_agent_ref(str(node)),
            )
    return {node: table[seg] for node, seg in spelled.items()}



def _drop_upward_scopes(
    raw: dict, *, file_scope: str, path: Path | None
) -> dict:
    """Return *raw* without any CONTAINING-scope top-level table, top-level ``meta:`` or top-level
    ``binding_derivations:`` (spec §0) — a shallow copy, warning-only (:func:`_warn_upward_drops`),
    never a raise.
    """
    if not isinstance(raw, dict):
        return raw
    drop_set = upward_scope_drop_set(file_scope)
    if not any(str(k) in drop_set for k in raw):
        return raw
    _warn_upward_drops(raw, file_scope=file_scope, path=path)
    return {k: v for k, v in raw.items() if str(k) not in drop_set}


#: The cascade LEVEL whose file is the per-agent ``agent.yaml``. Its contribution is
#: NOT its top-level tables — see :func:`_file_view`.
_AGENT_FILE_LEVEL: str = "agent"


def _file_view(raw: Any, *, level: str, path: Path | None, fold: bool = True) -> Any:
    """The part of a RAW settings doc at *level* that :func:`assemble_levels` actually MERGES.

    ⚑ WHY IT EXISTS. A consumer that judges a settings file has to judge what the file
    CONTRIBUTES, not what it CONTAINS. Reading the raw doc instead let the retirement scan
    prescribe a cure for an entry the cascade had already dropped: a ``box.yaml`` holding both
    an ``agent:`` table and an undeclared ``box`` key refused by naming the retired key and
    telling the user to rewrite it — for a table that directional enforcement never read —
    while the key that actually stopped the resolve went unnamed. A cure for a no-op is worse
    than no cure.

    Each dropped table is announced through :func:`announce_drop_once`, the SAME
    once-per-``(file, key)`` guard every reader warns through (spec §0: *"with a warning naming
    the file and key"*; §2h for an illegal ``pref:`` table).

    THREE rules, one per declaration: directional enforcement (spec §0) drops a CONTAINING
    scope's table, ``meta:`` and the reserved derivations node; a ``pref:`` table survives only
    where §2h permits one (:data:`~kanibako.settings.settings_prefs.PREF_LEGAL_LEVELS`); the
    per-agent file contributes its ROOT, ``agent:`` (Q92), ``workset:``, ``box:`` (Q85)
    tables only
    (:func:`~kanibako.settings.agent_file.contributed_tables`). What survives is case-folded by
    :func:`fold_agent_nodes` unless *fold* is off (the ``NARROW`` read folds at parse time).

    🛑 A TOP-LEVEL FILTER: nothing here descends.
    """
    if not isinstance(raw, dict):
        return raw
    if level not in PREF_LEGAL_LEVELS:
        raw = refuse_pref_table(raw, level=level, path=path)
    if level == _AGENT_FILE_LEVEL:
        _warn_upward_drops(raw, file_scope=level, path=path)
        view = contributed_tables(raw)
    else:
        view = _drop_upward_scopes(raw, file_scope=level, path=path)
    return fold_agent_nodes(view, path=path) if fold else view


#: The levels stage (h) audits, MOST-SPECIFIC FIRST (design 2B-ii, 2B-iii).
_H_AUDITED_LEVELS: tuple[str, ...] = ("box", "workset", "system", "base")


class ReadPurpose(Enum):
    """WHY a settings file is read — each member fixes its stages and their order (design 2A).

    ``RESOLVE``: the launch snapshot. Refuses a ``config:`` table (raw doc, non-agent files),
    then a retired behavior spelling (the view). ``SELECT``: agent selection; refuses a
    retired selection or mirror spelling (the view). ``NARROW``: assembly alone; the node
    fold and the agent file's shape run in :func:`assemble_levels`. ``DISPLAY``: plain
    ``show``; the view and its warnings, no refusal.
    ⚑ No purpose runs stage (h) (:func:`refuse_undeclared_per_file`) inside the read. A
    caller that runs it does so AFTER its own refusals, so a caller's tailored cure is the
    one a user reads (the launch runs it after its §0 audit). A set, reset or repair door
    does not run it; a bad entry outside the edited value's chain is keyspec §2a's
    set-time rule (an error unless ``--force``), not stage (h)'s.
    """

    RESOLVE = ("resolve", ("box", "workset", "agent", "system", "base"))
    SELECT = ("select", ("base", "system", "workset", "box"))
    NARROW = ("narrow", _BASE_TO_BOX_LEVELS)
    DISPLAY = ("display", _BASE_TO_BOX_LEVELS)

    def __init__(self, _name: str, order: tuple[str, ...]) -> None:
        #: The file order this read walks, one level per file. (The name keeps two members
        #: with the same order distinct: an Enum makes equal values aliases.)
        self.order = order


@dataclass(frozen=True)
class SettingsFile:
    """One settings file as read: the doc on disk (*stored*) and what the cascade merges (*view*)."""

    level: str
    path: Path | None
    stored: Any
    view: Any

    @property
    def loaded(self) -> bool:
        """True when a file is on disk here."""
        return self.path is not None and self.path.exists()


def read_settings_files(
    files: Iterable[tuple[str, Path | None]],
    *,
    purpose: ReadPurpose,
    subject: str | None = None,
    box_name: str | None = None,
) -> tuple[SettingsFile, ...]:
    """Read *files* (``(level, path)`` pairs) for *purpose*, in its file order.

    *subject* (the agent node) and *box_name* only fill the cures a retired spelling's
    refusal prints. Each phase runs over every file before the next phase starts.
    """
    by_level = {level: Path(path) if path is not None else None for level, path in files}
    levels = [level for level in purpose.order if level in by_level]
    stored: dict[str, Any] = {}
    if purpose is ReadPurpose.RESOLVE:
        # P1: load + the ``config:`` table (spec §1). The agent file is not asked here:
        # ``agent_file.level_table`` refuses a top-level ``config:`` there as a stray.
        for level in levels:
            if level != _AGENT_FILE_LEVEL:
                stored[level] = load_doc(by_level[level])
                if stored[level]:
                    refuse_config_table(stored[level], level=level, path=by_level[level])
    views: dict[str, Any] = {}
    for level in levels:
        path = by_level[level]
        if level not in stored:
            stored[level] = load_doc(path)
        if purpose is ReadPurpose.NARROW:
            continue
        if not (path is not None and path.exists()):
            views[level] = {}
            continue
        views[level] = _file_view(stored[level], level=level, path=path)
        if purpose is ReadPurpose.RESOLVE:
            refuse_retired_behavior_keys(
                views[level], level=level, path=path, subject=subject,
                box_name=box_name if level == "box" else None,
            )
        elif purpose is ReadPurpose.SELECT:
            refuse_retired_keys(
                views[level], level=level, path=path,
                box_name=box_name if level == "box" else None,
            )
    if purpose is ReadPurpose.NARROW:
        # ``assemble_levels``' own phases: every ``pref:`` drop, then every upward drop.
        for level in levels:
            if level not in PREF_LEGAL_LEVELS:
                refuse_pref_table(stored[level], level=level, path=by_level[level])
        for level in levels:
            _warn_upward_drops(stored[level], file_scope=level, path=by_level[level])
        views = {
            level: _file_view(stored[level], level=level, path=by_level[level], fold=False)
            for level in levels
        }
    return tuple(
        SettingsFile(level=level, path=by_level[level], stored=stored[level], view=views[level])
        for level in levels
    )


def refuse_undeclared_per_file(files: Iterable[SettingsFile]) -> None:
    """RAISE for the first (most-specific) file whose OWN view carries an undeclared entry.

    Stage (h)'s carrier, of ALREADY-READ files so each caller can place it after its own
    refusals (:class:`ReadPurpose`). ⚑ The walk is MOST-SPECIFIC FIRST
    (:data:`_H_AUDITED_LEVELS`), NOT a read's order: ``NARROW`` reads base→box, so its
    order would name the WORKSET file when both files carry one.
    """
    by_level = {f.level: f for f in files}
    for level in _H_AUDITED_LEVELS:
        f = by_level.get(level)
        if f is not None:
            refuse_undeclared_entries(f.view, level=level, path=f.path, stored=f.stored)


def refuse_undeclared_entries(
    view: Any, *, level: str, path: Path | None, stored: Any = None,
) -> None:
    """RAISE when *view* carries an undeclared entry, naming EVERY such key and *path*.

    Stage (h) — the per-file §0 audit for every :data:`_H_AUDITED_LEVELS` file. Each file's own
    view is judged (after its upward-scope drops), so a lower file's undeclared key is
    caught even when a higher file supplies that table; the refusal names the file.

    ⚑ ORDER, load-bearing: :class:`ReadPurpose` says why a caller's cure answers first —
    and :func:`retired_cure` runs before the generic message for the same reason.
    ``DISPLAY`` never reaches it: ``show`` still LISTS undeclared (Q5).
    """
    if not isinstance(view, dict):
        return
    found = undeclared_store_paths(view, oracle=keyspace_verdict)
    if not found:
        return
    # ⚑ retired_cure FIRST: a retired spelling is the more specific fault.
    retired_cure((SettingsFile(level=level, path=path, stored=stored, view=view),))
    named, entries, them = undeclared_listing(found)
    where = path if path is not None else "<settings>"
    raise SettingsError(
        f"the {level} settings file {where} carries {entries} "
        f"(spec §0 — the keyspace is CLOSED):\n"
        f"{named}\n"
        f"  Fix: remove {them} BY HAND from {where}; a per-key reset cannot remove "
        f"what is not a key."
    )


def undeclared_listing(
    findings: Sequence[tuple[tuple[str, ...], Judgment]],
) -> tuple[str, str, str]:
    """``(listing, count phrase, pronoun)`` for *findings* (``undeclared_store_paths``).

    The one listing stage (h) and the launch's §0 refusal both print (P10).
    """
    named = "\n".join(
        f"  - {display_store_path(segments, judgment.key_len)}: {judgment.note}"
        for segments, judgment in findings
    )
    count = len(findings)
    entries = (
        "1 entry that is not a settings key" if count == 1
        else f"{count} entries that are not settings keys"
    )
    return named, entries, "it" if count == 1 else "them"


def retired_cure(files: Iterable[SettingsFile]) -> None:
    """RAISE the TAILORED refusal when a read file's view still carries a RETIRED spelling.

    Called only once §0 has decided to refuse, to pick WHICH refusal the user reads: the
    retired key is refused either way. *files* are most-specific first; nothing is re-read.
    """
    for f in files:
        if f.loaded:
            refuse_retired_keys(f.view, level=f.level, path=f.path)
            refuse_retired_behavior_keys(f.view, level=f.level, path=f.path)


def _refuse_malformed_category(parts: tuple[str, ...], sub: Any) -> None:
    """RAISE on a non-``None`` non-map where a TABLE belongs, or pass.

    ⚑ THE POSITION IS THE DISCRIMINATOR, and this walk is the one place it cannot be
    assumed: a deep walk reaches ``system.channels.common`` — a path SCALAR that merely
    ENDS in a category token, while its family's ``system.channels.chat`` does not.
    :func:`~kanibako.settings.settings_keyspace.is_terminal_category_key` reads that
    position, so it judges and the message names the WHOLE key.

    ⚑ ONE JUDGE FOR EVERY TIER, the agent file included: a launch is refused at the FILE
    that holds the value, and the file it names is the one to edit whatever tier that file
    is. The DISPLAY verbs are not this walk — ``agent_record`` coerces a wrong shape so a
    broken file stays openable to repair (``settings_resolve._check_node_binds``).
    """
    key = ".".join(parts)
    if is_terminal_category_key(key) or _is_table_valued_agent_leaf(parts):
        refuse_scalar_at_table_key(key, sub)


def _is_table_valued_agent_leaf(parts: tuple[str, ...]) -> bool:
    """Does *parts* end AT a declared table-valued agent leaf, ``agent.<node>.<leaf>``?"""
    # ⚑ A node is ONE segment (``agent_ref.parse_agent_ref`` refuses a dotted one), so the
    # same word inside a family's entries or inside the table's own payload is DATA.
    return len(parts) == 3 and parts[0] == "agent" and parts[2] in TABLE_VALUED_AGENT_LEAVES


def _under_pref(parts: tuple[str, ...]) -> bool:
    """Is *parts* a key path under a ``pref.`` head? (§2h)"""
    return parts[:1] == (PREF_ROOT,)


def _pref_agent_segment(parts: tuple[str, ...]) -> str | None:
    """The agent segment under ``pref.agent`` (ONE segment, §2d, so index 2), else ``None``."""
    if parts[:2] != (PREF_ROOT, "agent") or len(parts) < 3:
        return None
    return parts[2]


def _is_bare_scalar_entry(value: Any) -> bool:
    """Is *value* a bind-map entry that is a BARE SCALAR?

    ⚑ A list is a structured-entry ATTEMPT (its arity is judgeable at the parse) and a table
    is the retired SPELLING; a bare scalar is neither, and is the one verdict §2h's agent
    judgment must precede. ``None`` is the per-entry OMIT.
    """
    return value is not None and not isinstance(value, (list, tuple, dict))


def _at_declared_category(parts: tuple[str, ...]) -> bool:
    """Is *parts* a DECLARED dest-keyed category position, a ``pref.`` head stripped (spec §2h)?

    A pref target counts only when it is requestable, so §2h's allowlist refusal names it.
    """
    if _under_pref(parts):
        parts = parts[1:]
        if pref_allowlist_entry(".".join(parts)) is None:
            return False
    return is_terminal_category_key(".".join(parts))


def _parse_node(
    value: Any, *, in_binds: bool, dest_keyed: bool = False, at_bindings: bool = False,
    path: tuple[str, ...] = (), for_pref_requests: bool = False,
) -> Any:
    """Recursively coerce a raw settings node into the ``StoreValue`` space.

    ⚑⚑ *dest_keyed* selects WHICH bind shape a leaf has and is the ONLY thing that decides it
    (R-3/R-6): both shapes admit a 2-element list with OPPOSITE meanings, so the choice is made by
    this CONTEXT FLAG — passed down from the caller that knows the node — and NEVER by the leaf's
    arity. *in_binds* / *at_bindings* and the depth rule they encode: llm-docs.

    *path* is the KEY PATH walked so far, doc-root first, and exists for ONE reason: it carries the
    SCOPE to :func:`_declaration_root_ref`, so an abstract category's bare leaf is rooted HERE
    (spec §2a) instead of downstream. A caller whose document does not START at a scope token seeds
    it — :func:`_agent_partial` does, because ``self:`` IS ``agent.<node>``.
    """
    if isinstance(value, dict):
        store = KeyStore()
        for key, sub in value.items():
            key_s = str(key)
            if at_bindings and key_s in _BIND_ARMS:
                # An ARM (``bindings.ro`` / ``.rw``) — a TERMINAL dest-keyed map (R-5).
                if isinstance(sub, dict):
                    store[key_s] = parse_bind_map(
                        sub, category=f"{_DEST_KEYED_CATEGORY}.{key_s}",
                        declared=_at_declared_category((*path, key_s)),
                        defer_shape=for_pref_requests and _under_pref(path),
                        pref_agent=_pref_agent_segment(path),
                    )
                    continue
                _refuse_malformed_category((*path, key_s), sub)
            if not in_binds and key_s in BIND_LEAF_CATEGORIES:
                # A TERMINAL dest-keyed category — the map is HERE, not one level down, so it is
                # parsed on the way PAST the category token. Same malformed-shape hand-off as an arm.
                # ⚑ ``not in_binds`` keeps a user's entry literally NAMED ``common`` inside another
                # category from being re-read as one — the guard ``at_bindings`` carries below.
                if isinstance(sub, dict):
                    store[key_s] = parse_bind_map(
                        sub, category=key_s,
                        root_ref=_declaration_root_ref(path, key_s),
                        declared=_at_declared_category((*path, key_s)),
                        defer_shape=for_pref_requests and _under_pref(path),
                        pref_agent=_pref_agent_segment(path),
                    )
                    continue
                _refuse_malformed_category((*path, key_s), sub)
            if not in_binds and key_s in _MARKER_LEAF_CATEGORIES:
                if isinstance(sub, dict):
                    store[key_s] = _parse_marker_map(sub, path=(*path, key_s))
                    continue
                _refuse_malformed_category((*path, key_s), sub)
            if not in_binds and _is_table_valued_agent_leaf((*path, key_s)):
                # A table-valued agent LEAF, whole — spec §2d, no §2a category involved,
                # so the branches above cannot reach it. Same rule, same wording.
                _refuse_malformed_category((*path, key_s), sub)
            # Entering a bind-shaped category: its entries below are binds.
            descend_binds = in_binds or key_s in BIND_CATEGORY_TOKENS
            store[key_s] = _parse_node(
                sub,
                in_binds=descend_binds,
                dest_keyed=dest_keyed,
                for_pref_requests=for_pref_requests,
                at_bindings=(not in_binds and key_s == _DEST_KEYED_CATEGORY),
                path=(*path, key_s),
            )
        return store
    if in_binds and isinstance(value, (list, tuple)):
        # A structured bind leaf — refs left RAW (S9); a malformed arity raises SettingsError.
        # ⚑ No bind-shaped category reaches the name-keyed branch any more; it survives for a
        # MALFORMED node handed back by :func:`parse_bind_map`.
        if dest_keyed:
            src, entry_opts = unpack_bind_entry(value)
            return BindEntry(src, entry_opts)
        host, box, opts = unpack_bind(value)
        return Bind(host, box, opts)
    # Scalar / None / genuine list[str] — stored verbatim (a list is not descended).
    return value


def _parse_marker_map(raw: dict, *, path: tuple[str, ...]) -> KeyStore:
    """Parse a dest-keyed MARKER map (``masks``): canonicalize each dest, keep each value as is."""
    # ⚑ A marker value is a 3-state marker, not a bind entry, so only the PER-MAP check applies.
    refuse_dest_spelled_twice(raw, category=path[-1])
    store = KeyStore()
    for key, sub in raw.items():
        dest = normalize_bind_dest(str(key))
        store[dest] = _parse_node(sub, in_binds=False, path=(*path, dest))
    return store


def parse_bind_map(
    raw: Any, *, category: str = "bindings", root_ref: str | None = None,
    declared: bool = True, defer_shape: bool = False,
    pref_agent: str | None = None,
) -> KeyStore:
    """Parse a raw DEST-KEYED category map into a :class:`KeyStore` of :class:`BindEntry`.

    *raw* is the ``{box_dest: [src[, opts]]}`` mapping at ANY terminal bind-shaped key — a
    ``bindings`` arm or one of the four whose token is the whole key. ONE parser, not two.
    Returns a nested node (not an opaque dict leaf) so it merges PER-ENTRY across levels; a
    ``None`` entry is preserved VERBATIM. llm-docs.

    ⚑⚑ THIS IS THE DECLARATION-LOAD SEAM: what gets STORED must resolve on its own, so the
    root is supplied HERE (:func:`_declared_source`) and never downstream — rooting at
    ASSEMBLY is FORBIDDEN by §2a.

    ⚑ *defer_shape* withholds ONLY the bare-scalar entry verdict, carrying that value for
    :func:`~kanibako.settings.settings_prefs.refuse_deferred_pref_shapes`. Every other check
    still runs here, on the WHOLE map.

    ⚑ *pref_agent* is the agent segment this map sits under; BOTH parses pass it, so it is
    judged before any shape (:func:`~kanibako.settings.settings_prefs.agent_segment_reason`).
    """
    if not isinstance(raw, dict):
        raise SettingsError(
            f"A dest-keyed {category!r} map must be a mapping "
            f"{{box_dest: [src[, options]]}}, got {type(raw).__name__}: {raw!r}."
        )
    if not defer_shape:
        check_bind_map(raw, category=category, declared=declared, pref_agent=pref_agent)
    else:
        # ⚑ NOT A SHAPE VERDICT, so it runs on the WHOLE map: a destination spelled twice
        # is a fact about the map, and the sub-map cannot see one straddling the carve-out.
        refuse_dest_spelled_twice(raw, category=category)
        # ⚑⚑ *pref_agent* TOO: this call still judges shapes AT PARSE TIME, and Q2 puts the
        # name ahead of every shape check.
        check_bind_map(
            {k: v for k, v in raw.items() if not _is_bare_scalar_entry(v)},
            category=category, declared=declared, pref_agent=pref_agent,
        )
    store = KeyStore()
    for key, sub in raw.items():
        # ⚑ THE ONE PLACE A STORED DEST IS CANONICALIZED ON READ (R-11) — ``~`` and ``~/`` must be
        # ONE entry. Producers normalize too; the function is idempotent, so neither place is
        # load-bearing alone. ⚑ The VALUE is never canonicalized: a host_src stays as authored.
        dest = normalize_bind_dest(str(key))
        if defer_shape and _is_bare_scalar_entry(sub):
            # ⚑ Carried VERBATIM and never unpacked: a ``BindEntry`` is what SAYS an
            # entry is well-formed, so deferring the shape defers that proof.
            store[dest] = sub
            continue
        entry = _parse_node(sub, in_binds=True, dest_keyed=True)
        if isinstance(entry, BindEntry):
            entry = BindEntry(
                _declared_source(entry.src, category, dest, root_ref), entry.opts,
            )
        store[dest] = entry
    return store


def _declared_source(
    src: str, category: str, dest: str, root_ref: str | None,
) -> str:
    """The §2a-conforming ``host_src`` to STORE for one entry.

    ⚑ A CONCRETE category takes no root at any scope, so its bare-relative source is
    :func:`~kanibako.settings.settings_resolve.refuse_unrooted_source`'s to refuse.
    """
    if root_ref is not None:
        return root_relative_source(src, root_ref)
    refuse_unrooted_source(src, category, dest)
    return src


def _parse_naming_file(
    raw: dict, *, file_path: Path | None, key_path: tuple[str, ...] = (),
    for_pref_requests: bool = False,
) -> KeyStore:
    """Parse one settings file's node, NAMING *file_path* in every refusal the parse raises.

    ⚑ ONE wrap covers EVERY defect under it — the reserved name from ``KeyStore.__setitem__``, the
    §2a retired shape and the bare-relative and arity refusals from :func:`parse_bind_map` — so
    nothing below has to learn what a file is, and no refusal added later has to be enrolled.
    ⚑⚑ THE MESSAGE IS KEPT VERBATIM AND THE FILE APPENDED, never re-worded: the KEY must stay the
    first thing the user reads on ``cli.main``'s ``Error: {e}`` line.
    ⚑ Re-raised as ``SettingsError`` per both callers' contract; ``ReservedKeyError``'s ``KeyError``
    base is read by the ``config set`` probe, not by anything on this path.
    ⚑ SHARED by :func:`_file_partial` and :func:`_agent_partial` — the file tiers and the agent
    tier walk the SAME parse, so they must name the file the same way; *key_path* is the only
    difference between them (the agent file's walk starts one scope in).
    """
    try:
        parsed = _parse_node(
            raw, in_binds=False, path=key_path, for_pref_requests=for_pref_requests,
        )
    except (ReservedKeyError, SettingsError) as exc:
        where = str(file_path) if file_path is not None else "<settings>"
        raise SettingsError(f"{exc} (in settings file {where})") from exc
    assert isinstance(parsed, KeyStore)
    return parsed


def _file_partial(
    raw: dict, *, path: Path | None = None, for_pref_requests: bool = False,
) -> KeyStore:
    """Build ONE level partial from a settings file's WHOLE nested content, SCOPE TOKEN KEPT (§0).

    The rule for every NON-agent level (``base`` / ``system`` / ``workset`` / ``box``); the agent
    tier uses :func:`_agent_partial`. ⚑ The bind DEPTH is not chosen here — :func:`_parse_node`
    derives it from the CATEGORY token it walks past, so there is no flag for a caller to get wrong.

    ⚑ *path* EXISTS TO NAME THE FILE IN EVERY REFUSAL THIS PARSE RAISES — the same argument, with
    the same rendering, that the siblings :func:`_drop_upward_scopes` and
    :func:`~kanibako.settings.settings_prefs.refuse_pref_table` already take. Without it a reserved
    leaf name (``box: get:``) and the RETIRED name-keyed §2a shape both named the offending KEY and
    left the user to work out WHICH of six cascade files to edit — the key is the defect, but the
    file is the address, and a cure with no address is a cure the user has to hunt for.
    ⚑ *for_pref_requests* marks the ONE reader whose consumer judges the agent segment
    (:func:`~kanibako.settings.settings_prefs.apply_prefs`), so only there is a bare-scalar
    entry's verdict deferred. The CASCADE's own read of the same file has no such consumer
    and keeps the verdict here: a value installed at a target is read AT that target (§2h).

    ⚑ NO LIVE CALLER OMITS IT ANY MORE. It stayed optional for the one that parsed a SYNTHESIZED
    table — ``collect_prefs``' ``{pref: …}`` wrapper — but that table is still read OFF a real
    workset or box file, and that file is what its refusals must name, so it passes the path too.
    The ``<settings>`` rendering a ``None`` still gives (exactly as the siblings give it) is now a
    fallback, not a caller's option.
    """
    if not isinstance(raw, dict):
        return KeyStore()
    return _parse_naming_file(
        fold_agent_nodes(raw, path=path), file_path=path,
        for_pref_requests=for_pref_requests,
    )


def _agent_partial(
    raw: dict, *, sub_key: str, path: Path | None = None, node: str | None = None
) -> KeyStore:
    """Build an AGENT-tier level partial (``agent.default`` or ``agent.<active>``) from the agent
    file, re-rooted under its TRUE discriminated name ``agent.<sub_key>``.

    ⚑ THE SEAM: :func:`~kanibako.settings.agent_file.level_table` owns the file's SHAPE (which
    tables a level reads, the flat-category re-root, and the two refusals that precede it — the
    nested ``self:`` and the stray top-level key) and hands back a RAW table; this function owns
    the STORE coercion and the §2d wrap. The split is what keeps the boundary free of
    ``KeyStore`` — and the import edge one-way.

    *sub_key* selects the TIER; the two agent levels are kept SEPARATE (spec §2) and merge by
    their true §2d names — NO bare-``agent`` collapse. *path* NAMES THE FILE in every refusal
    this level raises — the boundary's AND this function's own parse (:func:`_parse_naming_file`,
    the same wrap the file tiers get); *node* renders the boundary's message alone. Neither is
    read as a VALUE. llm-docs.

    ⚑ THE FILE'S ``agent:`` TABLE IS READ (Q92) — parsed by :func:`_file_partial`, the scope
    files' own builder, so it folds and parses exactly as that table does in a workset or system
    file. The all-agents tier takes its ``agent.default`` node; the active tier takes every other
    node and merges them beside the re-rooted ``self:`` (:func:`_scope_nodes`). So in foo's file,
    ``agent: {bar: …}`` MERGES but is never picked while foo is active (bar's own file is the one
    read when bar is), and ``agent: {default: …}`` sets ``agent.default.*`` at this file's level,
    which applies only while foo is. ``self:`` beside ``agent: <own node>:`` setting one leaf
    has already REFUSED at the boundary (Q103, ``agent_file._refuse_two_spellings``).
    """
    level = level_table(raw, sub_key=sub_key, node=node, path=path)
    for leaf, leaf_value in level.state.items():
        # ⚑ THE ONE TABLE-VALUED LEAF THE SPLIT HIDES: ``transform_settings`` is a §2d leaf,
        # not a category root, so :func:`~kanibako.settings.agent_file.level_table` splits it into
        # *state* and no parse below ever sees it. Without this the launch dropped it in silence.
        # The all-agents tier's *state* is empty (``self:`` is ``agent.<node>``), so this is the
        # active level's own leaves and only the launch reaches it — ``agent_record`` coerces.
        if leaf in TABLE_VALUED_AGENT_LEAVES and leaf_value is not None \
                and not isinstance(leaf_value, dict):
            refuse_scalar_at_table_key(
                f"agent.{level.node}.{leaf}", leaf_value,
                where=str(path) if path is not None else None,
            )
    scope = _scope_nodes(level.scope, sub_key=sub_key, path=path)
    store = _file_partial(level.contained, path=path)
    if not level.table and not scope:
        return store
    agent_node = KeyStore()
    store["agent"] = agent_node
    if level.table:
        # The discriminator (``default`` / the active agent's name) is the §2d key form and is
        # load-bearing: it keeps the fallback layer and any per-agent override distinct under the
        # merge.
        # ⚑ The KEY path is SEEDED, unlike every other level's: this document's root table IS
        # ``agent.<node>`` ([spec:15-21, "self"]), so the walk starts one scope in and the §2a
        # DECLARATION ROOT would otherwise have no scope to read. It is the ONLY thing this call
        # does differently from a file tier's — the file naming is the shared wrap's.
        agent_node[level.node] = _parse_naming_file(
            level.table, file_path=path, key_path=("agent", level.node),
        )
    if scope:
        # ⚑ WHAT THE BOUNDARY GUARANTEES, AND ALL IT GUARANTEES: before this overlay
        # ``agent_node`` holds at most the own node's ``self:`` categories, and
        # ``agent_file._contribution`` has refused an own-node entry that is not a table
        # (``_refuse_node_values``) and any own-node setting that equals, or is a prefix of or
        # under, one ``self:`` sets (``_refuse_two_spellings``, Q103). So nothing ``self:`` set is
        # replaced here; the other nodes land where nothing was.
        _overlay(agent_node, scope)
    return store


def _scope_nodes(scope: dict, *, sub_key: str, path: Path | None) -> KeyStore:
    """The node tables of the agent file's ``agent:`` table that the *sub_key* tier merges.

    Parsed as the scope files parse it (:func:`_file_partial`: node fold, bind parse, file named
    in every refusal). The all-agents tier takes the ``default`` node; the active tier the rest.
    """
    if not scope:
        return KeyStore()
    nodes = _file_partial({FILE_SCOPE: scope}, path=path).get(FILE_SCOPE)
    picked = KeyStore()
    if not isinstance(nodes, KeyStore):
        return picked
    for seg in dict.keys(nodes):
        if (seg == _AGENT_DEFAULT_SUB) == (sub_key == _AGENT_DEFAULT_SUB):
            picked[seg] = dict.__getitem__(nodes, seg)
    return picked


def dotted_partial(floor: dict[str, object] | None) -> KeyStore:
    """Build a merge LEVEL from the caller's flat ``{dotted key: value}`` declared-default *floor*,
    EXPLODED to the nested keyspace (S7) so it merges uniformly with the other partials.

    ⚑⚑ DO NOT PUT A CATEGORY-ENTRY SPELLING IN THIS DOCSTRING AS AN EXAMPLE — :func:`_insert_dotted`
    REFUSES BY NAME every floor key deeper than the terminal category key, and two examples have
    already been burned here (llm-docs). A behavior key such as ``"agent.access"`` is safe.
    """
    store = KeyStore()
    if not floor:
        return store
    for raw_key, raw_val in floor.items():
        _insert_dotted(store, str(raw_key), raw_val)
    return store


def _insert_dotted(store: KeyStore, dotted: str, value: Any) -> None:
    """Insert *value* at the dotted-path *dotted*, exploding to nested :class:`KeyStore` nodes (S7)
    and parsing the terminal leaf.

    ⚑ EVERY bind-shaped category is the exception (R-5/R-6): a floor key ENDS at the terminal key
    and its value is a whole dest-keyed ``BindMap``. A key that goes DEEPER is the retired
    name-keyed producer shape and is REFUSED here, loudly and by name. Why that matters: llm-docs.
    """
    parts = dotted.split(".")
    # A leaf is bind-shaped iff any ancestor segment is a bind category.
    in_binds = any(p in BIND_CATEGORY_TOKENS for p in parts[:-1])
    at_arm = len(parts) >= 2 and parts[-2] == _DEST_KEYED_CATEGORY
    if in_binds and _DEST_KEYED_CATEGORY in parts[:-1] and not at_arm:
        terminal_key = ".".join(parts[: parts.index(_DEST_KEYED_CATEGORY) + 2])
        raise SettingsError(
            f"Default-category key {dotted!r} names a binding by ENTRY NAME, "
            f"which is the RETIRED shape: a bindings arm is a TERMINAL key "
            f"({terminal_key}) whose value is the whole map "
            f"{{box_dest: [src[, options]]}} (spec §2a, R-5/R-10 — the entry "
            f"name was dropped 2026-08-06c). Emit the arm, not the entry."
        )
    deeper = [p for p in parts[:-1] if p in BIND_LEAF_CATEGORIES]
    if deeper:
        category = deeper[0]
        terminal_key = ".".join(parts[: parts.index(category) + 1])
        raise SettingsError(
            f"Default-category key {dotted!r} names a {category!r} entry by "
            f"ENTRY NAME, which is the RETIRED shape: {category!r} is a TERMINAL "
            f"key ({terminal_key}) whose value is the whole map "
            f"{{box_dest: [src[, options]]}} (spec §2a — the entry name was "
            f"dropped 2026-08-08c). Emit the category, not the entry."
        )
    node: KeyStore = store
    for part in parts[:-1]:
        # ⚑ UNBOUND dict.get (S3): never the bound ``node.get`` — a leaf named ``get`` would shadow
        # the method into a crash. Uniform even though these stores are module-built.
        existing = dict.get(node, part)
        if not isinstance(existing, KeyStore):
            existing = KeyStore()
            node[part] = existing
        node = existing
    if at_arm and parts[-1] in _BIND_ARMS and isinstance(value, dict):
        node[parts[-1]] = parse_bind_map(
            value, category=f"{_DEST_KEYED_CATEGORY}.{parts[-1]}",
        )
    elif parts[-1] in BIND_LEAF_CATEGORIES and isinstance(value, dict):
        # ⚑ SAME §2a rule as a settings file's own walk: a floor key names its scope in
        # its own segments, so the DECLARATION ROOT is read from them rather than left
        # unsupplied. A producer that already rooted (``agent_defaults.load_common``)
        # emits a self-resolving source, which is stored verbatim.
        node[parts[-1]] = parse_bind_map(
            value, category=parts[-1],
            root_ref=_declaration_root_ref(tuple(parts[:-1]), parts[-1]),
        )
    elif parts[-1] in _MARKER_LEAF_CATEGORIES and isinstance(value, dict):
        node[parts[-1]] = _parse_marker_map(value, path=tuple(parts))
    else:
        node[parts[-1]] = _parse_node(value, in_binds=in_binds)


def assemble_levels(
    *,
    agent_name: str,
    files: Iterable[SettingsFile],
    floor: dict[str, object] | None = None,
) -> list[KeyStore]:
    """Build each cascade scope's ONE nested ``KeyStore`` partial from the read *files* and return
    the six MOST-SPECIFIC-FIRST (S8): ``[box, workset, agent.<active>, agent.default, system, base]``.

    *files* come from :func:`read_settings_files`, with the purpose the caller chose; a level with
    no file there contributes an empty partial. *floor* (declared defaults + default-categories)
    folds UNDER the base file into the ``base`` level and is the SOLE sanctioned ``meta.*``
    source. NO ``machine`` path is consulted (S14). Per-parameter detail: llm-docs.
    """
    by_level = {f.level: f for f in files}

    def _view(level: str) -> Any:
        f = by_level.get(level)
        return f.view if f is not None else {}

    def _path(level: str) -> Path | None:
        f = by_level.get(level)
        return f.path if f is not None else None

    # The agent file's SHAPE checks (``agent_file.level_table``: a stray root, a nested
    # ``self:``) judge the file minus its dropped tables, not the contributed view, so a stray
    # still refuses by name.
    raw_agent = _agent_shape_input(by_level.get(_AGENT_FILE_LEVEL))
    agent_path = _path(_AGENT_FILE_LEVEL)

    # The floor is inserted FIRST and the base-file leaves overlay it, so a base-file entry wins
    # WITHIN this single level and the floor is the ultimate fallback.
    base_partial = dotted_partial(floor)
    _overlay(base_partial, _file_partial(_view("base"), path=_path("base")))

    # MOST-SPECIFIC-FIRST (S8). Each scope file's partial keeps its scope token so the merge works
    # by scope-qualified name; the agent tier keeps its §2d discriminator — NO bare-``agent``
    # collapse.
    return [
        _file_partial(_view("box"), path=_path("box")),
        _file_partial(_view("workset"), path=_path("workset")),
        _agent_partial(
            raw_agent, sub_key=agent_name, path=agent_path, node=agent_name,
        ),
        # ⚑⚑ THIS LEVEL HOLDS ONLY THE FILE'S ``agent: default:`` TABLE (Q92). ``self:`` has
        # no spelling for the all-agents tier (``self:`` IS ``agent.<node>``, so a ``default``
        # sub-table under it reads ``agent.<node>.default.*`` and REFUSES); the ``agent:``
        # table does, read as in any settings file, and applies only while this agent is
        # active, since only then is this file read.
        _agent_partial(
            raw_agent, sub_key=_AGENT_DEFAULT_SUB, path=agent_path, node=agent_name,
        ),
        _file_partial(_view("system"), path=_path("system")),
        base_partial,
    ]


def _agent_shape_input(agent_file: SettingsFile | None) -> Any:
    """What ``agent_file.level_table`` judges: the stored agent file minus its dropped tables."""
    if agent_file is None or not isinstance(agent_file.stored, dict):
        return {}
    drop_set = cascade_drop_set(_AGENT_FILE_LEVEL)
    return {k: v for k, v in agent_file.stored.items() if str(k) not in drop_set}


def agent_record(path: Path, *, node: str, purpose: ReadPurpose) -> AgentConfig:
    """Read agent *node*'s settings file at *path* for *purpose* and return its record.

    THE ONE READER of the agent file (design 2C): one :func:`read_settings_files` read, the
    file's shape judged by ``agent_file.level_table`` — the same view the launch's
    :func:`_agent_partial` takes — and the record built from that level
    (``agent_file.record``, which refuses every undeclared entry; its ``workset:`` / ``box:``
    tables are audited as the scope files' are). Every reader — the launch,
    ``agent show`` / ``info`` / ``list`` / ``get`` — takes the record from here, so one file
    gets one verdict. The repair door (``agent_file.clear_overrides``) never calls this.
    Returns an empty record if the file does not exist.

    *node* is the agent whose file this is; a store folder named after the file's alias
    (``self``) is refused, and so is a present folder named for the reserved all-agents tier
    (``default``, spec §2d), whose settings live in the system file. Two questions: whether a
    name is a legal KEY SEGMENT (``default`` is), and whether a folder is that agent's STORE.
    ``config_dest._missing_store_error`` answers the second for an ABSENT store.
    """
    from kanibako.settings.config_dest import _reserved_tier_store_sentence
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB
    from kanibako.settings.settings_keyspace import file_alias_reason

    alias = file_alias_reason(node)
    if alias is not None:
        raise SettingsError(
            f"{path.parent} is not an agent store: {alias}.\n"
            f"  Fix: rename the folder to the agent's name, or move it out of "
            f"{path.parent.parent}."
        )
    if not path.exists():
        return AgentConfig()
    if node == AGENT_DEFAULT_SUB:
        raise SettingsError(
            f"{path.parent} is not an agent store: {_reserved_tier_store_sentence()}.\n"
            f"  Fix: move {path.parent} out of {path.parent.parent}, or delete it -- the "
            f"tier's settings live in the system file's 'agent: {AGENT_DEFAULT_SUB}:' "
            f"table, never in a folder."
        )
    (read,) = read_settings_files(
        ((_AGENT_FILE_LEVEL, path),), purpose=purpose, subject=node,
    )
    level = level_table(_agent_shape_input(read), sub_key=node, node=node, path=path)
    cfg = record(level, node=node)
    refuse_undeclared_entries(level.contained, level=_AGENT_FILE_LEVEL, path=path)
    return cfg


def cascade_files(
    *,
    purpose: ReadPurpose,
    system_path: Path | None,
    agent_path: Path | None,
    workset_path: Path | None,
    box_path: Path | None,
    base_path: Path | None = None,
    subject: str | None = None,
    box_name: str | None = None,
) -> tuple[SettingsFile, ...]:
    """The five cascade files read for *purpose*; *base_path* defaults to the site base file."""
    return read_settings_files(
        (
            ("box", box_path),
            ("workset", workset_path),
            ("agent", agent_path),
            ("system", system_path),
            ("base", base_path if base_path is not None else settings_base_path()),
        ),
        purpose=purpose, subject=subject, box_name=box_name,
    )


def _overlay(base: KeyStore, top: KeyStore) -> None:
    """Deep-overlay *top*'s leaves onto *base*, in place — the same-level combine that layers a
    base-FILE partial over the declared-default floor.

    ⚑ NOT the cascade merge: a same-level union of two SOURCES. Matching subtrees descend so a deep
    file leaf does not clobber sibling floor leaves; any other leaf replaces wholesale. Unbound
    ``dict`` ops (S3).
    """
    for key in dict.keys(top):
        top_val = dict.__getitem__(top, key)
        base_val = dict.get(base, key)
        if isinstance(top_val, KeyStore) and isinstance(base_val, KeyStore):
            _overlay(base_val, top_val)
        else:
            dict.__setitem__(base, key, top_val)

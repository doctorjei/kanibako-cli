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
from pathlib import Path
from typing import Any

from kanibako.settings.agent_file import ROOT_SECTIONS, level_table
from kanibako.settings.config import settings_base_path
from kanibako.settings.config_io import load_doc
from kanibako.settings.kb_store import (
    BINDING_DERIVATIONS_NODE,
    SCOPE_CONTAINMENT,
    Bind,
    BindEntry,
)
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_prefs import refuse_pref_table
from kanibako.settings.settings_resolve import (
    SettingsError,
    unpack_bind,
    unpack_bind_entry,
)

_log = logging.getLogger(__name__)

# The bind-shaped category tokens; every one holds dest-keyed ``BindMap``(s). ``masks`` (a keyed
# 3-state, S5) and the scalar ``env`` / ``secret_path`` families keep their natural nested shape and
# are NOT bind-parsed. ``bindings`` carries the ``ro`` / ``rw`` sub-tables, each holding a map.
_BIND_CATEGORIES: frozenset[str] = frozenset(
    {"bindings", "caches", "seeded", "common", "synced"}
)

# ⚑ The ARMED bind-shaped category: ``bindings`` is the one whose category token
# is NOT the whole key — its two ARMS are (``bindings.ro`` / ``bindings.rw``), and
# each arm holds a ``BindMap``.
_DEST_KEYED_CATEGORY = "bindings"
#: The two arms a dest-keyed ``bindings`` node carries; each holds a ``BindMap``.
_BIND_ARMS: tuple[str, str] = ("ro", "rw")
#: The bind-shaped categories whose CATEGORY TOKEN IS THE WHOLE KEY — terminal ONE
#: LEVEL SHALLOWER than a ``bindings`` arm, with a ``BindMap`` for a value.
#: ⚑⚑ This set says WHERE the map sits, not WHETHER there is one: read at the wrong
#: depth, a destination is taken for an arm name. Why a blanket ``dest_keyed=True``
#: cannot replace it (the 2-element arity trap): llm-docs.
_DEST_KEYED_LEAF_CATEGORIES: frozenset[str] = frozenset(
    {"caches", "seeded", "common", "synced"}
)

# The agent sub-table that supplies the all-agents ``agent.default`` cascade level.
_AGENT_DEFAULT_SUB = "default"


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

#: The levels where a ``pref`` REQUEST may be WRITTEN (spec §2h) — the
#: single fact that decides which cure a retired ``box.agent_name`` gets.
_PREF_LEGAL_LEVELS: "frozenset[str]" = frozenset({"workset", "box"})


def _cure_assignment(sub: str, value: Any) -> str:
    """The ``<key>=<value>`` tail a cure can be COPY-PASTED with, for ONE retired mirror leaf.

    ⚑ TWO shapes, because the file's leaf has two. A SCALAR is quoted verbatim so the command
    runs as printed — ``bool`` through YAML's spelling, never Python's ``True``/``False``, which
    is not what the CLI parses. A nested TABLE has no single-token spelling at all, so the tail
    stays a PLACEHOLDER one level deeper rather than a repr that cannot work.
    """
    if isinstance(value, bool):
        return f"{sub}={str(value).lower()}"
    if value is None or isinstance(value, (dict, list, tuple)):
        return f"{sub}.<key>=<value>"
    return f"{sub}={str(value).strip()}" if str(value).strip() else f"{sub}=<value>"


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
    if level in _PREF_LEGAL_LEVELS:
        # ⚑ BOTH verbs take a REQUIRED subject positional — ``box set <box>`` and
        # ``workset set <workset>`` — and omitting it is the exact gap *box_name* was threaded
        # here to close. Unknown ⇒ a PLACEHOLDER, never nothing: a command that is missing an
        # argument fails in a way that reads as "the cure is wrong".
        subject = box_name if level == "box" and box_name else f"<{level}>"
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
    (``None`` at any other *level*, where no single box is being refused for).

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
    if level in _PREF_LEGAL_LEVELS:
        box_arg = f"{box_name} " if level == "box" and box_name else ""
        return (
            f"kanibako box set {box_arg}pref.system.agent={value}   "
            f"(or `kanibako box set {box_arg}--null pref.system.agent` for a "
            f"no-agent box)"
        )
    # M-4: no legal pref equivalent at base/system/agent — FLAG it, never silently relocate it.
    return (
        f"REMOVE it — a request may be written ONLY in a workset or box settings "
        f"file (spec §2h), so this key has NO equivalent at {level} scope. If you "
        f"meant the host-wide default, set it: kanibako system set "
        f"system.agent={value}. If you meant one box, set the request in THAT "
        f"box's settings file: kanibako box set pref.system.agent={value}"
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
        value = "" if found is None else str(found).strip()
        cure = _retired_key_cure(
            key, level=level, value=value or "<name>", box_name=box_name, mirror=mirror,
        )
        raise SettingsError(
            f"'{key}' is RETIRED and is still set in the {level} settings file "
            f"{path if path is not None else '<settings>'} "
            f"(as `{': '.join(parts)}:`).\n"
            f"{_MIRROR_STORY if mirror is not None else _SELECTION_STORY}\n"
            f"  Fix: {cure}\n"
            f"  then delete the `{': '.join(parts)}` entry from {path}."
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
    # ``auto_approve`` deeper in some other table is not swept up. Do not generalise this loop.
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
) -> str:
    """The LEVEL-APPROPRIATE fix for a retired BEHAVIOR key (M-22); per-level reasons in llm-docs."""
    agent = subject or "<agent>"
    if level == "agent":
        return f"kanibako agent set {agent} {successor}={tier}"
    if level in _PREF_LEGAL_LEVELS:
        return f"kanibako {level} set pref.agent.{agent}.{successor}={tier}"
    return f"kanibako system set {successor}={tier}"


def refuse_retired_behavior_keys(
    raw: Any, *, level: str, path: Path | None, subject: str | None = None,
) -> None:
    """RAISE when *raw* still carries a RETIRED behavior key (R-41 / RQ-2) —
    :data:`RETIRED_BEHAVIOR_KEYS`.

    *subject* is the agent node the cure should name; ``None`` renders the shape ``<agent>``. Never
    a warning and never a silent drop; called at the LAUNCH's behavior tier (see block comment).
    """
    from kanibako.settings.config import coerce_bool

    if not isinstance(raw, dict):
        return
    for leaf, successor in RETIRED_BEHAVIOR_KEYS.items():
        for parts, found in _behavior_leaf_sites(raw, leaf):
            spelling = ".".join(parts)
            raw_value = "" if found is None else str(found).strip()
            mapped = _RETIRED_BEHAVIOR_VALUE_MAP.get(leaf, {})
            as_bool = coerce_bool(raw_value) if raw_value else None
            tier = mapped.get(as_bool) if as_bool is not None else None
            # ⚑ The value line only ever states a translation the RULING makes; an unparseable
            # stored value gets the legal tiers instead of a guess.
            if tier is not None:
                value_line = (
                    f"Your stored `{leaf}: {raw_value}` means "
                    f"`{successor}: {tier}` (R-41's mapping: true → full, "
                    f"false → restricted)."
                )
            else:
                tier = "<restricted|editing|full>"
                value_line = (
                    f"Your stored `{leaf}: {raw_value or '(empty)'}` is not a "
                    f"boolean, so it maps to no tier — choose one of "
                    f"restricted | editing | full."
                )
            cure = _retired_behavior_cure(
                successor, level=level, tier=tier, subject=subject,
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
                f"  Fix: {cure}\n"
                f"  then delete the `{spelling}` entry from {path}."
            )


def _containing_scopes(file_scope: str) -> frozenset[str]:
    """The scope tokens that CONTAIN *file_scope* — the HEAD-slice of
    :data:`SCOPE_CONTAINMENT` strictly before it (spec §0, the drop-set)."""
    idx = SCOPE_CONTAINMENT.index(file_scope)
    return frozenset(SCOPE_CONTAINMENT[:idx])


def _drop_upward_scopes(
    raw: dict, *, file_scope: str, path: Path | None
) -> dict:
    """Return *raw* without any CONTAINING-scope top-level table, top-level ``meta:`` or top-level
    ``binding_derivations:`` (spec §0) — a shallow copy, warning-only, never a raise.

    THREE dropped tokens, THREE distinct rationales, one warning each (llm-docs).
    """
    if not isinstance(raw, dict):
        return raw
    # ONE drop-set: the containing scopes UNION the always-dropped tokens. Defensive branch —
    # ``base`` is not in SCOPE_CONTAINMENT, so ``.index`` would raise; it takes an empty set.
    containing = (
        _containing_scopes(file_scope)
        if file_scope in SCOPE_CONTAINMENT
        else frozenset()
    )
    drop_set = containing | frozenset({"meta", BINDING_DERIVATIONS_NODE})
    dropped = [str(k) for k in raw if str(k) in drop_set]
    if not dropped:
        return raw
    where = str(path) if path is not None else "<settings>"
    for token in dropped:
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
            # name; arbitrary unknown top-level tables still ride (llm-docs).
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
    return {k: v for k, v in raw.items() if str(k) not in drop_set}


def _parse_node(
    value: Any, *, in_binds: bool, dest_keyed: bool = False, at_bindings: bool = False,
) -> Any:
    """Recursively coerce a raw settings node into the ``StoreValue`` space.

    ⚑⚑ *dest_keyed* selects WHICH bind shape a leaf has and is the ONLY thing that decides it
    (R-3/R-6): both shapes admit a 2-element list with OPPOSITE meanings, so the choice is made by
    this CONTEXT FLAG — passed down from the caller that knows the node — and NEVER by the leaf's
    arity. *in_binds* / *at_bindings* and the depth rule they encode: llm-docs.
    """
    if isinstance(value, dict):
        store = KeyStore()
        for key, sub in value.items():
            key_s = str(key)
            if at_bindings and key_s in _BIND_ARMS:
                # An ARM (``bindings.ro`` / ``.rw``) — a TERMINAL dest-keyed map (R-5).
                # ⚑ A non-dict is a MALFORMED arm and is deliberately left to the legacy leaf path,
                # so ``settings_launch._assert_declared_categories`` still names the key.
                if isinstance(sub, dict):
                    store[key_s] = parse_bind_map(sub, category=key_s)
                    continue
            if not in_binds and key_s in _DEST_KEYED_LEAF_CATEGORIES:
                # A TERMINAL dest-keyed category — the map is HERE, not one level down, so it is
                # parsed on the way PAST the category token. Same malformed-shape hand-off as an arm.
                # ⚑ ``not in_binds`` keeps a user's entry literally NAMED ``common`` inside another
                # category from being re-read as one — the guard ``at_bindings`` carries below.
                if isinstance(sub, dict):
                    store[key_s] = parse_bind_map(sub, category=key_s)
                    continue
            # Entering a bind-shaped category: its entries below are binds.
            descend_binds = in_binds or key_s in _BIND_CATEGORIES
            store[key_s] = _parse_node(
                sub,
                in_binds=descend_binds,
                dest_keyed=dest_keyed,
                at_bindings=(not in_binds and key_s == _DEST_KEYED_CATEGORY),
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


def parse_bind_map(raw: Any, *, category: str = "bindings") -> KeyStore:
    """Parse a raw DEST-KEYED category map into a :class:`KeyStore` of :class:`BindEntry`.

    *raw* is the ``{box_dest: [src[, opts]]}`` mapping at ANY terminal bind-shaped key — a
    ``bindings`` arm or one of the four whose token is the whole key. ONE parser, not two;
    *category* only names the key in the refusals. Returns a nested node (not an opaque dict leaf)
    so it merges PER-ENTRY across levels; a ``None`` entry is preserved VERBATIM. llm-docs.
    """
    from kanibako.settings.settings_resolve import normalize_bind_dest

    if not isinstance(raw, dict):
        raise SettingsError(
            f"A dest-keyed {category!r} map must be a mapping "
            f"{{box_dest: [src[, options]]}}, got {type(raw).__name__}: {raw!r}."
        )
    store = KeyStore()
    for key, sub in raw.items():
        # ⚑ THE ONE PLACE A STORED DEST IS CANONICALIZED ON READ (R-11) — ``~`` and ``~/`` must be
        # ONE entry. Producers normalize too; the function is idempotent, so neither place is
        # load-bearing alone. ⚑ The VALUE is never canonicalized: a host_src stays as authored.
        dest = normalize_bind_dest(str(key))
        if isinstance(sub, dict):
            # ⚑ A nested MAPPING is the RETIRED name-keyed shape — refused BY NAME (spec §2a
            # "STALE SHAPES ARE REFUSED LOUDLY"). A 3-element list is refused by unpack_bind_entry.
            raise SettingsError(
                f"{category} entry {str(key)!r} holds a sub-table, which is the "
                f"RETIRED name-keyed shape {{name: {{...}}}}. A {category} value "
                f"is a FLAT map keyed by box DESTINATION whose value is "
                f"[src[, options]] (spec §2a); the entry name was dropped "
                f"(bindings 2026-08-06c, the other four 2026-08-08c). Re-key the "
                f"entry to its destination."
            )
        store[dest] = _parse_node(sub, in_binds=True, dest_keyed=True)
    return store


def _file_partial(raw: dict) -> KeyStore:
    """Build ONE level partial from a settings file's WHOLE nested content, SCOPE TOKEN KEPT (§0).

    The rule for every NON-agent level (``base`` / ``system`` / ``workset`` / ``box``); the agent
    tier uses :func:`_agent_partial`. ⚑ The bind DEPTH is not chosen here — :func:`_parse_node`
    derives it from the CATEGORY token it walks past, so there is no flag for a caller to get wrong.
    """
    if not isinstance(raw, dict):
        return KeyStore()
    parsed = _parse_node(raw, in_binds=False)
    assert isinstance(parsed, KeyStore)
    return parsed


def _agent_partial(
    raw: dict, *, sub_key: str, path: Path | None = None, node: str | None = None
) -> KeyStore:
    """Build an AGENT-tier level partial (``agent.default`` or ``agent.<active>``) from the agent
    file, re-rooted under its TRUE discriminated name ``agent.<sub_key>``.

    ⚑ THE SEAM: :func:`~kanibako.settings.agent_file.level_table` owns the file's SHAPE (which
    tables a level reads, the flat-category re-root, and the nested refusal that precedes it) and
    hands back a RAW table; this function owns the STORE coercion and the §2d wrap. The split is
    what keeps the boundary free of ``KeyStore`` — and the import edge one-way.

    *sub_key* selects the TIER; the two agent levels are kept SEPARATE (spec §2) and merge by
    their true §2d names — NO bare-``agent`` collapse. An empty level yields an empty partial,
    which is what the all-agents tier ALWAYS is out of this file since the flatten.
    *path* and *node* only render the boundary's refusal message; neither is read. llm-docs.
    """
    level = level_table(raw, sub_key=sub_key, node=node, path=path)
    if not level.table:
        return KeyStore()
    # The discriminator (``default`` / the active agent's name) is the §2d key form and is
    # load-bearing: it keeps the fallback layer and any per-agent override distinct under the merge.
    parsed_sub = _parse_node(level.table, in_binds=False)
    agent_node = KeyStore()
    agent_node[level.node] = parsed_sub
    store = KeyStore()
    store["agent"] = agent_node
    return store


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
    in_binds = any(p in _BIND_CATEGORIES for p in parts[:-1])
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
    deeper = [p for p in parts[:-1] if p in _DEST_KEYED_LEAF_CATEGORIES]
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
        node[parts[-1]] = parse_bind_map(value, category=parts[-1])
    elif parts[-1] in _DEST_KEYED_LEAF_CATEGORIES and isinstance(value, dict):
        node[parts[-1]] = parse_bind_map(value, category=parts[-1])
    else:
        node[parts[-1]] = _parse_node(value, in_binds=in_binds)


def assemble_levels(
    *,
    agent_name: str,
    base_path: Path | None = None,
    system_path: Path | None = None,
    agent_path: Path | None = None,
    workset_path: Path | None = None,
    box_path: Path | None = None,
    floor: dict[str, object] | None = None,
) -> list[KeyStore]:
    """Read each cascade scope's settings file into ONE nested ``KeyStore`` partial and return the
    six MOST-SPECIFIC-FIRST (S8): ``[box, workset, agent.<active>, agent.default, system, base]``.

    *floor* (declared defaults + default-categories) folds UNDER the base file into the ``base``
    level and is the SOLE sanctioned ``meta.*`` source. Absent / unreadable files yield an empty
    partial; NO ``machine`` path is consulted (S14). Per-parameter detail: llm-docs.
    """
    base_p = base_path if base_path is not None else settings_base_path()

    raw_base = load_doc(base_p)
    raw_system = load_doc(system_path)
    raw_agent = load_doc(agent_path)
    raw_workset = load_doc(workset_path)
    raw_box = load_doc(box_path)

    # ⚑ Both filters run on the RAW file view, BEFORE the partial is built, and that ordering is
    # LOAD-BEARING: the agent tier never mirrors a non-``self:`` table into its partial, so a
    # post-partial filter could not see (or warn about) a ``system:`` or ``pref:`` table there.
    #
    # ``pref:`` is legal in the WORKSET and BOX files ONLY (spec §2h) — elsewhere DROPPED with a
    # warning, the SAME treatment the sibling mis-scope gets; the HARD refusal lives at the WRITE
    # site. Directional enforcement then drops any CONTAINING-scope top-level table (spec §0):
    # ``system``'s containing set is empty, and ``base`` is a CODE FLOOR, EXEMPT for SCOPE keys but
    # NOT for ``meta``. Full reasoning: llm-docs.
    raw_base = refuse_pref_table(raw_base, level="base", path=base_p)
    raw_system = refuse_pref_table(raw_system, level="system", path=system_path)
    raw_agent = refuse_pref_table(raw_agent, level="agent", path=agent_path)

    raw_base = _drop_upward_scopes(raw_base, file_scope="base", path=base_p)
    raw_box = _drop_upward_scopes(raw_box, file_scope="box", path=box_path)
    raw_workset = _drop_upward_scopes(
        raw_workset, file_scope="workset", path=workset_path
    )
    # The ONE agent file builds TWO levels; drop+warn ONCE on the shared raw view.
    raw_agent = _drop_upward_scopes(raw_agent, file_scope="agent", path=agent_path)
    raw_system = _drop_upward_scopes(
        raw_system, file_scope="system", path=system_path
    )

    # The floor is inserted FIRST and the base-file leaves overlay it, so a base-file entry wins
    # WITHIN this single level and the floor is the ultimate fallback.
    base_partial = dotted_partial(floor)
    _overlay(base_partial, _file_partial(raw_base))

    # MOST-SPECIFIC-FIRST (S8). Each scope file's partial keeps its scope token so the merge works
    # by scope-qualified name; the agent tier keeps its §2d discriminator — NO bare-``agent``
    # collapse.
    return [
        _file_partial(raw_box),
        _file_partial(raw_workset),
        _agent_partial(
            raw_agent, sub_key=agent_name, path=agent_path, node=agent_name,
        ),
        # ⚑⚑ THIS LEVEL IS STRUCTURALLY EMPTY SINCE THE S2 FLATTEN, and saying so is the
        # point: the agent file has NO spelling for the all-agents tier at all (``self:`` IS
        # ``agent.<node>``, so a ``default`` sub-table under it reads
        # ``agent.<node>.default.*`` and REFUSES). That tier's route is the SYSTEM file's
        # ``agent: default:`` table, which arrives on the system level below. The call is KEPT
        # rather than deleted: dropping it re-indexes every ``base_levels[n]`` consumer, and
        # whether a permanently-empty rung should be encoded at all is a re-encoding question
        # boarded on its own, not a rider here.
        _agent_partial(
            raw_agent, sub_key=_AGENT_DEFAULT_SUB, path=agent_path, node=agent_name,
        ),
        _file_partial(raw_system),
        base_partial,
    ]


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

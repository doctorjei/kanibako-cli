"""Cascade level assembly — per-scope settings files → ordered KeyStore partials.

Block 2a of the KeyStore implementation. This module reads each cascade scope's
settings file(s) into ONE unified nested :class:`~kanibako.settings.settings_store.KeyStore`
partial per level and returns the ordered ``list[KeyStore]`` the merge (block 2b)
consumes. It builds ALONGSIDE the live launch cascades (``commands/start.py``,
``config.py:load_settings``) — it modifies none of them; the swap is block 7.

It performs READS and structural parsing ONLY: NO merge / precedence, NO
``@``-ref / ``$var`` / ``~`` expansion or cycle detection, NO typed views, NO
``config set``. Tokens are left RAW inside binds (``files store UNRESOLVED``,
design §6a / spec §0).

Authority
---------
* Spec ``settings-keyspace-1.8.0.md`` §2 (cascade — PRIMARY
  authority): the 6-level order ``base < system < agent.default < agent.<active> <
  workset < box``, high→low precedence; ``agent.default`` is an EXPLICIT
  level and both agent layers reuse the same linear ``_MISSING`` precedence (no
  nested mini-cascade) — the LEVEL ORDER is the precedence. §2d: the ONLY
  two agent key forms are ``agent.default.<key>`` and ``agent.<agent>.<key>`` (a
  concrete agent name) — §0 forbids a bare ``agent.<key>``. ``keystore-design.md``
  §2 (storage — partials are ``KeyStore``s, binds are ``Bind``); §6a / spec §0
  (files store UNRESOLVED — refs stay raw).
* Spec ``settings-keyspace-1.8.0.md`` §2 (cascade + scopes) / §2a
  (categories + value types) / §0 (namespace ORTHOGONAL to cascade).
* Keyspace audit 2026-06-27c #2: the ``machine`` (``/etc/kanibako.yaml``) tier is
  CUT — cascade floor is ``base`` (overridable) and the cascade ENDS at ``box``
  (the former ``required`` non-overridable cap is CUT, 2026-06-29f). This module
  reads NO ``machine_config_path()``.

Seams realized here (``plans/keystore-blocks/SEAMS.md``)
-------------------------------------------------------
* **S7** — partials are NESTED ``KeyStore``s (not flat dotted dicts); a scope
  file's nested tables are mirrored verbatim into the partial.
* **S8** — output order is MOST-SPECIFIC-FIRST:
  ``[box, workset, agent.<active>, agent.default, system, base]``. The
  two agent levels keep their TRUE discriminated keys (``agent.<active-name>.*`` /
  ``agent.default.*``, §2d) — NO bare-``agent`` collapse; level order is the
  cascade precedence.
* **S9** — binds parsed to ``Bind`` at ASSEMBLY with ``@``-refs / ``$vars`` / ``~``
  left RAW inside ``host`` / ``box`` (expansion is block 3).
* **S13** — ONE unified ``KeyStore`` partial per level holding BOTH behavior
  leaves AND category subtrees together (design §1/§2 single-source).
* **S14** — no ``machine`` tier; floor → ``base``; cascade ends at ``box`` (no
  ``required`` cap).

Keyspace convention — scope token KEPT; DOWNWARD/same-scope only (§0)
--------------------------------------------------------------------
Settings files are scope-ROOTED on disk (``config.py:_flatten_categories`` —
``{system: {bindings: {rw: {foo: …}}}}`` → ``system.bindings.rw.foo``). The scope
token is LOAD-BEARING (it names the DECLARATION ROOT an abstract-category source
is spelled against, and picks the mount mode for ``bindings``)
and namespace is ORTHOGONAL to cascade level (§0). A file may hold keys of its OWN
scope AND of scopes it CONTAINS (``system ⊃ agent ⊃ workset ⊃ box``) as
OVERRIDABLE defaults-down — e.g. a workset file may set ``box.*`` and it flows.
But **directional enforcement at RESOLVE** (spec §0, Jei-blessed 2026-07-02)
DROPS a top-level table of a CONTAINING scope found in a lower file (e.g.
``system:`` / ``workset:`` / ``agent:`` in a box file) at assembly, with a warning
naming the file + token — it never enters the merge (see
:func:`_drop_upward_scopes`). So a level partial mirrors its file's WHOLE nested
content MINUS any upward table, SCOPE TOKEN KEPT (``box.image``,
``box.caches.x`` at box; a workset file keeps ``workset.*`` + its ``box.*``
defaults) — the LEVEL identity is the FILE, not a lifted sub-table. Block 2b then
merges by the scope-qualified name across levels. The ``base`` code floor is
EXEMPT for SCOPE keys (the system-scope floor). ``@``-refs still view UP
read-only. A top-level ``meta:`` table is ALWAYS dropped from EVERY file
(``base`` included) — ``meta.*`` is a TOP-LEVEL protected namespace set by the
construct-time/bootstrap layer and stays RO everywhere (spec §0 / clause 4). The
sole sanctioned meta source is the runtime/identity FLOOR (``dotted_partial``),
which is never dropped, and a NESTED ``<scope>.meta`` bootstrap table (e.g.
``workset.meta`` from ``workset.py``) is UNTOUCHED — only the top-level ``meta:``
key is stripped (see :func:`_drop_upward_scopes`).

The AGENT tier yields TWO separate cascade levels from the one agent file (spec
§2): the file nests ``agent.default.<key>`` (the all-agents fallback
layer) and ``agent.<agent>.<key>`` (the per-agent layer). Each becomes a SEPARATE
cascade LEVEL and the per-agent DISCRIMINATOR is KEPT VERBATIM — the default layer
under ``agent.default.<key>``, the active layer under ``agent.<active-name>.<key>``
(the ONLY two agent key forms the spec allows — §2d; §0 forbids a
bare ``agent.<key>``). The two levels merge BY THEIR TRUE NAMES (block 2b); the
LEVEL ORDER (active above default, S8) is the explicit cascade precedence (§2
"explicit in the cascade … no nested mini-cascade"). The thin
active-over-default value-pick (``agent.<active>.<key> | agent.default.<key>``,
§2d) is an effective-agent READ deferred to the block-7 consumer, NOT a
name collapse here. Keeping the discriminator preserves §0 per-agent
independence: ``agent.<other>.*`` set within the AGENT scope (or higher) survives
the merge by its own name — but a box file may NOT set ``agent.<other>.*`` (that
is an upward write, dropped above; a box tweaks its agent via the ``box.agent.*``
mirror, §2b).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kanibako.settings.config import settings_base_path
from kanibako.settings.config_io import load_doc
from kanibako.settings.settings_prefs import refuse_pref_table
from kanibako.settings.settings_resolve import SettingsError, unpack_bind
from kanibako.settings.settings_store import (
    BINDING_DERIVATIONS_NODE,
    SCOPE_CONTAINMENT,
    Bind,
    KeyStore,
)

_log = logging.getLogger(__name__)

# The bind-shaped categories whose leaf value is a structured pair/tuple
# ``[host_src, box_dest[, options]]`` (spec §2a "REPRESENTATION") — each is parsed
# to a :class:`Bind` at assembly (S9). ``masks`` (a keyed ``dict[box_dest →
# bool|None]``, S5) and ``env`` (scalar ``{VAR} → value``) keep their natural
# nested shape and are NOT bind-parsed. ``bindings`` carries the ``ro`` / ``rw``
# sub-tables, each of which holds bind leaves.
_BIND_CATEGORIES: frozenset[str] = frozenset(
    {"bindings", "caches", "seeded", "common", "synced"}
)

# The agent sub-table that supplies the all-agents ``agent.default`` cascade level.
_AGENT_DEFAULT_SUB = "default"


# ---------------------------------------------------------------------------
# RETIRED agent-selection spellings — refuse by name (P7, spec §0 / §2b / §2g)
# ---------------------------------------------------------------------------
#
# ⚑ WHY THIS EXISTS AT ALL, given that migration is DOCUMENTATION-ONLY for this
# arc (IMPL-PLAN standing ruling 1). This is NOT migration machinery: it reads
# nothing, relocates nothing and writes nothing. It is §0's CLOSED-KEYSPACE rule
# — *an undeclared key is an ERROR that NAMES it* — applied to the two spellings
# P7 retired. The documentation-only ruling was made against failure modes of the
# "empty dir beside a populated one" shape; a box that SILENTLY RUNS A DIFFERENT
# AGENT (and seeds that agent's CREDENTIALS into itself) is categorically worse,
# and Jei's own M-7 ruling — hard error with a migration-grade message — is the
# precedent for loud in exactly this arc. Scope is deliberately TIGHT: these two
# keys, nothing else. This is NOT general resolve enforcement; that follow-on
# stays deferred (it is gated on ``settings_keyspace.RETIRING_KEYS`` emptying).
#
# Each entry maps the NESTED file path of the retired leaf to the retired KEY name.
# The CURE is LEVEL-DEPENDENT (see :func:`_retired_key_cure`) — a pref is legal only
# in a workset or box file (spec §2h), so telling a SYSTEM-file reader to
# "box set pref…" would prescribe a write that cannot fix their file.
# Migration record: M-4.
RETIRED_FILE_KEYS: "dict[tuple[str, ...], str]" = {
    ("box", "agent_name"): "box.agent_name",
    ("agent", "default", "default_agent"): "system.default_agent",
}

#: The levels where a ``pref`` REQUEST may be WRITTEN (spec §2h) — the
#: single fact that decides which cure a retired ``box.agent_name`` gets.
_PREF_LEGAL_LEVELS: "frozenset[str]" = frozenset({"workset", "box"})


def _retired_key_cure(key: str, *, level: str, value: str) -> str:
    """The LEVEL-APPROPRIATE fix for a retired key (M-4)."""
    if key == "system.default_agent":
        # Always the same cure: the replacement is a SYSTEM-scope key wherever the
        # stale leaf was found.
        return f"kanibako system config set system.agent={value}"
    # box.agent_name → the §2h request, but ONLY where a request may be written.
    if level in _PREF_LEGAL_LEVELS:
        return (
            f"kanibako box set pref.system.agent={value}   "
            f"(or `kanibako box set --null pref.system.agent` for a no-agent box)"
        )
    # M-4: *"A box.agent_name found in a system or agent file has no legal pref
    # equivalent — flag it rather than silently relocating it."*
    return (
        f"REMOVE it — a request may be written ONLY in a workset or box settings "
        f"file (spec §2h), so this key has NO equivalent at {level} scope. If you "
        f"meant the host-wide default, set it: kanibako system config set "
        f"system.agent={value}. If you meant one box, set the request in THAT "
        f"box's settings file: kanibako box set pref.system.agent={value}"
    )


#: The "no such leaf" sentinel for :func:`_nested_present`. ⚑ NOT ``None``: a
#: ``box: {agent_name:}`` leaf is PRESENT with the value ``None``, and it is still
#: the retired key — conflating present-null with absent is the same 3-state
#: mistake §2h warns about for prefs, and it would let the exact config the
#: refusal exists to catch slip through silently.
_NO_LEAF: Any = object()


def _nested_present(raw: Any, parts: "tuple[str, ...]") -> Any:
    """Read *raw* at the nested *parts* path, or :data:`_NO_LEAF` when ABSENT.

    Distinguishes ABSENT from PRESENT-``None`` (see :data:`_NO_LEAF`).
    """
    node: Any = raw
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return _NO_LEAF
        node = node[part]
    return node


def refuse_retired_keys(raw: Any, *, level: str, path: Path | None) -> None:
    """RAISE when *raw* still carries a RETIRED agent-selection key (P7).

    The two keys are :data:`RETIRED_FILE_KEYS`. The message names the KEY, the
    FILE, the fact that THE RULE CHANGED, and the one-line cure — it must never
    read as "your config is wrong" (the M-7 precedent). Never a warning and never
    a silent drop: a dropped ``box.agent_name`` would leave the box launching the
    system default with that agent's credentials, which is the exact failure this
    refusal exists to prevent.

    Called at the SELECTION seam (:mod:`kanibako.settings.agent_select`), not inside
    :func:`assemble_levels` — a raise there would also break ``config set``,
    i.e. the very command the message prescribes as the cure.
    """
    if not isinstance(raw, dict):
        return
    for parts, key in RETIRED_FILE_KEYS.items():
        found = _nested_present(raw, parts)
        if found is _NO_LEAF:
            continue
        # The cure carries the value the user ACTUALLY has, so it is
        # copy-pasteable rather than a shape to fill in. A present-``None`` (an
        # empty ``agent_name:`` leaf) has no value to quote → the shape.
        value = "" if found is None else str(found).strip()
        cure = _retired_key_cure(key, level=level, value=value or "<name>")
        raise SettingsError(
            f"'{key}' is RETIRED and is still set in the {level} settings file "
            f"{path if path is not None else '<settings>'} "
            f"(as `{': '.join(parts)}:`).\n"
            f"The RULE CHANGED in kanibako 1.8.0: a box no longer names its agent "
            f"with a key of its own — it REQUESTS one at the key that resolves "
            f"earlier (`pref.system.agent`, spec §2h), and the system default is "
            f"now `system.agent` (§2g). Refusing rather than running: kanibako "
            f"cannot tell which agent you meant, and guessing would launch a "
            f"DIFFERENT agent and seed that agent's credentials into this box.\n"
            f"  Fix: {cure}\n"
            f"  then delete the `{': '.join(parts)}` entry from {path}."
        )


def _containing_scopes(file_scope: str) -> frozenset[str]:
    """The scope tokens that CONTAIN *file_scope* (spec §0, the drop-set).

    A settings file contributes keys of its OWN scope and of scopes it CONTAINS
    (defaults-down); a top-level key naming a CONTAINING scope is an UPWARD write
    that :func:`_drop_upward_scopes` drops at assembly. Containment is
    ``system ⊃ agent ⊃ workset ⊃ box`` (:data:`SCOPE_CONTAINMENT`, single source),
    so the containing set is the HEAD-slice strictly BEFORE *file_scope*. The
    outermost scope (``system``) has an empty set — nothing contains it.
    """
    idx = SCOPE_CONTAINMENT.index(file_scope)
    return frozenset(SCOPE_CONTAINMENT[:idx])


def _drop_upward_scopes(
    raw: dict, *, file_scope: str, path: Path | None
) -> dict:
    """Return *raw* with any CONTAINING-scope top-level table, a top-level
    ``meta:`` table AND a top-level ``binding_derivations:`` table removed
    (spec §0).

    Directional enforcement at RESOLVE: a settings file may set keys of its own
    scope and of scopes it CONTAINS, but a top-level key of a CONTAINING scope
    (e.g. ``system:`` / ``workset:`` in a box file) is an UPWARD write — DROPPED
    here before it enters the partial, with ONE ``logger.warning`` per dropped
    token naming the file path and the token. Downward and same-scope tables are
    untouched (a workset file's ``box:`` defaults-down table still flows — the
    Jei-ruled defaults-down mechanism).

    ``meta`` is ALSO dropped, for EVERY file (``base`` included) — ``meta.*`` is a
    TOP-LEVEL protected read-only namespace set by the construct-time/bootstrap
    layer, RO everywhere (spec §0 / clause 4 "meta.* remains RO everywhere"); a
    settings file may not set it. ``meta`` is NOT a containing scope, so it earns a
    DISTINCT warning. This drop is TOP-LEVEL ONLY: a NESTED ``<scope>.meta``
    bootstrap table (e.g. ``workset.meta`` written by ``workset.py`` and read by
    ``read_workset_meta``) rides under its scope table and is UNTOUCHED — this
    function iterates only top-level keys of *raw* and never descends. The sole
    sanctioned meta source is the FLOOR (``dotted_partial``), inserted separately
    and never routed through this drop.

    ``binding_derivations`` is the THIRD dropped token, with a THIRD rationale
    (spec §0 fault class: "never enters the merge"): it is the RESERVED INTERNAL
    derivations node at the snapshot root (R-8, manifest
    ``not_keys.reserved_internal``) — machinery output regenerated per launch by
    ``commands.start._install_derived_bindings``, not a key. A hand-forged table
    in a settings file would otherwise ride into the snapshot beside the real
    materialisation (phantom ``--effective`` lines; a non-``Bind`` leaf crashes
    the ``derived_bindings`` lens with ``ViewError``). Same profile as ``meta``:
    EVERY file (``base`` included), TOP-LEVEL ONLY — a NESTED
    ``<scope>.binding_derivations`` key is not this rule's business. SCOPE
    TIGHT: this ONE name only — arbitrary unknown top-level tables still ride
    (general unknown-table refusal is the backlogged keyspace-ENFORCEMENT work,
    not this drop).

    ``base`` is EXEMPT for SCOPE keys (its containing set is empty — it is the
    system-scope floor) but NOT for ``meta``: a base-file top-level ``meta:`` table
    would clobber the floor's materialized identity anchors, so it drops too.

    Returns a shallow copy with the dropped keys removed (never mutates *raw*);
    a non-dict *raw* is returned unchanged. Warning-only side effect (no raise) —
    a mis-scoped key is a config mistake, not a hard error.
    """
    if not isinstance(raw, dict):
        return raw
    # ONE drop-set = the containing scopes (defensive: ``base`` is not in
    # SCOPE_CONTAINMENT so ``.index`` would raise — an unknown/base scope has an
    # empty containing set) UNION the always-dropped top-level ``meta`` token.
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
            # meta is NOT a containing scope — distinct rationale (spec §0 meta-RO /
            # clause 4): meta.* is a top-level RO namespace owned by the bootstrap
            # layer, never settable from a settings file.
            _log.warning(
                "Dropping top-level 'meta' table from %s settings file %s: "
                "meta.* is a read-only namespace set by the "
                "construct-time/bootstrap layer and remains RO everywhere (spec "
                "§0 meta-RO / clause 4); the key is ignored.",
                file_scope, where,
            )
        elif token == BINDING_DERIVATIONS_NODE:
            # binding_derivations is neither a containing scope nor meta — a
            # THIRD rationale (spec §0 fault class): the RESERVED INTERNAL
            # derivations node (R-8), machinery output, never file input.
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


def _parse_node(value: Any, *, in_binds: bool) -> Any:
    """Recursively coerce a raw settings node into the ``StoreValue`` space.

    *in_binds* is True while descending the subtree of a bind-shaped category
    (``bindings.{ro,rw}`` / ``caches`` / ``seeded`` / ``shared`` / ``synced``) —
    where a list/tuple LEAF is a structured ``[host_src, box_dest[, opts]]`` pair
    parsed to a :class:`Bind` (S9). Refs inside the bind stay RAW (spec §0). A
    plain ``dict`` descends (the per-name dict UNDER the category); any other
    leaf (scalar / ``None`` / a genuine ``list[str]``) is stored verbatim.
    """
    if isinstance(value, dict):
        store = KeyStore()
        for key, sub in value.items():
            key_s = str(key)
            # Entering a bind-shaped category: its named entries below are binds.
            descend_binds = in_binds or key_s in _BIND_CATEGORIES
            store[key_s] = _parse_node(sub, in_binds=descend_binds)
        return store
    if in_binds and isinstance(value, (list, tuple)):
        # A structured bind leaf — parse to Bind, refs left RAW (S9). A malformed
        # arity raises SettingsError (the structured shape is load-bearing).
        host, box, opts = unpack_bind(value)
        return Bind(host, box, opts)
    # Scalar / None / genuine list[str] — stored verbatim (KeyStore wraps None and
    # scalars as-is; a list is not descended).
    return value


def _file_partial(raw: dict) -> KeyStore:
    """Build ONE level partial from a settings file's WHOLE nested content.

    *raw* is the parsed file (``load_doc`` output). The full scope-ROOTED tree is
    mirrored into a nested :class:`KeyStore` with the SCOPE TOKEN KEPT (§0:
    namespace orthogonal to cascade) — binds parsed to :class:`Bind`, refs raw.
    An empty / non-dict file → an empty :class:`KeyStore`. This is the rule for
    every NON-agent level (``base`` / ``system`` / ``workset`` / ``box``); the
    agent tier uses :func:`_agent_partial`.
    """
    if not isinstance(raw, dict):
        return KeyStore()
    parsed = _parse_node(raw, in_binds=False)
    assert isinstance(parsed, KeyStore)
    return parsed


def _agent_partial(raw: dict, *, sub_key: str) -> KeyStore:
    """Build an AGENT-tier level partial (``agent.default`` or ``agent.<active>``).

    The agent settings file is rooted at a top-level ``agent:`` table holding
    per-agent sub-tables (``default:`` for the all-agents layer, ``<name>:`` for
    each agent). *sub_key* selects which sub-table becomes THIS level — the two
    are kept SEPARATE cascade levels (spec §2; today's
    ``read_agent_settings`` pre-merges them, which 2a deliberately does NOT).

    The per-agent DISCRIMINATOR is KEPT VERBATIM — the sub-table is re-rooted under
    its TRUE discriminated name ``agent.<sub_key>`` (``agent.default.<key>`` for the
    default layer, ``agent.<active-name>.<key>`` for the active layer), the ONLY two
    agent key forms the spec defines (§2d; §0 forbids a bare
    ``agent.<key>``). The two agent levels then merge BY THEIR TRUE NAMES (block 2b),
    each scope-qualified key overriding the same key at a lower level; the
    active-over-default value-pick (``agent.<active>.<key> | agent.default.<key>``,
    §2d) is a thin effective-agent READ deferred to the block-7 consumer (the
    cascade's job is precedence by LEVEL ORDER — §2 "explicit in the
    cascade … no nested mini-cascade" — not a name collapse). This preserves §0
    per-agent independence: a box/workset that sets ``agent.<other>.*`` (or directly
    sets ``agent.default.*``) keeps its true name and survives the merge intact.

    A missing ``agent`` table, or a *sub_key* with no matching sub-table (e.g. an
    active agent absent from the file), → an empty :class:`KeyStore` level.
    """
    agent = raw.get("self") if isinstance(raw, dict) else None
    if not isinstance(agent, dict):
        return KeyStore()
    sub = agent.get(sub_key)
    node_tbl: dict = dict(sub) if isinstance(sub, dict) else {}
    # ``self`` IS ``agent.<active-node>``: the FLATTENED cascade category
    # ``secret_path`` lives at the file's TOP level (``self.secret_path`` since the
    # 2026-07-14b flatten), NOT in the nested ``self.<node>`` sub-table (which still
    # holds bindings, pending their own flatten). It belongs to THIS node, so re-root
    # it alongside the sub-table for the ACTIVE layer ONLY — never the all-agents
    # ``default`` layer. Without this the launch SECRET export (which reads the
    # reconciled cascade) never sees an agent-scope secret_path → no token mount.
    if sub_key != _AGENT_DEFAULT_SUB:
        flat_secret = agent.get("secret_path")
        if isinstance(flat_secret, dict) and flat_secret:
            node_tbl["secret_path"] = flat_secret
    if not node_tbl:
        return KeyStore()
    # Re-root the sub-table under its TRUE discriminated name ``agent.<sub_key>``
    # (NO bare-token collapse). _parse_node handles the bind/category structure
    # inside. The discriminator (``default`` / the active agent's name) is the §2d
    # key form and is load-bearing — it keeps the all-agents fallback layer and any
    # per-agent override distinct under the cascade merge.
    parsed_sub = _parse_node(node_tbl, in_binds=False)
    agent_node = KeyStore()
    agent_node[sub_key] = parsed_sub
    store = KeyStore()
    store["agent"] = agent_node
    return store


def dotted_partial(floor: dict[str, object] | None) -> KeyStore:
    """Build a merge LEVEL from a flat ``{dotted key: value}`` mapping.

    *floor* is the target's declared ``{key: default}`` behavior defaults plus
    default-categories (mirrors what ``start.py`` gathers today). Its keys are the
    same SCOPE-QUALIFIED logical keys as the files use (flat dotted, e.g.
    ``"box.bindings.rw.home"`` / ``"agent.auto_approve"``); dotted keys are
    EXPLODED to the nested keyspace (S7) so the floor merges uniformly with the
    other partials. Bind-shaped values are parsed to :class:`Bind`.
    """
    store = KeyStore()
    if not floor:
        return store
    for raw_key, raw_val in floor.items():
        _insert_dotted(store, str(raw_key), raw_val)
    return store


def _insert_dotted(store: KeyStore, dotted: str, value: Any) -> None:
    """Insert *value* at the dotted-path *dotted* into *store*, exploding to nested
    :class:`KeyStore` nodes (S7). The terminal leaf is parsed: a value under a
    bind-shaped category segment becomes a :class:`Bind`; otherwise verbatim.
    """
    parts = dotted.split(".")
    # A leaf is bind-shaped iff any ancestor segment is a bind category.
    in_binds = any(p in _BIND_CATEGORIES for p in parts[:-1])
    node: KeyStore = store
    for part in parts[:-1]:
        # UNBOUND dict.get (S3): never the bound ``node.get`` — a leaf named
        # ``get`` would shadow the method into a crash. Keeps the collision-safe
        # convention uniform even though these stores are module-built.
        existing = dict.get(node, part)
        if not isinstance(existing, KeyStore):
            existing = KeyStore()
            node[part] = existing
        node = existing
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
    """Read each cascade scope's settings file into ONE nested ``KeyStore`` partial
    and return the ordered ``list[KeyStore]`` (MOST-SPECIFIC-FIRST, S8).

    The 6 levels, in order::

        [box, workset, agent.<active>, agent.default, system, base]

    matching design §4's ``base < system < agent.default < agent.<active> <
    workset < box`` reversed to high→low precedence (block 2b walks
    this order; the first scope that SETS a leaf wins).

    Each non-agent level's partial = its file's WHOLE nested content, scope token
    KEPT (§0). The agent file yields BOTH agent levels via its ``default`` and
    ``<active>`` sub-tables, each kept under its TRUE discriminated name
    (``agent.default.<key>`` / ``agent.<active-name>.<key>``, spec §2d) — NO
    bare-``agent`` collapse.

    * *agent_name* selects the active agent's sub-table for the ``agent.<active>``
      level; ``agent.default`` reads the ``default`` sub-table from the SAME file.
    * *base_path* defaults to ``settings_base_path()`` (the ``/etc`` floor) — no
      ``machine`` tier (S14); the cascade ends at ``box`` (no ``required`` cap).
      The base file uses the SAME scoped keyspace as every other file (NOT a
      synthetic ``base:`` wrapper).
    * *floor* (declared defaults + default-categories) is folded UNDER the base
      file's content into the ``base`` level — a base-FILE set-value beats the
      floor at the same key; the floor is the ultimate fallback. The floor is also
      the SOLE sanctioned ``meta.*`` source: a top-level ``meta:`` table is dropped
      from every FILE view (base included, spec §0 / clause 4 — RO everywhere)
      BEFORE it is built, so it can never clobber the floor's identity anchors; the
      floor itself is inserted separately and never dropped.

    Binds are parsed to :class:`Bind`; ``@``-ref / ``$var`` / ``~`` tokens stay RAW
    (S9 / spec §0). Absent / unreadable files → an empty :class:`KeyStore` partial
    (skipped cleanly by the merge). NO ``machine`` path is consulted.
    """
    base_p = base_path if base_path is not None else settings_base_path()

    raw_base = load_doc(base_p)
    raw_system = load_doc(system_path)
    raw_agent = load_doc(agent_path)
    raw_workset = load_doc(workset_path)
    raw_box = load_doc(box_path)

    # Directional enforcement at RESOLVE (spec §0). Each USER settings file may set
    # keys of its OWN scope and of scopes it CONTAINS (defaults-down), but NOT of a
    # CONTAINING scope: drop those upward top-level tables here (warn-once each) so
    # they never enter the merge. Done on the RAW file view BEFORE building the
    # partial — the agent tier never mirrors a non-``agent:`` table, so a
    # post-partial filter could not see (or warn) a ``system:`` table in the agent
    # file; the raw view catches it. The ``system`` file's containing-set is empty
    # (outermost — nothing contains it), so its scope-key pass is a no-op. The
    # ``base`` level (floor dict + ``/etc`` base file) is a CODE FLOOR and is EXEMPT
    # for SCOPE keys — it is the system-scope floor from which the auth gate is set.
    # BUT a top-level ``meta:`` table is dropped from EVERY file (base included):
    # ``meta.*`` is a top-level RO namespace owned by the bootstrap layer (spec §0 /
    # clause 4), so a base-FILE meta table must not clobber the floor's identity
    # anchors. ``file_scope="base"`` yields an empty containing set, so this drops
    # ONLY meta from base — its ``system.*`` scope floor stays exempt. (A nested
    # ``<scope>.meta`` bootstrap table is TOP-LEVEL-untouched — see the drop fn.)
    # ``pref:`` is legal in the WORKSET and BOX files ONLY (spec §2h —
    # "this is what BOUNDS the recursion"). A ``pref:`` table in the base /
    # system / agent file is DROPPED with a warning, the SAME treatment the
    # sibling mis-scope above gets: two behaviours for one fault class is the
    # confusion §0's convention 0 forbids, and dropping preserves the recursion
    # bound at least as strongly as erroring would. The HARD refusal §2h calls
    # for lives at the WRITE site (``config set pref.*`` at these scopes RAISES),
    # which is the only way a user creates one short of hand-editing.
    #
    # Run on the RAW view for the same reason ``_drop_upward_scopes`` is: the
    # agent tier never mirrors a non-``agent:`` table into its partial, so a
    # post-partial filter could not see (or warn about) a ``pref:`` table there.
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

    # The base partial carries the declared-default floor UNDER any base-file
    # content: a base-FILE set-value beats the floor at the same key (the floor is
    # the ultimate fallback). The floor is inserted first, then the file leaves
    # overlay, so a base-file entry wins WITHIN this single level.
    base_partial = dotted_partial(floor)
    _overlay(base_partial, _file_partial(raw_base))

    # MOST-SPECIFIC-FIRST (S8). Each scope file's partial keeps its scope token
    # so 2b merges by the scope-qualified name; the agent tier keeps its
    # default/<active> discriminator as the TRUE §2d key (``agent.default.*`` /
    # ``agent.<active-name>.*``), NO bare-``agent`` collapse.
    return [
        _file_partial(raw_box),
        _file_partial(raw_workset),
        _agent_partial(raw_agent, sub_key=agent_name),
        _agent_partial(raw_agent, sub_key=_AGENT_DEFAULT_SUB),
        _file_partial(raw_system),
        base_partial,
    ]


def _overlay(base: KeyStore, top: KeyStore) -> None:
    """Deep-overlay *top*'s leaves onto *base*, in place (same-level combine).

    Used ONLY to layer a base-FILE partial over the declared-default floor WITHIN
    the single ``base`` level (so a base-file set-value beats the floor default).
    This is NOT the cascade merge (block 2b) — it is a same-level union of two
    SOURCES (floor defaults + the base file). It descends matching :class:`KeyStore`
    subtrees so a deep base-file leaf (``agent.<agent>.bindings.rw.x``) overlays the same
    deep floor leaf without clobbering sibling floor leaves; a non-subtree leaf in
    *top* replaces *base*'s same key wholesale (the file is the authoritative
    source at this level). Uses unbound ``dict`` ops (S3) — both stores are
    module-built, but the bypass keeps the collision-safe convention uniform.
    """
    for key in dict.keys(top):
        top_val = dict.__getitem__(top, key)
        base_val = dict.get(base, key)
        if isinstance(top_val, KeyStore) and isinstance(base_val, KeyStore):
            _overlay(base_val, top_val)
        else:
            dict.__setitem__(base, key, top_val)

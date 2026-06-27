"""Cascade level assembly — per-scope settings files → ordered KeyStore partials.

Block 2a of the KeyStore implementation. This module reads each cascade scope's
settings file(s) into ONE unified nested :class:`~kanibako.settings_store.KeyStore`
partial per level and returns the ordered ``list[KeyStore]`` the merge (block 2b)
consumes. It builds ALONGSIDE the live launch cascades (``commands/start.py``,
``config.py:load_settings``) — it modifies none of them; the swap is block 7.

It performs READS and structural parsing ONLY: NO merge / precedence, NO
``@``-ref / ``$var`` / ``~`` expansion or cycle detection, NO typed views, NO
``config set``. Tokens are left RAW inside binds (``files store UNRESOLVED``,
design §6a / spec §0).

Authority
---------
* ``~/vault/rw/keystore-design.md`` §4 (cascade — primary): the 7-level order
  ``base < system < agent.default < agent.<active> < workset < box < required``,
  high→low precedence; ``agent.default`` is an EXPLICIT level (B1) and both agent
  layers reuse the same linear precedence (no nested mini-cascade); the active
  agent's subtree is selected by name. §2 (storage — partials are ``KeyStore``s,
  binds are ``Bind``). §6a / spec §0 (files store UNRESOLVED — refs stay raw).
* Spec ``settings-keyspace-1.6.0-target.md`` §2 (cascade + scopes) / §2a
  (categories + value types) / §0 (namespace ORTHOGONAL to cascade).
* Keyspace audit 2026-06-27c #2: the ``machine`` (``/etc/kanibako.yaml``) tier is
  CUT — cascade floor is ``base`` (overridable), cap is ``required``
  (non-overridable). This module reads NO ``machine_config_path()``.

Seams realized here (``plans/keystore-blocks/SEAMS.md``)
-------------------------------------------------------
* **S7** — partials are NESTED ``KeyStore``s (not flat dotted dicts); a scope
  file's nested tables are mirrored verbatim into the partial.
* **S8** — output order is MOST-SPECIFIC-FIRST:
  ``[required, box, workset, agent.<active>, agent.default, system, base]``.
* **S9** — binds parsed to ``Bind`` at ASSEMBLY with ``@``-refs / ``$vars`` / ``~``
  left RAW inside ``host`` / ``box`` (expansion is block 3).
* **S13** — ONE unified ``KeyStore`` partial per level holding BOTH behavior
  leaves AND category subtrees together (design §1/§2 single-source).
* **S14** — no ``machine`` tier; floor → ``base``, cap → ``required``.

Keyspace convention — scope token KEPT (namespace orthogonal to cascade, §0)
---------------------------------------------------------------------------
Settings files are scope-ROOTED on disk (``config.py:_flatten_categories`` —
``{system: {bindings: {rw: {foo: …}}}}`` → ``system.bindings.rw.foo``). The scope
token is LOAD-BEARING (it picks the source-root + mount mode via ``scope_roots``)
and namespace is ORTHOGONAL to cascade level (§0): a ``box``-LEVEL file may set a
``system.*``-scoped key. So a level partial mirrors its file's WHOLE nested
content with the SCOPE TOKEN KEPT (``box.image``, ``system.caches.x``,
``agent.bindings.rw.x``) — the LEVEL identity is the FILE, not a lifted sub-table.
Block 2b then merges by the scope-qualified name across levels.

The AGENT tier is the one normalization (design §4 B1): the agent file nests
``agent.default.<key>`` and ``agent.<agent>.<key>``, with the active agent
falling back to default. The two become SEPARATE cascade LEVELS, and within each
the per-agent DISCRIMINATOR (``default`` / ``<active>``) is COLLAPSED to the bare
``agent`` scope token (``agent.bindings.rw.x``, ``agent.model``) — so the two
levels merge active-over-default BY NAME (the level order encodes the precedence)
and the key matches ``scope_roots`` (bare ``agent.bindings.ro`` …). Only the
discriminator collapses; the ``agent`` scope token is kept.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kanibako.config import settings_base_path, settings_required_path
from kanibako.config_io import load_doc
from kanibako.settings_resolve import unpack_bind
from kanibako.settings_store import Bind, KeyStore

# The bind-shaped categories whose leaf value is a structured pair/tuple
# ``[host_src, box_dest[, options]]`` (spec §2a "REPRESENTATION") — each is parsed
# to a :class:`Bind` at assembly (S9). ``masks`` (a keyed ``dict[box_dest →
# bool|None]``, S5) and ``env`` (scalar ``{VAR} → value``) keep their natural
# nested shape and are NOT bind-parsed. ``bindings`` carries the ``ro`` / ``rw``
# sub-tables, each of which holds bind leaves.
_BIND_CATEGORIES: frozenset[str] = frozenset(
    {"bindings", "caches", "seeded", "shared", "synced"}
)

# The agent sub-table that supplies the all-agents ``agent.default`` cascade level.
_AGENT_DEFAULT_SUB = "default"


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
    every NON-agent level (``base`` / ``system`` / ``workset`` / ``box`` /
    ``required``); the agent tier uses :func:`_agent_partial`.
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
    are kept SEPARATE cascade levels (design §4 B1; today's ``read_agent_settings``
    pre-merges them, which 2a deliberately does NOT).

    The per-agent DISCRIMINATOR (``default`` / ``<active>``) is COLLAPSED: the
    sub-table is re-rooted under the BARE ``agent`` scope token, so this level's
    keys are ``agent.<key>`` / ``agent.bindings.rw.x`` — matching ``scope_roots``
    and letting the two agent levels merge active-over-default by name (the level
    ORDER carries the precedence). A missing ``agent`` table, or a *sub_key* with
    no matching sub-table (e.g. an active agent absent from the file), → an empty
    :class:`KeyStore` level.
    """
    agent = raw.get("agent") if isinstance(raw, dict) else None
    if not isinstance(agent, dict):
        return KeyStore()
    sub = agent.get(sub_key)
    if not isinstance(sub, dict):
        return KeyStore()
    # Re-root the sub-table under the bare ``agent`` scope token (discriminator
    # dropped). _parse_node handles the bind/category structure inside.
    parsed_sub = _parse_node(sub, in_binds=False)
    store = KeyStore()
    store["agent"] = parsed_sub
    return store


def _floor_partial(floor: dict[str, object] | None) -> KeyStore:
    """Build the declared-default floor partial (folded into the ``base`` level).

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
    required_path: Path | None = None,
    floor: dict[str, object] | None = None,
) -> list[KeyStore]:
    """Read each cascade scope's settings file into ONE nested ``KeyStore`` partial
    and return the ordered ``list[KeyStore]`` (MOST-SPECIFIC-FIRST, S8).

    The 7 levels, in order::

        [required, box, workset, agent.<active>, agent.default, system, base]

    matching design §4's ``base < system < agent.default < agent.<active> <
    workset < box < required`` reversed to high→low precedence (block 2b walks
    this order; the first scope that SETS a leaf wins).

    Each non-agent level's partial = its file's WHOLE nested content, scope token
    KEPT (§0). The agent file yields BOTH agent levels via its ``default`` and
    ``<active>`` sub-tables, each re-rooted under the bare ``agent`` token (design
    §4 B1).

    * *agent_name* selects the active agent's sub-table for the ``agent.<active>``
      level; ``agent.default`` reads the ``default`` sub-table from the SAME file.
    * *base_path* / *required_path* default to ``settings_base_path()`` /
      ``settings_required_path()`` (``/etc`` floor / cap) — no ``machine`` tier
      (S14). These files use the SAME scoped keyspace as every other file (NOT a
      synthetic ``base:`` / ``required:`` wrapper).
    * *floor* (declared defaults + default-categories) is folded UNDER the base
      file's content into the ``base`` level — a base-FILE set-value beats the
      floor at the same key; the floor is the ultimate fallback.

    Binds are parsed to :class:`Bind`; ``@``-ref / ``$var`` / ``~`` tokens stay RAW
    (S9 / spec §0). Absent / unreadable files → an empty :class:`KeyStore` partial
    (skipped cleanly by the merge). NO ``machine`` path is consulted.
    """
    base_p = base_path if base_path is not None else settings_base_path()
    required_p = required_path if required_path is not None else settings_required_path()

    raw_base = load_doc(base_p)
    raw_system = load_doc(system_path)
    raw_agent = load_doc(agent_path)
    raw_workset = load_doc(workset_path)
    raw_box = load_doc(box_path)
    raw_required = load_doc(required_p)

    # The base partial carries the declared-default floor UNDER any base-file
    # content: a base-FILE set-value beats the floor at the same key (the floor is
    # the ultimate fallback). The floor is inserted first, then the file leaves
    # overlay, so a base-file entry wins WITHIN this single level.
    base_partial = _floor_partial(floor)
    _overlay(base_partial, _file_partial(raw_base))

    # MOST-SPECIFIC-FIRST (S8). Each scope file's partial keeps its scope token
    # so 2b merges by the scope-qualified name; the agent tier collapses its
    # default/<active> discriminator to the bare ``agent`` token.
    return [
        _file_partial(raw_required),
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
    subtrees so a deep base-file leaf (``agent.bindings.rw.x``) overlays the same
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

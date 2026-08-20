"""Cascade MERGE — the depth-sensitive per-name union of the ordered partials.

ONE pure function, :func:`merge`, walks :mod:`kanibako.settings.settings_assemble`'s
ordered ``list[KeyStore]`` partials (MOST-SPECIFIC-FIRST, S8) into ONE raw merged
snapshot. PURE: no file / env / clock access, and it NEVER mutates its input
partials (S15) — it builds a fresh :class:`~kanibako.settings.keystore.KeyStore`.

OUT of scope (hard boundaries): ``@``-ref / ``$var`` / ``~`` expansion and cycle
detection (refs stay RAW), cross-scope ``box_dest`` collision, typed views,
``config set``.

Authority (design §6e / §3 / §6f / §4 / §6g; spec §2 / §2a / §2c), the seams
S3 / S15 / S16, the cascade order and the full per-name case table:
``llm-docs/kanibako/settings/settings_merge.py.md``.
"""

from __future__ import annotations

from kanibako.settings.kb_store import StoreValue
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_assemble import _BIND_CATEGORIES

# The scope-category segment whose present-None leaf means UNMASK, not a scalar
# reset (S16 — ``masks`` is the one keyed bool|None category, S5).
_MASKS_SEGMENT = "masks"

# The top-level table holding ``pref.*`` REQUESTS (spec §2h). Its subtree is EXEMPT
# from the present-None type-split — see :func:`_resolve_present_none`. Spelled, not
# imported: ``settings_prefs`` imports the settings stack, which would cycle.
_PREF_ROOT = "pref"


def merge(levels: list[KeyStore]) -> KeyStore:
    """Merge the ordered cascade partials into ONE raw snapshot.

    *levels* is block 2a's ``list[KeyStore]``, MOST-SPECIFIC-FIRST (S8):
    ``[box, workset, agent.<active>, agent.default, system, base]`` — that order IS
    the precedence, and no level is treated specially. Full per-name case table:
    llm-doc. Empty *levels* → an empty snapshot.
    """
    return _merge_nodes(levels, path=())


def _is_set(level: KeyStore, name: str) -> bool:
    """Is *name* SET at this *level*? (S3 — the canonical presence probe.)

    ⚑ UNBOUND ``dict.get`` — NEVER the bound ``level.get`` (a key literally named
    ``get`` shadows the method into a crash), and NEVER truthiness (a leaf set to
    ``0`` / ``""`` / ``False`` / present-``None`` still SETS the name and must win).
    """
    return dict.get(level, name, __MISSING__) is not __MISSING__


def _names_in_order(levels: list[KeyStore]) -> list[str]:
    """Union of every name SET across *levels*, first-seen MOST-SPECIFIC order.

    Deterministic — part of purity.
    """
    seen: dict[str, None] = {}
    for level in levels:
        # dict.keys(level) — NOT level.keys() — collision-safe (S3 general rule).
        for name in dict.keys(level):
            if name not in seen:
                seen[name] = None
    return list(seen)


def _merge_nodes(
    levels: list[KeyStore],
    *,
    path: tuple[str, ...],
) -> KeyStore:
    """Merge co-located :class:`KeyStore` nodes (MOST-SPECIFIC-FIRST) into ONE fresh node.

    *path* is the scope-qualified segment trail to this node, for the §3 / S16
    type-split.
    """
    out = KeyStore()
    for name in _names_in_order(levels):
        # MOST-SPECIFIC-FIRST ⇒ setters[0] wins; non-empty (the name came from the
        # union of SET names).
        setters = [
            (level, dict.get(level, name)) for level in levels if _is_set(level, name)
        ]
        winning_val = setters[0][1]

        # ⚑ RECURSE on ANY subtree winner — not only when a LOWER setter is also a
        # subtree (§6e). A lone subtree winner must recurse too, or the §3
        # present-None type-split never reaches its leaves; the recursion is also
        # what makes the node fresh (S15). Reasoning in full: llm-doc.
        subtrees = [v for _lv, v in setters if isinstance(v, KeyStore)]
        if isinstance(winning_val, KeyStore):
            out[name] = _merge_nodes(
                subtrees,
                path=(*path, name),
            )
            continue

        if winning_val is None:
            kept = _resolve_present_none(path=(*path, name))
            if kept is _OMIT:
                continue  # bind/category/masks present-None → OMIT from snapshot.
            out[name] = None  # scalar present-None → KEEP None (consumer default).
            continue

        if isinstance(winning_val, list):
            out[name] = list(winning_val)  # fresh list — never ALIAS a partial (S15).
            continue
        out[name] = winning_val  # scalar / Bind — immutable, stored verbatim.
    return out


def _deep_copy_store(store: KeyStore) -> KeyStore:
    """Deep-copy a :class:`KeyStore` subtree into a fresh, non-aliasing tree (S15).

    Unbound ``dict`` iteration (S3) so a key named ``keys`` / ``items`` cannot
    shadow the protocol.
    """
    out = KeyStore()
    for key in dict.keys(store):
        val = dict.__getitem__(store, key)
        if isinstance(val, KeyStore):
            out[key] = _deep_copy_store(val)
        elif isinstance(val, list):
            out[key] = list(val)
        else:
            out[key] = val
    return out


class _Omit:
    """Sentinel: a present-``None`` leaf that must be OMITTED from the snapshot (§3).

    Never stored; distinct from :data:`__MISSING__` (absence) and from
    present-``None`` (a kept scalar reset).
    """

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "_OMIT"


_OMIT = _Omit()


def _resolve_present_none(*, path: tuple[str, ...]) -> StoreValue | _Omit:
    """Classify a present-``None`` leaf at *path* by CATEGORY (§3 type-split, S16).

    Keyed by PATH, the SAME ``_BIND_CATEGORIES`` / ``masks`` rule block 2a uses.
    :data:`_OMIT` for a bind / category / masks leaf; ``None`` for a scalar leaf. A
    leaf is a category leaf when EITHER an ANCESTOR segment is a category (an ENTRY
    reset) OR the leaf's OWN segment is one (a CATEGORY-ROOT reset).

    ⚑ **The ``pref`` subtree is EXEMPT — no classification at all.** A pref is a
    REQUEST, not a value (spec §2h) and its path MIRRORS its target's, so
    classifying it would delete the RECORD of a ``null`` request. That argument and
    the §5 reasoning behind the ROOT case are in the llm-doc.
    """
    if path and path[0] == _PREF_ROOT:
        return None  # a REQUEST record — kept verbatim, never classified (§2h).
    own = path[-1] if path else ""
    ancestors = path[:-1]
    if own in _BIND_CATEGORIES or own == _MASKS_SEGMENT:
        return _OMIT  # whole-category-root reset → drop the category.
    if any(seg in _BIND_CATEGORIES for seg in ancestors):
        return _OMIT  # an entry under a bind category (incl. bindings.ro/rw).
    if _MASKS_SEGMENT in ancestors:
        return _OMIT  # a masks path entry → unmask.
    return None

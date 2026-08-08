"""Cascade MERGE — the depth-sensitive per-name union of the ordered partials.

ONE pure function, :func:`merge`, walks :mod:`kanibako.settings.settings_assemble`'s ordered
``list[KeyStore]`` partials (MOST-SPECIFIC-FIRST, S8) and produces ONE raw merged
snapshot. It is PURE: no file / env / clock access, same input → same output, and
it NEVER mutates its input partials (S15) — it builds a fresh
:class:`~kanibako.settings.settings_store.KeyStore`.

It is the depth-sensitive successor to ``settings_resolve.resolve_value``'s
most-specific-first winner-take-all (which used ``""`` as a terminal + a separate
``defaults`` pass): here the 3-state ``_MISSING`` / present-``None`` model
replaces both (assembly already folded the floor INTO the ``base`` level, so there
is no separate defaults dict). ``resolve_value`` itself is NOT retired — it still
serves the ``config.*`` / ``system.*`` FOUNDATION path tier (``paths.py``); it is
the launch/settings cascade that no longer goes through it.

OUT of scope (hard boundaries): NO ``@``-ref / ``$var`` / ``~`` expansion or cycle
detection (:mod:`kanibako.settings.settings_expand` — refs stay RAW), NO
``reconcile_categories`` / ``box_dest`` collision logic (the SEPARATE downstream
pass, §6g — merge keys by NAME only), NO typed views
(:mod:`kanibako.settings.settings_views`), NO ``config set``
(:mod:`kanibako.settings.config_interface`).

Authority
---------
* ``~/vault/rw/keystore-design.md`` §6e (depth-sensitive merge — PRIMARY): per-NAME
  recursive union of subtrees; a LEAF (scalar / ``list`` / :class:`Bind`) is
  replaced WHOLE by the highest-precedence scope that SETS it; "sets" is tested
  with the ``_MISSING`` sentinel (NOT truthiness) at EVERY named leaf. **§3** (the
  3-state ``None`` + the present-``None`` TERMINAL type-split). **§6f** (``masks``
  rides the SAME generic dict-merge). **§4** (cascade order; the cascade ends at
  ``box`` — the former ``required`` cap is CUT, 2026-06-29f). **§6g** (merge keys
  by NAME, distinct from reconcile which keys by ``box_dest``).
* Spec ``settings-keyspace-1.8.0.md`` §2 (cascade — ends at ``box``)
  / §2a (the category list + per-name coexistence) / §2c.

Seams realized here (``plans/keystore-blocks/SEAMS.md``)
-------------------------------------------------------
* **S3** — presence is the UNBOUND ``dict.get(level, name, _MISSING) is not
  _MISSING`` probe, never the bound ``.get`` (a key named ``get`` would shadow the
  method into a crash) and never truthiness (a leaf set to ``0`` / ``""`` /
  ``False`` still SETS and wins).
* **S15** — the merge does NOT mutate its input partials; it builds a fresh tree.
* **S16** — category-awareness keys off the SAME ``_BIND_CATEGORIES`` / ``masks``
  segment rule block 2a uses (reused here, single-source — not re-derived).
"""

from __future__ import annotations

from kanibako.settings.settings_assemble import _BIND_CATEGORIES
from kanibako.settings.settings_store import _MISSING, KeyStore, StoreValue

# The scope-category segment whose present-None leaf means UNMASK (not a scalar
# reset). S16 — reuse 2a's category awareness; ``masks`` is the one keyed
# bool|None category (S5), the bind-shaped categories are ``_BIND_CATEGORIES``.
_MASKS_SEGMENT = "masks"

# The top-level table holding ``pref.*`` REQUESTS (spec §2h). Its subtree is
# EXEMPT from the present-None type-split — see :func:`_resolve_present_none`.
# Spelled here rather than imported from ``settings_prefs`` to keep this module's
# import surface at ``settings_assemble`` + ``settings_store`` (it is one fixed
# token, and ``settings_prefs`` imports the settings stack, which would cycle).
_PREF_ROOT = "pref"

# The agent tier keeps its ``default`` / ``<active-name>`` discriminator as the true
# §2d key (``agent.default.*`` / ``agent.<active-name>.*``) in 2a — the merge unions
# by that scope-qualified name like every other level. The cascade ends at ``box``
# (the former ``required`` non-overridable cap is CUT, 2026-06-29f): the
# most-specific level is ``box`` and the merge is a pure highest-precedence-wins
# union, no level is treated specially.


def merge(levels: list[KeyStore]) -> KeyStore:
    """Merge the ordered cascade partials into ONE raw snapshot.

    *levels* is block 2a's ``list[KeyStore]``, MOST-SPECIFIC-FIRST (S8):
    ``[box, workset, agent.<active>, agent.default, system, base]``.
    The merge walks names across the partials and, for each NAME at each depth,
    applies the depth-sensitive rule (§6e / §3):

    * **absent everywhere** (``_MISSING`` at every level) → not in the snapshot.
    * the highest-precedence setter's value AND a lower setter's value are both
      :class:`KeyStore` subtrees → RECURSE (per-name union of those subtrees); a
      lower NON-subtree value at the same name is shadowed by the higher subtree.
    * otherwise the **highest-precedence scope that SETS the name wins** (presence
      tested via the UNBOUND ``dict.get(level, name, _MISSING) is not _MISSING``,
      S3 — never truthiness, never the bound ``.get``); its leaf
      (scalar / ``list`` / :class:`Bind`) replaces any lower value atomically.
    * **present-None TERMINAL** (the type-split, §3, keyed by the name's CATEGORY
      via its PATH, S16): a present-``None`` winner SETS the name (clears lower
      scopes), and the snapshot result depends on the category —
      ``scalar leaf`` → KEEP ``None`` (the consumer applies its default);
      ``bind / category leaf`` → OMIT (no mount; build-4 tier-2 honesty, §5);
      ``masks leaf`` → OMIT (unmask that path; §6f);
      ``anything under the top-level ``pref`` table`` → KEEP verbatim, with NO
      classification at all — a pref is a REQUEST, not a value (spec §2h), and
      the request's path mirrors its target's, so classifying it would delete
      the RECORD of a ``null`` request. See :func:`_resolve_present_none`.

    Returns the merged ``snapshot``. PURE: no I/O; ``@``-ref / ``$var`` / ``~``
    tokens stay RAW (expansion is block 3); the input partials are NOT mutated
    (S15). An empty *levels* list → an empty snapshot.
    """
    return _merge_nodes(levels, path=())


def _is_set(level: KeyStore, name: str) -> bool:
    """Is *name* SET at this *level*? (S3 — the canonical presence probe.)

    Uses the UNBOUND ``dict.get(level, name, _MISSING)`` — NEVER the bound
    ``level.get`` (a key literally named ``get`` shadows the method into a crash)
    and NEVER truthiness (a leaf set to ``0`` / ``""`` / ``False`` / present-``None``
    still SETS the name and must win). Absent ⇒ ``_MISSING`` ⇒ ``False``.
    """
    return dict.get(level, name, _MISSING) is not _MISSING


def _names_in_order(levels: list[KeyStore]) -> list[str]:
    """The union of every name SET across *levels*, in first-seen MOST-SPECIFIC
    order (deterministic — purity). A name SET at several levels appears once, at
    its most-specific occurrence; iteration order within a level follows insertion
    (``dict`` order) so the same input always yields the same snapshot key order.
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
    """Merge a list of co-located :class:`KeyStore` nodes (MOST-SPECIFIC-FIRST) at
    *path* into ONE fresh node. *levels* are the same-position subtrees being
    unioned (the whole partial list at the root, or the KeyStore-valued levels at a
    name during recursion). *path* is the scope-qualified segment trail to this
    node (for the §3 / S16 category type-split).

    The cascade ends at ``box`` (no ``required`` cap, 2026-06-29f): every name is
    a pure highest-precedence-wins union, no level is treated specially.
    """
    out = KeyStore()
    for name in _names_in_order(levels):
        # Setters for this name, MOST-SPECIFIC-FIRST, paired with their value.
        setters = [
            (level, dict.get(level, name)) for level in levels if _is_set(level, name)
        ]
        # setters is non-empty (name came from the union of set names).
        winning_val = setters[0][1]

        # RECURSE whenever the winner is a subtree — ALWAYS, not only when a LOWER
        # setter is also a subtree (§6e). Recursing a LONE subtree winner too is what
        # carries the §3 present-None type-split into its leaves (a present-None
        # category/masks leaf under a subtree that ONLY ONE level sets must still be
        # OMITted — see _resolve_present_none). The recursion also yields a fresh,
        # non-aliasing node (S15), so it subsumes the old single-subtree deep-copy
        # branch. A lower non-subtree setter at this name is shadowed by the higher
        # subtree (filtered out of *subtrees*), unchanged.
        subtrees = [v for _lv, v in setters if isinstance(v, KeyStore)]
        if isinstance(winning_val, KeyStore):
            out[name] = _merge_nodes(
                subtrees,
                path=(*path, name),
            )
            continue

        # Apply the present-None type-split (§3) to a present-None leaf winner.
        if winning_val is None:
            kept = _resolve_present_none(path=(*path, name))
            if kept is _OMIT:
                continue  # bind/category/masks present-None → OMIT from snapshot.
            out[name] = None  # scalar present-None → KEEP None (consumer default).
            continue

        # A non-KeyStore leaf: immutable (Bind tuple / str / int / bool) or a fresh
        # ``list`` ref — the snapshot must never ALIAS an input partial (S15).
        if isinstance(winning_val, list):
            out[name] = list(winning_val)  # fresh list — never alias the input.
            continue
        out[name] = winning_val  # scalar / Bind — immutable, stored verbatim.
    return out


def _deep_copy_store(store: KeyStore) -> KeyStore:
    """Deep-copy a :class:`KeyStore` subtree into a fresh, non-aliasing tree (S15).

    Recurses into nested KeyStores and copies ``list`` leaves; immutable leaves
    (``Bind`` tuples, scalars, ``None``) are shared safely. Uses unbound ``dict``
    iteration (S3) so a key named ``keys`` / ``items`` cannot shadow the protocol.
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
    """Sentinel: a present-``None`` leaf that must be OMITTED from the snapshot
    (a bind / category / masks reset — no default to synthesize, §3). Module-private,
    never stored, distinct from :data:`_MISSING` (absence) and from present-``None``
    (a kept scalar reset).
    """

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "_OMIT"


_OMIT = _Omit()


def _resolve_present_none(*, path: tuple[str, ...]) -> StoreValue | _Omit:
    """Classify a present-``None`` leaf at *path* by CATEGORY (the §3 type-split,
    keyed by PATH per S16 — the SAME ``_BIND_CATEGORIES`` / ``masks`` segment rule
    block 2a uses). Returns :data:`_OMIT` for a bind / category / masks leaf (drop
    it — no mount / unmask) or ``None`` for a scalar leaf (keep it — consumer
    default).

    ⚑ **The ``pref`` subtree is EXEMPT — no classification at all.** A pref is a
    REQUEST, not a value (spec §2h), and the request's own path mirrors its
    TARGET's, so ``pref.agent.claude.common.<box_dest>`` carries ``common`` among
    its ancestors and the category rule below would OMIT it — deleting the
    RECORD of a ``null`` request from the snapshot while the request itself was
    applied, so ``config show`` / ``--effective`` could not show it. It is also
    the literal implementation of §2h's *"the pref layer MUST NOT interpret
    emptiness AT ALL"*: three idioms already exist downstream (present-``None``,
    terminal ``""``, the COPY-disable sentinel) and the pref layer must not
    become a fourth place deciding what "empty" means. The classification the
    spec DOES want happens at the INSTALLED target key, where the path is
    ``agent.claude.common.<box_dest>`` and the ordinary rule below applies. (⚑ The
    trailing segment is a DESTINATION, not a name — the four categories went
    TERMINAL and dest-keyed on 2026-08-08c, so ``.plugins``, which this note used
    to spell, is no longer a key segment anywhere.)

    *path* is the full segment trail to the leaf. A leaf is a category leaf when
    EITHER an ANCESTOR segment is a bind category / ``masks`` (an ENTRY reset like
    ``bindings.rw[/p] = None`` / ``common[~/x] = None`` / ``masks./p = None`` — the
    same "any ancestor is a category" test 2a's ``_insert_dotted`` uses) OR the
    leaf's OWN segment IS a category (a whole-CATEGORY-ROOT reset like
    ``bindings = None`` / ``caches = None`` / ``masks = None``).

    ⚑ BOTH RESET SPELLINGS SURVIVED THE 2026-08-08c DEST-KEY RETOOL WITH NO EDIT
    HERE, and that is the frozenset doing its job: ``_BIND_CATEGORIES`` already
    held all five tokens, and the ancestor test never cared whether the segment
    below a category was a NAME or a DESTINATION. The path shape changed; the
    classification did not. The root case keeps the §5 tier-2 coupling honest:
    a category accessor promises ``Mapping`` / ``set``, so a present-``None`` at the
    category root must OMIT (drop the whole category), never KEEP a bare ``None``
    where a mapping is contracted. Design §3 spells the ENTRY case; the ROOT case is
    its consistent extension (the design is silent on a whole-category reset — flag
    raised in chat). A non-category scalar leaf → KEEP ``None``.
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

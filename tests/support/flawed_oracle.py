"""FROZEN LEGACY BASELINE — the OLD by-name resolver, RETIRED because it was WRONG in cases. NOT a correctness authority. Used ONLY as a drift tripwire in the equivalence test. On divergence, adjudicate against the SPEC (reference/settings-keyspace-1.6.0-target.md), never in favor of this code.

This module holds the retired by-NAME LevelView category resolver
(:func:`flawed_oracle_categories`, formerly ``resolve_categories``) and its
key-shape predicate (:func:`flawed_oracle_is_category_key`, formerly
``is_category_key``), plus their module-private helpers.  It has NO product
caller — the launch + CLI paths resolve categories through the KeyStore snapshot
pipeline (``build_launch_snapshot`` → ``snapshot_category_entries`` →
``reconcile_categories``).  The code is preserved SOLELY as the drift tripwire the
equivalence test compares the live snapshot path against.

⚑ It is NOT an oracle of correctness: the snapshot path REPLACED this resolver
because this code was wrong in a number of cases.  A divergence between the two
therefore does NOT implicate the snapshot path — the OLD code here could be the
buggy side.  The adjudication authority is the SPEC
(``reference/settings-keyspace-1.6.0-target.md``), never this module.

The shared category primitives (``CategoryEntry``, the key regexes, the delivery
tables, ``_bind_options``) still LIVE in :mod:`kanibako.settings_categories`
alongside the live ``reconcile_categories``; this module imports them so the
frozen baseline stays byte-faithful to the retired logic without duplicating the
live building blocks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from kanibako.settings_categories import (
    BIND_KEY_RE,
    COPY,
    ENV,
    ENV_KEY_RE,
    MASK_KEY_RE,
    MOUNT,
    _bind_options,
    _DELIVERY,
    _DISABLE_SENTINEL,
    _SCOPE_APPLY_ORDER,
    CategoryEntry,
)
from kanibako.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    _Unset,
    expand_expr,
    resolve_value,
    unpack_bind,
)


def flawed_oracle_is_category_key(key: str) -> bool:
    """True if *key* is any scope-category key (one of the eight shapes)."""
    return (
        BIND_KEY_RE.match(key) is not None
        or MASK_KEY_RE.match(key) is not None
        or ENV_KEY_RE.match(key) is not None
    )


def _as_scalar(value: object) -> str:
    """Narrow a resolved ``env`` value to its scalar ``str`` form.

    The LOAD layer preserves structured category leaves (binding pair/tuple,
    ``masks`` list — spec §2a), so a resolved value is typed ``object``.  ``env``
    is the one remaining scalar category (its value is a plain VAR value); this
    narrows it to ``str`` (a real ``str`` passes through untouched).  Binding and
    ``masks`` leaves are unpacked structurally (:func:`unpack_bind` /
    :func:`_mask_dests`) and do NOT pass through here.
    """
    return value if isinstance(value, str) else str(value)


def _discover(levels: list[LevelView], pred: Callable[[str], bool]) -> set[str]:
    """Collect every key (values AND defaults, all levels) matching *pred*."""
    keys: set[str] = set()
    for level in levels:
        for key in level.values:
            if pred(key):
                keys.add(key)
        for key in level.defaults:
            if pred(key):
                keys.add(key)
    return keys


def _mask_dests(value: object) -> list[str]:
    """Narrow a resolved ``masks`` value to its real ``list[box_dest]``.

    Per spec §2a ``masks`` is a real ``list[box_dest]`` — the LOAD layer (P1)
    preserves a YAML list verbatim, so the common case is an actual ``list`` /
    ``tuple`` whose elements ARE the box-dest paths (each kept as-is, NOT
    re-derived from a flattened string — the old comma-string shim
    ``str()``-reprd a preserved list into garbage, the latent corruption this
    closes).  A bare scalar ``str`` (a single-mask config or in-code default) is
    a one-element list.  Empty / whitespace-only elements are dropped.
    """
    raw = value if isinstance(value, (list, tuple)) else [value]
    return [s for s in (str(e).strip() for e in raw) if s]


def flawed_oracle_categories(
    *,
    levels: list[LevelView],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
    scope_roots: Mapping[str, str] | None = None,
) -> list[CategoryEntry]:
    """Resolve the unified scope-category config into ordered entries.

    .. note::
       **FROZEN LEGACY BASELINE — NO product caller.** This is the OLD by-NAME
       LevelView-cascade resolver, RETIRED because it was WRONG in a number of
       cases.  The launch + CLI paths now resolve categories through the KeyStore
       snapshot pipeline (``build_launch_snapshot`` →
       ``snapshot_category_entries`` → :func:`reconcile_categories`).  This
       function is kept SOLELY as the drift TRIPWIRE that
       ``tests/test_settings_launch_equivalence.py`` compares the snapshot path
       against.  It is NOT a correctness authority: on a divergence the SPEC
       (``reference/settings-keyspace-1.6.0-target.md``) adjudicates, never this
       code (the OLD path here could be the buggy side).

    *levels* are MOST-SPECIFIC-FIRST (``[box, workset, agent, system, ...]``).
    *lookup* resolves ``@``-refs.  *scope_roots* maps a group prefix
    (``"{scope}.<category>"``) to a host-space root expression; absent/empty
    means no root join (bind-shaped categories only).

    Returns entries in apply order (see :mod:`kanibako.settings_categories`
    module docstring).  Does NOT resolve cross-category collisions (sub-step 4b).
    Raises :class:`SettingsError` if a non-suppressed bind value is not a
    structured 2-/3-element pair/tuple
    (:func:`~kanibako.settings_resolve.unpack_bind`).
    """
    entries: list[tuple[tuple[int, str, str], CategoryEntry]] = []

    # --- bind-shaped categories: bindings.{ro,rw} / caches / seeded / shared / synced
    for key in _discover(levels, lambda k: BIND_KEY_RE.match(k) is not None):
        m = BIND_KEY_RE.match(key)
        assert m is not None
        scope = m.group("scope")
        category = m.group("category")
        name = m.group("name")
        group = f"{scope}.{category}"
        delivery = _DELIVERY[category]

        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            continue
        if rv.terminal:
            # Explicit "" — suppressed.
            continue
        if delivery == COPY and rv.value == _DISABLE_SENTINEL:
            # "empty" sentinel disables a COPY (seed/synced) entry.
            continue

        # Structured unpack (spec §2a): a category binding value is a 2-/3-element
        # list/tuple, NOT a colon-string. The optional 3rd element is the
        # per-entry options override (``opts_override``): when PRESENT (a 3-element
        # entry — incl. an explicit empty string ``""``) it OVERRIDES the category
        # default for THIS entry; when absent (``None``, a 2-element entry) the
        # category default applies.
        try:
            host_src_raw, guest_dest_raw, opts_override = unpack_bind(rv.value)
        except SettingsError as exc:
            raise SettingsError(f"Category '{key}': {exc}") from exc

        host_src = expand_expr(host_src_raw, space="host", ctx=ctx, lookup=lookup)
        guest_dest = expand_expr(guest_dest_raw, space="guest", ctx=ctx, lookup=lookup)

        # Root join: only for a relative host_src under a group that has a root.
        root_expr = scope_roots.get(group) if scope_roots else None
        if root_expr and not host_src.startswith("/"):
            root = expand_expr(root_expr, space="host", ctx=ctx, lookup=lookup)
            host_src = f"{root.rstrip('/')}/{host_src}"

        # Per-entry options override (spec §2a): an explicit 3rd element wins for
        # this MOUNT entry (incl. an explicit ``""`` — no relabel); a 2-element
        # entry (``opts_override is None``) falls back to the category default.
        # COPY / ENV deliveries carry no mount flags (options stays ``""``).
        if delivery == MOUNT:
            options = opts_override if opts_override is not None else _bind_options(category)
        else:
            options = ""
        sort_key = (_SCOPE_APPLY_ORDER[scope], category, name)
        entries.append(
            (
                sort_key,
                CategoryEntry(
                    category=category,
                    scope=scope,
                    box_dest=guest_dest,
                    host_src=host_src,
                    delivery=delivery,
                    options=options,
                    name=name,
                ),
            )
        )

    # --- masks: {scope}.masks = list[box_dest] (tmpfs hide; no host source)
    for key in _discover(levels, lambda k: MASK_KEY_RE.match(k) is not None):
        m = MASK_KEY_RE.match(key)
        assert m is not None
        scope = m.group("scope")

        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset) or rv.terminal:
            continue
        for idx, raw_dest in enumerate(_mask_dests(rv.value)):
            box_dest = expand_expr(raw_dest, space="guest", ctx=ctx, lookup=lookup)
            sort_key = (_SCOPE_APPLY_ORDER[scope], "masks", f"{idx:04d}:{box_dest}")
            entries.append(
                (
                    sort_key,
                    CategoryEntry(
                        category="masks",
                        scope=scope,
                        box_dest=box_dest,
                        host_src=None,
                        delivery=MOUNT,
                        options="ro",
                        name=box_dest,
                    ),
                )
            )

    # --- env: {scope}.env.{VAR} = value (scalar; no host source, no guest path)
    for key in _discover(levels, lambda k: ENV_KEY_RE.match(k) is not None):
        m = ENV_KEY_RE.match(key)
        assert m is not None
        scope = m.group("scope")
        var = m.group("name")

        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset) or rv.terminal:
            continue
        value = expand_expr(_as_scalar(rv.value), space="guest", ctx=ctx, lookup=lookup)
        sort_key = (_SCOPE_APPLY_ORDER[scope], "env", var)
        entries.append(
            (
                sort_key,
                CategoryEntry(
                    category="env",
                    scope=scope,
                    box_dest=var,
                    host_src=None,
                    delivery=ENV,
                    options=value,
                    name=var,
                ),
            )
        )

    entries.sort(key=lambda pair: pair[0])
    return [entry for _, entry in entries]

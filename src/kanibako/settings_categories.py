"""Unified scope-category resolution (pure, additive).

This module GENERALIZES the (now-folded) ``settings_shares`` + ``settings_seeds``
resolvers into one *category* primitive.  Every path-delivery mechanism the
settings framework exposes at a scope is a CATEGORY; this module discovers the
category keys across the precedence levels, resolves each via the engine in
:mod:`kanibako.settings_resolve`, and emits one ordered ``list[CategoryEntry]``
tagged with its *delivery* (COPY vs MOUNT).  It is **pure**: no file I/O, no
mounting/copying, no global mutable state.  It imports only stdlib and the
expression engine.

It does NOT do cross-category collision resolution (identical-dest authority
order, depth-order, ``synced``↔``binding`` errors, the ``group_auth`` gate) —
that is sub-step 4b, layered on top of the entries this module produces.  Nor
does it rewire launch (``start.py``); the compatibility wrappers
:func:`resolve_shares` / :func:`resolve_seeds` (in their original modules) call
:func:`resolve_categories` and adapt the subset back to the old ``Mount`` /
``SeedPair`` shapes, so the launch path is byte-for-byte unchanged this step.

The eight categories
--------------------
Available at every scope ``{system, agent, workset, box}``:

============= ===================================== ============= =========
category      key shape                              host_src      delivery
============= ===================================== ============= =========
``masks``     ``{scope}.masks``  (list[box_dest])    ``None``      MOUNT
``bindings.ro``  ``{scope}.bindings.ro.{name}``      bind          MOUNT
``bindings.rw``  ``{scope}.bindings.rw.{name}``      bind          MOUNT
``caches``    ``{scope}.caches.{name}``              bind          MOUNT
``seeded``    ``{scope}.seeded.{name}``              bind          COPY
``shared``    ``{scope}.shared.{name}``              bind          MOUNT
``synced``    ``{scope}.synced.{name}``              bind          COPY
``env``       ``{scope}.env.{VAR}``  (value)         ``None``      ENV
============= ===================================== ============= =========

A "bind" value is a ``host_src:guest_dest`` expression (the engine's
:func:`split_bind`).  ``masks`` carries a list of guest paths to tmpfs-hide (no
host source); ``env`` carries a scalar value for ``{VAR}`` (no host source, no
guest *path* — its ``box_dest`` field is the VAR name).

Delivery
~~~~~~~~
* ``seeded`` and ``synced`` are file **COPIES** (synced creds inode-swap, which
  breaks single-file binds — they are copy-synced).
* ``caches``, ``bindings.ro``, ``bindings.rw``, ``shared``, ``masks`` are podman
  **MOUNTs** that physically shadow whatever is at the same path.
* ``env`` is neither — it is delivered as an environment variable (``ENV``).

Two orthogonal axes (unchanged from shares/seeds)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **The KEY's scope** selects the source root (via *scope_roots*) the relative
  ``host_src`` is joined under, and — for ``bindings`` — the mount mode.
* **The LEVEL where the key is SET** decides *precedence*.  A box may set a
  system-scoped key to a terminal ``""`` to suppress an inherited entry.

Accumulate / apply order
~~~~~~~~~~~~~~~~~~~~~~~~~~
Distinct ``(category, scope, name)`` entries accumulate.  For a single key, the
most-specific level that set it wins (:func:`resolve_value`).  Entries are
returned in scope *apply* order ``system, agent, workset, box`` (the REVERSE of
precedence) so the most-specific scope lands LAST — letting a later copy overlay
an earlier one and podman's "last ``-v`` wins" dedup honor box over system.
Within a scope they are ordered ``(category, name)`` ascending for determinism.
4b imposes the cross-category authority order on top of this.

Root-join rule
~~~~~~~~~~~~~~~
*scope_roots* maps a GROUP PREFIX (the key up to and including the category
token, e.g. ``"agent.bindings.rw"`` or ``"workset.shared"``) to a host-space
root expression.  When a root exists for a key's group AND the resolved
``host_src`` is not absolute, the source becomes ``root / host_src``; otherwise
``host_src`` is used as-is.  Groups absent from *scope_roots* mean no join.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from kanibako.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    _Unset,
    expand_expr,
    resolve_value,
    split_bind,
)

# Delivery tags.
Delivery = Literal["COPY", "MOUNT", "ENV"]
COPY: Final[Delivery] = "COPY"
MOUNT: Final[Delivery] = "MOUNT"
ENV: Final[Delivery] = "ENV"

# The bind-shaped categories (one ``{scope}.<category>.<name>`` key per entry,
# value is a ``host_src:guest_dest`` expression).  ``masks`` (a list) and
# ``env`` (a scalar) have bespoke key shapes handled separately below.
#
# NOTE the regex order: ``bindings.ro`` / ``bindings.rw`` must precede a bare
# ``bindings`` (there is none) and ``seeded``/``shared``/``synced`` are distinct
# tokens.  Listed longest-first so the alternation is unambiguous.
_BIND_CATEGORIES = ("bindings.ro", "bindings.rw", "caches", "seeded", "shared", "synced")

# delivery per category (the COPY/MOUNT split — design §3).
_DELIVERY: dict[str, Delivery] = {
    "masks": MOUNT,
    "bindings.ro": MOUNT,
    "bindings.rw": MOUNT,
    "caches": MOUNT,
    "seeded": COPY,
    "shared": MOUNT,
    "synced": COPY,
    "env": ENV,
}

# One regex for the bind-shaped categories: scope . <category> . name
# (name greedily captures the remainder, which may contain dots).
_CATEGORY_ALT = "|".join(c.replace(".", r"\.") for c in _BIND_CATEGORIES)
BIND_KEY_RE = re.compile(
    rf"^(?P<scope>system|agent|workset|box)\.(?P<category>{_CATEGORY_ALT})\.(?P<name>.+)$"
)
# ``{scope}.masks`` — value-less category (a list of box_dest paths). The KEY has
# no per-entry name; entries are expanded per list element (name = the index).
MASK_KEY_RE = re.compile(r"^(?P<scope>system|agent|workset|box)\.masks$")
# ``{scope}.env.{VAR}`` — scalar env var; VAR may NOT contain dots (env names).
ENV_KEY_RE = re.compile(
    r"^(?P<scope>system|agent|workset|box)\.env\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)$"
)

# Sentinel value that disables a COPY entry (parallels resolve_template's
# "empty"; preserved from the seed resolver).
_DISABLE_SENTINEL = "empty"

# Apply order: REVERSE of precedence (most-specific scope lands LAST).
_SCOPE_APPLY_ORDER = {"system": 0, "agent": 1, "workset": 2, "box": 3}


@dataclass(frozen=True)
class CategoryEntry:
    """One resolved scope-category entry (pre-collision-resolution).

    *category* is the category token (``"masks"``, ``"bindings.ro"``,
    ``"bindings.rw"``, ``"caches"``, ``"seeded"``, ``"shared"``, ``"synced"``,
    ``"env"``).  *scope* is the KEY's scope.  *box_dest* is the in-box
    destination (a guest path for path categories; the VAR name for ``env``).
    *host_src* is the resolved host source path, or ``None`` for value-only
    categories (``masks`` and ``env``).  *delivery* is COPY / MOUNT / ENV per the
    category.  *options* carries mount flags (``"ro"`` / ``"Z,U"``) for MOUNT
    entries (and ``env``'s VALUE for ``env`` entries — see below).  *name* is the
    ``<name>`` leaf for diagnostics.

    For ``env`` entries, *box_dest* is the variable NAME and *options* holds the
    resolved variable VALUE (env carries no path / mount flags).
    """

    category: str
    scope: str
    box_dest: str
    host_src: str | None
    delivery: Delivery
    options: str
    name: str


def is_category_key(key: str) -> bool:
    """True if *key* is any scope-category key (one of the eight shapes)."""
    return (
        BIND_KEY_RE.match(key) is not None
        or MASK_KEY_RE.match(key) is not None
        or ENV_KEY_RE.match(key) is not None
    )


def _bind_options(category: str) -> str:
    """Mount options for a bind-shaped MOUNT category.

    ``bindings.ro`` is read-only; every other rw bind category (``bindings.rw``,
    ``caches``, ``shared``) gets ``Z,U`` (SELinux relabel + userns chown), the
    same options the old ``share_rw`` mounts used.
    """
    return "ro" if category == "bindings.ro" else "Z,U"


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


def _split_into_list(value: str) -> list[str]:
    """Split a ``masks`` scalar into a list of box-dest paths.

    A masks value is rendered by ``_flatten_dotted`` from a YAML list; this
    module sees the already-flattened scalar.  Commas separate elements; a
    backslash escapes a comma.  Empty elements are dropped.  (A single path with
    no comma is one element.)
    """
    out: list[str] = []
    cur: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        c = value[i]
        if c == "\\" and i + 1 < n:
            cur.append(value[i + 1])
            i += 2
            continue
        if c == ",":
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    out.append("".join(cur))
    return [e for e in (s.strip() for s in out) if e]


def resolve_categories(
    *,
    levels: list[LevelView],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
    scope_roots: Mapping[str, str] | None = None,
) -> list[CategoryEntry]:
    """Resolve the unified scope-category config into ordered entries.

    *levels* are MOST-SPECIFIC-FIRST (``[box, workset, agent, system, ...]``).
    *lookup* resolves ``@``-refs.  *scope_roots* maps a group prefix
    (``"{scope}.<category>"``) to a host-space root expression; absent/empty
    means no root join (bind-shaped categories only).

    Returns entries in apply order (see module docstring).  Does NOT resolve
    cross-category collisions (sub-step 4b).  Raises :class:`SettingsError` if a
    non-suppressed bind value lacks a ``host_src:guest_dest`` colon.
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

        host_src_raw, guest_dest_raw = split_bind(rv.value)
        if guest_dest_raw is None:
            raise SettingsError(
                f"Category '{key}' must specify 'host_src:guest_dest' "
                f"(no unescaped ':' in value {rv.value!r})."
            )

        host_src = expand_expr(host_src_raw, space="host", ctx=ctx, lookup=lookup)
        guest_dest = expand_expr(guest_dest_raw, space="guest", ctx=ctx, lookup=lookup)

        # Root join: only for a relative host_src under a group that has a root.
        root_expr = scope_roots.get(group) if scope_roots else None
        if root_expr and not host_src.startswith("/"):
            root = expand_expr(root_expr, space="host", ctx=ctx, lookup=lookup)
            host_src = f"{root.rstrip('/')}/{host_src}"

        options = _bind_options(category) if delivery == MOUNT else ""
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
        for idx, raw_dest in enumerate(_split_into_list(rv.value)):
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
        value = expand_expr(rv.value, space="guest", ctx=ctx, lookup=lookup)
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

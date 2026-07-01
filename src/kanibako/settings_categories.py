"""Unified scope-category resolution (pure, additive).

This module GENERALIZES the (now-folded) ``settings_shares`` + ``settings_seeds``
resolvers into one *category* primitive.  Every path-delivery mechanism the
settings framework exposes at a scope is a CATEGORY; this module discovers the
category keys across the precedence levels, resolves each via the engine in
:mod:`kanibako.settings_resolve`, and emits one ordered ``list[CategoryEntry]``
tagged with its *delivery* (COPY vs MOUNT).  It is **pure**: no file I/O, no
mounting/copying, no global mutable state.  It imports only stdlib and the
expression engine.

Cross-category collision resolution (identical-dest authority order, depth-order,
``synced``↔``binding`` errors, the credential ``shares`` gate) is
:func:`reconcile_categories` (sub-step 4b), layered on top of category entries.

**Block 7c status:** :func:`reconcile_categories` is LIVE — the by-dest pass the
KeyStore snapshot path uses (fed by ``settings_launch.snapshot_category_entries``).
:func:`resolve_categories` (the OLD by-NAME LevelView resolver) is RETAINED
TEST-ONLY as the snapshot equivalence oracle and has no product caller; the
``settings_shares`` / ``settings_seeds`` wrapper modules it used to feed were
retired in 7c (the launch + ``workset share`` paths now resolve through the
snapshot pipeline). See :func:`resolve_categories`'s own note.

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

A "bind" value is a STRUCTURED pair/tuple ``[host_src, box_dest[, options]]``
unpacked by the engine's :func:`~kanibako.settings_resolve.unpack_bind` (spec
§2a — never a colon-joined string).  ``masks`` carries a list of guest paths to
tmpfs-hide (no host source); ``env`` carries a scalar value for ``{VAR}`` (no
host source, no guest *path* — its ``box_dest`` field is the VAR name).

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
    unpack_bind,
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

# Sentinel value that disables a COPY entry (the "empty" terminal, preserved
# from the seed resolver).
_DISABLE_SENTINEL = "empty"

# Apply order: REVERSE of precedence (most-specific scope lands LAST).
_SCOPE_APPLY_ORDER = {"system": 0, "agent": 1, "workset": 2, "box": 3}

# Category AUTHORITY order for an identical ``box_dest`` (D-B1; later WINS /
# applied-on-top): ``seed < cache < binding < shared < synced < masks``.
# ``bindings.ro`` and ``bindings.rw`` collapse to the single ``binding`` rank;
# ``seeded`` -> seed, ``caches`` -> cache.  ``env`` is not a path category (its
# "dest" is a VAR name, never a guest path) so it has no authority rank and never
# participates in box_dest collisions.
_CATEGORY_AUTHORITY: dict[str, int] = {
    "seeded": 0,        # seed
    "caches": 1,        # cache
    "bindings.ro": 2,   # binding
    "bindings.rw": 2,   # binding
    "shared": 3,        # shared
    "synced": 4,        # synced
    "masks": 5,         # masks
}


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

    *is_credential* tags an entry whose content is an agent CREDENTIAL.  It is the
    hook the credential ``shares`` gate (D-M4) keys off for ``seeded`` entries: a
    credential ``seeded`` copy is suppressed when the box is PRIVATE (``shares``
    False), exactly as ``synced`` (always credential-bearing) is.  Core never sets
    it; the agent
    plugin marks its cred seeds (Phase 8).  Defaults to False.
    """

    category: str
    scope: str
    box_dest: str
    host_src: str | None
    delivery: Delivery
    options: str
    name: str
    is_credential: bool = False


def is_category_key(key: str) -> bool:
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


def resolve_categories(
    *,
    levels: list[LevelView],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
    scope_roots: Mapping[str, str] | None = None,
) -> list[CategoryEntry]:
    """Resolve the unified scope-category config into ordered entries.

    .. note::
       **RETAINED TEST-ONLY (block 7c): this is the OLD by-NAME LevelView-cascade
       resolver and has NO product caller.** The launch + CLI paths now resolve
       categories through the KeyStore snapshot pipeline
       (``build_launch_snapshot`` → :func:`snapshot_category_entries` →
       :func:`reconcile_categories`). This function is kept solely as the
       EQUIVALENCE ORACLE that ``tests/test_settings_launch_equivalence.py``
       compares the snapshot path against (plus a few direct category unit tests).
       It is a DELIBERATE retained-for-verification exception to the "no live old
       resolver" gate, NOT a live path — a removal candidate once the snapshot
       swap is shipped and stable. (:func:`reconcile_categories`, by contrast, IS
       live — it is the by-dest pass the snapshot path uses.)

    *levels* are MOST-SPECIFIC-FIRST (``[box, workset, agent, system, ...]``).
    *lookup* resolves ``@``-refs.  *scope_roots* maps a group prefix
    (``"{scope}.<category>"``) to a host-space root expression; absent/empty
    means no root join (bind-shaped categories only).

    Returns entries in apply order (see module docstring).  Does NOT resolve
    cross-category collisions (sub-step 4b).  Raises :class:`SettingsError` if a
    non-suppressed bind value is not a structured 2-/3-element pair/tuple
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


@dataclass(frozen=True)
class ReconciledCategories:
    """The reconciled, emit-ready partition of category entries (sub-step 4b).

    *mounts* are the MOUNT-delivered winners (``caches``, ``bindings.{ro,rw}``,
    ``shared``, ``masks``), depth-sorted by ``box_dest`` path-depth ASCENDING
    (shallower first), so a later ``-v`` / podman's own depth-sort lands the most
    specific mount on top (mask-inside-``~/workspace``, ``home``-under-everything).
    *copies* are the COPY-delivered winners (``seeded``, ``synced``) in a
    deterministic order.  *envs* are the ENV entries (no box_dest collision —
    their "dest" is a VAR name), in deterministic order.

    Each path ``box_dest`` appears at most once across *mounts* + *copies*
    combined: identical-dest collisions were resolved by category authority.
    """

    mounts: list[CategoryEntry]
    copies: list[CategoryEntry]
    envs: list[CategoryEntry]


def _path_depth(box_dest: str) -> int:
    """Path-depth of a guest dest for the mount depth-sort (shallower first).

    Depth = number of non-empty path components.  ``~/`` / ``/`` is shallowest;
    ``~/workspace`` is deeper; ``~/workspace/vault`` deeper still.  Guest dests are
    already ``@``-expanded (``~`` -> ``/home/agent``) before reaching here.
    """
    return len([c for c in box_dest.strip("/").split("/") if c])


def reconcile_categories(
    entries: list[CategoryEntry],
    *,
    shares: bool = True,
) -> ReconciledCategories:
    """Resolve cross-category collisions and partition for emission (4b, D-B1).

    Takes the ordered ``list[CategoryEntry]`` from :func:`resolve_categories`
    (apply order, see that function) and returns a :class:`ReconciledCategories`
    with the per-dest winners split into MOUNT / COPY / ENV lists.

    Authority order (identical ``box_dest`` -> later WINS / applied-on-top):
    ``seed < cache < binding < shared < synced < masks`` (D-B1).  Among entries
    sharing a resolved ``box_dest`` the HIGHEST-authority category wins; ties
    WITHIN one authority rank are broken by :data:`_SCOPE_APPLY_ORDER` (box scope
    wins), then by the input order (stable) for full determinism.

    A ``synced`` (COPY) and a ``binding`` (MOUNT) naming the EXACT same
    ``box_dest`` is a CONFIG ERROR (a copy cannot override a live mount) — raised
    as :class:`~kanibako.errors.ConfigError`, never a silent no-op.

    The emitted MOUNT list is sorted by ``box_dest`` path-depth ASCENDING so
    podman's last-``-v``-wins/depth-sort resolves nested-but-different dests
    (mask-inside-``~/workspace``, ``home``-under-everything).  COPY and ENV lists
    keep a deterministic order.

    *shares* gates credential delivery (D-M4; auth-level design step 4): when
    False — the box is PRIVATE (auth tier ``"box"``, no shared source, today's
    distinct-auth) — every ``synced`` entry is SUPPRESSED, as is any ``seeded``
    entry flagged :attr:`CategoryEntry.is_credential` (the plugin's cred-seed
    hook).  When True (the box shares at the global OR workset tier) they are kept.
    The gate is applied BEFORE collision resolution, so a suppressed ``synced``
    cannot win — or error against — a colliding binding. (Callers pass
    ``shares=auth.shares`` off the resolved
    :class:`~kanibako.settings_launch.AuthSource`.)

    Raises :class:`~kanibako.errors.ConfigError` on a ``synced``↔``binding``
    identical-dest collision.
    """
    from kanibako.errors import ConfigError

    # --- share gate (D-M4): a PRIVATE box (shares=False) suppresses cred
    # deliveries up front — the same drop today's group_auth=False produced.
    gated: list[CategoryEntry] = []
    for e in entries:
        if not shares:
            if e.category == "synced":
                continue
            if e.category == "seeded" and e.is_credential:
                continue
        gated.append(e)

    # --- env entries never collide on a guest path; keep them aside (order kept).
    envs = [e for e in gated if e.delivery == ENV]
    path_entries = [e for e in gated if e.delivery != ENV]

    # --- group by resolved box_dest; pick the per-dest authority winner.
    # Preserve input order so ties beyond (authority, scope) are stable.
    by_dest: dict[str, list[CategoryEntry]] = {}
    for e in path_entries:
        by_dest.setdefault(e.box_dest, []).append(e)

    winners: list[CategoryEntry] = []
    for box_dest, group in by_dest.items():
        # synced (COPY) vs binding (MOUNT) at the EXACT same dest -> config error.
        has_synced = any(e.category == "synced" for e in group)
        has_binding = any(
            e.category in ("bindings.ro", "bindings.rw") for e in group
        )
        if has_synced and has_binding:
            raise ConfigError(
                f"Category collision at '{box_dest}': a 'synced' copy and a "
                f"'binding' mount target the same destination. A copy cannot "
                f"override a live mount — resolve by removing one (e.g. drop the "
                f"binding or point the synced copy elsewhere)."
            )
        # Highest authority wins; tie -> box scope wins (apply order); then the
        # stable input order. ``enumerate`` index keeps the original sequence.
        winner = max(
            enumerate(group),
            key=lambda pair: (
                _CATEGORY_AUTHORITY[pair[1].category],
                _SCOPE_APPLY_ORDER[pair[1].scope],
                pair[0],
            ),
        )[1]
        winners.append(winner)

    # --- partition winners by delivery; depth-sort the mounts (shallow first).
    mounts = [w for w in winners if w.delivery == MOUNT]
    copies = [w for w in winners if w.delivery == COPY]

    # MOUNT depth-sort: shallower box_dest first so the deepest (most specific)
    # mount lands LAST and wins. Stable tie-break by box_dest for determinism.
    mounts.sort(key=lambda e: (_path_depth(e.box_dest), e.box_dest))
    # COPY: deterministic by box_dest (no depth constraint).
    copies.sort(key=lambda e: e.box_dest)

    return ReconciledCategories(mounts=mounts, copies=copies, envs=envs)

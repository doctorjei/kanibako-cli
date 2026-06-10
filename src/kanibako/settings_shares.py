"""Scoped-share resolution (pure, additive).

This module turns settings-framework *scoped-share* config entries into a list
of :class:`~kanibako.targets.base.Mount` objects.  It is **pure**: no file I/O,
no global mutable state.  It imports only stdlib, the expression engine in
:mod:`kanibako.settings_resolve`, and the :class:`Mount` dataclass.  Wiring it
into container launch is a separate increment.

The share model
---------------
A share is declared with a key of the shape::

    {scope}.path.share_{ro|rw}.{name}

where ``scope`` is one of ``system``/``crab``/``workset``/``box``, the mode is
``ro`` or ``rw``, and ``name`` is an arbitrary identifier (it may itself contain
dots — everything after ``share_{ro,rw}.`` is the name).  The value is a
``host_src:guest_dest`` bind expression.

Two orthogonal axes
~~~~~~~~~~~~~~~~~~~~~
* **The KEY's scope** decides the *source root* the relative ``host_src`` is
  joined under (via *scope_roots*) and the *mount mode* (``ro`` → ``"ro"``;
  ``rw`` → ``"Z,U"``).
* **The LEVEL where the key is SET** decides *precedence*.  These differ on
  purpose: a box may set ``system.path.share_rw.foo = ""`` to **suppress** the
  system-scoped share ``foo`` for just that box (foreign-prefix suppression —
  an explicit ``""`` is terminal and produces no mount).

Accumulate / apply order
~~~~~~~~~~~~~~~~~~~~~~~~~~
Distinct share names accumulate.  For a SINGLE key, the most-specific level that
set it wins (standard :func:`resolve_value` precedence).  The returned mounts
are ordered by scope *apply* order ``system, crab, workset, box`` — the REVERSE
of the precedence list — so the most-specific scope comes LAST, letting
podman's "last ``-v`` wins" dedup honor box over system.  Within a scope, mounts
are ordered by ``(mode, name)`` ascending, for full determinism.

Root-join rule
~~~~~~~~~~~~~~~
*scope_roots* maps a GROUP PREFIX (the key up to and including
``share_ro``/``share_rw``, e.g. ``"crab.path.share_rw"``) to a host-space root
expression (e.g. ``"@system.path.crabs/$CRAB/share"``).  When a root exists for
a key's group AND the resolved ``host_src`` is NOT absolute, the source becomes
``root / host_src``; otherwise ``host_src`` is used as-is.  A group absent from
*scope_roots* (or mapped to ``None``/``""``) means no join — this is the ``box``
case, where ``host_src`` is an arbitrary host path.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path

from kanibako.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    _Unset,
    expand_expr,
    resolve_value,
    split_bind,
)
from kanibako.targets.base import Mount

# Matches a scoped-share key: scope . path . share_{ro|rw} . name
# (name greedily captures the remainder, which may contain dots).
_SHARE_KEY_RE = re.compile(
    r"^(?P<scope>system|crab|workset|box)\.path\.share_(?P<mode>ro|rw)\.(?P<name>.+)$"
)

# Apply order: REVERSE of precedence (most-specific scope mounts LAST so
# podman's last-`-v`-wins dedup honors it).
_SCOPE_APPLY_ORDER = {"system": 0, "crab": 1, "workset": 2, "box": 3}


def resolve_shares(
    *,
    levels: list[LevelView],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
    scope_roots: Mapping[str, str] | None = None,
) -> list[Mount]:
    """Resolve scoped-share config into a deterministic list of mounts.

    *levels* are ordered MOST-SPECIFIC-FIRST (``[box, workset, crab, system]``).
    *lookup* resolves ``@``-refs (typically a closure over *levels*).
    *scope_roots* maps a group prefix (``"{scope}.path.share_{ro,rw}"``) to a
    host-space root expression; absent/empty means no root join.  Returns mounts
    in apply order (see module docstring).  Raises :class:`SettingsError` if a
    non-suppressed share value lacks a ``host_src:guest_dest`` colon.
    """
    # 1. Discover share keys across every level's values AND defaults.
    keys: set[str] = set()
    for level in levels:
        for key in level.values:
            if _SHARE_KEY_RE.match(key):
                keys.add(key)
        for key in level.defaults:
            if _SHARE_KEY_RE.match(key):
                keys.add(key)

    mounts: list[tuple[tuple[int, str, str], Mount]] = []
    for key in keys:
        m = _SHARE_KEY_RE.match(key)
        assert m is not None  # keys were filtered by the same regex.
        scope = m.group("scope")
        mode = m.group("mode")
        name = m.group("name")
        group = f"{scope}.path.share_{mode}"

        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            # Defensive: the key came from some level, so it must resolve.
            continue
        if rv.terminal:
            # Explicit "" — suppressed; do not mount.
            continue

        host_src_raw, guest_dest_raw = split_bind(rv.value)
        if guest_dest_raw is None:
            raise SettingsError(
                f"Share '{key}' must specify 'host_src:guest_dest' "
                f"(no unescaped ':' in value {rv.value!r})."
            )

        host_src = expand_expr(
            host_src_raw, space="host", ctx=ctx, lookup=lookup,
        )
        guest_dest = expand_expr(
            guest_dest_raw, space="guest", ctx=ctx, lookup=lookup,
        )

        # Root join: only for relative host_src under a group that has a root.
        root_expr = scope_roots.get(group) if scope_roots else None
        if root_expr and not host_src.startswith("/"):
            root = expand_expr(root_expr, space="host", ctx=ctx, lookup=lookup)
            source = Path(root) / host_src
        else:
            source = Path(host_src)

        options = "ro" if mode == "ro" else "Z,U"
        sort_key = (_SCOPE_APPLY_ORDER[scope], mode, name)
        mounts.append((sort_key, Mount(source=source, destination=guest_dest, options=options)))

    mounts.sort(key=lambda pair: pair[0])
    return [mount for _, mount in mounts]

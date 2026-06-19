"""Scoped-binding resolution — COMPATIBILITY WRAPPER over the category model.

The scoped-share resolver was folded into the unified scope-category primitive
in :mod:`kanibako.settings_categories` (1.6.0 config-core revamp, sub-step 4a).
This module is now a thin compatibility layer: it filters the resolved
categories down to the ``bindings.{ro,rw}`` subset and adapts each entry back to
the :class:`~kanibako.targets.base.Mount` shape the launch path
(``start.py:_build_share_mounts``) still consumes.  ``start.py`` is rewired to
read categories directly in sub-step 4c; until then this wrapper keeps the
launch path byte-for-byte unchanged.

The "share" keyspace was renamed in the revamp::

    {scope}.path.share_ro.{name}  ->  {scope}.bindings.ro.{name}
    {scope}.path.share_rw.{name}  ->  {scope}.bindings.rw.{name}

The two orthogonal axes (the KEY's scope picks the source root + mount mode; the
LEVEL where set picks precedence) and terminal-``""`` suppression are unchanged —
they live in :mod:`kanibako.settings_categories` now.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from kanibako.settings_categories import (
    _SCOPE_APPLY_ORDER,  # noqa: F401  (re-exported for back-compat imports)
    resolve_categories,
)
from kanibako.settings_resolve import LevelView, ResolveCtx
from kanibako.targets.base import Mount

# Matches a scoped-binding key: scope . bindings . {ro|rw} . name
# (name greedily captures the remainder, which may contain dots).
SHARE_KEY_RE = re.compile(
    r"^(?P<scope>system|agent|workset|box)\.bindings\.(?P<mode>ro|rw)\.(?P<name>.+)$"
)


def is_share_key(key: str) -> bool:
    """True if *key* is a scoped-binding key ({scope}.bindings.{ro,rw}.{name})."""
    return SHARE_KEY_RE.match(key) is not None


def resolve_shares(
    *,
    levels: list[LevelView],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
    scope_roots: Mapping[str, str] | None = None,
) -> list[Mount]:
    """Resolve scoped-binding config into a deterministic list of mounts.

    COMPATIBILITY WRAPPER: delegates to
    :func:`kanibako.settings_categories.resolve_categories`, keeps only the
    ``bindings.{ro,rw}`` entries, and maps each to a :class:`Mount`.  The
    returned ordering, options (``ro`` / ``Z,U``), root-join, and suppression
    semantics match the pre-revamp resolver exactly.

    *scope_roots* maps a group prefix (``"{scope}.bindings.{ro,rw}"``) to a
    host-space root expression; absent/empty means no root join.
    """
    from pathlib import Path

    entries = resolve_categories(
        levels=levels, ctx=ctx, lookup=lookup, scope_roots=scope_roots
    )
    mounts: list[Mount] = []
    for e in entries:
        if e.category not in ("bindings.ro", "bindings.rw"):
            continue
        assert e.host_src is not None  # bindings always have a source.
        mounts.append(
            Mount(source=Path(e.host_src), destination=e.box_dest, options=e.options)
        )
    return mounts

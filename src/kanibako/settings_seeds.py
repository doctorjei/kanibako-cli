"""Copy-once-at-init seed resolution — COMPATIBILITY WRAPPER.

The seed resolver was folded into the unified scope-category primitive in
:mod:`kanibako.settings_categories` (1.6.0 config-core revamp, sub-step 4a).
This module is now a thin compatibility layer: it filters the resolved
categories down to the ``seeded`` subset and adapts each entry back to the
:class:`SeedPair` shape the launch path (``start.py:_apply_init_seeds``) still
consumes.  ``start.py`` is rewired to read categories directly in sub-step 4c;
until then this wrapper keeps the launch path byte-for-byte unchanged.

The seed keyspace was renamed in the revamp (``.path`` dropped)::

    {scope}.path.seeded.{name}  ->  {scope}.seeded.{name}

A seed is COPIED ONCE at box/agent init (the CALLER copies — this module is
pure).  Suppression via terminal ``""`` and the ``"empty"`` disable sentinel are
unchanged; they live in :mod:`kanibako.settings_categories` now.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from kanibako.settings_categories import (
    _SCOPE_APPLY_ORDER,  # noqa: F401  (re-exported for back-compat imports)
    resolve_categories,
)
from kanibako.settings_resolve import LevelView, ResolveCtx

# Matches a seed key: scope . seeded . name
# (name greedily captures the remainder, which may contain dots).
SEED_KEY_RE = re.compile(
    r"^(?P<scope>system|agent|workset|box)\.seeded\.(?P<name>.+)$"
)


def is_seed_key(key: str) -> bool:
    """True if *key* is a seed config key ({scope}.seeded.{name})."""
    return SEED_KEY_RE.match(key) is not None


@dataclass(frozen=True)
class SeedPair:
    """A resolved copy-once-at-init seed: copy host_src -> guest_dest (guest space)."""

    host_src: str       # resolved host path
    guest_dest: str     # resolved guest/container path (e.g. /home/agent/...)
    scope: str          # "system" | "agent" | "workset" | "box"
    name: str


def resolve_seeds(
    *,
    levels: list[LevelView],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
) -> list[SeedPair]:
    """Resolve seed config into a deterministic list of copy-pairs.

    COMPATIBILITY WRAPPER: delegates to
    :func:`kanibako.settings_categories.resolve_categories`, keeps only the
    ``seeded`` entries, and maps each to a :class:`SeedPair`.  Ordering and
    suppression semantics match the pre-revamp resolver exactly.
    """
    entries = resolve_categories(levels=levels, ctx=ctx, lookup=lookup)
    pairs: list[SeedPair] = []
    for e in entries:
        if e.category != "seeded":
            continue
        assert e.host_src is not None  # seeds always have a source.
        pairs.append(
            SeedPair(
                host_src=e.host_src,
                guest_dest=e.box_dest,
                scope=e.scope,
                name=e.name,
            )
        )
    return pairs

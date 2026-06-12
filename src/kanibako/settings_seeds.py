"""Copy-once-at-init seed resolution (pure, additive).

This module turns settings-framework *seed* config entries into a list of
``(host_src, guest_dest)`` copy-pairs.  A seed is copied ONCE at box/crab init
(the CALLER does the copy — this module is **pure**: no file I/O, no copying,
no global mutable state).  It imports only stdlib and the expression engine in
:mod:`kanibako.settings_resolve`.  Wiring it into init is a separate increment.

It is the simpler sibling of :mod:`kanibako.settings_shares`: seeds have **no
``ro``/``rw`` mode**, **no root-join** (``host_src`` is a full expression, not a
relative path joined under a scope root), and yield plain string pairs instead
of :class:`~kanibako.targets.base.Mount` objects.

The seed model
--------------
A seed is declared with a key of the shape::

    {scope}.path.seeded.{name}

where ``scope`` is one of ``system``/``crab``/``workset``/``box`` and ``name``
is an arbitrary identifier (it may itself contain dots — everything after
``seeded.`` is the name).  The value is a ``host_src:guest_dest`` bind
expression: ``host_src`` resolves in HOST space, ``guest_dest`` in GUEST space
(a container ``/home/agent/...`` path).

Two orthogonal axes
~~~~~~~~~~~~~~~~~~~~~
* **The KEY's scope** is informational (it documents the seed's *reach*).
* **The LEVEL where the key is SET** decides *precedence*.  These differ on
  purpose: a box may set ``system.path.seeded.foo = ""`` to **suppress** an
  inherited system-scoped seed for just that box, or override it with its own
  ``host_src:guest_dest``.

Suppression / disable
~~~~~~~~~~~~~~~~~~~~~~~
A seed is SKIPPED (no copy-pair emitted) when the winning value is either an
explicit terminal ``""`` (``rv.terminal``) or the sentinel string ``"empty"``
(mirroring the existing ``resolve_template`` "empty" convention).

Accumulate / apply order
~~~~~~~~~~~~~~~~~~~~~~~~~~
Distinct seed names accumulate.  For a SINGLE key, the most-specific level that
set it wins (standard :func:`resolve_value` precedence).  The returned pairs are
ordered by scope *apply* order ``system, crab, workset, box`` — the REVERSE of
the precedence list — so a later/more-specific scope's copy overlays an earlier
one.  Within a scope, pairs are ordered by ``name`` ascending, for full
determinism.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from kanibako.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    _Unset,
    expand_expr,
    resolve_value,
    split_bind,
)

# Matches a seed key: scope . path . seeded . name
# (name greedily captures the remainder, which may contain dots).
SEED_KEY_RE = re.compile(
    r"^(?P<scope>system|crab|workset|box)\.path\.seeded\.(?P<name>.+)$"
)

# Sentinel value that disables a seed (parallels resolve_template's "empty").
_DISABLE_SENTINEL = "empty"


def is_seed_key(key: str) -> bool:
    """True if *key* is a seed config key ({scope}.path.seeded.{name})."""
    return SEED_KEY_RE.match(key) is not None


# Apply order: REVERSE of precedence (most-specific scope copies LAST so a
# later copy overlays an earlier one).
_SCOPE_APPLY_ORDER = {"system": 0, "crab": 1, "workset": 2, "box": 3}


@dataclass(frozen=True)
class SeedPair:
    """A resolved copy-once-at-init seed: copy host_src -> guest_dest (guest space)."""

    host_src: str       # resolved host path
    guest_dest: str     # resolved guest/container path (e.g. /home/agent/...)
    scope: str          # "system" | "crab" | "workset" | "box"
    name: str


def resolve_seeds(
    *,
    levels: list[LevelView],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
) -> list[SeedPair]:
    """Resolve seed config into a deterministic list of copy-pairs.

    *levels* are ordered MOST-SPECIFIC-FIRST (``[box, workset, crab, system]``).
    *lookup* resolves ``@``-refs (typically a closure over *levels*).  Returns
    :class:`SeedPair` objects in apply order (see module docstring).  Raises
    :class:`SettingsError` if a non-suppressed seed value lacks a
    ``host_src:guest_dest`` colon.
    """
    # 1. Discover seed keys across every level's values AND defaults.
    keys: set[str] = set()
    for level in levels:
        for key in level.values:
            if SEED_KEY_RE.match(key):
                keys.add(key)
        for key in level.defaults:
            if SEED_KEY_RE.match(key):
                keys.add(key)

    pairs: list[tuple[tuple[int, str], SeedPair]] = []
    for key in keys:
        m = SEED_KEY_RE.match(key)
        assert m is not None  # keys were filtered by the same regex.
        scope = m.group("scope")
        name = m.group("name")

        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            # Defensive: the key came from some level, so it must resolve.
            continue
        if rv.terminal:
            # Explicit "" — suppressed; do not copy.
            continue
        if rv.value == _DISABLE_SENTINEL:
            # "empty" sentinel — disabled; do not copy.
            continue

        host_src_raw, guest_dest_raw = split_bind(rv.value)
        if guest_dest_raw is None:
            raise SettingsError(
                f"Seed '{key}' must specify 'host_src:guest_dest' "
                f"(no unescaped ':' in value {rv.value!r})."
            )

        host_src = expand_expr(host_src_raw, space="host", ctx=ctx, lookup=lookup)
        guest_dest = expand_expr(guest_dest_raw, space="guest", ctx=ctx, lookup=lookup)

        sort_key = (_SCOPE_APPLY_ORDER[scope], name)
        pairs.append(
            (sort_key, SeedPair(host_src=host_src, guest_dest=guest_dest, scope=scope, name=name))
        )

    pairs.sort(key=lambda pair: pair[0])
    return [seed for _, seed in pairs]

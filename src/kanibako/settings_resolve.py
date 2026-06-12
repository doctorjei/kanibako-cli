"""Settings-framework expression resolution engine (pure, additive).

This module is the single home for the settings-framework expression grammar:
precedence resolution, ``$VAR``/``~``/``@``-ref expansion, and ``host_src:
guest_dest`` bind splitting.  It operates ONLY on already-parsed nested data
(passed in as :class:`LevelView` objects and a lookup callback).  It performs
**no file I/O, no mounting, no global mutable state**, and imports neither
``config.py`` nor ``paths.py``.  It is intentionally format-agnostic — the
caller is responsible for parsing TOML/YAML into the simple mappings this
module consumes.

Precedence model (the design's "unset ≠ '' " distinction)
---------------------------------------------------------
:func:`resolve_value` walks the levels ordered MOST-SPECIFIC-FIRST
(``[box, workset, crab, system]``):

* **Explicit set values beat all declared defaults**, regardless of level.
* Among set values, the **most-specific** level wins.
* An explicit ``""`` is a **terminal suppression**: it wins at its level and
  does NOT fall through to a less-specific default (``terminal=True``).
* Among defaults (reached only when nothing is set), the most-specific
  (highest-authority) declared default wins.
* Nothing set and no default ⇒ :data:`UNSET`.

Expansion is a separate, explicit step (:func:`expand_expr`): the caller takes
the winning raw literal from :func:`resolve_value` and expands it in the
appropriate *space* (``"host"`` or ``"guest"``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from kanibako.errors import KanibakoError

GUEST_HOME = "/home/agent"
MAX_REF_DEPTH = 64

_VAR_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_REF_NAME_RE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")


class SettingsError(KanibakoError):
    """Raised on unknown variable, unresolvable/cyclic ``@``-ref, or depth-cap."""


class _Unset:
    """Sentinel type for "no value resolved" (distinct from an explicit ``""``)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


@dataclass(frozen=True)
class ResolveCtx:
    """Context for variable expansion.

    *xdg* maps XDG variable names (e.g. ``"XDG_DATA_HOME"``) to host paths.
    The dataclass is frozen; do not mutate *xdg* in place.
    """

    crab_name: str | None
    workset_name: str | None
    host_home: str
    xdg: dict[str, str]


@dataclass(frozen=True)
class LevelView:
    """A single precedence level's explicitly-set values and declared defaults.

    *name* is the level name (e.g. ``"box"``).  *values* holds values the user
    explicitly set at this level; *defaults* holds defaults declared at this
    level.
    """

    name: str
    values: Mapping[str, str]
    defaults: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedValue:
    """A resolved (but not yet expanded) value plus its provenance.

    *value* is the raw winning literal (``@``-refs/``$vars``/``~`` intact).
    *level* names the level that supplied it.  *is_default* is True when the
    value came from a declared default rather than an explicit set.  *terminal*
    is True when the winning value was an explicit ``""`` (a terminal
    suppression that does not fall through to defaults).
    """

    value: str
    level: str
    is_default: bool = False
    terminal: bool = False


def _unescape(s: str) -> str:
    """Resolve backslash escapes consistently.

    A backslash before any character yields that character literally
    (``\\:`` → ``:``, ``\\\\`` → ``\\``, ``\\x`` → ``x``).  A trailing lone
    backslash is kept literal.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            if i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            # Trailing lone backslash: keep literal.
            out.append("\\")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def split_bind(value: str) -> tuple[str, str | None]:
    """Split ``host_src:guest_dest`` into its two halves.

    Scans left-to-right; a backslash escapes the next character (so ``\\:`` is
    a literal colon, ``\\\\`` a literal backslash).  Splits at the FIRST
    UNESCAPED ``:``.  With no unescaped colon, returns ``(unescaped(value),
    None)`` for a plain scalar.  Each returned half has its escapes resolved.

    Linux container paths only — no Windows drive-letter / URI special-casing.
    Use ``\\:`` to embed a literal colon in either half.
    """
    i = 0
    n = len(value)
    while i < n:
        c = value[i]
        if c == "\\":
            # Skip the escaped character.
            i += 2
            continue
        if c == ":":
            host = _unescape(value[:i])
            guest = _unescape(value[i + 1 :])
            return host, guest
        i += 1
    return _unescape(value), None


def expand_expr(
    expr: str,
    *,
    space: Literal["host", "guest"],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
    chain: tuple[str, ...] = (),
) -> str:
    """Expand a single path/scalar expression (one bind half).

    Single left-to-right scan emitting literal vs expanded segments.  A
    substituted value is a LEAF — it is not re-scanned (prevents
    double-expansion / loops).

    Grammar:

    * **Escapes:** ``\\@``→``@``, ``\\$``→``$``, ``\\\\``→``\\``; a backslash
      before any other char yields that char literally.
    * **``~``:** ONLY when it is the FIRST character of *expr*.  Expands to
      ``ctx.host_home`` (``space=="host"``) or :data:`GUEST_HOME`
      (``space=="guest"``).  A ``~`` elsewhere is literal.
    * **``$VAR`` / ``${VAR}``:** name = ``[A-Za-z_][A-Za-z0-9_]*``.  ``CRAB`` →
      ``ctx.crab_name``, ``WORKSET`` → ``ctx.workset_name``, ``XDG_*`` →
      ``ctx.xdg[name]``.  Unknown names, or known names whose context value is
      ``None``/missing, raise :class:`SettingsError`.
    * **``@``-ref:** ``@`` then a dotted name ``[A-Za-z0-9_]+(\\.[...])*``.  The
      ref name ends at the first char outside that set.  Cycle-guarded against
      *chain* and capped at :data:`MAX_REF_DEPTH`.  Substitutes
      ``lookup(ref_name, chain + (ref_name,))``; the result is a leaf.
    """
    out: list[str] = []
    i = 0
    n = len(expr)

    # Leading ~ → home (only at position 0).
    if n > 0 and expr[0] == "~":
        out.append(ctx.host_home if space == "host" else GUEST_HOME)
        i = 1

    while i < n:
        c = expr[i]
        if c == "\\":
            if i + 1 < n:
                out.append(expr[i + 1])
                i += 2
                continue
            out.append("\\")
            i += 1
            continue
        if c == "$":
            seg, i = _expand_var(expr, i, ctx)
            out.append(seg)
            continue
        if c == "@":
            seg, i = _expand_ref(expr, i, lookup, chain)
            out.append(seg)
            continue
        out.append(c)
        i += 1

    return "".join(out)


def _expand_var(expr: str, i: int, ctx: ResolveCtx) -> tuple[str, int]:
    """Expand a ``$VAR`` or ``${VAR}`` starting at index *i* (the ``$``)."""
    n = len(expr)
    braced = i + 1 < n and expr[i + 1] == "{"
    name_start = i + 2 if braced else i + 1
    m = _VAR_NAME_RE.match(expr, name_start)
    if m is None:
        raise SettingsError(f"Malformed variable reference at: {expr[i:]!r}")
    name = m.group(0)
    end = m.end()
    if braced:
        if end >= n or expr[end] != "}":
            raise SettingsError(f"Unterminated ${{...}} reference: {expr[i:]!r}")
        end += 1
    return _resolve_var(name, ctx), end


def _resolve_var(name: str, ctx: ResolveCtx) -> str:
    """Resolve a variable name against the context namespace."""
    if name == "CRAB":
        if ctx.crab_name is None:
            raise SettingsError("Variable $CRAB is not set in this context.")
        return ctx.crab_name
    if name == "WORKSET":
        if ctx.workset_name is None:
            raise SettingsError("Variable $WORKSET is not set in this context.")
        return ctx.workset_name
    if name.startswith("XDG_"):
        if name not in ctx.xdg:
            raise SettingsError(f"Variable ${name} is not set in this context.")
        return ctx.xdg[name]
    raise SettingsError(f"Unknown variable: ${name}")


def _expand_ref(
    expr: str,
    i: int,
    lookup: Callable[[str, tuple[str, ...]], str],
    chain: tuple[str, ...],
) -> tuple[str, int]:
    """Expand an ``@ref`` starting at index *i* (the ``@``)."""
    m = _REF_NAME_RE.match(expr, i + 1)
    if m is None:
        raise SettingsError(f"Malformed @-reference at: {expr[i:]!r}")
    ref_name = m.group(0)
    end = m.end()
    if ref_name in chain:
        cycle = " -> ".join((*chain, ref_name))
        raise SettingsError(f"Cyclic @-reference: {cycle}")
    if len(chain) >= MAX_REF_DEPTH:
        raise SettingsError(
            f"@-reference depth cap ({MAX_REF_DEPTH}) exceeded resolving "
            f"'{ref_name}'."
        )
    return lookup(ref_name, (*chain, ref_name)), end


def resolve_value(
    key: str,
    *,
    levels: list[LevelView],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
) -> ResolvedValue | _Unset:
    """Resolve *key* by precedence over *levels* (most-specific first).

    Returns the raw winning literal (unexpanded) with provenance, or
    :data:`UNSET`.  Does NOT expand — the caller expands the result via
    :func:`expand_expr` with the appropriate *space*.  *ctx*/*lookup* are
    accepted for signature stability; the pure precedence logic does not use
    them.
    """
    del ctx, lookup  # accepted for signature stability; unused here.

    # Pass 1: explicit set values, most-specific first.
    for level in levels:
        if key in level.values:
            val = level.values[key]
            if val == "":
                return ResolvedValue(value="", level=level.name, terminal=True)
            return ResolvedValue(value=val, level=level.name)

    # Pass 2: declared defaults, most-specific first.
    for level in levels:
        if key in level.defaults:
            return ResolvedValue(
                value=level.defaults[key], level=level.name, is_default=True
            )

    return UNSET

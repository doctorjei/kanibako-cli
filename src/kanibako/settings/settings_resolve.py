"""Settings expression resolution: precedence, expansion, and the box-layout contract.

Terminology: a *space* (``"host"`` / ``"guest"``) is which side's home ``~`` expands to; a
*terminal* value is an explicit ``""`` suppression, distinct from :data:`UNSET`; a *name-keyed*
bind leaf is ``[host_src, box_dest[, opts]]`` and a *dest-keyed* one is ``[src[, opts]]``.

See ``llm-docs/kanibako/settings/settings_resolve.py.md``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

# ⚑ NOTHING FROM ``settings/`` MAY BE IMPORTED HERE — not ``config.py``, not ``paths.py``.
# This module is the bottom of the dependency order and they all import IT; a reverse edge is
# a cycle with no facade to hide in (every ``settings/__init__.py`` is import-free).
from kanibako.agent_ref import CANONICAL_SEP, SEGMENT_CHAR_CLASS
from kanibako.errors import KanibakoError

# Single source of truth for the box-side home; ``~`` in box-side destinations expands to it.
# ⚑ The transitive in-tree closure is ``agent_ref`` + ``errors`` and NOTHING else, which is what
# makes this constant safe to import from anywhere — including the agent plugin packages.
GUEST_HOME = "/home/agent"
# The image agent-user contract, alongside GUEST_HOME. Pure machinery, NOT a settings key;
# ``runtime.container.KEEP_ID_USERNS`` is built from these two ids.
GUEST_UID = 1000
GUEST_GID = 1000

# The GUEST workspace/vault leaves, in the two forms their consumers need: a RELPATH to join onto
# a host ``Path`` and an absolute to compare a box-side dest against.
# ⚑ GUEST-SIDE ONLY. ``paths_defaults.WORKSPACE_PATH`` is a HOST leaf spelled identically and the
# two are INDEPENDENT — this module may import NOTHING from ``settings/``, so a guest constant
# CANNOT be expressed in terms of a host one; the separation is structural, not conventional.
# 🛑 The values are DECLARED KEY NAMES in the closed keyspace (``box.bindings.rw[~/workspace]``,
# ``box.bindings.ro[~/vault/ro]``, ``box.bindings.rw[~/vault/rw]``); ``canonicalize_dest`` expands
# the ``~``. Respelling one redeclares three keys and hard-errors every existing user ``box.yaml``.
# ⚑ ONE PYTHON CARRIER, NOT ONE CARRIER: the five bundled ``containers/Containerfile.template-*``
# files each hardwire ``WORKDIR /home/agent/workspace`` and cannot import a Python constant.
GUEST_WORKSPACE_RELPATH = "workspace"
GUEST_VAULT_RELPATH = "vault"
GUEST_VAULT_RO_RELPATH = f"{GUEST_VAULT_RELPATH}/ro"
GUEST_VAULT_RW_RELPATH = f"{GUEST_VAULT_RELPATH}/rw"
GUEST_WORKSPACE = f"{GUEST_HOME}/{GUEST_WORKSPACE_RELPATH}"
GUEST_VAULT_RO = f"{GUEST_HOME}/{GUEST_VAULT_RO_RELPATH}"
GUEST_VAULT_RW = f"{GUEST_HOME}/{GUEST_VAULT_RW_RELPATH}"

# The FIXED box-side root for state that must be placed BEFORE the box is live (machinery, not a
# settings key); ``box_supervisor.project_pinned_xdg`` restores real XDG locations after boot.
# ⚑ HOME-RELATIVE by design — its three consumers anchor it differently, so a leading `~` or an
# absolute `/home/agent` would be wrong for two of the three.
# ⚑ NARROW — the resolve-before-liveness class ONLY; the HOST-side roots do not belong here.
BOX_PINNED_ROOT_RELPATH = ".kanibako"
#: The STATE facet of :data:`BOX_PINNED_ROOT_RELPATH`; further facets become further names here,
#: never a second mechanism.
BOX_PINNED_STATE_RELPATH = f"{BOX_PINNED_ROOT_RELPATH}/state"

MAX_REF_DEPTH = 64

_VAR_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# ⚑ COMPOSED FROM the agent-side charset, never restating it, so the subset relation holds BY
# CONSTRUCTION — a node-name is a key segment, and these two literals drifted TWICE when separate.
_REF_SEG = f"[{SEGMENT_CHAR_CLASS}{CANONICAL_SEP}]+"
_REF_NAME_RE = re.compile(rf"{_REF_SEG}(?:\.{_REF_SEG})*")


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
    """Context for variable expansion: ``$AGENT``/``$WORKSET``/``~``, ``$XDG_*``, ``@config.*``.

    ⚑ Frozen protects rebinding, not the dicts — do not mutate *xdg* / *config* in place.
    """

    agent_name: str | None
    workset_name: str | None
    host_home: str
    xdg: dict[str, str]
    config: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LevelView:
    """A single precedence level's explicitly-set *values* and declared *defaults*.

    ⚑ The two mappings stay SEPARATE — that split is what makes set-beats-default expressible.
    ⚑ ``object``, not ``str``: a CATEGORY value may be a structured leaf (spec §2a).
    """

    name: str
    values: Mapping[str, object]
    defaults: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedValue:
    """A resolved (but NOT yet expanded) raw literal plus its provenance.

    ⚑ ``terminal=True`` means SUPPRESSED, not "empty" — a consumer that substitutes its own
    fallback there has reintroduced the fall-through the flag exists to prevent.
    """

    value: object
    level: str
    is_default: bool = False
    terminal: bool = False


def _unescape(s: str) -> str:
    """Resolve backslash escapes: ``\\x`` → ``x`` for any *x*; a trailing lone backslash stays."""
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
    """Split ``host_src:guest_dest`` at the FIRST UNESCAPED ``:``; no colon ⇒ ``(value, None)``.

    ⚑ The CLI-input edge only — its one live consumer is ``workset share add``/``rm``.  Storage
    and the category-load path are pure structured and use the unpackers below.
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


def unpack_bind(value: object) -> tuple[str, str, str | None]:
    """Unpack a NAME-KEYED category bind leaf: 2 or 3 elements (spec §2a REPRESENTATION).

    ⚑ A malformed leaf is a configuration ERROR, never something to silently re-derive.
    """
    if not isinstance(value, (list, tuple)):
        raise SettingsError(
            f"Binding category value must be a structured pair/tuple "
            f"[host_src, box_dest[, options]], got {type(value).__name__}: "
            f"{value!r}."
        )
    if len(value) == 2:
        host_src, box_dest = value
        return str(host_src), str(box_dest), None
    if len(value) == 3:
        host_src, box_dest, options = value
        return str(host_src), str(box_dest), str(options)
    raise SettingsError(
        f"Binding category value must have 2 or 3 elements "
        f"[host_src, box_dest[, options]], got {len(value)}: {value!r}."
    )


def unpack_bind_entry(value: object) -> tuple[str, str | None]:
    """Unpack a DEST-KEYED bind entry leaf: 1 or 2 elements — the dest is the map KEY (R-3/R-6).

    ⚑ A BARE scalar (``{dest: src}``) is deliberately NOT accepted — one spelling per entry.
    ⚑⚑ NOT interchangeable with :func:`unpack_bind`, and the two must NEVER be chosen by ARITY:
    both take a 2-element list and the meanings are OPPOSITE.  Pick by the NODE, not the shape.
    """
    if not isinstance(value, (list, tuple)):
        raise SettingsError(
            f"Dest-keyed binding entry must be a structured entry "
            f"[src[, options]], got {type(value).__name__}: {value!r}."
        )
    if len(value) == 1:
        (src,) = value
        return str(src), None
    if len(value) == 2:
        src, options = value
        return str(src), str(options)
    raise SettingsError(
        f"Dest-keyed binding entry must have 1 or 2 elements "
        f"[src[, options]], got {len(value)}: {value!r}. (The DESTINATION is the "
        f"map key, not part of the entry.)"
    )


def normalize_bind_dest(dest: str) -> str:
    """Canonicalize a binding DESTINATION (leading ``~`` → GUEST_HOME, trailing ``/`` dropped).

    ⚑⚑ DESTINATIONS ONLY. NEVER CALL THIS ON A ``host_src`` — a dest is a GUEST path (fixed
    machinery); a source's ``~`` is the INVOKING USER's home and must stay UNRESOLVED.
    ⚑ IDEMPOTENT, and applied at every producer AND again on read, deliberately.
    ⚑ Everything else — including an ``@``-ref or ``$var`` dest — is carried VERBATIM, and
    nothing is REFUSED here; the RESOLVED-``box_dest`` collision check downstream stays.
    """
    out = dest
    if out == "~":
        return GUEST_HOME
    if out.startswith("~/"):
        out = GUEST_HOME + out[1:]
    if len(out) > 1 and out.endswith("/"):
        out = out.rstrip("/") or "/"
    return out


def match_var(expr: str, i: int) -> tuple[str, int]:
    """Parse ``$VAR`` / ``${VAR}`` at index *i*, returning ``(name, end_index)``.

    ⚑ PRECONDITION: ``expr[i] == "$"`` — NOT re-verified here; the caller has already dispatched.
    ⚑ THE SINGLE parser for the ``$`` family (three callers, two modules) — do not fork it.
    """
    n = len(expr)
    braced = i + 1 < n and expr[i + 1] == "{"
    name_start = i + 2 if braced else i + 1
    m = _VAR_NAME_RE.match(expr, name_start)
    if m is None:
        raise SettingsError(f"Malformed variable reference at: {expr[i:]!r}")
    end = m.end()
    if braced:
        if end >= n or expr[end] != "}":
            raise SettingsError(f"Unterminated ${{...}} reference: {expr[i:]!r}")
        end += 1
    return m.group(0), end


def match_ref(expr: str, i: int) -> tuple[str, int]:
    """Parse bare ``@a.b.c`` or braced ``@{a.b.c}`` at index *i*, returning ``(name, end_index)``.

    ⚑ PRECONDITION: ``expr[i] == "@"`` — NOT re-verified; stated because this is a CROSS-MODULE
    seam, so a new caller scanning for ``@`` differently must still hand over the ``@``'s index.
    ⚑ THE SINGLE parser for both spellings (three callers, three modules — seam S25).
    ⚑ NESTING IS NOT SUPPORTED and fails loudly: a substituted value is a LEAF, never re-scanned.
    """
    n = len(expr)
    braced = i + 1 < n and expr[i + 1] == "{"
    name_start = i + 2 if braced else i + 1
    m = _REF_NAME_RE.match(expr, name_start)
    if m is None:
        raise SettingsError(f"Malformed @-reference at: {expr[i:]!r}")
    end = m.end()
    if braced:
        if end >= n or expr[end] != "}":
            raise SettingsError(f"Unterminated @{{...}} reference: {expr[i:]!r}")
        end += 1
    return m.group(0), end


def expand_expr(
    expr: str,
    *,
    space: Literal["host", "guest"],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
    chain: tuple[str, ...] = (),
    defer_env: bool = False,
) -> str:
    """Expand one path/scalar expression (one bind half) in the named *space*, left to right.

    ⚑ A substituted value is a LEAF — never re-scanned, which is what prevents double-expansion.
    ⚑ ``~`` expands ONLY at position 0; elsewhere it is literal.
    ⚑ *defer_env* emits the ENVIRONMENT tokens (``~``, ``$VAR``) VERBATIM for a LATER resolver in
    a DIFFERENT environment; ``@``-refs (CONFIG) still expand here.
    """
    out: list[str] = []
    i = 0
    n = len(expr)

    # Leading ~ → home (only at position 0). Deferred (verbatim) when defer_env.
    if n > 0 and expr[0] == "~":
        if defer_env:
            out.append("~")
        else:
            out.append(ctx.host_home if space == "host" else GUEST_HOME)
        i = 1

    while i < n:
        c = expr[i]
        if c == "\\":
            if i + 1 < n:
                nxt = expr[i + 1]
                # ⚑ THE ESCAPE RULE INVERTS UNDER DEFERRAL: host-side ``\\x`` -> ``x``, but a
                # DEFERRED escape of an ENVIRONMENT-significant char is carried VERBATIM so the
                # BOX resolver still sees it.  ``\\@`` stays unescaped — this pass owns ``@``.
                if defer_env and nxt in ("$", "~", "\\"):
                    out.append("\\")
                    out.append(nxt)
                else:
                    out.append(nxt)
                i += 2
                continue
            out.append("\\")
            i += 1
            continue
        if c == "$":
            if defer_env:
                # Emit the $VAR / ${VAR} SOURCE SPAN verbatim, using the expander's own scan so
                # deferral and expansion agree on exactly where the token ends.
                seg, i = _scan_var_span(expr, i)
                out.append(seg)
                continue
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


def _scan_var_span(expr: str, i: int) -> tuple[str, int]:
    """Return the VERBATIM ``$VAR`` / ``${VAR}`` source span at *i* — the ``defer_env`` twin."""
    _name, end = match_var(expr, i)
    return expr[i:end], end


def _expand_var(expr: str, i: int, ctx: ResolveCtx) -> tuple[str, int]:
    """Expand a ``$VAR`` / ``${VAR}`` at index *i*; only the name RESOLUTION is this function's."""
    name, end = match_var(expr, i)
    return _resolve_var(name, ctx), end


def _resolve_var(name: str, ctx: ResolveCtx) -> str:
    """Resolve a variable name against the context namespace."""
    if name == "AGENT":
        if ctx.agent_name is None:
            raise SettingsError("Variable $AGENT is not set in this context.")
        return ctx.agent_name
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
    """Expand an ``@ref`` at index *i*: cycle guard, depth cap, ``lookup`` — spelling-agnostic."""
    ref_name, end = match_ref(expr, i)
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
    """Resolve *key* over *levels* (most-specific first); returns the RAW literal or :data:`UNSET`.

    ⚑ Does NOT expand — the caller expands via :func:`expand_expr` in the appropriate *space*.
    ⚑ NOT the settings cascade any more (that is ``settings_merge.merge``); this now serves the
    ``config.*`` / ``system.*`` FOUNDATION path tier, whose callers pass ONE level each.
    """
    del ctx, lookup  # accepted for signature stability; unused here.

    # ⚑ TWO FULL PASSES, and that is what makes SET BEAT DEFAULT AT ANY LEVEL: pass 1 exhausts
    # every level's values before pass 2 looks at any level's defaults.  Fusing them inverts it.
    # ⚑ ``val == ""`` (not falsiness) — an empty structured leaf must not read as terminal.
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


def _no_lookup(ref: str, chain: tuple[str, ...]) -> str:
    """Refusing ``@``-ref lookup: behavior settings are plain scalars and carry no cross-refs.

    ⚑ NO CALLERS — ``settings_launch`` defines and uses its own same-named twin (see the doc).
    """
    raise SettingsError(f"@-refs are not supported in behavior settings: {ref}")

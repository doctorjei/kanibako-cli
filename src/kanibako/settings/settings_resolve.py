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
(``[box, workset, agent, system]``):

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

from kanibako.agent_ref import CANONICAL_SEP, SEGMENT_CHAR_CLASS
from kanibako.errors import KanibakoError

# Single source of truth for the box-side home directory.  Boxes always run as
# the ``agent`` user with this home; ``~`` in box-side destinations expands to
# it.  This module has no in-tree imports beyond ``kanibako.errors`` (which
# imports nothing), so it is safe to import this constant from anywhere —
# including the agent plugin packages — without circular-import risk.  Every
# box-side ``/home/agent`` literal in the tree derives from this constant.
GUEST_HOME = "/home/agent"
# The image agent-user contract, alongside GUEST_HOME (the same contract's home
# half): every kanibako image creates the ``agent`` user with uid/gid 1000 and
# home ``/home/agent``.  Pure machinery, NOT a settings key — the images
# hardwire agent=1000, so no configuration could meaningfully differ.  The
# ``--userns=keep-id:uid=…,gid=…`` mapping in ``container.py`` derives from
# these so the calling host user always lands on the in-box agent user.
GUEST_UID = 1000
GUEST_GID = 1000

# The FIXED box-side root for state that CANNOT resolve without a live box but
# MUST be placed before the box is live.  Part of the same box-layout contract as
# GUEST_HOME above — machinery, NOT a settings key.
#
# The problem it solves: a mount destination goes into the container runtime's
# argument list before anything is running, and a `copy` category runs at
# `create` with no container at all — so a destination containing a `$XDG_*`
# token has to be resolved by the HOST, guessing at what the box would say.  The
# guess was maintained by hand across four separate resolvers, and they were
# already out of agreement (`start.py` derived the helper dest from
# `$XDG_STATE_HOME` while hardcoding the matching `mkdir` at `~/.local/state`).
# One fixed root deletes the whole class: it resolves identically on both sides,
# always.  XDG compliance is then restored PROPERLY, after boot, by projecting
# this root onto the box's real XDG locations
# (`kanibako.box_supervisor.project_pinned_xdg`) rather than by four resolvers
# agreeing.
#
# ⚑ HOME-RELATIVE by design: the three consumers anchor it differently — the box's
# own `$HOME` in-box, the box-home BIND SOURCE host-side (`proj.shell_path`), and a
# `~` expression in the defaults data — so a leading `~` or an absolute
# `/home/agent` would be wrong for two of the three.
# ⚑ NARROW: this is a compromise for the resolve-before-liveness class ONLY.  The
# HOST-side roots (`config.data`, `system.cache`, `system.runtime`) resolve eagerly
# with a real environment to read and do NOT belong here.
BOX_PINNED_ROOT_RELPATH = ".kanibako"
#: The STATE facet of :data:`BOX_PINNED_ROOT_RELPATH` — the pinned counterpart of
#: ``$XDG_STATE_HOME/kanibako``.  Further facets (cache, runtime, …) become further
#: names here, never a second mechanism.
BOX_PINNED_STATE_RELPATH = f"{BOX_PINNED_ROOT_RELPATH}/state"

MAX_REF_DEPTH = 64

_VAR_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# The ``@``-ref name grammar MUST admit every character an AGENT NODE-NAME can
# contain, because a node-name is a key segment (``meta.agent.<node>.…``,
# ``agent.<node>.…``).  ⚑ So it is COMPOSED FROM the agent-side charset rather
# than restating it: ``agent_ref.SEGMENT_CHAR_CLASS`` is exactly what
# ``agent_ref._is_segment_safe`` admits in one half of ``<persona>℘<harness>``,
# and this class is that plus the ``℘`` that joins the halves.  The subset
# relation therefore holds BY CONSTRUCTION.
#
# ⚑ That composition is the point.  These were two independent literals and they
# drifted TWICE — first missing ``℘``, then missing ``-`` — each time truncating
# the name mid-segment and leaving the rest as a literal suffix:
# ``@meta.agent.kimi-k3℘claude.auth.share_support`` parsed as the absent name
# ``meta.agent.kimi`` (rendered ``""``) plus the literal
# ``-k3℘claude.auth.share_support``, which then fed ``as_bool`` and crashed every
# such launch with "expected bool, got str" — and, at the ``@meta.agent.<a>.path``
# sites, silently produced a garbage path instead.  A third drift (non-ASCII
# personas, e.g. ``漢字℘claude``) was latent here until 2026-08-04.
#
# ``\w`` (inside ``SEGMENT_CHAR_CLASS``) is what admits letters and digits in any
# language.  It is still a NARROW class, not a catch-all: a broad ``[^\s@]+`` is
# REJECTED because it would swallow ``/`` and break embedded ``@config.data/...``
# path refs.  ``.`` is absent from BOTH sides — it is this grammar's own segment
# separator, which is why an agent name may not contain one (``agent_ref``
# rejects it at parse time).
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
    """Context for variable expansion.

    *xdg* maps XDG variable names (e.g. ``"XDG_DATA_HOME"``) to host paths.
    *config* is the Layer-1 CONFIG-key FOUNDATION (spec §1): the resolved
    ``config.*`` bootstrap paths keyed by their full dotted name (``config.data``,
    ``config.settings``, ``config.agents``, ``config.primary_workset``,
    ``config.registry``).  It is consulted by the ``@config.*`` ref route — config
    is NOT a settings cascade level (spec §1A / JC-2), it is a foundation injected
    here exactly the way ``$XDG_*`` is.  Empty when no foundation is available
    (behavior-only contexts where ``@config.*`` never appears).

    The dataclass is frozen; do not mutate *xdg* / *config* in place.
    """

    agent_name: str | None
    workset_name: str | None
    host_home: str
    xdg: dict[str, str]
    config: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LevelView:
    """A single precedence level's explicitly-set values and declared defaults.

    *name* is the level name (e.g. ``"box"``).  *values* holds values the user
    explicitly set at this level; *defaults* holds defaults declared at this
    level.

    A value is typically a scalar ``str`` (a behavior setting, an ``env`` value,
    or a legacy colon-string bind), but for the path-delivery CATEGORIES it may
    be a STRUCTURED leaf — a binding pair/tuple ``[host_src, box_dest[, opts]]``
    or a ``masks`` list (spec §2a; preserved at load by
    :func:`kanibako.settings.config.read_categories`).  Hence ``object``, not ``str``.
    The structural unpacking of those leaves lands on the resolve path in a
    later phase; the scalar behavior path is unchanged.
    """

    name: str
    values: Mapping[str, object]
    defaults: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedValue:
    """A resolved (but not yet expanded) value plus its provenance.

    *value* is the raw winning literal (``@``-refs/``$vars``/``~`` intact).
    *level* names the level that supplied it.  *is_default* is True when the
    value came from a declared default rather than an explicit set.  *terminal*
    is True when the winning value was an explicit ``""`` (a terminal
    suppression that does not fall through to defaults).

    *value* is ``object`` because a CATEGORY value may be a structured leaf (a
    binding pair/tuple, a ``masks`` list) preserved through load (spec §2a); the
    common case is a scalar ``str``.  Consumers that need a string narrow at
    their site (the structural unpacking of category leaves lands later).
    """

    value: object
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


def unpack_bind(value: object) -> tuple[str, str, str | None]:
    """Unpack a STRUCTURED category binding leaf into ``(host_src, box_dest, options)``.

    This is the structural successor to :func:`split_bind` on the CATEGORY path
    (spec §2a "REPRESENTATION"): a ``bindings.*``/``caches``/``seeded``/``shared``/
    ``synced`` value is a STRUCTURED PAIR — a YAML list / Python ``tuple`` — not a
    colon-joined ``"host:box"`` string.  *value* is therefore the list/tuple leaf
    that :func:`kanibako.settings.config.read_categories` preserves at load:

    * **2 elements** ``[host_src, box_dest]`` → ``(host_src, box_dest, None)``;
      the caller falls back to the category-default mount options.
    * **3 elements** ``[host_src, box_dest, options]`` → ``(host_src, box_dest,
      options)``; the explicit options string OVERRIDES the category default for
      this entry only (the spec's per-entry options channel — e.g. ``"z"`` /
      ``""`` for a live socket).

    Each element is narrowed to ``str`` (a YAML scalar may parse as ``int``/etc.).
    A non-list/tuple value, or a list/tuple of the wrong arity, raises
    :class:`SettingsError` — the structured shape is load-bearing and a malformed
    leaf is a configuration error, not something to silently re-derive.

    NOTE: :func:`split_bind` (the colon form) is NOT removed — it remains the
    parser for the CLI-INPUT edge (``config set k=h:b[:opts]`` mirroring podman
    ``-v``).  Storage / the category-load path is pure structured and uses THIS.
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
    """Unpack a DEST-KEYED binding entry leaf into ``(src, options)``.

    The dest-keyed sibling of :func:`unpack_bind` (disk-store rework R-3/R-6).
    Where a name-keyed arm stores ``{name: [host_src, box_dest[, opts]]}``, a
    dest-keyed arm stores ``{box_dest: [src[, opts]]}`` — the destination is the
    mapping KEY, so the VALUE carries one element fewer:

    * **1 element** ``[src]`` → ``(src, None)``; the caller falls back to the
      category-default mount options.
    * **2 elements** ``[src, options]`` → ``(src, options)``; the explicit
      options string OVERRIDES the category default for this entry only.

    Each element is narrowed to ``str`` (a YAML scalar may parse as ``int``/etc.).
    A non-list/tuple value, or a list/tuple of the wrong arity, raises
    :class:`SettingsError`. ⚑ A BARE scalar (``{dest: src}``) is deliberately NOT
    accepted: the ruled shape is 1-or-2 ELEMENTS, the exact transposition of
    today's 2-or-3 rule, and admitting a second spelling of the same entry is
    precisely the duplicate-form confusion CONVENTIONS §0 opens with.

    ⚑⚑ **This function is NOT interchangeable with :func:`unpack_bind`, and the
    two must never be chosen by ARITY.** Both accept a 2-element list, and the
    meanings are opposite — ``[a, b]`` is ``(host, box)`` here and ``(src,
    opts)`` there. The caller picks the unpacker from the NODE the value came
    from (a name-keyed bind node vs a dest-keyed arm), never from the value's
    shape; see :class:`~kanibako.settings.kb_store.BindEntry` for the full
    rule. During the P5→P8 bridge both node shapes exist, so both unpackers are
    live; P8 deletes :func:`unpack_bind` and its shape.
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
    """Canonicalize a binding DESTINATION — the map key of a dest-keyed arm (R-11).

    ⚑⚑ **DESTINATIONS ONLY. NEVER CALL THIS ON A ``host_src``.** The asymmetry is
    the whole ruling (spec §2a, "THE DESTINATION IS CANONICALIZED; THE SOURCE IS
    NOT"): a dest is a GUEST path and the guest home is FIXED MACHINERY
    (:data:`GUEST_HOME`, not a settings key), so expanding it yields the same
    absolute path on every host, forever. A ``host_src``'s ``~`` is the INVOKING
    USER's home — absolutizing THAT would bake one machine's ``/home/<user>`` into
    a settings file shared across users and machines (§0 "files store entries
    UNRESOLVED").

    Two normalizations, both of them identity-preserving for an already-canonical
    dest (this function is IDEMPOTENT, and is applied at every producer AND again
    on read, deliberately):

    * a LEADING ``~`` expands to :data:`GUEST_HOME` (``~`` → ``/home/agent``,
      ``~/x`` → ``/home/agent/x``). A ``~`` anywhere else is literal, exactly as
      :func:`expand_expr` treats it;
    * a TRAILING ``/`` is dropped (never from a bare ``/``). This half is not
      cosmetic: ``~`` and ``~/`` were the two spellings that, under dest-keying,
      became two dict entries at ONE destination — the collision that triggered
      R-11 in the first place.

    ⚑ Everything else is carried VERBATIM, including an ``@``-ref or ``$var``
    destination. Those cannot be absolutized here — they resolve later, in
    ``settings_expand`` — so a dest-keyed arm is unique in the UNRESOLVED namespace
    only and the RESOLVED-``box_dest`` collision check in ``settings_categories``
    stays (design §2b-CAVEAT). Nothing is REFUSED here either: a non-absolute
    literal dest is a spec violation, but refusing it is not R-11's job and a
    refusal keyed on "does not start with /" would fire on every legitimate
    ``@``-ref dest.
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
    """Parse the ``$VAR`` / ``${VAR}`` reference starting at index *i* (the ``$``).

    Returns ``(var_name, end_index)`` — the name WITHOUT its ``$`` (and without
    the braces, when braced), plus the index one past the whole token.  Raises
    :class:`SettingsError` on a malformed reference.

    ⚑ PRECONDITION: ``expr[i] == "$"``.  The caller has already dispatched on that
    character; this function does NOT re-verify it and will happily parse a name
    starting at ``i + 1`` regardless of what ``expr[i]`` actually is.

    The SINGLE parser for the ``$`` token family, exactly as :func:`match_ref` is
    for ``@``: shared by :func:`_expand_var` (which resolves the name),
    :func:`_scan_var_span` (which re-emits the source span for ``defer_env``) and
    ``settings_configset._scan_tokens`` (which collects names for set-time
    validation).  Those three carried THREE copies of this ten-line parse before;
    one grammar, one copy, so a change to the token shape cannot land in some of
    them and not others.
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
    """Parse the ``@``-reference starting at index *i* (the ``@``).

    Returns ``(ref_name, end_index)`` — the dotted name WITHOUT its ``@`` (and
    without the braces, when braced), plus the index one past the whole token.
    Raises :class:`SettingsError` on a malformed reference.

    ⚑ PRECONDITION: ``expr[i] == "@"``.  Every caller dispatches on that character
    before calling, and this function does NOT re-verify it — ``match_ref("xa.b",
    0)`` returns ``("a.b", 4)`` rather than raising.  It is stated because this is
    a cross-module seam, not a file-private helper: a new caller that scans for
    ``@`` differently must still hand over the index OF the ``@``.

    TWO SPELLINGS, one meaning:

    * **BARE** ``@a.b.c`` — the dotted name runs greedily and ends at the first
      character outside the name set (``@a.b/x`` refs ``a.b``).  This is the
      NORMAL spelling and is unchanged.
    * **BRACED** ``@{a.b.c}`` — the same name, delimited explicitly, so a LITERAL
      SUFFIX may follow it: ``@{a.b.c}x`` resolves ``a.b.c`` then appends ``x``.
      Braces are needed ONLY where the next character would otherwise be eaten by
      the greedy name match — the spec's
      ``@workset.logs/@{meta.box.name}.jsonl`` is exactly that case, since bare
      ``@meta.box.name.jsonl`` parses as the single (absent) name
      ``meta.box.name.jsonl`` and silently loses both the box name and the
      extension.  Where both forms are expressible they resolve identically.

    This is the SINGLE parser for both spellings, shared by the scanner
    (:func:`_expand_ref`), the whole-value shape test
    (``settings_expand._is_whole_value_ref``) and set-time validation
    (``settings_configset._scan_tokens``) — one grammar, not three (seam S25).

    The braced form deliberately MIRRORS ``${...}`` (:func:`_expand_var` /
    :func:`_scan_var_span`): optional brace, the same name regex, a required
    closing brace, and a distinct "unterminated" error.  Same idea, same
    spelling, so the two token families cannot drift apart.  NESTING IS NOT
    SUPPORTED and fails loudly (``@{a@{b}}`` is unterminated): a substituted
    value is a LEAF and is never re-scanned, so a nested form would imply a
    second, contradictory model of when expansion happens.
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
    * **``$VAR`` / ``${VAR}``:** name = ``[A-Za-z_][A-Za-z0-9_]*``.  ``AGENT`` →
      ``ctx.agent_name``, ``WORKSET`` → ``ctx.workset_name``, ``XDG_*`` →
      ``ctx.xdg[name]``.  Unknown names, or known names whose context value is
      ``None``/missing, raise :class:`SettingsError`.
    * **``@``-ref:** two spellings, parsed by :func:`match_ref` and resolved
      IDENTICALLY.  BARE ``@a.b.c`` — ``@`` then a dotted name
      ``[A-Za-z0-9_]+(\\.[...])*`` that ends at the first char outside that set.
      BRACED ``@{a.b.c}`` — the same name delimited explicitly, so a LITERAL
      SUFFIX may follow (``@{a.b.c}.jsonl``, which the bare form cannot express
      because the name match would swallow ``.jsonl``).  Braces are needed only
      there; ``@a.b.c`` stays the normal spelling.  Either way: cycle-guarded
      against *chain*, capped at :data:`MAX_REF_DEPTH`, and substitutes
      ``lookup(ref_name, chain + (ref_name,))``; the result is a leaf.

    *defer_env* (default ``False`` — existing callers are byte-for-byte
    unaffected): when ``True``, the ENVIRONMENT tokens ``~`` and ``$VAR`` /
    ``${VAR}`` are NOT expanded — they are emitted VERBATIM (the exact source
    span, ``${...}`` braces included), to be resolved later in a DIFFERENT
    environment.  ``@``-refs (CONFIG) still expand normally, and escapes are
    still honored.  This is the box-side deferral of the KeyStore expansion pass
    (design §6a: ``$XDG``/``~`` name the box environment, host ≠ box, so a
    ``box_dest`` env token stays deferred — block 3 / S17).  It is additive: with
    ``defer_env=False`` the scan is identical to before.
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
                # ESCAPES. Host-side: ``\\x`` -> ``x`` (consume the backslash).
                # Deferred (box-side): an escape of an ENVIRONMENT-significant char
                # (``$`` / ``~`` / ``\\``) is carried VERBATIM, because the BOX
                # resolver — not this host pass — re-scans for ``$VAR`` / ``~`` and
                # must see the same escape to honor the user's "literal, NOT a var"
                # intent (else ``\\$`` -> ``$`` would be re-expanded box-side,
                # defeating the escape). ``\\@`` is still unescaped to ``@``: this
                # pass OWNS ``@``-refs on BOTH sides, the box never processes ``@``,
                # so a literal ``@`` is the correct box-side residue. (Spec-silent
                # box-side escape contract — flagged to the director.)
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
                # Emit the $VAR / ${VAR} span VERBATIM (deferred to box-side). Use
                # the same name scan as the expander so we know exactly where the
                # token ends — but emit the source span, not a resolved value.
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
    """Return the VERBATIM ``$VAR`` / ``${VAR}`` source span starting at *i* (the
    ``$``) plus the index past it — for the ``defer_env`` box-side deferral.

    Recognizes the SAME token shape :func:`_expand_var` resolves (so deferral and
    expansion agree on token boundaries), but emits the source text unchanged
    (``$XDG_STATE_HOME`` / ``${XDG_STATE_HOME}``) rather than a resolved value. A
    malformed reference raises identically to :func:`_expand_var` — a deferred
    token must still be well-formed at build, just not resolved here — because
    both delegate the parse to :func:`match_var`; "identically" is now structural
    rather than two copies that happen to agree.
    """
    _name, end = match_var(expr, i)
    return expr[i:end], end


def _expand_var(expr: str, i: int, ctx: ResolveCtx) -> tuple[str, int]:
    """Expand a ``$VAR`` or ``${VAR}`` starting at index *i* (the ``$``).

    Parses via the shared :func:`match_var`; only the RESOLUTION of the name is
    this function's own (the ``defer_env`` twin :func:`_scan_var_span` re-emits
    the source span instead, from the same parse).
    """
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
    """Expand an ``@ref`` starting at index *i* (the ``@``).

    Both spellings (bare ``@a.b.c`` and braced ``@{a.b.c}``) are parsed by the
    shared :func:`match_ref`; everything below — the cycle guard, the depth cap,
    the ``lookup`` substitution — is spelling-agnostic and identical for the two.
    """
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


def _no_lookup(ref: str, chain: tuple[str, ...]) -> str:
    """Default ``@``-ref lookup: behavior settings carry no cross-refs.

    Behavior settings are plain scalars (model, bootstrap, autonomy, …) used
    verbatim — there is no ``@``-ref expansion in this tier, so any ``@``-ref is
    a configuration error.
    """
    raise SettingsError(f"@-refs are not supported in behavior settings: {ref}")

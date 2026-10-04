"""Settings expression resolution: precedence, expansion, and the box-layout contract.

Terminology: a *space* (``"host"`` / ``"guest"``) is which side's home ``~`` expands to; a
*terminal* value is an explicit ``""`` suppression, distinct from :data:`UNSET`; a *name-keyed*
bind leaf is ``[host_src, box_dest[, opts]]`` and a *dest-keyed* one is ``[src[, opts]]``.

See ``llm-docs/kanibako/settings/settings_resolve.py.md``.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

# ⚑⚑ THE IMPORT-TIME CLOSURE IS ``agent_ref`` + ``errors`` AND NOTHING ELSE — not ``config.py``,
# not ``paths.py``. This module is the bottom of the dependency order and they all import IT; a
# reverse edge is a cycle with no facade to hide in (every ``settings/__init__.py`` is
# import-free). ⚑ IT IS A CONTRACT, not an accident: the agent plugin packages import this module
# for ONE constant (``UNSET`` / ``GUEST_HOME``), so an edge added here is an import-time cost they
# all pay, in an import order this module does not control.
#
# ⚑⚑ CALL-TIME EXCEPTION, AND IT IS FIVE NAMES FROM THREE MODULES IN ONE RULE: the dest-keyed
# map checks read ``agent_config.is_self_resolving``, ``settings_categories.{ABSTRACT_CATEGORIES,
# BARE_RELATIVE_SOURCE_HAZARD}`` and ``settings_keyspace.{BIND_CATEGORIES,
# is_terminal_category_tail}``, because a map's own checks need the keyspace to decide what a map
# IS and every reader of one must run them. ⚑ THE CYCLE IS MEASURED, NOT ASSUMED: hoisting
# ``agent_config.is_self_resolving`` to module level raises ``ImportError: cannot import name
# 'SettingsError' from partially initialized module 'kanibako.settings.settings_resolve'``, because
# ``agent_config`` imports ``SettingsError`` from here at ITS module level. The other two modules
# measure clean at import time and stay call-time anyway: a module-level edge is a standing claim
# about two modules' relative import order, which only the imported module can honor.
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


def expand_guest_home(value: str) -> str:
    """Expand a ``$GUEST_HOME`` prefix in a box-side path expression.

    The defaults files write every in-box destination as a ``$GUEST_HOME``
    expression so the guest-home literal lives in exactly one place (the
    :data:`~kanibako.settings.settings_resolve.GUEST_HOME` constant, single SoT).  Only the
    leading ``$GUEST_HOME`` token is substituted; the rest is left verbatim.
    """
    if value.startswith("$GUEST_HOME"):
        return GUEST_HOME + value[len("$GUEST_HOME"):]
    return value


# The GUEST workspace/vault leaves, in the two forms their consumers need: a RELPATH to join onto
# a host ``Path`` and an absolute to compare a box-side dest against.
# ⚑ GUEST-SIDE ONLY. ``bootstrap.WORKSPACE_PATH`` is a HOST leaf spelled identically and the
# two are INDEPENDENT — no guest constant is expressed in terms of a host one, since
# ``bootstrap`` is not in this module's import-time closure (above); the separation is
# structural, not conventional.
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

#: The terminal type a box gets when the HOST has none. Jei, 2026-09-08: an EMPTY (or unset)
#: ``TERM`` falls back to this — and ONLY an empty one; a SET value passes through UNVALIDATED.
DEFAULT_TERM = "xterm"


def _host_term() -> str:
    """Read the host's ``TERM`` for ``$TERM``; empty or unset ⇒ :data:`DEFAULT_TERM`.

    ⚑ A SET VALUE IS DELIBERATELY NOT CHECKED, and the asymmetry with the ``$XDG_*`` twin
    (``paths.resolve_xdg``, which ignores a relative value) is the point: ``os.path.isabs`` is
    a pure test on a string, while "is this ``TERM`` usable" is a terminfo lookup — and this
    resolution runs HOST-side for a consumer that is IN the box, whose terminfo set differs.
    A host-side check would be guessing about a place it cannot see, so an unusable value is
    carried through and degrades the terminal in the box, visibly and overridably by setting
    the key. Do NOT restore the symmetry here.
    """
    return os.environ.get("TERM", "") or DEFAULT_TERM


def _host_colorterm() -> str | None:
    """Read the host's ``COLORTERM`` for ``$COLORTERM``; empty or unset ⇒ ``None``.

    ⚑ THERE IS NO ``DEFAULT_COLORTERM`` TWIN, and the missing constant is the design.
    ``TERM`` can fall back because every terminal HAS a type; ``COLORTERM`` is an
    unstandardized convention whose modern meaning is a CAPABILITY CLAIM — *this
    display does 24-bit color* — and that is the HOST's claim to make, never ours.
    So absence has no substitute here: ``None`` is a legitimate answer, and the
    whole-value caller DROPS the key rather than delivering one
    (``settings_expand._resolve_whole_value_var``).
    🛑 AN EMPTY HOST VALUE IS ABSENCE, not a third answer to pass through. Readers
    of ``COLORTERM`` disagree about ``""`` — some test only for the variable's
    presence — so emitting one would be the same false claim in another spelling.
    """
    return os.environ.get("COLORTERM") or None


@dataclass(frozen=True)
class ResolveCtx:
    """Context for variable expansion: ``$AGENT``/``$WORKSET``/``~``, ``$XDG_*``, ``$TERM``,
    ``$COLORTERM``, ``@config.*``.

    ⚑ Frozen protects rebinding, not the dicts — do not mutate *xdg* / *config* in place.
    """

    agent_name: str | None
    workset_name: str | None
    host_home: str
    xdg: dict[str, str]
    config: Mapping[str, str] = field(default_factory=dict)
    #: ``$TERM``'s answer, defaulted from the host env (:func:`_host_term`). ⚑ DEFAULTED rather
    #: than built per call site like *xdg*: ``TERM`` is one process-wide host fact with no
    #: per-context variation and no map to build, so threading a builder through every ctx
    #: would buy nothing and would invite the PARTIAL per-context namespace the spec calls a
    #: bug. A caller that knows better may still override it.
    term: str = field(default_factory=_host_term)
    #: ``$COLORTERM``'s answer — a PASSTHROUGH, so ``None`` is a REAL answer meaning the
    #: host set none and the box must get NO such variable (:func:`_host_colorterm`).
    #: Defaulted from the host env for the same reason *term* is: one process-wide host
    #: fact, no map to build, no per-context variation.
    colorterm: str | None = field(default_factory=_host_colorterm)


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


#: The characters :func:`expand_expr` reads as syntax (``~`` only at position 0, escaped
#: everywhere for simplicity).
_EXPR_SIGNIFICANT: frozenset[str] = frozenset("\\$@~")


def literal_expr(text: str) -> str:
    """The expression that expands to *text* VERBATIM: each ``\\ $ @ ~`` gets a backslash.

    For a host path a floor hands the expander as a value (a ``$``/``@`` in a directory
    name is data, not a token). Host-space expansion removes every escape; a deferred
    scan keeps ``\\$ \\~ \\\\`` for the box resolver and reads ``\\@`` as no reference.
    """
    return "".join(f"\\{c}" if c in _EXPR_SIGNIFICANT else c for c in text)


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
    """Canonicalize a binding DESTINATION (fully resolved, idempotent).

    ``~`` is EXPANDED, ``.`` / ``..`` / repeated ``/`` are RESOLVED, and a trailing ``/``
    is DROPPED. An ``@``-ref or ``$var`` dest is carried VERBATIM after ``~`` expansion
    and trailing-``/`` stripping; only the ``normpath`` step is skipped for them.

    ⚑⚑ DESTINATIONS ONLY. NEVER CALL THIS ON A ``host_src`` — a dest is a GUEST path (fixed
    machinery); a source's ``~`` is the INVOKING USER's home and must stay UNRESOLVED.
    ⚑ IDEMPOTENT, and applied at every producer AND again on read, deliberately.
    ⚑ Nothing is REFUSED here; the RESOLVED-``box_dest`` collision check downstream stays.
    """
    out = dest
    if out == "~":
        return GUEST_HOME
    if out.startswith("~/"):
        out = GUEST_HOME + out[1:]
    if len(out) > 1 and out.endswith("/"):
        out = out.rstrip("/") or "/"
    if "@" in out or "$" in out:
        return out
    if posixpath.isabs(out):
        out = posixpath.normpath(out)
        if out.startswith("//"):
            out = out[1:]
    return out


# ---------------------------------------------------------------------------
# THE dest-keyed map checks (spec §2a) — ONE checker, EVERY reader
# ---------------------------------------------------------------------------
# ⚑⚑ EVERY reader of a dest-keyed map routes through here, because the checks
# belong to the MAP and not to the reader: a settings FILE, a floor key and an agent
# FILE all read the same shapes, so a check owned by one reader is a check the
# others do not run.


def refuse_dest_spelled_twice(
    raw: Mapping[str, Any], *, category: str, where: str | None = None,
) -> None:
    """RAISE when two keys of the dest-keyed map *raw* are ONE destination (spec §2a).

    Canonicalization makes ``~``/``~/``, ``//``, ``.``/``..`` and a trailing ``/`` ONE
    destination, so two such keys land on one store key and the second would overwrite
    the first without a word. A destination is the map's IDENTITY, and neither spelling
    may silently win — the refusal names BOTH. *category* names the key; *where* names
    the file when the reader knows it.
    """
    spelled: dict[str, str] = {}
    for key in raw:
        dest = normalize_bind_dest(str(key))
        first = spelled.get(dest)
        if first is not None:
            raise SettingsError(
                f"{category} spells one destination twice: {first!r} and {str(key)!r} "
                f"are both the guest path {dest!r}, because a destination is "
                f"CANONICALIZED on read (spec §2a) and neither spelling may silently win.\n"
                f"  Fix: keep one spelling of {dest!r} in {category} and remove the other."
                f"{_in_file(where)}"
            )
        spelled[dest] = str(key)


def refuse_unrooted_source(
    src: str, category: str, dest: str, *, where: str | None = None,
) -> None:
    """RAISE when *src* is a bare-relative source in a category that takes no root (spec §2a).

    A CONCRETE category takes no root at any scope, so a bare-relative source there can only
    resolve against the process CWD and is refused where it is declared. An ABSTRACT
    category HAS a declaration root and joins a bare leaf under it, so it is never judged
    here — including when the key path named no scope, because that path is not a KEY and
    §0's undeclared-key refusal downstream is the one that must name it.

    ⚑ The category decides this on its own, so no caller has to pass a root: a category with
    a declaration root is an ABSTRACT one by definition of the root table.
    """
    from kanibako.settings.agent_config import is_self_resolving
    from kanibako.settings.settings_categories import (
        ABSTRACT_CATEGORIES,
        BARE_RELATIVE_SOURCE_HAZARD,
    )

    if is_self_resolving(src) or category in ABSTRACT_CATEGORIES:
        return
    raise SettingsError(
        f"{category} entry at {dest!r} declares a bare-relative host source "
        f"{src!r}; a source must fully resolve on its own — absolute, ~, $var or "
        f"an @-ref. {category} takes NO root at any scope (spec §2a), so no later "
        f"layer may supply the missing one: {BARE_RELATIVE_SOURCE_HAZARD}. "
        f"Spell the source out.{_in_file(where)}"
    )


def refuse_scalar_at_table_key(
    key: str, value: Any, *, where: str | None = None,
) -> None:
    """RAISE on a non-``None`` SCALAR where *key*'s TABLE goes (spec §0, closed keyspace).

    Every §2a category is TERMINAL and dest-keyed, and a table-valued agent leaf is a table
    WHOLE: the key ENDS at the category token and its VALUE is the map. A scalar there is a
    wrong SHAPE, not a wrong value, and §0 admits neither here — a passthrough would store
    something no reader can apply and say nothing.

    ⚑⚑ A present-``None`` IS THE ONE NON-MAP VALUE A CATEGORY ACCEPTS, and it is not a
    defect: spec §2h classifies it as an OMIT (no mount, no unmask) and calls it the
    suppression power a scope has over what it contains. So ``null`` passes, and only a
    NON-null scalar refuses — one shape, one verdict.

    *key* is the spelling the CALLER holds, which decides both what the message names and
    which example it prescribes: a dest-keyed category's entries are box destinations, a
    marker's are 3-state markers, and a table-valued agent leaf's are its own entries
    (:data:`~kanibako.settings.settings_keyspace.BIND_CATEGORIES` is what tells the first
    two apart, ``masks`` being dest-keyed without being bind-shaped). *where* names the
    file when the reader has it; ⚑ the settings tier's own parse supplies the file in its
    wrapper instead, so a caller already wrapped in one passes none.
    """
    if value is None or isinstance(value, dict):
        return
    from kanibako.settings.config_io import render_stored_scalar
    from kanibako.settings.settings_keyspace import (
        BIND_CATEGORIES,
        is_terminal_category_key,
    )

    leaf = key.rpartition(".")[2]
    if not is_terminal_category_key(key) and leaf not in BIND_CATEGORIES:
        shape = "a table of its own entries"
    elif leaf == "masks":
        shape = "a dest-keyed map of 3-state markers, {box_dest: true}"
    else:
        shape = (
            "a dest-keyed map, keyed by box destination, {box_dest: [src[, options]]}"
        )
    raise SettingsError(
        f"'{key}' holds a scalar ({render_stored_scalar(value)}) where a TABLE goes: it is "
        f"{shape}, and an entry inside one is DATA in the value, never a key of its own "
        f"(spec §2a). One shape, one verdict: a key outside the declared shape is an "
        f"ERROR that names itself, never a silent accept.\n"
        f"  Fix: give '{key}' its table, or delete the entry. A present `null` is the one "
        f"non-table value a category accepts — it OMITS the whole category (spec §2h), "
        f"and a `null` ENTRY omits just that destination.\n"
        f"  The cure is the hand-edit: a category's entries are DATA, so no verb writes "
        f"one.{_in_file(where)}"
    )


def check_bind_map(
    raw: Mapping[str, Any], *, category: str, where: str | None = None,
) -> None:
    """RAISE on every malformed or doubly-spelled entry of ONE dest-keyed bind map.

    The per-entry checks (the retired sub-table shape, the entry arity, the unrooted
    source) and the per-map one (two spellings of one destination) together. *category*
    names the key in every refusal; *where* names the file when the reader has it. A
    ``None`` entry is a legal UNSET, so only its spelling is judged.

    ⚑ THE ENTRY CHECKS RUN WHERE THE PARSE UNPACKS ONE, and nowhere else: a list or tuple is
    an entry, so its arity and its source are judged, and a value that is neither is left as
    the store coercion leaves it. That keeps this checker's verdict IDENTICAL to the reader
    it is shared with — a check that refused more here would make the agent-file reader
    stricter than the settings-file tier rather than equal to it.
    """
    refuse_dest_spelled_twice(raw, category=category, where=where)
    for key, sub in raw.items():
        if sub is None:
            continue
        dest = normalize_bind_dest(str(key))
        if isinstance(sub, dict):
            raise SettingsError(
                f"{category} entry {str(key)!r} holds a sub-table, which is the "
                f"RETIRED name-keyed shape {{name: {{...}}}}. A {category} value "
                f"is a FLAT map keyed by box DESTINATION whose value is "
                f"[src[, options]] (spec §2a); the entry name was dropped "
                f"(bindings 2026-08-06c, the other four 2026-08-08c). Re-key the "
                f"entry to its destination.{_in_file(where)}"
            )
        if isinstance(sub, (list, tuple)):
            try:
                src, _opts = unpack_bind_entry(sub)
            except SettingsError as exc:
                raise SettingsError(
                    f"{category} entry {str(key)!r}: {exc}{_in_file(where)}"
                ) from exc
            refuse_unrooted_source(src, category, dest, where=where)


def check_bind_tables(
    tables: Mapping[str, Any], *,
    root: str, scope: str, contained: Sequence[str], where: str | None,
) -> None:
    """RAISE on a malformed or doubly-spelled dest-keyed bind map an agent file's tables carry.

    An agent file is read as a RAW node table, so it never reaches the settings tier's
    bind parse — the same maps, judged by the same checks, at the reader the launch and
    the ``agent`` verbs share. *tables* is the file's own root: the ``root:`` table, the
    ``scope:`` table's node tables, and every *contained* scope table — each walked to the
    dest-keyed maps it holds. *root* / *scope* / *contained* are the three key names, passed
    in because they belong to the caller.

    ⚑ ``root:`` and every *contained* table are the SCOPE'S OWN TABLE, so their category tokens
    sit at the first level; only the ``scope:`` table carries its node names above them. Both
    shapes end at one table of categories, which is what makes ONE walk the whole file's.
    """
    for token in (root, *contained):
        own = tables.get(token)
        if isinstance(own, dict):
            _check_node_binds(own, where=where)
    node_tables = tables.get(scope)
    if isinstance(node_tables, dict):
        for node_table in node_tables.values():
            if isinstance(node_table, dict):
                _check_node_binds(node_table, where=where)


def _check_node_binds(table: Mapping[str, Any], *, where: str | None) -> None:
    """Check every dest-keyed map one scope's or agent node's *table* holds.

    ONE level only, plus a ``bindings`` arm's: every dest-keyed category is TERMINAL, so a
    node table's category token IS the map. Nothing is descended, so a table-valued key whose
    contents are not settings is never read as one.

    ⚑ THE SHAPE OF THE CATEGORY ITSELF IS JUDGED FIRST, ahead of the ``dict`` guard below:
    a non-``None`` scalar at a category token is a wrong SHAPE (spec §0, closed keyspace)
    and a passthrough would store it silently. A present-``None`` is the category's OMIT
    (spec §2h) and passes — :func:`refuse_scalar_at_table_key` is where that distinction
    lives, so the ``dict`` test below still sees every MAP.
    """
    from kanibako.settings.settings_keyspace import is_terminal_category_tail

    for key, value in table.items():
        if is_terminal_category_tail((key,)) or key == "bindings":
            refuse_scalar_at_table_key(key, value, where=where)
        if not isinstance(value, dict):
            continue
        if is_terminal_category_tail((key,)):
            _check_dest_map(value, category=key, where=where)
        elif key == "bindings":
            for arm, arm_map in value.items():
                if not is_terminal_category_tail(("bindings", arm)):
                    continue
                refuse_scalar_at_table_key(f"bindings.{arm}", arm_map, where=where)
                if isinstance(arm_map, dict):
                    _check_dest_map(arm_map, category=f"bindings.{arm}", where=where)


def _check_dest_map(raw: Mapping[str, Any], *, category: str, where: str | None) -> None:
    """Run the checks that apply to the dest-keyed map *category* holds."""
    from kanibako.settings.settings_keyspace import BIND_CATEGORIES

    if category in BIND_CATEGORIES:
        check_bind_map(raw, category=category, where=where)
    else:
        # A MARKER map holds 3-state markers rather than bind entries, so the PER-MAP
        # spelling check is the whole of what applies to it.
        refuse_dest_spelled_twice(raw, category=category, where=where)


def _in_file(where: str | None) -> str:
    """The ``(in settings file X)`` clause, matching the parse wrapper's own wording."""
    return f" (in settings file {where})" if where is not None else ""


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
    ⚑ THE SINGLE parser for both spellings, shared across modules (seam S25).
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


def resolve_var(name: str, ctx: ResolveCtx) -> str | _Unset:
    """Resolve a variable name against the context namespace — THREE-STATE.

    Answers the value, or :data:`UNSET` for the one class of variable whose declared
    answer can be LEGITIMATE ABSENCE: a PASSTHROUGH of a host signal that has no
    substitute value (``$COLORTERM``). Every other unresolvable name still RAISES and
    NAMES ITSELF — :data:`UNSET` is never a way of saying "I could not work this out".

    ⚑ THIS IS THE PRIMITIVE and :func:`_resolve_var` is derived from it, not the other
    way round. Only a caller that can EXPRESS absence may see the third state, and
    exactly one can: a WHOLE-VALUE expression, which drops its holder key (§6b). An
    embedded token is string substitution and has no way to say "no variable".
    """
    if name == "AGENT":
        if ctx.agent_name is None:
            raise SettingsError("Variable $AGENT is not set in this context.")
        return ctx.agent_name
    if name == "WORKSET":
        if ctx.workset_name is None:
            raise SettingsError("Variable $WORKSET is not set in this context.")
        return ctx.workset_name
    if name == "TERM":
        # Never refuses and never validates — see :func:`_host_term`.
        return ctx.term
    if name == "COLORTERM":
        # A PASSTHROUGH with NO fallback value: the host's signal, or nothing at all.
        # ⚑ The asymmetry with ``TERM`` directly above is deliberate — see
        # :func:`_host_colorterm` for why no literal could stand in here.
        return ctx.colorterm if ctx.colorterm is not None else UNSET
    if name.startswith("XDG_"):
        if name not in ctx.xdg:
            raise SettingsError(f"Variable ${name} is not set in this context.")
        return ctx.xdg[name]
    raise SettingsError(f"Unknown variable: ${name}")


def _resolve_var(name: str, ctx: ResolveCtx) -> str:
    """The EMBEDDED-token answer: :func:`resolve_var` with absence coerced to ``""``.

    ⚑ THE SAME RULE THE EMBEDDED ``@``-REF PATH ALREADY KEEPS
    (``settings_expand._lookup_str``): an absent referent substitutes an empty string,
    which never deletes the HOST expression's key. ``"c=$COLORTERM"`` on a host with
    none is ``"c="``, because the key's value is that whole string and it exists.
    """
    value = resolve_var(name, ctx)
    return "" if isinstance(value, _Unset) else value


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

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
from collections.abc import Callable, Iterator, Mapping, Sequence
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
# 'match_braced' from partially initialized module 'kanibako.settings.settings_resolve'``, because
# ``agent_config`` imports ``match_braced`` from here at ITS module level. The other two modules
# measure clean at import time and stay call-time anyway: a module-level edge is a standing claim
# about two modules' relative import order, which only the imported module can honor.
# BARE-REF-GUARD: the one-release guard adds a second call-time edge,
# ``settings_keyspace_probe.{KeyClass, keyspace_verdict}``, to ask whether a name is declared.
from kanibako.agent_ref import CANONICAL_SEP, ENV_VAR_NAME, SEGMENT_CHAR_CLASS
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
  """Expand a leading ``{$GUEST_HOME}`` in a box-side path expression.

  The defaults files write every in-box destination as a ``{$GUEST_HOME}``
  expression so the guest-home literal lives in exactly one place (the
  :data:`~kanibako.settings.settings_resolve.GUEST_HOME` constant, single SoT).  Only the
  leading token is substituted; the rest is left verbatim.
  BARE-REF-GUARD: a leading ``$GUEST_HOME`` / ``${GUEST_HOME}`` is refused with the
  braced cure (keyspec "References"); ``GUEST_HOME`` is a known variable here only.
  """
  token = "{$GUEST_HOME}"
  if value.startswith(token):
    return GUEST_HOME + value[len(token) :]
  if value[:1] == "$":
    refuse_bare_reference(value, 0, is_var=lambda name: name == "GUEST_HOME")
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

_VAR_NAME_RE = re.compile(ENV_VAR_NAME)
# ⚑ COMPOSED FROM the agent-side charset, never restating it, so the subset relation holds BY
# CONSTRUCTION — a node-name is a key segment, and these two literals drifted TWICE when separate.
_REF_SEG = f"[{SEGMENT_CHAR_CLASS}{CANONICAL_SEP}]+"
_REF_NAME_RE = re.compile(rf"{_REF_SEG}(?:\.{_REF_SEG})*")


class SettingsError(KanibakoError):
  """Raised on unknown variable, unresolvable/cyclic ``{key}``-ref, or depth-cap."""


class ExprSyntaxError(SettingsError):
  """A value the reference grammar refuses: an unpaired brace, an empty or malformed
  reference, an illegal escape (keyspec "References").

  *entry* names the setting that holds the value once a reader that knows it has
  said so (:meth:`naming`); ``None`` until then.  *chain* is that setting's key path,
  for a reader that must find the file holding it (:meth:`in_file`).
  """

  def __init__(
    self, message: str, *, entry: str | None = None, chain: tuple[str, ...] = ()
  ) -> None:
    super().__init__(message)
    self.entry = entry
    self.chain = chain

  def naming(
    self, entry: str, *, chain: tuple[str, ...] = (), reached_from: str | None = None
  ) -> "ExprSyntaxError":
    """This refusal with *entry* (at key path *chain*) named in front; one already named
    is returned as is.

    *reached_from* names the setting whose reference led to *entry*, as a trail.
    """
    if self.entry is not None:
      return self
    trail = "" if reached_from is None else f" (reached from {reached_from})"
    return ExprSyntaxError(f"'{entry}': {str(self).rstrip('.')}{trail}.", entry=entry, chain=chain)

  def in_file(self, where: str | None) -> "ExprSyntaxError":
    """This refusal with the file *where* that holds the value appended; ``None`` adds none."""
    if where is None:
      return self
    return ExprSyntaxError(f"{self}{_in_file(where)}", entry=self.entry, chain=self.chain)


class _Unset:
  """Sentinel type for "no value resolved" (distinct from an explicit ``""``)."""

  __slots__ = ()

  def __repr__(self) -> str:
    return "UNSET"


UNSET = _Unset()

#: The terminal type a box gets when the HOST has none. [Jei:2026-09-08]: an EMPTY (or unset)
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
#: everywhere for simplicity); braces are doubled rather than escaped.
_EXPR_SIGNIFICANT: frozenset[str] = frozenset("\\~")


def _brace_literal(c: str) -> str:
  """``{``/``}`` doubled; any other character unchanged."""
  return c * 2 if c in "{}" else c


# BARE-REF-GUARD: the guard's data mode. Removing the guard: delete this class and return
# a plain ``str`` from :func:`literal_expr` and :func:`data_expr`'s callers.
class DataExpr(str):
  """An expression the CODE built from data, never user-authored settings text.

  :func:`iter_expr_tokens` reads it with the same grammar but skips
  :func:`refuse_bare_reference`: the one-release guard is for text a user wrote, and
  a host path, a persona value, or a probed source holding ``@box.image`` or
  ``$TERM`` is data that resolves as written (keyspec "References"). Built by
  :func:`literal_expr`, and by :func:`data_expr` for a deferred value the host pass
  already tokenized. ``str()`` keeps the mark; a concatenation or a slice returns a
  plain ``str``, which the guard reads as user text again.
  """

  __slots__ = ()

  def __str__(self) -> str:
    return self


def data_expr(text: str) -> DataExpr:
  """*text* as a :class:`DataExpr`: an expression whose tokens the guard already passed."""
  return DataExpr(text)


def literal_expr(text: str) -> DataExpr:
  """The expression that expands to *text* VERBATIM: ``\\`` and ``~`` backslashed, braces
  doubled, as a :class:`DataExpr`.

  For a host path a floor hands the expander as a value (a ``{`` in a directory name is
  data, not a token). Host-space expansion removes every escape; a deferred scan keeps
  them, so the box pass reads the same text.
  """
  return DataExpr("".join(f"\\{c}" if c in _EXPR_SIGNIFICANT else _brace_literal(c) for c in text))


def literal_map(values: Mapping[str, str]) -> dict[str, str]:
  """*values* with every value a :func:`literal_expr`: a table of resolved host paths."""
  return {key: literal_expr(value) for key, value in values.items()}


def is_verbatim_text(path: Sequence[str]) -> bool:
  """Whether the key at *path* holds TEXT, never an expression: ``agent.<node>.endpoint``.

  Keyspec ``agent.default.endpoint``: verbatim in every settings file and at ``set`` —
  no ``{key}``-ref, ``{$VAR}``/``~`` expansion or ``\\`` unescape, never refused for its
  characters.  Its readers: the expander (every resolve), ``config set`` validation,
  and the persona route, whose other values still enter as :func:`literal_expr`.
  ⚑ A SINGLE NAMED EXCEPTION, not a key-kind facility: separating literal keys from
  computed ones is a deferred design, and a second literal key belongs to it.
  """
  return len(path) == 3 and path[0] == "agent" and path[2] == "endpoint"


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
  is DROPPED. A dest holding a ``{`` (a reference) is carried VERBATIM after ``~``
  expansion and trailing-``/`` stripping; only the ``normpath`` step is skipped for it.

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
  if "{" in out:
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
  raw: Mapping[str, Any],
  *,
  category: str,
  where: str | None = None,
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
  src: str,
  category: str,
  dest: str,
  *,
  where: str | None = None,
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
  from kanibako.settings.settings_categories import ABSTRACT_CATEGORIES

  if is_self_resolving(src) or category in ABSTRACT_CATEGORIES:
    return
  raise SettingsError(
    unrooted_source_message(f"{category} entry at {dest!r}", src, category) + _in_file(where)
  )


def unrooted_source_message(
  entry: str,
  src: str,
  category: str,
  *,
  unrooted_because: str | None = None,
) -> str:
  """THE text of the bare-relative host-source refusal (spec §2a), for *entry* in *category*.

  *unrooted_because* says why nothing will root *src*; it defaults to the reason that is
  true of a CONCRETE category everywhere. A caller refusing an ABSTRACT category's leaf
  must pass the reason true of its own path, because that category does take a root
  where it is declared.
  """
  from kanibako.settings.settings_categories import BARE_RELATIVE_SOURCE_HAZARD

  because = unrooted_because or (
    f"{category} takes NO root at any scope (spec §2a), so no later layer may "
    f"supply the missing one"
  )
  return (
    f"{entry} declares a bare-relative host source {src!r}; a source must fully "
    f"resolve on its own — absolute, ~, {{$VAR}} or a {{key}} reference. {because}: "
    f"{BARE_RELATIVE_SOURCE_HAZARD}. Spell the source out."
  )


def refuse_scalar_at_table_key(
  key: str,
  value: Any,
  *,
  where: str | None = None,
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
  if leaf == "masks":
    shape = "a dest-keyed map of 3-state markers, {box_dest: true}"
  elif is_terminal_category_key(key) or leaf in BIND_CATEGORIES:
    shape = "a dest-keyed map, keyed by box destination, {box_dest: [src[, options]]}"
  else:
    shape = "a table of its own entries"
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
  raw: Mapping[str, Any],
  *,
  category: str,
  where: str | None = None,
  declared: bool = True,
  pref_agent: str | None = None,
) -> None:
  """RAISE on every malformed or doubly-spelled entry of ONE dest-keyed bind map.

  The per-entry checks (the retired sub-table shape, a value that is not a list, the
  entry arity, the unrooted source) and the per-map one (two spellings of one
  destination) together. *category* names the key in every refusal; *where* names the
  file when the reader has it. A ``None`` entry is a legal UNSET, so only its spelling
  is judged.

  ⚑ EVERY NON-``None`` ENTRY IS JUDGED HERE, and by the SAME reader: a sub-table is the
  retired shape, and anything else must unpack as the structured entry, so a bare scalar
  is refused rather than left to the store coercion — ONE verdict for the settings-file
  tier and the agent-file reader alike.

  ⚑ *declared* is False for a map whose KEY is not a declared category position
  (``agent.common``, a node with no discriminator): its entries are undeclared keys, so
  a value that is not a list is left to the §0 refusal, which names the whole key.

  ⚑ *pref_agent* is the agent segment the map sits under — ``zippity`` for a
  ``pref.agent.zippity.<category>`` entry — or ``None`` for every other key. The bind-map
  parse passes it deferred or not, and it is judged FIRST, by
  :func:`~kanibako.settings.settings_prefs.agent_segment_reason`, which says why.
  """
  if pref_agent is not None:
    # ⚑ FUNCTION-SCOPE: ``settings_prefs`` imports THIS module at module scope, so a
    # module-scope import back would close a cycle.
    from kanibako.settings.settings_prefs import agent_segment_reason

    reason = agent_segment_reason(pref_agent)
    if reason is not None:
      raise SettingsError(
        f"{category} entry under pref.agent.{pref_agent}: {reason}.{_in_file(where)}"
      )
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
    if not declared and not isinstance(sub, (list, tuple)):
      continue
    try:
      src, _opts = unpack_bind_entry(sub)
    except SettingsError as exc:
      raise SettingsError(f"{category} entry {str(key)!r}: {exc}{_in_file(where)}") from exc
    refuse_unrooted_source(src, category, dest, where=where)


def check_bind_tables(
  tables: Mapping[str, Any],
  *,
  root: str,
  scope: str,
  contained: Sequence[str],
  where: str | None,
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

  ⚑ A non-dict at a category token is CONCEDED HERE, not refused, and that is the agent
  file's READ side on purpose: a wrong-SHAPE value must not kill the verbs that SHOW it,
  because the repair doors have to stay reachable — so the read coerces and the WRITE
  side refuses (:func:`~kanibako.settings.agent_file.table_value_error`). A LAUNCH is the
  other reader and refuses the same shape, naming the file
  (:func:`refuse_scalar_at_table_key`).
  """
  from kanibako.settings.settings_keyspace import is_terminal_category_tail

  for key, value in table.items():
    if not isinstance(value, dict):
      continue
    if is_terminal_category_tail((key,)):
      _check_dest_map(value, category=key, where=where)
    elif key == "bindings":
      for arm, arm_map in value.items():
        if isinstance(arm_map, dict) and is_terminal_category_tail(("bindings", arm)):
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


def match_braced(expr: str, i: int) -> tuple[Literal["ref", "var"], str, int] | None:
  """Parse ``{a.b.c}`` (a key) or ``{$NAME}`` (a variable) at index *i*.

  Returns ``(kind, name, end_index)``, or ``None`` when no reference starts there:
  ``{{``, or a ``{`` not followed by a name and its ``}``.
  ⚑ PRECONDITION: ``expr[i] == "{"``.
  ⚑ THE SINGLE parser for the braced spelling. ``None`` is a verdict on this position
  only; in a value :func:`iter_expr_tokens` refuses it (:func:`_brace_refusal`).
  """
  j = i + 1
  kind: Literal["ref", "var"] = "ref"
  pattern = _REF_NAME_RE
  if j < len(expr) and expr[j] == "$":
    kind, pattern, j = "var", _VAR_NAME_RE, j + 1
  m = pattern.match(expr, j)
  if m is None or m.end() >= len(expr) or expr[m.end()] != "}":
    return None
  return kind, m.group(0), m.end() + 1


def unquoted_reference_hint(value: object) -> str:
  """The sentence for a map YAML read from an UNQUOTED whole-value reference, else ``""``.

  ``key: {$HOME}`` is the flow mapping ``{"$HOME": None}``; the name is judged by
  :func:`match_braced`, so only a map that IS a reference's spelling gets the hint.
  """
  if not isinstance(value, dict) or len(value) != 1:
    return ""
  ((name, inner),) = value.items()
  text = f"{{{name}}}"
  if inner is not None or not isinstance(name, str) or match_braced(text, 0) is None:
    return ""
  return f" It was read as a map because it was unquoted: write '{text}'."


#: The characters a backslash may escape (keyspec "References"): ``\\`` and ``\~``.
_ESCAPABLE: frozenset[str] = frozenset("\\~")

#: The characters that can start a token; everything else is plain text.
# BARE-REF-GUARD: ``@`` and ``$`` are here only for :func:`refuse_bare_reference`.
_TOKEN_START: frozenset[str] = frozenset("\\{}@$")

#: The variables a value can name (keyspec "References"): :func:`resolve_var` answers
#: these, and every ``XDG_*`` name (:data:`KNOWN_VAR_PREFIX`).
KNOWN_VARS: frozenset[str] = frozenset({"AGENT", "WORKSET", "TERM", "COLORTERM"})
KNOWN_VAR_PREFIX = "XDG_"


def is_known_var(name: str) -> bool:
  """Whether *name* is a variable :func:`resolve_var` reads (:data:`KNOWN_VARS`, ``XDG_*``)."""
  return name in KNOWN_VARS or (
    name.startswith(KNOWN_VAR_PREFIX) and len(name) > len(KNOWN_VAR_PREFIX)
  )


def _is_declared_key(name: str) -> bool:
  """Whether *name* is a declared key: the closed keyspace's own verdict."""
  from kanibako.settings.settings_keyspace_probe import KeyClass, keyspace_verdict

  return keyspace_verdict(name).cls is KeyClass.KEY


# BARE-REF-GUARD: the one-release migration guard (keyspec "References"). Removing it:
# delete this function and :func:`refuse_bare_reference`, their calls in
# :func:`iter_expr_tokens`, and ``@$`` from :data:`_TOKEN_START`; grep BARE-REF-GUARD for
# every other site.
def bare_reference_at(
  expr: str,
  i: int,
  *,
  is_var: Callable[[str], bool] = is_known_var,
) -> tuple[str, str] | None:
  """``(old spelling, braced cure)`` when the ``@`` or ``$`` at *i* starts an old
  reference spelling; else ``None``.

  The spellings: a bare ``@a.b.c`` naming a declared key, or whose leading dotted part
  is one (``@meta.box.name.jsonl``); ``@{a.b.c}`` naming a declared key; a bare
  ``$NAME`` or ``${NAME}`` naming a known variable (*is_var*). Any other ``@`` or ``$``
  is text.
  ⚑ PRECONDITION: ``expr[i]`` is ``@`` or ``$``.
  """
  rest = expr[i + 1 :]
  if expr[i] == "@":
    if rest.startswith("{"):
      braced = match_braced(expr, i + 1)
      if braced is not None and braced[0] == "ref" and _is_declared_key(braced[1]):
        return expr[i : braced[2]], f"{{{braced[1]}}}"
      return None
    m = _REF_NAME_RE.match(expr, i + 1)
    if m is None:
      return None
    parts = m.group(0).split(".")
    for k in range(len(parts), 0, -1):
      head = ".".join(parts[:k])
      if _is_declared_key(head):
        return expr[i : m.end()], f"{{{head}}}" + "".join(f".{p}" for p in parts[k:])
    return None
  in_braces = rest.startswith("{")
  m = _VAR_NAME_RE.match(expr, i + 2 if in_braces else i + 1)
  if m is None or not is_var(m.group(0)):
    return None
  end = m.end()
  if in_braces:
    if expr[end : end + 1] != "}":
      return None
    end += 1
  return expr[i:end], f"{{${m.group(0)}}}"


def refuse_bare_reference(
  expr: str,
  i: int,
  *,
  is_var: Callable[[str], bool] = is_known_var,
) -> None:
  """RAISE when the ``@`` or ``$`` at *i* starts an old reference spelling
  (:func:`bare_reference_at`), naming the braced spelling that cures it; else return.

  ⚑ PRECONDITION: ``expr[i]`` is ``@`` or ``$``.
  """
  found = bare_reference_at(expr, i, is_var=is_var)
  if found is not None:
    old, cure = found
    raise ExprSyntaxError(
      f"Old reference spelling '{old}' at: {expr[i:]!r} — references are braced "
      f"now: write '{cure}'."
    )


# BARE-REF-GUARD: delete with :func:`refuse_bare_reference`, and the callers' arms with it.
def starts_bare_reference(expr: str) -> bool:
  """Whether *expr* OPENS with an old reference spelling :func:`refuse_bare_reference` refuses.

  For a predicate that reads a value's leading anchor (``agent_config``): it passes
  such a value on, so the expander refuses it by name with the braced cure, rather
  than calling it a bare relative path.
  """
  return expr[:1] in ("@", "$") and bare_reference_at(expr, 0) is not None


ExprTokenKind = Literal["text", "home", "escape", "brace", "ref", "var"]


@dataclass(frozen=True)
class ExprToken:
  """One token of a value: *kind*, its *value*, and its source span ``[start, end)``.

  *value* is the text for ``text``, ``"~"`` for ``home`` (a leading ``~``), the escaped
  character for ``escape``, the brace for ``brace`` (a doubled ``{{``/``}}``), and the
  NAME for ``ref``/``var``.
  """

  kind: ExprTokenKind
  value: str
  start: int
  end: int


def _brace_refusal(expr: str, i: int) -> ExprSyntaxError:
  """The refusal for a ``{`` at *i* that opens no reference (keyspec "References")."""
  close = expr.find("}", i + 1)
  if close == -1:
    return ExprSyntaxError(f"Unpaired '{{' at: {expr[i:]!r} — write a literal '{{' as '{{{{'.")
  noun = "Empty" if close == i + 1 else "Malformed"
  return ExprSyntaxError(
    f"{noun} reference {expr[i : close + 1]!r} at: {expr[i:]!r} — a reference is "
    f"{{a.b.c}} or {{$NAME}}; write a literal '{{' as '{{{{' and '}}' as '}}}}'."
  )


def iter_expr_tokens(expr: str) -> Iterator[ExprToken]:
  """Tokenize the value *expr*, left to right, refusing what the grammar forbids.

  ⚑ THE ONE tokenizer of a value: :func:`expand_expr` and
  ``settings_configset.scan_tokens`` both read it, so the expander and the set-time
  scan cannot disagree. A GENERATOR, so a refusal raises at its own position, after
  the tokens before it.
  Refused with :class:`ExprSyntaxError` (keyspec "References"): an unpaired ``{`` or
  ``}``; an empty or malformed ``{...}``; a backslash before any character outside
  :data:`_ESCAPABLE`; an old reference spelling (:func:`refuse_bare_reference`).
  ``{{``/``}}`` are literal braces, a leading ``~`` is a ``home`` token, a TRAILING lone
  backslash is literal text, and ``@`` and ``$`` are text.
  """
  # BARE-REF-GUARD: a :class:`DataExpr` is code-built data, not user text.
  guard = not isinstance(expr, DataExpr)
  n = len(expr)
  i = 0
  if n and expr[0] == "~":
    yield ExprToken("home", "~", 0, 1)
    i = 1
  start = i
  while i < n:
    c = expr[i]
    if c not in _TOKEN_START:
      i += 1
      continue
    if c in "@$":
      # BARE-REF-GUARD: ``@`` and ``$`` are text once the guard passes them.
      if guard:
        refuse_bare_reference(expr, i)
      i += 1
      continue
    if start < i:
      yield ExprToken("text", expr[start:i], start, i)
    if c == "\\":
      if i + 1 == n:
        yield ExprToken("text", "\\", i, n)
        i = n
      elif expr[i + 1] in _ESCAPABLE:
        yield ExprToken("escape", expr[i + 1], i, i + 2)
        i += 2
      else:
        ordinary = expr[i + 1]
        # ``\@``/``\$`` escaped a sigil before ``@`` and ``$`` became ordinary.
        cure = ""
        if ordinary in "@$":
          # BARE-REF-GUARD: a plain ``@``/``$`` here would be refused too.
          found = bare_reference_at(expr, i + 1)
          cure = (
            f" '{ordinary}' is an ordinary character: write '\\{ordinary}' as '{ordinary}'."
            if found is None
            else f" '{found[0]}' is refused for this release as an old reference "
            f"spelling: write '{found[1]}' for the "
            f"{'key' if ordinary == '@' else 'variable'}'s value; the literal "
            f"text has no spelling until the guard is removed."
          )
        raise ExprSyntaxError(
          f"Illegal escape '{expr[i : i + 2]}' at: {expr[i:]!r} — only "
          f"\\\\ and \\~ escape; write a literal '\\' as '\\\\'.{cure}"
        )
    elif c in "{}":
      if expr[i + 1 : i + 2] == c:
        yield ExprToken("brace", c, i, i + 2)
        i += 2
      elif c == "}":
        raise ExprSyntaxError(f"Unpaired '}}' at: {expr[i:]!r} — write a literal '}}' as '}}}}'.")
      else:
        braced = match_braced(expr, i)
        if braced is None:
          raise _brace_refusal(expr, i)
        kind, name, end = braced
        yield ExprToken(kind, name, i, end)
        i = end
    start = i
  if start < n:
    yield ExprToken("text", expr[start:], start, n)


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
  ⚑ *defer_env* emits the ENVIRONMENT tokens (``~``, ``{$VAR}``) VERBATIM for a LATER resolver
  in a DIFFERENT environment; ``{key}``-refs (CONFIG) still expand here.
  """
  out: list[str] = []
  for tok in iter_expr_tokens(expr):
    kind, value = tok.kind, tok.value
    if kind == "text":
      out.append(value)
    elif kind == "home":
      if defer_env:
        out.append("~")
      else:
        out.append(ctx.host_home if space == "host" else GUEST_HOME)
    elif kind == "escape":
      # ⚑ THE ESCAPE RULE INVERTS UNDER DEFERRAL: host-side ``\\x`` -> ``x``, but a
      # DEFERRED escape is carried VERBATIM so the BOX resolver still sees it.
      out.append(f"\\{value}" if defer_env else value)
    elif kind == "brace":
      # ⚑ A literal brace stays DOUBLED under deferral, so the box pass reads it the same way.
      out.append(value * 2 if defer_env else value)
    elif kind == "var":
      out.append(expr[tok.start : tok.end] if defer_env else _resolve_var(value, ctx))
    else:
      out.append(_lookup_ref(value, lookup, chain))
  return "".join(out)


def resolve_var(name: str, ctx: ResolveCtx) -> str | _Unset:
  """Resolve a variable name against the context namespace — THREE-STATE.

  Answers the value, or :data:`UNSET` for the one class of variable whose declared
  answer can be LEGITIMATE ABSENCE: a PASSTHROUGH of a host signal that has no
  substitute value (``$COLORTERM``). Every other unresolvable name still RAISES and
  NAMES ITSELF — :data:`UNSET` is never a way of saying "I could not work this out".

  ⚑ THIS IS THE PRIMITIVE and :func:`_resolve_var` is derived from it, not the other
  way round. Only a caller that can EXPRESS absence may see the third state, and
  exactly one can: a WHOLE-VALUE expression, which drops its holder key (spec §0). An
  embedded token is string substitution and has no way to say "no variable".
  """
  if name == "AGENT":
    if ctx.agent_name is None:
      raise SettingsError("Variable {$AGENT} is not set in this context.")
    return ctx.agent_name
  if name == "WORKSET":
    if ctx.workset_name is None:
      raise SettingsError("Variable {$WORKSET} is not set in this context.")
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
      raise SettingsError(f"Variable {{${name}}} is not set in this context.")
    return ctx.xdg[name]
  raise SettingsError(f"Unknown variable: {{${name}}}")


def _resolve_var(name: str, ctx: ResolveCtx) -> str:
  """The EMBEDDED-token answer: :func:`resolve_var` with absence coerced to ``""``.

  ⚑ THE SAME RULE THE EMBEDDED ``@``-REF PATH ALREADY KEEPS
  (``settings_expand._lookup_str``): an absent referent substitutes an empty string,
  which never deletes the HOST expression's key. ``"c=$COLORTERM"`` on a host with
  none is ``"c="``, because the key's value is that whole string and it exists.
  """
  value = resolve_var(name, ctx)
  return "" if isinstance(value, _Unset) else value


def _lookup_ref(
  ref_name: str,
  lookup: Callable[[str, tuple[str, ...]], str],
  chain: tuple[str, ...],
) -> str:
  """One key reference: cycle guard, depth cap, ``lookup`` — spelling-agnostic."""
  guard_ref(ref_name, chain, len(chain))
  return lookup(ref_name, (*chain, ref_name))


def guard_ref(ref_name: str, trail: tuple[str, ...], depth: int) -> None:
  """RAISE when key reference *ref_name* closes a cycle or passes the depth cap.

  *trail* holds the references in progress, outermost first; *ref_name* in it is a cycle,
  named as the trail plus *ref_name*. *depth* counts the references above this one, and
  from :data:`MAX_REF_DEPTH` on the reference is refused.
  ⚑ THE ONE guard for both resolvers: :func:`_lookup_ref` and ``settings_expand._Expander``.
  """
  if ref_name in trail:
    cycle = " -> ".join(f"{{{name}}}" for name in (*trail, ref_name))
    raise SettingsError(f"Cyclic reference: {cycle}")
  if depth >= MAX_REF_DEPTH:
    raise SettingsError(
      f"Reference depth cap ({MAX_REF_DEPTH}) exceeded resolving '{{{ref_name}}}'."
    )


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
      return ResolvedValue(value=level.defaults[key], level=level.name, is_default=True)

  return UNSET


#: The levels whose retired-entry cure is ALWAYS a ``set`` that reads that same level's own file,
#: so it refuses the write while the entry is there. Measured end to end through the real CLI
#: with the entry present: these three rc 1. ``base`` is not among them. The ``agent`` tier is
#: NOT a level decision: each cure decides it per arm.
SET_TARGETS_ITS_OWN_FILE: "frozenset[str]" = frozenset({"system", "workset", "box"})


def delete_before_set_step(
  entry: str,
  *,
  where: Any,
  parents: "Sequence[str]" = (),
  collides_with: str | None = None,
  removal_first: bool = False,
) -> str:
  """The delete-before-the-``set`` step, naming every parent that delete can leave empty.

  ⚑ THE PARENTS ARE PART OF THE CURE, NOT A COURTESY. YAML reads a key with nothing
  under it as a NULL, and a null under a table key is an entry that is not a key — so a
  delete that names only the leaf can leave the file holding one. Naming the parents
  keeps the file a document of keys, which is the whole point of printing them.

  *entry* is the thing to delete, spelled as the site spells it and carrying its own
  noun (``the `agent: default: default_agent` entry`` / ``the `self.foo` table``), so
  each site keeps the spelling its message already uses. *parents* are the ancestor
  tables, innermost first, already spelled for the file; empty for a top-level leaf,
  which has no parent to strand. *collides_with* names what the printed ``set`` refuses
  while *entry* is still stored (``a retired entry``); ``None``: it does not refuse.
  *removal_first*: the printed fix opens with that removal, and only then offers a ``set``.
  """
  stranded = (
    (
      f" — and {' / '.join(parents)} with it, if that leaves "
      f"{'it' if len(parents) == 1 else 'them'} empty: a table left with nothing "
      f"under it parses as null, and a null under a table key is an entry that is "
      f"not a key (§0)"
    )
    if parents
    else ""
  )
  # ⚑ ONE SENTENCE PER ARM, each measured end to end through the real CLI: where the
  # printed ``set`` refuses the write while the entry is there (rc 1) it says so; where
  # the write succeeds (rc 0) it must not promise a refusal that never comes. No
  # sentence names a file, so none reaches an arm another does not.
  if removal_first:
    then = (
      f"§2a refuses a write that collides with {collides_with} still stored in the files it reads"
      if collides_with
      else "it does not refuse this entry"
    )
    why = f"The fix below starts with that removal, then offers a `set`, and {then}."
  elif collides_with:
    why = (
      "The fix below is a `set`, and §2a refuses a write that collides with "
      f"{collides_with} still stored in the files it reads."
    )
  else:
    why = (
      "The fix below is a `set`, and it does not refuse this entry — nothing blocks "
      "the write. Delete the entry, then run the fix below: deleting it alone loses "
      "the setting."
    )
  return f"  Delete the {entry} from {where} FIRST{stranded}. {why}"

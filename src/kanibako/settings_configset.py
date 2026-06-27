"""``config set`` — set-time VALIDATION + the RAW category write-back.

Block 5 of the KeyStore implementation. Two cooperating pieces implement the
KeyStore ``config set`` semantics (design §6d / spec §2a), build ALONGSIDE the
live ``config_interface.set_config_value`` router (block 7 wires the CLI to call
THIS — this module does NOT touch ``cli.py`` or the live setter):

1. :func:`validate_config_set` — the B5 set-time validation. A PURE function
   returning a typed :class:`Verdict` (``OK`` / :class:`Warn` / :class:`Error`).
   It REUSES the resolver's parse grammar (``split_bind`` for the ``:`` notation;
   ``_VAR_NAME_RE`` / ``_REF_NAME_RE`` + the same escape / ``$VAR`` / ``@ref`` scan
   as :func:`kanibako.settings_resolve.expand_expr`) and the
   :mod:`kanibako.config_interface` key registry (``is_known_key`` / ``KEY_TYPES``
   / ``_coerce_value``) — it does NOT invent a second validator (S25). It validates
   references for WELL-FORMEDNESS WITHOUT resolving them to literals (design §6d /
   spec §0 "files store UNRESOLVED").
2. :func:`repoint_host_src` — the RAW category write. On a category key it reads
   the EXISTING raw tuple at the COMMAND's scope file (key-MUST-exist, else a hard
   error), swaps element 0 (``host_src``) for the user's VERBATIM raw input,
   PRESERVES ``box_dest`` + any options (elements 1/2), and writes the FULL raw
   tuple back via the existing YAML I/O (``config_io``). The stored form is RAW —
   ``@``-refs / ``$XDG`` / ``~`` are NEVER expanded to a literal (S12/S24).

B5 severity split (design §6d, RATIFIED by Jei 2026-06-27):

* **Hard ERROR, refuse to write** (don't poison the file): malformed syntax or
  the forbidden ``:`` ``src:dest`` notation; a type mismatch for a typed scalar
  key; a dangling reference (an ``@``-ref to a non-existent config key, or an
  unknown ``$VAR``).
* **WARN, proceed**: a host source PATH that does not exist yet (some sources are
  created-if-missing — vault is the precedent).
* **NO ``@``-ref-repoint warning (B4).** Repointing an ``@``-ref ``host_src`` to a
  literal (or vice-versa) is a normal, explicit file edit at the command's scope
  — identical to a hand-edit of the YAML — so it does NOT warn.

OUT of scope (hard boundaries): NO CLI wiring / NO rewrite of
``set_config_value``'s routing / does NOT touch ``cli.py`` or the ``config``
subcommands (block 7). NO merge / expansion / views / consumer swap. NO
``@``-ref / ``$VAR`` / ``~`` resolution to literals — files store UNRESOLVED.

Authority
---------
* ``~/vault/rw/keystore-design.md`` §6d (``config set`` write-back + B4 + B5 —
  PRIMARY), §2 / §6a (files store UNRESOLVED — write RAW, never expanded).
* Spec ``settings-keyspace-1.6.0-target.md`` §2a (config-set block: source-only,
  key-must-exist, value types), §0 (files store UNRESOLVED).

Seams realized here (``plans/keystore-blocks/SEAMS.md``)
-------------------------------------------------------
* **S24** — ``config set`` writes the FULL RAW tuple at the COMMAND's scope,
  key-must-exist, source-only; never creates a key, never writes an expanded
  literal, no ``:`` notation.
* **S25** — validation REUSES the resolver parse + the key registry (one
  validator). The hard-error vs warn split is exactly B5; NO ``@``-ref-repoint
  warning (B4).
* **S3** — snapshot access uses the UNBOUND ``dict.<method>(obj, …)`` bypass (a
  key named ``get`` / ``items`` cannot shadow the protocol into a crash).
* **S12** — the RAW form is what persists; this writer is one of the two WRITE-ONCE
  writers (build path + ``config set``), and it writes RAW only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Union

from kanibako.config_interface import KEY_TYPES, _coerce_value
from kanibako.config_io import dump_doc, load_doc
from kanibako.settings_resolve import _REF_NAME_RE, _VAR_NAME_RE, split_bind
from kanibako.settings_store import _MISSING, KeyStore, StoreValue

__all__ = [
    "Verdict",
    "OK",
    "Warn",
    "Error",
    "validate_config_set",
    "repoint_host_src",
    "ConfigSetError",
    "RefLookup",
    "VarLookup",
    "HostExists",
]


# --------------------------------------------------------------------------- #
# Verdict — the typed validation result (S25)                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _OK:
    """The value is valid; ``config set`` may proceed with no message."""


@dataclass(frozen=True)
class Warn:
    """The value is acceptable but ``config set`` should WARN, then proceed.

    The sole B5 warn case: a host source PATH that does not exist yet (some
    sources are created-if-missing — vault is the precedent). *message* is the
    human-readable warning (the caller surfaces it; this module never prints).
    """

    message: str


@dataclass(frozen=True)
class Error:
    """The value is invalid; ``config set`` must REFUSE to write (don't poison
    the file).

    The B5 hard-error cases: malformed syntax / the forbidden ``:`` notation; a
    typed-scalar type mismatch; a dangling reference (``@``-ref to a non-existent
    key, or an unknown ``$VAR``). *message* is the human-readable reason.
    """

    message: str


#: The single OK verdict (a singleton — OK carries no data, so one instance is
#: enough; compare with ``verdict is OK`` or ``isinstance(verdict, _OK)``).
OK: _OK = _OK()

#: A ``config set`` validation verdict: proceed silently (:data:`OK`), proceed
#: with a warning (:class:`Warn`), or refuse (:class:`Error`).
Verdict = Union[_OK, Warn, Error]

# Callback aliases (block 7 wires the real snapshot / env / filesystem; tests
# pass simple stubs). All are PURE from this module's perspective.
#: Does this dotted CONFIG key exist (anywhere in the resolved keyspace)? Used to
#: validate an ``@``-ref points at a real key WITHOUT resolving it to a literal.
RefLookup = Callable[[str], bool]
#: Is this ``$VAR`` name a known/resolvable environment variable in context?
VarLookup = Callable[[str], bool]
#: Does this host source PATH exist on disk yet? (A miss → WARN, not error.)
HostExists = Callable[[str], bool]


class ConfigSetError(Exception):
    """Raised by :func:`repoint_host_src` when the write cannot proceed.

    The write-path counterpart to an :class:`Error` verdict: a key that does not
    already exist at the command scope (source-only repoints, never creates — S24)
    or a stored value that is not a category tuple. Validation (:func:`validate_
    config_set`) returns a verdict; the write raises, because reaching the write
    with an un-creatable / non-category key is a caller contract breach, not user
    input to soft-report.
    """


# --------------------------------------------------------------------------- #
# Reference / variable token extraction — reuse the resolver parse grammar    #
# --------------------------------------------------------------------------- #


def _scan_tokens(value: str) -> tuple[list[str], list[str]]:
    """Scan *value* for ``@``-ref and ``$VAR`` token NAMES, raising on a malformed
    token. Returns ``(ref_names, var_names)`` — WITHOUT resolving anything (design
    §6d: validate references for well-formedness, never expand to a literal).

    This mirrors :func:`kanibako.settings_resolve.expand_expr`'s scanner EXACTLY
    (the same escape rule, the same ``$VAR`` / ``${VAR}`` and ``@ref`` token shapes
    via the shared ``_VAR_NAME_RE`` / ``_REF_NAME_RE``), so "well-formed" here means
    EXACTLY what the build expander will later accept — one grammar, not a second
    (S25). A leading ``~`` is the home token (environment, validated for existence
    elsewhere / box-deferred); it carries no name to check. A malformed ``$`` /
    ``@`` token raises :class:`ValueError` (the caller maps it to an
    :class:`Error`).
    """
    refs: list[str] = []
    var_names: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        c = value[i]
        if c == "\\":
            # An escape consumes the next char as a literal (so ``\@`` / ``\$`` are
            # NOT tokens) — matching expand_expr's escape handling.
            i += 2
            continue
        if c == "$":
            braced = i + 1 < n and value[i + 1] == "{"
            name_start = i + 2 if braced else i + 1
            m = _VAR_NAME_RE.match(value, name_start)
            if m is None:
                raise ValueError(f"malformed variable reference at {value[i:]!r}")
            end = m.end()
            if braced:
                if end >= n or value[end] != "}":
                    raise ValueError(
                        f"unterminated ${{...}} reference at {value[i:]!r}"
                    )
                end += 1
            var_names.append(m.group(0))
            i = end
            continue
        if c == "@":
            m = _REF_NAME_RE.match(value, i + 1)
            if m is None:
                raise ValueError(f"malformed @-reference at {value[i:]!r}")
            refs.append(m.group(0))
            i = m.end()
            continue
        i += 1
    return refs, var_names


# --------------------------------------------------------------------------- #
# validate_config_set — the B5 set-time validation (S25)                      #
# --------------------------------------------------------------------------- #


def validate_config_set(
    key: str,
    value: str,
    *,
    is_category: bool,
    ref_exists: RefLookup,
    var_known: VarLookup,
    host_exists: HostExists | None = None,
) -> Verdict:
    """Validate a ``config set`` ``(key, value)`` at set-time (B5), returning a
    :class:`Verdict`. PURE — no file / env / clock access of its own (its
    filesystem/snapshot/env reach is the injected callbacks).

    *key* is the canonical config key; *value* is the user's RAW input (for a
    category key this is the new ``host_src``; for a scalar key it is the whole
    value). *is_category* tells the two apart (the caller — block 7 — knows from
    the key registry whether the key is a bind-shaped category). The three
    callbacks inject the snapshot / env / filesystem so this function stays pure
    and testable:

    * *ref_exists(dotted)* → does this ``@``-ref name a real config key? (existence
      only — NOT a resolution to a literal, per §6d / spec §0).
    * *var_known(name)* → is this ``$VAR`` a known/resolvable env var in context?
    * *host_exists(path)* → does this host source path exist on disk yet? (Only
      consulted for a category key whose ``host_src`` is a plain LITERAL path —
      a missing path → WARN, never error. Defaults to "exists" when omitted.)

    .. note::
       The not-yet-existent-host-path WARN is real B5 behavior, NOT optional: the
       caller (block 7's CLI wiring) MUST pass *host_exists* for every category
       key, or the WARN branch can never fire. It is a parameter (default "exists")
       only to keep this function PURE — the filesystem reach is injected, not
       imported — not to make the warn discretionary.

    Severity (design §6d B5):

    * **Error** — malformed syntax / the forbidden ``:`` ``src:dest`` notation; a
      typed-scalar type mismatch; a dangling ``@``-ref (``ref_exists`` False) or an
      unknown ``$VAR`` (``var_known`` False).
    * **Warn** — a category ``host_src`` that is a plain literal path not present
      on disk yet.
    * **OK** otherwise. Repointing an ``@``-ref (B4) is NOT warned.
    """
    # 1. The forbidden ``:`` ``src:dest`` notation (spec §2a — source-only has no
    #    delimiter; a tuple is never a colon-joined string). ``split_bind`` returns
    #    a non-None 2nd half iff an UNESCAPED ``:`` is present — the exact parse the
    #    resolver uses, so an ESCAPED ``\:`` (a literal colon in a path) is allowed.
    _src, dest = split_bind(value)
    if dest is not None:
        return Error(
            f"'{key}': the ':' src:dest notation is not allowed "
            f"(config set is source-only; got {value!r}). Use the value alone; "
            f"to embed a literal ':' in a path, escape it as '\\:'."
        )

    # 2. Token well-formedness + dangling-reference check (reuse the resolver parse
    #    grammar; NEVER resolve to a literal — §6d / spec §0).
    try:
        ref_names, var_names = _scan_tokens(value)
    except ValueError as exc:
        return Error(f"'{key}': malformed value {value!r}: {exc}")
    for ref in ref_names:
        if not ref_exists(ref):
            return Error(
                f"'{key}': dangling @-reference '@{ref}' "
                f"(no such config key in the keyspace)."
            )
    for var in var_names:
        if not var_known(var):
            return Error(
                f"'{key}': unknown variable '${var}' "
                f"(not a known/resolvable environment variable)."
            )

    # 3. Typed scalar keys — reuse the key registry's coercion (the H2 check). A
    #    typed key whose value cannot coerce → Error. Category keys are not typed
    #    scalars (their value is a path expression), so skip this for them. A value
    #    carrying any token (``@``/``$``/leading ``~``) is a reference expression,
    #    not a literal scalar to type-check — its terminal type is only known after
    #    build, so don't type-coerce it here (§0 files store UNRESOLVED).
    if not is_category and key in KEY_TYPES and not (ref_names or var_names):
        coerced = _coerce_value(key, value)
        # Mirror the live setter's failure check (set_config_value): for a TYPED
        # key (``KEY_TYPES.get(key)`` truthy), ``_coerce_value`` returns a ``str``
        # ONLY when coercion FAILED (success yields the typed Python value, e.g. a
        # real ``bool``). We already gate on ``key in KEY_TYPES``, so any ``str``
        # here IS the H2 coercion-failure signal — no brittle prefix match needed.
        if isinstance(coerced, str) and KEY_TYPES.get(key):
            return Error(f"'{key}': {coerced}")

    # 4. A category ``host_src`` that is a plain literal path (no tokens) which does
    #    NOT exist on disk yet → WARN, proceed (created-if-missing; vault precedent).
    #    A token-bearing host_src is not a concrete host path here (it resolves at
    #    build), so it is not path-checked.
    if is_category and not (ref_names or var_names) and host_exists is not None:
        # A leading ``~`` is a home-relative path; strip it for the existence probe
        # is the caller's job (it owns home resolution). We only path-check a value
        # the caller can resolve — pass the raw literal; the caller's host_exists
        # decides. (host_exists default omitted ⇒ no warn, treated as present.)
        if not host_exists(value):
            return Warn(
                f"'{key}': host source path {value!r} does not exist yet "
                f"(it will be created if the source is created-if-missing)."
            )

    return OK


# --------------------------------------------------------------------------- #
# repoint_host_src — the RAW category write-back (S24)                         #
# --------------------------------------------------------------------------- #


def repoint_host_src(
    scope_path: Path,
    key: str,
    new_host_src: str,
) -> None:
    """Repoint a category key's ``host_src`` in the COMMAND-scope file, RAW (S24).

    Reads the EXISTING raw tuple at dotted *key* in *scope_path* (the command's
    scope file — ``box config`` → box file, ``workset config`` → workset file, …),
    replaces ONLY element 0 (``host_src``) with *new_host_src* VERBATIM, PRESERVES
    ``box_dest`` + any options string (elements 1/2) in their RAW form, and writes
    the FULL tuple back via the existing YAML I/O. The key MUST ALREADY EXIST in
    that file (source-only repoints, never creates — there is no way for a
    source-only edit to name a dest); an absent key raises :class:`ConfigSetError`.

    The stored value is RAW — ``@``-refs / ``$XDG`` / ``~`` in *new_host_src* AND in
    the preserved ``box_dest`` / options are written verbatim, never expanded to a
    literal (S12/S24 / spec §0 "files store UNRESOLVED"). The on-disk leaf stays a
    structured YAML list (the §2a representation), never a colon-joined string.

    *new_host_src* is the user's already-validated raw input
    (:func:`validate_config_set` ran first); this function performs the file edit
    only, and does NOT re-validate.
    """
    data = load_doc(scope_path)
    parts = key.split(".")

    # Walk to the leaf, requiring every intermediate table AND the leaf to already
    # exist (key-must-exist, S24). A plain ``dict`` walk on the loaded YAML — these
    # are raw mappings, not a KeyStore — so no collision-bypass is needed here.
    node: object = data
    for seg in parts[:-1]:
        if not isinstance(node, dict) or seg not in node:
            raise ConfigSetError(
                f"config set cannot create key '{key}': it must already exist at "
                f"this scope ({scope_path}). config set is source-only and repoints "
                f"an existing bind; it never creates one."
            )
        node = node[seg]
    leaf_name = parts[-1]
    if not isinstance(node, dict) or leaf_name not in node:
        raise ConfigSetError(
            f"config set cannot create key '{key}': it must already exist at this "
            f"scope ({scope_path}). config set is source-only and repoints an "
            f"existing bind; it never creates one."
        )

    existing = node[leaf_name]
    if not isinstance(existing, (list, tuple)) or not (2 <= len(existing) <= 3):
        raise ConfigSetError(
            f"config set cannot repoint '{key}': its stored value is not a category "
            f"tuple [host_src, box_dest[, options]] "
            f"(got {type(existing).__name__}: {existing!r})."
        )

    # Swap element 0 (host_src), PRESERVE box_dest + any options RAW. Store as a
    # plain list (the §2a YAML representation; round-trips through config_io).
    new_tuple = [new_host_src, *list(existing[1:])]
    node[leaf_name] = new_tuple
    dump_doc(scope_path, data)


# --------------------------------------------------------------------------- #
# snapshot-backed RefLookup helper (a convenience for block-7 wiring)         #
# --------------------------------------------------------------------------- #


def make_ref_lookup(snapshot: KeyStore) -> RefLookup:
    """Build a :data:`RefLookup` over a resolved/raw :class:`KeyStore` *snapshot*.

    Returns a predicate ``ref_exists(dotted)`` → True iff the dotted path names a
    present key in *snapshot* (existence only — it does NOT resolve the value to a
    literal, per §6d / spec §0). Walks the dotted segments with the UNBOUND
    ``dict.get(node, seg, _MISSING)`` probe (S3): a key named ``get`` / ``items``
    cannot shadow the protocol, and a present-``None`` leaf still counts as
    EXISTING (it is set, just to None — a legitimate ``@``-ref target, §3).
    """

    def ref_exists(dotted: str) -> bool:
        node: StoreValue = snapshot
        for seg in dotted.split("."):
            if not isinstance(node, KeyStore):
                return False
            got = dict.get(node, seg, _MISSING)
            if got is _MISSING:
                return False
            node = got
        return True

    return ref_exists

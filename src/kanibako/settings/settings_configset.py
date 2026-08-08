"""``config set`` — the set-time VALIDATION.

Block 5 of the KeyStore implementation. :func:`validate_config_set` implements the
KeyStore ``config set`` value check (design §6d / spec §2a): a PURE function
returning a typed :class:`Verdict` (``OK`` / :class:`Error`). It REUSES the
resolver's parse grammar (the resolver's OWN ``match_var`` / ``match_ref`` token
parsers, called rather than re-derived, inside the same escape-aware scan as
:func:`kanibako.settings.settings_resolve.expand_expr`) and the
:mod:`kanibako.settings.config_keys` key registry (``KEY_TYPES`` /
``_coerce_value``) — it does NOT invent a second validator (S25). Q9 (spec §2a,
ruling 2026-06-29): the dangling/unknown/cycle judgement is FULL RESOLUTION via
the injected E3 ``resolves`` probe (does the edited value resolve cleanly
post-edit?), NOT the retired conservative per-token existence check. The FILE
still stores the RAW (unresolved) form — resolution is for the CHECK only
(§0 "files store UNRESOLVED").

⚑⚑⚑ **THIS MODULE IS SCALAR-ONLY, AND THAT IS A RULING** (DS-BL1 = (a), Jei
2026-08-07g — *"accept the loss uniformly"*). Every bind-shaped category is
YAML-only: ``config set``/``reset`` refuse all six BY NAME in the verb preamble and
NOTHING routes a category write. The category half of this module was deleted in
QA′ (2026-08-08, on Jei's word) rather than left to rot:

* ``repoint_host_src`` — the RAW category write-back (S24), with
  ``_bindings_arm_of``, ``_refuse_stale_bind_shape`` and ``ConfigSetError``. Its
  last caller was ``config_interface._set_category_value``, deleted with the route.
  ⚑ R-8's THREE-ELEMENT stale-shape refusal went with it. That refusal was a
  RULING (Jei, 2026-08-06e: option A — docs only; the 2-element heuristic was
  option B and was DECLINED), so it is recorded here rather than merely dropped:
  if a category write route is ever rebuilt, R-8 must be rebuilt with it.
* ``validate_config_set``'s ``is_category=True`` arm — the ``:`` ``src:dest``
  refusal, the bare-relative refusal with ``_rooted_form_hint``, and the
  not-yet-existent-host-path ``Warn`` (whose deletion left the ``Verdict`` union
  with no warn member, so ``Warn`` and the ``HostExists`` callback went too).
  The live caller — ``config_interface.set_config_value``'s E3 set-time probe —
  always passed ``is_category=False``, so this is a zero-behaviour-change deletion.

**Do not rebuild either half without a spec edit and a fresh ruling.**

B5 severity split (design §6d, RATIFIED by Jei 2026-06-27), as it stands with the
category arm gone:

* **Hard ERROR, refuse to write** (don't poison the file): malformed ``$``/``@``
  token syntax; a type mismatch for a typed scalar key; a dangling reference (an
  ``@``-ref to a non-existent config key, or an unknown ``$VAR``) in the edited
  value's own post-edit chain.
* **NO ``@``-ref-repoint warning (B4).** Repointing a value's ``@``-ref to a
  literal (or vice-versa) is a normal, explicit file edit at the command's scope
  — identical to a hand-edit of the YAML — so it does NOT warn.

OUT of scope (hard boundaries): NO CLI wiring / NO rewrite of
``set_config_value``'s routing / does NOT touch ``cli.py`` or the ``config``
subcommands. NO merge / expansion / views / consumer swap. NO ``@``-ref / ``$VAR``
/ ``~`` resolution to literals — files store UNRESOLVED.

Authority
---------
* ``~/vault/rw/keystore-design.md`` §6d (``config set`` + B4 + B5 — PRIMARY),
  §2 / §6a (files store UNRESOLVED).
* Spec ``settings-keyspace-1.8.0.md`` §2a (config-set block: source-only,
  key-must-exist, value types), §0 (files store UNRESOLVED).

Seams realized here (``plans/keystore-blocks/SEAMS.md``)
-------------------------------------------------------
* **S25** — validation REUSES the resolver parse + the key registry (one
  validator). The hard-error split is exactly B5; NO ``@``-ref-repoint warning (B4).
  ⚑ **S24** (the ``config set`` RAW write-back) is NO LONGER REALIZED ANYWHERE:
  its whole surface was the CLI category repoint, retired by DS-BL1 = (a).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from kanibako.settings.config_keys import KEY_TYPES, _coerce_value
from kanibako.settings.settings_resolve import SettingsError, match_ref, match_var

__all__ = [
    "Verdict",
    "OK",
    "Error",
    "validate_config_set",
    "ResolveProbe",
]


# --------------------------------------------------------------------------- #
# Verdict — the typed validation result (S25)                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _OK:
    """The value is valid; ``config set`` may proceed with no message."""


@dataclass(frozen=True)
class Error:
    """The value is invalid; ``config set`` must REFUSE to write (don't poison
    the file).

    The B5 hard-error cases: malformed token syntax; a typed-scalar type mismatch;
    a dangling reference (``@``-ref to a non-existent key, or an unknown ``$VAR``).
    *message* is the human-readable reason.
    """

    message: str


#: The single OK verdict (a singleton — OK carries no data, so one instance is
#: enough; compare with ``verdict is OK`` or ``isinstance(verdict, _OK)``).
OK: _OK = _OK()

#: A ``config set`` validation verdict: proceed (:data:`OK`) or refuse
#: (:class:`Error`).
#:
#: ⚑ IT IS A TWO-WAY UNION NOW, AND THE MISSING THIRD MEMBER IS THE POINT. A
#: ``Warn`` variant existed for exactly one case — a category ``host_src`` naming a
#: host path that does not exist yet — and it went with the category arm in QA′.
#: A union member no code path can produce is a shape a future consumer branches on
#: for nothing, so it was deleted rather than kept "in case". Restoring a warn
#: severity means restoring a producer for it in the same change.
Verdict = Union[_OK, Error]

# Callback alias (the seam — config_interface — wires the real snapshot; tests pass
# simple stubs). PURE from this module's perspective.
#: The E3 RESOLUTION probe (Q9, spec §2a / design Q9). Given the edited ``(key,
#: raw_value)``, apply the candidate raw value into the merged COMMAND-target
#: snapshot at *key*, lenient-``expand`` it, and answer the ONE E3 question — does
#: the EDITED VALUE resolve cleanly post-edit? Returns ``None`` if it resolves
#: cleanly (ALLOW), else a human reason naming the broken UPSTREAM dependency
#: (BLOCK — a dangling ``@``-ref / unknown ``$VAR`` / cycle in the edited value's
#: own transitive chain that the edit does NOT fix). An UNRELATED / DOWNSTREAM
#: defect, or one the edit re-points away from / fixes, leaves the edited key clean
#: → ``None``. The probe NEVER resolves a stored literal (§0 files store UNRESOLVED).
ResolveProbe = Callable[[str, str], "str | None"]


# --------------------------------------------------------------------------- #
# Reference / variable token extraction — reuse the resolver parse grammar    #
# --------------------------------------------------------------------------- #


def _scan_tokens(value: str) -> tuple[list[str], list[str]]:
    """Scan *value* for ``@``-ref and ``$VAR`` token NAMES, raising on a malformed
    token. Returns ``(ref_names, var_names)`` — WITHOUT resolving anything (design
    §6d: validate references for well-formedness, never expand to a literal).

    This mirrors :func:`kanibako.settings.settings_resolve.expand_expr`'s scanner EXACTLY
    (the same escape rule, and BOTH token families via the scanner's own parsers —
    :func:`~kanibako.settings.settings_resolve.match_var` for ``$VAR`` / ``${VAR}`` and
    :func:`~kanibako.settings.settings_resolve.match_ref` for ``@ref`` / ``@{ref}``, called
    rather than re-derived), so "well-formed" here means EXACTLY what the build
    expander will later accept — one grammar, not a second (S25). Both ``@``
    spellings are accepted, bare ``@a.b`` and braced ``@{a.b}``, and a DANGLING
    braced ref is judged exactly like a dangling bare one (the E3 ``resolves``
    probe at step 3a, which sees only the ref NAME this returns). A leading ``~`` is
    the home token (environment, validated for existence elsewhere / box-deferred);
    it carries no name to check. A malformed ``$`` / ``@`` token raises
    :class:`ValueError` (the caller maps it to an :class:`Error`).
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
            # Same deal as the ``@`` arm below: the resolver's OWN parser, not a
            # third copy of it. Both token families now speak one grammar AND one
            # error style; hand-rolling either here is what let them drift.
            try:
                name, i = match_var(value, i)
            except SettingsError as exc:
                raise ValueError(str(exc)) from None
            var_names.append(name)
            continue
        if c == "@":
            # BOTH spellings via the shared parser: bare ``@a.b`` and braced
            # ``@{a.b}`` (which lets a literal suffix follow — ``@{a.b}.jsonl``
            # yields the ONE ref ``a.b``, not the swallowed ``a.b.jsonl``).
            try:
                name, i = match_ref(value, i)
            except SettingsError as exc:
                raise ValueError(str(exc)) from None
            refs.append(name)
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
    resolves: ResolveProbe,
) -> Verdict:
    """Validate a ``config set`` ``(key, value)`` at set-time (Q9 — FULL RESOLUTION
    + the E3 rule), returning a :class:`Verdict`. PURE — no file / env / clock
    access of its own (its snapshot reach is the injected callback).

    *key* is the canonical config key; *value* is the user's RAW input.

    ⚑ SCALAR KEYS ONLY. There is no ``is_category`` discrimination any more,
    because there is no category caller: DS-BL1 = (a) made every bind-shaped
    category YAML-only and QA′ deleted the arm (module docstring). The three checks
    below are the ones that were ALREADY unconditional; nothing here was widened
    from a category rule to a scalar one.

    * *resolves(key, value)* — the E3 RESOLUTION probe (Q9 / spec §2a). The caller
      builds the full lenient COMMAND-target snapshot (assemble→merge→expand
      [lenient]), applies *value* at *key*, and answers: does the EDITED VALUE
      resolve cleanly post-edit? ``None`` = clean (ALLOW); a reason string = the
      edited value's own transitive UPSTREAM chain stays unresolvable (BLOCK —
      naming the broken dep). This REPLACES the conservative per-token existence
      check (the retired ``ref_exists``/``var_known``): a dangling ``@``-ref /
      unknown ``$VAR`` / cycle now BLOCKS only when it is IN the edited value's
      post-edit chain; an UNRELATED / DOWNSTREAM defect, or one the edit fixes,
      ALLOWS — so ``config set`` stays usable to REPAIR a broken config.

    Severity (spec §2a, the Q9 + E3 ruling):

    * **Error** — malformed token syntax; a typed-scalar type mismatch; OR the
      edited value's own transitive upstream chain stays unresolvable post-edit
      (``resolves`` returns a reason — a dangling ``@``-ref / unknown ``$VAR`` /
      cycle the edit does NOT fix).
    * **OK** otherwise. Repointing an ``@``-ref (B4) is NOT warned; an UNRELATED /
      DOWNSTREAM defect does NOT block.

    ⚑ A COLON IS ORDINARY CONTENT HERE. The forbidden ``:`` ``src:dest`` notation
    was a CATEGORY rule about the bind SHAPE (a structured pair spelled as a joined
    string); a scalar has no such shape, and ``endpoint =
    https://api.anthropic.com`` is the obvious value that must pass. Do not
    reintroduce a colon check on this path.
    """
    # 1. Token well-formedness (reuse the resolver parse grammar; NEVER resolve to a
    #    literal — §0). This is a fast, pure pre-check for MALFORMED ``$``/``@``
    #    syntax (an unterminated ``${`` / a bare ``$`` etc.) before any snapshot
    #    work; it also tells us whether the value bears tokens (so a token-bearing
    #    value is not type-coerced at step 3). Dangling/unknown/cycle is NOT judged
    #    here — that is the E3 resolution probe's job (step 2).
    try:
        ref_names, var_names = _scan_tokens(value)
    except ValueError as exc:
        return Error(f"'{key}': malformed value {value!r}: {exc}")

    # 2. E3 FULL-RESOLUTION check (Q9, spec §2a): does the edited value resolve
    #    cleanly post-edit? A reason → the edited value's own transitive UPSTREAM
    #    chain is unresolvable (dangling / unknown ``$VAR`` / cycle the edit does
    #    not fix) → BLOCK, naming the broken dep. An UNRELATED / DOWNSTREAM defect,
    #    or one the edit fixes, leaves the edited key clean → no reason → continue.
    reason = resolves(key, value)
    if reason is not None:
        return Error(f"'{key}': {reason}")

    # 3. Typed scalar keys — reuse the key registry's coercion (the H2 check). A
    #    typed key whose value cannot coerce → Error. A value carrying any token
    #    (``@``/``$``/leading ``~``) is a reference expression, not a literal scalar
    #    to type-check — its terminal type is only known after build, so don't
    #    type-coerce it here (§0 files store UNRESOLVED).
    if key in KEY_TYPES and not (ref_names or var_names):
        coerced = _coerce_value(key, value)
        # Mirror the live setter's failure check (set_config_value): for a TYPED
        # key (``KEY_TYPES.get(key)`` truthy), ``_coerce_value`` returns a ``str``
        # ONLY when coercion FAILED (success yields the typed Python value, e.g. a
        # real ``bool``). We already gate on ``key in KEY_TYPES``, so any ``str``
        # here IS the H2 coercion-failure signal — no brittle prefix match needed.
        if isinstance(coerced, str) and KEY_TYPES.get(key):
            return Error(f"'{key}': {coerced}")

    return OK

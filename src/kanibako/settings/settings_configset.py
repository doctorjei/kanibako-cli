"""``config set`` — set-time VALIDATION + the RAW category write-back.

Block 5 of the KeyStore implementation. Two cooperating pieces implement the
KeyStore ``config set`` semantics (design §6d / spec §2a), build ALONGSIDE the
live ``config_interface.set_config_value`` router (block 7 wires the CLI to call
THIS — this module does NOT touch ``cli.py`` or the live setter):

1. :func:`validate_config_set` — the set-time validation. A PURE function
   returning a typed :class:`Verdict` (``OK`` / :class:`Warn` / :class:`Error`).
   It REUSES the resolver's parse grammar (``split_bind`` for the ``:`` notation;
   the resolver's OWN ``match_var`` / ``match_ref`` token parsers, called rather
   than re-derived, inside the same escape-aware scan as
   :func:`kanibako.settings.settings_resolve.expand_expr`) and the
   :mod:`kanibako.settings.config_keys` key registry (``KEY_TYPES`` / ``_coerce_value``)
   — it does NOT invent a second validator (S25). Q9 (spec §2a, ruling 2026-06-29):
   the dangling/unknown/cycle judgement is now FULL RESOLUTION via the injected E3
   ``resolves`` probe (does the edited value resolve cleanly post-edit?), NOT the
   retired conservative per-token existence check. The FILE still stores the RAW
   (unresolved) form — resolution is for the CHECK only (§0 "files store
   UNRESOLVED").
2. :func:`repoint_host_src` — the RAW category write. On a category key it reads
   the EXISTING raw tuple — the COMMAND-scope file's own tuple when the file sets
   the key, else the effective CASCADE tuple supplied by the caller from the
   set-time merged snapshot (*cascade_bind* — F10: the key must exist in the
   CASCADE, not necessarily in the command's own file; spec §2a "the key MUST
   ALREADY EXIST in the cascade") — swaps element 0 (``host_src``) for the user's
   VERBATIM raw input, PRESERVES ``box_dest`` + any options (elements 1/2), and
   writes the FULL raw tuple to the COMMAND-scope file via the existing YAML I/O
   (``config_io``). It refuses when the key exists NOWHERE in the cascade, when
   the backing value is not a category tuple at all, when a file intermediate is
   not a mapping, and — R-8/P8 — when a ``bindings.{ro,rw}`` key's value is the
   STALE 3-element shape (:func:`_refuse_stale_bind_shape`).
   The stored form is RAW — ``@``-refs / ``$XDG`` / ``~`` are NEVER expanded to a
   literal (S12/S24).

⚑⚑⚑ **HALF OF THIS MODULE HAS NO LIVE CALLER AS OF DS-BL1 = (a)** (Jei,
2026-08-07g — *"accept the loss uniformly"*): every bind-shaped category is
YAML-only, so ``config set``/``reset`` refuse all six BY NAME in the verb preamble
and NOTHING routes a category write. Orphaned, MEASURED, and deliberately left in
place for one follow-up pass rather than half-deleted here:

* :func:`repoint_host_src` (with :func:`_bindings_arm_of`,
  :func:`_refuse_stale_bind_shape`, :class:`ConfigSetError`) — its last caller was
  ``config_interface._set_category_value``, deleted with the route.
* :func:`validate_config_set`'s ``is_category=True`` arm (the ``:`` notation
  refusal, the bare-relative refusal + :func:`_rooted_form_hint`, the
  host-path :class:`Warn`) — the live caller passes ``is_category=False``.
  ⚑ The SCALAR half is LIVE: ``config_interface.set_config_value`` runs it as the
  E3 set-time probe for every routed scalar key.

⚑ Deleting the orphans also deletes the R-8 three-element refusal
(:func:`_refuse_stale_bind_shape`) and the test that pins Jei's DECLINED option B
(the 2-element case) — a ruled behaviour, so the deletion wants his word, not a
tidy-up. **Do not build anything new on the orphans; do not restore a caller.**

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
* Spec ``settings-keyspace-1.8.0.md`` §2a (config-set block: source-only,
  key-must-exist, value types), §0 (files store UNRESOLVED).

Seams realized here (``plans/keystore-blocks/SEAMS.md``)
-------------------------------------------------------
* **S24** — ``config set`` writes the FULL RAW tuple at the COMMAND's scope,
  key-must-exist-in-the-CASCADE (F10 — the ruled §2a reading: the CLI can only
  REPOINT a bind that already has a guest mount somewhere in the cascade),
  source-only; never creates a NEW bind name, never writes an expanded literal,
  no ``:`` notation.
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
from typing import Callable, Sequence, Union

from kanibako.settings.agent_config import is_self_resolving
from kanibako.settings.config_keys import (
    KEY_TYPES,
    _coerce_value,
    parse_agent_node_bind_key,
)
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.settings_resolve import (
    SettingsError,
    match_ref,
    match_var,
    split_bind,
)

__all__ = [
    "Verdict",
    "OK",
    "Warn",
    "Error",
    "validate_config_set",
    "repoint_host_src",
    "ConfigSetError",
    "ResolveProbe",
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

# Callback aliases (the seam — config_interface — wires the real snapshot / env /
# filesystem; tests pass simple stubs). All are PURE from this module's perspective.
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
#: Does this host source PATH exist on disk yet? (A miss → WARN, not error.)
HostExists = Callable[[str], bool]


class ConfigSetError(Exception):
    """Raised by :func:`repoint_host_src` when the write cannot proceed.

    The write-path counterpart to an :class:`Error` verdict: a key that exists
    NOWHERE in the cascade (source-only repoints an existing bind, never creates
    one — S24/F10), a stored value that is not a category tuple, a STALE
    3-element value under a dest-keyed ``bindings`` arm (R-8 — see
    :func:`_refuse_stale_bind_shape`), or a file intermediate that is not a
    mapping. Validation (:func:`validate_config_set`) returns a verdict; the
    write raises, because reaching the write with an un-creatable / non-category
    key is a caller contract breach, not user input to soft-report.
    """


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
    probe at step 3a, which sees only the ref NAME this returns). A leading ``~``
    is the home token (environment, validated for existence elsewhere /
    box-deferred); it carries no name to check. A malformed ``$`` / ``@`` token
    raises :class:`ValueError` (the caller maps it to an :class:`Error`).
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


def _rooted_form_hint(key: str) -> str:
    """The rooted spelling to suggest for *key*, or ``""`` when there is none.

    ⚑ NO LIVE CALLER: reached only from :func:`validate_config_set`'s
    ``is_category=True`` arm, which lost its caller with the category write route
    (DS-BL1 = (a) — see the module docstring).

    Only the ABSTRACT categories (``common``/``caches``/``seeded``) have a
    declaration root; ``bindings.{ro,rw}`` and ``synced`` take none at ANY scope
    (spec §2a), so a relative source there gets the bare refusal — suggesting a
    rooted form for them would be inventing a root the keyspace does not have.

    ⚑⚑ IT KEYS ON THE TERMINAL KEY, NOT ON A PER-ENTRY SPELLING — and it had to be
    moved. It used to match ``BIND_KEY_RE``, which on 2026-08-08c began compiling
    its fail-closed ``(?!)`` form, so this returned ``""`` for EVERY key in the
    keyspace and the per-scope rooted cure vanished from the bare-relative-source
    refusal without a word. The category is now read off
    ``settings_keyspace.is_terminal_category_tail`` — the keyspace's OWN answer to
    "where does a category key end" — so the hint follows the shape instead of
    tracking a regex whose membership moves underneath it.

    ⚑ THE ROOT IS PER SCOPE, and reading it off the spec's own table is the point:
    §2a gives ``@config.data`` for system, ``@meta.agent.<a>.path`` for
    agent, ``@meta.workset.path`` for workset and ``@meta.box.path`` for box. A
    single agent-shaped hint would send a user editing ``workset.common`` to a
    root that has nothing to do with their key — a confidently wrong instruction,
    which is worse than saying nothing. An UNDISCRIMINATED ``agent.<category>`` gets
    no hint at all: there is no agent to name, and ``@meta.agent..path`` would be a
    ref that resolves to nothing.
    """
    from kanibako.settings.settings_categories import (
        ABSTRACT_CATEGORIES,
        DECLARATION_ROOT_REF,
    )
    from kanibako.settings.settings_keyspace import is_terminal_category_tail

    segments = key.split(".")
    if not is_terminal_category_tail(segments):
        return ""
    category = segments[-1]
    # ``bindings.{ro,rw}`` lands here as a trailing ``ro``/``rw`` and is filtered by
    # the same test as ``synced`` — neither is abstract, so neither has a root.
    if category not in ABSTRACT_CATEGORIES:
        return ""
    scope_segments = segments[:-1]
    # The agent scope is DISCRIMINATED (``agent.<node>``); name the agent the user
    # actually typed rather than a placeholder, so the hint is copy-pasteable. The
    # node may itself be dotted, so it is everything after the ``agent`` token.
    if len(scope_segments) >= 2 and scope_segments[0] == "agent":
        scope, agent = "agent", ".".join(scope_segments[1:])
    elif scope_segments in (["system"], ["workset"], ["box"]):
        scope, agent = scope_segments[0], ""
    else:
        return ""
    return f"{DECLARATION_ROOT_REF[scope].format(agent=agent)}/{category}"


def validate_config_set(
    key: str,
    value: str,
    *,
    is_category: bool,
    resolves: ResolveProbe,
    host_exists: HostExists | None = None,
) -> Verdict:
    """Validate a ``config set`` ``(key, value)`` at set-time (Q9 — FULL RESOLUTION
    + the E3 rule), returning a :class:`Verdict`. PURE — no file / env / clock
    access of its own (its snapshot/filesystem reach is the injected callbacks).

    *key* is the canonical config key; *value* is the user's RAW input (for a
    category key this is the new ``host_src``; for a scalar key it is the whole
    value). *is_category* tells the two apart (the caller knows from the key
    registry whether the key is a bind-shaped category). The callbacks inject the
    snapshot / filesystem so this function stays pure and testable:

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
    * *host_exists(path)* → does this host source path exist on disk yet? (Only
      consulted for a category key whose ``host_src`` is a plain LITERAL path —
      a missing path → WARN, never error. Defaults to "exists" when omitted.)

    .. note::
       The not-yet-existent-host-path WARN is real B5 behavior, NOT optional: the
       caller MUST pass *host_exists* for every category key, or the WARN branch can
       never fire. It is a parameter (default "exists") only to keep this function
       PURE — the filesystem reach is injected, not imported.

    Severity (spec §2a, the Q9 + E3 ruling):

    * **Error** — malformed syntax / the forbidden ``:`` ``src:dest`` notation
      (CATEGORY keys only — a colon is ordinary content in a scalar value); a
      typed-scalar type mismatch; OR the edited value's own transitive upstream
      chain stays unresolvable post-edit (``resolves`` returns a reason — a
      dangling ``@``-ref / unknown ``$VAR`` / cycle the edit does NOT fix).
    * **Warn** — a category ``host_src`` that is a plain literal path not present
      on disk yet.
    * **OK** otherwise. Repointing an ``@``-ref (B4) is NOT warned; an UNRELATED /
      DOWNSTREAM defect does NOT block.
    """
    # 1. The forbidden ``:`` ``src:dest`` notation (spec §2a — source-only has no
    #    delimiter; a tuple is never a colon-joined string). ``split_bind`` returns
    #    a non-None 2nd half iff an UNESCAPED ``:`` is present — the exact parse the
    #    resolver uses, so an ESCAPED ``\:`` (a literal colon in a path) is allowed.
    #
    #    ⚑ CATEGORY-ONLY, like step 1b beneath it. The rule is about the BIND
    #    SHAPE: a category value is a structured pair, so a colon in it means the
    #    author reached for the forbidden joined spelling. A SCALAR key has no
    #    such shape and a colon in it is ordinary content — ``endpoint =
    #    https://api.anthropic.com`` is the obvious case. Ungated, this refused
    #    every such value the moment a scalar caller existed, which is exactly
    #    what happened when the E3 probe was wired to the scalar path.
    _src, dest = split_bind(value)
    if is_category and dest is not None:
        return Error(
            f"'{key}': the ':' src:dest notation is not allowed "
            f"(config set is source-only; got {value!r}). Use the value alone; "
            f"to embed a literal ':' in a path, escape it as '\\:'."
        )

    # 1b. A category ``host_src`` must FULLY RESOLVE ON ITS OWN (spec §2a
    #     ): absolute, ``~``, ``$var`` or an ``@``-ref. A bare relative
    #     source is REFUSED at set time.
    #
    #     ⚑ Why this cannot be left to the resolution probe below: a literal
    #     ``plugins`` resolves PERFECTLY — to the relative string ``plugins``,
    #     which the mount spec then interprets against whatever the launching
    #     process's CWD happens to be. It is not a broken value, it is a value that
    #     silently means somewhere else. Nothing downstream can catch it, because
    #     the assembly-time layer that used to supply a root is GONE: sources are
    #     rooted AT DECLARATION now, and ``config set`` is not a declaration site.
    #
    #     ⚑ Why REFUSE rather than absolutise (asymmetric with ``workset share
    #     add``, deliberately): ``share add`` DOCUMENTED a join and must keep
    #     producing the same mount for the same input, so it preserves that promise
    #     by joining at write time. ``config set`` never promised one, so there is
    #     nothing to preserve — and no defensible root to invent, since the key may
    #     name any scope and any category. The message shows the rooted form for an
    #     ABSTRACT category, read PER SCOPE off the spec's declaration-root table
    #     (:data:`~kanibako.settings.settings_categories.DECLARATION_ROOT_REF`) — and the
    #     set-time snapshot materializes ``meta.agent.<a>.path``, so the suggested
    #     value is one the very next ``config set`` ACCEPTS.
    if is_category and not is_self_resolving(value):
        rooted = _rooted_form_hint(key)
        hint = f" The rooted form for this key is '{rooted}/{value}'." if rooted else ""
        return Error(
            f"'{key}': host source {value!r} is a bare relative path and cannot "
            f"resolve on its own. Give an absolute path, '~/…', '$VAR' or an "
            f"'@'-reference (spec §2a: a stored source carries no implicit "
            f"root).{hint}"
        )

    # 2. Token well-formedness (reuse the resolver parse grammar; NEVER resolve to a
    #    literal — §0). This is a fast, pure pre-check for MALFORMED ``$``/``@``
    #    syntax (an unterminated ``${`` / a bare ``$`` etc.) before any snapshot
    #    work; it also tells us whether the value bears tokens (so a token-bearing
    #    host_src is NOT path-checked at step 4). Dangling/unknown/cycle is NO
    #    longer judged here — that is the E3 resolution probe's job (step 3a).
    try:
        ref_names, var_names = _scan_tokens(value)
    except ValueError as exc:
        return Error(f"'{key}': malformed value {value!r}: {exc}")

    # 3a. E3 FULL-RESOLUTION check (Q9, spec §2a): does the edited value resolve
    #     cleanly post-edit? A reason → the edited value's own transitive UPSTREAM
    #     chain is unresolvable (dangling / unknown ``$VAR`` / cycle the edit does
    #     not fix) → BLOCK, naming the broken dep. An UNRELATED / DOWNSTREAM defect,
    #     or one the edit fixes, leaves the edited key clean → no reason → continue.
    reason = resolves(key, value)
    if reason is not None:
        return Error(f"'{key}': {reason}")

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


def _bindings_arm_of(key: str) -> str:
    """The dest-keyed ARM *key* names an entry inside, or ``""`` when it names none.

    ⚑ THE NAME IS HISTORICAL AND NARROWER THAN THE ANSWER. When it was written the
    ``bindings.{ro,rw}`` arms were the only dest-keyed categories; since 2026-08-08c
    all six are, so ``box.caches.x`` gets ``box.caches`` back exactly as
    ``box.bindings.ro.x`` gets ``box.bindings.ro``. The rename is owed and is not
    taken here (it would collide with a test pass in flight); read "arm" as "any
    dest-keyed bind-shaped category key", which is what it has always MEANT.

    Recognition REUSES the two existing retired-spelling parsers rather than a
    third copy of the grammar — :data:`~kanibako.settings.settings_categories.SCOPE_BIND_KEY_RE`
    for ``{system,workset,box}.bindings.{ro,rw}.<name>`` and
    :func:`~kanibako.settings.config_keys.parse_agent_node_bind_key` for
    ``agent.<node>.bindings.{ro,rw}.<name>`` (which splits the node segment
    non-greedily, so it cannot be folded into the regex). Those two are exactly
    the pair ``config set``/``reset`` already refuse by name (R-9); this reads the
    same two spellings for a different question — WHICH ARM a value belongs to,
    so the refusal below can name it. ``settings_categories`` is imported inside
    the function, matching :func:`_rooted_form_hint`'s deferral in this module.

    ⚑ It answers about the KEY, never the file location: *dest_parts* moves where
    :func:`repoint_host_src` WRITES, but the value SHAPE follows the key's category.

    ⚑⚑ THE TERMINAL FILTER IS A TAUTOLOGY TODAY, AND IT IS KEPT ANYWAY — read the
    reason before deleting it. It exists because R-8 is about the DEST-KEYED shape,
    NOT about being retired, and those were once different sets:
    ``SCOPE_BIND_KEY_RE`` has matched all six since DS-BL1 = (a) emptied
    ``SETTABLE_BIND_CATEGORIES``, while ``_TERMINAL_BIND_CATEGORIES`` held only the
    two ``bindings`` arms, so without the filter a NAME-KEYED ``box.common`` would
    have been reported as an arm and :func:`_refuse_stale_bind_shape` would have
    refused a perfectly good ``[host_src, box_dest, options]``. The 2026-08-08c
    shape flip made every bind-shaped category terminal, so the two sets now
    COINCIDE and the test can no longer fail — which changes nothing about WHICH
    question is being asked. ⚑ It is the question that is load-bearing: this must
    keep asking ``_TERMINAL_BIND_CATEGORIES`` rather than trusting the regex,
    because re-admitting a name-keyed category would separate the sets again and a
    dropped filter would then refuse live values with no edit here to blame.
    """
    from kanibako.settings.settings_categories import (
        SCOPE_BIND_KEY_RE,
        _TERMINAL_BIND_CATEGORIES,
    )

    m = SCOPE_BIND_KEY_RE.match(key)
    if m is not None and m.group("category") in _TERMINAL_BIND_CATEGORIES:
        return f"{m.group('scope')}.{m.group('category')}"
    parsed = parse_agent_node_bind_key(key)
    if parsed is not None:
        node_raw, category, _name = parsed
        return f"agent.{node_raw}.{category}"
    return ""


def _refuse_stale_bind_shape(
    key: str, arm: str, value: "Sequence[str]", origin: str
) -> None:
    """R-8: refuse a STALE 3-element bind value under a dest-keyed arm, by name.

    A ``bindings.{ro,rw}`` arm is a TERMINAL key holding ``{box_dest: [host_src[,
    options]]}`` (R-5/R-6): the destination is the map KEY, so an ENTRY value is
    1- or 2-element and a 3-element ``[host_src, box_dest, options]`` is
    unambiguously the RETIRED name-keyed shape. Carrying it through would write
    element 1 back as a ``box_dest`` — a mount at a destination no longer read
    from the value — so it is REFUSED, naming both the stale shape and the key
    (spec §0: refuse loudly, never quietly reinterpret).

    *arm* is :func:`_bindings_arm_of`'s answer (``""`` ⇒ nothing to check: *key*
    names no dest-keyed entry at all). ⚑ IT NOW COVERS ALL SIX. The older note here
    said the four ``caches``/``seeded``/``common``/``synced`` were exempt because
    they "still carry a per-name key and their 3-element form is LIVE"; the shape
    cutover it named as future work (phase QB) landed on 2026-08-08c, so their
    3-element form is the stale name-keyed shape too and R-8 applies to them for
    exactly the reason it applied to a ``bindings`` arm.
    *origin* labels where the value came from, matching the sibling arity messages.

    ⚑⚑ THREE-ELEMENT ONLY, AND THAT IS A RULING (Jei, 2026-08-06e), not an
    oversight to tidy up later. A stored ``[src, box_dest]`` and a live
    ``[src, options]`` are BOTH legally 2 elements and cannot be told apart; the
    heuristic refusal ("refuse when element 1 looks like a path") was offered as
    option B and DECLINED in favour of option A — DOCS ONLY, no runtime refusal
    (DS-BL8/8a). Do NOT extend this to the 2-element case; that needs a FRESH
    ruling, and R-8 does not reach it by inference.
    """
    if not arm or len(value) != 3:
        return
    raise ConfigSetError(
        f"config set cannot repoint '{key}': {origin} is a STALE 3-element "
        f"[host_src, box_dest, options] bind — the retired name-keyed shape "
        f"(got {list(value)!r}). '{arm}' is a dest-keyed TERMINAL arm "
        f"{{box_dest: [host_src[, options]]}} whose entries carry NO box_dest "
        f"(R-5/R-6); rewriting element 1 as one would mount at a destination the "
        f"arm no longer reads from the value. Refusing rather than reinterpreting."
    )


def repoint_host_src(
    scope_path: Path,
    key: str,
    new_host_src: str,
    *,
    cascade_bind: "Sequence[str] | None" = None,
    dest_parts: "Sequence[str] | None" = None,
) -> None:
    """Repoint a category key's ``host_src`` in the COMMAND-scope file, RAW (S24).

    ⚑⚑⚑ **NO LIVE CALLER SINCE DS-BL1 = (a).** Its one caller,
    ``config_interface._set_category_value``, was deleted with the ``config set``
    category route; every bind-shaped category is now YAML-only and the verbs refuse
    all six by name. Retained pending one deliberate deletion pass — see the module
    docstring for what goes with it (the R-8 refusal below is a RULED behaviour).
    Unit-tested still, so it is provably intact rather than rotting.

    Finds the EXISTING raw tuple for dotted *key*: the tuple in *scope_path* (the
    command's scope file — ``box set`` → box file, ``workset set`` → workset
    file, …) when that file sets the key, else *cascade_bind* — the key's
    effective RAW tuple from the set-time merged cascade snapshot, supplied by
    the caller (F10; spec §2a: "the key MUST ALREADY EXIST in the cascade" — the
    CLI can only REPOINT a bind that already has a guest mount SOMEWHERE in the
    cascade, never CREATE one). It then replaces ONLY element 0 (``host_src``)
    with *new_host_src* VERBATIM, PRESERVES ``box_dest`` + any options string
    (elements 1/2) in their RAW form, and writes the FULL tuple to the
    COMMAND-scope file via the existing YAML I/O (creating the file's intermediate
    tables when the tuple comes from the cascade). A key that exists NOWHERE —
    absent from the file AND no *cascade_bind* — raises :class:`ConfigSetError`.

    The stored value is RAW — ``@``-refs / ``$XDG`` / ``~`` in *new_host_src* AND in
    the preserved ``box_dest`` / options are written verbatim, never expanded to a
    literal (S12/S24 / spec §0 "files store UNRESOLVED"). The on-disk leaf stays a
    structured YAML list (the §2a representation), never a colon-joined string.

    *new_host_src* is the user's already-validated raw input
    (:func:`validate_config_set` ran first); this function performs the file edit
    only, and does NOT re-validate. *cascade_bind* is the caller's cascade lookup
    result, already normalized to a plain 2-/3-element sequence of RAW strings
    (pre-expansion — the merge stores files' tuples verbatim).

    ⚑ WHICH KEYS STILL ARRIVE HERE: NONE. The older note here listed the four
    ``caches``/``seeded``/``common``/``synced`` as the live arrivals and called them
    "still NAME-keyed, so their 2-/3-element form is LIVE"; both halves are now
    false — DS-BL1 = (a) took their CLI write route (so nothing routes a key here at
    all) and the 2026-08-08c shape flip made them dest-keyed TERMINAL keys like the
    ``bindings`` arms. The arity bracket below is therefore not a live acceptance of
    a name-keyed shape: it is the pre-flip contract this function was written
    against, preserved intact until the deletion pass.

    ⚑⚑ R-8, P8 — a key that DOES reach here (this function is pure and
    key-agnostic; only its callers are gated) is checked against the dest-keyed
    shape by :func:`_refuse_stale_bind_shape`: a 3-element value is the retired
    name-keyed shape and RAISES, naming the arm and the stale tuple. That check now
    fires for all six categories, because all six are dest-keyed. It is the
    3-element case ONLY — the 2-element residual is a ruled, documented accept (see
    the ``NAME THE ELEMENTS`` note at the write). This is defence in depth, not a
    live route.
    """
    data = load_doc(scope_path)
    # *dest_parts*, when supplied, is the FILE location to walk/write (sections +
    # leaf), overriding the default "split the canonical key". ⚑ NO CALLER SUPPLIES
    # IT TODAY: its one user was the per-agent bind repoint, which had to write under
    # the node file's ``self`` table rather than at the key's canonical split, and
    # R-9 retired that route. The parameter stays because this function is pure and
    # key-agnostic — it takes an arbitrary dotted path and edits the YAML at it.
    parts = list(dest_parts) if dest_parts is not None else key.split(".")
    leaf_name = parts[-1]

    # Walk the command-scope file toward the leaf WITHOUT requiring it: the
    # must-exist check is against the CASCADE (F10). The file's own tuple, when
    # present, is the base (it IS the cascade winner at the command's scope);
    # otherwise *cascade_bind* supplies the raw box_dest/options. A plain ``dict``
    # walk on the loaded YAML — raw mappings, not a KeyStore — so no
    # collision-bypass is needed here.
    node: object = data
    for seg in parts[:-1]:
        if not isinstance(node, dict) or seg not in node:
            node = None
            break
        node = node[seg]

    # R-8's arity refusal (P8). Computed ONCE from the KEY, then applied to
    # whichever tuple actually backs the write. ⚑ The arity gates below still
    # bracket 2..3 because that is the shape this function was written against, NOT
    # because a name-keyed category is still live — since 2026-08-08c none is, and
    # no caller routes any key here at all (see the docstring).
    arm = _bindings_arm_of(key)

    if isinstance(node, dict) and leaf_name in node:
        existing = node[leaf_name]
        if not isinstance(existing, (list, tuple)) or not (2 <= len(existing) <= 3):
            raise ConfigSetError(
                f"config set cannot repoint '{key}': its stored value is not a "
                f"category tuple [host_src, box_dest[, options]] "
                f"(got {type(existing).__name__}: {existing!r})."
            )
        _refuse_stale_bind_shape(key, arm, list(existing), "its stored value")
        base: "Sequence[str]" = list(existing)
    elif cascade_bind is not None:
        if not (2 <= len(cascade_bind) <= 3):
            raise ConfigSetError(
                f"config set cannot repoint '{key}': the cascade tuple is not a "
                f"category tuple [host_src, box_dest[, options]] "
                f"(got {list(cascade_bind)!r})."
            )
        _refuse_stale_bind_shape(key, arm, list(cascade_bind), "the cascade tuple")
        base = list(cascade_bind)
    else:
        raise ConfigSetError(
            f"config set cannot create key '{key}': it must already exist in "
            f"the cascade, and no configured settings scope sets it. config set "
            f"is source-only and repoints an existing bind; it never creates one."
        )

    # Swap host_src, PRESERVE box_dest + any options RAW. Store as a
    # plain list (the §2a YAML representation; round-trips through config_io),
    # creating the file's intermediate tables as needed (a cascade-sourced tuple
    # lands in a file that may not have them yet). An intermediate that exists but
    # is NOT a mapping is refused — overwriting it would clobber user data.
    wnode: dict = data
    for seg in parts[:-1]:
        nxt = wnode.get(seg)
        if nxt is None:
            nxt = {}
            wnode[seg] = nxt
        elif not isinstance(nxt, dict):
            raise ConfigSetError(
                f"config set cannot repoint '{key}': '{seg}' in {scope_path} is "
                f"not a mapping (got {type(nxt).__name__}: {nxt!r})."
            )
        wnode = nxt

    # ⚑ NAME THE ELEMENTS — do NOT go back to ``[new_host_src, *base[1:]]``.
    # That positional spelling is arity-safe and MEANING-BLIND: the arity gate
    # above accepts any 2-or-3 element tuple, so a value whose second element is
    # not a box_dest is carried through silently and re-stored as one. The
    # bindings rework introduced exactly such a shape (a 2-element ``(src, opts)``
    # pair — same arity, different meaning), and R-8 says a stale shape must be
    # REFUSED, never quietly reinterpreted.
    #
    # ⚑⚑ BE PRECISE ABOUT WHAT THE NAMING BUYS, because it is easy to over-read:
    # naming the elements does NOT make any shape raise. What it buys is that the
    # assumption is WRITTEN DOWN AT THE SITE and greppable, so the author who
    # flips a shape cannot walk past it. The RUNTIME refusal is a separate thing
    # and it is ``_refuse_stale_bind_shape`` above (R-8, landed in P8) — reached
    # from both arity gates.
    #
    # ⚑⚑⚑ WHAT IS STILL CARRIED THROUGH HERE, AND WHY THAT IS A RULING. The
    # refusal covers the 3-ELEMENT value under a ``bindings`` arm ONLY. Fed a
    # dest-keyed 2-element ``[src, opts]`` under such an arm, this code STILL
    # passes the gate, binds ``box_dest = opts`` and writes the same bytes the
    # positional spelling would. That is DELIBERATE: a stored ``[src, box_dest]``
    # and a live ``[src, options]`` are indistinguishable at 2 elements, the
    # heuristic refusal was offered as option B and DECLINED (option A — docs
    # only, DS-BL8/8a, Jei 2026-08-06e). Do not "finish the job" here; changing it
    # needs a fresh ruling. Implementation plan §5(a) named this the arc's prime
    # silent-breakage site — the arity half is now closed, the 2-element half is
    # an accepted, documented residual. It is unreachable in product besides: no
    # ``bindings`` key of any scope routes here (R-9 — see this function's
    # docstring), so the refusal is defence in depth on a pure, key-agnostic edit.
    box_dest = base[1]
    options = list(base[2:])  # zero or one element — options are optional
    wnode[leaf_name] = [new_host_src, box_dest, *options]
    dump_doc(scope_path, data)

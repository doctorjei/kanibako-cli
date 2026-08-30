"""``config set`` — the set-time value VALIDATION (KeyStore block 5, seam S25).

⚑ The file keeps the RAW form — resolution here is for the CHECK only (spec §0).

⚑⚑⚑ SCALAR-ONLY, AND THAT IS A RULING (DS-BL1 = (a), Jei 2026-08-07g). The category half —
``repoint_host_src`` (S24), the ``is_category`` arm, ``Warn``, R-8's stale-shape refusal — was
DELETED in QA′. Rebuilding any of it needs a spec edit and a fresh ruling; the reasoning is in
``llm-docs/kanibako/settings/settings_configset.py.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from kanibako.settings.config_keys import KEY_TYPES, CoercionError, _coerce_value
from kanibako.settings.settings_resolve import SettingsError, match_ref, match_var

__all__ = [
    "Verdict",
    "OK",
    "Error",
    "validate_config_set",
    "ResolveProbe",
]


# ---- Verdict — the typed validation result (S25) ---- #


@dataclass(frozen=True)
class _OK:
    """The value is valid; ``config set`` may proceed with no message."""


@dataclass(frozen=True)
class Error:
    """The value is invalid; ``config set`` must REFUSE to write (don't poison the file)."""

    message: str


#: The single OK verdict; it carries no data.
OK: _OK = _OK()

#: ⚑ TWO-WAY, and the missing third member is the point: ``Warn``'s only producer went with the
#: category arm. Restoring a warn severity means restoring a producer in the same change.
Verdict = Union[_OK, Error]

#: The E3 RESOLUTION probe (Q9, spec §2a), wired by ``config_interface``: does the edited
#: ``(key, raw_value)`` resolve cleanly post-edit? ``None`` = clean; else a reason naming the
#: broken UPSTREAM dependency. ⚑ A DOWNSTREAM or unrelated defect answers ``None`` — that
#: asymmetry is what keeps ``config set`` usable to REPAIR a broken config.
ResolveProbe = Callable[[str, str], "str | None"]


# ---- Token scan — the resolver's own parse grammar ---- #


def _scan_tokens(value: str) -> tuple[list[str], list[str]]:
    """Scan *value* for ``@``-ref and ``$VAR`` token NAMES, resolving nothing.

    A malformed token raises :class:`ValueError`. Mirrors ``expand_expr``'s scanner EXACTLY by
    calling its own parsers — one grammar, not a second (S25).
    """
    refs: list[str] = []
    var_names: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        c = value[i]
        if c == "\\":
            # An escape consumes the next char, so ``\@`` / ``\$`` are NOT tokens.
            i += 2
            continue
        if c == "$":
            try:
                name, i = match_var(value, i)
            except SettingsError as exc:
                raise ValueError(str(exc)) from None
            var_names.append(name)
            continue
        if c == "@":
            # Both spellings — ``@{a.b}.jsonl`` yields the ONE ref ``a.b``, not ``a.b.jsonl``.
            try:
                name, i = match_ref(value, i)
            except SettingsError as exc:
                raise ValueError(str(exc)) from None
            refs.append(name)
            continue
        i += 1
    return refs, var_names


# ---- validate_config_set — the B5 set-time validation ---- #


def validate_config_set(
    key: str,
    value: str,
    *,
    resolves: ResolveProbe,
) -> Verdict:
    """Validate a ``config set`` ``(key, value)`` at set-time, returning a :class:`Verdict`.

    PURE — no file / env / clock access; its snapshot reach is *resolves*. B5
    severity: **Error** on malformed token syntax, a typed-scalar type mismatch, or an upstream
    chain the edit leaves unresolvable; **OK** otherwise, a repoint included (B4).

    ⚑ SCALAR KEYS ONLY — no ``is_category`` discrimination any more (module docstring).
    ⚑ A COLON IS ORDINARY CONTENT HERE. The ``:`` ``src:dest`` refusal was a rule about the bind
    SHAPE, which a scalar has not; ``endpoint = https://api.anthropic.com`` must pass. Do not
    reintroduce a colon check on this path.
    """
    # 1. MALFORMED syntax only, before any snapshot work; it also tells us whether the value
    #    bears tokens (step 3 needs that). Dangling / unknown / cycle is the E3 probe's job.
    try:
        ref_names, var_names = _scan_tokens(value)
    except ValueError as exc:
        return Error(f"'{key}': malformed value {value!r}: {exc}")

    # 2. E3 FULL-RESOLUTION check (Q9) — a reason BLOCKS.
    reason = resolves(key, value)
    if reason is not None:
        return Error(f"'{key}': {reason}")

    # 3. Typed scalar keys — the H2 check, reusing the registry's coercion. A token-bearing
    #    value has no terminal type until build, so it is NOT type-checked here.
    if key in KEY_TYPES and not (ref_names or var_names):
        coerced = _coerce_value(key, value)
        # ⚑ THE FAILURE IS A TYPE, NOT A STRING. This used to read "a typed key gave back
        # a ``str``, so coercion failed" — true only while every declared type coerced to
        # a NON-string. ``path`` does not, and a legal path value would have been reported
        # as its own error message.
        if isinstance(coerced, CoercionError):
            return Error(f"'{key}': {coerced.message}")

    return OK

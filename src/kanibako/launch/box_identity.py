"""STANDALONE box identity generation, plus the box-name blocklist.

A standalone box is named ``<kuid>_<leaf>``: a stable :mod:`kanibako.kuid` prefix
stored as the settable ``workset.kuid`` key, joined by ``_`` to the project-root
basename run through :func:`sanitize_cap`.  Only the kuid is stable — the leaf is
re-derived live, so it tracks directory moves.

This module is pure (modulo ``os.urandom``/clock via :mod:`kanibako.kuid`) and
side-effect free so the sanitize/cap/collision-regen logic is directly
unit-testable.

See ``llm-docs/kanibako/launch/box_identity.py.md`` for the name grammar, the
blocklist rule and the resolve branches.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

from kanibako import kuid
from kanibako.errors import ProjectError

# Maximum length of the sanitized leaf component.
_LEAF_CAP = 32
# Fallback leaf when the project basename sanitizes to empty.
_EMPTY_LEAF_FALLBACK = "box"
# Characters that are NOT replaced during sanitization.
_SAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9._-]")
# Bound on collision-regeneration attempts before giving up.
_MAX_REGEN_ATTEMPTS = 1000

# The leaf half of the canonical ``<kuid>_<leaf>`` shape; the prefix half is
# :func:`kanibako.kuid.is_valid`.  It is the verbatim shape the generator emits,
# so it is also the grammar a user may assert with a fully-formed ``--name``.
# ⚑ The prefix alphabet is the kuid's Crockford set, NOT RFC-4648 ``[a-z2-7]``.
_LEAF_RE = re.compile(r"^[a-z0-9._-]{1,32}$")

# ---------------------------------------------------------------------------
# Box-name BLOCKLIST validation (W1 Phase D, §Design 8).  A name is rejected only
# for a blocked character or a structural rule — everything else is permitted, so
# unicode letters/digits and interior ``.`` ARE allowed.
#
# ⚑ Validate a name AFTER lowercase-folding (the ``--name`` R2 invariant):
# uppercase ASCII is not itself a violation, it is folded upstream.
# ---------------------------------------------------------------------------

# ASCII punctuation that survives (the ONLY ASCII punctuation permitted).
_ALLOWED_PUNCT = frozenset("_-.")
# ASCII punctuation that is blocked = string.punctuation minus the survivors.
_BLOCKED_ASCII_PUNCT = frozenset(string.punctuation) - _ALLOWED_PUNCT

# Suggested length bound.
_NAME_MIN_LEN = 1
_NAME_MAX_LEN = 64


def _box_name_violation(name: str) -> str | None:
    """Return a human-readable reason *name* is an invalid box name, else ``None``."""
    if len(name) < _NAME_MIN_LEN:
        return "box name must not be empty"
    if len(name) > _NAME_MAX_LEN:
        return f"box name must be at most {_NAME_MAX_LEN} characters"

    if name in (".", ".."):
        return f"box name must not be '{name}'"

    # Per-character blocklist.
    for ch in name:
        codepoint = ord(ch)
        if codepoint <= 0x1F or codepoint == 0x7F:
            return (
                f"box name must not contain control character U+{codepoint:04X}"
            )
        if ch.isspace():
            return "box name must not contain whitespace"
        if ch in _BLOCKED_ASCII_PUNCT:
            return f"box name must not contain '{ch}'"

    # Structural: leading/trailing rules.
    if name.startswith("-"):
        return "box name must not start with '-' (collides with CLI flags)"
    if name.startswith("."):
        return "box name must not start with '.' (hidden/relative)"
    if name.endswith("."):
        return "box name must not end with '.'"
    # ⚑ Redundant with the per-char whitespace block above, but kept explicit:
    # the Windows-portability intent is not recoverable from that general check.
    if name != name.rstrip():
        return "box name must not end with whitespace"

    return None


def is_valid_box_name(name: str) -> bool:
    """Return ``True`` when *name* passes the box-name blocklist (non-raising).

    The "flag, don't reject" companion to :func:`validate_box_name`: a
    pre-existing non-conforming box still resolves, it just gets warned about.
    """
    return _box_name_violation(name) is None


def box_name_reason(name: str) -> str | None:
    """Return the reason *name* is invalid (for a warning message), else ``None``."""
    return _box_name_violation(name)


def validate_box_name(name: str) -> None:
    """Raise :class:`~kanibako.errors.ProjectError` if *name* is an invalid box name.

    Enforced at creation and at ``--name`` — i.e. on NEW names only.
    """
    reason = _box_name_violation(name)
    if reason is not None:
        raise ProjectError(f"Invalid box name '{name}': {reason}")


def sanitize_cap(leaf: str) -> str:
    """Sanitize, lowercase, and cap a project-basename *leaf* for a box name.

    Non-portable characters become ``_``; every box name is lowercase.  An empty
    result — an empty or all-illegal basename — falls back to ``"box"``.
    """
    sanitized = _SAFE_CHAR_RE.sub("_", leaf).lower()[:_LEAF_CAP]
    return sanitized or _EMPTY_LEAF_FALLBACK


def is_canonical_standalone_name(name: str) -> bool:
    """True when *name* matches the canonical ``<kuid>_<leaf>`` shape.

    ⚑ Case-sensitive: callers lowercase a supplied name first.
    """
    prefix, sep, leaf = name.partition("_")
    if not sep:
        return False
    return kuid.is_valid(prefix) and _LEAF_RE.match(leaf) is not None


def standalone_kuid(name: str) -> str:
    """Return the kuid PREFIX of a standalone box *name* (``<kuid>_<leaf>``)."""
    # Everything up to the FIRST ``_``: unambiguous even when the leaf holds one,
    # because the kuid alphabet never contains ``_``.
    return name.partition("_")[0]


def compose_standalone_name(box_kuid: str, root: Path) -> str:
    """Compose the LIVE standalone box name ``<box_kuid>_<leaf>`` for *root*.

    The stored kuid stays put; the leaf is re-derived from the current basename,
    so a moved box re-composes to a new name.  Mirrors :func:`_generate_with_leaf`.
    """
    return f"{box_kuid}_{sanitize_cap(root.name)}"


def _generate_with_leaf(leaf: str, existing: set[str]) -> str:
    """Build ``<kuid>_<leaf>``, regenerating the kuid until the WHOLE name is free.

    *leaf* must already be sanitized.  Raises :class:`RuntimeError` once
    :data:`_MAX_REGEN_ATTEMPTS` is exhausted.
    """
    for _ in range(_MAX_REGEN_ATTEMPTS):
        name = f"{kuid.generate()}_{leaf}"
        if name not in existing:
            return name
    raise RuntimeError(
        "Could not generate a unique standalone box name after "
        f"{_MAX_REGEN_ATTEMPTS} attempts."
    )


def make_standalone_box_name(root: Path, existing: set[str]) -> str:
    """Generate a unique standalone box name for project *root*.

    *existing* is the set of standalone box names currently registered (see
    ``registry.standalone``).
    """
    return _generate_with_leaf(sanitize_cap(root.name), existing)


def validate_standalone_name(supplied: str, existing: set[str]) -> None:
    """Pre-flight a user-supplied standalone ``--name`` for a refusable collision.

    Raises the SAME :class:`~kanibako.errors.ProjectError` that
    :func:`resolve_standalone_name` would, on the one refusable case: a verbatim
    canonical id already in *existing*.  Every other input is satisfiable, so
    this is a no-op for them.  ⚑ Keep the two refusal messages identical.

    ⚑ Callers run this BEFORE any filesystem mutation so a doomed standalone
    ``create`` refuses up front instead of leaving a half-created tree (BUG-A).
    """
    if not supplied:
        return
    supplied = supplied.lower()
    if not is_canonical_standalone_name(supplied):
        return
    if supplied in existing:
        prefix = supplied.partition("_")[0]
        raise ProjectError(
            f"already a box with that name '{supplied}' — try without the "
            f"'{prefix}_' prefix to generate a fresh one"
        )


def resolve_standalone_name(
    root: Path, supplied: str, existing: set[str],
) -> str:
    """Resolve the standalone box name for *root* given a user *supplied* name.

    Three branches: empty → a fresh name; a non-canonical string → its whole
    text becomes the leaf; a verbatim canonical id → honored if free, refused if
    taken.  The last is the only refusable input.
    """
    if not supplied:
        return make_standalone_box_name(root, existing)

    supplied = supplied.lower()

    if not is_canonical_standalone_name(supplied):
        # Not a canonical id: use the whole supplied string as the leaf source.
        return _generate_with_leaf(sanitize_cap(supplied), existing)

    # A verbatim canonical id: honor it if free, else refuse with guidance.
    if supplied in existing:
        prefix = supplied.partition("_")[0]
        raise ProjectError(
            f"already a box with that name '{supplied}' — try without the "
            f"'{prefix}_' prefix to generate a fresh one"
        )
    return supplied

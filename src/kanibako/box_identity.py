"""STANDALONE box identity generation (``<random24>_<sanitized,capped leaf>``).

A standalone box is named ``<random24>_<leaf>`` where:

* ``random24`` is 24 bits of randomness (``os.urandom(3)``) encoded as a
  fixed-width, lowercased base32 token (no padding) — always 5 characters.
* ``leaf`` is the project-root basename, sanitized so only portable filename
  characters survive (``[^A-Za-z0-9._-]`` → ``_``), capped at 32 characters,
  with an empty leaf falling back to ``"box"``.

The pieces are joined with ``_``.  Because the random prefix can collide with
an already-registered standalone box name (design-review D-M13), the generator
regenerates the random component (bounded retries) until the *whole* name is
unique within a caller-supplied ``existing`` set.

This module is pure (modulo ``os.urandom``) and side-effect free so the
sanitize/cap/collision-regen logic is directly unit-testable.
"""

from __future__ import annotations

import base64
import os
import re
import string
from pathlib import Path

from kanibako.errors import ProjectError

# Maximum length of the sanitized leaf component.
_LEAF_CAP = 32
# Fallback leaf when the project basename sanitizes to empty.
_EMPTY_LEAF_FALLBACK = "box"
# Characters that are NOT replaced during sanitization.
_SAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9._-]")
# Bound on collision-regeneration attempts before giving up.
_MAX_REGEN_ATTEMPTS = 1000

# A canonical standalone box name is ``<random24>_<leaf>`` where the random
# prefix is exactly 5 lowercase base32 chars and the leaf is 1-32 chars drawn
# from the sanitized, *lowercased* alphabet (see :func:`sanitize_cap`).  This
# is the verbatim shape the generator emits, so it is also what a user may
# assert by passing a fully-formed ``--name``.
_CANONICAL_NAME_RE = re.compile(r"^[a-z2-7]{5}_[a-z0-9._-]{1,32}$")

# ---------------------------------------------------------------------------
# Box-name BLOCKLIST validation (W1 Phase D, §Design 8).
#
# A box name is REJECTED if it contains any blocked character or violates a
# structural rule; everything else is permitted (so unicode letters/digits and
# interior ``.`` ARE allowed — the reason this is a blocklist, not an allowlist).
# The blocked sets are defined by standard categories so the rule is COMPLETE:
#
#   * Control chars ``U+0000-U+001F`` and ``U+007F``.
#   * All whitespace (ASCII space + any Unicode whitespace, via ``str.isspace``).
#   * ASCII punctuation EXCEPT ``_ - .`` (this single set subsumes both the
#     Windows-reserved chars ``< > : " / \ | ? *`` and the POSIX shell
#     metacharacters).
#   * Structural: not ``.``/``..``; no leading ``-`` (CLI-flag collision) or
#     leading ``.`` (hidden/relative); no trailing ``.`` or whitespace
#     (Windows); length 1-64.
#
# Uppercase ASCII is NOT blocked — it is folded to lowercase by the ``--name``
# invariant (R2) BEFORE validation runs, so validate a name post-fold.
# ---------------------------------------------------------------------------

# ASCII punctuation that survives (the ONLY ASCII punctuation permitted).
_ALLOWED_PUNCT = frozenset("_-.")
# ASCII punctuation that is blocked = string.punctuation minus the survivors.
_BLOCKED_ASCII_PUNCT = frozenset(string.punctuation) - _ALLOWED_PUNCT

# Suggested length bound.
_NAME_MIN_LEN = 1
_NAME_MAX_LEN = 64


def _box_name_violation(name: str) -> str | None:
    """Return a human-readable reason *name* is an invalid box name, else ``None``.

    Pure and side-effect free.  Implements the §Design 8 blocklist + structural
    rules.  Callers pass a name that has ALREADY been lowercase-folded (the R2
    ``--name`` invariant); uppercase is not itself a violation.
    """
    if len(name) < _NAME_MIN_LEN:
        return "box name must not be empty"
    if len(name) > _NAME_MAX_LEN:
        return f"box name must be at most {_NAME_MAX_LEN} characters"

    # Structural: reserved relative-path names.
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
    # A trailing-whitespace check is redundant with the per-char whitespace
    # block above, but kept explicit for the Windows-portability intent.
    if name != name.rstrip():
        return "box name must not end with whitespace"

    return None


def is_valid_box_name(name: str) -> bool:
    """Return ``True`` when *name* passes the §Design 8 box-name blocklist.

    Non-raising companion to :func:`validate_box_name` for the "flag, don't
    reject" case (pre-existing non-conforming boxes still resolve but get
    warned).  Validate a name AFTER lowercase-folding (R2 invariant).
    """
    return _box_name_violation(name) is None


def box_name_reason(name: str) -> str | None:
    """Return the reason *name* is invalid (for a warning message), else ``None``."""
    return _box_name_violation(name)


def validate_box_name(name: str) -> None:
    """Raise :class:`~kanibako.errors.ProjectError` if *name* is an invalid box name.

    Enforced at creation / ``--name`` (NEW names).  Pure and side-effect free.
    Validate a name AFTER lowercase-folding (R2 invariant) — uppercase is not a
    violation, it is folded upstream.
    """
    reason = _box_name_violation(name)
    if reason is not None:
        raise ProjectError(f"Invalid box name '{name}': {reason}")


def random24() -> str:
    """Return a 24-bit random token as a fixed-width lowercase base32 string.

    24 bits encode to exactly 5 base32 characters (with the trailing ``=``
    padding stripped), so every token is the same width.  Lowercased for
    container/path friendliness.
    """
    raw = os.urandom(3)  # 3 bytes == 24 bits
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def sanitize_cap(leaf: str) -> str:
    """Sanitize, lowercase, and cap a project-basename *leaf* for a box name.

    Non-portable characters (anything outside ``[A-Za-z0-9._-]``) become ``_``;
    the result is lowercased (every box name is lowercase) and capped at
    :data:`_LEAF_CAP` characters.  An empty result (e.g. an empty or all-illegal
    basename) falls back to ``"box"``.
    """
    sanitized = _SAFE_CHAR_RE.sub("_", leaf).lower()[:_LEAF_CAP]
    return sanitized or _EMPTY_LEAF_FALLBACK


def is_canonical_standalone_name(name: str) -> bool:
    """True when *name* matches the canonical ``<random24>_<leaf>`` shape.

    The prefix must be exactly 5 lowercase base32 chars (``[a-z2-7]``) and the
    leaf 1-32 chars of ``[a-z0-9._-]`` (lowercase, matching :func:`sanitize_cap`).
    An over-long or illegal-char leaf, or a wrong-width prefix, fails the match.
    The check is case-sensitive: callers lowercase a supplied name first.
    """
    return _CANONICAL_NAME_RE.match(name) is not None


def _generate_with_leaf(leaf: str, existing: set[str]) -> str:
    """Build ``<random24>_<leaf>`` with whole-name collision regen.

    *leaf* must already be sanitized (see :func:`sanitize_cap`).  Regenerates
    the random prefix (bounded retries) until the whole name is unique within
    *existing*.  Shared by :func:`make_standalone_box_name` (leaf from the root
    basename) and :func:`resolve_standalone_name` (leaf from a supplied string).

    Raises :class:`RuntimeError` if a unique name cannot be found within
    :data:`_MAX_REGEN_ATTEMPTS` attempts.
    """
    for _ in range(_MAX_REGEN_ATTEMPTS):
        name = f"{random24()}_{leaf}"
        if name not in existing:
            return name
    raise RuntimeError(
        "Could not generate a unique standalone box name after "
        f"{_MAX_REGEN_ATTEMPTS} attempts."
    )


def make_standalone_box_name(root: Path, existing: set[str]) -> str:
    """Generate a unique standalone box name for project *root*.

    Builds ``<random24>_<sanitize_cap(root.name)>`` and, if that whole name is
    already in *existing*, regenerates the random prefix (bounded retries) until
    the name is unique.  *existing* is the set of standalone box names currently
    registered (see ``registry.standalone``).

    Raises :class:`RuntimeError` if a unique name cannot be found within
    :data:`_MAX_REGEN_ATTEMPTS` attempts (effectively impossible with a sane
    ``existing`` set; the bound guards against a degenerate caller).
    """
    return _generate_with_leaf(sanitize_cap(root.name), existing)


def validate_standalone_name(supplied: str, existing: set[str]) -> None:
    """Pre-flight a user-supplied standalone ``--name`` for a refusable collision.

    Pure and side-effect free.  Raises the SAME
    :class:`~kanibako.errors.ProjectError` that :func:`resolve_standalone_name`
    would on the one refusable case — a *supplied* name that is a verbatim
    canonical ``<random24>_<leaf>`` id already present in *existing*.  Every other
    input (empty, or a non-canonical string that becomes a fresh
    ``<random24>_<leaf>``) is always satisfiable, so this is a no-op for them.

    Callers run this BEFORE any filesystem mutation so a doomed standalone
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

    Pure (modulo ``os.urandom``) so it is directly unit-testable.  Branches:

    1. *supplied* empty → :func:`make_standalone_box_name` (fresh prefix +
       sanitized ``root.name`` leaf, whole-name collision regen).
    2. Otherwise lowercase *supplied*, then:

       * If it does NOT match the canonical ``<random24>_<leaf>`` shape (see
         :func:`is_canonical_standalone_name`) → treat the WHOLE supplied string
         as a raw leaf: ``<fresh-random>_<sanitize_cap(supplied)>`` with
         collision regen.  (An over-long / illegal name lands here.)
       * If it DOES match → the user is asserting a full canonical id verbatim.
         If it is free in *existing* → return it as-is; if taken → raise
         :class:`~kanibako.errors.ProjectError`.
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

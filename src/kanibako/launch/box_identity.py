"""STANDALONE box identity generation (``<kuid>_<sanitized,capped leaf>``).

A standalone box is named ``<kuid>_<leaf>`` where:

* ``kuid`` is the box's stable :mod:`kanibako.kuid` id — a 25-bit Crockford
  base32 token (24 data + 1 parity → always 5 lowercased chars). It REPLACES the
  former ``<random24>`` slot (settings-conformance P6d): the kuid is GENERATED at
  creation and STORED as the settable ``workset.kuid`` key, so it is the STABLE
  cross-move identity prefix.
* ``leaf`` is the project-root basename, sanitized so only portable filename
  characters survive (``[^A-Za-z0-9._-]`` → ``_``), capped at 32 characters,
  with an empty leaf falling back to ``"box"``. The leaf tracks dir MOVES (it is
  re-derived live from the current root basename), so only the kuid is stable.

The pieces are joined with ``_``.  Because the kuid prefix can collide with an
already-registered standalone box name (design-review D-M13), the generator
regenerates the kuid (bounded retries) until the *whole* name is unique within a
caller-supplied ``existing`` set.

This module is pure (modulo ``os.urandom``/clock via :mod:`kanibako.kuid`) and
side-effect free so the sanitize/cap/collision-regen logic is directly
unit-testable.  The kuid CODEC lives in the break-off-ready :mod:`kanibako.kuid`;
the ``<kuid>_<leaf>`` NAME composition + the ``workset.kuid`` key wiring live
here / in the settings layer (D6a — nothing kanibako-specific in ``kuid``).
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

# A canonical standalone box name is ``<kuid>_<leaf>`` where the prefix is a
# VALID kuid (5 Crockford base32 chars with odd parity — see :func:`kanibako.
# kuid.is_valid`) and the leaf is 1-32 chars drawn from the sanitized, *lowercased*
# alphabet (see :func:`sanitize_cap`).  This is the verbatim shape the generator
# emits, so it is also what a user may assert by passing a fully-formed ``--name``.
# (The prefix ALPHABET is now the kuid's Crockford set, NOT RFC-4648 ``[a-z2-7]``.)
_LEAF_RE = re.compile(r"^[a-z0-9._-]{1,32}$")

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
    """True when *name* matches the canonical ``<kuid>_<leaf>`` shape.

    The prefix (up to the FIRST ``_``) must be a VALID kuid (:func:`kanibako.
    kuid.is_valid` — 5 Crockford base32 chars with odd parity) and the leaf 1-32
    chars of ``[a-z0-9._-]`` (lowercase, matching :func:`sanitize_cap`).  An
    over-long or illegal-char leaf, or a non-kuid prefix, fails the match.  The
    check is case-sensitive: callers lowercase a supplied name first.
    """
    prefix, sep, leaf = name.partition("_")
    if not sep:
        return False
    return kuid.is_valid(prefix) and _LEAF_RE.match(leaf) is not None


def standalone_kuid(name: str) -> str:
    """Return the kuid PREFIX of a standalone box *name* (``<kuid>_<leaf>``).

    The kuid is everything up to the FIRST ``_`` (the kuid alphabet never
    contains ``_``, so this is unambiguous even when the leaf itself holds one).
    Exposes the generated/stored kuid so the create path can persist it as the
    ``workset.kuid`` key WITHOUT re-deriving it — the name is composed FROM the
    kuid, so this is the inverse.
    """
    return name.partition("_")[0]


def compose_standalone_name(box_kuid: str, root: Path) -> str:
    """Compose the LIVE standalone box name ``<box_kuid>_<leaf>`` for *root*.

    *box_kuid* is the STABLE stored ``workset.kuid`` prefix; the leaf is derived
    LIVE from the current *root* basename (``sanitize_cap(root.name)``) so it
    TRACKS directory moves (the kuid stays put, the leaf follows the dir — spec
    2026-07-04).  The single source for re-composing a moved box's name from its
    stored kuid; mirrors the join in :func:`_generate_with_leaf`.
    """
    return f"{box_kuid}_{sanitize_cap(root.name)}"


def _generate_with_leaf(leaf: str, existing: set[str]) -> str:
    """Build ``<kuid>_<leaf>`` with whole-name collision regen.

    *leaf* must already be sanitized (see :func:`sanitize_cap`).  Regenerates
    the kuid prefix (bounded retries) until the whole name is unique within
    *existing*.  Shared by :func:`make_standalone_box_name` (leaf from the root
    basename) and :func:`resolve_standalone_name` (leaf from a supplied string).

    Raises :class:`RuntimeError` if a unique name cannot be found within
    :data:`_MAX_REGEN_ATTEMPTS` attempts.
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

    Builds ``<kuid>_<sanitize_cap(root.name)>`` and, if that whole name is
    already in *existing*, regenerates the kuid prefix (bounded retries) until
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
    canonical ``<kuid>_<leaf>`` id already present in *existing*.  Every other
    input (empty, or a non-canonical string that becomes a fresh
    ``<kuid>_<leaf>``) is always satisfiable, so this is a no-op for them.

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

    Pure (modulo the kuid generator) so it is directly unit-testable.  Branches:

    1. *supplied* empty → :func:`make_standalone_box_name` (fresh kuid prefix +
       sanitized ``root.name`` leaf, whole-name collision regen).
    2. Otherwise lowercase *supplied*, then:

       * If it does NOT match the canonical ``<kuid>_<leaf>`` shape (see
         :func:`is_canonical_standalone_name`) → treat the WHOLE supplied string
         as a raw leaf: ``<fresh-kuid>_<sanitize_cap(supplied)>`` with
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

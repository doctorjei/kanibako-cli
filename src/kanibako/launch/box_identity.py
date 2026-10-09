"""STANDALONE box identity generation, plus the box-name allowlist.

A standalone box is named ``<kuid>_<leaf>``: a stable :mod:`kanibako.kuid` prefix
stored as the settable ``workset.kuid`` key, joined by ``_`` to the project-root
basename run through :func:`sanitize_cap`.  Only the kuid is stable — the leaf is
re-derived live, so it tracks directory moves.

This module is pure (modulo ``os.urandom``/clock via :mod:`kanibako.kuid`) and
side-effect free so the sanitize/cap/collision-regen logic is directly
unit-testable.

See ``llm-docs/kanibako/launch/box_identity.py.md`` for the name grammar, the
allowlist rule and the resolve branches.
"""

from __future__ import annotations

import enum
import re
import unicodedata
from pathlib import Path

from kanibako import kuid
from kanibako.errors import DerivedBoxNameError, ProjectError
from kanibako.identifiers import find_identifier
from kanibako.settings.messages import BOX_NAME_CHARSET, ERR_DERIVED_BOX_NAME, ERR_LEAF_NOT_ASCII

# Maximum length of the sanitized leaf component.
_LEAF_CAP = 32
# Fallback leaf when the project basename sanitizes to empty.
_EMPTY_LEAF_FALLBACK = "box"
# Characters kept as they are during sanitization; the trailing '-' is literal.
_LEAF_CHARS = r"A-Za-z0-9._-"
# A separator's placeholder; a run holding one, with any ``_`` beside it, becomes one ``_``.
_SEP = "\0"
_SEPARATOR_RUN_RE = re.compile(rf"[_{_SEP}]*{_SEP}[_{_SEP}]*")
# What the leaf loses at either end: a box name may not start with '-' or '.'.
_LEAF_END_CHARS = "_-."
# Bound on collision-regeneration attempts before giving up.
_MAX_REGEN_ATTEMPTS = 1000

# The leaf half of the canonical ``<kuid>_<leaf>`` shape; the prefix half is
# :func:`kanibako.kuid.is_valid`.  It is the verbatim shape the generator emits,
# so it is also the grammar a user may assert with a fully-formed ``--name``.
# ⚑ The prefix alphabet is the kuid's Crockford set, NOT RFC-4648 ``[a-z2-7]``.
# ⚑ UPPERCASE IS ADMITTED IN THE LEAF (spec §0, ⚑ NAMING RULES) while the kuid
# half stays lowercase.  Built from :data:`_LEAF_CHARS` and :data:`_LEAF_CAP`, so it
# admits precisely what :func:`sanitize_cap` emits.  The two halves are opposite BY
# DESIGN: a leaf carries the source directory's case, a kuid has one canonical
# spelling of its own.
_LEAF_RE = re.compile(rf"^[{_LEAF_CHARS}]{{1,{_LEAF_CAP}}}$")

# ---------------------------------------------------------------------------
# Box-name ALLOWLIST (spec §0, ⚑ NAMING RULES): 1–64 characters, each an ASCII
# letter, digit, ``_``, ``-``, or ``.``; not starting with ``-`` or ``.``, not
# ending with ``.``.  ASCII-only because the container name ``kb-<W>-<B>`` must
# match podman's ``[a-zA-Z0-9][a-zA-Z0-9_.-]*``.
#
# ⚑ A name is validated AS THE USER TYPED IT (spec §0, ⚑ NAMING RULES).  Uppercase
# ASCII is not a violation and is no longer folded upstream; collision is what
# compares case-blind, and that happens elsewhere.
# ---------------------------------------------------------------------------

# The permitted characters: the leaf alphabet, which is exactly the spec's set.
_NAME_CHAR_RE = re.compile(rf"[{_LEAF_CHARS}]")

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

    for ch in name:
        if not _NAME_CHAR_RE.fullmatch(ch):
            # Only printable ASCII is quoted: a lookalike or a combining mark would hide.
            shown = f"'{ch}'" if " " < ch < "\x7f" else f"U+{ord(ch):04X}"
            return f"box name must not contain {shown}; {BOX_NAME_CHARSET}"

    if name.startswith("-"):
        return "box name must not start with '-' (collides with CLI flags)"
    if name.startswith("."):
        return "box name must not start with '.' (hidden/relative)"
    if name.endswith("."):
        return "box name must not end with '.'"

    return None


def is_valid_box_name(name: str) -> bool:
    """Return ``True`` when *name* passes the box-name allowlist (non-raising).

    The "flag, don't reject" companion to :func:`validate_box_name`: a
    pre-existing non-conforming box still resolves, it just gets warned about.
    """
    return _box_name_violation(name) is None


class Designation(enum.Enum):
    """What a command's box designation is, by its shape alone."""

    ABSENT = "absent"
    """No designation: the current directory is the box's path."""
    PATH = "path"
    """Cannot be a box name but can be a path, so it is resolved as a path."""
    IDENTIFIER = "identifier"
    """A valid box name, which is also a relative path: ambiguous between the two."""
    INVALID = "invalid"
    """Neither a valid box name nor a valid path."""


def classify_designation(value: str | None) -> Designation:
    """Classify a box designation (system-design § Detection & import).

    Every valid box name is also a relative path, never the reverse, so a string
    that fails the box-name rule but can name a path is a PATH.  Shape only: the
    filesystem is not consulted.  An empty string is ABSENT, like ``None``.
    """
    if not value:
        return Designation.ABSENT
    if "\0" in value:
        return Designation.INVALID
    if is_valid_box_name(value):
        return Designation.IDENTIFIER
    return Designation.PATH


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


def _ascii_spelling(ch: str) -> str | None:
    """*ch*'s ASCII letters once its diacritics are dropped (``é`` → ``e``), else ``None``.

    ⚑ Decomposed one character at a time, and only the ASCII result is used: a kana
    such as ``が`` decomposes to a non-ASCII base, so it is never split here.
    """
    base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
    return base if base.isascii() and base.isalnum() else None


def sanitize_cap(leaf: str) -> str:
    """Sanitize and cap a project-basename *leaf* for a box name, KEEPING its case.

    Latin letters lose their diacritics, composed or not; whitespace, ASCII
    punctuation, and Unicode punctuation and separators are separators, and each
    run of them becomes one ``_``.  The ends lose ``_``,
    ``-`` and ``.``, and an empty result falls back to ``"box"``.  Raises
    :class:`~kanibako.errors.DerivedBoxNameError` when *leaf* holds a character
    with no ASCII spelling, such as kanji; each caller adds its cure.

    ⚑ The source directory's case is the user's, and it is kept (spec §0,
    ⚑ NAMING RULES).  Only the kuid half of ``<kuid>_<leaf>`` is lowercase; the
    two halves are opposite by design and must not be unified.
    """
    parts = []
    after_letter = False  # a combining mark here belongs to a letter already spelled
    for ch in leaf:
        if after_letter and unicodedata.combining(ch):
            continue
        if _NAME_CHAR_RE.fullmatch(ch):
            parts.append(ch)
        elif ch.isascii() or ch.isspace() or unicodedata.category(ch)[0] in "PZ":
            parts.append(_SEP)
        else:
            spelled = _ascii_spelling(ch)
            if spelled is None:
                raise DerivedBoxNameError(ERR_DERIVED_BOX_NAME % (
                    leaf, ERR_LEAF_NOT_ASCII % f"U+{ord(ch):04X}"))
            parts.append(spelled)
        after_letter = parts[-1].isalnum()
    collapsed = _SEPARATOR_RUN_RE.sub("_", "".join(parts)).strip(_LEAF_END_CHARS)
    return collapsed[:_LEAF_CAP].strip(_LEAF_END_CHARS) or _EMPTY_LEAF_FALLBACK


def is_canonical_standalone_name(name: str) -> bool:
    """True when *name* matches the canonical ``<kuid>_<leaf>`` SHAPE.

    ⚑ A shape test only.  The leaf is case-preserving and the kuid is not
    (:func:`kanibako.kuid.is_valid` canonicalizes what it is handed), so
    ``K3XY0_Foo`` is a canonical shape whose STORED spelling — a canonical kuid,
    the leaf untouched — is :func:`_canonical_name`'s answer rather than this one's.
    """
    prefix, sep, leaf = name.partition("_")
    if not sep:
        return False
    return kuid.is_valid(prefix) and _LEAF_RE.match(leaf) is not None


def _canonical_name(supplied: str) -> str:
    """A canonical-shaped *supplied* name as it would be STORED.

    The kuid half goes through :func:`kanibako.kuid.canonicalize` — the kuid module's
    own one spelling per id, which is lowercase — and the leaf is left exactly as
    typed.  Precondition: :func:`is_canonical_standalone_name` is true of *supplied*.
    """
    prefix, _, leaf = supplied.partition("_")
    return f"{kuid.canonicalize(prefix)}_{leaf}"


def _refuse_taken(stored: str) -> ProjectError:
    """The ONE refusal for a verbatim canonical id already in use, reported AS STORED.

    Built here rather than raised at each site so :func:`validate_standalone_name`'s
    pre-flight and :func:`resolve_standalone_name`'s refusal cannot drift apart — the
    pre-flight exists precisely to predict the other's message.
    """
    prefix = stored.partition("_")[0]
    return ProjectError(
        f"already a box with that name '{stored}' — try without the "
        f"'{prefix}_' prefix to generate a fresh one"
    )


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
        # ⚑ Case-blind (spec §0): the leaf now carries the directory's case, so an
        # exact test would call ``k3xy0_Foo`` free while ``k3xy0_foo`` is registered.
        if find_identifier(name, existing) is None:
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
    if not is_canonical_standalone_name(supplied):
        return
    stored = find_identifier(_canonical_name(supplied), existing)
    if stored is not None:
        raise _refuse_taken(stored)


def resolve_standalone_name(
    root: Path, supplied: str, existing: set[str], *, box_kuid: str | None = None,
) -> str:
    """Resolve the standalone box name for *root* given a user *supplied* name.

    Three branches: empty → a fresh name; a non-canonical string → its whole
    text becomes the leaf; a verbatim canonical id → honored if free, refused if
    taken.  The last is the only refusable input.  *box_kuid*, a box's STORED kuid,
    is the kuid half of a non-canonical name instead of a fresh one.

    ⚑ The case the user typed survives every branch (spec §0), because
    :func:`sanitize_cap` no longer folds; the kuid half is canonicalized, never the
    leaf.
    """
    if not supplied:
        return make_standalone_box_name(root, existing)

    if not is_canonical_standalone_name(supplied):
        # Not a canonical id: use the whole supplied string as the leaf source.
        if box_kuid is not None:
            return f"{box_kuid}_{sanitize_cap(supplied)}"
        return _generate_with_leaf(sanitize_cap(supplied), existing)

    # A verbatim canonical id: honor it if free, else refuse with guidance.
    name = _canonical_name(supplied)
    stored = find_identifier(name, existing)
    if stored is not None:
        raise _refuse_taken(stored)
    return name

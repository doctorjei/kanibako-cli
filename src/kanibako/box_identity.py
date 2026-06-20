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
from pathlib import Path

# Maximum length of the sanitized leaf component.
_LEAF_CAP = 32
# Fallback leaf when the project basename sanitizes to empty.
_EMPTY_LEAF_FALLBACK = "box"
# Characters that are NOT replaced during sanitization.
_SAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9._-]")
# Bound on collision-regeneration attempts before giving up.
_MAX_REGEN_ATTEMPTS = 1000


def random24() -> str:
    """Return a 24-bit random token as a fixed-width lowercase base32 string.

    24 bits encode to exactly 5 base32 characters (with the trailing ``=``
    padding stripped), so every token is the same width.  Lowercased for
    container/path friendliness.
    """
    raw = os.urandom(3)  # 3 bytes == 24 bits
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def sanitize_cap(leaf: str) -> str:
    """Sanitize and cap a project-basename *leaf* for use in a box name.

    Non-portable characters (anything outside ``[A-Za-z0-9._-]``) become ``_``;
    the result is capped at :data:`_LEAF_CAP` characters.  An empty result
    (e.g. an empty or all-illegal basename) falls back to ``"box"``.
    """
    sanitized = _SAFE_CHAR_RE.sub("_", leaf)[:_LEAF_CAP]
    return sanitized or _EMPTY_LEAF_FALLBACK


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
    leaf = sanitize_cap(root.name)
    for _ in range(_MAX_REGEN_ATTEMPTS):
        name = f"{random24()}_{leaf}"
        if name not in existing:
            return name
    raise RuntimeError(
        "Could not generate a unique standalone box name after "
        f"{_MAX_REGEN_ATTEMPTS} attempts."
    )

"""Crockford-base32 codec for the "kuid" — a ULID-flavored 25-bit short id.

A compact, human-typable box identifier: ms-within-second(10) | random(14) |
odd-parity(1), packed into five lowercase base32 chars. Bit layout, sentinel
derivation and callers: ``llm-docs/kanibako/kuid.py.md``.

⚑ Intentionally PURE — stdlib-only, ZERO ``kanibako`` imports, so it stays
extractable. Nothing box/workset/registry-specific belongs here.
"""

from __future__ import annotations

import os
import time

# Crockford base32, value 0->31: digits 0-9 then a-z minus i, l, o, u. Lowercased.
ALPHABET: str = "0123456789abcdefghjkmnpqrstvwxyz"

# The all-zero value. Reserved as the workset default sentinel; ``generate``'s odd
# parity is what keeps it unmintable.
# ⚑ Even parity, so ``is_valid(SENTINEL)`` is False BY DESIGN. PRESENT-SENTINEL
# ("no kuid stored") is NOT INVALID ("a wrong kuid stored") — the sentinel is
# exempted at CALL SITES, never special-cased inside is_valid/decode here.
SENTINEL: str = "00000"

BITS: int = 25  # width of the packed value
CHARS: int = 5  # base32 chars needed for 25 bits (5 bits each)

_MS_BITS = 10  # milliseconds-within-second field (0-999 fits in 10 bits)
_RANDOM_BITS = 14  # uniform random field
# (_MS_BITS + _RANDOM_BITS + 1 parity bit == BITS)


def _popcount(n: int) -> int:
    """Number of set bits in a non-negative int."""
    return bin(n).count("1")


def encode(value: int) -> str:
    """Encode a 25-bit int to exactly 5 Crockford-base32 chars, MSB-first."""
    if not 0 <= value < (1 << BITS):
        raise ValueError(f"value out of range for {BITS}-bit kuid: {value!r}")
    out = []
    for i in range(CHARS):
        shift = 5 * (CHARS - 1 - i)
        out.append(ALPHABET[(value >> shift) & 0x1F])
    return "".join(out)


def decode(s: str) -> int:
    """Decode 5 Crockford-base32 chars to a 25-bit int, canonicalizing first."""
    s = canonicalize(s)
    if len(s) != CHARS:
        raise ValueError(f"kuid must be {CHARS} chars, got {len(s)}: {s!r}")
    value = 0
    for ch in s:
        idx = ALPHABET.find(ch)
        if idx < 0:
            raise ValueError(f"char not in kuid alphabet: {ch!r}")
        value = (value << 5) | idx
    return value


def canonicalize(s: str) -> str:
    """Fold user input toward canonical form (Crockford input rules); does NOT validate.

    ⚑ The result MAY still be an invalid length or charset — that judgment
    belongs to ``is_valid``/``decode``.
    """
    s = s.lower().replace("-", "")
    return s.replace("o", "0").replace("i", "1").replace("l", "1")


def is_valid(s: str) -> bool:
    """True iff ``s`` canonicalizes to 5 in-alphabet chars with ODD parity.

    ⚑ ``is_valid(SENTINEL)`` is False — all-zero has even parity. Correct: a
    caller that may hold a sentinel tests for it BEFORE asking here.
    """
    try:
        value = decode(s)
    except ValueError:
        return False
    return _popcount(value) % 2 == 1


def generate() -> str:
    """Build a fresh kuid: ms(10) | random(14) | odd-parity(1) -> 5 chars.

    Invariants: always 5 chars, always ``is_valid`` True, never ``SENTINEL``.
    """
    ms = int(time.time() * 1000) % 1000  # 0-999, fits _MS_BITS
    random14 = int.from_bytes(os.urandom(2), "big") & ((1 << _RANDOM_BITS) - 1)
    data24 = (ms << _RANDOM_BITS) | random14
    # Odd parity: pick the bit that makes the total popcount odd (>= 1).
    parity = 0 if _popcount(data24) % 2 == 1 else 1
    value25 = (data24 << 1) | parity
    return encode(value25)

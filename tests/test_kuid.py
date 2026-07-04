"""Unit tests for kanibako.kuid — the pure Crockford-base32 25-bit id codec.

Mutation-proving focus: the parity computation and the sentinel reservation.
Breaking either in the source must turn at least one assertion here RED.
"""

from __future__ import annotations

import pytest

from kanibako import kuid


def _popcount(n: int) -> int:
    """Independent popcount for the tests (do NOT reuse the module's helper)."""
    return bin(n).count("1")


def _expected_parity(data24: int) -> int:
    """Parity computed from first principles: total popcount must be ODD."""
    return 0 if _popcount(data24) % 2 == 1 else 1


# --- alphabet + constants -------------------------------------------------


def test_alphabet_shape() -> None:
    assert kuid.ALPHABET == "0123456789abcdefghjkmnpqrstvwxyz"
    assert len(kuid.ALPHABET) == 32
    assert len(set(kuid.ALPHABET)) == 32  # no dupes
    assert kuid.ALPHABET == kuid.ALPHABET.lower()
    for banned in "ilou":
        assert banned not in kuid.ALPHABET
    assert kuid.BITS == 25
    assert kuid.CHARS == 5
    assert kuid.SENTINEL == "00000"


def test_hex_alignment() -> None:
    # Low 16 alphabet positions match hex digits (sanity for the codec).
    assert kuid.ALPHABET[0:16] == "0123456789abcdef"


# --- round trips ----------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 2, 31, 32, 1023, (1 << 25) - 1, 0x1234567])
def test_round_trip_decode_of_encode(value: int) -> None:
    s = kuid.encode(value)
    assert len(s) == kuid.CHARS
    assert kuid.decode(s) == value


def test_round_trip_random_values() -> None:
    import random

    rng = random.Random(1234)
    for _ in range(500):
        v = rng.randrange(0, 1 << 25)
        assert kuid.decode(kuid.encode(v)) == v


def test_round_trip_encode_of_decode() -> None:
    for s in ("00001", "zzzzz", "abcde", "10000", "0000z"):
        assert kuid.encode(kuid.decode(s)) == s


def test_encode_is_msb_first() -> None:
    # Bit 24 (MSB) set alone -> top char is position 16 ("g"), rest zero.
    assert kuid.encode(1 << 24) == "g0000"
    # Bit 0 (LSB) set alone -> last char is "1".
    assert kuid.encode(1) == "00001"


# --- encode / decode error paths ------------------------------------------


@pytest.mark.parametrize("bad", [1 << 25, -1, (1 << 25) + 5])
def test_encode_out_of_range_raises(bad: int) -> None:
    with pytest.raises(ValueError):
        kuid.encode(bad)


@pytest.mark.parametrize("bad", ["", "0000", "000000", "zzz"])
def test_decode_wrong_length_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        kuid.decode(bad)


@pytest.mark.parametrize("bad", ["0000!", "@@@@@", "0000 "])
def test_decode_out_of_alphabet_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        kuid.decode(bad)


# --- canonicalize ---------------------------------------------------------


def test_canonicalize_crockford_folds() -> None:
    assert kuid.canonicalize("OIL") == "011"
    assert kuid.canonicalize("o-i-l") == "011"
    assert kuid.canonicalize("ABCDE") == "abcde"
    assert kuid.canonicalize("z-z-z-z-z") == "zzzzz"
    # I/L both fold to 1, O folds to 0, case-insensitively (I,i,L,l,O,o).
    assert kuid.canonicalize("IiLlOo") == "111100"


def test_canonicalize_feeds_decode() -> None:
    # A user typing an uppercase/hyphenated form decodes to the same value.
    assert kuid.decode("G-0-0-0-0") == kuid.decode("g0000") == (1 << 24)


# --- sentinel (MUTATION-PROVEN) -------------------------------------------


def test_sentinel_decodes_to_zero() -> None:
    assert kuid.decode(kuid.SENTINEL) == 0


def test_sentinel_is_not_valid() -> None:
    # All-zero has EVEN parity -> is_valid is False BY DESIGN. If is_valid were
    # to special-case the sentinel True, or invert the parity check, this REDs.
    assert kuid.is_valid(kuid.SENTINEL) is False


# --- parity / validity (MUTATION-PROVEN) ----------------------------------


def test_is_valid_true_only_for_odd_parity() -> None:
    # Odd-popcount values are valid; flipping any single bit (even popcount) is not.
    assert kuid.is_valid(kuid.encode(1)) is True  # popcount 1
    assert kuid.is_valid(kuid.encode(3)) is False  # popcount 2 (even)
    assert kuid.is_valid(kuid.encode(7)) is True  # popcount 3


def test_single_bit_corruption_detected() -> None:
    # Start from a known valid (odd-parity) value; flipping ANY one of the 25
    # bits makes popcount even -> is_valid must be False.
    base = kuid.decode(kuid.generate())
    assert kuid.is_valid(kuid.encode(base)) is True
    for bit in range(25):
        flipped = base ^ (1 << bit)
        assert kuid.is_valid(kuid.encode(flipped)) is False, f"bit {bit}"


# --- generate() invariants ------------------------------------------------


def test_generate_invariants_loop() -> None:
    for _ in range(2000):
        s = kuid.generate()
        assert len(s) == kuid.CHARS
        assert all(ch in kuid.ALPHABET for ch in s)
        assert kuid.is_valid(s) is True
        assert s != kuid.SENTINEL


def test_generate_ms_field(monkeypatch: pytest.MonkeyPatch) -> None:
    # The top 10 bits (value >> 15) decode to int(time.time()*1000) % 1000.
    # Derive expected ms with the SAME expression to dodge float rounding.
    t = 1234.567
    monkeypatch.setattr(kuid.time, "time", lambda: t)
    monkeypatch.setattr(kuid.os, "urandom", lambda n: b"\x00\x00")
    value = kuid.decode(kuid.generate())
    assert (value >> 15) == int(t * 1000) % 1000


def test_generate_parity_reserves_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force data24 == 0 (ms 0, random14 0). Even popcount -> parity bit is 1,
    # so the result is "00001", NOT the sentinel "00000". This is the core
    # mutation-proof of the parity computation.
    monkeypatch.setattr(kuid.time, "time", lambda: 1000.000)  # ms % 1000 == 0
    monkeypatch.setattr(kuid.os, "urandom", lambda n: b"\x00\x00")
    s = kuid.generate()
    assert s == "00001"
    assert s != kuid.SENTINEL
    assert kuid.is_valid(s) is True


@pytest.mark.parametrize(
    "t, random14",
    [
        (1000.0, 0),  # ms 0, rand popcount 0 -> data24 even -> parity 1
        (1000.0, 1),  # ms 0, rand popcount 1 -> data24 odd  -> parity 0
        (1000.0, 3),  # ms 0, rand popcount 2 -> data24 even -> parity 1
        (1234.5, 0x2AAA),  # mixed
        (999.0, 0x3FFF),  # random field saturated
        (1500.25, 0x1555),  # mixed
    ],
)
def test_generate_parity_polarities(
    monkeypatch: pytest.MonkeyPatch, t: float, random14: int
) -> None:
    # Independently reconstruct the expected 25-bit value and assert generate()
    # matches. A mutated parity line diverges here -> RED. ms derived in-test
    # with the impl's exact expression to dodge float rounding.
    monkeypatch.setattr(kuid.time, "time", lambda: t)
    monkeypatch.setattr(
        kuid.os, "urandom", lambda n: (random14 & 0x3FFF).to_bytes(2, "big")
    )
    ms = int(t * 1000) % 1000
    data24 = (ms << 14) | random14
    expected = (data24 << 1) | _expected_parity(data24)
    s = kuid.generate()
    assert s == kuid.encode(expected)
    assert kuid.is_valid(s) is True
    # The whole 25-bit value has ODD popcount.
    assert _popcount(kuid.decode(s)) % 2 == 1

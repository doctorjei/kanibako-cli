"""ASCII spellings for non-ASCII text (spec §0, ⚑ NAMING RULES).

Latin letters drop their accents (``café`` → ``cafe``) and kana are written in
Hepburn, kana by kana (``とうきょう`` → ``toukyou``).  Kanji has no spelling.

Pure and stdlib-only.
"""

from __future__ import annotations

import unicodedata
from typing import Literal, overload

_KATAKANA_OFFSET = 0x60  # katakana U+30A1–U+30F6 = hiragana U+3041–U+3096 + 0x60


def _with_katakana(hiragana: dict[str, str]) -> dict[str, str]:
    return {c: s for h, s in hiragana.items() for c in (h, chr(ord(h) + _KATAKANA_OFFSET))}


_KANA = _with_katakana(dict(zip(
    "あいうえお" "かきくけこ" "がぎぐげご" "さしすせそ" "ざじずぜぞ" "たちつてと" "だぢづでど"
    "なにぬねの" "はひふへほ" "ばびぶべぼ" "ぱぴぷぺぽ" "まみむめも" "やゆよ" "らりるれろ"
    "わゐゑをん" "ゔゎゕゖ",
    "a i u e o  ka ki ku ke ko  ga gi gu ge go  sa shi su se so  za ji zu ze zo"
    " ta chi tsu te to  da ji zu de do  na ni nu ne no  ha hi fu he ho"
    " ba bi bu be bo  pa pi pu pe po  ma mi mu me mo  ya yu yo  ra ri ru re ro"
    " wa i e o n  vu wa ka ke".split(),
    strict=True,
))) | {"ヷ": "va", "ヸ": "vi", "ヹ": "ve", "ヺ": "vo", "・": "-"}
_SMALL_VOWEL = _with_katakana(dict(zip("ぁぃぅぇぉ", "aiueo", strict=True)))
_SMALL_Y = _with_katakana(dict(zip("ゃゅょ", "auo", strict=True)))
_SOKUON = frozenset(_with_katakana({"っ": ""}))
_CHOON = "ー"
_HALFWIDTH_KANA = range(0xFF61, 0xFFA0)  # widened by NFKC; its voicing marks then compose
_VOWELS = "aeiou"

# Latin letters NFKD leaves alone.
_LATIN = str.maketrans({
    "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O",
    "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th",
    "ı": "i", "ŋ": "ng", "Ŋ": "Ng", "ħ": "h", "Ħ": "H", "ŧ": "t", "Ŧ": "T",
})


@overload
def to_ascii(text: str, *, strict: Literal[False]) -> str: ...
@overload
def to_ascii(text: str, *, strict: bool = True) -> str | None: ...


def to_ascii(text: str, *, strict: bool = True) -> str | None:
    """*text* in its ASCII spelling; ASCII passes through unchanged.

    A character with no spelling makes the result ``None``, or with
    ``strict=False`` stays in place for the caller.  No table spelling has more
    bytes than its UTF-8 source.
    """
    text = unicodedata.normalize("NFC", "".join(
        unicodedata.normalize("NFKC", c) if ord(c) in _HALFWIDTH_KANA else c for c in text))
    out: list[str] = []
    kana_units: set[int] = set()
    sokuons: list[int] = []
    joinable = False  # the last unit is a kana a small kana may still join
    for ch in text:
        if ch in _SMALL_Y or ch in _SMALL_VOWEL:
            if joinable:
                out[-1] = _join_small(out[-1], ch)
            else:
                out.append("y" + _SMALL_Y[ch] if ch in _SMALL_Y else _SMALL_VOWEL[ch])
            joinable = False
            continue
        joinable = ch in _KANA and _KANA[ch][-1] in _VOWELS  # not ン or ・
        if ch in _KANA:
            kana_units.add(len(out))
            out.append(_KANA[ch])
        elif ch in _SOKUON:
            sokuons.append(len(out))
            out.append("")
        elif ch == _CHOON:
            prev = out[-1][-1:] if out else ""
            out.append(prev if prev in _VOWELS else "")
        elif ch.isascii():
            out.append(ch)
        elif unicodedata.combining(ch) and out and _is_ascii_alnum(out[-1][-1:]):
            continue  # a mark NFC could not compose onto the letter before it
        else:
            spelled = _latin_spelling(ch)
            if spelled is None:
                if strict:
                    return None
                spelled = ch
            out.append(spelled)
    for i in sokuons:
        nxt = out[i + 1] if i + 1 in kana_units else ""
        doubled = nxt[:1] if nxt[:1].isalpha() and nxt[:1] not in _VOWELS else ""
        out[i] = "t" if nxt.startswith("ch") else doubled
    return "".join(out)


def _join_small(base: str, small: str) -> str:
    """Kana *base*, which ends in a vowel, followed by the small kana *small*.

    ``キョ`` kyo, ``デュ`` dyu, ``キェ`` kye, ``ファ`` fa, ``クァ`` kwa, ``ウィ`` wi, ``イェ`` ye.
    """
    stem, own = base[:-1], base[-1]
    palatal = (own == "i" and stem.endswith(("sh", "ch", "j"))) or stem == "y"  # シ チ ジ ヤ: no y
    if small in _SMALL_Y:
        vowel = _SMALL_Y[small]
        return stem + ("" if palatal else "y") + vowel if stem else base + "y" + vowel
    vowel = _SMALL_VOWEL[small]
    if not stem:
        return {"u": "w", "i": "y"}.get(base, base) + vowel if vowel != base else base + vowel
    if vowel == own:
        return base
    if own == "i":
        return stem + ("" if palatal else "y") + vowel
    if own == "u" and stem in ("k", "g"):
        return stem + "w" + vowel
    return stem + vowel


def _is_ascii_alnum(text: str) -> bool:
    return text.isascii() and text.isalnum()


def _latin_spelling(ch: str) -> str | None:
    """*ch*'s ASCII letters once its marks are dropped (``é`` → ``e``), else ``None``."""
    base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
    base = base.translate(_LATIN)
    return base if _is_ascii_alnum(base) else None

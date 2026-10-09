"""ASCII spellings (spec §0, ⚑ NAMING RULES): Latin drops accents, kana is Hepburn."""

from __future__ import annotations

import pytest

from kanibako.launch import romanize
from kanibako.launch.romanize import to_ascii


@pytest.mark.parametrize("text, spelled", [
    ("plain-ASCII_1.0", "plain-ASCII_1.0"),
    ("café", "cafe"), ("Ångström", "Angstrom"), ("Straße", "Strasse"), ("Łódź", "Lodz"),
    ("Zürich", "Zurich"), ("Ｃａｆｅ", "Cafe"), ("Æsir", "AEsir"), ("Þór", "Thor"),
    ("かにばこ", "kanibako"), ("カニバコ", "kanibako"),
    ("かに", "kani"), ("カニ", "kani"), ("ｶﾆ", "kani"), ("ｶﾞｯｺｳ", "gakkou"),
    ("がっこう", "gakkou"), ("まっちゃ", "matcha"), ("しゃしん", "shashin"),
    ("きょう", "kyou"), ("じゅう", "juu"), ("ちゃ", "cha"), ("りゅう", "ryuu"),
    ("ラーメン", "raamen"), ("ファイル", "fairu"), ("ティー", "tii"), ("チェ", "che"),
    ("ヴァ", "va"), ("ウィ", "wi"), ("ジョン・スミス", "jon-sumisu"),
    ("とうきょう", "toukyou"), ("おおさか", "oosaka"), ("しんぶん", "shinbun"),
    ("きんえん", "kinen"), ("ーあ", "a"), ("あっ", "a"), ("ぁ", "a"), ("ゃ", "ya"),
])
def test_spelling(text: str, spelled: str) -> None:
    assert to_ascii(text) == spelled


@pytest.mark.parametrize("text, spelled", [
    # Any base + small ゃゅょ: the base's consonant, then y and the vowel.
    ("デュオ", "dyuo"), ("フュージョン", "fyuujon"), ("テューバ", "tyuuba"), ("ヴュ", "vyu"),
    ("キャ", "kya"), ("シュ", "shu"), ("チョ", "cho"), ("ジャ", "ja"), ("ヂャ", "ja"),
    # イ + small vowel: y + vowel; ウ + small vowel: w + vowel.
    ("イェール", "yeeru"), ("ウェ", "we"), ("ウォ", "wo"),
    # ク/グ + small vowel: kw/gw + vowel.
    ("クァ", "kwa"), ("クィ", "kwi"), ("クェ", "kwe"), ("クォ", "kwo"), ("グァ", "gwa"),
    # i-row + small ェ: consonant + ye, or + e after sh/ch/j.
    ("キェ", "kye"), ("ギェ", "gye"), ("ニェ", "nye"), ("シェ", "she"), ("チェ", "che"),
    ("ジェ", "je"),
    # Any other base + small vowel: the base's consonant + that vowel.
    ("ファ", "fa"), ("フィ", "fi"), ("フォ", "fo"), ("ツァ", "tsa"), ("ティ", "ti"),
    ("ディ", "di"), ("トゥ", "tu"), ("ドゥ", "du"), ("スィ", "si"), ("ヴォ", "vo"),
])
def test_extended_katakana(text: str, spelled: str) -> None:
    assert to_ascii(text) == spelled


@pytest.mark.parametrize("text", ["東京", "かに東", "日本語プロジェクト", "\U0001F600", "́x"])
def test_no_spelling_is_none(text: str) -> None:
    assert to_ascii(text) is None


def test_non_strict_leaves_what_it_cannot_spell() -> None:
    assert to_ascii("東京café", strict=False) == "東京cafe"


def test_no_table_spelling_has_more_bytes_than_its_source() -> None:
    # Table spellings never grow (system-design § socket name); NFKD ones can (Ⅷ → VIII).
    tables = [romanize._KANA, romanize._SMALL_VOWEL,
              {k: "y" + v for k, v in romanize._SMALL_Y.items()},
              {chr(k): v for k, v in romanize._LATIN.items()}]
    for table in tables:
        for source, spelled in table.items():
            assert len(spelled.encode()) <= len(source.encode()), source
    # Joined kana and sokuon/choon units stay within their sources' bytes too.
    for text in ["しょ", "っちゃ", "ヴァー", "ｶﾞｯ", "ウィ", "ちぇ"]:
        assert len(to_ascii(text).encode()) <= len(text.encode()), text

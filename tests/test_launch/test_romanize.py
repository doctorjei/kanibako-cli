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


@pytest.mark.parametrize("text", ["東京", "かに東", "日本語プロジェクト", "\U0001F600", "́x"])
def test_no_spelling_is_none(text: str) -> None:
    assert to_ascii(text) is None


def test_non_strict_leaves_what_it_cannot_spell() -> None:
    assert to_ascii("東京café", strict=False) == "東京cafe"


def test_no_table_spelling_has_more_bytes_than_its_source() -> None:
    # A name never grows on the way to ASCII (system-design § socket name).
    tables = [romanize._KANA, romanize._SMALL_VOWEL,
              {k: "y" + v for k, v in romanize._SMALL_Y.items()},
              {chr(k): v for k, v in romanize._LATIN.items()}]
    for table in tables:
        for source, spelled in table.items():
            assert len(spelled.encode()) <= len(source.encode()), source
    # Joined kana and sokuon/choon units stay within their sources' bytes too.
    for text in ["しょ", "っちゃ", "ヴァー", "ｶﾞｯ", "ウィ", "ちぇ"]:
        assert len(to_ascii(text).encode()) <= len(text.encode()), text

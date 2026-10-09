# `src/kanibako/launch/romanize.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
_KATAKANA_OFFSET = 96
_KANA = _with_katakana(dict(zip('あいうえおかきくけこがぎぐげごさしすせそざじずぜぞたちつてとだぢづでどなにぬねのはひふへほばびぶべぼぱぴぷぺぽまみむめもやゆよらりるれろわゐゑをんゔゎゕゖ', 'a i u e o  ka ki ku ke ko  ga gi gu ge go  sa shi su se so  za ji zu ze zo ta chi tsu te to  da ji zu de do  na ni nu ne no  ha hi fu he ho ba bi bu be bo  pa pi pu pe po  ma mi mu me mo  ya yu yo  ra ri ru re ro wa i e o n  vu wa ka ke'.split(), strict=True))) | {'ヷ': 'va', 'ヸ': 'vi', 'ヹ': 've', 'ヺ': 'vo', '・': '-'}
_SMALL_VOWEL = _with_katakana(dict(zip('ぁぃぅぇぉ', 'aiueo', strict=True)))
_SMALL_Y = _with_katakana(dict(zip('ゃゅょ', 'auo', strict=True)))
_SOKUON = frozenset(_with_katakana({'っ': ''}))
_CHOON = 'ー'
_HALFWIDTH_KANA = range(65377, 65440)
_VOWELS = 'aeiou'
_LATIN = str.maketrans({'ß': 'ss', 'ẞ': 'SS', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE', 'ø': 'o', 'Ø': 'O', 'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D', 'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th', 'ı': 'i', 'ŋ': 'ng', 'Ŋ': 'Ng', 'ħ': 'h', 'Ħ': 'H', 'ŧ': 't', 'Ŧ': 'T'})
```

## Functions
```
@overload
def to_ascii(text: str, *, strict: Literal[False]) -> str
@overload
def to_ascii(text: str, *, strict: bool=True) -> str | None
def to_ascii(text: str, *, strict: bool=True) -> str | None
def _with_katakana(hiragana: dict[str, str]) -> dict[str, str]
def _opens_on_vowel_or_y(kana: str) -> bool
def _join_small(base: str, small: str) -> str
def _is_ascii_alnum(text: str) -> bool
def _latin_spelling(ch: str) -> str | None
```

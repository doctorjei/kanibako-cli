# `src/kanibako/kuid.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/kuid.py.md`.


## Variables

```
ALPHABET: str = '0123456789abcdefghjkmnpqrstvwxyz'
SENTINEL: str = '00000'
BITS: int = 25
CHARS: int = 5
_MS_BITS = 10
_RANDOM_BITS = 14
```

## Functions
```
def encode(value: int) -> str
def decode(s: str) -> int
def canonicalize(s: str) -> str
def is_valid(s: str) -> bool
def generate() -> str
def _popcount(n: int) -> int
```

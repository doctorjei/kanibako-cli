# `src/kanibako/launch/box_identity.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/launch/box_identity.py.md`.


## Variables

```
_LEAF_CAP = 32
_EMPTY_LEAF_FALLBACK = 'box'
_SAFE_CHAR_RE = re.compile('[^A-Za-z0-9._-]')
_MAX_REGEN_ATTEMPTS = 1000
_LEAF_RE = re.compile('^[a-z0-9._-]{1,32}$')
_ALLOWED_PUNCT = frozenset('_-.')
_BLOCKED_ASCII_PUNCT = frozenset(string.punctuation) - _ALLOWED_PUNCT
_NAME_MIN_LEN = 1
_NAME_MAX_LEN = 64
```

## Functions
```
def is_valid_box_name(name: str) -> bool
def box_name_reason(name: str) -> str | None
def validate_box_name(name: str) -> None
def sanitize_cap(leaf: str) -> str
def is_canonical_standalone_name(name: str) -> bool
def standalone_kuid(name: str) -> str
def compose_standalone_name(box_kuid: str, root: Path) -> str
def make_standalone_box_name(root: Path, existing: set[str]) -> str
def validate_standalone_name(supplied: str, existing: set[str]) -> None
def resolve_standalone_name(root: Path, supplied: str, existing: set[str]) -> str
def _box_name_violation(name: str) -> str | None
def _generate_with_leaf(leaf: str, existing: set[str]) -> str
```

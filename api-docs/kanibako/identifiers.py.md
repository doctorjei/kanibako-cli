# `src/kanibako/identifiers.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
NAME_CHAR_CLASS = 'A-Za-z0-9_'
NAME_MAX_LEN = 64
LEADING_DIGIT_FLAW = 'must not start with a digit'
_NAME_CHAR_RE = re.compile(f'[{NAME_CHAR_CLASS}]')
_NOT_NAME_CHAR_RE = re.compile(f'[^{NAME_CHAR_CLASS}]')
_DERIVED_SEPARATORS = '-.'
```

## Functions
```
def name_flaw(name: str) -> str | None
def is_name(name: str) -> bool
def derived_name(text: str) -> str
def underscore_spelling(name: str) -> str | None
def agent_node_case(name: str) -> str
def find_identifier(name: str, candidates: Iterable[str]) -> str | None
def _fold(name: str) -> str
```

# `src/kanibako/agent_ref.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/agent_ref.py.md`.

```python
SEPARATORS = ((PLUS_SEP := '+'), (CANONICAL_SEP := '℘'))

_SAFE_EXTRA = frozenset('-_')

SEGMENT_CHAR_CLASS = '\\w' + ''.join((re.escape(ch) for ch in sorted(_SAFE_EXTRA)))

_DOT_HINT = "; '.' is reserved as settings key-path separator and cannot appear in an agent name"

def _is_segment_safe(segment: str) -> bool:
    ...

def _first_sep_index(raw: str) -> int:
    ...

def display_agent_ref(node: str) -> str:
    ...

def canonicalize_agent_ref(raw: str) -> str:
    ...

def parse_agent_ref(raw: str) -> tuple[str, str]:
    ...

def harness_of(node: str) -> str:
    ...

def persona_of(node: str) -> str:
    ...

def with_harness(node: str, harness: str) -> str:
    ...
```

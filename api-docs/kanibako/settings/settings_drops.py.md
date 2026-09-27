# `src/kanibako/settings/settings_drops.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_drops.py.md`.


## Functions
```
def containing_scopes(file_scope: str) -> frozenset[str]
def contained_scopes(file_scope: str) -> tuple[str, ...]
def writable_scopes(level: str) -> frozenset[str]
def upward_scope_drop_set(file_scope: str) -> frozenset[str]
def cascade_drop_set(level: str) -> frozenset[str]
```

# `src/kanibako/settings/settings_merge.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_merge.py.md`.


## Variables

```
_MASKS_SEGMENT = 'masks'
_PREF_ROOT = 'pref'
_OMIT = _Omit()
```

## Functions
```
def merge(levels: list[KeyStore]) -> KeyStore
def _is_set(level: KeyStore, name: str) -> bool
def _names_in_order(levels: list[KeyStore]) -> list[str]
def _merge_nodes(levels: list[KeyStore], *, path: tuple[str, ...]) -> KeyStore
def _deep_copy_store(store: KeyStore) -> KeyStore
def _resolve_present_none(*, path: tuple[str, ...]) -> StoreValue | _Omit
```

## Classes

```
class _Omit:
    def __repr__(self) -> str
```

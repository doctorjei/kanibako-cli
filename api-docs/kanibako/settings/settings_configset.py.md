# `src/kanibako/settings/settings_configset.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_configset.py.md`.


## Variables

```
OK: _OK = _OK()
__all__ = ['Verdict', 'OK', 'Error', 'validate_config_set', 'ResolveProbe']
```

## Types
```
Verdict = Union[_OK, Error]
ResolveProbe = Callable[[str, str], 'str | None']

```

## Functions
```
def validate_config_set(key: str, value: str, *, resolves: ResolveProbe) -> Verdict
def _scan_tokens(value: str) -> tuple[list[str], list[str]]
```

## Classes

```
@dataclass(frozen=True)
class Error:
    message: str

@dataclass(frozen=True)
class _OK:
```

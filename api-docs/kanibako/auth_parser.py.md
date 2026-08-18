# `src/kanibako/auth_parser.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/auth_parser.py.md`.


## Variables

```
_URL_RE = re.compile('(https?://(?:console\\.anthropic\\.com|claude\\.ai)[^\\s\\"\'<>]+)')
_CODE_RE = re.compile('(?:verification\\s+code|code|key)\\s*(?:is)?[:=]\\s*([A-Z0-9]{4,8})\\b', re.IGNORECASE)
```

## Functions
```
def parse_auth_output(output: str) -> AuthPrompt | None
```

## Classes

```
@dataclass
class AuthPrompt:
    url: str
    code: str | None = None
```

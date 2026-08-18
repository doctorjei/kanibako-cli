# `src/kanibako/bun_sea.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/bun_sea.py.md`.


## Variables

```
_BUN_MARKER = b'\n---- Bun! ----\n'
_OFFSETS_SIZE = 32
_MODULE_STRUCT_SIZE = 52
```

## Functions
```
def list_modules(binary_path: Path) -> list[BunModule]
def extract_module(binary_path: Path, name_suffix: str='cli.js') -> bytes
def extract_cli_js(binary_path: Path) -> bytes
def cli_js_hash(binary_path: Path) -> str
def _parse_header(f) -> tuple[int, int, int]
```

## Classes

```
class BunSEAError(Exception):

@dataclass
class BunModule:
    name: str
    content_offset: int
    content_length: int
```

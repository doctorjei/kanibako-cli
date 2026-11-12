# `src/kanibako/migration/yaml_to_yapyon.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/migration/yaml_to_yapyon.py.md`.


## Functions
```
def from_yaml(text: str, *, source: str | None=None, Loader=None, keep_comments: bool=True, yaml_compatible: bool=False, max_digits: int | None=None, max_depth: int | None=None, max_size: int | None=None) -> Conversion
def _same(left, right, seen=None)
def _quoted(value)
def _compatible(handle)
def _tags(node, yaml)
def _occurrences(text, yaml, Loader)
def _recover(text, yaml, Loader, keep_comments)
def _handle(tree, path)
def _place(text, comments, parse_options)
```

## Classes

```
class ConversionError(ValueError):

@dataclass(frozen=True)
class Note:
    kind: str
    message: str
    path: tuple = ()
    line: int | None = None

@dataclass(frozen=True)
class Comment:
    text: str
    path: tuple
    line: int
    column: int
    placement: str = 'value'
    relocated: bool = False

@dataclass(frozen=True)
class Conversion:
    text: str
    data: object
    notes: tuple[Note, ...]
    comments: tuple[Comment, ...]
    verified: bool
    yaml_verified: bool | None

@dataclass
class _Occurrence:
    kind: str
    start: int
    end: int
    line: int
    column: int
    value: object = None
    tag: str | None = None
    style: str | None = None
    flow: bool = False
    anchor: str | None = None
    children: list = field(default_factory=list)
    path: tuple = ()
    boundary: int = 0
    code_end: int = 0
    relocated: bool = False

class _Lines:
    def __init__(self, text, line_breaks='\n')

    def line(self, index)
    def end(self, index)
    def offset(self, where)
```

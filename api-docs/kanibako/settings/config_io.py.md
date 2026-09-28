# `src/kanibako/settings/config_io.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/config_io.py.md`.


## Variables

```
_MERGE_TAG = 'tag:yaml.org,2002:merge'
_VALUE_TAG = 'tag:yaml.org,2002:value'
_ABSENT = object()
```

## Functions
```
def load_doc(path: Path | None) -> dict
def dump_doc(path: Path, data: dict) -> None
def write_root_key(path: Path, key: str, value: object) -> None
def remove_root_key(path: Path, key: str) -> bool
def write_nested_key(path: Path, sections: tuple[str, ...], key: str, value: object) -> None
def remove_nested_key(path: Path, sections: tuple[str, ...], key: str) -> bool
def count_leaves(node: object) -> int
def render_stored_scalar(v: object) -> str
def stored_leaf_object(noun_file: 'Path | None', sections: tuple[str, ...], leaf: str, *, default: object=None) -> object
def read_stored_leaf(noun_file: 'Path | None', sections: tuple[str, ...], leaf: str, *, render: 'Callable[[object], str]'=render_stored_scalar) -> str | None
def render_stored_pref(v: object) -> str
def read_stored_pref(noun_file: 'Path | None', sections: tuple[str, ...], leaf: str, *, render: 'Callable[[object], str]'=render_stored_pref) -> str | None
def _yaml_problem(exc: yaml.YAMLError) -> str
```

## Classes

```
class _DuplicateKey(Exception):
    def __init__(self, dotted: str, first: yaml.Mark, second: yaml.Mark) -> None

class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_document(self, node: yaml.Node) -> object

    def _check_duplicates(self, node: yaml.Node, where: str, seen_nodes: set[int]) -> None
```

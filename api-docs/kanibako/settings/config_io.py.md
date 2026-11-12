# `src/kanibako/settings/config_io.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/config_io.py.md`.


## Variables

```
MAX_DOC_DEPTH = 64
_NULL_TAG = 'tag:yaml.org,2002:null'
_BRACE_VALUE_RE = re.compile('^(\\s*(?:-\\s+)?(?:[^\\s#\'\\"{-][^#]*?:\\s+)?)(\\{[^#]*?)\\s*(?:\\s#.*)?$')
_MERGE_TAG = 'tag:yaml.org,2002:merge'
_VALUE_TAG = 'tag:yaml.org,2002:value'
_ABSENT = object()
```

## Functions
```
def parse_standalone_marker(raw: bytes) -> str | None
def render_standalone_marker(marker: str) -> str
def read_packaged_bytes(ref: Traversable) -> bytes
def persona_store_location() -> Path
def persona_store_agents(*, root: Path | None=None) -> dict[str, Path]
def load_persona_agent(name: str, *, root: Path | None=None) -> tuple[Path, dict[str, Any]]
def fetch_persona_secret(name: str, *, root: Path | None=None) -> str
def parse_native_yaml(text: str | bytes) -> Any
def parse_packaged(text: str) -> object
def load_packaged_mapping(ref: Traversable, filename: str) -> dict[str, Any]
def yaml_problem(exc: yaml.YAMLError) -> str
def parse_doc_text(text: str, *, source: str | None=None) -> object
def parse_template_yaml(text: str) -> object
def read_doc_text(path: Path, *, encoding: str | None=None) -> str
def read_doc_text_capped(path: Path, *, cap: int, encoding: str | None=None) -> str
def write_doc_text(path: Path, text: str) -> None
def read_doc_bytes(path: Path) -> bytes
def overwrite_doc_bytes_in_existing_parent(path: Path, data: bytes) -> int
def append_doc_text_in_existing_parent(path: Path, text: str) -> None
def unlink_doc_file(path: Path, *, missing_ok: bool=False) -> None
def read_box_doc_bytes(path: Path, root: Path) -> bytes
def write_box_doc_bytes(path: Path, root: Path, data: bytes) -> None
def overwrite_box_doc_bytes_in_existing_parent(path: Path, root: Path, data: bytes) -> int
def read_box_doc_text(path: Path, root: Path) -> str
def write_box_doc_text(path: Path, root: Path, text: str) -> None
def overwrite_doc_text(path: Path, text: str) -> None
def overwrite_doc_text_in_existing_parent(path: Path, text: str) -> None
def copy_doc_file(src: Path, dst: Path) -> None
def replace_json_doc(path: Path, data: Any) -> None
def parse_json_text(text: str | bytes | bytearray) -> Any
def parse_toml_text(text: str) -> dict[str, Any]
def load_jsonc(text: str) -> object | None
def load_json_doc(path: Path) -> Any
def dump_json_doc(path: Path, data: Any, *, indent: int | None=None) -> None
def render_json_text(data: Any, *, separators: tuple[str, str] | None=None, indent: int | None=None) -> str
def load_doc(path: Path | None) -> dict
def dump_doc(path: Path, data: dict, *, header: str='') -> None
def write_root_key(path: Path, key: str, value: object) -> None
def remove_root_key(path: Path, key: str) -> bool
def refuse_scalar_sections(path: Path, sections: tuple[str, ...], *, data: dict | None=None) -> None
def write_nested_key(path: Path, sections: tuple[str, ...], key: str, value: object) -> None
def remove_nested_key(path: Path, sections: tuple[str, ...], key: str) -> bool
def count_leaves(node: object) -> int
def render_stored_scalar(v: object) -> str
def stored_leaf_object(noun_file: 'Path | None', sections: tuple[str, ...], leaf: str, *, default: object=None) -> object
def read_stored_leaf(noun_file: 'Path | None', sections: tuple[str, ...], leaf: str, *, render: 'Callable[[object], str]'=render_stored_scalar) -> str | None
def render_stored_pref(v: object) -> str
def read_stored_pref(noun_file: 'Path | None', sections: tuple[str, ...], leaf: str, *, render: 'Callable[[object], str]'=render_stored_pref) -> str | None
def _read_none_as_null(cls: type) -> None
def _plain_containers(value: Any, memo: dict[int, Any] | None=None) -> Any
def _unquoted_brace_hint(mark: yaml.Mark | None) -> str
def _guard_document(data: object) -> None
def _parse_guarded_yaml(text: str) -> object
def _strip_jsonc(text: str) -> str
def _unnamed_key(data: dict) -> tuple[str, object] | None
def _dump_text(data: dict) -> str
def _resolved_section(table: dict, section: str) -> str
```

## Classes

```
class DuplicateKeyError(yaml.YAMLError):
    def __init__(self, dotted: str, first: yaml.Mark, second: yaml.Mark) -> None

    def __str__(self) -> str

class CyclicAnchorError(yaml.YAMLError):
    def __init__(self, dotted: str) -> None

    def __str__(self) -> str

class DocumentTooDeepError(yaml.YAMLError):
    def __init__(self, dotted: str, limit: int) -> None

    def __str__(self) -> str

class _PackagedLoader(getattr(yaml, 'CSafeLoader', yaml.SafeLoader)):

class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_document(self, node: yaml.Node) -> object

    def _check_duplicates(self, node: yaml.Node, where: str, seen_nodes: set[int]) -> None

class _SafeDumper(yaml.SafeDumper):
```

# `src/kanibako/settings/config_io.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/config_io.py.md`.


## Functions
```
def load_doc(path: Path | None) -> dict
def dump_doc(path: Path, data: dict) -> None
def write_root_key(path: Path, key: str, value: object) -> None
def remove_root_key(path: Path, key: str) -> bool
def write_nested_key(path: Path, sections: tuple[str, ...], key: str, value: object) -> None
def remove_nested_key(path: Path, sections: tuple[str, ...], key: str) -> bool
def render_stored_scalar(v: object) -> str | None
def read_stored_leaf(noun_file: 'Path | None', sections: tuple[str, ...], leaf: str) -> str | None
def read_stored_pref(noun_file: 'Path | None', sections: tuple[str, ...], leaf: str) -> str | None
def _yaml_problem(exc: yaml.YAMLError) -> str
```

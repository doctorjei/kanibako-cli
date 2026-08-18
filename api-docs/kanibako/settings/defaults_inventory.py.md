# `src/kanibako/settings/defaults_inventory.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/defaults_inventory.py.md`.


## Variables

```
_PROBE_AGENT = 'default'
_MODES = ('primary', 'named', 'standalone')
_BIND_SOURCES_OUTSIDE_THE_FILE: dict[str, str] = {'~/canon/COLLECTION.md': 'core_defaults.py (packaged rom tree)', '~/canon/bible/ROM_CONTENTS.md': 'core_defaults.py (packaged rom tree)', '~/canon/bible/general': 'core_defaults.py (packaged rom tree)', '~/canon/bible/workset': 'core_defaults.py (packaged rom tree)', '~/canon/bible/box': 'core_defaults.py (packaged rom tree)', '~/canon/bible/agent': 'core_defaults.py (plugin rom chapter)', '<box_image_dir>': 'core-defaults.yaml (images:, gated)'}
_SEED_LAYER_SOURCE = 'launch/templates.py (seed layers)'
_VALUE_COLUMN_CAP = 40
```

## Functions
```
def manifest_default_rows() -> dict[str, dict[str, Any]]
def source_groups() -> tuple[tuple[str, frozenset[str]], ...]
def key_rows() -> list[DefaultRow]
def bind_rows() -> list[DefaultRow]
def env_rows() -> tuple[list[DefaultRow], PluginConsultation]
def print_defaults(out: Any) -> None
def _scope_of(key: str, row: dict[str, Any]) -> str
def _render_default(default: Any) -> tuple[str, tuple[tuple[str, str], ...]]
def _scalar(value: Any) -> str
def _bind_family_by_dest() -> dict[str, str]
def _installed_targets() -> dict[str, Any]
def _print_table(rows: list[DefaultRow], out: Any) -> None
def _sorted(rows: list[DefaultRow]) -> list[DefaultRow]
```

## Classes

```
class DefaultRow(NamedTuple):
    key: str
    value: str
    scope: str
    source: str
    per_mode: tuple[tuple[str, str], ...] = ()
    internal: bool = False

class PluginConsultation(NamedTuple):
    consulted: tuple[str, ...]
    declaring: tuple[str, ...]
```

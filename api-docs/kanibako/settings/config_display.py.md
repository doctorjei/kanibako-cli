# `src/kanibako/settings/config_display.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def flatten_under(prefix: str, table: dict) -> dict[str, str]
def _flatten_table(node: dict, prefix: str, out: dict[str, str]) -> None
def _is_bind_map_key(key: str) -> bool
def _flatten_bind_map(table: dict, key: str, out: dict[str, str]) -> None
def _nested_settings_overrides(data: dict) -> dict[str, str]
def _pref_overrides(path: Path | None) -> dict[str, str]
def _print_pref_block(snapshot: Any, out: Any) -> None
def _print_category_block(snapshot: Any, error: str | None, out: Any, box_ctx: Any, declared_by: 'Mapping[str, str] | None'=None) -> None
def _iter_agent_tiers(scope: str, scope_node: Any)
def _sub(node: Any, path: 'tuple[str, ...]') -> Any
```

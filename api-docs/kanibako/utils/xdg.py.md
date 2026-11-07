# `src/kanibako/utils/xdg.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/utils/xdg.py.md`.


## Variables

```
logger = get_logger('paths')
_runtime_fallback_cache: dict[tuple[str, str], Path] = {}
_RUNTIME_TMP_PREFIX = 'kanibako-runtime-'
```

## Functions
```
def resolve_xdg(var_name: str, spec_default_suffix: str | None) -> Path
def xdg(env_var: str, default_suffix: str) -> Path
def user_config_home() -> Path
def default_data_home() -> Path
def spec_default_xdg_map(data_home: Path | None) -> dict[str, str]
def host_xdg_map(data_home: Path | None=None) -> dict[str, str]
def _fallback_runtime_dir(var_name: str) -> Path
def _runtime_base_usable(base: Path, *, follow_symlinks: bool=True, require_private: bool=False) -> bool
```

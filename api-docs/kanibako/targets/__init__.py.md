# `src/kanibako/targets/__init__.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
logger = logging.getLogger(__name__)
__all__ = ['AgentInstall', 'Mount', 'ShellTarget', 'Target', 'TargetSetting', 'discover_targets', 'get_target', 'resolve_target']
_EP_LOAD_FAILED: set[str] = set()
_RESERVED_NAME_WARNED: set[str] = set()
_COLLIDING_NAME_WARNED: set[str] = set()
_NO_PLUGIN_SHAPE_WARNED: set[str] = set()
```

## Functions
```
def discover_targets() -> dict[str, type[Target]]
def get_target(name: str) -> type[Target]
def resolve_target(name: str | None=None) -> Target
def _register(targets: dict[str, type[Target]], declared: dict[str, tuple[str, str]], name: str, cls: type[Target], source: str, *, tier: str, override: bool) -> None
def _scan_plugin_modules(targets: dict[str, type[Target]], declared: dict[str, tuple[str, str]]) -> None
def _require_meta_name(target: Target) -> Target
```

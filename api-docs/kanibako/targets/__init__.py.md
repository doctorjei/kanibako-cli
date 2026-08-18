# `src/kanibako/targets/__init__.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/targets/__init__.py.md`.


## Variables

```
logger = logging.getLogger(__name__)
__all__ = ['AgentInstall', 'Mount', 'NoAgentTarget', 'Target', 'TargetSetting', 'discover_targets', 'get_target', 'resolve_target']
_EP_LOAD_FAILED: set[str] = set()
```

## Functions
```
def discover_targets(project_path: Path | None=None) -> dict[str, type[Target]]
def get_target(name: str, project_path: Path | None=None) -> type[Target]
def resolve_target(name: str | None=None, project_path: Path | None=None) -> Target
def _scan_plugin_modules(targets: dict[str, type[Target]]) -> None
def _scan_directory_plugins(directory: Path, targets: dict[str, type[Target]]) -> None
def _require_meta_name(target: Target) -> Target
```

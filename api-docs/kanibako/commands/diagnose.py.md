# `src/kanibako/commands/diagnose.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/diagnose.py.md`.


## Variables

```
_PROBE_HIT_PREFIX = 'KANIBAKO_HAS:'
_SHELL_SOURCE_LABELS = {'box.shell': 'box.shell', '$KANIBAKO_SHELL': '$KANIBAKO_SHELL', 'image': 'image default', 'sh': 'fallback'}
_DEVCONTAINERS_EXT_ID = 'ms-vscode-remote.remote-containers'
```

## Functions
```
def probe_missing_executables(runtime, image: str, executables: list[str]) -> list[str]
def run_system_diagnose(args: object) -> int
def run_box_diagnose(args: object) -> int
def run_rig_diagnose(args: object) -> int
def _format_check(status: str, label: str, detail: str) -> str
def _check_runtime() -> tuple[str, str]
def _check_image(config: object) -> tuple[str, str]
def _resolved_shell_detail(config, std, runtime, image) -> str
def _check_agents(config=None, std=None, runtime=None, image=None) -> list[tuple[str, str, str]]
def _check_journal(std, box_key: str | None=None) -> list[tuple[str, str]]
def _check_storage(data_path: Path) -> tuple[str, str]
def _check_vscode_docker_path(settings_path: Path) -> tuple[str, str, str]
def _check_vscode(config_home: Path | None=None) -> list[tuple[str, str, str]]
def _diagnose_baseline(args: object) -> None
```

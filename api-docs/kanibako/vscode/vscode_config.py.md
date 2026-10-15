# `src/kanibako/vscode/vscode_config.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/vscode/vscode_config.py.md`.


## Variables

```
AGENT_MARKERS_DIR = '/tmp/kanibako/agents'
_IMAGE_CONFIGS_SUBPATH = 'Code/User/globalStorage/ms-vscode-remote.remote-containers/imageConfigs'
_SESSION_START_MATCHER = 'startup|resume|clear|compact'
_SESSION_START_COMMAND = 'python3 "/opt/kanibako/kanibako/scripts/import-directives.py" --additional-context "$KANIBAKO_DIRECTIVE_SEED" || true'
_SESSION_START_RETIRED_COMMANDS: 'frozenset[str]' = frozenset({'python3 "$HOME/playbook/kanibako/scripts/import-directives.py" --additional-context "$KANIBAKO_DIRECTIVE_SEED" || true'})
_SESSION_END_MATCHER = 'clear|logout|prompt_input_exit|other'
_PID_SCRIPT_DIR = '~/canon/charter/general/scripts/util'
_AGENT_MARKER_WRITE_COMMAND = f'{_PID_SCRIPT_DIR}/pid-add.sh "$PPID" || true'
_AGENT_MARKER_REMOVE_COMMAND = f'{_PID_SCRIPT_DIR}/pid-rm.sh "$PPID" || true'
_AGENT_MARKER_WRITE_RETIRED_COMMANDS: 'frozenset[str]' = frozenset({'~/canon/bible/general/scripts/util/pid-add.sh "$PPID" || true', 'd="${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}"; mkdir -p "$d" && printf %s "$PPID" > "$d/$PPID" || true'})
_AGENT_MARKER_REMOVE_RETIRED_COMMANDS: 'frozenset[str]' = frozenset({'~/canon/bible/general/scripts/util/pid-rm.sh "$PPID" || true', 'd="${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}"; rm -f "$d/$PPID" || true'})
_CODEX_EVENT_KEY = 'session_start'
_CODEX_EVENT_KEYS = {'SessionStart': _CODEX_EVENT_KEY, 'PostToolUse': 'post_tool_use', 'Stop': 'stop'}
_CHARTER_HOOK_DIR = '~/canon/charter/general/scripts/hooks'
_MAIL_MONITOR_COMMAND = '~/canon/handbook/general/scripts/behavior/check-comms.sh'
_MAIL_MONITOR_MATCHER = 'Bash|Read|Write|Edit|Glob|Grep|Agent'
_EDITED_MATCHER = 'Write|Edit'
_CODEX_MANAGED_HOOKS: 'tuple[_CodexHook, ...]' = (_CodexHook('SessionStart', _SESSION_START_MATCHER, _SESSION_START_COMMAND, 600), *(_CodexHook('SessionStart', source, f'{_CHARTER_HOOK_DIR}/{script}.sh "$PPID"', 600) for source, script in (('startup', 'startup'), ('resume', 'resume'), ('clear', 'clear-start'), ('compact', 'compact'))), _CodexHook('PostToolUse', _MAIL_MONITOR_MATCHER, _MAIL_MONITOR_COMMAND, 5), _CodexHook('PostToolUse', _EDITED_MATCHER, f'{_CHARTER_HOOK_DIR}/edited.sh', 5), _CodexHook('Stop', None, f'{_CHARTER_HOOK_DIR}/stop.sh', 5))
_CODEX_SANDBOX_MODE_KEY = 'sandbox_mode'
_CODEX_SANDBOX_MODE = 'danger-full-access'
_CODEX_REGION_BEGIN = '# >>> kanibako-managed (instruction-delivery hook + trust) — do not edit >>>'
_CODEX_REGION_END = '# <<< kanibako-managed (instruction-delivery hook + trust) <<<'
_CODEX_PROVIDER_REGION_BEGIN = '# >>> kanibako-managed (model provider) — do not edit >>>'
_CODEX_PROVIDER_REGION_END = '# <<< kanibako-managed (model provider) <<<'
```

## Functions
```
def load_jsonc(text: str) -> object | None
def attached_container_config_path(image_ref: str, config_home: Path) -> Path
def merge_attached_container_config(existing: dict, *, workspace_folder: str, extension: str | None) -> dict
def seed_attached_container_config(path: Path, *, workspace_folder: str, extension: str | None) -> bool
def merge_session_start_hook(settings: dict) -> dict
def merge_marker_write_hook(settings: dict) -> dict
def merge_marker_remove_hook(settings: dict) -> dict
def seed_session_start_hook(settings_path: Path, *, root: Path) -> bool
def codex_trusted_hash(event_key: str, matcher: str | None, command: str, timeout_sec: int=600) -> str
def merge_codex_config(text: str, *, box_config_path: str, codex_cwd: str, model_provider: CodexModelProvider | None=None) -> str
def merge_codex_model_provider(text: str, *, provider_id: str, name: str, base_url: str, wire_api: str, env_key: str, model: str | None=None) -> str
def seed_codex_config(config_path: Path, *, box_config_path: str, codex_cwd: str, model_provider: CodexModelProvider | None=None, root: Path) -> bool
def seed_codex_sandbox_mode(config_path: Path, *, root: Path) -> bool
def _strip_jsonc(text: str) -> str
def _encode_image_ref(ref: str) -> str
def _read_existing_config(path: Path, root: Path | None=None) -> dict
def _make_box_home(root: Path) -> None
def _write_if_changed(path: Path, existing: dict, merged: dict, *, root: Path) -> bool
def _is_retired_command(entry: object, retired: 'frozenset[str]') -> bool
def _drop_retired_hooks(groups: list, retired: 'frozenset[str]') -> list
def _merge_managed_command_hook(settings: dict, *, event: str, matcher: str | None, command: str, retired: 'frozenset[str]') -> dict
def _toml_basic_string(value: str) -> str
def _first_table_index(lines: list[str]) -> int
def _extract_delimited_region(text: str, begin: str, end: str) -> tuple[str, str | None]
def _strip_delimited_region(text: str, begin: str, end: str) -> str
def _assemble_codex_managed(body: str, regions: list[str]) -> str
def _strip_codex_region(text: str) -> str
def _reconcile_codex_sandbox_mode(text: str) -> str
def _apply_root_key(lines: list[str], key: str, *, desired: 'str | None', managed: 'tuple[str, ...]', unconditional: bool=False) -> list[str]
def _count_hook_groups(text: str, event: str) -> int
def _build_codex_managed_region(*, box_config_path: str, codex_cwd: str, user_groups: 'dict[str, int]') -> str
def _strip_codex_provider_region(text: str) -> str
def _apply_provider_root_keys(body: str, *, model: str | None, provider_id: str) -> str
def _remove_provider_root_keys(body: str) -> str
def _build_codex_provider_region(*, provider_id: str, name: str, base_url: str, wire_api: str, env_key: str) -> str
```

## Classes

```
class CodexModelProvider(NamedTuple):
    provider_id: str
    name: str
    base_url: str
    wire_api: str
    env_key: str
    model: str | None

class _CodexHook(NamedTuple):
    event: str
    matcher: 'str | None'
    command: str
    timeout: int
```

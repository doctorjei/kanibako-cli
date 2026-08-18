# `src/kanibako/vscode/vscode_config.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/vscode/vscode_config.py.md`.


## Variables

```
AGENT_MARKERS_DIR = '/tmp/kanibako/agents'
_IMAGE_CONFIGS_SUBPATH = 'Code/User/globalStorage/ms-vscode-remote.remote-containers/imageConfigs'
_CLAUDE_MODE_BY_TIER: 'dict[str, str]' = {'editing': 'acceptEdits', 'full': 'bypassPermissions'}
_MANAGED_MODES: frozenset[str] = frozenset(_CLAUDE_MODE_BY_TIER.values())
_SESSION_START_MATCHER = 'startup|resume|clear|compact'
_SESSION_START_COMMAND = 'python3 "/opt/kanibako/kanibako/scripts/import-directives.py" --additional-context "$KANIBAKO_DIRECTIVE_SEED" || true'
_SESSION_END_MATCHER = 'clear|logout|prompt_input_exit|other'
_AGENT_MARKER_WRITE_COMMAND = f'd="${{KANIBAKO_AGENT_MARKERS_DIR:-{AGENT_MARKERS_DIR}}}"; mkdir -p "$d" && printf %s "$PPID" > "$d/$PPID" || true'
_AGENT_MARKER_REMOVE_COMMAND = f'd="${{KANIBAKO_AGENT_MARKERS_DIR:-{AGENT_MARKERS_DIR}}}"; rm -f "$d/$PPID" || true'
_CODEX_EVENT_KEY = 'session_start'
_CODEX_APPROVAL_POLICY_KEY = 'approval_policy'
_CODEX_SANDBOX_MODE_KEY = 'sandbox_mode'
_CODEX_SANDBOX_MODE = 'danger-full-access'
_CODEX_APPROVAL_BY_TIER: 'dict[str, str]' = {'restricted': 'untrusted', 'editing': 'on-request', 'full': 'never'}
_CODEX_MANAGED_APPROVALS: frozenset[str] = frozenset(_CODEX_APPROVAL_BY_TIER.values())
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
def merge_permission_mode(settings: dict, mode: str) -> dict
def clear_permission_mode(settings: dict) -> dict
def seed_claude_permission_mode(settings_path: Path, *, access: str) -> bool
def merge_session_start_hook(settings: dict) -> dict
def merge_marker_write_hook(settings: dict) -> dict
def merge_marker_remove_hook(settings: dict) -> dict
def seed_session_start_hook(settings_path: Path) -> bool
def codex_trusted_hash(event_key: str, matcher: str | None, command: str, timeout_sec: int=600) -> str
def merge_codex_config(text: str, *, box_config_path: str, codex_cwd: str, model_provider: CodexModelProvider | None=None) -> str
def merge_codex_model_provider(text: str, *, provider_id: str, name: str, base_url: str, wire_api: str, env_key: str, model: str) -> str
def seed_codex_config(config_path: Path, *, box_config_path: str, codex_cwd: str, model_provider: CodexModelProvider | None=None) -> bool
def seed_codex_approval(config_path: Path, *, access: str) -> bool
def seed_goose_mode(config_path: Path, *, access: str, descriptor: PluginDescriptor) -> bool
def _strip_jsonc(text: str) -> str
def _encode_image_ref(ref: str) -> str
def _read_existing_config(path: Path) -> dict
def _claude_managed_mode(access: str) -> 'str | None'
def _write_if_changed(path: Path, existing: dict, merged: dict) -> bool
def _merge_managed_command_hook(settings: dict, *, event: str, matcher: str | None, command: str) -> dict
def _toml_basic_string(value: str) -> str
def _first_table_index(lines: list[str]) -> int
def _extract_delimited_region(text: str, begin: str, end: str) -> tuple[str, str | None]
def _strip_delimited_region(text: str, begin: str, end: str) -> str
def _assemble_codex_managed(body: str, regions: list[str]) -> str
def _strip_codex_region(text: str) -> str
def _codex_managed_approval(access: str) -> str
def _reconcile_codex_approval(text: str, access: str) -> str
def _apply_root_key(lines: list[str], key: str, *, desired: 'str | None', managed: 'tuple[str, ...]', unconditional: bool=False) -> list[str]
def _count_session_start_groups(text: str) -> int
def _build_codex_managed_region(*, box_config_path: str, codex_cwd: str, group_index: int) -> str
def _strip_codex_provider_region(text: str) -> str
def _apply_provider_root_keys(body: str, *, model: str, provider_id: str) -> str
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
    model: str
```

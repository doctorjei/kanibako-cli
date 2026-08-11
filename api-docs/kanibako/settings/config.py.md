# `src/kanibako/settings/config.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/config.py.md`.

```python
BOX_META_FILE = 'settings.yaml'

_BOOL_TRUE = frozenset({'true', '1', 'yes', 'on'})

_BOOL_FALSE = frozenset({'false', '0', 'no', 'off'})

def coerce_bool(value: object) -> bool | None:
    ...

_DEFAULTS = {'paths_project_toml': BOX_META_FILE, 'box_image': 'ghcr.io/doctorjei/kanibako-oci:latest', 'box_shell': ''}

@dataclass
class KanibakoConfig:
    paths_project_toml: str = _DEFAULTS['paths_project_toml']
    box_image: str = _DEFAULTS['box_image']
    box_shell: str = _DEFAULTS['box_shell']
    box_share_images: bool = False
    config_paths: dict[str, str] = field(default_factory=dict)

def _flatten_toml(data: dict, prefix: str='') -> dict[str, object]:
    ...

def config_file_path(config_home: Path) -> Path:
    ...

def config_base_path() -> Path:
    ...

def settings_base_path() -> Path:
    ...

def _present_scalar_fields(path: Path) -> dict[str, object]:
    ...

def load_config(path: Path) -> KanibakoConfig:
    ...

_BOX_SCALAR_FIELDS: dict[str, str] = {'box.image': 'box_image', 'box.share_images': 'box_share_images', 'box.shell': 'box_shell'}

def _resolve_box_scalars(global_path: Path, floor_values: 'dict[str, object]', *, workset_path: Path | None, box_path: Path | None, cli_overrides: 'dict[str, object] | None') -> dict[str, object]:
    ...

def load_merged_config(global_path: Path, project_path: Path | None=None, *, workset_path: Path | None=None, cli_overrides: 'dict[str, object] | None'=None) -> KanibakoConfig:
    ...

def write_global_config(path: Path, cfg: KanibakoConfig | None=None) -> None:
    ...

def write_project_config(path: Path, image: str) -> None:
    ...

def persist_creation_flags(box_settings_path: Path, *, materializing: bool, image: str | None=None, share_images: bool | None=None) -> None:
    ...

def write_box_enable_vault(path: Path, enable_vault: bool=True) -> None:
    ...

def read_box_enable_vault(path: Path, *, default_from: Path | None=None) -> bool:
    ...

def carried_box_settings(box_tier: Path, workset_tier: Path | None) -> dict:
    ...

def read_workset_kuid(path: Path) -> str:
    ...

def read_workset_skip_kuid_check(path: Path) -> bool:
    ...

def _split_config_key(flat_key: str) -> tuple[str, str]:
    ...

def write_project_config_key(path: Path, flat_key: str, value: str) -> None:
    ...

def unset_project_config_key(path: Path, flat_key: str) -> bool:
    ...

def load_project_overrides(path: Path) -> dict[str, str]:
    ...

def read_agent_settings(path: Path, agent_name: str) -> dict[str, str]:
    ...

def read_system_agent(system_path: Path | None) -> str | None:
    ...

def read_setup_completed(config_path: Path | None) -> str | None:
    ...

def setup_compat_gate(config_path: Path | None) -> str | None:
    ...

_PSEUDO_AGENTS = frozenset({'no_agent', 'general'})

def resolve_agent(*, explicit_agent: str | None, requested: str | None=None, project_path: Path | None=None) -> str:
    ...

def write_agent_setting(path: Path, key: str, value: str, agent_name: str) -> None:
    ...

def _flatten_dotted(data: dict, prefix: str='') -> dict[str, str]:
    ...
```

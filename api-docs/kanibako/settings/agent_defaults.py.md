# `src/kanibako/settings/agent_defaults.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/agent_defaults.py.md`.


## Functions
```
def load_descriptor(package: str, filename: str) -> PluginDescriptor
def load_behavior(package: str, filename: str) -> 'tuple[TargetSetting, ...]'
def load_category_binds(package: str, filename: str, agent: str) -> CategoryBindDefaults
def load_envs(package: str, filename: str, agent: str) -> 'dict[str, str]'
def load_common(package: str, filename: str, agent: str) -> 'dict[str, BindArm]'
def _expand(value: str) -> str
def _load_doc(package: str, filename: str) -> dict[str, Any]
def _build_binding(entry: dict[str, Any], package: str) -> Binding
def _build_access_row(tier: str, raw: dict[str, Any] | None, *, channel: Channel, source: str='') -> AccessTierRow
def _build_access_realization(raw: dict[str, Any] | None, *, source: str='') -> AccessRealization | None
def _build_setting_arg(entry: dict[str, Any], *, source: str='') -> SettingArg
def _build_behavior(entry: dict[str, Any], *, source: str='') -> TargetSetting
def _build_persona(raw: dict[str, Any] | None) -> PersonaSpec | None
def _build_cred_file(entry: dict[str, Any]) -> CredFileSpec
def _env_values(doc: dict[str, Any], filename: str) -> dict[str, str]
```

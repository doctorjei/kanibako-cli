# `packages/agent-codex/src/kanibako/plugins/codex/target.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/plugins/codex/target.py.md`.


## Variables

```
logger = get_logger('targets.codex')
_NPM_ROOT_TIMEOUT = 10
_DEFAULTS_PACKAGE = 'kanibako.plugins.codex'
_DEFAULTS_FILE = 'codex-defaults.yaml'
_CODEX_DESCRIPTOR = load_descriptor(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
_CODEX_BEHAVIOR = load_behavior(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
```

## Functions
```
def _platform_pkg_and_triple() -> tuple[str, str] | None
def _npm_root_global() -> Path | None
def _resolve_vendored_binary(npm_root: Path) -> Path | None
def _is_elf(path: Path) -> bool
def _resolve_path_executable() -> Path | None


class CodexTarget(Target):
    @property
    def name(self) -> str
    @property
    def display_name(self) -> str
    @property
    def descriptor(self) -> PluginDescriptor | None
    def default_category_binds(self) -> CategoryBindDefaults
    def default_envs(self) -> dict[str, str]
    @property
    def default_entrypoint(self) -> str | None
    def has_resumable_session(self, home: Path) -> bool
    def deliver_panel_permissions(self, *, config_root: Path, access: str) -> bool
    def deliver_directive_hook(self, *, config_root: Path, access: str, model_provider: 'CodexModelProvider | None'=None) -> bool
    def reattach_config_notice(self) -> str | None
    @property
    def setup_entrypoint(self) -> str | None
    @property
    def setup_args(self) -> list[str]
    def should_run_setup(self, output: str) -> bool
    def detect(self) -> AgentInstall | None
    def check_auth(self) -> bool
    def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome
    def verify_persona(self, endpoint: str, token_path: Path | None, model: str | None, *, timeout: float=5.0) -> PersonaProbeOutcome
    def generate_agent_config(self) -> AgentConfig
    def setting_descriptors(self) -> list[TargetSetting]
```

# `packages/agent-claude/src/kanibako/plugins/claude/target.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/plugins/claude/target.py.md`.


## Variables

```
logger = get_logger('targets.claude')
_UPDATE_TIMEOUT = 300
_LAUNCHER = Path.home() / '.local' / 'bin' / 'claude'
_INSTALL_DIR = Path.home() / '.local' / 'share' / 'claude'
_PERSONA_BASE_URL_VAR = 'ANTHROPIC_BASE_URL'
_PERSONA_TOKEN_VAR = 'ANTHROPIC_AUTH_TOKEN'
_DEFAULTS_PACKAGE = 'kanibako.plugins.claude'
_DEFAULTS_FILE = 'claude-defaults.yaml'
_CLAUDE_DESCRIPTOR = load_descriptor(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
_CLAUDE_BEHAVIOR = load_behavior(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
```

## Functions
```
def _autoupdater_disabled_env() -> dict[str, str]


class ClaudeTarget(Target):
    @property
    def name(self) -> str
    @property
    def display_name(self) -> str
    @property
    def descriptor(self) -> PluginDescriptor | None
    def transform_cred(self, spec: CredFileSpec, src: Path | None, dst: Path, direction: str) -> None
    def writeback_extra(self, *, project_home: Path, host_home: Path) -> None
    @property
    def default_entrypoint(self) -> str | None
    def has_resumable_session(self, home: Path) -> bool
    @property
    def config_dir_name(self) -> str
    def deliver_panel_permissions(self, *, config_root: Path, access: str) -> bool
    def deliver_directive_hook(self, *, config_root: Path, access: str, model_provider: 'CodexModelProvider | None'=None) -> bool
    def credential_check_path(self, home: Path) -> Path | None
    def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome
    def verify_persona(self, endpoint: str, token_path: Path | None, model: str | None, *, timeout: float=5.0) -> PersonaProbeOutcome
    def invalidate_credentials(self, home: Path) -> None
    def detect(self) -> AgentInstall | None
    def generate_agent_config(self) -> AgentConfig
    def default_common(self) -> dict[str, BindArm]
    def default_category_binds(self) -> CategoryBindDefaults
    def default_envs(self) -> dict[str, str]
    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]
    def check_auth(self) -> bool
    def prepare_host(self, install: AgentInstall, *, auto_auth: bool, data_path: Path) -> None
    def setting_descriptors(self) -> list[TargetSetting]
    def refresh_credentials(self, home: Path) -> None
    def writeback_credentials(self, home: Path) -> None
```

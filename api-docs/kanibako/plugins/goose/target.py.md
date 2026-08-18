# `packages/agent-goose/src/kanibako/plugins/goose/target.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/plugins/goose/target.py.md`.


## Variables

```
logger = get_logger('targets.goose')
_BINARY = Path.home() / '.local' / 'bin' / 'goose'
_DEFAULTS_PACKAGE = 'kanibako.plugins.goose'
_DEFAULTS_FILE = 'goose-defaults.yaml'
_GOOSE_DESCRIPTOR = load_descriptor(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
_GOOSE_BEHAVIOR = load_behavior(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
```

## Functions
```
class GooseTarget(Target):
    @property
    def descriptor(self) -> PluginDescriptor | None
    def default_category_binds(self) -> CategoryBindDefaults
    def default_envs(self) -> dict[str, str]
    def transform_cred(self, spec: 'CredFileSpec', src: 'Path | None', dst: Path, direction: str) -> None
    @property
    def name(self) -> str
    @property
    def display_name(self) -> str
    @property
    def config_dir_name(self) -> str
    @property
    def default_entrypoint(self) -> str | None
    def has_resumable_session(self, home: Path) -> bool
    def deliver_panel_permissions(self, *, config_root: Path, access: str) -> bool
    def should_run_setup(self, output: str) -> bool
    @property
    def setup_entrypoint(self) -> str | None
    @property
    def setup_args(self) -> list[str]
    def detect(self) -> AgentInstall | None
    def credential_check_path(self, home: Path) -> Path | None
    def invalidate_credentials(self, home: Path) -> None
    def check_auth(self) -> bool
    def generate_agent_config(self) -> AgentConfig
    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]
    def setting_descriptors(self) -> list[TargetSetting]
```

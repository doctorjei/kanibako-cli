# `packages/agent-goose/src/kanibako/plugins/goose/target.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/plugins/goose/target.py.md`.


## Variables

```
logger = get_logger('targets.goose')
GOOSE_CONFIG_FILE = 'config.yaml'
_BINARY = Path.home() / '.local' / 'bin' / 'goose'
_DEFAULTS_PACKAGE = 'kanibako.plugins.goose'
_DEFAULTS_FILE = 'goose-defaults.yaml'
_BOX_CONFIG_MAX_BYTES = 1 << 20
_HOOKS_DIR = ('.agents', 'plugins', 'kanibako', 'hooks')
_HOOKS_FILE = 'hooks.json'
_GOOSE_DESCRIPTOR = load_descriptor(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
_GOOSE_BEHAVIOR = load_behavior(_DEFAULTS_PACKAGE, _DEFAULTS_FILE)
```

## Functions
```
def _host_secrets_present() -> bool
def _box_provider_named(box_home: Path) -> bool
```

## Classes

```
class GooseTarget(Target):
    @property
    def descriptor(self) -> PluginDescriptor | None
    def default_category_binds(self) -> CategoryBindDefaults
    def default_envs(self) -> dict[str, str]
    def cred_writeback_error(self, spec: 'CredFileSpec', data: bytes) -> str | None
    @property
    def name(self) -> str
    @property
    def display_name(self) -> str
    @property
    def config_dir_name(self) -> str
    @property
    def default_entrypoint(self) -> str | None
    def deliver_directive_hook(self, *, config_root: Path, access: str, model_provider: 'CodexModelProvider | None'=None) -> bool
    def has_resumable_session(self, home: Path) -> bool
    def should_run_setup(self, output: str) -> bool
    @property
    def setup_entrypoint(self) -> str | None
    @property
    def setup_args(self) -> list[str]
    def detect(self) -> AgentInstall | None
    @property
    def checks_box_login(self) -> bool
    def check_auth(self, *, box_home: Path | None=None, delivered: frozenset[str]=frozenset()) -> bool
    def generate_agent_config(self) -> AgentConfig
    def setting_descriptors(self) -> list[TargetSetting]
```

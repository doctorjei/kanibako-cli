# `src/kanibako/targets/base.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/targets/base.py.md`.


## Variables

```
_PROVIDER_TEXT_CAP = 300
_PROVIDER_READ_CAP = 8 * 1024
_REFUSAL_STATUSES = (401, 403)
_MODEL_REQUIRED_STATUSES = (400, 422)
```

## Types
```
BindArm = dict[str, tuple[str, ...]]
CategoryBindDefaults = dict[str, BindArm]

```

## Functions
```
def http_probe(url: str, *, headers: dict[str, str], body: dict, timeout: float) -> ProbeResponse
def probe_outcome(response: ProbeResponse, sent: ProbeEvidence) -> PersonaProbeOutcome
def probe_outcome_no_model(response: ProbeResponse, sent: ProbeEvidence) -> PersonaProbeOutcome
def _bearer_secrets(headers: Mapping[str, str]) -> tuple[str, ...]
def _provider_text(raw: bytes, headers: Mapping[str, str]) -> str
def _tilde(path: Path) -> str
def _validate_agent_binary(binary: Path) -> str | None
```

## Classes

```
@dataclass(frozen=True)
class TargetSetting:
    key: str
    description: str
    default: str = ''
    choices: tuple[str, ...] = ()

@dataclass(frozen=True)
class Mount:
    source: Path
    destination: str
    options: str = ''

    def to_volume_arg(self) -> str

@dataclass
class AgentInstall:
    name: str
    binary: Path
    install_dir: Path
    launcher: Path | None = None

class BindKind(Enum):
    FILE = 'file'
    DIR = 'dir'

class HostSrcOrigin(Enum):
    LAUNCHER = 'launcher'
    INSTALL_DIR = 'install_dir'
    BINARY = 'binary'
    LITERAL = 'literal'

class BindScope(Enum):
    AGENT_CRITICAL = 'agent_critical'
    AGENT = 'agent'

@dataclass(frozen=True)
class Binding:
    key: str
    origin: HostSrcOrigin
    box_dest: str
    kind: BindKind
    scope: BindScope
    ro: bool = True
    literal_src: Path | None = None

class Channel(Enum):
    FLAG = 'flag'
    ENV = 'env'

@dataclass(frozen=True)
class SettingArg:
    setting_key: str
    channel: Channel
    flag: tuple[str, ...] = ()
    env_var: str = ''

@dataclass(frozen=True)
class AccessTierRow:
    flag: tuple[str, ...] = ()
    env_value: str = ''

@dataclass(frozen=True)
class AccessRealization:
    channel: Channel
    env_var: str = ''
    restricted: 'AccessTierRow | None' = None
    editing: 'AccessTierRow | None' = None
    full: 'AccessTierRow | None' = None
    setting_key: str = ''

    def row(self, tier: str) -> 'AccessTierRow | None'
    def renders(self, tier: str) -> bool
    def rendered_tiers(self) -> tuple[str, ...]

@dataclass(frozen=True)
class PersonaSpec:
    token_var: str = ''
    endpoint_delivery: str = 'env'
    wire_api: str = 'responses'
    provider_pin: tuple[tuple[str, str], ...] = ()
    model_required: bool = False

class ProbeResponse(NamedTuple):
    status: int | None
    body: str = ''

class PersonaSettings(NamedTuple):
    endpoint: str | None
    model: str | None
    auth_env: str
    env: Mapping[str, str] = MappingProxyType({})
    env_dropped: tuple[str, ...] = ()

class PersonaReadOutcome(NamedTuple):
    settings: PersonaSettings | None
    reject_reason: str | None

class PersonaProbeVerdict(Enum):
    PASS = 'pass'
    REJECTED = 'rejected'
    INCONCLUSIVE = 'inconclusive'
    NOT_APPLICABLE = 'not_applicable'

@dataclass(frozen=True)
class ProbeEvidence:
    endpoint: str
    model: str | None = None
    model_origin: str = ''
    token_path: Path | None = None
    status: int | None = None
    provider_text: str = ''

    def lines(self, indent: str='  ') -> tuple[str, ...]
    def block(self, indent: str='  ') -> str

class PersonaProbeOutcome(NamedTuple):
    verdict: PersonaProbeVerdict
    reason: str | None = None
    evidence: 'ProbeEvidence | None' = None

    @classmethod
    def passed(cls) -> 'PersonaProbeOutcome'
    @classmethod
    def rejected(cls, evidence: ProbeEvidence) -> 'PersonaProbeOutcome'
    @classmethod
    def inconclusive(cls, reason: str, evidence: 'ProbeEvidence | None'=None) -> 'PersonaProbeOutcome'
    @classmethod
    def not_applicable(cls, reason: str) -> 'PersonaProbeOutcome'
    def evidence_block(self, indent: str='  ') -> str

@dataclass(frozen=True)
class Operation:
    fragment: tuple[str, ...]

class Cadence(Enum):
    SYNC = 'sync'
    SEED_ONCE = 'seed_once'

@dataclass(frozen=True)
class CredFileSpec:
    home_rel: str
    host_rel: str
    cadence: Cadence = Cadence.SYNC
    mtime_gate: bool = True
    filtered: bool = False
    is_dir: bool = False

@dataclass(frozen=True)
class PluginDescriptor:
    command: tuple[str, ...]
    bindings: tuple[Binding, ...]
    mode: dict[str, tuple[str, ...]]
    operations: dict[str, Operation] = field(default_factory=dict)
    access_realization: AccessRealization | None = None
    settings: tuple[SettingArg, ...] = ()
    persona: 'PersonaSpec | None' = None
    cred_files: tuple[CredFileSpec, ...] = ()
    host_prep: bool = False
    init_dirs: tuple[str, ...] = ()
    auth_share_support: bool = False
    vscode_extension: str | None = None

class Target(ABC):
    @property
    @abstractmethod
    def name(self) -> str
    @property
    @abstractmethod
    def display_name(self) -> str
    @abstractmethod
    def detect(self) -> AgentInstall | None
    @property
    def has_binary(self) -> bool
    @property
    def descriptor(self) -> 'PluginDescriptor | None'
    def check_auth(self) -> bool
    def prepare_host(self, install: 'AgentInstall', *, auto_auth: bool, data_path: Path) -> None
    def default_common(self) -> dict[str, BindArm]
    def default_seeds(self) -> dict[str, BindArm]
    def default_envs(self) -> dict[str, str]
    def rom_root(self) -> Path | None
    def default_category_binds(self) -> CategoryBindDefaults
    def setting_descriptors(self) -> list[TargetSetting]
    def generate_agent_config(self) -> AgentConfig
    @property
    def default_entrypoint(self) -> str | None
    def has_resumable_session(self, home: Path) -> bool
    def should_run_setup(self, output: str) -> bool
    @property
    def setup_entrypoint(self) -> str | None
    @property
    def setup_args(self) -> list[str]
    @property
    def config_dir_name(self) -> str
    def credential_check_path(self, home: Path) -> Path | None
    def deliver_panel_permissions(self, *, config_root: Path, access: str) -> bool
    def deliver_directive_hook(self, *, config_root: Path, access: str, model_provider: 'CodexModelProvider | None'=None) -> bool
    def reattach_config_notice(self) -> str | None
    def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome
    def verify_persona(self, endpoint: str, token_path: Path | None, model: str | None, *, env: Mapping[str, str] | None=None, timeout: float=5.0) -> PersonaProbeOutcome
    def invalidate_credentials(self, home: Path) -> None
    def transform_cred(self, spec: CredFileSpec, src: Path | None, dst: Path, direction: str) -> None
    def refresh_credentials(self, home: Path) -> None
    def writeback_credentials(self, home: Path) -> None
    def writeback_extra(self, *, project_home: Path, host_home: Path) -> None
```

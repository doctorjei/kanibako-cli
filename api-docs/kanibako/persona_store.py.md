# `src/kanibako/persona_store.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/persona_store.py.md`.


## Variables

```
_POINTER_CAP = 16 * 1024
_ENDPOINT_SCHEMES = frozenset({'http', 'https'})
```

## Functions
```
def persona_store_root() -> Path
def locate_entry(ref: str) -> PersonaEntry | None
def resolve_secret_path(entry: PersonaEntry) -> SecretPathResult
def validate_endpoint(endpoint: str) -> None
def read_persona_bundle(ref: str, target: Target) -> PersonaBundle | None


@dataclass(frozen=True)
class PersonaEntry:
    node: str
    persona: str
    harness: str
    persona_dir: Path
    config_dir: Path

class SecretPathResult(NamedTuple):
    path: Path | None
    error: str | None

class PersonaBundle(NamedTuple):
    endpoint: str | None = None
    model: str | None = None
    auth_env: str | None = None
    env: Mapping[str, str] = MappingProxyType({})
    env_dropped: tuple[str, ...] = ()
    token_path: Path | None = None
    token_error: str | None = None
    reject_reason: str | None = None
    no_reader: bool = False

    def to_persona_values(self) -> dict[str, str]
```

# `src/kanibako/tweakcc.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/tweakcc.py.md`.


## Variables

```
logger = get_logger('tweakcc')
TRANSFORM_NAME: Final[str] = 'tweakcc'
```

## Functions
```
def resolve_tweakcc_config(agent_tweakcc: dict, project_tweakcc: dict | None=None) -> TweakccConfig
def load_external_config(config_path: str | None) -> dict
def build_merged_config(tweakcc_cfg: TweakccConfig, kanibako_defaults: dict | None=None) -> dict
def write_merged_config(config: dict, output_path: Path) -> None
def _deep_merge(base: dict, override: dict) -> dict


@dataclass
class TweakccConfig:
    enabled: bool = False
    config_path: str | None = None
    overrides: dict = field(default_factory=dict)
```

# `src/kanibako/settings/settings_cli_level.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/settings_cli_level.py.md`.


## Variables

```
SELECTION_KEY: Final[str] = 'system.agent'
CLI_SHADOWED_KEYS: Final[Mapping[str, str]] = {'--agent': SELECTION_KEY, '-M/--model': 'agent.<agent>.model', '-N/-C/-R': 'agent.<agent>.continue_mode', '--image': 'box.image', '--share-images': 'box.share_images'}
_FORBIDDEN_HEADS: Final[frozenset[str]] = frozenset({'meta', 'config', 'pref'})
```

## Functions
```
def build_cli_level(*, selection: 'Mapping[str, object] | None'=None, active_agent: 'str | None'=None, model: 'str | None'=None, new_session: bool=False, continue_session: bool=False, resume: bool=False, image: 'str | None'=None, share_images: bool=False) -> 'dict[str, object] | None'
def guard_cli_level(level: 'Mapping[str, object] | None', *, active_agent: 'str | None'=None, valid_agents: 'Collection[str] | None'=None, agent_leaves: 'Collection[str] | None'=None) -> None
```

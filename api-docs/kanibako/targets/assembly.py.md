# `src/kanibako/targets/assembly.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/targets/assembly.py.md`.


## Variables

```
_logger = get_logger('targets.assembly')
```

## Functions
```
def entrypoint(descriptor: PluginDescriptor) -> str
def resolve_mode(*, resume_mode: bool, new_session: bool, is_new_project: bool, extra_args: list[str], available_modes: Collection[str]) -> str
def resolve_access_tier(access: 'str | None') -> str
def effective_access(*, secure: bool, autonomous: bool, access: 'str | None'=None) -> str
def access_row(descriptor: PluginDescriptor, tier: str, *, agent: str='') -> 'AccessTierRow | None'
def assemble_argv(descriptor: PluginDescriptor, *, mode_fragment: 'Sequence[str] | None', access: str, setting_values: dict[str, str], op_fragment: 'Sequence[str] | None'=None, extra_args: list[str], agent: str='') -> list[str]
def assemble_env(descriptor: PluginDescriptor, *, access: str, setting_values: dict[str, str], agent: str='') -> dict[str, str]
def env_realization_drivers(descriptor: PluginDescriptor) -> dict[str, str]
def resolve_binding_source(binding: Binding, install: AgentInstall, *, override: str='') -> Path | None
def declares_box_dest(descriptor: 'PluginDescriptor | None', box_dest: str) -> bool
def descriptor_mounts(descriptor: PluginDescriptor, install: AgentInstall, *, overrides: dict[str, str] | None=None) -> list[Mount]


class BindingSourceError(Exception):
    ...
```

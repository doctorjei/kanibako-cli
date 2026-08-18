# `src/kanibako/settings/config_keys.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/config_keys.py.md`.


## Variables

```
KNOWN_CONFIG_KEYS: frozenset[str] = frozenset({'model', 'allow_helpers', 'access', 'endpoint', 'bootstrap', 'continue_mode', 'box.image', 'box.share_images', 'box.images_store', 'box.shell', 'system.auth.share_allowed', 'workset.auth.share_allowed', 'workset.auth.global_sync', 'box.auth.global_enabled', 'box.auth.workset_enabled', 'box.enable_vault', 'workset.registry', 'workset.auth.path', 'workset.boxes', 'workset.vault_ro', 'workset.vault_rw', 'workset.logs', 'workset.workspaces', 'workset.channelroot', 'workset.channels.common', 'workset.channels.chat', 'workset.channels.share', 'workset.channels.broadcast', 'workset.channels.mailboxes', 'workset.channels.share_global', 'workset.template', 'workset.canon', 'box.canon', 'workset.kuid', 'workset.skip_kuid_check', 'config.data', 'config.settings', 'config.agents', 'config.primary_workset', 'config.registry', 'config.journal', 'system.backup', 'system.channelroot', 'system.template', 'system.canon', 'system.cache', 'system.runtime', 'system.agent'})
DYNAMIC_PREFIXES: tuple[str, ...] = ('env.',)
KEY_TYPES: dict[str, str] = {'box.share_images': 'bool', 'system.auth.share_allowed': 'bool', 'workset.auth.share_allowed': 'bool', 'workset.auth.global_sync': 'bool', 'box.auth.global_enabled': 'bool', 'box.auth.workset_enabled': 'bool', 'box.enable_vault': 'bool', 'workset.skip_kuid_check': 'bool'}
AGENT_DEFAULT_SUB = 'default'
_KEY_ROUTES: dict[str, tuple[tuple[str, ...], str]] = {'box.image': (('box',), 'image'), 'box.shell': (('box',), 'shell'), 'box.share_images': (('box',), 'share_images'), 'box.images_store': (('box',), 'images_store'), 'system.agent': (('system',), 'agent'), 'system.auth.share_allowed': (('system', 'auth'), 'share_allowed'), 'workset.auth.share_allowed': (('workset', 'auth'), 'share_allowed'), 'workset.auth.global_sync': (('workset', 'auth'), 'global_sync'), 'box.auth.global_enabled': (('box', 'auth'), 'global_enabled'), 'box.auth.workset_enabled': (('box', 'auth'), 'workset_enabled'), 'box.enable_vault': (('box',), 'enable_vault'), 'workset.registry': (('workset',), 'registry'), 'workset.auth.path': (('workset', 'auth'), 'path'), 'workset.boxes': (('workset',), 'boxes'), 'workset.vault_ro': (('workset',), 'vault_ro'), 'workset.vault_rw': (('workset',), 'vault_rw'), 'workset.logs': (('workset',), 'logs'), 'workset.workspaces': (('workset',), 'workspaces'), 'workset.channelroot': (('workset',), 'channelroot'), 'workset.channels.common': (('workset', 'channels'), 'common'), 'workset.channels.chat': (('workset', 'channels'), 'chat'), 'workset.channels.share': (('workset', 'channels'), 'share'), 'workset.channels.broadcast': (('workset', 'channels'), 'broadcast'), 'workset.channels.mailboxes': (('workset', 'channels'), 'mailboxes'), 'workset.channels.share_global': (('workset', 'channels'), 'share_global'), 'workset.template': (('workset',), 'template'), 'workset.canon': (('workset',), 'canon'), 'box.canon': (('box',), 'canon'), 'workset.kuid': (('workset',), 'kuid'), 'workset.skip_kuid_check': (('workset',), 'skip_kuid_check')}
_SCOPE_NAMESPACES: frozenset[str] = frozenset({'system', 'agent', 'workset', 'box', 'config', 'meta'})
_SCOPE_CONTAINMENT: tuple[str, ...] = SCOPE_CONTAINMENT
_SCOPE_WRITE_ALLOWED: dict[ConfigLevel, frozenset[str]] = {level: frozenset(_SCOPE_CONTAINMENT[_SCOPE_CONTAINMENT.index(level.value):]) for level in ConfigLevel}
_SETTINGS_SCOPE_TOKENS: frozenset[str] = frozenset(_SCOPE_CONTAINMENT)
_FLAT_TO_CANONICAL: dict[str, str] = {_dot_to_flat(canonical): canonical for canonical in _KEY_ROUTES if _dot_to_flat(canonical) != canonical}
_PERSONA_STATE_LEAVES: frozenset[str] = frozenset({'endpoint', 'model', 'continue_mode', 'access', 'allow_helpers', 'bootstrap', 'template', 'canon'})
_PERSONA_ENV_SECTIONS: frozenset[str] = frozenset({'env'})
_AGENT_NODE_BIND_RE = re.compile('^agent\\.(?P<node>.+?)\\.(?P<cat>bindings\\.(?:ro|rw))\\.(?P<name>.+)$')
_AGENT_NODE_SECRET_RE = re.compile('^agent\\.(?P<node>.+?)\\.secret_path\\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)$')
_SCOPE_ENV_RE = re.compile('^(?P<scope>system|workset|box)\\.env\\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)$')
_NO_BARE_AGENT_KEY_SCOPES: 'frozenset[ConfigLevel]' = frozenset({ConfigLevel.box, ConfigLevel.workset})
_SCOPE_SECRET_RE = re.compile('^(?P<scope>system|workset|box)\\.secret_path\\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)$')
_SCOPE_READ_COMMAND = {'system': 'kanibako system get', 'workset': 'kanibako workset get <workset>', 'box': 'kanibako box get <box>'}
```

## Functions
```
def resolve_key(raw: str) -> str
def is_access_key(canonical: str) -> bool
def access_value_error(canonical: str, value: str) -> str | None
def parse_agent_node_bind_key(key: str) -> 'tuple[str, str, str] | None'
def scope_env_var_error(canonical: str) -> str | None
def bare_env_retired_error(key: str, *, verb: str, command_scope: 'ConfigLevel | None'=None) -> str | None
def box_agent_retired_error(canonical: str, *, verb: str, active_agent: str | None=None) -> str
def box_agent_redirect_key(canonical: str, command_scope: 'ConfigLevel | None', active_agent: str | None=None) -> str | None
def bare_agent_key_scope_error(canonical: str, command_scope: 'ConfigLevel | None', *, verb: str, active_agent: str | None=None) -> str | None
def is_known_key(arg: str) -> bool
def is_system_path_key(key: str) -> bool
def system_key_refusal(key: str, *, verb: str) -> str
def has_no_cli_write_route(target: str) -> bool
def scope_bind_retired_error(canonical: str, *, verb: str) -> str | None
def agent_node_bind_retired_error(canonical: str, *, verb: str) -> str | None
def agent_key_reason(node: str, tail: str) -> str | None
def agent_write_key_error(node: str, tail: str, *, verb: str) -> str | None
def agent_read_key_error(node: str, tail: str) -> str | None
def _coerce_value(canonical: str, value: 'str | None') -> object | str | None
def _scope_direction_error(canonical: str, command_scope: 'ConfigLevel | None') -> str | None
def _dot_to_flat(key: str) -> str
def _route_key(canonical: str) -> str
def _parse_persona_agent_key(key: str) -> 'tuple[str, str] | None'
def _is_persona_agent_key(key: str) -> bool
def _is_agent_node_bind_key(key: str) -> bool
def _parse_agent_node_secret_key(key: str) -> 'tuple[str, str] | None'
def _is_agent_node_secret_key(key: str) -> bool
def _persona_display_key(canonical: str) -> str
def _node_secret_display_key(canonical: str) -> str
def _is_bare_env_key(key: str) -> bool
def _is_scope_env_key(key: str) -> bool
def _is_agent_setting(key: str) -> bool
def _is_box_agent_key(key: str) -> bool
def _user_config_file_str() -> 'Path | str'
def _config_key_refusal(canonical: str, *, action: str) -> str
def _is_scope_secret_key(key: str) -> bool
def _is_pref_key(key: str) -> bool
def _pref_level(command_scope: 'ConfigLevel | None') -> str | None
def _pref_write_site_error(canonical: str, command_scope: 'ConfigLevel | None', *, verb: str='set') -> str | None
def _pref_target_error(canonical: str, command_scope: 'ConfigLevel | None') -> str | None
def _pref_sections_leaf(canonical: str) -> 'tuple[tuple[str, ...], str]'
def _scope_bind_match(key: str) -> 're.Match[str] | None'
def _agent_bind_match(key: str) -> 're.Match[str] | None'
def _is_agent_scope_bind_key(key: str) -> bool
def _is_scope_bind_key(key: str) -> bool
def _retired_because(category: str) -> str
def _bind_route_retired_message(display_key: str, *, verb: str, route: str, why: str, cure: str, survives: str) -> str
def _is_path_category_key(key: str) -> bool
def _has_dedicated_route(canonical: str) -> bool
def _probes_at_set_time(canonical: str) -> bool


class ConfigLevel(Enum):
    box = 'box'
    workset = 'workset'
    agent = 'agent'
    system = 'system'
```

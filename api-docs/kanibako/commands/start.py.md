# `src/kanibako/commands/start.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/start.py.md`.


## Variables

```
SUPERVISOR_FALLBACK_RELPATH = '.kanibako/supervisor-fallback.log'
DIRECTIVE_FLATTENER = '/opt/kanibako/kanibako/scripts/import-directives.py'
DIRECTIVE_MANIFEST_RELPATH = '.kanibako/directive-manifest.json'
VAULT_MASK_DEST = core_defaults.vault_mask_default()
_BOOTSTRAP_NONE = 'none'
_BOOTSTRAP_MISSING = object()
_PERSONA_TOKEN_VAR = 'ANTHROPIC_AUTH_TOKEN'
_ASSEMBLY_BINDINGS: 'tuple[str, ...]' = ('meta', 'assembly', 'bindings')
_ASSEMBLY_SEEDED: 'tuple[str, ...]' = ('meta', 'assembly', 'seeded')
_ASSEMBLY_SYNCED: 'tuple[str, ...]' = ('meta', 'assembly', 'synced')
_ASSEMBLY_ENV: 'tuple[str, ...]' = ('meta', 'assembly', 'env')
_HOME_OPTIONS: 'str' = 'Z,U'
_COLLISION_WARNED: 'set[tuple[str, str]]' = set()
_UNIX_SOCKET_PATH_LIMIT = 104
_ROTATE_MAX_BYTES = 1048576
_SOCKET_HASH_LEN = 16
```

## Functions
```
def ensure_persona_share_symlinks(std, agent_id, target) -> None
def add_start_parser(subparsers: argparse._SubParsersAction) -> None
def add_shell_parser(subparsers: argparse._SubParsersAction) -> None
def run_start(args: argparse.Namespace) -> int
def run_shell(args: argparse.Namespace) -> int
def start_detached(project_dir: str | None, *, explicit_agent: str | None=None, warm_only: bool=True) -> int
def writeback_session_credentials(target, proj, *, auth_src) -> None
def reset_collision_warnings() -> None
def emit_collision_warnings(collisions) -> None
def persona_create_verdict(std, config, proj, *, explicit_agent: str | None=None) -> str | None
def seed_new_box(std, config, proj, *, explicit_agent: str | None=None) -> None
def bounded_socket_name(identity: str, run_dir: Path) -> str
def validate_socket_path(socket_path: Path) -> None
def _agent_critical_dests() -> list[tuple[str, str]]
def _declared_behavior(key: str) -> str
def _declared_behavior_bool(key: str) -> bool
def _bootstrap_default() -> str
def _is_no_bootstrap(program: str | None) -> bool
def _effective_agent_scalar(proj, system_settings_path: 'Path | None', agent_id: str, *, key: str, floor: str, agent_state: 'agent_file.AgentFileLevel | None'=None, agent_path: 'Path | None'=None) -> 'str | None'
def _effective_bootstrap(proj, system_settings_path: 'Path | None', agent_id: str, *, agent_path: 'Path | None'=None) -> str
def _effective_transform(proj, system_settings_path: 'Path | None', agent_id: str, target, agent_cfg) -> 'str | None'
def _resolve_bootstrap_program(project_dir: str | None=None, explicit_agent: str | None=None) -> str
def _bootstrap_available(program: str | None=None) -> bool
def _check_box_components(proj) -> str | None
def _resolve_existing_box(std: StandardPaths, config: KanibakoConfig, project_dir: str | None) -> ProjectPaths | None
def _broken_standalone_error(std: StandardPaths, project_dir: str) -> str | None
def _no_box_error(project_dir: str | None, std: StandardPaths | None=None) -> str
def _unbuilt_box_error(proj: ProjectPaths) -> str | None
def _launch_issues_path(std, container_name: str) -> Path
def _check_launch_baseline(runtime, image, bootstrap_program, container_name, std)
def _print_launch_issues(std, container_name: str) -> None
def _shadow_issues_path(std, container_name: str) -> Path
def _persist_shadow_issues(std, container_name: str, shadowed: list[str]) -> None
def _print_shadow_issues(std, container_name: str) -> None
def _bootstrap_wrap(program: str, inner_cmd: str, cli_args: list[str]) -> tuple[str, list[str]]
def _env_flag_enabled(value: str | None) -> bool
def _build_supervisor_pid1(supervisor_argv: list[str], fallback_argv: list[str]) -> tuple[str, list[str]]
def _bootstrap_attach(program: str) -> list[str]
def _apply_tweakcc(install, agent_cfg, cache_path, image, runtime_cmd, logger)
def _parse_cli_env(cli_env: list[str] | None) -> dict[str, str]
def _refuse_retired_behavior(*, proj, agent_id, system_settings_path, agent_cfg_path) -> None
def _deliver_panel_permissions(*, target, proj, access, provider, logger)
def _assemble_image_sharing_mounts(*, merged, proj, runtime, std, agent_id, system_settings_path, agent_cfg_path, auth_src, extra_mounts, logger)
def _assemble_launch_env(*, std, proj, deliveries, env_slots, extra_mounts, logger)
def _start_helper_hub(*, runtime, image, container_name, proj, target, install, binary_mnts, tweakcc_entry, std, container_env, entrypoint, box_shell, agent_id, system_settings_path, agent_cfg_path, auth_src, extra_mounts)
def _persist_or_announce_flags(proj, box_settings_path: Path, *, image_override: str | None, share_images: bool) -> None
def _legacy_env_file_has_content(path) -> bool
def _warn_legacy_env_files(std, proj) -> None
def _run_container(*, project_dir: str | None, entrypoint: str | None, image_override: str | None, new_session: bool, continue_override: bool=False, safe_mode: bool, autonomous: bool=False, resume_mode: bool, extra_args: list[str], no_helpers: bool=False, no_auto_auth: bool=False, browser: bool=False, share_images: bool=False, persistent: bool=False, explicit_persistent: bool=False, explicit_ephemeral: bool=False, detach: bool=False, restart: bool=False, model_override: str | None=None, cli_env: list[str] | None=None, box_shell_mode: bool=False, explicit_agent: str | None=None, setup_only: bool=False, print_container: bool=False, warm_only: bool=False) -> int
def _print_setup_did_not_take(target) -> None
def _spawn_creds_watcher(proj) -> None
def _teardown_persistent_box(runtime: ContainerRuntime, container_name: str) -> None
def _build_config_env(env_slots: 'CollapsedEnvs') -> dict[str, str]
def _emit_secret_mounts(secrets, logger) -> 'tuple[list, list[str]]'
def _secret_export_shim(program: str, args: list[str], export_vars: list[str]) -> 'tuple[str, list[str]]'
def _directive_flatten_shim(program: str, args: list[str]) -> 'tuple[str, list[str]]'
def _secret_pointer_usable(raw_path: str) -> bool
def _name_new_box_probe(std, proj) -> None
def _persona_bundle_for(agent_id: str, target) -> 'PersonaBundle | None'
def _persona_values_for(agent_id: str, target) -> 'dict[str, str] | None'
def _warn_persona_store_diagnostics(agent_id: str, bundle) -> None
def _persona_wiring(target) -> 'PersonaSpec'
def _persona_token_pointer(agent_cfg, var: str, bundle) -> object
def _persona_secret_path_keys(agent_cfg, bundle) -> 'list[str]'
def _persona_probe_error(target, endpoint: str, token_ptr: 'str | None', model: 'str | None', display: str, logger) -> 'str | None'
def _resolve_codex_persona_env_key(agent_cfg, wiring, bundle=None) -> 'str | None'
def _resolve_codex_persona_provider(agent_id: str, endpoint: str, env_key: str, model: str, wiring) -> 'CodexModelProvider'
def _preflight_persona_load(agent_id: str, agent_cfg, keyspace_endpoint: str | None, logger, *, target=None, keyspace_model: object=__MISSING__, bundle=None, probe: bool=False) -> 'tuple[str | None, str | None, CodexModelProvider | None]'
def _model_tristate(keyspace_model: object) -> object
def _preflight_env_persona(agent_cfg, endpoint: str, keyspace_model: object, wiring, display: str, *, bundle=None, target=None, probe: bool=False, logger=None) -> 'tuple[str | None, str | None, CodexModelProvider | None]'
def _persona_no_endpoint_error(agent_id: str, wiring) -> str
def _preflight_config_file_persona(agent_id: str, agent_cfg, endpoint: str, keyspace_model: object, wiring, display: str, *, bundle=None, target=None, probe: bool=False, logger=None) -> 'tuple[str | None, str | None, CodexModelProvider | None]'
def _codex_persona_token_error(agent_cfg, wiring, endpoint: str, display: str, bundle=None) -> 'str | None'
def _effective_behavior_for_display(target, agent_cfg, project_toml, *, system_settings_path, workset_config_path=None, node_name=None) -> dict[str, str]
def _resolve_box_auth_source(*, std, proj, agent_name: str, system_settings_path, agent_cfg_path, selection_level: 'Mapping[str, object] | None')
def _resolve_box_launch_decisions(*, std, proj, target, agent_name: str, agent_cfg, system_settings_path, agent_cfg_path, selection_level: 'Mapping[str, object] | None', persona_values: 'Mapping[str, str] | None'=None) -> 'tuple[AuthSource, str | None, object]'
def _persona_model_state(snapshot: 'KeyStore', active_agent: str) -> object
def _launch_snapshot_inputs(*, std, proj, agent_name: str)
def _merge_default_categories(table: dict[str, object], incoming: 'Mapping[str, object]', *, family: str, origins: dict[tuple[str, str], str]) -> None
def _resolve_launch_snapshot(*, std, proj, agent_name: str, system_settings_path, agent_cfg_path, desc, install, target=None, agent_cfg=None, persona_values: 'Mapping[str, str] | None'=None, socket_path=None, log_path=None, graph_root=None, storage_conf_path=None, deliver_creds: bool=True, include_base_families: bool=True, extra_default_categories: 'Mapping[str, object] | None'=None, guarantee_create: bool=True, cli_level: 'Mapping[str, object] | None'=None, cli_env: 'Mapping[str, str] | None'=None, realize: 'Callable[[KeyStore], LaunchRealization] | None'=None, narrow_bind_dests: 'frozenset[str] | None'=None)
def _annotate_pref_origin(exc, prefs)
def _install_derived_bindings(snapshot, derived: 'Mapping[tuple[str, ...], object]') -> None
def _install_assembly_collapse(snapshot, entries, *, whole_box: bool, cli_env: 'Mapping[str, str] | None'=None) -> None
def _snapshot_home(snapshot) -> str
def _bind_map_from_mounts(mounts: list) -> 'dict[str, object]'
def _launch_bind_map(snapshot) -> 'dict[str, object]'
def _bind_map_masks(bindings) -> 'list[str]'
def _is_agent_delivery(entry) -> bool
def _agent_delivered_dests(entries: list) -> 'frozenset[str]'
def _narrow_bind_map(entries: list, dests: 'frozenset[str]') -> 'dict[str, object]'
def _emit_category_mounts(bindings, *, label: str, must_exist: frozenset[str]=frozenset(), skip_if_absent: frozenset[str]=frozenset()) -> list
def _seed_box_home(*, std, proj, target, desc, agent_id: str, agent_cfg_path, system_settings_path, auth_src, logger, suppress_oauth: bool=False) -> None
def _sync_box_at_create(*, std, proj, agent_name: str, target=None, desc=None, install=None, agent_cfg=None, global_config_path, agent_config_path, persona_values: 'Mapping[str, str] | None'=None, logger, deliver_creds: bool=True) -> None
def _snapshot_scalar(snapshot: 'KeyStore', dotted: str) -> str | None
def _snapshot_assembly_bindings(snapshot: 'KeyStore') -> 'dict[str, object] | None'
def _snapshot_assembly_seeded(snapshot: 'KeyStore') -> 'list[CollapsedCopy] | None'
def _snapshot_assembly_synced(snapshot: 'KeyStore') -> 'list[CollapsedCopy] | None'
def _launch_env_map(snapshot: 'KeyStore') -> 'CollapsedEnvs'
def _launch_seed_list(snapshot: 'KeyStore') -> 'list[CollapsedCopy]'
def _launch_synced_list(snapshot: 'KeyStore') -> 'list[CollapsedCopy]'
def _install_box_handbook(*, proj, snapshot: 'KeyStore', agent_id: str, logger) -> None
def _box_journal_key(proj) -> str
def _write_create_entry(std, proj) -> None
def _clear_create_entry(std, proj) -> None
def _pending_create_entry(std, proj) -> dict | None
def _register_new_box(std, proj, *, force: bool=False) -> None
def _synced_uptodate(src: Path, dest: Path) -> bool
def _apply_shell_copy(src: Path, dest: Path, *, label: str, name: str, host_src: str, logger, if_absent: bool, skip_if: 'Callable[[Path, Path], bool] | None'=None) -> None
def _host_copy_dest(box_dest: str, box_root: Path, *, label: str, name: str, logger) -> Path | None
def _apply_init_seeds(*, std, proj, agent_name: str, target=None, global_config_path, agent_config_path, logger, deliver_creds: bool=True) -> 'KeyStore'
def _synced_host_dest(box_dest: str, bindings, *, logger) -> 'Path | None'
def _synced_last_wins(copies: 'list[CollapsedCopy]') -> 'list[CollapsedCopy]'
def _synced_masks_replaced(copies: 'list[CollapsedCopy]', bindings) -> 'list[str]'
def _apply_synced_copies(*, snapshot: 'KeyStore', bindings, logger, skip_if: 'Callable[[Path, Path], bool] | None') -> None
def _core_env_default_categories(*, proj, target, agent_id) -> dict[str, str]
def _install_realized_env(snapshot, env, *, agent_id: str, desc) -> None
def _declared_agent_env_key(snapshot, agent_id: str, var: str) -> 'str | None'
def _refuse_realized_twin(var: str, declared_key: str, *, agent_id: str, driving_key: str, is_access: bool) -> None
def _channel_default_categories(std, proj) -> 'core_defaults.BindArmTable'
def _seed_channel_files(std, proj) -> None
def _core_default_categories(std, proj, *, guarantee_create: bool=True) -> 'core_defaults.BindArmTable'
def _canon_reprotect_hook(proj, logger)
def _kanibako_mounts()
def _run_setup_command(*, runtime: ContainerRuntime, image: str, proj, container_name: str, setup_entrypoint: str, setup_args: list[str], extra_mounts: list, tmpfs_masks, container_env: dict[str, str]) -> int
def _container_logs(runtime: ContainerRuntime, name: str) -> str
def _container_exit_code(runtime: ContainerRuntime, name: str) -> int
def _interactive_host() -> bool
def _restore_host_terminal() -> None
def _validate_mounts(mounts: list, logger) -> None
def _rotate_file(path: Path) -> None


class LaunchRealization(NamedTuple):
    effective_state: dict[str, str]
    cascade_access: str
    launch_access: str
    env: dict[str, str]

class _LaunchRealizer:
    def __init__(self, *, desc, agent_id: str, safe_mode: bool, autonomous: bool, provider_pin=())

    @property
    def result(self) -> LaunchRealization

    def __call__(self, snapshot) -> LaunchRealization
```

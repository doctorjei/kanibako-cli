# `src/kanibako/commands/stop.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def stale_lock_cure(lock_file) -> str
def stuck_container_cure(runtime: ContainerRuntime, name: str, state: str) -> str
def rm_command(runtime: ContainerRuntime, name: str) -> str
def rm_cure(runtime: ContainerRuntime, name: str) -> str
def rm_left_it(runtime: ContainerRuntime, name: str) -> bool
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run(args: argparse.Namespace) -> int
def stop_box_helper_hub(std: StandardPaths, proj: ProjectPaths) -> None
def _writeback_on_stop(runtime, proj, container_name: str, *, std, config, box_is_live: bool, already_said: tuple[ConfigError | SettingsError, ...]=()) -> None
def _warn_settings(exc: KanibakoError) -> None
def _load_paths(config: BootstrapConfig) -> tuple[StandardPaths, ConfigError | None]
def _resolve_target(std: StandardPaths, config: BootstrapConfig, project_dir: str | None) -> tuple[ProjectPaths, ConfigError | None]
def _is_live(runtime: ContainerRuntime, name: str) -> bool
def _stop_one(runtime: ContainerRuntime, *, project_dir: str | None) -> int
def _boxes_rendering_no_name() -> list[str]
def _stop_all(runtime: ContainerRuntime, *, force: bool=False) -> int
```

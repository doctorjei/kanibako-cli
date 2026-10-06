# `src/kanibako/commands/stop.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def add_parser(subparsers: argparse._SubParsersAction) -> None
def run(args: argparse.Namespace) -> int
def _writeback_on_stop(runtime, proj, container_name: str, *, std, config, box_is_live: bool, already_said: tuple[ConfigError | SettingsError, ...]=()) -> None
def _warn_settings(exc: ConfigError) -> None
def _load_paths(config: BootstrapConfig) -> tuple[StandardPaths, ConfigError | None]
def _resolve_target(std: StandardPaths, config: BootstrapConfig, project_dir: str | None) -> tuple[ProjectPaths, ConfigError | None]
def _stop_one(runtime: ContainerRuntime, *, project_dir: str | None) -> int
def _boxes_rendering_no_name() -> list[str]
def _stop_all(runtime: ContainerRuntime, *, force: bool=False) -> int
```

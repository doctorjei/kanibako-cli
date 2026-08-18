# `src/kanibako/runtime/container.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/runtime/container.py.md`.


## Variables

```
logger = get_logger('container')
KEEP_ID_USERNS = f'--userns=keep-id:uid={GUEST_UID},gid={GUEST_GID}'
_POST_START_TIMEOUT_S = 30.0
_POST_START_POLL_S = 0.25
_CANON_GUEST_PREFIX = f'{GUEST_HOME}/canon'
_CANON_SEED_DENY_GUEST_PREFIXES = tuple((f'{GUEST_HOME}/{rel}' for rel in CANON_SEED_DENY_PREFIXES))
```

## Functions
```
def remove_box_tree(target: Path) -> bool
def detect_shadowed_mounts(shell_path: Path, project_path: Path, extra_mounts: list | None, enable_vault: bool) -> list[str]
def _run_post_start(hook: 'Callable[[], None]') -> None
def _is_managed_canon_dest(dest: str) -> bool
def _is_seed_denied_canon_dest(dest: str) -> bool
def _guest_dest_to_host(dest: str, shell_path: Path, project_path: Path, *, map_home_root: bool=False) -> Path | None
def _precreate_mount_stubs(shell_path: Path, project_path: Path, extra_mounts: list | None, enable_vault: bool, vault_ro_path: Path, vault_rw_path: Path, tmpfs_masks: list[str]) -> None
```

## Classes

```
class ContainerRuntime:
    def __init__(self, command: str | None=None) -> None

    def image_exists(self, image: str) -> bool
    def image_inspect(self, image: str) -> dict | None
    def pull(self, image: str, *, quiet: bool=True) -> bool
    def remove_image(self, image: str) -> None
    def unshare_rm(self, path: Path) -> bool
    def unshare_chown(self, paths: list[Path], uid: int, gid: int) -> bool
    def unshare_chmod(self, paths: list[Path], mode: str) -> bool
    def build(self, image: str, containerfile: Path, context: Path) -> None
    def rebuild(self, image: str, containerfile: Path, context: Path, build_args: dict[str, str] | None=None) -> int
    def run_interactive(self, image: str, *, container_name: str | None=None) -> int
    def commit(self, container: str, image: str) -> None
    def cp(self, src: Path, dest: str) -> bool
    def save(self, image: str, out: Path) -> bool
    def load(self, archive: Path) -> str | None
    def diff(self, image: str) -> list[str]
    def ensure_image(self, image: str, containers_dir: Path | None=None) -> None
    def run(self, image: str, *, shell_path: Path, project_path: Path, vault_ro_path: Path, vault_rw_path: Path, extra_mounts: list | None=None, tmpfs_masks: list[str] | None=None, enable_vault: bool=True, env: dict[str, str] | None=None, name: str | None=None, entrypoint: str | None=None, cli_args: list[str] | None=None, detach: bool=False, post_start: 'Callable[[], None] | None'=None) -> int
    def exec(self, name: str, command: list[str], *, env: dict[str, str] | None=None, attach: bool=False) -> int
    def exec_ready(self, name: str) -> bool
    def container_exists(self, name: str) -> bool
    def stop(self, name: str) -> bool
    def rm(self, name: str) -> bool
    def is_running(self, name: str) -> bool
    def inspect_env(self, name: str, key: str) -> str | None
    def container_image(self, name: str) -> str | None
    def list_running(self, prefix: str='kanibako-') -> list[tuple[str, str, str]]
    def get_local_digests(self, image: str) -> list[str]
    def get_local_digest(self, image: str) -> str | None
    def get_local_created(self, image: str) -> str | None
    def get_local_tags(self, image: str) -> list[str]
    def get_local_label(self, image: str, label: str) -> str | None
    def get_local_platform(self, image: str) -> str | None
    def list_local_images(self) -> list[tuple[str, str]]

    @staticmethod
    def _detect() -> str
    def _unshare_apply(self, argv: list[str], paths: list[Path]) -> bool
    def _watch_for_start(self, name: str, post_start: 'Callable[[], None]') -> 'threading.Event'
```

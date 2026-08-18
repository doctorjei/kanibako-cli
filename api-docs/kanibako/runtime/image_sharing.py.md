# `src/kanibako/runtime/image_sharing.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/runtime/image_sharing.py.md`.


## Variables

```
logger = get_logger('image_sharing')
SHARED_STORE_CONTAINER_PATH = '/var/lib/shared-images'
VIRTIOFS_GRAPHROOT_MESSAGE = "Error: podman's image storage is on a virtiofs filesystem.\n  graph root: {graph_root}\n  Rootless podman cannot overlay-mount or pivot_root on virtiofs, so the\n  box cannot launch (otherwise you'd hit a cryptic 'pivot_root: permission\n  denied' / overlay error from the runtime).\n  This can happen if you use kento to compose VM images.\n\n  Fix it one of these ways:\n    - Back podman's graph root with a real filesystem (a real-disk or\n      loopback-backed ext4) instead of the virtiofs share.\n    - Use a rootful runtime by pointing KANIBAKO_DOCKER_CMD at a rootful\n      podman/docker shim (rootful overlay works on virtiofs).\n\n  Note: rootless podman on a virtiofs graph root is an unsupported\n  configuration."
```

## Functions
```
def detect_graph_root(runtime_cmd: str) -> Path | None
def is_rootless_podman(runtime_cmd: str) -> bool | None
def path_fstype(path: Path) -> str | None
def virtiofs_graphroot_message(runtime_cmd: str) -> str | None
def generate_storage_conf(shared_store_path: str) -> str
def write_storage_conf(staging_dir: Path) -> Path
```

# `src/kanibako/boxfs.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
logger = get_logger(__name__)
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
```

## Functions
```
def first_symlink(path: Path, root: Path) -> Path | None
def no_symlink_or_warn(path: Path, root: Path) -> bool
def read_bytes(path: Path, root: Path) -> bytes
def read_text(path: Path, root: Path, encoding: str='utf-8') -> str
def write_bytes(path: Path, root: Path, data: bytes, mode: int=384) -> None
def write_text(path: Path, root: Path, data: str, mode: int=384) -> None
def copy_file(src: Path, dst: Path, *, src_root: Path | None, dst_root: Path | None) -> None
def copy_tree(src: Path, dst: Path, *, src_root: Path | None, dst_root: Path | None) -> None
def touch(path: Path, root: Path, mode: int=438) -> None
def unlink(path: Path, root: Path, *, missing_ok: bool=False) -> None
def rmtree_contents(path: Path, root: Path) -> None
def chmod(path: Path, root: Path, mode: int) -> None
def chmod_tree(path: Path, root: Path, *, add: int=0, remove: int=0) -> None
def rename(src: Path, dst: Path, root: Path) -> None
def _parts(path: Path, root: Path) -> tuple[str, ...]
def _is_link_at(name: str, dir_fd: int) -> bool
def _open_dir_at(name: str, dir_fd: int, path: Path, shown: Path, create: bool) -> int
def _open_dirs(path: Path, root: Path, parts: tuple[str, ...], create: bool) -> int
@contextlib.contextmanager
def _parent(path: Path, root: Path, *, create: bool=False) -> Iterator[tuple[int, str]]
@contextlib.contextmanager
def _dir(path: Path, root: Path) -> Iterator[int]
def _open_leaf(name: str, dir_fd: int, path: Path, flags: int, mode: int=438) -> int
def _refuse_leaf_link(name: str, dir_fd: int, path: Path) -> None
def _regular(fd: int, path: Path) -> os.stat_result
def _open_read(path: Path, root: Path | None) -> int
def _replace_at(dir_fd: int, leaf: str, fill: Callable[[BinaryIO], object], mode: int, times: tuple[int, int] | None=None) -> None
@contextlib.contextmanager
def _write_parent(path: Path, root: Path | None) -> Iterator[tuple[int, str]]
def _copy_dir(sfd: int, dfd: int, shown: Path) -> None
def _chmod_fd(fd: int, mode: int, path: Path) -> None
```

## Classes

```
class SymlinkRefusedError(OSError):
    def __init__(self, path: Path, link: Path) -> None
```

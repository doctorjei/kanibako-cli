# `src/kanibako/tweakcc_cache.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/tweakcc_cache.py.md`.


## Variables

```
logger = get_logger('tweakcc_cache')
```

## Functions
```
def config_hash(config: dict) -> str
```

## Classes

```
class TweakccCacheError(Exception):

@dataclass
class CacheEntry:
    path: Path
    fd: int

class TweakccCache:
    def __init__(self, cache_dir: Path) -> None

    def ensure_dir(self) -> None
    def cache_key(self, cli_js_hash: str, cfg_hash: str) -> str
    def get(self, key: str) -> CacheEntry | None
    def put(self, key: str, source_binary: Path, patch_fn: Callable[[Path, Path], None]) -> CacheEntry
    def release(self, entry: CacheEntry) -> bool

    def _entry_path(self, key: str) -> Path
```

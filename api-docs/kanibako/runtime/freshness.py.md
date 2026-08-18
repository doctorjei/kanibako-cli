# `src/kanibako/runtime/freshness.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/runtime/freshness.py.md`.


## Variables

```
_CACHE_TTL = 86400
_VERSION_LABEL = 'org.opencontainers.image.version'
_MAX_VERSION_TAG_PROBES = 12
_BANNER = "Note: A newer version of {image} is available. Run 'kanibako rig update' to update."
```

## Functions
```
def check_image_freshness(runtime: ContainerRuntime, image: str, cache_path: Path) -> None
def _is_newer_available(runtime: ContainerRuntime, image: str, cache_path: Path) -> bool
def _parse_pep440(tag: str) -> Version | None
def _version_tags_sorted(image: str, cache_path: Path) -> list[tuple[Version, str]]
def _resolve_remote_version(image: str, platform: str | None, remote_digests: set[str], cache_path: Path) -> Version | None
def _resolve_local_version(runtime: ContainerRuntime, image: str, remote_digests: set[str], remote_version: Version | None, cache_path: Path) -> Version | None
def _parse_ts(value: str | None) -> datetime | None
def _cache_key(image: str, platform: str | None) -> str
def _load_cache(cache_path: Path) -> dict
def _store_cache(cache_path: Path, cache: dict) -> None
def _cached_field(cache_path: Path, key: str, field: str, fetch)
def _cached_remote_digests(image: str, platform: str | None, cache_path: Path) -> set[str]
def _cached_remote_tags(image: str, cache_path: Path) -> list[str]
def _cached_tag_digest(image: str, tag: str, cache_path: Path) -> str | None
def _cached_remote_created(image: str, platform: str | None, cache_path: Path) -> str | None
```

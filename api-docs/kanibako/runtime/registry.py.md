# `src/kanibako/runtime/registry.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/runtime/registry.py.md`.


## Variables

```
_TIMEOUT = 5
_ACCEPT = ', '.join(('application/vnd.docker.distribution.manifest.v2+json', 'application/vnd.oci.image.index.v1+json', 'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json'))
_INDEX_MEDIA_TYPES = frozenset(('application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json'))
```

## Functions
```
def get_remote_digests(image: str, platform: str | None) -> set[str]
def list_remote_tags(image: str) -> list[str]
def get_remote_tag_digest(image: str, tag: str) -> str | None
def get_remote_created(image: str, platform: str | None) -> str | None
def _fetch_manifest_config_digest(registry: str, repo: str, ref: str, token: str | None) -> str | None
def _fetch_config_created(registry: str, repo: str, config_digest: str, token: str | None) -> str | None
def _parse_image_ref(image: str) -> tuple[str, str, str]
def _get_anonymous_token(registry: str, repo: str) -> str | None
def _manifest_headers(token: str | None) -> dict[str, str]
def _fetch_manifest_digest(registry: str, repo: str, tag: str, token: str | None) -> str | None
def _fetch_manifest_meta(registry: str, repo: str, tag: str, token: str | None) -> tuple[str | None, str | None]
def _resolve_index_child(registry: str, repo: str, tag: str, token: str | None, platform: str) -> str | None
```

# `src/kanibako/settings/keyspace_manifest.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**
Prose for these symbols lives in `llm-docs/kanibako/settings/keyspace_manifest.py.md`.


## Variables

```
KEYSPACE_MANIFEST_FILENAME = 'keyspace-manifest.yaml'
```

## Functions
```
def manifest_doc() -> dict[str, Any]
@lru_cache(maxsize=1)
def _parse_manifest() -> dict[str, Any]
```

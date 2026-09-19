# `src/kanibako/settings/workset_dirkeys.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**


## Variables

```
WORKSET_PATH_REF = 'meta.workset.path'
```

## Functions
```
def resolve_workset_dir_key(workset_root: Path, repoint: str | None, default_leaf: str, *, key: str, extra_refs: Mapping[str, str] | None=None) -> Path
def _host_ctx() -> ResolveCtx
```

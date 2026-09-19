# `src/kanibako/runtime/rig_resolve.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**


## Functions
```
def resolve_rig(name: str, runtime: ContainerRuntime, std: StandardPaths, merged: KanibakoConfig, *, registry: dict[str, RigRecord] | None=None) -> RigResolution
```

## Classes

```
@dataclass(frozen=True)
class RigResolution:
    name: str
    kind: str
    image: str
    prep_action: str
    containerfile: Path | None = None
    source_ref: str | None = None
```

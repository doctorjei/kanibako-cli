# `src/kanibako/runtime/rig_bundle.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**


## Variables

```
BUNDLE_SUFFIX = '.rig.tgz'
_META_ARCNAME = 'rig.yaml'
_IMAGE_ARCNAME = 'image.tar'
_CONTAINERFILE_ARCNAME = 'Containerfile'
```

## Functions
```
def pack_bundle(out: Path, rig_yaml: Path, image_tar: Path, containerfile: Path | None=None) -> None
def unpack_bundle(tgz: Path, dest_dir: Path) -> dict[str, Path]
def read_bundle_meta(tgz: Path) -> RigMeta
def _is_safe_member(name: str) -> bool
```

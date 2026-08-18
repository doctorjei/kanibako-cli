# `src/kanibako/runtime/rig_source.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/runtime/rig_source.py.md`.


## Variables

```
_TEMPLATE_SCAN_LINES = 20
_TEMPLATE_HEADER_RE = re.compile('^#\\s*kanibako-template(?:-check)?:\\s*', re.IGNORECASE)
_FROM_DIRECTIVE_RE = re.compile('^\\s*FROM\\s+\\S', re.IGNORECASE)
_REF_RE = re.compile('^[a-z0-9]+([._-][a-z0-9]+)*(:[0-9]+)?(/[a-z0-9]+([._-][a-z0-9]+)*)*(:[\\w][\\w.-]*)?(@sha256:[a-f0-9]+)?$')
_IMAGE_TAR_MARKERS = ('manifest.json', 'oci-layout')
```

## Functions
```
def fetch_to_temp(url: str) -> Path
def detect_source_kind(source: str, *, force: str | None=None) -> str
def derive_name(source: str, kind: str) -> str | None
def _is_image_tar(path: str) -> bool
def _has_template_signal(path: Path) -> bool
def _name_from_containerfile_basename(basename: str) -> str | None
def _name_from_ref(ref: str) -> str | None
def _name_from_image_tar(path: str) -> str | None
```

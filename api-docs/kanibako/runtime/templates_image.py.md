# `src/kanibako/runtime/templates_image.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/runtime/templates_image.py.md`.


## Variables

```
_TEMPLATE_PREFIX = 'kanibako-template-'
_RIG_PREFIX = 'kanibako-rig-'
_VALID_NAME_RE = re.compile('^[a-z0-9][a-z0-9_-]*$')
_TEMPLATE_FILE_PREFIX = 'Containerfile.template-'
_DESC_HEADER_RE = re.compile('^#\\s*kanibako-template:\\s*(.+?)\\s*$')
_DESC_HEADER_SCAN_LINES = 10
_CHECK_HEADER_RE = re.compile('^#\\s*kanibako-template-check:\\s*(.+?)\\s*$')
_CHECK_HEADER_SCAN_LINES = 30
```

## Functions
```
def validate_template_name(name: str) -> None
def template_image_name(name: str) -> str
def rig_image_name(name: str) -> str
def read_template_checks(containerfile: Path) -> tuple[str, ...]
def list_bundled_templates(containers_dir: Path | None=None, *, override_dir: Path | None=None) -> list[BundledTemplate]
def _bundled_containers_dir() -> Path | None
def _read_template_description(containerfile: Path, name: str) -> str
def _scan_template_dir(containers_dir: Path | None, source: str) -> list[BundledTemplate]


class BundledTemplate(NamedTuple):
    name: str
    description: str
    source: str = 'bundled'
```

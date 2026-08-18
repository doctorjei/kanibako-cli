# `src/kanibako/scripts/import-directives.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/scripts/import-directives.py.md`.


## Variables

```
CODEX_DOC_LIMIT = 32 * 1024
MANIFEST_VERSION = 1
IMPORT_RE = re.compile('(?<![\\w@])@([\\w./~-]+)')
FENCE_RE = re.compile('^\\s{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$')
COMMENT_OPEN = '<!--'
COMMENT_CLOSE = '-->'
_TRAILING_PUNCT = '.,;:!?)]}\'"'
_USAGE = "usage: import-directives.py SOURCE DEST [--manifest PATH]   (DEST '-' = stdout)\n       import-directives.py --additional-context SOURCE\n"
```

## Functions
```
def atomic_write_text(path: Path, data: str, mode: int=420) -> None
def code_span_ranges(line: str) -> list[tuple[int, int]]
def strip_comments(line: str, in_comment: bool) -> tuple[str, bool]
def build_manifest(fl: Flattener, seed: Path, dest: Path, output: str) -> dict
def flatten(source: str, dest: str | None, *, additional_context: bool=False, manifest: str | None=None) -> int
def main(argv: list[str]) -> int
```

## Classes

```
class Flattener:
    def __init__(self) -> None

    def slug(self, path: Path) -> str
    def anchor(self, path: Path) -> str
    def resolve(self, raw: str, importing_file: Path)
    def collect(self, path: Path) -> None
    def render(self, source: Path) -> str

    def _slugify(self, path: Path) -> str
    def _record_misses(self, tried: list[Path]) -> None
    def _process_text(self, text: str, importing_file: Path) -> str
    def _process_line(self, line: str, importing_file: Path) -> str
```

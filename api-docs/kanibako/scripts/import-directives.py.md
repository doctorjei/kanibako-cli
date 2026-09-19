# `src/kanibako/scripts/import-directives.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Variables

```
DIRECTIVE_SIZE_WARN_LIMIT = 32 * 1024
MANIFEST_VERSION = 1
IMPORT_PATH = '[\\w./~-]+'
IMPORT_RE = re.compile(f'(?<![\\w@])@(?P<path>{IMPORT_PATH})')
LINK_RE = re.compile(f'\\[(?P<text>[^\\]\\n]+)\\]\\(@(?P<lpath>{IMPORT_PATH})\\)')
MENTION_RE = re.compile(f'{LINK_RE.pattern}|{IMPORT_RE.pattern}')
NUMBERED_ROW_RE = re.compile('^(?P<indent>[ \\t]*)(?P<num>\\d+(?:\\.\\d+)*\\.?)(?P<gap>[ \\t]+)')
ATX_HEADING_RE = re.compile('^ {0,3}(?P<hashes>#{1,6})(?:\\s|$)')
FENCE_RE = re.compile('^\\s{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$')
COMMENT_OPEN = '<!--'
COMMENT_CLOSE = '-->'
MAX_HEADING_DEPTH = 6
DEFAULT_TITLE_FMT = '@'
SECTION_TITLE_FMT = '{__SECTION__(source, sep)} @'
_TOKEN = '\x00kanibako-row:{}\x00'
_TOKEN_RE = re.compile('\\x00kanibako-row:(\\d+)\\x00')
_TRAILING_PUNCT = '.,;:!?)]}\'"'
_GLOB_MAGIC_RE = re.compile('[*?\\[]')
_TEMPLATE_PARAMS = ('target', 'source', 'sep', 'title_fmt')
_TEMPLATE_CALLS: dict[str, tuple[bool, str | None]] = {'__IMPORT__': (True, None), '__LINK__': (False, None), '__IMPORTSECTION__': (True, SECTION_TITLE_FMT), '__LINKSECTION__': (False, SECTION_TITLE_FMT)}
_TEMPLATE_CALL_RE = re.compile('^(?P<indent>[ \\t]*)(?P<name>__(?:IMPORT|LINK)(?:SECTION)?__)\\s*\\(.*\\)\\s*$')
_USAGE = "usage: import-directives.py SOURCE DEST [--manifest PATH]   (DEST '-' = stdout)\n       import-directives.py --additional-context SOURCE\n"
```

## Functions
```
def atomic_write_text(path: Path, data: str, mode: int=420) -> None
def code_span_ranges(line: str) -> list[tuple[int, int]]
def strip_comments(line: str, in_comment: bool) -> tuple[str, bool]
def first_real_line(text: str) -> str | None
def gfm_anchor(text: str) -> str
def home_relative(path: Path) -> str
def assign_section_numbers(rows: list[tuple[str | None, bool]], enclosing_level: int=1) -> list[tuple[str | None, int] | None]
def super_of(entry: str, sep: str='.') -> str
def evaluate_expression(expr: str, scope: dict[str, object]) -> object
def render_format(fmt: str, title: str, scope: dict[str, object]) -> str
def parse_template_call(line: str) -> tuple[str, str, dict[str, object]] | None
def split_trailing_punct(text: str) -> tuple[str, str]
def build_manifest(fl: Flattener, seed: Path, dest: Path, output: str) -> dict
def flatten(source: str, dest: str | None, *, additional_context: bool=False, manifest: str | None=None) -> int
def main(argv: list[str]) -> int
def _count_title_slots(fmt: str) -> int
def _template_literal(node: ast.expr, param: str, name: str) -> object
```

## Classes

```
class FormatError(Exception):

class TemplateCallError(Exception):

class Flattener:
    def __init__(self) -> None

    def next_section(self, source: str='', sep: str='.') -> str
    def preplink(self, target: str, source: str='', sep: str='.', title_fmt: str | None=None, base: Path | None=None, current: str='') -> list[tuple[Path, str, str, str]]
    def template_import(self, entries: list[tuple[tuple[Path, str, str, str], str | None]], importing_file: Path, enclosing: int | None, listing: int) -> list[str]
    def template_link(self, entries: list[tuple[tuple[Path, str, str, str], str | None]], importing_file: Path, enclosing: int | None, listing: int) -> list[str]
    def slug(self, path: Path) -> str
    def anchor(self, path: Path) -> str
    def resolve(self, raw: str, importing_file: Path)
    def collect(self, path: Path) -> None
    def render(self, source: Path) -> str

    def _template_entries(self, target: str, source: str, sep: str, title_fmt: str | None, importing_file: Path, current: str) -> list[tuple[tuple[Path, str, str, str], str | None]]
    def _retitle(self, path: Path, old_title: str, new_line: str) -> bool
    def _run_template_call(self, line: str, call: tuple[str, str, dict[str, object]], importing_file: Path, enclosing: int | None, listing: int) -> str
    def _slugify(self, path: Path) -> str
    def _unique_anchor(self, base: str) -> str
    def _record_misses(self, tried: list[Path]) -> None
    def _process_text(self, text: str, importing_file: Path) -> tuple[str, str]
    def _process_line(self, line: str, importing_file: Path, enclosing: int | None=None, listing: int=0) -> tuple[str, bool]
    def _finalize_links(self) -> None
    def _emit(self, path: Path) -> str

class _Row:
    __slots__ = ('container', 'target', 'text', 'number', 'gap', 'enclosing', 'listing', 'kind')

    def __init__(self, container: Path, target: Path, text: str, number: str | None, gap: str, enclosing: int | None, listing: int, kind: str='authored') -> None
```

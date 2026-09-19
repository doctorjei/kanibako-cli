#!/usr/bin/env python3
"""Generate a signature-only API doc for a source file: no comments, no docstrings, no bodies.

DETERMINISTIC & REGENERABLE - never hand-edit the output; re-run this instead. Signatures come
from the AST, so the doc cannot drift from the code without the diff showing it.
"""

from __future__ import annotations

import argparse
import ast
import copy
import sys
from pathlib import Path

STUB = ast.Expr(value=ast.Constant(value=Ellipsis))

# PEP 695 (``type Foo = int | str``) is 3.12+, and ``ast.TypeAlias`` does not EXIST before it -
# so naming the class directly makes this tool crash on import-time attribute lookup under 3.11.
# That mattered the moment the tool moved into the repository: the project's floor is
# ``requires-python = ">=3.11"`` and every CI job pins 3.11, so a 3.12-only tool is one no clone
# and no workflow can run.
#
# A TUPLE rather than a class, because ``isinstance`` takes one directly and an EMPTY tuple
# matches nothing - which is exactly right on 3.11, where no node can be a type alias because the
# syntax does not parse.  A 3.11 run meeting ``type Foo = int`` therefore raises SyntaxError and
# is reported as a PARSE ERROR at exit 2, loudly, rather than quietly filing the statement wrong.
# Nothing is lost on 3.12+: there the tuple holds the real node type and every branch below reads
# exactly as it did when it named the class.
TYPE_ALIAS_NODES: tuple[type[ast.AST], ...] = (
  (ast.TypeAlias,) if hasattr(ast, "TypeAlias") else ()
)

# ---------------------------------------------------------------------------
# Types-vs-Variables discriminator
#
# A module-level assignment lands in ``## Types`` only when its RIGHT-HAND SIDE is a type
# EXPRESSION.  The annotation never decides - only the value does.  Two counter-examples from
# ``src/kanibako/box_supervisor.py``, both of which are VARIABLES:
#
#   XDG_PROJECTIONS: tuple[tuple[str, str, str], ...] = (('XDG_STATE_HOME', ...),)
#       ANNOTATED with a type, but the RHS is a tuple literal -> Variable.
#   log = get_logger('box_supervisor')
#       RHS is a call -> Variable.  (``TypeVar``/``NewType``/``ParamSpec`` are the only calls
#       that make a Type; everything else that calls something produces a value.)
#
# Anything the rules below cannot positively identify as a type falls through to Variables.
# That bias is deliberate: a value mislabelled "Type" is a lie about the API, a type listed
# under Variables is merely a filing error.
# ---------------------------------------------------------------------------

# Subscript bases that can only ever be spelled to build a type: ``BindMap = dict[str, X]``.
TYPE_SUBSCRIPT_BASES = frozenset({
  "Annotated", "Awaitable", "Callable", "ClassVar", "Coroutine", "Dict", "FrozenSet",
  "Generator", "Iterable", "Iterator", "List", "Literal", "Mapping", "MutableMapping",
  "MutableSequence", "Optional", "Sequence", "Set", "Tuple", "Type", "Union",
  "dict", "frozenset", "list", "set", "tuple", "type",
})

# Calls that DEFINE a type rather than produce a value.
TYPE_FACTORY_CALLS = frozenset({
  "NewType", "ParamSpec", "TypeAliasType", "TypeVar", "TypeVarTuple",
})

# Bare names that are types when used as an alias RHS (``PWTimeout: type[Exception] = Exception``).
TYPE_BARE_NAMES = frozenset({
  "Any", "AnyStr", "BaseException", "Exception", "bool", "bytearray", "bytes", "complex",
  "dict", "float", "frozenset", "int", "list", "object", "set", "str", "tuple", "type",
})


def _tail_name(node: ast.AST) -> str:
  """Rightmost identifier of a Name/Attribute chain (``collections.abc.Callable`` -> Callable)."""
  if isinstance(node, ast.Attribute):
    return node.attr
  if isinstance(node, ast.Name):
    return node.id
  return ""


def _is_type_leaf(node: ast.AST) -> bool:
  """A leaf of a ``X | Y`` union that is recognisably a type (or ``None``)."""
  if isinstance(node, ast.Constant) and node.value is None:
    return True
  if isinstance(node, ast.Subscript):
    return _tail_name(node.value) in TYPE_SUBSCRIPT_BASES
  if isinstance(node, (ast.Name, ast.Attribute)):
    return _tail_name(node) in TYPE_BARE_NAMES
  return False


def is_type_assignment(node: ast.stmt) -> bool:
  """True when *node* declares a TYPE (see the discriminator note above)."""
  if isinstance(node, TYPE_ALIAS_NODES):       # PEP 695: ``type Foo = int | str``
    return True
  if not isinstance(node, (ast.Assign, ast.AnnAssign)):
    return False

  annotation = node.annotation if isinstance(node, ast.AnnAssign) else None
  if annotation is not None:
    # ``Foo: TypeAlias = ...`` is a declaration of intent; take it at its word.
    if _tail_name(annotation) == "TypeAlias":
      return True

  value = node.value
  if value is None:
    return False
  if isinstance(value, ast.Call):
    return _tail_name(value.func) in TYPE_FACTORY_CALLS
  if isinstance(value, ast.Subscript):
    return _tail_name(value.value) in TYPE_SUBSCRIPT_BASES
  if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
    # ``MaybeStr = str | None`` - every leaf must be type-ish, or it is a value union
    # (``_MODELED_KEYS = IDENTITY_KEYS | frozenset({...})`` is a frozenset, not a type).
    leaves: list[ast.AST] = []
    stack: list[ast.AST] = [value]
    while stack:
      cur = stack.pop()
      if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.BitOr):
        stack += [cur.left, cur.right]
      else:
        leaves.append(cur)
    return all(_is_type_leaf(leaf) for leaf in leaves)
  if isinstance(value, (ast.Name, ast.Attribute)):
    # A bare alias is a type only when it names one, or the annotation says ``type[...]``.
    if _tail_name(value) in TYPE_BARE_NAMES:
      return True
    return annotation is not None and _tail_name(annotation) == "type"
  return False


# ---------------------------------------------------------------------------
# Naming / ordering
# ---------------------------------------------------------------------------

def bound_name(node: ast.stmt) -> str:
  """The name a statement binds (first target for a tuple unpack); '' when there is none."""
  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
    return node.name
  if isinstance(node, TYPE_ALIAS_NODES):
    # Both hops are ``getattr`` because a TUPLE of node types does not narrow the way naming the
    # class did: to a checker ``node`` is still ``ast.stmt``, which has no ``.name``.  On 3.12+
    # this reads ``node.name.id`` exactly as before; on 3.11 the branch is unreachable.
    return getattr(getattr(node, "name", None), "id", "")
  if isinstance(node, ast.AnnAssign):
    return node.target.id if isinstance(node.target, ast.Name) else ""
  if isinstance(node, ast.Assign):
    for target in node.targets:
      for sub in ast.walk(target):
        if isinstance(sub, ast.Name):
          return sub.id
  return ""


def _is_private(name: str) -> bool:
  return name.startswith("_")


def by_visibility(nodes: list[ast.stmt]) -> list[ast.stmt]:
  """Public first, then ``_``-prefixed; STABLE source order inside each partition.

  This is the ONLY ordering rule.  Source order is information the author already encoded, so
  the doc reproduces it rather than imposing a second, unmaintained ordering of its own: if a
  listing reads wrong, the fix belongs in the module, where one change corrects both.
  """
  return [n for n in nodes if not _is_private(bound_name(n))] + \
         [n for n in nodes if _is_private(bound_name(n))]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
  """Decorator lines + the ``def`` line, with NO trailing colon and NO body."""
  stubbed = copy.deepcopy(node)
  stubbed.body = [STUB]
  lines = ast.unparse(stubbed).split("\n")
  if lines[-1].strip() == "...":        # drop the stub body ast.unparse insists on
    lines.pop()
  lines[-1] = lines[-1].rstrip()
  if lines[-1].endswith(":"):
    lines[-1] = lines[-1][:-1]
  return lines


def _class_header(node: ast.ClassDef) -> list[str]:
  """Decorator lines + ``class Name(Bases):``."""
  bare = copy.deepcopy(node)
  bare.decorator_list = []
  bare.body = [STUB]
  header = ast.unparse(bare).split("\n")[0]
  return [f"@{ast.unparse(dec)}" for dec in node.decorator_list] + [header]


def render_class(node: ast.ClassDef) -> list[str]:
  """A class as its surface: fields, then ``__init__``, public methods, private methods, nested."""
  fields = [c for c in node.body if isinstance(c, (ast.AnnAssign, ast.Assign))]
  methods = [c for c in node.body if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))]
  nested = [c for c in node.body if isinstance(c, ast.ClassDef)]

  init = [m for m in methods if m.name == "__init__"]
  rest = [m for m in methods if m.name != "__init__"]

  groups: list[list[str]] = []
  # Field order is SEMANTIC (dataclass positional order), so it is never repartitioned.
  if fields:
    groups.append([ast.unparse(f) for f in fields])
  for group in (init, [m for m in rest if not _is_private(m.name)],
                [m for m in rest if _is_private(m.name)]):
    if group:
      groups.append([line for m in group for line in _signature(m)])
  for inner in nested:
    groups.append(render_class(inner))

  body: list[str] = []
  for group in groups:
    if body:
      body.append("")
    body += group
  # An empty class (docstring-only, e.g. a bare exception subclass) renders as its header ALONE.
  # No `...` filler: bodies are gone from this document everywhere, without exception, and the
  # fence is not Python, so nothing needs a placeholder to stay syntactically whole.
  return _class_header(node) + [f"    {line}" if line else "" for line in body]


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

# Which headings are followed by a blank line before the fence.  This is ASYMMETRIC on purpose,
# and the asymmetry is inherited rather than designed: ``## Variables`` has the blank line in Jei's
# hand-edited exemplar (api-doc-format-exemplars/jei-box_supervisor.py.md) and ``## Types`` /
# ``## Functions`` do not.  ``## Classes`` was added later and ruled to follow ``## Variables``.
# Reproducing the exemplar byte-for-byte outranks internal tidiness here; making all four uniform
# is a one-line change to this set if that is ever ruled the other way.
BLANK_AFTER_HEADING = frozenset({"Variables", "Classes"})


def sections(src: str) -> list[tuple[str, list[str]]]:
  """(heading, fenced lines) for each non-empty section, in document order."""
  body = ast.parse(src).body
  assignments = [n for n in body if isinstance(n, (ast.Assign, ast.AnnAssign, *TYPE_ALIAS_NODES))]
  functions = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
  classes = [n for n in body if isinstance(n, ast.ClassDef)]

  variables = [n for n in assignments if not is_type_assignment(n)]
  types = [n for n in assignments if is_type_assignment(n)]

  out: list[tuple[str, list[str]]] = []
  if variables:
    out.append(("Variables", [ast.unparse(n) for n in by_visibility(variables)]))
  if types:
    # The trailing blank line is verbatim from the exemplar (box_supervisor.py.md line 30).
    out.append(("Types", [ast.unparse(n) for n in by_visibility(types)] + [""]))
  if functions:
    out.append(("Functions", [line for fn in by_visibility(functions) for line in _signature(fn)]))
  if classes:
    lines: list[str] = []
    for cls in by_visibility(classes):
      if lines:
        lines.append("")                      # one blank line between class blocks
      lines += render_class(cls)
    out.append(("Classes", lines))
  return out


def document(src_path: Path, mirror_rel: str, src: str) -> str:
  """The full markdown doc for one module."""
  # The tool is spelled as a PATH IN BACKTICKS, which is the EXEMPLAR'S OWN FORM restored rather
  # than a new choice -- repo-relative now, because the tool is in the repo; the exemplars' path
  # was relative to canon, their own root.  The hand-made exemplars read ``regenerate with
  # `notebook/scripts/dev-tools/gen-api-doc.py` ``.  The 98th replaced that path with a prose
  # description for one reason - the tool then lived in the maintainer's canon notebook, so the
  # path was a claim no clone could resolve.  Moving the tool to ``scripts/`` on 2026-09-19 spent
  # that reason, and the path below resolves in every clone.
  #
  # ⚑ IT IS A CHECKED CLAIM, not decoration.  `api-docs/` is not excluded from `kinemata claims`,
  # which reads `.md` and resolves path claims against the tree - so all 139 docs assert this file
  # exists, and deleting or moving the tool without regenerating reds the documentation gate.
  lines = [
    f"# `{src_path.as_posix()}` — API surface",
    "",
    "_Signatures only: no comments, no docstrings, no bodies._",
    "**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**",
  ]
  # The prose pointer is emitted ONLY when the file it points at exists: most modules have no
  # llm-docs companion, and a pointer to a file that was never written is a false message to
  # every reader.  Writing one therefore makes its api-doc stale - that is the pointer arriving.
  if Path("llm-docs", mirror_rel + ".md").exists():
    lines.append(f"Prose for these symbols lives in `llm-docs/{mirror_rel}.md`.")
  blocks = sections(src)
  if blocks:
    lines.append("")                          # first blank of the two under the header
  for heading, fenced in blocks:
    lines.append("")
    lines.append(f"## {heading}")
    if heading in BLANK_AFTER_HEADING:
      lines.append("")
    lines += ["```", *fenced, "```"]
  return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Paths & discovery
# ---------------------------------------------------------------------------

SOURCE_ROOTS = ("src", "packages")

# WHAT api-docs COVER: the importable library code of the five distributions - nothing else.
# Three families under the source roots are NOT that, and are skipped:
#
#   build/                gitignored wheel-build copies of packages/*/src - duplicates, not source.
#   tests/                not API surface.  The main repo's tests/ tree is already outside the
#                         source roots; excluding packages/agent-*/tests/ makes that consistent.
#   data/base/canon/      shipped DATA, delivered into a box and run standalone.  Not importable
#                         from the package, and its interface is --help, not its symbols.
SKIP_PARTS = ("build", "tests")
SKIP_SUBPATHS = (("data", "base", "canon"),)


def _skipped(path: Path) -> bool:
  parts = path.parts
  if any(part in SKIP_PARTS for part in parts):
    return True
  return any(
    parts[i:i + len(sub)] == sub
    for sub in SKIP_SUBPATHS
    for i in range(len(parts) - len(sub) + 1)
  )


def mirror_rel(src_path: Path) -> str:
  """Doc path relative to api-docs/ (and to llm-docs/), minus the ``.md`` suffix.

  Everything up to and including the first ``src`` component is dropped, so both
  ``src/kanibako/settings/paths.py`` and
  ``packages/agent-claude/src/kanibako/plugins/claude/target.py`` mirror under ``kanibako/``.
  A path with no ``src`` component loses only its first component.
  """
  parts = src_path.parts
  if "src" in parts:
    return "/".join(parts[parts.index("src") + 1:])
  return "/".join(parts[1:])


def out_path(src_path: Path) -> Path:
  return Path("api-docs", mirror_rel(src_path) + ".md")


def discover() -> list[Path]:
  """Every source module under the source roots, sorted, deterministic."""
  found: set[Path] = set()
  for root in SOURCE_ROOTS:
    for path in Path(root).rglob("*.py"):
      if not _skipped(path):
        found.add(path)
  return sorted(found)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

EPILOG = """\
OUTPUT PATH
  api-docs/<mirror>.md, where <mirror> is the source path with everything up to and including
  the first `src` component removed:
    src/kanibako/settings/paths.py                 -> api-docs/kanibako/settings/paths.py.md
    packages/agent-claude/src/.../claude/target.py -> api-docs/kanibako/plugins/claude/target.py.md
  --out overrides it for a single file.

DOCUMENT SHAPE
  A header block, then up to four fenced sections - each emitted only if it has content:
    ## Variables   module-level assignments whose RHS is a VALUE
    ## Types       module-level assignments whose RHS is a TYPE EXPRESSION
    ## Functions   module-level functions
    ## Classes     module-level classes
  A section names the kind of symbol it holds, so a class never files under Functions.
  Fences are plain ``` - the contents are a signature listing, not runnable Python.
  Bodies are gone entirely: no `...` stubs, and `def` lines carry no trailing colon.
  The header points at `llm-docs/<mirror>.md` only when that companion exists, so writing one
  makes the api-doc stale until it is regenerated.

ORDERING
  Public first, then _-prefixed; STABLE SOURCE ORDER inside each partition.  That is the only
  rule - source order is the author's, and the doc never imposes a second one, so a listing
  that reads wrong is fixed in the module.  It applies to every section, classes included.
  Inside a class: fields (source order - dataclass field order is semantic), then __init__,
  then public methods, then private methods, then nested classes.

SCOPE
  api-docs cover the IMPORTABLE LIBRARY CODE of the five distributions.  --all walks src/ and
  packages/ and skips three families that are not that:
    build/            gitignored wheel-build copies of packages/*/src
    tests/            not API surface
    data/base/canon/  shipped data payloads, run standalone in a box; their interface is --help

MODES
  <source>          regenerate one doc
  --all             regenerate every module under src/ and packages/ (see SCOPE)
  --check           exit 1 if a doc is missing or stale, instead of writing
  --check --all     CI staleness gate over the whole tree
  --stdout          print instead of writing
  --list-unchanged  list the docs that are ALREADY current, over the same tree as --all: one
                    repo-relative path per line and nothing else, for a machine to read.  It
                    writes no file and exits 0 whether or not anything is stale - it reports the
                    healthy set, it does not judge it, and a doc ABSENT from the list is how a
                    parity check sees staleness.  (Exit 2 only when a source will not parse, so
                    no verdict was formed at all.)

Run from the repository root; paths are repo-relative.
"""


def _render(src_path: Path) -> str:
  return document(src_path, mirror_rel(src_path), src_path.read_text())


# The three verdicts a module can carry.  Named, because two modes read them and a mistyped
# literal would silently empty a listing rather than fail.
NEW, CHANGED, CURRENT = "new", "changed", "current"


def _compare(src_path: Path) -> tuple[Path, str, str]:
  """(destination, the doc we WOULD write, verdict) - NEW, CHANGED or CURRENT.

  ONE verdict for every mode that needs one, so a listing cannot disagree with the count it is
  checked against.  Raises SyntaxError, from _render, when the source does not parse.
  """
  dest = out_path(src_path)
  text = _render(src_path)
  if not dest.exists():
    return dest, text, NEW
  return dest, text, CHANGED if dest.read_text() != text else CURRENT


def _list_unchanged() -> int:
  """Print the repo-relative path of every api-doc that is already what we would write.

  The HEALTHY population, on purpose: the list an outside check compares against must be
  non-empty when nothing is wrong, or the check reds in the good state and cannot tell a clean
  tree from a run that looked at nothing.  So a missing or stale doc is simply ABSENT here, and
  that absence is the staleness signal - there is deliberately no "list the stale ones" mode.
  Exit is 0 either way: this mode reports, it never judges, and a non-zero exit would read as
  the oracle being broken rather than a doc being stale.  The one failure it does report is a
  source that will not parse, where no verdict exists to report.
  """
  for src_path in discover():
    try:
      dest, _text, verdict = _compare(src_path)
    except SyntaxError as exc:
      # stdout carries paths and nothing else - the consumer is a regex extractor.
      print(f"PARSE ERROR: {src_path}: {exc}", file=sys.stderr)
      return 2
    if verdict == CURRENT:
      print(dest.as_posix())
  return 0


def _run_all(check: bool) -> int:
  paths = discover()
  new = changed = current = 0
  stale: list[str] = []
  for src_path in paths:
    try:
      dest, text, verdict = _compare(src_path)
    except SyntaxError as exc:
      print(f"PARSE ERROR: {src_path}: {exc}")
      return 2
    if verdict == NEW:
      new += 1
      stale.append(f"MISSING: {dest}")
    elif verdict == CHANGED:
      changed += 1
      stale.append(f"STALE: {dest}")
    else:
      current += 1
      continue
    if not check:
      dest.parent.mkdir(parents=True, exist_ok=True)
      dest.write_text(text)
  for line in stale:
    print(line)
  verb = "stale/missing" if check else "written"
  print(f"{len(paths)} modules: {new} new, {changed} changed, {current} unchanged "
        f"({new + changed} {verb})")
  return 1 if check and stale else 0


def main() -> int:
  ap = argparse.ArgumentParser(
    description="Generate a signature-only API doc (no comments, docstrings or bodies).",
    epilog=EPILOG,
    formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  ap.add_argument("source", nargs="?",
                  help="repo-relative path, e.g. src/kanibako/settings/paths.py")
  ap.add_argument("--all", action="store_true",
                  help="regenerate every importable module under src/ and packages/ (see SCOPE)")
  ap.add_argument("--out", help="output path (default: api-docs/<mirror>.md)")
  ap.add_argument("--check", action="store_true", help="exit 1 if a doc is missing or stale")
  ap.add_argument("--stdout", action="store_true", help="print instead of writing")
  ap.add_argument("--list-unchanged", action="store_true",
                  help="print the path of every doc that is already current; writes nothing")
  args = ap.parse_args()

  if args.list_unchanged:
    if args.source or args.all or args.out or args.check or args.stdout:
      ap.error("--list-unchanged takes no source and combines with no other mode")
    return _list_unchanged()
  if args.all:
    if args.source or args.out or args.stdout:
      ap.error("--all takes no source and cannot be combined with --out or --stdout")
    return _run_all(args.check)
  if not args.source:
    ap.error("a source path is required (or use --all)")

  src_path = Path(args.source)
  dest = Path(args.out) if args.out else out_path(src_path)
  text = _render(src_path)

  if args.stdout:
    print(text, end="")
    return 0
  if args.check:
    if not dest.exists() or dest.read_text() != text:
      print(f"STALE or MISSING: {dest}")
      return 1
    print(f"current: {dest}")
    return 0
  dest.parent.mkdir(parents=True, exist_ok=True)
  dest.write_text(text)
  print(f"wrote {dest} ({len(text.splitlines())} lines)")
  return 0


if __name__ == "__main__":
  sys.exit(main())

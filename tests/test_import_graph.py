"""The shipped tree's NON-LAZY import graph is a DAG.

⚑⚑ **THE ASSERTION IS NOT "THE PACKAGE GRAPH IS ACYCLIC" — THAT IS FALSE TODAY.**
``settings/__init__.py`` names "the tree's one large import cycle, every arc of which is
broken today by a" deferred import, and ``settings/config.py`` + ``settings/paths.py``
between them carry dozens of function-local imports that exist for exactly that reason.
The honest invariant is the one those deferrals BUY: **no import that runs when a module
is imported can return to the module that started it.**

**Why this exists rather than another named-file pin.** Everything standing in for this
rule is a proxy over ONE file — ``test_bootstrap_is_import_free`` and
``test_messages_imports_only_the_terminal_leaf`` in ``tests/test_settings/test_paths.py``,
plus the ``⚑ Lazy import … do not hoist`` comments, which are prose with nothing enforcing
them. A cycle introduced anywhere else goes unseen, and each proxy has to be hand-extended
whenever a new leaf appears (P13: a pin asserts the RULE, never an inventory). A graph
assertion has no allowlist to maintain.
🛑 Those two named tests are RETAINED, not subsumed. Each scans its file's TEXT for an
``import``/``from`` at any indentation, so it forbids EVERY import in
``settings/bootstrap.py`` and ``settings/messages.py`` — function-local ones INCLUDED,
which :func:`_from_statement` deliberately cannot see. Stronger in that one dimension and
narrower in every other; this graph is an addition to them, never a replacement.
⚑ It would have caught a real event: a LibCST codemod HOISTED a function-local import to
module scope in ``settings/defaults_inventory.py``, converting a deliberate cycle-breaker
into a module-level edge. It happened not to close a cycle, and nothing would have said so
if it had.

---

**WHAT COUNTS AS AN EDGE — the whole model, because the answer is not obvious.**

An edge is an import statement that **executes when the importing module is imported**:

* A **function or method body** is skipped. That is the tree's primary cycle-breaker and it
  is deliberate — ``settings/paths.py`` reaches ``project/workset.py`` only from inside
  function bodies precisely so that ``project/workset.py`` can import ``StandardPaths`` at
  module scope (``CONVENTIONS.md`` § *Import structure*). Counting those would assert a rule
  the tree has never held.
* An ``if TYPE_CHECKING:`` body is skipped, for the same reason with a different mechanism:
  it never runs. It is not decoration — measured, counting the guarded arm turns this tree
  from a DAG into three cycles: ``utils`` ↔ ``settings/paths``, ``targets/base`` ↔
  ``vscode/vscode_config``, and a ``targets/assembly`` triangle through the same arc. An
  ``else:`` arm beside one DOES run and is walked.
* A **class body** IS walked: it executes at import time.
* ``try`` / ``except`` / ``if`` / ``with`` / ``for`` / ``while`` / ``match`` bodies are all
  walked. A guarded import still runs.

🛑 **ANCESTOR PACKAGES ARE NOT SYNTHESISED, and that is a modelling decision, not a
concession.** Importing ``kanibako.targets.base`` does execute ``kanibako/targets/__init__``
— but if that ``__init__`` is the module already in progress, Python finds it in
``sys.modules`` and the import is a no-op. Adding ``X.Y → X`` edges therefore reports the
ordinary package-with-a-facade shape as a cycle: measured here, it manufactures two false
arcs (``targets/__init__`` → ``targets/shell`` → ``targets``, and the same shape in
``packages/agent-claude``), neither of which is a cycle at runtime.

**Corpus:** ``src/kanibako/**`` plus ``packages/*/src/**``, as one graph. Settled 2026-09-02.
The in-repo plugins import core function-locally on purpose (a module-scope name there fails
mid-launch for a new plugin on an old core), and a module-scope walk cannot see a
function-local import at all — so including them costs nothing and buys the case that
matters: **a plugin that grows a MODULE-LEVEL edge back into core is a real defect, and
nothing else would catch it.** Their reason for deferring differs from ``settings/``'s;
this test does not care. It asserts one property over one graph.
⚑ ``src/kinemata_views.py`` is out of scope: it sits beside ``src/kanibako/``, not inside it,
and is a kinemata predicate module rather than shipped source.
⚑ The corpus RULE — every shipped python file, core plus each plugin's own ``src`` tree —
has FOUR carriers under ``tests/``, this file included. Re-derive the set rather than
trusting this sentence:
``git grep -n "_shipped_sources\\|_plugin_sources\\|_source_roots" -- tests/``.
``_shipped_sources`` in ``test_plugin_import_compat.py``, and a near-verbatim copy of it in
``test_settings/test_settings_launch.py``, both want a FLAT file list; ``_plugin_sources``
in ``test_plugin_store_isolation.py`` groups BY DISTRIBUTION and its own docstring says why
it is not shared; ``_source_roots`` here pairs each file with the import root that NAMES
its module, which no other carrier needs. THREE shapes over FOUR carriers, then: each shape
needs something the others do not, which is what makes a copy defensible and why P10 wants
that reason stated where the copy lives. Consolidating them into ``tests/support/`` is a
live candidate and was left alone here deliberately.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, NamedTuple

from tests.support.repo import REPO_ROOT


# --------------------------------------------------------------------------- #
# The corpus                                                                   #
# --------------------------------------------------------------------------- #

#: Directories that are never shipped source: stale wheel-build copies, and another
#: agent's live worktree.  Same exclusion ``_shipped_sources`` states.
_NOT_SOURCE = ("build", ".claude")


def _source_roots() -> list[tuple[Path, Path]]:
    """``(import root, subtree to walk)`` for every shipped source tree.

    The import root is what a module name is relative TO — ``src/`` makes
    ``src/kanibako/cli.py`` into ``kanibako.cli`` — and it differs from the subtree
    actually walked, because ``src/`` holds a non-shipped sibling of ``kanibako/``.
    """
    roots = [(REPO_ROOT / "src", REPO_ROOT / "src" / "kanibako")]
    roots += [(pkg, pkg) for pkg in sorted((REPO_ROOT / "packages").glob("*/src"))]
    return roots


def _module_name(path: Path, import_root: Path) -> str:
    """The dotted name ``path`` is importable as, from *import_root*."""
    parts = list(path.relative_to(import_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def corpus() -> dict[str, Path]:
    """Every shipped module, by the name it is imported as.

    ⚑ A ``.py`` file whose derived name is not a dotted run of identifiers is NOT a
    node here, because nothing can import it: it is package DATA that happens to be
    Python.  The rule rather than a list of the files it currently reaches — there are
    three today, a bundled flattener script and two canon payload scripts under
    ``agent-claude``, and a fourth would need no edit here.  Excluding them is not
    cosmetic: an unimportable file cannot participate in a cycle, but a ``SyntaxError``
    in one would red this test for a reason that has nothing to do with imports.
    """
    found: dict[str, Path] = {}
    for import_root, subtree in _source_roots():
        for path in sorted(subtree.rglob("*.py")):
            if any(part in _NOT_SOURCE for part in path.parts):
                continue
            name = _module_name(path, import_root)
            if all(part.isidentifier() for part in name.split(".")):
                found[name] = path
    return found


# --------------------------------------------------------------------------- #
# The walk                                                                     #
# --------------------------------------------------------------------------- #

def _guards_type_checking(node: ast.If) -> bool:
    """Is this ``if TYPE_CHECKING:`` (bare, or ``typing.TYPE_CHECKING``)?

    Deliberately only the two plain spellings.  A compound test
    (``if TYPE_CHECKING and x:``) reads as a live conditional here and its body is
    walked — erring toward COUNTING an edge, which can only over-report a cycle and
    never hide one.
    """
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


#: Nodes that hold further statements: ordinary statements, plus the two grammar
#: containers that are not themselves statements.
_HOLDS_STATEMENTS = (ast.stmt, ast.ExceptHandler, ast.match_case)


def module_scope_imports(node: ast.AST) -> Iterator[ast.Import | ast.ImportFrom]:
    """Every import statement that RUNS when the enclosing module is imported."""
    for child in ast.iter_child_nodes(node):
        yield from _from_statement(child)


def _from_statement(node: ast.AST) -> Iterator[ast.Import | ast.ImportFrom]:
    """The same question asked of ONE statement.

    Split from :func:`module_scope_imports` rather than inlined, because the two
    ask different things — "what is inside this node" versus "is this node it" — and
    an ``else:`` arm needs the second.  Collapsing them dropped every import directly
    under such an arm, which is what ``test_the_else_arm_of_a_type_checking_guard_is_an_edge``
    caught.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return  # a body that runs on CALL, not on import: the cycle-breaker
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        yield node
    elif isinstance(node, ast.If) and _guards_type_checking(node):
        for alternative in node.orelse:  # the `else:` arm DOES run
            yield from _from_statement(alternative)
    elif isinstance(node, _HOLDS_STATEMENTS):
        yield from module_scope_imports(node)


def _absolute_target(node: ast.ImportFrom, importer: str, path: Path) -> str:
    """The absolute dotted module an ``ImportFrom`` names, relative form resolved."""
    if not node.level:
        return node.module or ""
    if path.name == "__init__.py":
        package = importer  # a package's own `__init__` IS the package
    else:
        package = importer.rsplit(".", 1)[0] if "." in importer else ""
    parts = package.split(".") if package else []
    ascend = node.level - 1
    if ascend:
        parts = parts[:-ascend] if ascend <= len(parts) else []
    base = ".".join(parts)
    return f"{base}.{node.module}" if node.module else base


class Arc(NamedTuple):
    """One edge, with the site that would have to change to remove it."""

    importer: str
    imported: str
    site: str  # `path:lineno`, relative to the repo root


def edges(modules: dict[str, Path]) -> list[Arc]:
    """The module-scope edge set over *modules*, in a stable order.

    ``from X import Y`` contributes an edge to ``X``, and — when ``X.Y`` is itself a
    module in the corpus — to ``X.Y`` as well, because that form imports the submodule.
    A name that resolves outside the corpus is not an edge; this graph is about the
    shipped tree reaching back into itself.
    """
    seen: set[tuple[str, str]] = set()
    arcs: list[Arc] = []
    for importer, path in sorted(modules.items()):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in module_scope_imports(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                base = _absolute_target(node, importer, path)
                targets = [base] + [f"{base}.{alias.name}" for alias in node.names]
            site = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
            for target in targets:
                if target in modules and target != importer:
                    if (importer, target) not in seen:
                        seen.add((importer, target))
                        arcs.append(Arc(importer, target, site))
    return arcs


# --------------------------------------------------------------------------- #
# The property                                                                 #
# --------------------------------------------------------------------------- #

def cycles(arcs: list[Arc]) -> list[list[str]]:
    """Every cycle reachable as a back edge, each as a CLOSED path ``a → b → a``.

    A depth-first colouring: a grey node reached again is a back edge, and the grey
    stack from that node down is the arc.  One cycle per back edge, which is enough —
    a graph with any cycle has at least one back edge — and the path is what a reader
    needs, not the count.
    """
    out: dict[str, list[str]] = {}
    for arc in arcs:
        out.setdefault(arc.importer, []).append(arc.imported)

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}
    stack: list[str] = []
    found: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        colour[node] = GREY
        stack.append(node)
        for nxt in sorted(out.get(node, ())):
            if colour.get(nxt, WHITE) == GREY:
                arc = stack[stack.index(nxt):]
                # Normalised by rotation so one cycle is reported once however entered.
                key = tuple(arc[arc.index(min(arc)):] + arc[:arc.index(min(arc))])
                if key not in seen:
                    seen.add(key)
                    found.append([*arc, nxt])
            elif colour.get(nxt, WHITE) == WHITE:
                visit(nxt)
        stack.pop()
        colour[node] = BLACK

    for node in sorted({arc.importer for arc in arcs} | {arc.imported for arc in arcs}):
        if colour.get(node, WHITE) == WHITE:
            visit(node)
    return found


def _report(found: list[list[str]], arcs: list[Arc]) -> str:
    """The refusal.  It NAMES THE ARC — a bare "cycle detected" makes the next reader
    re-derive by hand the part that is most of the work."""
    site = {(arc.importer, arc.imported): arc.site for arc in arcs}
    lines = [
        f"the non-lazy import graph has {len(found)} cycle(s). Every arc below is a "
        f"MODULE-SCOPE import — one of them has to move into a function body (or, if it "
        f"is types-only, under `if TYPE_CHECKING:`):",
        "",
    ]
    for path in found:
        lines.append("  " + " -> ".join(path))
        for start, end in zip(path, path[1:]):
            lines.append(f"      {start} -> {end}")
            lines.append(f"          {site.get((start, end), '?')}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Guarding the guard                                                           #
# --------------------------------------------------------------------------- #

def _imports_from(body: list[ast.stmt], module: str) -> list[ast.ImportFrom]:
    """Every ``from <module> import …`` anywhere beneath the statements in *body*.

    The two re-anchor pins below have to assert the KIND of an import, not merely its
    presence.  A text match cannot tell the two deferral mechanisms apart, so a guarded
    import that quietly became function-local — or the reverse — would leave every
    assertion passing while the discrimination those tests exist to prove stopped being
    exercised.  Taking a statement LIST rather than a node is what makes the distinction
    expressible: ``if TYPE_CHECKING:`` is read from ``.body`` alone, because an import in
    the ``else:`` arm beside it is a live edge and not a guarded one.
    """
    return [
        node
        for stmt in body
        for node in ast.walk(stmt)
        if isinstance(node, ast.ImportFrom) and node.module == module
    ]


def test_the_corpus_is_not_vacuous() -> None:
    """An empty corpus would make the assertion below pass for free (P15)."""
    modules = corpus()
    assert len(modules) > 100, len(modules)
    assert {"kanibako.cli", "kanibako.settings.paths", "kanibako.project.workset"} <= set(modules)
    assert any(name.startswith("kanibako.plugins.") for name in modules), (
        "no in-repo plugin module was collected; `packages/*/src` is in scope"
    )
    # `src/kinemata_views.py` sits BESIDE `src/kanibako/`; it is a kinemata predicate
    # module, not shipped source.
    assert "kinemata_views" not in modules


def test_the_edge_set_is_not_vacuous() -> None:
    """A walk that collected nothing would be a DAG trivially."""
    arcs = edges(corpus())
    assert len(arcs) > 200, len(arcs)


def test_the_walk_excludes_a_function_local_import() -> None:
    """The deferral the tree's documented cycle rests on is INVISIBLE to this graph.

    ``CONVENTIONS.md`` § *Import structure*: ``project/workset.py`` imports from
    ``settings/paths.py`` at module scope, and the reverse edge is deferred into function
    bodies.  Both halves are asserted — the present edge and the absent one — because an
    over-eager walk and an under-eager one fail in opposite directions and only the pair
    pins the discrimination.
    """
    pairs = {(arc.importer, arc.imported) for arc in edges(corpus())}
    assert ("kanibako.project.workset", "kanibako.settings.paths") in pairs
    assert ("kanibako.settings.paths", "kanibako.project.workset") not in pairs

    tree = ast.parse(
        (REPO_ROOT / "src" / "kanibako" / "settings" / "paths.py").read_text(encoding="utf-8")
    )
    deferred = [
        imported
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for imported in _imports_from(node.body, "kanibako.project.workset")
    ]
    assert deferred, (
        "`settings/paths.py` no longer reaches `kanibako.project.workset` from inside a "
        "FUNCTION BODY; the deferral this test discriminates against is gone or has "
        "changed mechanism — re-anchor the pin"
    )


def test_the_walk_excludes_a_type_checking_import() -> None:
    """A types-only import is skipped, and the pin is a pair that WOULD be a cycle.

    ``utils.py`` names ``ProjectPaths`` under ``if TYPE_CHECKING:`` while
    ``settings/paths.py`` imports ``utils`` for real.  Counting the guarded arm would
    close that two-module loop against code that is correct.
    """
    pairs = {(arc.importer, arc.imported) for arc in edges(corpus())}
    assert ("kanibako.settings.paths", "kanibako.utils") in pairs
    assert ("kanibako.utils", "kanibako.settings.paths") not in pairs

    tree = ast.parse((REPO_ROOT / "src" / "kanibako" / "utils.py").read_text(encoding="utf-8"))
    guarded = [
        imported
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and _guards_type_checking(node)
        for imported in _imports_from(node.body, "kanibako.settings.paths")
    ]
    assert guarded, (
        "`utils.py` no longer names `kanibako.settings.paths` UNDER `if TYPE_CHECKING:`; "
        "the guarded import this test discriminates against is gone or has changed "
        "mechanism — re-anchor the pin"
    )


def test_a_class_body_import_is_an_edge() -> None:
    """A class body runs at import time, so an import in one is a real edge."""
    source = "class C:\n    import kanibako.cli\n"
    names = [n.names[0].name for n in module_scope_imports(ast.parse(source))]
    assert names == ["kanibako.cli"]


def test_the_else_arm_of_a_type_checking_guard_is_an_edge() -> None:
    """``if TYPE_CHECKING: ... else: import x`` — the ``else`` is what actually runs."""
    source = "if TYPE_CHECKING:\n    import a\nelse:\n    import b\n"
    names = [n.names[0].name for n in module_scope_imports(ast.parse(source))]
    assert names == ["b"]


def test_the_cycle_finder_names_the_whole_arc() -> None:
    """Proving the detector can red, and that its report is a PATH and not a verdict.

    A synthetic graph, so this holds whatever the real tree looks like: a test never
    seen to fail is not evidence.
    """
    arcs = [
        Arc("a", "b", "a.py:1"),
        Arc("b", "c", "b.py:2"),
        Arc("c", "a", "c.py:3"),
        Arc("d", "a", "d.py:4"),  # feeds in without joining the loop
    ]
    found = cycles(arcs)
    assert found == [["a", "b", "c", "a"]], found

    message = _report(found, arcs)
    assert "a -> b -> c -> a" in message
    for site in ("a.py:1", "b.py:2", "c.py:3"):
        assert site in message
    assert "d.py:4" not in message


def test_the_cycle_finder_accepts_a_dag() -> None:
    """The complement: a diamond is not a cycle, however many paths it has."""
    arcs = [
        Arc("a", "b", "a.py:1"),
        Arc("a", "c", "a.py:2"),
        Arc("b", "d", "b.py:1"),
        Arc("c", "d", "c.py:1"),
    ]
    assert cycles(arcs) == []


# --------------------------------------------------------------------------- #
# The assertion                                                                #
# --------------------------------------------------------------------------- #

def test_the_non_lazy_import_graph_is_acyclic() -> None:
    """No import that runs at import time returns to the module that started it."""
    arcs = edges(corpus())
    found = cycles(arcs)
    assert not found, _report(found, arcs)

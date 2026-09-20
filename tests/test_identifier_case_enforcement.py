"""ENFORCEMENT guardrail: identifier comparison folds, and only ONE place folds it.

``specs/settings-keyspace-1.8.0.md`` §0, the ``⚑ NAMING RULES`` bullet, is two rules:
**fold to compare, never fold to store**, and **never fold a filesystem path**.  Prose
cannot hold either one — the tree has already been through a cure that folded on ENTRY
(``[R171]``, retired) and through a half-folded lookup that folded the QUERY and not the
stored key.  Both look correct in a diff.

So the rule is asserted syntactically, in two directions:

* **Nobody membership-tests an identifier registry directly.**  ``name in registry`` is
  the exact-match comparison ``find_identifier`` exists to replace, and it is the shape
  every one of the fixed sites had.  The allowed population is ZERO — there is no
  allowlist here and there must not be one.
* **Nobody folds an identifier by hand.**  ``identifiers._fold`` is private precisely so
  that no caller can fold one half of a comparison; a ``.lower()`` on a variable named
  like an identifier is that private fold, re-spelled.

🛑 **If a variable named ``name``/``value`` genuinely is NOT a kanibako identifier —
an HTTP header field, say — RENAME IT.**  ``proxy/server.py`` was renamed to ``header``
for exactly this reason.  Do not add an exemption: a name that reads like an identifier
and is folded IS the confusion this guard exists to stop, whatever the variable holds.

Scope note: the SHIPPED source trees only (core plus each plugin's ``packages/*/src``).
Tests fold freely — they build fixtures rather than shipping behavior.

Indent note: 4 spaces, matching every sibling under ``tests/`` (house style is 2).
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

from tests.support.repo import REPO_ROOT

#: The carrier.  The one module permitted to fold.
_CARRIER = "src/kanibako/identifiers.py"

#: The public comparison seam every lookup must go through.
_SEAM = "find_identifier"

#: Functions that return an identifier-keyed REGISTRY (``{name: …}``).  Derived from the
#: rule rather than from a sweep: each one's whole purpose is to hand back a table whose
#: KEYS are box or workset names, which is precisely what may not be tested with ``in``.
#: :func:`test_every_named_loader_still_exists` reds if one is renamed away.
#: ⚑ A PUBLIC WRAPPER IS A LOADER TOO.  ``list_worksets`` is a one-line re-export of
#: ``_load_registry``, and naming only the private one made this guard return zero hits
#: for every caller of the public one — five unfixed sites, invisible. Listing a loader
#: without its wrappers is the way this check goes quietly vacuous.
_LOADERS = frozenset({
    "load_primary_boxes",       # settings/paths.py       — PRIMARY box membership
    "load_workset_boxes",       # project/workset_registry.py — a workset's boxes:
    "load_standalone",          # project/registry_store.py   — standalone: section
    "load_deregistered",        # project/registry_store.py   — deregistered: section
    "standalone_box_names",     # project/registry_store.py   — the same keys, as a set
    "_load_registry",           # project/workset.py          — worksets: as {name: root}
    "list_worksets",            # project/workset.py          — its PUBLIC wrapper
    "_primary_name_domain",     # settings/paths.py           — the PRIMARY name domain
})

#: Functions returning the WHOLE registry document, keyed by SECTION rather than by name.
#: A registry reaches a caller through one of these too — ``read_names(r)["worksets"]``
#: is the same table ``_load_registry`` returns.
_DOCUMENT_LOADERS = frozenset({"read_names", "load_registry", "_load"})

#: Sections of those documents whose keys are identifiers.  ``rigs``/``image_shells`` are
#: deliberately absent: they are not box or workset names and §0 does not reach them.
_NAME_SECTIONS = frozenset({"worksets", "standalone", "deregistered", "boxes"})

#: Variable spellings that hold a kanibako identifier.  Folding one by hand is the
#: retired entry fold, wherever it appears.
#:
#: ⚑ ``supplied`` and ``leaf`` are the standalone door's two: ``supplied`` is a whole
#: box name as the user typed it, and ``leaf`` is the case-CARRYING half of one
#: (``<kuid>_<leaf>``, the source directory's basename).  ``sanitize_cap`` folded that
#: leaf until Phase 2, which is the retired cure by another variable name.
_IDENTIFIER_VARS = frozenset({
    "name", "value", "target", "ws_name", "box_name", "proj_name",
    "cand", "candidate", "agent_name", "workset_name", "supplied", "leaf",
})

_FOLDS = frozenset({"lower", "casefold"})

# ⚑ THE ENTRY FOLD IS GONE, AND SO IS ITS DECLARATION.  Both guards below are now
# ABSOLUTE: outside the carrier the permitted population is ZERO, with no inventory to
# keep and nothing to renumber.  ``_ENTRY_FOLD`` was a ``{path: count}`` map of the
# three files holding the retired ``[R171]`` cure, asserted EQUAL in BOTH directions
# precisely so that removing those folds would red and take the declaration with it.
# That is what happened.  🛑 Do not reintroduce it, or anything shaped like it, to let
# a new fold pass — an inventory here is an exemption list wearing a pin's clothes.


def _scan_roots() -> list[Path]:
    """The shipped source trees: the core package plus each plugin's package."""
    roots = [REPO_ROOT / "src" / "kanibako"]
    roots.extend(sorted((REPO_ROOT / "packages").glob("*/src/kanibako")))
    return roots


def _shipped() -> list[tuple[str, Path]]:
    """``(repo-relative path, path)`` for every shipped ``.py``, build copies excluded."""
    out: list[tuple[str, Path]] = []
    for root in _scan_roots():
        for py in sorted(root.rglob("*.py")):
            if any(part in ("build", ".claude") for part in py.parts):
                continue
            out.append((py.relative_to(REPO_ROOT).as_posix(), py))
    return out


def _called(node: ast.AST) -> str | None:
    """The bare function name a ``Call`` names, attribute access resolved."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _literal(node: ast.AST) -> str | None:
    """The value of a string literal, or ``None``."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _scopes(tree: ast.Module) -> list[ast.AST]:
    """Module scope plus every function body — where a local binding is meaningful."""
    return [tree] + [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def membership_tests(tree: ast.Module) -> list[int]:
    """Lines holding an ``in``/``not in`` test against an identifier registry.

    A registry reaches the test three ways, and all three are resolved: a direct loader
    call, a local bound from one, and a name-section subscript of a registry DOCUMENT
    (``read_names(r)["worksets"]``).  Bindings are read per scope, so a local in one
    function cannot make an unrelated variable elsewhere look like a registry.
    """
    hits: list[int] = []
    for scope in _scopes(tree):
        registries: set[str] = set()
        documents: set[str] = set()
        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target, called = node.targets[0], _called(node.value)
            bound = (
                [target] if isinstance(target, ast.Name)
                else list(target.elts) if isinstance(target, ast.Tuple) else []
            )
            for element in bound:
                if not isinstance(element, ast.Name):
                    continue
                if called in _LOADERS or _is_section(node.value, documents):
                    registries.add(element.id)
                elif called in _DOCUMENT_LOADERS:
                    documents.add(element.id)

        def is_registry(expr: ast.AST) -> bool:
            if _called(expr) in _LOADERS:
                return True
            if isinstance(expr, ast.Name) and expr.id in registries:
                return True
            return _is_section(expr, documents)

        for node in ast.walk(scope):
            if not isinstance(node, ast.Compare):
                continue
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and is_registry(comparator):
                    hits.append(node.lineno)
    return sorted(set(hits))


def _is_section(expr: ast.AST, documents: set[str]) -> bool:
    """Is *expr* ``<registry document>["<name section>"]``?"""
    if not isinstance(expr, ast.Subscript):
        return False
    if _literal(expr.slice) not in _NAME_SECTIONS:
        return False
    return _called(expr.value) in _DOCUMENT_LOADERS or (
        isinstance(expr.value, ast.Name) and expr.value.id in documents
    )


def _receiver_leaves(expr: ast.AST) -> list[str]:
    """Every name a fold's RECEIVER could be called, defaults and conditionals unwrapped.

    ``name.lower()`` and ``args.name.lower()`` are the easy shapes — the guard reads the
    last segment of an attribute chain, because what a value is CALLED is what the next
    reader goes by.  The shapes that hid one are the wrappers: ``(spec.name or "")`` is a
    ``BoolOp`` and ``(a if p else b)`` an ``IfExp``, and reading only the outermost node
    saw neither.  Both are descended, so a default-valued identifier is still one.
    """
    if isinstance(expr, ast.Name):
        return [expr.id]
    if isinstance(expr, ast.Attribute):
        return [expr.attr]
    if isinstance(expr, ast.BoolOp):
        return [leaf for value in expr.values for leaf in _receiver_leaves(value)]
    if isinstance(expr, ast.IfExp):
        return _receiver_leaves(expr.body) + _receiver_leaves(expr.orelse)
    if isinstance(expr, ast.Call):
        # ``_SAFE_CHAR_RE.sub("_", leaf).lower()`` -- the receiver is a CALL, and reading
        # only Name/Attribute/BoolOp/IfExp saw nothing.  That is the shape ``sanitize_cap``
        # held, so ``leaf`` in _IDENTIFIER_VARS pinned nothing until this branch existed.
        return [leaf for arg in expr.args for leaf in _receiver_leaves(arg)]
    return []


def hand_folds(tree: ast.Module) -> list[int]:
    """Lines applying ``.lower()``/``.casefold()`` to an identifier-named variable."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _FOLDS:
            continue
        if any(leaf in _IDENTIFIER_VARS for leaf in _receiver_leaves(node.func.value)):
            hits.append(node.lineno)
    return sorted(set(hits))


@cache
def _findings() -> dict[str, dict[str, list[int]]]:
    """``{repo-relative path: {"in": [lines], "fold": [lines]}}`` over shipped source."""
    found: dict[str, dict[str, list[int]]] = {}
    for rel, path in _shipped():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        tests, folds = membership_tests(tree), hand_folds(tree)
        if tests or folds:
            found[rel] = {"in": tests, "fold": folds}
    return found


def _cite(rel: str, kind: str) -> str:
    return f"{rel}:{','.join(str(n) for n in _findings()[rel][kind])}"


class TestTheScan:
    """A guard that scans nothing reports clean (P15)."""

    def test_every_scan_root_exists(self):
        missing = [str(r) for r in _scan_roots() if not r.is_dir()]
        assert not missing, f"scan root(s) gone — fix _scan_roots(): {missing}"
        assert len(_scan_roots()) >= 2, _scan_roots()

    def test_the_corpus_is_not_vacuous(self):
        rels = [rel for rel, _ in _shipped()]
        assert len(rels) > 100, len(rels)
        assert _CARRIER in rels
        assert any(rel.startswith("packages/") for rel in rels)

    def test_every_named_loader_still_exists(self):
        """A renamed loader would silently empty this guard's subject."""
        defined: set[str] = set()
        for _, path in _shipped():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            defined.update(
                node.name for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
        gone = sorted((_LOADERS | _DOCUMENT_LOADERS) - defined)
        assert not gone, (
            f"named registry loader(s) no longer defined anywhere in shipped source: "
            f"{gone} — they were renamed or removed, and this guard now covers less "
            f"than it claims. Re-derive _LOADERS/_DOCUMENT_LOADERS."
        )

    def test_the_seam_is_where_the_carrier_says_it_is(self):
        carrier = REPO_ROOT / _CARRIER
        assert carrier.is_file(), carrier
        assert f"def {_SEAM}(" in carrier.read_text(encoding="utf-8"), (
            f"{_CARRIER} no longer defines {_SEAM}; the comparison seam moved"
        )

    def test_the_membership_detector_reds_on_all_three_shapes(self):
        """Synthetic source, so this holds whatever the real tree looks like.

        A detector never seen to fire is not evidence.  Each arm is the shape one of
        the fixed sites actually had.
        """
        source = (
            "def f(std, name, registry):\n"
            "    if name in load_primary_boxes(std):\n"          # direct call
            "        pass\n"
            "    boxes = load_standalone(registry)\n"
            "    if name in boxes:\n"                            # local binding
            "        pass\n"
            "    doc = read_names(registry)\n"
            "    if name in doc['worksets']:\n"                  # section subscript
            "        pass\n"
            "    if name in read_names(registry)['worksets']:\n"  # both at once
            "        pass\n"
        )
        assert membership_tests(ast.parse(source)) == [2, 5, 8, 10]

    def test_the_membership_detector_accepts_the_cured_shape(self):
        """The complement: routed through the seam, and an unrelated ``in``."""
        source = (
            "def f(std, name, registry, allowed):\n"
            "    if find_identifier(name, load_primary_boxes(std)) is not None:\n"
            "        pass\n"
            "    boxes = load_standalone(registry)\n"
            "    stored = find_identifier(name, boxes)\n"
            "    if stored is not None:\n"
            "        return boxes[stored]\n"
            "    if name in allowed:\n"  # not a registry: an ordinary container
            "        pass\n"
        )
        assert membership_tests(ast.parse(source)) == []

    def test_the_fold_detector_reds_on_a_bare_name_and_an_attribute_chain(self):
        source = (
            "def f(args, name):\n"
            "    a = name.lower()\n"
            "    b = args.name.lower()\n"
            "    c = ws_name.casefold()\n"
            "    d = self.path.lower()\n"      # not an identifier variable
            "    e = 'LITERAL'.lower()\n"      # not a variable at all
            "    return a, b, c, d, e\n"
        )
        assert hand_folds(ast.parse(source)) == [2, 3, 4]

    def test_the_fold_detector_sees_through_a_DEFAULT_and_a_CONDITIONAL(self):
        """The shape that hid a live fold: a receiver wrapped in ``or`` / ``if else``.

        ``(spec.name or "").lower()`` is a ``BoolOp``, not a ``Name`` or ``Attribute``,
        and reading only the outermost node missed it entirely — which is how
        ``_lifecycle.py`` came to hold two folds while this guard counted one.
        """
        source = (
            "def f(spec, name, other):\n"
            '    a = (spec.name or "").lower()\n'
            '    b = (name if name else "x").casefold()\n'
            '    c = (other.path or "").lower()\n'   # still not an identifier
            "    return a, b, c\n"
        )
        assert hand_folds(ast.parse(source)) == [2, 3]


class TestNobodyMembershipTestsARegistry:
    """``name in registry`` is the exact-match compare ``find_identifier`` replaces."""

    def test_no_direct_membership_test_against_an_identifier_registry(self):
        offenders = sorted(rel for rel, hits in _findings().items() if hits["in"])
        assert not offenders, (
            "an identifier registry is membership-tested directly:\n  "
            + "\n  ".join(_cite(rel, "in") for rel in offenders)
            + f"\n\nBox and workset names compare WITHOUT REGARD TO CASE (spec §0, "
            f"⚑ NAMING RULES). Route the lookup through "
            f"`kanibako.identifiers.{_SEAM}`, which hands back the STORED spelling — "
            f"index with THAT, never with the name as typed. There is no allowlist "
            f"here and adding one would retire the rule."
        )


class TestNobodyFoldsAnIdentifierByHand:
    """``identifiers._fold`` is private so that no caller can fold one HALF of a compare."""

    def test_no_hand_fold_of_an_identifier_outside_the_carrier(self):
        offenders = sorted(
            rel for rel, hits in _findings().items()
            if hits["fold"] and rel != _CARRIER
        )
        assert not offenders, (
            "an identifier is folded by hand:\n  "
            + "\n  ".join(_cite(rel, "fold") for rel in offenders)
            + "\n\nFOLD TO COMPARE, NEVER TO STORE (spec §0). A comparison belongs in "
            f"`kanibako.identifiers.{_SEAM}`; a fold anywhere else is either the "
            "retired [R171] entry fold or a half-folded lookup. If the variable is NOT "
            "a kanibako identifier, RENAME IT rather than exempting it — "
            "`proxy/server.py` calls its HTTP field names `header` for this reason."
        )

    def test_the_carrier_folds(self):
        """Anti-vacuity: the detector is not merely finding nothing anywhere."""
        assert _findings().get(_CARRIER, {}).get("fold"), (
            f"{_CARRIER} does not fold at all — the detector is broken, or the "
            f"comparison moved out of its carrier"
        )

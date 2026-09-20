"""ENFORCEMENT guardrail: identifier comparison folds, and only ONE place folds it.

``specs/settings-keyspace-1.8.0.md`` §0, the ``⚑ NAMING RULES`` bullet, is two rules:
**fold to compare, never fold to store**, and **never fold a filesystem path**.  Prose
cannot hold either one — the tree has already been through a cure that folded on ENTRY
(``[R171]``, retired) and through a half-folded lookup that folded the QUERY and not the
stored key.  Both look correct in a diff.

So the rule is asserted syntactically, in three directions:

* **Nobody membership-tests an identifier registry directly.**  ``name in registry`` is
  the exact-match comparison ``find_identifier`` exists to replace, and it is the shape
  every one of the fixed sites had.  The allowed population is ZERO — there is no
  allowlist here and there must not be one.
* **Nobody folds an identifier by hand.**  ``identifiers._fold`` is private precisely so
  that no caller can fold one half of a comparison; a ``.lower()`` on a variable named
  like an identifier is that private fold, re-spelled.
* **Nobody composes an agent NODE out of a declared NAME.**  An agent is the one kind
  with two spellings (``[R173]``): the name keeps its plugin's case, the node is that
  name lowercased.  ``with_harness(node, target.name)`` builds a node segment out of
  the wrong one, and no amount of folding on the REGISTRY side reaches it — the value
  comes off the class, not off the key.

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

#: The node COMPOSER, and the attribute that holds a plugin's DECLARED NAME.  An agent
#: has two spellings (``[R173]``): the name keeps the plugin's case, the node is that
#: name lowercased.  Composing a node out of the first is the third way this rule is
#: broken, and the one a registry-side fix does not reach.
_COMPOSER = "with_harness"
_DECLARED_NAME_ATTRS = frozenset({"name"})

#: The derivation seam — the only sanctioned way to turn a NAME into a NODE.
_NODE_SEAM = "agent_node_case"

#: The REF parser.  What it returns is a canonical ref, **not a node**: it normalises
#: the separator and validates the charset, and folds NOTHING.  A ref arriving from
#: outside the process — a ``KANIBAKO_AGENT`` container stamp, a settings VALUE — has
#: therefore still to pass :data:`_NODE_SEAM` before it may spell a store path.
_REF_PARSER = "canonicalize_agent_ref"

#: Composers that turn a node into a STORE PATH.  Handed a ref that never folded, they
#: name a directory the launch does not write.
_STORE_PATH_COMPOSERS = frozenset({"agent_settings_path"})

#: Identifier variables with NO node/name split — a box or a workset name, where the
#: typed case IS the stored case (``[R172]``; ``[R173]`` explicitly does not reach
#: them).  Deriving an agent NODE from one of these is the retired entry fold wearing
#: the new seam's name, and the ``.lower()`` detector below cannot see it.
_NODELESS_IDENTIFIER_VARS = frozenset({
    "box_name", "ws_name", "workset_name", "proj_name", "supplied", "leaf",
})

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


def _harness_arg(call: ast.Call) -> ast.AST | None:
    """``with_harness``'s SECOND argument, positional or by keyword.

    ⚑ The keyword form is not hypothetical politeness: ``with_harness(a,
    harness=target.name)`` is the same composition and reading ``args[1]`` alone
    returned ``None`` for it, so the rule held for one spelling of its own subject.
    """
    if len(call.args) >= 2:
        return call.args[1]
    for kw in call.keywords:
        if kw.arg == "harness":
            return kw.value
    return None


def _is_declared_name(expr: ast.AST) -> bool:
    """Is *expr* a plugin's DECLARED NAME — ``<anything>.name``, wrappers unwrapped?

    A call to the node seam is the CURED shape and stops the walk: whatever
    ``agent_node_case(...)`` was handed, what comes back is a node.  Everything else
    is descended exactly as :func:`_receiver_leaves` descends a fold's receiver, so a
    default (``spec.name or ""``) and a conditional are still declared names.

    🛑 A bare ``ast.Name`` is deliberately NOT a hit.  ``with_harness(node, found)``
    and ``with_harness(node, agent_real_name)`` pass values whose PROVENANCE this
    function cannot see; the hoisted-local arm of :func:`unfolded_node_derivations`
    is what reads provenance, and it reads it per scope.
    """
    if isinstance(expr, ast.Call):
        if _called(expr) == _NODE_SEAM:
            return False
        return any(_is_declared_name(arg) for arg in expr.args)
    if isinstance(expr, ast.Attribute):
        return expr.attr in _DECLARED_NAME_ATTRS
    if isinstance(expr, ast.BoolOp):
        return any(_is_declared_name(value) for value in expr.values)
    if isinstance(expr, ast.IfExp):
        return _is_declared_name(expr.body) or _is_declared_name(expr.orelse)
    return False


def unfolded_node_derivations(tree: ast.Module) -> list[int]:
    """Lines building a node's HARNESS segment out of an unfolded declared name.

    ``with_harness(node, <harness>)`` composes a NODE, and every segment of a node is
    lowercase (``[R173]``, keyspec §0).  ``target.name`` is the declared NAME, which
    keeps the plugin's own case — so handing one straight to ``with_harness`` spells
    ``agents/Shell/`` and ``agent.Shell.*`` from a value that was never a node.

    ⚑ **Folding the registry key is not enough, which is why this is a separate rule
    from the two above.**  The sites ``[R176]`` measured read ``Target.name``, the class
    property — never the key the registry filed the class under — so a registry keyed by
    node still wrote the declared case at launch.  One more lived in
    ``commands/box/_parser.py`` and no measurement had named it; this is what finds the
    next one.

    Three shapes reach the composer and all three are read: the argument itself, the
    argument by KEYWORD, and a LOCAL hoisted out of one earlier in the same scope.
    🛑 **What is NOT read is where the value came from across a call boundary.**  A
    parameter is opaque here by design — ``settings_launch.meta_identity_floor``
    composes the ``meta.agent.<a>.name`` VALUE, which is a NAME and must keep its
    case, out of a parameter this guard cannot and should not second-guess.  The
    declaration at that call site is what carries the distinction.
    """
    hits: list[int] = []
    for scope in _scopes(tree):
        hoisted: set[str] = set()
        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and _is_declared_name(node.value):
                hoisted.add(target.id)

        for node in ast.walk(scope):
            if not isinstance(node, ast.Call) or _called(node) != _COMPOSER:
                continue
            harness = _harness_arg(node)
            if harness is None:
                continue
            if _is_declared_name(harness) or (
                isinstance(harness, ast.Name) and harness.id in hoisted
            ):
                hits.append(node.lineno)
    return sorted(set(hits))


def unfolded_stamp_derivations(tree: ast.Module) -> list[int]:
    """Lines spelling a STORE PATH from a ref that was parsed but never folded.

    ``canonicalize_agent_ref`` normalises the separator and validates the charset.  It
    folds NOTHING — so a ``KANIBAKO_AGENT`` stamp or a settings value that reaches
    ``agent_settings_path`` through it alone names ``agents/Kirobo/`` while the launch
    writes ``agents/kirobo/``.  ``[R173]``: *any lookup that takes a user-supplied or
    value-supplied agent spelling and reaches for a node folds at that hop.*

    ⚑ This is the rule ``with_harness`` does not reach, because these sites compose no
    node at all — they hand the ref straight to the path.  Both live under a blanket
    ``except``, so the failure is silent: credential writeback simply stops.

    🛑 **Its declared limit: ONE hop, within ONE scope.**  A local bound directly from
    the parser is tracked; a local bound from THAT local is not, and neither is a
    value that crosses a call.  Widening it further wants a declaration rather than a
    syntax rule (``[R160]``), and a guard that claimed the wider rule while checking
    the narrow one would be worse than one that says which it checks.
    """
    hits: list[int] = []
    for scope in _scopes(tree):
        unfolded: set[str] = set()
        assigns = sorted(
            (n for n in ast.walk(scope) if isinstance(n, ast.Assign)),
            key=lambda n: n.lineno,
        )
        for node in assigns:
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            calls = {
                _called(sub) for sub in ast.walk(node.value)
                if isinstance(sub, ast.Call)
            }
            if _REF_PARSER in calls and _NODE_SEAM not in calls:
                unfolded.add(target.id)
            elif _NODE_SEAM in calls:
                unfolded.discard(target.id)

        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            if _called(node) not in _STORE_PATH_COMPOSERS:
                continue
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Name) and arg.id in unfolded:
                    hits.append(node.lineno)
    return sorted(set(hits))


def hand_folds(tree: ast.Module) -> list[int]:
    """Lines folding an identifier by hand — a ``.lower()``/``.casefold()`` call, or the SEAM.

    ⚑ **The seam counts, and leaving it out was a hole the ``[R173]`` work opened.**
    ``agent_node_case`` is public now, so ``box_name = agent_node_case(box_name)``
    folds an identifier to store with no attribute call anywhere in it — the exact
    cure ``[R172]`` retired, passing every guard because the detector read only
    ``.lower()`` and ``.casefold()``.

    🛑 The seam arm is narrower than the attribute arm ON PURPOSE, and not as a
    concession: deriving a node from an AGENT name is what the seam is FOR, so
    ``agent_node_case(target.name)`` is the cured shape and must stay clean.  What
    cannot be cured is a box or a workset name (:data:`_NODELESS_IDENTIFIER_VARS`) —
    those have no second spelling to derive (``[R172]``, and ``[R173]`` says it does
    not reach them), so the call can only be a fold on the way to storage.
    """
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _called(node) == _NODE_SEAM:
            if any(
                leaf in _NODELESS_IDENTIFIER_VARS
                for arg in node.args for leaf in _receiver_leaves(arg)
            ):
                hits.append(node.lineno)
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _FOLDS:
            continue
        if any(leaf in _IDENTIFIER_VARS for leaf in _receiver_leaves(node.func.value)):
            hits.append(node.lineno)
    return sorted(set(hits))


@cache
def _findings() -> dict[str, dict[str, list[int]]]:
    """``{repo-relative path: {"in"/"fold"/"node"/"stamp": [lines]}}`` over shipped source."""
    found: dict[str, dict[str, list[int]]] = {}
    for rel, path in _shipped():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        tests, folds = membership_tests(tree), hand_folds(tree)
        nodes = unfolded_node_derivations(tree)
        stamps = unfolded_stamp_derivations(tree)
        if tests or folds or nodes or stamps:
            found[rel] = {
                "in": tests, "fold": folds, "node": nodes, "stamp": stamps,
            }
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


class TestNobodyComposesANodeFromADeclaredName:
    """A node's segments are lowercase; ``Target.name`` is not (``[R173]``)."""

    def test_no_with_harness_call_takes_an_unfolded_declared_name(self):
        offenders = sorted(rel for rel, hits in _findings().items() if hits["node"])
        assert not offenders, (
            "a node's harness segment is composed from a DECLARED NAME:\n  "
            + "\n  ".join(_cite(rel, "node") for rel in offenders)
            + f"\n\nAn agent's NAME keeps its plugin's case; its NODE — the "
            f"`agent.<node>.*` slot and the `agents/<node>/` store spelled from it — "
            f"is that name in lowercase (spec §0, ⚑ NAMING RULES). Derive it through "
            f"`kanibako.identifiers.{_NODE_SEAM}`. There is no allowlist here: "
            f"folding the plugin registry's KEY does not reach these sites, which is "
            f"the whole reason this rule is separate."
        )

    def test_the_detector_reds_on_the_shape_it_is_named_for(self):
        """Synthetic, so it holds whatever the real tree looks like."""
        source = (
            "def f(agent_name, target, other):\n"
            "    a = with_harness(agent_name, target.name)\n"
            "    b = with_harness(agent_name, agent_node_case(target.name))\n"
            "    c = with_harness(agent_name, found)\n"
            "    d = with_harness(agent_name, other.harness)\n"
            "    return a, b, c, d\n"
        )
        assert unfolded_node_derivations(ast.parse(source)) == [2]

    def test_the_detector_reds_on_the_KEYWORD_and_the_HOISTED_shapes(self):
        """The two spellings of its own subject that read ``args[1]`` alone missed.

        A keyword argument is the same composition, and a hoisted local is the
        one-line edit that silences a positional hit without changing a thing about
        what is written to disk.
        """
        source = (
            "def f(agent_name, target):\n"
            "    a = with_harness(agent_name, harness=target.name)\n"
            "    declared = target.name\n"
            "    b = with_harness(agent_name, declared)\n"
            '    c = with_harness(agent_name, (target.name or ""))\n'
            "    node = agent_node_case(target.name)\n"
            "    d = with_harness(agent_name, node)\n"
            "    return a, b, c, d\n"
        )
        assert unfolded_node_derivations(ast.parse(source)) == [2, 4, 5]

    def test_the_stamp_detector_reds_on_a_parsed_but_unfolded_ref(self):
        """``canonicalize_agent_ref`` validates a ref; it does not fold one."""
        source = (
            "def f(std, stamp):\n"
            "    agent = canonicalize_agent_ref(stamp)\n"
            "    return agent_settings_path(std.agents, agent)\n"
        )
        assert unfolded_stamp_derivations(ast.parse(source)) == [3]

    def test_the_stamp_detector_accepts_the_cured_shape(self):
        """Folded at the hop, in either spelling of the cure."""
        one_statement = (
            "def f(std, stamp):\n"
            "    ref = canonicalize_agent_ref(stamp)\n"
            "    agent = with_harness(ref, agent_node_case(harness_of(ref)))\n"
            "    return agent_settings_path(std.agents, agent)\n"
        )
        assert unfolded_stamp_derivations(ast.parse(one_statement)) == []
        rebound = (
            "def f(std, stamp):\n"
            "    agent = canonicalize_agent_ref(stamp)\n"
            "    agent = agent_node_case(agent)\n"
            "    return agent_settings_path(std.agents, agent)\n"
        )
        assert unfolded_stamp_derivations(ast.parse(rebound)) == []

    def test_the_seam_fold_detector_separates_an_agent_from_a_box(self):
        """The seam is a DERIVATION for an agent and a FOLD for anything else."""
        source = (
            "def f(target, box_name, ws_name, agent_name):\n"
            "    a = agent_node_case(target.name)\n"      # the cured shape
            "    b = agent_node_case(agent_name)\n"       # an agent: still a derivation
            "    c = agent_node_case(box_name)\n"         # no node to derive
            "    d = agent_node_case(ws_name)\n"          # no node to derive
            "    return a, b, c, d\n"
        )
        assert hand_folds(ast.parse(source)) == [4, 5]

    def test_no_store_path_is_spelled_from_an_unfolded_stamp(self):
        offenders = sorted(rel for rel, hits in _findings().items() if hits["stamp"])
        assert not offenders, (
            "a store path is spelled from a ref that was parsed but never folded:\n  "
            + "\n  ".join(_cite(rel, "stamp") for rel in offenders)
            + f"\n\n`{_REF_PARSER}` normalises a ref's separator and validates its "
            f"charset; it folds NOTHING. A `KANIBAKO_AGENT` stamp or a settings "
            f"VALUE reaching a store path through it alone names `agents/Kirobo/` "
            f"while the launch writes `agents/kirobo/` (spec §0, ⚑ NAMING RULES; "
            f"[R173]: any lookup that takes a value-supplied agent spelling and "
            f"reaches for a node folds at that hop). Fold the HARNESS segment "
            f"through `kanibako.identifiers.{_NODE_SEAM}`, which is what the launch "
            f"itself does — folding the whole ref would move a capitalised PERSONA's "
            f"store, which the launch does not."
        )

    def test_the_ref_parser_and_the_path_composers_still_exist(self):
        """A rename on any of them would empty the stamp rule without failing it."""
        defined: set[str] = set()
        for _, path in _shipped():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            defined.update(
                node.name for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
        gone = sorted(({_REF_PARSER} | _STORE_PATH_COMPOSERS) - defined)
        assert not gone, (
            f"no longer defined anywhere in shipped source: {gone} — the stamp rule "
            f"now covers less than it claims. Re-derive _REF_PARSER / "
            f"_STORE_PATH_COMPOSERS."
        )

    def test_the_composer_and_the_seam_both_still_exist(self):
        """A rename on either side would empty this guard without failing it."""
        agent_ref = (REPO_ROOT / "src" / "kanibako" / "agent_ref.py").read_text(
            encoding="utf-8"
        )
        assert f"def {_COMPOSER}(" in agent_ref, (
            f"agent_ref.py no longer defines {_COMPOSER}; the node composer moved"
        )
        carrier = (REPO_ROOT / _CARRIER).read_text(encoding="utf-8")
        assert f"def {_NODE_SEAM}(" in carrier, (
            f"{_CARRIER} no longer defines {_NODE_SEAM}; the node derivation moved"
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

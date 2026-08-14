"""The agent-file BOUNDARY pins: nothing outside ``settings/agent_file.py`` spells the file.

Three pins, each guarding a different way the boundary leaks (S1, rulings 49-52 —
*"There is no self:<agent>:foo"* / *"There's no need for our code to ever use self.
It was created exclusively for config files"*):

(a) the AST census — no module but the boundary carries the root spelling in a STRING;
(b) root-via-constant — the written document's root is :data:`agent_file.ROOT_SECTIONS`,
    asserted THROUGH the constant so a rename cannot pass by editing the test;
(c) route-carries-no-address — a per-node route hands back a SLOT, never a
    ``(path, sections, leaf)`` tuple, so no caller can hold a file address at all.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from kanibako.settings import agent_file
from kanibako.settings.agent_config import AgentConfig
from kanibako.settings.agent_file import AgentFileSlot

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The module that is ALLOWED to spell it — the boundary itself.
_BOUNDARY = _REPO_ROOT / "src" / "kanibako" / "settings" / "agent_file.py"

#: The root spelling as a WORD: not ``itself`` / ``myself`` (a ``\w`` before it),
#: not ``self.foo`` written as a Python attribute access (a ``.`` before it), and
#: not ``self-contained`` (a ``-`` after it).  A trailing ``.`` or ``:`` — the two
#: ways the file's root is actually written — DOES match.
_ROOT_WORD = re.compile(r"(?<![\w.])self(?![\w-])")

#: The ONE unrelated ``self`` that ships: procfs.  ``/proc/self/mountinfo`` is the
#: KERNEL's self-reference — a different vocabulary entirely, and naming it as an
#: exemption says so, where widening :data:`_ROOT_WORD` would have hidden the
#: distinction behind a regex nobody re-reads.
_PROCFS = "/proc/self"


def _source_files() -> list[Path]:
    """Every SHIPPED python file: the core package plus each plugin's own src tree."""
    files = sorted((_REPO_ROOT / "src").rglob("*.py"))
    for pkg in sorted((_REPO_ROOT / "packages").glob("*/src")):
        files.extend(sorted(pkg.rglob("*.py")))
    return [f for f in files if f != _BOUNDARY]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """The ``id()`` of every Constant that is a DOCSTRING, not a value."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


class TestNoRootSpellingOutsideTheBoundary:
    """(a) The CENSUS: the file's root table is a string in ONE module.

    ⚑ SCOPE, DELIBERATELY: STRING VALUES ONLY.  Comments and docstrings are OUT —
    they are prose ABOUT the shape, they cannot route a read or a write, and
    sweeping them would make the pin a style rule instead of a leak detector.  A
    drifted comment is a real defect, but it is not this one.
    """

    def test_census(self) -> None:
        offenders: list[str] = []
        for path in _source_files():
            tree = ast.parse(path.read_text(), filename=str(path))
            docstrings = _docstring_nodes(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant):
                    continue
                if not isinstance(node.value, str) or id(node) in docstrings:
                    continue
                if _PROCFS in node.value:
                    continue
                if _ROOT_WORD.search(node.value):
                    rel = path.relative_to(_REPO_ROOT)
                    offenders.append(f"{rel}:{node.lineno}: {node.value!r}")
        assert not offenders, (
            "The per-agent file's root table is spelled OUTSIDE "
            "kanibako/settings/agent_file.py:\n  " + "\n  ".join(offenders)
            + "\n`self` is a FILE-SURFACE alias, never internal traffic (rulings "
            "51/52) — route the site through agent_file (a slot, a level, or "
            "file_spelling()), never a literal."
        )


class TestRootViaConstant:
    """(b) A document WRITTEN through the boundary is rooted at the CONSTANT."""

    def test_save_roots_the_document_at_the_constant(self, tmp_path: Path) -> None:
        from kanibako.settings.config_io import load_doc

        path = tmp_path / "claude" / "settings.yaml"
        agent_file.save(path, AgentConfig(name="Claude", state={"model": "opus"}))
        # ⚑ THROUGH the constant, never a literal: a rename that misses a site must
        # fail HERE rather than be re-blessed by editing this line.
        assert set(load_doc(path)) == {agent_file.ROOT_SECTIONS[0]}
        assert agent_file.load(path).state == {"model": "opus"}

    def test_leaf_round_trip_through_the_slot(self, tmp_path: Path) -> None:
        from kanibako.settings.config_io import load_doc

        slot = agent_file.slot_for(tmp_path, "claude", "model")
        agent_file.write_leaf(slot, "sonnet")
        assert set(load_doc(slot.path)) == {agent_file.ROOT_SECTIONS[0]}
        assert agent_file.read_leaf(slot) == "sonnet"
        assert agent_file.remove_leaf(slot) is True
        assert agent_file.read_leaf(slot) is None

    def test_root_sections_is_one_segment(self) -> None:
        # The row ``settings_assemble._BEHAVIOR_TABLE_SHAPES`` consumes it as a
        # (prefix, depth=0) pair, so a second segment would silently mis-walk.
        assert len(agent_file.ROOT_SECTIONS) == 1


class TestRouteCarriesNoAddress:
    """(c) A per-node route is a SLOT: it cannot carry a file ADDRESS at all."""

    _CASES = (
        ("_persona_agent_target", "agent.claude.model"),
        ("_node_bind_target", "agent.claude.bindings.ro.share"),
        ("_node_secret_target", "agent.claude.secret_path.TOK"),
    )

    @pytest.mark.parametrize("fn_name,key", _CASES, ids=[c[0] for c in _CASES])
    def test_returns_a_slot_not_a_tuple(
        self, fn_name: str, key: str, tmp_path: Path
    ) -> None:
        from kanibako.settings import config_dest

        route = getattr(config_dest, fn_name)(key, tmp_path)
        assert isinstance(route, AgentFileSlot), (
            f"{fn_name} returned {type(route).__name__}; a per-node route is an "
            f"AgentFileSlot so no caller can hold a `self`-rooted address."
        )
        # ⚑ THE SAME-ARITY TRAP, PINNED: a NamedTuple would keep every unpacking
        # call site working AND keep this isinstance True.  A frozen dataclass is
        # what makes the old shape unavailable rather than merely discouraged.
        assert not isinstance(route, tuple)

    @pytest.mark.parametrize("attr", ("sections", "leaf"))
    def test_slot_has_no_address_attribute(self, attr: str, tmp_path: Path) -> None:
        slot = agent_file.slot_for(tmp_path, "claude", "env.FOO")
        assert not hasattr(slot, attr), (
            f"AgentFileSlot grew a {attr!r} attribute — the address is produced "
            f"INSIDE the boundary and is not part of the slot (P3/P4)."
        )

    def test_slot_is_frozen(self, tmp_path: Path) -> None:
        import dataclasses

        slot = agent_file.slot_for(tmp_path, "claude", "model")
        assert dataclasses.is_dataclass(slot)
        with pytest.raises(dataclasses.FrozenInstanceError):
            slot.tail = "access"  # type: ignore[misc]

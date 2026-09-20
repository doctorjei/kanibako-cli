"""The fold-to-compare carrier: ``kanibako.identifiers``.

Covers the helper's own contract and the structural promise its docstring makes —
that it stays a terminal leaf of the in-tree import graph.  The RULE it exists to
serve (every identifier lookup routed through it, and nobody folding by hand) is
pinned separately in ``tests/test_identifier_case_enforcement.py``.

Indent note: 4 spaces, matching every sibling under ``tests/`` (house style is 2).
"""

from __future__ import annotations

import re

from tests.support.repo import REPO_ROOT

from kanibako.identifiers import find_identifier


class TestFindIdentifier:
    def test_exact_match_returns_the_candidate(self):
        assert find_identifier("foo", ["bar", "foo"]) == "foo"

    def test_a_case_variant_matches_and_returns_the_STORED_spelling(self):
        """The whole point: compare case-blind, hand back the spelling on disk."""
        assert find_identifier("Foo", ["bar", "foo"]) == "foo"
        assert find_identifier("foo", ["bar", "FOO"]) == "FOO"
        assert find_identifier("FoO", {"fOo": "/x"}) == "fOo"

    def test_a_miss_is_None(self):
        assert find_identifier("foo", ["bar", "baz"]) is None
        assert find_identifier("foo", []) is None

    def test_a_dict_is_compared_by_its_KEYS(self):
        """The usual call passes a registry straight in."""
        assert find_identifier("Foo", {"foo": "/some/path"}) == "foo"
        assert find_identifier("/some/path", {"foo": "/some/path"}) is None

    def test_the_empty_string_is_a_legal_candidate_and_a_FALSY_return(self):
        """Why the documented contract says ``is not None``, never truthiness.

        A caller writing ``if find_identifier(...):`` would read a real match as a
        miss here — which is exactly the bug the docstring warns about, so it is
        asserted rather than described.
        """
        found = find_identifier("", [""])
        assert found == ""
        assert found is not None
        assert not found  # falsy, and still a hit

    def test_casefold_not_lower(self):
        """``casefold()`` is the wider fold, and agent segments admit non-ASCII.

        The German sharp s is the standard separator: ``"ß".lower()`` is ``"ß"``,
        while ``"ß".casefold()`` is ``"ss"``.  A ``lower()``-based helper would miss
        this pair, so the assertion discriminates the two implementations rather
        than merely exercising one.
        """
        assert "STRASSE".lower() != "straße".lower()
        assert find_identifier("STRASSE", ["straße"]) == "straße"

    def test_a_registry_holding_BOTH_spellings_resolves_rather_than_raising(self):
        """Documented contract: ambiguity is a STORAGE question, not this one's.

        Phase 1 changes nothing about what is stored, so a table that already holds
        ``Foo`` beside ``foo`` must keep resolving — by iteration order.  Refusing
        here would turn an existing registry into an unusable one.
        """
        assert find_identifier("FOO", {"Foo": 1, "foo": 2}) == "Foo"
        assert find_identifier("FOO", {"foo": 2, "Foo": 1}) == "foo"

    def test_candidates_may_be_a_one_shot_iterator(self):
        assert find_identifier("Foo", iter(["a", "foo", "b"])) == "foo"


class TestTheCarrierIsATerminalLeaf:
    """``identifiers.py`` imports nothing from the tree, and its docstring says why.

    ``settings/paths.py`` reaches it, and ``settings/paths.py`` →
    ``settings/agent_config.py`` → ``agent_ref`` is a live module-scope chain; an
    in-tree import here could close it.  ``test_import_graph.py`` asserts the whole
    graph is a DAG, but only for imports that RUN at module scope — a function-local
    import added here would be invisible to it and still make this module a hop in
    someone else's cycle.  This scans the TEXT, so indentation cannot hide one.
    """

    def test_no_in_tree_import_at_any_indentation(self):
        text = (REPO_ROOT / "src" / "kanibako" / "identifiers.py").read_text(
            encoding="utf-8"
        )
        offenders = [
            line.strip()
            for line in text.splitlines()
            if re.match(r"\s*(import|from)\s", line) and "kanibako" in line
        ]
        assert not offenders, (
            "kanibako/identifiers.py grew an in-tree import; it is the "
            f"comparison carrier and must stay a terminal leaf: {offenders}"
        )

    def test_the_scan_would_see_an_import_it_was_looking_for(self):
        """Anti-vacuity: the matcher above is not simply matching nothing."""
        sample = "def f():\n        from kanibako.settings.paths import StandardPaths\n"
        assert [
            line.strip()
            for line in sample.splitlines()
            if re.match(r"\s*(import|from)\s", line) and "kanibako" in line
        ] == ["from kanibako.settings.paths import StandardPaths"]

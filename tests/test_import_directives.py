"""Tests for the directive flattener (import-directives.py).

The flattener is a box-side data script, not an importable package module, so we
load it from its package-data path. It resolves Claude Code's documented ``@path``
memory-import syntax into a single flat file with fragment-reference sections.
"""

from __future__ import annotations

import importlib.resources
import importlib.util
import json

import pytest
from kanibako import data as _kani_data


def _load_flattener():
    root = importlib.resources.files(_kani_data.__name__)
    script = root.joinpath(
        "global", "base", "shared", "playbook", "kanibako", "scripts",
        "import-directives.py",
    )
    spec = importlib.util.spec_from_file_location("import_directives", str(script))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


flattener = _load_flattener()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake HOME so ~ expansion and home-relative slugs are deterministic."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _run(home, files: dict[str, str], source: str = "root.md") -> str:
    for rel, body in files.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    dest = home / "out.md"
    rc = flattener.flatten(str(home / source), str(dest))
    assert rc == 0
    return dest.read_text(encoding="utf-8")


class TestResolution:
    def test_basic_import_becomes_link_and_section(self, home):
        out = _run(home, {
            "root.md": "See @child.md here.",
            "child.md": "# Child\nbody",
        })
        assert "[child.md](#child_md)" in out
        assert "## child_md" in out
        assert "# Child\nbody" in out

    def test_relative_to_importing_file_not_cwd(self, home):
        # GENERAL imports rules/RULE.md; it must resolve next to GENERAL, not cwd.
        out = _run(home, {
            "root.md": "@sub/GENERAL.md",
            "sub/GENERAL.md": "@rules/RULE.md",
            "sub/rules/RULE.md": "rule body",
        })
        assert "rule body" in out
        assert "## sub_rules_RULE_md" in out

    def test_tilde_expands_to_home(self, home):
        out = _run(home, {
            "root.md": "@~/deep/FILE.md",
            "deep/FILE.md": "tilde body",
        })
        assert "tilde body" in out
        # link text keeps the as-written path; anchor is the lowercased slug;
        # the section heading preserves case.
        assert "[~/deep/FILE.md](#deep_file_md)" in out
        assert "## deep_FILE_md" in out

    def test_absolute_path(self, home):
        target = home / "abs.md"
        out = _run(home, {"root.md": f"@{target}", "abs.md": "abs body"})
        assert "abs body" in out

    def test_missing_file_left_literal(self, home):
        out = _run(home, {"root.md": "@nope.md stays literal"})
        assert "@nope.md stays literal" in out
        assert "## nope" not in out

    def test_trailing_punctuation_kept_outside_link(self, home):
        out = _run(home, {"root.md": "see @child.md.", "child.md": "x"})
        assert "[child.md](#child_md)." in out


class TestSkips:
    def test_code_span_not_imported(self, home):
        out = _run(home, {"root.md": "literal `@child.md` here", "child.md": "x"})
        assert "`@child.md`" in out
        assert "## child_md" not in out

    def test_fenced_block_not_imported(self, home):
        out = _run(home, {
            "root.md": "```\n@child.md\n```\n",
            "child.md": "x",
        })
        assert "## child_md" not in out

    def test_email_not_mistaken_for_import(self, home):
        out = _run(home, {"root.md": "mail user@example.com now"})
        assert "user@example.com" in out
        assert "## example" not in out


class TestDedupAndOrder:
    def test_diamond_imported_once(self, home):
        out = _run(home, {
            "root.md": "@a.md and @b.md",
            "a.md": "A pulls @shared.md",
            "b.md": "B pulls @shared.md",
            "shared.md": "shared once",
        })
        assert out.count("## shared_md") == 1
        assert out.count("shared once") == 1
        # both a and b point at the one shared section
        assert out.count("[shared.md](#shared_md)") == 2

    def test_cycle_terminates(self, home):
        out = _run(home, {
            "root.md": "@a.md",
            "a.md": "A @b.md",
            "b.md": "B @a.md",
        })
        assert out.count("## a_md") == 1
        assert out.count("## b_md") == 1

    def test_slug_collision_numbered(self, home):
        # ~/a/b.md and ~/a/b_md both normalise to a_b_md -> second gets a suffix.
        out = _run(home, {
            "root.md": "@a/b.md and @a/b_md",
            "a/b.md": "dotted",
            "a/b_md": "undated",
        })
        assert "## a_b_md" in out
        assert "## a_b_md_2" in out


class TestDepthCap:
    def test_fifth_hop_dropped_and_neutralised(self, home, capsys):
        out = _run(home, {
            "root.md": "@d1.md",
            "d1.md": "@d2.md",
            "d2.md": "@d3.md",
            "d3.md": "@d4.md",
            "d4.md": "@d5.md",
            "d5.md": "leaf",
        })
        # d1..d4 imported (hops 1-4); d5 (hop 5) dropped + neutralised.
        assert "## d4_md" in out
        assert "## d5_md" not in out
        assert "`@d5.md`" in out
        assert "leaf" not in out
        assert "depth>4" in capsys.readouterr().err


class TestOutputShape:
    def test_generated_header_and_source_missing(self, home):
        out = _run(home, {"root.md": "top @child.md", "child.md": "c"})
        assert out.startswith("<!-- GENERATED by kanibako")
        # source content is the preamble (no ## heading of its own)
        assert out.count("---\n## ") == 1

    def test_source_not_found_returns_2(self, home):
        rc = flattener.flatten(str(home / "absent.md"), str(home / "out.md"))
        assert rc == 2


class TestOutputModes:
    def _write_tree(self, home):
        (home / "root.md").write_text("top @child.md", encoding="utf-8")
        (home / "child.md").write_text("child body", encoding="utf-8")

    def test_additional_context_json_payload(self, home, capsys):
        """claude/codex delivery: emits a SessionStart additionalContext payload."""
        self._write_tree(home)
        rc = flattener.flatten(str(home / "root.md"), None, additional_context=True)
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        hso = payload["hookSpecificOutput"]
        assert hso["hookEventName"] == "SessionStart"
        ctx = hso["additionalContext"]
        assert "child body" in ctx
        assert "[child.md](#child_md)" in ctx
        assert ctx.startswith("<!-- GENERATED by kanibako")

    def test_dash_dest_writes_raw_to_stdout(self, home, capsys):
        self._write_tree(home)
        rc = flattener.flatten(str(home / "root.md"), "-")
        assert rc == 0
        out = capsys.readouterr().out
        assert "child body" in out
        assert out.startswith("<!-- GENERATED by kanibako")
        assert not (home / "-").exists()  # never wrote a literal "-" file

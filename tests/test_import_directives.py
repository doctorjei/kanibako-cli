"""Tests for the directive flattener (import-directives.py).

The flattener is a box-side data script, not an importable package module, so we
load it from its package-data path. It resolves Claude Code's documented ``@path``
memory-import syntax into a single flat file with fragment-reference sections.
"""

from __future__ import annotations

import importlib.resources
import importlib.util
import json
import os
import re

import pytest


def _load_flattener():
    # The flattener is MACHINERY, not canon content: it ships in ``kanibako.scripts``
    # (P-2) and reaches a box through the existing ``kani_pkg`` package bind, at
    # ``/opt/kanibako/kanibako/scripts/import-directives.py``. Its filename is not a
    # Python identifier, so it is loaded from its package-data path, never imported.
    script = importlib.resources.files("kanibako.scripts").joinpath(
        "import-directives.py"
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


# The generated header is a single HTML comment at the very start of the output,
# terminated by ``-->`` and a newline. That SHAPE is the contract; the wording
# inside it is not, so both helpers below key off the terminator alone.
_COMMENT_END = "-->"
_HEADER_END = _COMMENT_END + "\n"


def _body(out: str) -> str:
    """The flattened document past the leading generated header comment.

    ⚑ The header's WORDING is the author's and tests must not pin it — only its
    SHAPE (a leading HTML comment ending ``-->``) is contractual. Splitting on
    the terminator rather than on a phrase inside the comment is what lets the
    text be reworded, or the comment grow back to several lines, without
    touching a single assertion in this file.
    """
    head, sep, body = out.partition(_HEADER_END)
    assert sep and head.startswith("<!--"), f"no generated header: {out[:80]!r}"
    return body


def _has_generated_header(out: str) -> bool:
    """Whether *out* is marked as generated — SHAPE ONLY, never the prose.

    The contract is "the artifact opens with an HTML comment", so that is what
    this measures: a leading ``<!--`` whose first line closes it.
    """
    return out.startswith("<!--") and out.split("\n", 1)[0].endswith(_COMMENT_END)


class TestResolution:
    def test_basic_import_becomes_link_and_section(self, home):
        out = _run(home, {
            "root.md": "See @child.md here.",
            "child.md": "# Child\nbody",
        })
        assert "[child.md](#child_md)" in out
        assert "# Child\nbody" in out
        # no generated section header -- the file's own headings carry it
        assert "## child_md" not in out

    def test_relative_to_importing_file_not_cwd(self, home):
        # GENERAL imports rules/RULE.md; it must resolve next to GENERAL, not cwd.
        out = _run(home, {
            "root.md": "@sub/GENERAL.md",
            "sub/GENERAL.md": "@rules/RULE.md",
            "sub/rules/RULE.md": "rule body",
        })
        assert "rule body" in out
        assert "## sub_rules_RULE_md" not in out

    def test_tilde_expands_to_home(self, home):
        out = _run(home, {
            "root.md": "pull @~/deep/FILE.md now",
            "deep/FILE.md": "tilde body",
        })
        assert "tilde body" in out
        # link text keeps the as-written path; the anchor is the lowercased slug.
        assert "[~/deep/FILE.md](#deep_file_md)" in out
        assert "## deep_FILE_md" not in out

    def test_absolute_path(self, home):
        target = home / "abs.md"
        out = _run(home, {"root.md": f"@{target}", "abs.md": "abs body"})
        assert "abs body" in out

    def test_missing_file_neutralised(self, home):
        out = _run(home, {"root.md": "@nope.md stays inert"})
        # A missing target is neutralised to an inert backticked form, not left as
        # a raw live import, and produces no section.
        assert "`@nope.md`" in out
        assert "## nope" not in out
        # No live import survives: re-flattening the output changes nothing for it
        # (idempotent -- the backticked mention is skipped as a code span).
        (home / "out2src.md").write_text(out, encoding="utf-8")
        dest2 = home / "out2.md"
        assert flattener.flatten(str(home / "out2src.md"), str(dest2)) == 0
        out2 = dest2.read_text(encoding="utf-8")
        assert "`@nope.md`" in out2
        assert "## nope" not in out2

    def test_missing_file_trailing_punct_outside_ticks(self, home):
        out = _run(home, {"root.md": "see @nope.md."})
        assert "`@nope.md`." in out

    def test_missing_file_warns_on_stderr(self, home, capsys):
        """⚑ Neutralizing the mention makes the failure INVISIBLE in the artifact —
        the flat file simply lacks the content — and the launch shim's ``|| true``
        swallows the exit status. The stderr warning is the ONLY signal that an
        import went nowhere, which is exactly the silent-degradation shape the
        kickoff loader's transition window depends on being visible."""
        (home / "root.md").write_text("@nope.md stays inert\n", encoding="utf-8")
        rc = flattener.flatten(str(home / "root.md"), str(home / "out.md"))
        assert rc == 0
        err = capsys.readouterr().err
        assert "unresolved import @nope.md" in err
        assert "root.md" in err

    def test_missing_file_warning_names_the_importing_file(self, home, capsys):
        """The warning names the file that CONTAINED the dead import, not the entry
        point — otherwise a deep chain gives no clue where to look."""
        (home / "root.md").write_text("@guide.md\n", encoding="utf-8")
        (home / "guide.md").write_text("see @gone.md\n", encoding="utf-8")
        assert flattener.flatten(str(home / "root.md"), str(home / "out.md")) == 0
        err = capsys.readouterr().err
        assert "unresolved import @gone.md" in err
        assert "guide.md" in err

    def test_resolvable_imports_do_not_warn(self, home, capsys):
        (home / "root.md").write_text("@child.md\n", encoding="utf-8")
        (home / "child.md").write_text("body\n", encoding="utf-8")
        assert flattener.flatten(str(home / "root.md"), str(home / "out.md")) == 0
        assert "unresolved import" not in capsys.readouterr().err

    def test_code_span_mention_does_not_warn(self, home, capsys):
        """A backticked path is literal text, never an import — so it must not warn
        (this is also what keeps flattening IDEMPOTENT: a second pass over an output
        full of neutralized mentions must stay silent)."""
        (home / "root.md").write_text("literal `@nope.md` here\n", encoding="utf-8")
        assert flattener.flatten(str(home / "root.md"), str(home / "out.md")) == 0
        assert "unresolved import" not in capsys.readouterr().err

    def test_unresolvable_nested_in_resolvable_chain(self, home):
        # The real-world case (a resolvable guide referencing a not-yet-created
        # sibling, e.g. KANIBAKO.md -> @CONTENTS.md): the chain still resolves; the
        # missing mention neutralizes INLINE and never becomes a phantom section.
        out = _run(home, {
            "root.md": "@guide.md",
            "guide.md": "# Guide\nSee @CONTENTS.md for more.",
        })
        assert "# Guide" in out              # the resolvable file's section survives
        assert "## guide_md" not in out      # ...but with no generated header
        assert "`@CONTENTS.md`" in out        # missing sibling neutralized inline
        assert "## CONTENTS" not in out       # no phantom section for the missing file

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


class TestCommentStripping:
    """HTML comments are authoring guidance: stripped from the flat output, and
    never a live import (see the module docstring)."""

    def test_inline_comment_removed_surroundings_kept(self, home):
        out = _run(home, {"root.md": "before <!-- note --> after"})
        assert "note" not in out.split("-->", 1)[1]   # past the generated header
        assert "before  after" in out

    def test_block_comment_removed(self, home):
        out = _run(home, {"root.md": "top\n<!--\nhidden\nlines\n-->\nbottom\n"})
        body = out.split("-->", 1)[1]
        assert "hidden" not in body and "lines" not in body
        assert "top" in body and "bottom" in body

    def test_import_inside_comment_not_resolved(self, home):
        out = _run(home, {
            "root.md": "<!-- example: @child.md -->\nreal text",
            "child.md": "SECRET",
        })
        assert "## child_md" not in out
        assert "SECRET" not in out

    def test_comment_inside_fence_survives(self, home):
        out = _run(home, {"root.md": "```markdown\n<!-- example markup -->\n```\n"})
        assert "<!-- example markup -->" in out

    def test_comment_spanning_lines_around_live_import(self, home):
        """A block comment must not swallow a real import that follows it."""
        out = _run(home, {
            "root.md": "<!--\nc1\nc2\n-->\nsee @child.md\n",
            "child.md": "BODY",
        })
        assert "[child.md](#child_md)" in out
        assert "BODY" in out
        assert "c1" not in out.split("-->", 1)[1]

    def test_whole_comment_lines_leave_no_blank_run(self, home):
        out = _run(home, {"root.md": "a\n<!--\nx\ny\nz\n-->\nb\n"})
        body = _body(out)
        assert "a\nb" in body            # stripped block leaves no gap

    def test_generated_header_survives(self, home):
        out = _run(home, {"root.md": "<!-- gone -->\nkept"})
        assert _has_generated_header(out)
        assert "kept" in out

    def test_generated_header_points_at_the_canon(self, home):
        """The editable sources live under ``~/canon`` (handbook on the host,
        notebook in the box) — not under the retired ``~/playbook``."""
        out = _run(home, {"root.md": "body"})
        header = out.split("-->", 1)[0]
        assert "~/canon" in header
        assert "playbook" not in header

    def test_unterminated_comment_warns_at_eof(self, home, capsys):
        """⚑ Comment state is carried ACROSS lines, so ONE stray ``<!--`` swallows a
        file to EOF — every heading, rule and live import after it silently vanishes
        from the flattened artifact. Invisible in the output by construction, so the
        flattener must SAY so."""
        (home / "root.md").write_text(
            "kept\n<!-- oops, never closed\nswallowed @child.md\n", encoding="utf-8"
        )
        (home / "child.md").write_text("BODY", encoding="utf-8")

        rc = flattener.flatten(str(home / "root.md"), str(home / "out.md"))
        assert rc == 0
        err = capsys.readouterr().err
        assert "unterminated HTML comment" in err
        assert "root.md" in err

        # The swallowed content really is gone — that is what the warning is for.
        body = (home / "out.md").read_text().split("-->", 1)[1]
        assert "kept" in body
        assert "swallowed" not in body and "BODY" not in body

    def test_balanced_comments_do_not_warn(self, home, capsys):
        (home / "root.md").write_text("a\n<!-- fine -->\nb\n", encoding="utf-8")
        assert flattener.flatten(str(home / "root.md"), str(home / "out.md")) == 0
        assert "unterminated HTML comment" not in capsys.readouterr().err


class TestDedupAndOrder:
    def test_diamond_imported_once(self, home):
        out = _run(home, {
            "root.md": "@a.md and @b.md",
            "a.md": "A pulls @shared.md",
            "b.md": "B pulls @shared.md",
            "shared.md": "shared once",
        })
        assert out.count("shared once") == 1
        # both a and b point at the one shared section
        assert out.count("[shared.md](#shared_md)") == 2
        assert "## shared_md" not in out

    def test_cycle_terminates(self, home):
        # A deep cycle (>4 hops) still terminates: import-once, not a depth cap,
        # is the termination guarantee.
        out = _run(home, {
            "root.md": "root @a.md",
            "a.md": "A @b.md",
            "b.md": "B @c.md",
            "c.md": "C @d.md",
            "d.md": "D @e.md",
            "e.md": "E @a.md",
        })
        # each file's body emitted exactly once -> each link appears once,
        # except a's, which both the source and e point at
        assert out.count("[a.md](#a_md)") == 2
        for name in ("b", "c", "d", "e"):
            assert out.count(f"[{name}.md](#{name}_md)") == 1
        for name in ("a", "b", "c", "d", "e"):
            assert f"## {name}_md" not in out

    def test_slug_collision_numbered(self, home):
        # ~/a/b.md and ~/a/b_md both normalise to a_b_md -> second gets a suffix.
        out = _run(home, {
            "root.md": "@a/b.md and @a/b_md",
            "a/b.md": "dotted",
            "a/b_md": "undated",
        })
        assert "[a/b.md](#a_b_md)" in out
        assert "[a/b_md](#a_b_md_2)" in out
        assert "dotted" in out and "undated" in out


class TestFullDepthResolution:
    def test_deep_chain_resolved_no_depth_cap(self, home, capsys):
        # A chain far deeper than the old four-hop cap resolves in full: the
        # intermediate files are imports-only, so they collapse (no sections of
        # their own), but their imports are collected transitively and the
        # 7th-hop leaf content lands -- with no depth warning.
        out = _run(home, {
            "root.md": "top @d1.md",
            "d1.md": "@d2.md",
            "d2.md": "@d3.md",
            "d3.md": "@d4.md",
            "d4.md": "@d5.md",
            "d5.md": "@d6.md",
            "d6.md": "@d7.md",
            "d7.md": "leaf",
        })
        assert "[d1.md](#d1_md)" in out    # the source's own link survives
        for n in range(2, 8):
            # the deeper links lived only in collapsed imports-only bodies
            assert f"[d{n}.md](#d{n}_md)" not in out
        assert "leaf" in out
        assert "depth>" not in capsys.readouterr().err


class TestExcludedSections:
    """Rule 1: a functionally-empty or imports-only file contributes its imports
    but NO section. Rule 2: no generated ``## <slug>`` headers anywhere."""

    def test_zero_byte_file_excluded(self, home):
        out = _run(home, {"root.md": "top @empty.md", "empty.md": ""})
        body = _body(out)
        assert "[empty.md](#empty_md)" in body   # the source's link stays
        assert "---" not in body                 # but no section is emitted

    def test_whitespace_only_file_excluded(self, home):
        out = _run(home, {"root.md": "top @blank.md", "blank.md": "  \n\n\t\n"})
        assert "---" not in _body(out)

    @pytest.mark.parametrize("stub", [
        "<!-- just a note -->\n",                  # single-line
        "<!--\nmulti\nline\n-->\n",                # multi-line block
        "   <!-- spaced inline -->   \n",          # inline, padded
    ])
    def test_comment_only_file_excluded(self, home, stub):
        out = _run(home, {"root.md": "top @stub.md", "stub.md": stub})
        assert "---" not in _body(out)

    def test_content_with_inline_trailing_comment_survives(self, home):
        # A comment AFTER real content strips the comment, not the file.
        out = _run(home, {
            "root.md": "top @child.md",
            "child.md": "real <!-- note --> tail",
        })
        assert "real  tail" in out

    def test_fenced_comment_markers_keep_file_alive(self, home):
        # Comment delimiters inside a fence are literal content, so the file
        # is CONTENT and keeps its section.
        out = _run(home, {
            "root.md": "top @child.md",
            "child.md": "```markdown\n<!-- example markup -->\n```\n",
        })
        body = _body(out)
        assert "---" in body
        assert "<!-- example markup -->" in body

    def test_code_span_mention_is_content_not_an_import(self, home):
        # A backticked @path is literal text: it triggers no import AND makes
        # the file CONTENT, so the section survives.
        out = _run(home, {
            "root.md": "top @child.md",
            "child.md": "literal `@nope.md` only",
        })
        assert "literal `@nope.md` only" in _body(out)

    def test_imports_only_file_collapses_children_present(self, home):
        out = _run(home, {
            "root.md": "top @index.md",
            "index.md": "@a.md\n@b.md\n",
            "a.md": "AAA body",
            "b.md": "BBB body",
        })
        body = _body(out)
        assert "[index.md](#index_md)" in body     # the source's link stays
        assert "[a.md](#a_md)" not in body         # index's body is NOT emitted
        assert "[b.md](#b_md)" not in body
        assert "AAA body" in body and "BBB body" in body   # children land intact

    def test_nested_imports_only_chain_collapses_transitively(self, home):
        out = _run(home, {
            "root.md": "top @a.md",
            "a.md": "@b.md",
            "b.md": "@c.md",
            "c.md": "leaf content",
        })
        body = _body(out)
        assert "[a.md](#a_md)" in body
        assert "[b.md](#b_md)" not in body
        assert "[c.md](#c_md)" not in body
        assert body.count("leaf content") == 1     # exactly once

    def test_diamond_through_imports_only_dedups(self, home):
        out = _run(home, {
            "root.md": "@a.md and @b.md",
            "a.md": "@shared.md",
            "b.md": "@shared.md",
            "shared.md": "shared once",
        })
        body = _body(out)
        assert body.count("shared once") == 1
        assert "[shared.md](#shared_md)" not in body   # only in collapsed bodies

    def test_unresolved_only_import_keeps_its_section(self, home, capsys):
        # A file whose ONLY import resolves to nothing is CONTENT: the
        # neutralized `@path` is the only in-artifact sign of the miss, and a
        # collapsed index would hide it -- the section must survive, loudly.
        out = _run(home, {
            "root.md": "top @guide.md",
            "guide.md": "@nope.md",
        })
        body = _body(out)
        assert "---" in body                       # guide kept its section
        assert "`@nope.md`" in body
        err = capsys.readouterr().err
        assert "unresolved import @nope.md" in err

    def test_imports_only_source_collapses(self, home):
        # The rule applies to the source too: an imports-only SOURCE yields the
        # generated header comment plus the surviving children, nothing else.
        out = _run(home, {"root.md": "@child.md\n", "child.md": "# Child\nbody"})
        body = _body(out)
        assert body.startswith("# Child")
        assert "---" not in body

    def test_no_generated_slug_headers_anywhere(self, home):
        # Assert on the SLUG strings, not on ``##`` generally -- the files' own
        # markdown headings legitimately start with #.
        out = _run(home, {
            "root.md": "top @sub/child.md and @guide.md",
            "sub/child.md": "# Own Title\nchild body",
            "guide.md": "# Guide Title\nguide body",
        })
        assert "## sub_child_md" not in out
        assert "## guide_md" not in out
        assert "# Own Title" in out and "# Guide Title" in out

    def test_content_file_body_is_byte_preserved(self, home):
        # Modulo the pre-existing comment-stripping and import-rewriting, a
        # CONTENT file's body lands verbatim.
        child = "# Child\n\nSome **markdown** & <tags>.\n\n- one\n- two\n"
        out = _run(home, {"root.md": "top @child.md", "child.md": child})
        assert child.strip() in out


class TestOutputShape:
    def test_generated_header_and_source_missing(self, home):
        out = _run(home, {"root.md": "top @child.md", "child.md": "c"})
        assert _has_generated_header(out)
        # source content is the preamble; the child follows under its own
        # content, `---`-separated, with NO generated section header
        assert out.count("\n---\n") == 1
        assert "top [child.md](#child_md)" in out
        assert "## child_md" not in out

    def test_source_not_found_returns_2(self, home):
        rc = flattener.flatten(str(home / "absent.md"), str(home / "out.md"))
        assert rc == 2


class TestSizeWarning:
    """The size advisory — HARNESS-NEUTRAL, and advisory only.

    ⚑ The threshold is not a statement about any one agent. Harnesses cap their
    instruction file at different sizes (codex 32KiB, kimi 40KiB) and the constant
    is consulted for every one of them, so the warning names none: on a box running
    a roomier harness, a message naming a specific tool fired as a false alarm about
    something that was not even installed. The artifact is still written in full —
    this is a warning, never a gate, so ``flatten`` still returns 0.
    """

    _MESSAGE = (
        "WARNING: agent directives file exceeds 32KiB, "
        "the limit for some harnesses / agents."
    )

    def _flatten_to_size(self, home, capsys, size: int) -> str:
        """Flatten a padded source whose OUTPUT is EXACTLY *size* bytes; return stderr.

        The generated header is a fixed prefix, so adding N bytes of body adds N
        bytes of output: one correction round converges, and the assertion below
        proves it did rather than trusting it. Padding to an exact size is what
        lets the boundary be tested at all — ``>`` and ``>=`` differ by one byte.
        """
        src, dest = home / "root.md", home / "out.md"
        body = "x" * size
        for _ in range(3):
            src.write_text(body + "\n", encoding="utf-8")
            assert flattener.flatten(str(src), str(dest)) == 0
            actual = len(dest.read_bytes())
            err = capsys.readouterr().err
            if actual == size:
                return err
            body = "x" * (len(body) + size - actual)
        raise AssertionError(f"could not pad output to exactly {size}B")

    def test_over_limit_warns_with_the_harness_neutral_text(self, home, capsys):
        err = self._flatten_to_size(home, capsys, flattener.DIRECTIVE_SIZE_WARN_LIMIT + 1)
        # The delivery path is unchanged: stderr, ``import-directives: `` prefixed.
        assert err == f"import-directives: {self._MESSAGE}\n"
        # 🛑 The whole point of the rename: no agent is named.
        assert "codex" not in err.lower()

    def test_at_limit_is_silent(self, home, capsys):
        """Strictly GREATER than, so the limit itself is fine."""
        err = self._flatten_to_size(home, capsys, flattener.DIRECTIVE_SIZE_WARN_LIMIT)
        assert err == ""

    def test_small_output_is_silent(self, home, capsys):
        (home / "root.md").write_text("tiny\n", encoding="utf-8")
        assert flattener.flatten(str(home / "root.md"), str(home / "out.md")) == 0
        assert capsys.readouterr().err == ""

    def test_message_size_matches_the_constant(self):
        """The size is spelled out in the message, so pin the two together — a
        constant moved without the wording would warn about the wrong number."""
        assert flattener.DIRECTIVE_SIZE_WARN_LIMIT == 32 * 1024
        assert f"{flattener.DIRECTIVE_SIZE_WARN_LIMIT // 1024}KiB" in self._MESSAGE


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
        assert _has_generated_header(ctx)

    def test_dash_dest_writes_raw_to_stdout(self, home, capsys):
        self._write_tree(home)
        rc = flattener.flatten(str(home / "root.md"), "-")
        assert rc == 0
        out = capsys.readouterr().out
        assert "child body" in out
        assert _has_generated_header(out)
        assert not (home / "-").exists()  # never wrote a literal "-" file


class TestManifest:
    """The flatten RECEIPT — what a watcher re-checks to keep the slot fresh.

    The slot is written ONCE per agent launch, so without this the box runs on
    whatever the directives said at launch time, silently (the launch shim is
    ``|| true``). The receipt is what makes a mid-life edit noticeable.
    """

    def _flatten(self, home, files: dict[str, str]) -> dict:
        for rel, body in files.items():
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        rc = flattener.flatten(
            str(home / "root.md"),
            str(home / "out.md"),
            manifest=str(home / "manifest.json"),
        )
        assert rc == 0
        return json.loads((home / "manifest.json").read_text(encoding="utf-8"))

    @staticmethod
    def _sha256(path) -> str:
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_records_every_collected_file_with_its_content_hash(self, home):
        man = self._flatten(home, {
            "root.md": "@child.md",
            "child.md": "body @deep.md",
            "deep.md": "leaf",
        })
        assert man["version"] == 1
        assert man["seed"] == str(home / "root.md")
        assert man["dest"] == str(home / "out.md")
        by_path = {e["path"]: e for e in man["inputs"]}
        for name in ("root.md", "child.md", "deep.md"):
            entry = by_path[str(home / name)]
            assert entry["sha256"] == self._sha256(home / name)
            assert "absent" not in entry
        # The seed is an input like any other -- editing it must be noticed too.
        assert man["inputs"][0]["path"] == str(home / "root.md")

    def test_records_an_unresolved_import_as_absent(self, home):
        """🛑 THE MISS SIDE. A watcher that re-checks only the files the flatten FOUND
        can never notice one APPEARING -- nothing it watches would have moved -- yet a
        directive that starts existing is exactly the edit a user expects to land.
        (Live case: an unresolved plugin-directives import on a real box today.)"""
        man = self._flatten(home, {"root.md": "@child.md and @nope.md"})
        by_path = {e["path"]: e for e in man["inputs"]}
        assert by_path[str(home / "nope.md")] == {
            "path": str(home / "nope.md"), "absent": True,
        }

    def test_excluded_files_are_still_recorded_with_their_hashes(self, home):
        """Exclusion is OUTPUT-ONLY: a functionally-empty or imports-only file is
        still read, hashed and recorded, so the watcher notices the moment an
        edit gives it real content."""
        man = self._flatten(home, {
            "root.md": "@empty.md and @index.md and @nope.md",
            "empty.md": "",
            "index.md": "@child.md",
            "child.md": "body",
        })
        by_path = {e["path"]: e for e in man["inputs"]}
        for name in ("empty.md", "index.md"):
            entry = by_path[str(home / name)]
            assert entry["sha256"] == self._sha256(home / name)
            assert "absent" not in entry
        assert by_path[str(home / "nope.md")] == {
            "path": str(home / "nope.md"), "absent": True,
        }

    def test_absent_entry_is_the_path_that_would_have_resolved(self, home):
        # The recorded path is what the import NAMED (resolved against the importing
        # file, ~ expanded), so creating exactly that file flips the entry to a hit.
        man = self._flatten(home, {"root.md": "@sub/GUIDE.md", "sub/other.md": "x"})
        assert {"path": str(home / "sub" / "GUIDE.md"), "absent": True} in man["inputs"]
        (home / "sub" / "GUIDE.md").write_text("now here", encoding="utf-8")
        man2 = self._flatten(home, {})
        entry = {e["path"]: e for e in man2["inputs"]}[str(home / "sub" / "GUIDE.md")]
        assert entry["sha256"] == self._sha256(home / "sub" / "GUIDE.md")

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file anyway")
    def test_a_collected_but_unreadable_file_is_absent_not_a_hash(self, home):
        """It contributed no content, so it is an absence that may END -- the same
        shape, and the same watch, as an import that pointed nowhere."""
        (home / "root.md").write_text("@locked.md", encoding="utf-8")
        locked = home / "locked.md"
        locked.write_text("secret", encoding="utf-8")
        locked.chmod(0o000)
        try:
            rc = flattener.flatten(
                str(home / "root.md"), str(home / "out.md"),
                manifest=str(home / "manifest.json"),
            )
            assert rc == 0
            man = json.loads((home / "manifest.json").read_text())
        finally:
            locked.chmod(0o644)
        assert {"path": str(locked), "absent": True} in man["inputs"]

    def test_output_sha256_is_the_bytes_written_to_dest(self, home):
        man = self._flatten(home, {"root.md": "@child.md", "child.md": "body"})
        assert man["output_sha256"] == self._sha256(home / "out.md")

    def test_receipt_carries_content_hashes_and_nothing_else(self, home):
        """🛑 DELIBERATELY ABSENT: a timestamp or a length invites a later reader to
        COMPARE it, and these sources span an NFS home and read-only package binds
        where mtime is not comparable. Record only what the check uses.
        (⚑ Asserted on the KEYS, not on the serialized text -- a tmp path carries this
        test's own name, so a substring check over the blob would test itself.)"""
        man = self._flatten(home, {"root.md": "@child.md", "child.md": "b"})
        assert set(man) == {"version", "seed", "dest", "output_sha256", "inputs"}
        for entry in man["inputs"]:
            assert set(entry) in ({"path", "sha256"}, {"path", "absent"})

    def test_no_manifest_written_unless_asked(self, home):
        (home / "root.md").write_text("body", encoding="utf-8")
        assert flattener.flatten(str(home / "root.md"), str(home / "out.md")) == 0
        assert not (home / "manifest.json").exists()

    def test_additional_context_mode_refuses_a_manifest(self, home):
        """FILE mode only: the hook mode writes no DEST, so a receipt for it would
        describe a file nobody maintains. Refused, not silently ignored."""
        (home / "root.md").write_text("body", encoding="utf-8")
        rc = flattener.main([
            "import-directives.py", "--additional-context", str(home / "root.md"),
            "--manifest", str(home / "manifest.json"),
        ])
        assert rc == 2
        assert not (home / "manifest.json").exists()

    def test_main_threads_the_manifest_flag(self, home):
        (home / "root.md").write_text("body", encoding="utf-8")
        rc = flattener.main([
            "import-directives.py", str(home / "root.md"), str(home / "out.md"),
            "--manifest", str(home / "manifest.json"),
        ])
        assert rc == 0
        assert json.loads((home / "manifest.json").read_text())["version"] == 1

    def test_manifest_flag_without_a_path_is_a_usage_error(self, home):
        (home / "root.md").write_text("body", encoding="utf-8")
        rc = flattener.main([
            "import-directives.py", str(home / "root.md"), str(home / "out.md"),
            "--manifest",
        ])
        assert rc == 2


class TestAtomicAndUnchangedWrites:
    def test_dest_is_replaced_never_written_in_place(self, home):
        """A harness reading a HALF-WRITTEN instruction file is strictly worse than one
        reading a stale file. tmp+rename means a reader holding the old file keeps
        reading the whole old file; an in-place write would show it a truncated one."""
        (home / "root.md").write_text("first", encoding="utf-8")
        dest = home / "out.md"
        assert flattener.flatten(str(home / "root.md"), str(dest)) == 0
        with open(dest, encoding="utf-8") as held:
            (home / "root.md").write_text("second", encoding="utf-8")
            assert flattener.flatten(str(home / "root.md"), str(dest)) == 0
            assert "first" in held.read()          # the OLD file, intact
        assert "second" in dest.read_text(encoding="utf-8")
        # and nothing is left behind in the directory
        assert [p.name for p in home.iterdir() if ".tmp" in p.name] == []

    def test_identical_render_does_not_rewrite_dest(self, home):
        """RENDERED-OUTPUT GATE: an edit that does not survive into the flattened form
        (here, an HTML comment -- stripped by design) moves the input hashes but not
        the artifact. Rewriting an unchanged instruction file only invites a harness to
        reload it for nothing."""
        (home / "root.md").write_text("kept\n<!-- one -->\n", encoding="utf-8")
        dest, man = home / "out.md", home / "manifest.json"
        assert flattener.flatten(str(home / "root.md"), str(dest), manifest=str(man)) == 0
        before_ino = dest.stat().st_ino
        before = json.loads(man.read_text())

        (home / "root.md").write_text("kept\n<!-- two -->\n", encoding="utf-8")
        assert flattener.flatten(str(home / "root.md"), str(dest), manifest=str(man)) == 0

        assert dest.stat().st_ino == before_ino          # not rewritten at all
        after = json.loads(man.read_text())
        # ...but the RECEIPT is refreshed, or every later check would read "stale"
        # forever against input hashes that already moved.
        assert after["inputs"] != before["inputs"]
        assert after["output_sha256"] == before["output_sha256"]

    def test_changed_render_does_rewrite_dest(self, home):
        (home / "root.md").write_text("first", encoding="utf-8")
        dest = home / "out.md"
        assert flattener.flatten(str(home / "root.md"), str(dest)) == 0
        before_ino = dest.stat().st_ino
        (home / "root.md").write_text("second", encoding="utf-8")
        assert flattener.flatten(str(home / "root.md"), str(dest)) == 0
        assert dest.stat().st_ino != before_ino
        assert "second" in dest.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# ``[Display Text](@path/file.md)`` — the form that INCLUDES *and* LINKS.
# --------------------------------------------------------------------------

def _heading_ids(body: str) -> list[str]:
    """The fragment ids a GFM renderer would give this document's headings.

    ⚑ Deliberately a SECOND, independent implementation of GitHub's documented
    rule (lowercase · spaces to ``-`` · drop the rest · duplicates counted from
    one). Asserting the flattener's targets against the flattener's own
    derivation would prove only that it agrees with itself.
    """
    seen: dict[str, int] = {}
    ids: list[str] = []
    for m in re.finditer(r"^#+ (.+)$", body, re.M):
        base = re.sub(r"[^\w-]", "", m.group(1).strip().lower().replace(" ", "-"))
        n = seen.get(base, 0)
        seen[base] = n + 1
        ids.append(base if n == 0 else f"{base}-{n}")
    return ids


class TestLinkedIncludeCategories:
    """§2 — a ``[text](@path)`` link is classified by WHERE IT SITS."""

    def test_in_a_list_heading_is_number_then_display_text(self, home):
        out = _body(_run(home, {
            "root.md": "## Contents\n\n1.1 [Foo Man](@foo.md)\n",
            "foo.md": "foo body",
        }))
        assert "1.1 [Foo Man](#11-foo-man)" in out
        assert "## 1.1 Foo Man" in out
        assert "foo body" in out

    def test_not_in_a_list_heading_is_the_display_text_alone(self, home):
        out = _body(_run(home, {
            "root.md": "prose\n\n[Rando Include](@rando.md)\n",
            "rando.md": "rando body",
        }))
        assert "[Rando Include](#rando-include)" in out
        assert "# Rando Include" in out
        assert "rando body" in out

    def test_a_loose_link_takes_the_enclosing_level_exactly(self, home):
        """§3 — a link outside a list is headed as a SIBLING of the section it was
        written in: its relative term is zero, so it lands on the enclosing
        heading's own level, alongside that list's shallowest rows."""
        out = _body(_run(home, {
            "root.md": "### Rules\n\n1.1 [Numbered](@a.md)\n\n[Loose](@b.md)\n",
            "a.md": "A",
            "b.md": "B",
        }))
        assert "### 1.1 Numbered" in out
        assert "### Loose" in out
        assert re.search(r"^#{1,2} Loose", out, re.M) is None

    def test_a_loose_link_in_a_file_with_no_heading_is_top_level(self, home):
        """No heading above it and no section around the file either — the floor
        is H1, since a heading cannot sit at level 0."""
        out = _body(_run(home, {"root.md": "[Loose](@b.md)\n", "b.md": "B"}))
        assert out.startswith("[Loose](#loose)")
        assert "\n# Loose\n" in out

    def test_an_ordinary_markdown_link_is_left_alone(self, home):
        """The target's leading ``@`` is the whole marker."""
        out = _body(_run(home, {"root.md": "see [docs](guide.md) please\n"}))
        assert "[docs](guide.md)" in out

    def test_two_rows_naming_one_file_share_its_single_section(self, home):
        """Import-once is unchanged, so one file gets ONE heading: the first row to
        name it. The other row points at that same heading."""
        out = _body(_run(home, {
            "root.md": "1. [First Name](@a.md)\n2. [Second Name](@a.md)\n",
            "a.md": "shared body",
        }))
        assert out.count("shared body") == 1
        assert "# 1. First Name" in out
        assert "# 2. Second Name" not in out
        assert "1. [First Name](#1-first-name)" in out
        assert "2. [Second Name](#1-first-name)" in out


class TestLinkedIncludeSkips:
    """The link form obeys the same literal-text rules as the bare form."""

    def test_a_link_inside_a_comment_is_not_an_import(self, home):
        """🛑 THE TRAP (design §5c): comments are stripped BEFORE import parsing, so
        a ``[text](@path)`` inside ``<!-- ... -->`` DOES NOT EXIST. An inventory
        that regexes raw source instead reports collisions that cannot occur."""
        out = _body(_run(home, {
            "root.md": "<!--[STOCK]\n## 2.2 Example\n1. [Nope](@a.md)\n-->\nreal text\n",
            "a.md": "SECRET",
        }))
        assert "SECRET" not in out
        assert "Nope" not in out
        assert "2.2 Example" not in out
        assert "real text" in out

    def test_a_link_inside_a_fence_is_literal(self, home):
        out = _body(_run(home, {
            "root.md": "```markdown\n1. [Shown](@a.md)\n```\n",
            "a.md": "SECRET",
        }))
        assert "1. [Shown](@a.md)" in out       # verbatim, still showing the @
        assert "SECRET" not in out

    def test_a_link_inside_a_code_span_is_literal(self, home):
        out = _body(_run(home, {
            "root.md": "write `[Shown](@a.md)` like so\n",
            "a.md": "SECRET",
        }))
        assert "`[Shown](@a.md)`" in out
        assert "SECRET" not in out


class TestLinkedIncludeDepth:
    """§3 / §5c — header depth is the count of dot-separated PARTS."""

    def test_depth_follows_part_count_not_magnitude(self, home):
        out = _body(_run(home, {
            "root.md": (
                "1. [One](@a.md)\n"
                "1.1 [Two](@b.md)\n"
                "1.1.2 [Three](@c.md)\n"
                "11.2 [Also Two](@d.md)\n"
            ),
            "a.md": "A", "b.md": "B", "c.md": "C", "d.md": "D",
        }))
        assert "\n# 1. One\n" in out
        assert "\n## 1.1 Two\n" in out
        assert "\n### 1.1.2 Three\n" in out
        assert "\n## 11.2 Also Two\n" in out   # magnitude 11, still depth 2

    def test_a_nested_files_local_list_never_heads_above_its_chapter(self, home):
        """🛑 THE INVERSION THIS RULE EXISTS FOR. Directive numbering is FILE-LOCAL
        and restarts at ``1.`` inside every file, so depth taken from the number
        ALONE emits an H1 in the middle of an H2 chapter — a sub-sub-section that
        outranks the chapter containing it. The includer's own level is what keeps
        an included section strictly UNDER the section that included it."""
        out = _body(_run(home, {
            "root.md": "## Contents\n\n2.1 [Chapter](@chapter.md)\n",
            "chapter.md": "### Rules\n\n1. [Canon Sections](@canon.md)\n",
            "canon.md": "canon body",
        }))
        assert "## 2.1 Chapter" in out
        assert "### 1. Canon Sections" in out          # base 2 + one part
        assert re.search(r"^# 1\. Canon Sections", out, re.M) is None

    def test_depth_composes_through_the_authored_headings(self, home):
        """⚑ Composition is carried by the AUTHORED headings, not computed. An
        included body is emitted verbatim, so the ``### Rules`` a file wrote is
        still ``###`` in the flattened document — measuring the next hop against
        THAT is what makes a three-deep chain step down one level at a time."""
        out = _body(_run(home, {
            "root.md": "## Contents\n\n1. [Alpha](@a.md)\n",
            "a.md": "### Alpha Contents\n\n1. [Beta](@b.md)\n",
            "b.md": "#### Beta Contents\n\n1. [Gamma](@c.md)\n",
            "c.md": "gamma body",
        }))
        assert "\n## 1. Alpha\n" in out
        assert "\n### 1. Beta\n" in out
        assert "\n#### 1. Gamma\n" in out

    def test_a_headingless_file_falls_back_to_its_own_section_level(self, home):
        """No heading above the link inside the includer — so it measures against
        the section that file was itself included under, one frame out."""
        out = _body(_run(home, {
            "root.md": "### Deep Contents\n\n1. [Alpha](@a.md)\n",
            "a.md": "just prose\n\n1. [Beta](@b.md)\n",
            "b.md": "beta body",
        }))
        assert "\n### 1. Alpha\n" in out
        assert "\n### 1. Beta\n" in out      # sibling of the section it came from
        assert re.search(r"^#{1,2} 1\. Beta", out, re.M) is None

    def test_the_same_list_heads_differently_under_different_headings(self, home):
        """Identical file-local numbering under a ``##`` and under a ``###`` heads
        one level apart — and the ids still match their own headings, since depth
        never enters an id and the pair dedups on TEXT alone."""
        out = _body(_run(home, {
            "root.md": "## Contents\n\n1. [Alpha](@alpha.md)\n1.1 [Beta](@beta.md)\n",
            "alpha.md": "## Shallow\n\n1. [Overview](@ov1.md)\n",
            "beta.md": "### Deep\n\n1. [Overview](@ov2.md)\n",
            "ov1.md": "first overview",
            "ov2.md": "second overview",
        }))
        assert "\n## 1. Overview\n" in out       # under Alpha's ``## Shallow``
        assert "\n### 1. Overview\n" in out      # under Beta's ``### Deep``
        assert "1. [Overview](#1-overview)" in out
        assert "1. [Overview](#1-overview-1)" in out
        assert set(re.findall(r"\]\(#([^)]+)\)", out)) <= set(_heading_ids(out))

    def test_depth_is_clamped_at_six(self, home):
        """Markdown has no ``#######``: past six levels an include stops getting
        deeper rather than emitting a heading that renders as literal text."""
        rows = "".join(
            f"{'.'.join('1' * n)} [L{n}](@f{n}.md)\n" for n in range(1, 9)
        )
        files = {"root.md": f"## Contents\n\n{rows}"}
        for n in range(1, 9):
            files[f"f{n}.md"] = f"body {n}"
        out = _body(_run(home, files))
        assert "\n## 1. L1\n" in out             # shallowest row, relative term 0
        assert "\n###### 1.1.1.1.1 L5\n" in out  # 2 + 4
        assert "\n###### 1.1.1.1.1.1.1.1 L8\n" in out   # clamped, not ``########``
        assert "#######" not in out

    def test_absolute_part_count_is_not_used(self, home):
        """🛑 THE PROPERTY, stated on its own. A list whose SHALLOWEST row is
        2-part sits at its enclosing heading's level — ``##``, not ``###``. Read
        absolutely, ``1.1`` would bury itself one level under the ``## Contents``
        its author wrote it beneath."""
        out = _body(_run(home, {
            "root.md": "## Contents\n\n1.1 [Alpha](@a.md)\n1.2 [Beta](@b.md)\n",
            "a.md": "A", "b.md": "B",
        }))
        assert "\n## 1.1 Alpha\n" in out
        assert "\n## 1.2 Beta\n" in out
        assert "###" not in out

    def test_rom_contents_shape(self, home):
        """``rom/charter/ROM_CONTENTS.md``: ``## Charter Contents`` + ``1.1`` -> ``##``."""
        out = _body(_run(home, {
            "root.md": "## Charter Contents\n\n1.1 [Identity & Environment](@g.md)\n",
            "g.md": "general body",
        }))
        assert "\n## 1.1 Identity & Environment\n" in out

    def test_collection_shape(self, home):
        """``rom/COLLECTION.md``: ``## Library`` + ``1.`` -> ``##``. ⚑ Corrects
        UPWARD — read absolutely this is ``#``, which OUTRANKS the ``## Library``
        it came from."""
        out = _body(_run(home, {
            "root.md": "## Library\n\n1. [Charter (Core)](@b.md)\n2. [Handbook](@h.md)\n",
            "b.md": "charter body", "h.md": "handbook body",
        }))
        assert "\n## 1. Charter (Core)\n" in out
        assert "\n## 2. Handbook\n" in out
        assert re.search(r"^# \d", out, re.M) is None

    def test_sys_general_shape(self, home):
        """``template/handbook/general/SYS_GENERAL.md``: ``### Rules``
        + ``1.`` -> ``###``."""
        out = _body(_run(home, {
            "root.md": "### Rules\n\n1. [Canon Sections](@c.md)\n2. [Project Work](@d.md)\n",
            "c.md": "canon body", "d.md": "data body",
        }))
        assert "\n### 1. Canon Sections\n" in out
        assert "\n### 2. Project Work\n" in out

    def test_every_generated_heading_has_a_space_after_the_hashes(self, home):
        """🛑 CommonMark 4.2: ``##1.1 Foo`` is a PARAGRAPH, not a heading. Measured.
        Without the space the whole artifact silently loses its structure."""
        out = _body(_run(home, {
            "root.md": "1. [One](@a.md)\n1.1 [Two](@b.md)\n1.1.2 [Three](@c.md)\n",
            "a.md": "A", "b.md": "B", "c.md": "C",
        }))
        assert re.search(r"^#+[^# ]", out, re.M) is None


class TestGeneratedFragmentIds:
    """§4 / §5a — targets are ONE ``#`` plus the GFM-derived id."""

    def test_dots_vanish_and_case_is_lowered(self, home):
        out = _body(_run(home, {
            "root.md": "## Contents\n\n1.1 [Foo Man](@a.md)\n", "a.md": "A",
        }))
        assert "1.1 [Foo Man](#11-foo-man)" in out
        assert "## 1.1 Foo Man" in out     # the HEADING keeps its capitalization

    def test_ampersand_yields_a_double_hyphen(self, home):
        """Odd-looking and CORRECT: the spaces around ``&`` become hyphens and the
        ``&`` itself is dropped, which is what GitHub does — so the link resolves."""
        out = _body(_run(home, {
            "root.md": "## Contents\n\n1.1 [Identity & Environment](@a.md)\n",
            "a.md": "A",
        }))
        assert "(#11-identity--environment)" in out
        assert "## 1.1 Identity & Environment" in out

    def test_parentheses_are_dropped(self, home):
        out = _body(_run(home, {
            "root.md": "1. [Charter (Core)](@a.md)\n", "a.md": "A",
        }))
        assert "1. [Charter (Core)](#1-charter-core)" in out
        assert "# 1. Charter (Core)" in out

    def test_the_target_takes_exactly_one_hash(self, home):
        """A target beginning with ``#`` is a FRAGMENT and everything after the
        first ``#`` is the id, so ``(##1.1)`` asks for an id nothing ever has.
        Depth lives in the heading only."""
        out = _body(_run(home, {
            "root.md": "1.1.2 [Deep](@a.md)\n", "a.md": "A",
        }))
        assert "(#112-deep)" in out
        assert "(##" not in out

    def test_collision_counter_starts_at_one(self, home):
        """🛑 THE FIRST occurrence keeps the bare id; the SECOND takes ``-1``. Off by
        one here yields a link pointing at an id the renderer gave to nobody."""
        out = _body(_run(home, {
            "root.md": "1.1.2 [Baz Man](@a.md)\n11.2 [Baz Man](@b.md)\n",
            "a.md": "A", "b.md": "B",
        }))
        assert "1.1.2 [Baz Man](#112-baz-man)" in out
        assert "11.2 [Baz Man](#112-baz-man-1)" in out
        assert "-2)" not in out

    def test_third_collision_takes_minus_two(self, home):
        out = _body(_run(home, {
            "root.md": "1.1.2 [Baz](@a.md)\n11.2 [Baz](@b.md)\n1.12 [Baz](@c.md)\n",
            "a.md": "A", "b.md": "B", "c.md": "C",
        }))
        assert "(#112-baz)" in out
        assert "(#112-baz-1)" in out
        assert "(#112-baz-2)" in out

    def test_collisions_are_counted_in_the_order_headings_are_emitted(self, home):
        """A renderer counts duplicates down the RENDERED document, so the counter
        must follow section order — not the order the rows were discovered."""
        out = _body(_run(home, {
            "root.md": "## Contents\n\n1.1 [Same Name](@a.md)\n1.2 [Same Name](@b.md)\n",
            "a.md": "A", "b.md": "B",
        }))
        first = out.index("## 1.1 Same Name")
        second = out.index("## 1.2 Same Name")
        assert first < second
        assert "1.1 [Same Name](#11-same-name)" in out
        assert "1.2 [Same Name](#12-same-name)" in out


class TestBareImportUnchanged:
    """🛑 BOTH FORMS INCLUDE. Only LINKING changed — the bare form is NOT deprecated
    and must behave exactly as it did."""

    def test_bare_import_includes_and_generates_no_heading(self, home):
        out = _body(_run(home, {
            "root.md": "top @child.md", "child.md": "child body",
        }))
        assert "[child.md](#child_md)" in out    # slug link text, as before
        assert "child body" in out
        assert re.search(r"^#+ ", out, re.M) is None   # no generated heading at all

    def test_bare_and_linked_forms_coexist(self, home):
        out = _body(_run(home, {
            "root.md": "1. [Linked](@a.md)\n\nalso @b.md\n",
            "a.md": "A body", "b.md": "B body",
        }))
        assert "1. [Linked](#1-linked)" in out
        assert "# 1. Linked" in out
        assert "[b.md](#b_md)" in out
        assert "# B body" not in out and "## b_md" not in out
        assert out.count("\n# ") == 1          # exactly ONE generated heading

    def test_unresolved_link_target_stays_inert_and_warns(self, home, capsys):
        (home / "root.md").write_text("1. [Gone](@nope.md)\n", encoding="utf-8")
        assert flattener.flatten(str(home / "root.md"), str(home / "out.md")) == 0
        body = _body((home / "out.md").read_text(encoding="utf-8"))
        assert "[Gone](`@nope.md`)" in body     # neutralized exactly as a bare one is
        assert "unresolved import @nope.md" in capsys.readouterr().err

    def test_reflattening_the_output_changes_nothing(self, home):
        out = _run(home, {
            "root.md": "1. [Linked](@a.md)\n\nalso @b.md\n",
            "a.md": "A body", "b.md": "B body",
        })
        (home / "again.md").write_text(_body(out), encoding="utf-8")
        assert flattener.flatten(str(home / "again.md"), str(home / "out2.md")) == 0
        again = _body((home / "out2.md").read_text(encoding="utf-8"))
        assert "1. [Linked](#1-linked)" in again   # already-generated links are inert
        assert again.count("A body") == 1


class TestExclusionRenumbersSurvivors:
    """§5 Q6+Q7 — ONE mechanism, not two features: a row whose target is excluded
    DISAPPEARS, and that renumbers the rows that survive it."""

    LIVE = {
        "root.md": (
            "## Handbook Contents\n\n"
            "2.1 [System-Wide](@general.md)\n"
            "2.2 [Agent Directives](@agent.md)\n"
            "2.3 [Workset Directives](@workset.md)\n"
            "2.4 [Box Directives](@box.md)\n"
        ),
        "general.md": "general body",
        "agent.md": "<!--[STOCK]\nstill blank for now\n-->\n",   # comment-only
        "workset.md": "workset body",
        "box.md": "box body",
    }

    def test_excluded_row_disappears_and_survivors_renumber(self, home):
        """The live case: the plugin ``SYS_AGENT.md`` files are comment-only, so
        ``2.2 Agent Directives`` drops and 2.3/2.4 become 2.2/2.3."""
        out = _body(_run(home, dict(self.LIVE)))
        assert "2.1 [System-Wide](#21-system-wide)" in out
        assert "Agent Directives" not in out          # row gone, not blanked
        assert "2.2 [Workset Directives](#22-workset-directives)" in out
        assert "2.3 [Box Directives](#23-box-directives)" in out
        assert "2.4" not in out

    def test_the_headings_follow_the_new_numbers(self, home):
        """🛑 The two-pass point: a heading generated from the AUTHORED number would
        say ``## 2.3 Workset Directives`` under a row that now reads ``2.2``."""
        out = _body(_run(home, dict(self.LIVE)))
        assert "## 2.2 Workset Directives" in out
        assert "## 2.3 Box Directives" in out
        assert "## 2.3 Workset Directives" not in out
        assert "## 2.4 Box Directives" not in out

    def test_the_dropped_row_leaves_no_blank_line_behind(self, home):
        out = _body(_run(home, dict(self.LIVE)))
        rows = [ln for ln in out.splitlines() if ln.startswith("2.")]
        assert rows == [
            "2.1 [System-Wide](#21-system-wide)",
            "2.2 [Workset Directives](#22-workset-directives)",
            "2.3 [Box Directives](#23-box-directives)",
        ]

    def test_an_imports_only_target_also_drops_its_row(self, home):
        out = _body(_run(home, {
            "root.md": "1. [Kept](@a.md)\n2. [Index](@index.md)\n3. [Also](@c.md)\n",
            "a.md": "A body",
            "index.md": "@deep.md\n",
            "deep.md": "deep body",
            "c.md": "C body",
        }))
        assert "[Index]" not in out
        assert "2. [Also](#2-also)" in out    # renumbered down into the gap
        assert "deep body" in out             # ...but the child still lands
        assert "# 2. Also" in out

    def test_authored_numbers_survive_untouched_when_nothing_drops(self, home):
        """Renumbering is MINIMAL, not positional: ``1.1.2`` beside ``11.2`` keeps
        both, where a resequence would silently rewrite them to ``1.1.1``/``11.1``."""
        out = _body(_run(home, {
            "root.md": "1.1 [A](@a.md)\n1.1.2 [B](@b.md)\n11.2 [C](@c.md)\n",
            "a.md": "A", "b.md": "B", "c.md": "C",
        }))
        assert "1.1 [A](#11-a)" in out
        assert "1.1.2 [B](#112-b)" in out
        assert "11.2 [C](#112-c)" in out

    def test_a_descendant_follows_its_parents_new_number(self, home):
        out = _body(_run(home, {
            "root.md": (
                "## Contents\n\n"
                "1.1 [Gone](@gone.md)\n1.2 [Kept](@k.md)\n1.2.1 [Child](@c.md)\n"
            ),
            "gone.md": "<!-- blank -->\n",
            "k.md": "K body",
            "c.md": "C body",
        }))
        assert "1.1 [Kept](#11-kept)" in out
        assert "1.1.1 [Child](#111-child)" in out
        assert "## 1.1 Kept" in out
        assert "### 1.1.1 Child" in out


class TestTerminalDotConvention:
    """🛑 §5c — a single-part number carries a terminal dot (``1.`` ``2.`` ``3.``);
    a multi-part number does not (``1.1`` ``2.4``). His, intentional, and it is
    FORMATTING, so a renumber has to keep it right."""

    def test_authored_forms_are_preserved(self, home):
        out = _body(_run(home, {
            "root.md": "1. [Top](@a.md)\n1.1 [Sub](@b.md)\n",
            "a.md": "A", "b.md": "B",
        }))
        assert "\n# 1. Top\n" in out
        assert "\n## 1.1 Sub\n" in out

    def test_renumbering_keeps_the_dot_at_every_depth(self, home):
        """A renumber spanning BOTH depths at once: the single-part rows keep their
        terminal dot as they shift down, the multi-part rows keep going without
        one. Emitting ``1.1.`` or a bare ``2`` is the defect this pins."""
        out = _body(_run(home, {
            "root.md": (
                "1. [Gone](@gone.md)\n"
                "2. [Top](@top.md)\n"
                "3. [Next](@next.md)\n"
                "3.1 [SubGone](@subgone.md)\n"
                "3.2 [SubKept](@subkept.md)\n"
            ),
            "gone.md": "<!-- blank -->\n",
            "top.md": "T",
            "next.md": "N",
            "subgone.md": "<!-- blank -->\n",
            "subkept.md": "S",
        }))
        assert "1. [Top](#1-top)" in out and "\n# 1. Top\n" in out
        assert "2. [Next](#2-next)" in out and "\n# 2. Next\n" in out
        # the sub-rows kept their authored parent prefix ``3`` -- their parent row
        # renumbered to ``2``, so they follow it -- and take NO terminal dot.
        assert "2.1 [SubKept](#21-subkept)" in out
        assert "\n## 2.1 SubKept\n" in out
        assert "2.1." not in out
        assert re.search(r"^\d+ \[", out, re.M) is None   # never a bare ``2 [x]``


class TestNumberSeam:
    """§5d — ALL number handling lives behind ``assign_section_numbers``, so that
    computing numbers from nesting later is a single-site change. These pin the
    seam's contract directly."""

    def test_depth_is_the_enclosing_level_plus_relative_nesting(self):
        """🛑 RELATIVE, not absolute. A one-row list heads at its enclosing level
        whatever its number says — ``1.`` and ``1.1.2`` alike — because a
        file-local number carries no absolute depth of its own."""
        for number in ("1.", "1.1", "1.1.2", "11.2"):
            assert flattener.assign_section_numbers([(number, False)], 2) == [(number, 2)]
            assert flattener.assign_section_numbers([(number, False)], 4) == [(number, 4)]

    def test_relative_nesting_is_measured_from_the_lists_shallowest_row(self):
        assert flattener.assign_section_numbers(
            [("1.1", False), ("1.1.2", False), ("11.2", False)], 2
        ) == [("1.1", 2), ("1.1.2", 3), ("11.2", 2)]
        # the same shapes in a list that starts one level shallower
        assert flattener.assign_section_numbers(
            [("1.", False), ("1.1", False), ("1.1.2", False)], 2
        ) == [("1.", 2), ("1.1", 3), ("1.1.2", 4)]

    def test_an_excluded_row_does_not_move_the_survivors_depth(self):
        """``min(parts)`` is taken over the list AS AUTHORED, so dropping the only
        shallow chapter must not silently promote its siblings a level."""
        assert flattener.assign_section_numbers(
            [("1.", True), ("1.1", False), ("1.2", False)], 2
        ) == [None, ("1.1", 3), ("1.2", 3)]

    def test_the_terminal_dot_is_formatting(self):
        """Applied to whatever number a row ENDS UP with, which is what keeps it
        right across a renumber rather than only as authored."""
        assert flattener.assign_section_numbers(
            [("1.", True), ("2.", False), ("3.", False)], 2
        ) == [None, ("1.", 2), ("2.", 2)]
        assert flattener.assign_section_numbers(
            [("2.1", True), ("2.2", False), ("2.3", False)], 2
        ) == [None, ("2.1", 2), ("2.2", 2)]

    def test_depth_is_clamped_at_the_markdown_maximum(self):
        assert flattener.MAX_HEADING_DEPTH == 6
        assert flattener.assign_section_numbers([("1.", False)], 6) == [("1.", 6)]
        assert flattener.assign_section_numbers(
            [("1.", False), ("1.1.1.1", False)], 5
        ) == [("1.", 5), ("1.1.1.1", 6)]

    def test_a_row_outside_a_list_takes_the_enclosing_level(self):
        assert flattener.assign_section_numbers(
            [("1.1", False), (None, False)], 2
        ) == [("1.1", 2), (None, 2)]
        assert flattener.assign_section_numbers([(None, False)], 3) == [(None, 3)]
        assert flattener.assign_section_numbers([(None, False)]) == [(None, 1)]

    def test_a_dropped_row_yields_nothing(self):
        assert flattener.assign_section_numbers([(None, True)]) == [None]

    def test_gfm_anchor_derivation(self):
        assert flattener.gfm_anchor("1.1 Foo Man") == "11-foo-man"
        assert flattener.gfm_anchor("1. Charter (Core)") == "1-charter-core"
        assert flattener.gfm_anchor("1.1 Identity & Environment") == (
            "11-identity--environment"
        )
        assert flattener.gfm_anchor("Rando Include") == "rando-include"
        assert flattener.gfm_anchor("2.1 System-Wide Information & Directives") == (
            "21-system-wide-information--directives"
        )


class TestTwoPassConsistency:
    """The invariant the two-pass render exists to hold: the table of contents and
    the headings are generated from the SAME final numbers, so neither can point
    at something the other does not have."""

    TREE = {
        "root.md": (
            "1. [Alpha](@a.md)\n"
            "2. [Beta](@b.md)\n"
            "2.1 [Beta One](@b1.md)\n"
            "2.2 [Gone](@gone.md)\n"
            "2.3 [Beta Three](@b3.md)\n"
            "3. [Gamma & Delta](@g.md)\n"
            "\n[Loose One](@loose.md)\n"
        ),
        "a.md": "alpha body",
        "b.md": "beta body",
        "b1.md": "beta one body",
        "gone.md": "<!-- nothing yet -->\n",
        "b3.md": "beta three body",
        "g.md": "gamma body",
        "loose.md": "loose body",
    }

    def test_every_target_resolves_to_a_heading_that_exists(self, home):
        body = _body(_run(home, dict(self.TREE)))
        targets = re.findall(r"\]\(#([^)]+)\)", body)
        assert targets                       # the test would pass vacuously otherwise
        assert set(targets) <= set(_heading_ids(body))

    def test_every_generated_heading_is_referenced(self, home):
        body = _body(_run(home, dict(self.TREE)))
        ids = _heading_ids(body)
        assert set(ids) == set(re.findall(r"\]\(#([^)]+)\)", body))

    def test_no_two_headings_derive_the_same_id(self, home):
        ids = _heading_ids(_body(_run(home, dict(self.TREE))))
        assert len(ids) == len(set(ids))

    def test_no_placeholder_survives_into_the_artifact(self, home):
        body = _body(_run(home, dict(self.TREE)))
        assert "\x00" not in body


class TestCanonicalExample:
    """§3a — the design's reference example, verified against the GFM derivation,
    reproduced end to end. Where any other section of the design differs, this
    governs."""

    SOURCE = {
        "root.md": (
            "# Big Bad Instructions File\n"
            "\n"
            "## Contents\n"
            "\n"
            "1.1   [Foo Man](@path/to/foo.md)\n"
            "1.1.2 [Baz Man](@path/to/the/baz.md)\n"
            "1.2   [Display Me](@path/to/bar.md)\n"
            "11.2  [Baz Man](@path/to/bizzer.md)\n"
            "\n"
            "[Rando Include](@some/other/rando.md)\n"
        ),
        "path/to/foo.md": "<path/to/foo.md content here>\n",
        "path/to/the/baz.md": "<path/to/the/baz.md content here>\n",
        "path/to/bar.md": "<path/to/bar.md content here>\n",
        "path/to/bizzer.md": "<path/to/bizzer.md content here>\n",
        "some/other/rando.md": "<some/other/rando.md content here>\n",
    }

    EXPECTED = """\
# Big Bad Instructions File

## Contents

1.1   [Foo Man](#11-foo-man)
1.1.2 [Baz Man](#112-baz-man)
1.2   [Display Me](#12-display-me)
11.2  [Baz Man](#112-baz-man-1)

[Rando Include](#rando-include)

---

## 1.1 Foo Man

<path/to/foo.md content here>

---

### 1.1.2 Baz Man

<path/to/the/baz.md content here>

---

## 1.2 Display Me

<path/to/bar.md content here>

---

## 11.2 Baz Man

<path/to/bizzer.md content here>

---

## Rando Include

<some/other/rando.md content here>
"""

    def test_reproduced_end_to_end(self, home):
        assert _body(_run(home, dict(self.SOURCE))) == self.EXPECTED

    def test_the_authored_column_alignment_survives(self, home):
        """The rows are padded so the display texts line up; renumbering rewrites
        the number, not the gap the author put after it."""
        body = _body(_run(home, dict(self.SOURCE)))
        assert "1.1   [Foo Man]" in body
        assert "11.2  [Baz Man]" in body


# --------------------------------------------------------------------------
# The template functions: __SUPER__, __SECTION__, and the title formatter.
# --------------------------------------------------------------------------


def _canon_data(rel: str) -> str:
    """A shipped canon file's real path, under ``kanibako.data``."""
    return str(importlib.resources.files("kanibako.data").joinpath(f"global/{rel}"))


def _scope(fl=None, **extra):
    """A title-format namespace of the shape ``preplink`` builds."""
    fl = fl or flattener.Flattener()
    scope = {"__SECTION__": fl.next_section, "__SUPER__": flattener.super_of}
    scope.update(extra)
    return scope


class TestSectionIdAlgebra:
    """``__SUPER__`` and ``__SECTION__`` — the ids a template function MINTS,
    which is a different job from the authored numbers ``assign_section_numbers``
    renumbers: one reads a number off the page, the other allocates a new one."""

    def test_super_of_his_worked_examples(self):
        assert flattener.super_of("5.4") == "5"
        assert flattener.super_of("1.1") == "1"
        assert flattener.super_of("1-2-3", "-") == "1-2"

    def test_the_other_separator_is_ordinary_text_inside_a_part(self):
        """Which is what makes these two differ at all: read with ``.``, the ``-``
        in ``1-2`` is just characters."""
        assert flattener.super_of("1.2-3") == "1"
        assert flattener.super_of("1-2.3") == "1-2"

    def test_no_separator_means_no_parent(self):
        """🛑 Not ``"1"``. ``rsplit`` would return the element itself, making a
        root element its own parent."""
        assert flattener.super_of("1") == ""
        assert flattener.super_of("1-2") == ""     # read with the default ``.``

    def test_section_allocates_his_worked_sequence(self):
        """His six examples, in order, against ONE render's counters."""
        fl = flattener.Flattener()
        assert fl.next_section("2", ".") == "2.1"
        assert fl.next_section("1", ".") == "1.1"
        assert fl.next_section("1", "-") == "1-2"
        assert fl.next_section("1.2", ".") == "1.2.1"
        assert fl.next_section("1.2", "-") == "1.2-2"
        assert fl.next_section("1-2", ".") == "1-2.1"

    def test_one_counter_per_source_shared_across_separators(self):
        """🛑 Keyed on *source* ALONE. The second call is the SECOND section of
        source ``1`` however it is asked to render the join, so it is ``2`` —
        and ``1.2`` / ``1-2`` remain distinct SOURCES with counters of their own."""
        fl = flattener.Flattener()
        assert [fl.next_section("1", sep) for sep in (".", "-", ".")] == [
            "1.1", "1-2", "1.3",
        ]

    def test_a_root_source_carries_no_leading_separator(self):
        """Root is the common case — ``COLLECTION.md`` IS the root, where
        ``__CURRENT__`` is ``""``. Join only the non-empty parts."""
        fl = flattener.Flattener()
        assert [fl.next_section() for _ in range(3)] == ["1", "2", "3"]
        assert flattener.Flattener().next_section("", "-") == "1"

    def test_counters_reset_with_the_render_not_the_process(self):
        """The reset boundary is ONE assembly run, which is what makes a repeated
        render of the same tree reproducible."""
        first = flattener.Flattener()
        assert [first.next_section("1") for _ in range(3)] == ["1.1", "1.2", "1.3"]
        assert flattener.Flattener().next_section("1") == "1.1"


class TestRenderFormat:
    """The formatter. 🛑 NOT ``str.format`` — the braces are an EVALUATION
    MARKER, and the shipped default dies under ``str.format`` (measured)."""

    def test_str_format_cannot_do_this_which_is_why_it_has_its_own_name(self):
        """The SHIPPED default format, run through ``str.format``. ⚑ The ``noqa``
        is itself the evidence: ruff's F524 sees the same defect, statically."""
        with pytest.raises(KeyError):
            "{__SECTION__(source, sep)} {}".format("Foobar Info")  # noqa: F524

    def test_the_title_slot(self):
        assert flattener.render_format("@", "Foo Man", _scope()) == "Foo Man"

    def test_brace_escapes_are_literal_braces(self):
        assert flattener.render_format("{{@}}", "Foo", _scope()) == "{Foo}"
        assert flattener.render_format("{{{{@}}}}", "Foo", _scope()) == "{{Foo}}"

    def test_empty_braces_are_the_title_slot_too(self):
        """Defined as the slot, not left to fall out as an empty expression."""
        assert flattener.render_format("{} @", "Foo", _scope()) == "Foo Foo"

    def test_an_expression_is_evaluated_in_the_given_scope(self):
        scope = _scope(source="1.2", sep="-")
        assert flattener.render_format("{source + sep} @", "Foo", scope) == "1.2- Foo"

    def test_an_expression_can_call_the_section_allocator(self):
        """The shipped default format. ⚑ It ALLOCATES, so two renders of the same
        format take consecutive numbers."""
        scope = _scope(source="1", sep=".")
        fmt = "{__SECTION__(source, sep)} @"
        assert flattener.render_format(fmt, "Foo", scope) == "1.1 Foo"
        assert flattener.render_format(fmt, "Bar", scope) == "1.2 Bar"

    def test_an_expression_can_call_super(self):
        scope = _scope(source="1.2", sep=".")
        assert flattener.render_format("{__SUPER__(source, sep)}: @", "Foo", scope) == (
            "1: Foo"
        )

    def test_at_at_is_a_literal_at_and_is_not_a_slot(self):
        assert flattener.render_format("@@ @", "Foo", _scope()) == "@ Foo"
        assert flattener.render_format("@@@", "Foo", _scope()) == "@Foo"

    def test_exactly_one_unescaped_at_is_required(self):
        for fmt in ("", "no slot here", "@ and @", "@@ @ @"):
            with pytest.raises(flattener.FormatError):
                flattener.render_format(fmt, "Foo", _scope())

    def test_four_ats_escape_to_zero_slots_and_are_refused(self):
        """⚑ His escape ORDER, checked: ``@@@@`` is two literals, hence ZERO
        slots — so it raises, exactly as the empty format does."""
        with pytest.raises(flattener.FormatError):
            flattener.render_format("@@@@", "Foo", _scope())

    def test_a_format_with_no_at_is_refused_even_when_braces_would_render(self):
        """``{}`` alone is the ``str.format`` reflex, and it is not a title slot
        DECLARATION — the ``@`` is."""
        with pytest.raises(flattener.FormatError):
            flattener.render_format("{}", "Foo", _scope())

    def test_a_substituted_title_is_inert(self):
        """🛑 Never rescan what was just substituted. Titles come from arbitrary
        markdown, so a heading carrying a brace is not hypothetical — and the
        expression it looks like would be a ``NameError`` if it were evaluated."""
        assert flattener.render_format("@", "Use {foo} syntax", _scope()) == (
            "Use {foo} syntax"
        )
        assert flattener.render_format("@", "an @ and a }", _scope()) == "an @ and a }"

    def test_an_evaluated_value_is_inert_too(self):
        scope = _scope(brace="{@}")
        assert flattener.render_format("{brace}@", "T", scope) == "{@}T"

    def test_the_output_is_final_and_is_never_re_rendered(self):
        """Unescape EXACTLY ONCE. Pass one consumed ``{{``, so a second pass over
        the same text would read the surviving ``{`` as an expression."""
        once = flattener.render_format("{{literal}} @", "Foo", _scope())
        assert once == "{literal} Foo"
        with pytest.raises(NameError):
            flattener.render_format("@" + once, "Foo", _scope())

    def test_unbalanced_braces_are_refused(self):
        for fmt in ("{unclosed @", "} @"):
            with pytest.raises(flattener.FormatError):
                flattener.render_format(fmt, "Foo", _scope())


class TestPreplink:
    """The workhorse: what each target of one call is to be TITLED. It decides
    titles and nothing else — no content is read or collected here, which is what
    lets ``__LINK__`` share it with ``__IMPORT__``."""

    def test_returns_four_tuples(self, home):
        """🛑 The arity is load-bearing: both callers unpack exactly four."""
        (home / "a.md").write_text("# Alpha\nbody\n", encoding="utf-8")
        rows = flattener.Flattener().preplink(str(home / "a.md"))
        assert len(rows) == 1
        entry, old_title, new_title, header = rows[0]
        assert entry == home / "a.md"
        assert (old_title, new_title, header) == ("# Alpha", "Alpha", "# ")

    def test_a_heading_is_split_into_header_and_title(self, home):
        (home / "d.md").write_text("#### Foobar Info\n", encoding="utf-8")
        _entry, old_title, new_title, header = flattener.Flattener().preplink(
            str(home / "d.md")
        )[0]
        assert (old_title, header, new_title) == ("#### Foobar Info", "#### ", "Foobar Info")
        assert header + new_title == old_title

    def test_a_first_line_that_is_not_a_heading_takes_no_header(self, home):
        (home / "p.md").write_text("Just a paragraph.\n# Later Heading\n", encoding="utf-8")
        _entry, old_title, new_title, header = flattener.Flattener().preplink(
            str(home / "p.md")
        )[0]
        assert (old_title, new_title, header) == (
            "Just a paragraph.", "Just a paragraph.", "",
        )

    def test_glob_results_are_sorted_lexicographically(self, home):
        """Canon order is law; a directory walk is not an order."""
        for name in ("gamma.md", "alpha.md", "beta.md"):
            (home / "ch" / name).parent.mkdir(parents=True, exist_ok=True)
            (home / "ch" / name).write_text(f"# {name}\n", encoding="utf-8")
        rows = flattener.Flattener().preplink(str(home / "ch" / "*.md"))
        assert [entry.name for entry, _, _, _ in rows] == [
            "alpha.md", "beta.md", "gamma.md",
        ]

    def test_a_missing_target_is_skipped_silently(self, home):
        """⚑ NOT the fail-open the required-material tests forbid: required vs
        optional is a different layer, and the flattener has no notion of it."""
        assert flattener.Flattener().preplink(str(home / "nope.md")) == []
        assert flattener.Flattener().preplink(str(home / "nothing" / "*.md")) == []

    def test_an_empty_target_is_skipped_silently(self, home):
        (home / "blank.md").write_text("\n\n   \n", encoding="utf-8")
        (home / "comments.md").write_text(
            "<!--\n# Heading Shaped Decoy\nstill a comment\n-->\n\n", encoding="utf-8"
        )
        fl = flattener.Flattener()
        assert fl.preplink(str(home / "blank.md")) == []
        assert fl.preplink(str(home / "comments.md")) == []

    def test_the_title_is_the_first_line_of_real_text_not_the_first_hash(self, home):
        """🛑 A COMMENT IS NOT REAL TEXT, and the blocks contain heading-shaped
        decoys, so the naive test picks the wrong title."""
        (home / "c.md").write_text(
            "# Real Title\n<!--\n# Decoy\n-->\nbody\n", encoding="utf-8"
        )
        (home / "after.md").write_text(
            "<!--\n# Decoy\n\nstill inside\n-->\n\n## Real Title\n", encoding="utf-8"
        )
        fl = flattener.Flattener()
        assert fl.preplink(str(home / "c.md"))[0][1] == "# Real Title"
        assert fl.preplink(str(home / "after.md"))[0][1] == "## Real Title"

    # The six shipped canon files, measured through the real stripper. Every one
    # opens with a multi-line ``[STOCK]`` authoring comment, and COLLECTION.md's
    # runs sixteen lines with ``# Entrypoint to Canon`` inside it.
    SHIPPED_TITLES = [
        ("rom/COLLECTION.md", "# Canon Law - Introduction"),
        ("rom/charter/ROM_CONTENTS.md", "# Charter (Core Tome, Read-Only)"),
        ("template/handbook/SYS_CONTENTS.md", "# Handbook (System Tome)"),
        ("template/handbook/general/SYS_GENERAL.md", "## System-Wide Information"),
        ("template/box/home/canon/notebook/MY_CONTENTS.md", "# Notebook"),
        ("rom/charter/general/ROM_GENERAL.md", "## The Canon"),
    ]

    @pytest.mark.parametrize("rel,expected", SHIPPED_TITLES)
    def test_shipped_canon_titles(self, rel, expected):
        rows = flattener.Flattener().preplink(_canon_data(rel))
        assert [old for _, old, _, _ in rows] == [expected]

    def test_the_default_format_yields_the_bare_title(self, home):
        (home / "a.md").write_text("# Alpha\n", encoding="utf-8")
        fl = flattener.Flattener()
        assert fl.preplink(str(home / "a.md"))[0][2] == "Alpha"
        assert fl.preplink(str(home / "a.md"), title_fmt="")[0][2] == "Alpha"

    def test_the_section_format_numbers_each_entry_in_turn(self, home):
        """⚑ ``__SECTION__`` increments ONCE PER ENTRY — the format is rendered
        inside the loop, so each globbed file gets its own number."""
        for name in ("a.md", "b.md", "c.md"):
            (home / "n" / name).parent.mkdir(parents=True, exist_ok=True)
            (home / "n" / name).write_text(f"## {name}\n", encoding="utf-8")
        rows = flattener.Flattener().preplink(
            str(home / "n" / "*.md"), "1", ".", "{__SECTION__(source, sep)} @"
        )
        assert [new for _, _, new, _ in rows] == ["1.1 a.md", "1.2 b.md", "1.3 c.md"]

    def test_a_bad_format_is_refused_before_anything_is_numbered(self, home):
        (home / "a.md").write_text("# Alpha\n", encoding="utf-8")
        with pytest.raises(flattener.FormatError):
            flattener.Flattener().preplink(str(home / "a.md"), title_fmt="no slot")

    def test_a_relative_target_is_anchored_at_the_given_base(self, home, monkeypatch):
        """⚑ NOT the process CWD, which in a box is wherever the agent stood."""
        (home / "ch").mkdir()
        (home / "ch" / "a.md").write_text("# Anchored\n", encoding="utf-8")
        (home / "elsewhere").mkdir()
        monkeypatch.chdir(home / "elsewhere")
        fl = flattener.Flattener()
        assert fl.preplink("ch/a.md") == []                     # CWD-relative: nothing
        assert fl.preplink("ch/a.md", base=home)[0][1] == "# Anchored"
        assert fl.preplink("ch/*.md", base=home)[0][1] == "# Anchored"

    def test_current_is_bound_in_the_expression_namespace(self, home):
        (home / "a.md").write_text("# Alpha\n", encoding="utf-8")
        rows = flattener.Flattener().preplink(
            str(home / "a.md"), title_fmt="{__CURRENT__}/@", current="2.4",
        )
        assert rows[0][2] == "2.4/Alpha"

    def test_the_id_each_entry_was_minted_is_recorded(self, home):
        """The id is minted INSIDE the format and only the rendered title comes
        back, so the allocator's answer is recorded rather than parsed out of
        prose. It is what an imported document's own ``__CURRENT__`` becomes."""
        for name in ("a.md", "b.md"):
            (home / "m" / name).parent.mkdir(parents=True, exist_ok=True)
            (home / "m" / name).write_text(f"# {name}\n", encoding="utf-8")
        fl = flattener.Flattener()
        fl.preplink(str(home / "m" / "*.md"), "1", ".", flattener.SECTION_TITLE_FMT)
        assert fl.last_minted == ["1.1", "1.2"]
        # A format that mints nothing leaves the entry without an id.
        fl.preplink(str(home / "m" / "*.md"))
        assert fl.last_minted == [None, None]


# --------------------------------------------------------------------------
# The four call forms: __IMPORT__, __LINK__ and their SECTION wrappers.
# --------------------------------------------------------------------------


class TestTemplateCallParsing:
    """Recognition only — a line is a call, or it is prose. 🛑 Parsed with
    ``ast``, never with a regex over the argument text."""

    def test_the_four_names_are_recognized_alone_on_a_line(self):
        for name in ("__IMPORT__", "__LINK__", "__IMPORTSECTION__", "__LINKSECTION__"):
            got = flattener.parse_template_call(f'{name}("a.md")')
            assert got == (name, "", {"target": "a.md"})

    def test_indentation_is_kept(self):
        name, indent, args = flattener.parse_template_call('    __IMPORT__("a.md")')
        assert (name, indent, args) == ("__IMPORT__", "    ", {"target": "a.md"})

    def test_positional_arguments_bind_in_signature_order(self):
        _n, _i, args = flattener.parse_template_call('__IMPORT__("a", "1", "-", "@!")')
        assert args == {"target": "a", "source": "1", "sep": "-", "title_fmt": "@!"}

    def test_keyword_arguments_are_supported_though_no_call_site_uses_one(self):
        """The signature has four named parameters; a source is entitled to name
        them, and a regex over the argument text could not read this."""
        _n, _i, args = flattener.parse_template_call(
            '__LINKSECTION__("a", sep="-", title_fmt=None)'
        )
        assert args == {"target": "a", "sep": "-", "title_fmt": None}

    def test_a_comma_inside_an_argument_survives(self):
        """Which is the whole reason this is not a split on commas."""
        _n, _i, args = flattener.parse_template_call('__IMPORT__("a,b.md", "1,2")')
        assert args == {"target": "a,b.md", "source": "1,2"}

    def test_prose_is_not_a_call(self):
        for line in (
            "Some prose about __IMPORT__ and how it works.",
            "`__IMPORTSECTION__(\"a.md\")`",
            "__SECTION__(\"1\")",
            "",
        ):
            assert flattener.parse_template_call(line) is None

    def test_a_line_that_is_not_a_whole_call_is_left_alone(self):
        """⚑ WHOLE-LINE, deliberately: the name must open the line and the call
        must close it, so no run of prose around a call is ever consumed."""
        for line in (
            '__IMPORT__("a.md"',                 # unclosed
            '__IMPORT__("a.md") and prose',      # trailing prose
            'See __IMPORT__("a.md")',            # leading prose
        ):
            assert flattener.parse_template_call(line) is None

    def test_a_malformed_call_is_refused_rather_than_guessed_at(self):
        for line in (
            '__IMPORT__(some_name)',             # not a literal
            '__IMPORT__(*args)',                 # not a literal either
            '__IMPORT__(42)',                    # not a string
            '__IMPORT__()',                      # no target
            '__IMPORT__("a", "b", "c", "d", "e")',   # too many
            '__IMPORT__("a", nope="x")',         # unknown argument
            '__IMPORT__("a", target="a")',       # given twice
            '__IMPORT__("a") + (1)',             # not a single call
            '__IMPORT__("a",,)',                 # a syntax error
        ):
            with pytest.raises(flattener.TemplateCallError):
                flattener.parse_template_call(line)


class TestTemplateImportAndLink:
    """What the four forms DO. ``__IMPORT__`` re-titles its target's own heading
    and pulls its body in; ``__LINK__`` writes the row and nothing else."""

    def test_importsection_numbers_titles_and_includes(self, home):
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("child.md")\n',
            "child.md": "# Child\n\nbody\n",
        }))
        # The row carries the minted title; the SECTION carries it as a heading.
        assert "[1 Child](#1-child)" in out
        assert "# 1 Child" in out
        assert "body" in out
        assert "__IMPORTSECTION__" not in out

    def test_import_without_a_section_format_takes_the_bare_title(self, home):
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORT__("child.md")\n',
            "child.md": "## Child\n\nbody\n",
        }))
        assert "[Child](#child)" in out
        assert "## Child" in out          # the heading level is the file's own

    def test_linksection_writes_a_row_and_imports_nothing(self, home):
        out = _body(_run(home, {
            "root.md": '# Root\n\n__LINKSECTION__("proc.md")\n',
            "proc.md": "# Procedure\n\nload me on demand\n",
        }))
        assert f"[1 Procedure]({(home / 'proc.md').as_posix()})" in out
        assert "load me on demand" not in out      # NOT imported
        assert "#1-procedure" not in out           # points at the FILE, not a section

    def test_link_without_a_section_format_takes_the_bare_title(self, home):
        out = _body(_run(home, {
            "root.md": '# Root\n\n__LINK__("proc.md")\n',
            "proc.md": "# Procedure\n\nbody\n",
        }))
        assert f"[Procedure]({(home / 'proc.md').as_posix()})" in out

    def test_a_glob_expands_to_one_row_per_entry_in_sorted_order(self, home):
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("ch/*.md")\n',
            "ch/gamma.md": "# Gamma\n",
            "ch/alpha.md": "# Alpha\n",
            "ch/beta.md": "# Beta\n",
        }))
        rows = [ln for ln in out.split("\n") if ln.startswith("[")]
        assert rows == [
            "[1 Alpha](#1-alpha)", "[2 Beta](#2-beta)", "[3 Gamma](#3-gamma)",
        ]

    def test_the_call_lines_indentation_is_kept_on_every_row(self, home):
        out = _body(_run(home, {
            "root.md": '# Root\n\n  __IMPORTSECTION__("ch/*.md")\n',
            "ch/a.md": "# A\n",
            "ch/b.md": "# B\n",
        }))
        assert "  [1 A](#1-a)\n  [2 B](#2-b)" in out

    def test_imports_and_links_share_one_section_sequence(self, home):
        """⚑ THE SHIPPED PAIRING, and it is deliberate: directives are IMPORTED
        inline, procedures are LINKED because they are load-on-demand, and the
        two calls run one continuous sequence over the parent document."""
        out = _body(_run(home, {
            "root.md": (
                '# Root\n\n## Directives\n__IMPORTSECTION__("d/*.md")\n'
                '\n## Procedures\n__LINKSECTION__("p/*.md")\n'
            ),
            "d/one.md": "# One\n",
            "d/two.md": "# Two\n",
            "p/three.md": "# Three\n",
        }))
        rows = [ln for ln in out.split("\n") if ln.startswith("[")]
        assert rows[:2] == ["[1 One](#1-one)", "[2 Two](#2-two)"]
        assert rows[2] == f"[3 Three]({(home / 'p' / 'three.md').as_posix()})"

    def test_a_call_line_is_not_import_only_so_its_file_keeps_its_section(self, home):
        """Like the ``[text](@path)`` form: the call leaves DISPLAY TEXT behind,
        and collapsing the file that carries it would delete the very table of
        contents the call exists to produce."""
        out = _body(_run(home, {
            "root.md": '@index.md\n',
            "index.md": '__IMPORTSECTION__("child.md")\n',
            "child.md": "# Child\n\nbody\n",
        }))
        assert "[1 Child](#1-child)" in out

    def test_an_at_inside_a_call_argument_is_not_a_bare_import(self, home, capsys):
        """The call is recognized FIRST and returns, so the argument text never
        reaches the ``@path`` scanner."""
        out = _body(_run(home, {"root.md": '# Root\n\n__IMPORTSECTION__("@box/c.md")\n'}))
        assert "@box" not in out
        assert "`@" not in out                     # not neutralized as a mention
        assert "unresolved import @box" not in capsys.readouterr().err


class TestTemplateCurrent:
    """``__CURRENT__`` — the section id of the document being processed."""

    def test_current_is_empty_at_the_root(self, home):
        """🛑 ROOT IS THE COMMON CASE: ``COLLECTION.md`` IS the root, so the very
        first id minted is ``1`` and not ``.1``."""
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("a.md")\n__IMPORTSECTION__("b.md")\n',
            "a.md": "# A\n",
            "b.md": "# B\n",
        }))
        assert "[1 A](#1-a)" in out and "[2 B](#2-b)" in out
        assert ".1 A" not in out

    def test_an_imported_document_mints_under_its_own_id(self, home):
        """An entry minted ``X`` carries ``__CURRENT__ == X`` while it is
        processed, so the calls nested inside it mint ``X.1``, ``X.2``, ..."""
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("mid.md")\n',
            "mid.md": '# Mid\n\n__IMPORTSECTION__("leaf.md")\n__IMPORTSECTION__("two.md")\n',
            "leaf.md": '# Leaf\n\n__IMPORTSECTION__("deep.md")\n',
            "two.md": "# Two\n",
            "deep.md": "# Deep\n",
        }))
        for expected in (
            "[1 Mid](#1-mid)",
            "[1.1 Leaf](#11-leaf)",
            "[1.2 Two](#12-two)",
            "[1.1.1 Deep](#111-deep)",
        ):
            assert expected in out, out
        assert "# 1.1.1 Deep" in out

    def test_an_explicit_source_overrides_current(self, home):
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("a.md", "7-3", "-")\n',
            "a.md": "# A\n",
        }))
        assert "[7-3-1 A](#7-3-1-a)" in out

    def test_the_flattener_records_each_documents_current(self, home):
        fl = flattener.Flattener()
        for rel, body in {
            "root.md": '# Root\n__IMPORTSECTION__("mid.md")\n',
            "mid.md": '# Mid\n__IMPORTSECTION__("leaf.md")\n',
            "leaf.md": "# Leaf\n",
        }.items():
            (home / rel).write_text(body, encoding="utf-8")
        fl.collect((home / "root.md").resolve())
        assert fl.current_id.get((home / "root.md").resolve(), "") == ""
        assert fl.current_id[(home / "mid.md").resolve()] == "1"
        assert fl.current_id[(home / "leaf.md").resolve()] == "1.1"


class TestTemplateGlobBase:
    """A glob is resolved relative to the file the call is WRITTEN IN — never
    the process CWD, which in a box is wherever the agent happened to stand."""

    def test_a_glob_follows_the_containing_file_not_the_cwd(self, home, monkeypatch):
        decoy = home / "decoy"
        (decoy / "ch").mkdir(parents=True)
        (decoy / "ch" / "wrong.md").write_text("# Wrong\n", encoding="utf-8")
        monkeypatch.chdir(decoy)
        out = _body(_run(home, {
            "root.md": '# Root\n\n@sub/index.md\n',
            "sub/index.md": '# Index\n\n__IMPORTSECTION__("ch/*.md")\n',
            "sub/ch/right.md": "# Right\n",
        }))
        assert "[1 Right](#1-right)" in out
        assert "Wrong" not in out

    def test_a_named_target_follows_the_containing_file_too(self, home, monkeypatch):
        """The same anchoring a bare ``@path`` already gets — the two must
        agree, or one form of the same relative path would resolve and the
        other would not."""
        monkeypatch.chdir(home)
        out = _body(_run(home, {
            "root.md": '# Root\n\n@sub/index.md\n',
            "sub/index.md": '# Index\n\n__IMPORTSECTION__("near.md")\n',
            "sub/near.md": "# Near\n",
        }))
        assert "[1 Near](#1-near)" in out


class TestTemplateTitleRewrite:
    """🛑 ONE replacement, at the title line's own offset — not every occurrence
    of the same text."""

    def test_a_second_occurrence_of_the_title_text_is_untouched(self, home):
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("child.md")\n',
            "child.md": (
                "# Child\n\nA contents block repeats it:\n\n# Child\n\n"
                "```\n# Child\n```\n"
            ),
        }))
        assert out.count("# 1 Child") == 1
        assert out.count("\n# Child") == 2       # the echo and the fenced one

    def test_an_indented_atx_title_is_still_matched(self, home):
        """⚑ ``old_title`` comes back STRIPPED while the line in the body is as
        authored, and an ATX heading may carry up to three leading spaces — so
        the match cannot assume the raw line equals the title."""
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("child.md")\n',
            "child.md": "   ## Child\n\nbody\n",
        }))
        assert "## 1 Child" in out

    def test_the_authored_indentation_is_put_back(self, home):
        """⚑ Asserted on the BODY rather than the artifact: a section's leading
        whitespace is stripped when it is joined into the output, so the
        document cannot show what the rewrite itself preserved."""
        (home / "child.md").write_text("   ## Child\n\nbody\n", encoding="utf-8")
        fl = flattener.Flattener()
        path = (home / "child.md").resolve()
        fl.collect(path)
        fl._retitle(path, "## Child", "## 1 Child")
        assert fl.sections[path].split("\n")[0] == "   ## 1 Child"

    def test_a_title_that_did_not_survive_processing_says_so(self, home, capsys):
        """The title is read off DISK and the body is the PROCESSED text, so a
        title line that processing rewrote is not there to re-title. Invisible
        in the artifact, hence said out loud."""
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("child.md")\n',
            "child.md": "# See @other.md\n\nbody\n",
            "other.md": "other body\n",
        }))
        assert "could not re-title" in capsys.readouterr().err
        assert "1 See" not in out

    def test_a_title_that_is_not_a_heading_is_still_re_titled(self, home):
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("child.md")\n',
            "child.md": "Just a paragraph.\n\nmore\n",
        }))
        assert "1 Just a paragraph." in out
        assert "[1 Just a paragraph.](#1-just-a-paragraph)" in out

    def test_one_file_imported_twice_keeps_one_title(self, home):
        """Import-once means ONE heading however many calls name the file, so the
        first call owns the title and every row reads it back — a row and the
        heading it points at can never disagree."""
        out = _body(_run(home, {
            "root.md": (
                '# Root\n\n__IMPORTSECTION__("child.md")\n'
                '__IMPORTSECTION__("child.md")\n'
            ),
            "child.md": "# Child\n\nbody\n",
        }))
        assert out.count("[1 Child](#1-child)") == 2
        assert out.count("# 1 Child") == 1


class TestTemplateMisses:
    """The manifest's absent side: a NAMED path that is not there is still
    watched, so a chapter that starts existing takes effect."""

    def _flatten(self, home, files: dict[str, str]) -> dict:
        for rel, body in files.items():
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        rc = flattener.flatten(
            str(home / "root.md"), str(home / "out.md"),
            manifest=str(home / "manifest.json"),
        )
        assert rc == 0
        return json.loads((home / "manifest.json").read_text(encoding="utf-8"))

    def test_a_missing_named_target_is_recorded_as_absent(self, home):
        man = self._flatten(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("gone.md")\n',
        })
        by_path = {e["path"]: e for e in man["inputs"]}
        assert by_path[str(home / "gone.md")] == {
            "path": str(home / "gone.md"), "absent": True,
        }

    def test_a_missing_link_target_is_recorded_too(self, home):
        man = self._flatten(home, {
            "root.md": '# Root\n\n__LINKSECTION__("gone.md")\n',
        })
        assert {"path": str(home / "gone.md"), "absent": True} in man["inputs"]

    def test_a_glob_matching_nothing_is_not_a_miss_of_a_named_path(self, home):
        """⚑ No path was NAMED, so there is nothing to watch and no spelling to
        invent for it."""
        man = self._flatten(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("ch/*.md")\n',
        })
        assert [e for e in man["inputs"] if e.get("absent")] == []

    def test_a_missing_target_is_skipped_without_a_warning(self, home, capsys):
        """Required-vs-optional lives in another layer; an absent OPTIONAL
        chapter must not shout on every launch."""
        _run(home, {"root.md": '# Root\n\n__IMPORTSECTION__("gone.md")\n'})
        assert capsys.readouterr().err == ""


class TestTemplateFailureModes:
    """A broken call is REPORTED, never fatal: a flatten that died would leave
    the box with no canon at all."""

    def test_a_malformed_call_warns_and_stays_literal(self, home, capsys):
        out = _body(_run(home, {"root.md": "# Root\n\n__IMPORT__(child)\n"}))
        assert "__IMPORT__(child)" in out
        assert "must be a literal" in capsys.readouterr().err

    def test_a_bad_title_format_warns_and_stays_literal(self, home, capsys):
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORT__("child.md", "1", ".", "no slot")\n',
            "child.md": "# Child\n",
        }))
        assert '__IMPORT__("child.md", "1", ".", "no slot")' in out
        assert "exactly one '@' symbol" in capsys.readouterr().err

    def test_a_raising_expression_warns_and_stays_literal(self, home, capsys):
        """🛑 Phase 3 is what first lets a DOCUMENT reach the ``{expr}``
        evaluator, and an expression can raise anything at all. The canon must
        survive one bad call."""
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORT__("child.md", "1", ".", "{boom} @")\n',
            "child.md": "# Child\n\nbody\n",
        }))
        assert '__IMPORT__("child.md", "1", ".", "{boom} @")' in out
        assert "NameError" in capsys.readouterr().err

    def test_the_existing_import_once_guard_still_holds_a_cycle(self, home, capsys):
        """🛑 REUSED, not rebuilt: ``Flattener.started`` is the cycle guard, and
        a cycle terminates with each file contributing exactly one section."""
        out = _body(_run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("a.md")\n',
            "a.md": '# A\n\n__IMPORTSECTION__("b.md")\n',
            "b.md": '# B\n\n__IMPORTSECTION__("a.md")\n',
        }))
        assert out.count("# 1.1 B") == 1
        assert out.count("# 1 A") == 1
        assert "[1 A](#1-a)" in out and "[1.1 B](#11-b)" in out

    def test_a_self_import_terminates(self, home, capsys):
        out = _body(_run(home, {"root.md": '# Root\n\n__IMPORTSECTION__("root.md")\n'}))
        assert "# Root" in out
        assert "import cycle" in capsys.readouterr().err

    def test_a_cycle_back_to_an_ancestor_says_so(self, home, capsys):
        """The ancestor's body does not exist to re-title yet, so the row has no
        heading to point at and disappears — invisible unless it is said."""
        _run(home, {
            "root.md": '# Root\n\n__IMPORTSECTION__("a.md")\n',
            "a.md": '# A\n\n__IMPORTSECTION__("root.md")\n',
        })
        assert "import cycle" in capsys.readouterr().err

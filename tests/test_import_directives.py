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
        assert "## guide_md" in out          # the resolvable link/section survives
        assert "# Guide" in out
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
        body = out.split("regenerated. -->\n", 1)[1]
        assert "a\nb" in body            # stripped block leaves no gap

    def test_generated_header_survives(self, home):
        out = _run(home, {"root.md": "<!-- gone -->\nkept"})
        assert out.startswith("<!-- GENERATED by kanibako")
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
        assert out.count("## shared_md") == 1
        assert out.count("shared once") == 1
        # both a and b point at the one shared section
        assert out.count("[shared.md](#shared_md)") == 2

    def test_cycle_terminates(self, home):
        # A deep cycle (>4 hops) still terminates: import-once, not a depth cap,
        # is the termination guarantee.
        out = _run(home, {
            "root.md": "@a.md",
            "a.md": "A @b.md",
            "b.md": "B @c.md",
            "c.md": "C @d.md",
            "d.md": "D @e.md",
            "e.md": "E @a.md",
        })
        for name in ("a", "b", "c", "d", "e"):
            assert out.count(f"## {name}_md") == 1

    def test_slug_collision_numbered(self, home):
        # ~/a/b.md and ~/a/b_md both normalise to a_b_md -> second gets a suffix.
        out = _run(home, {
            "root.md": "@a/b.md and @a/b_md",
            "a/b.md": "dotted",
            "a/b_md": "undated",
        })
        assert "## a_b_md" in out
        assert "## a_b_md_2" in out


class TestFullDepthResolution:
    def test_deep_chain_resolved_no_depth_cap(self, home, capsys):
        # A chain far deeper than the old four-hop cap resolves in full: every
        # hop, including the 5th, 6th and 7th, becomes a ## section + link, the
        # leaf content is present, and no depth warning is emitted.
        out = _run(home, {
            "root.md": "@d1.md",
            "d1.md": "@d2.md",
            "d2.md": "@d3.md",
            "d3.md": "@d4.md",
            "d4.md": "@d5.md",
            "d5.md": "@d6.md",
            "d6.md": "@d7.md",
            "d7.md": "leaf",
        })
        for n in range(1, 8):
            assert f"## d{n}_md" in out
            assert f"[d{n}.md](#d{n}_md)" in out
        assert "leaf" in out
        assert "depth>" not in capsys.readouterr().err


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

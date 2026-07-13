#!/usr/bin/env python3
"""Flatten a kanibako directive tree into one self-contained file.

kanibako delivers layered instruction content -- a read-only built-in guide plus
the user's own directives -- to every agent harness uniformly, without relying on
any agent's native import support and without clobbering the user's files. This
script is that flattener: given a SOURCE "kickoff" file and a DEST path it reads
the source, resolves every ``@path`` import it finds, and writes a single flat
document to DEST in which no live import directives remain.

Import *syntax* follows Claude Code's *documented* memory-import spec
(code.claude.com/docs/en/memory), so that claude reading the same directive
sources resolves identically to this flattener:

  * ``@path`` imports; both relative and absolute paths are allowed.
  * Relative paths resolve relative to the file containing the import, not cwd.
  * ``~`` expands to the home directory.
  * Import parsing skips Markdown code spans and fenced code blocks; a path
    wrapped in backticks stays literal.

We do NOT honour Claude's documented four-hop depth cap: that bound exists for
Anthropic's context budget, not ours, and kanibako flattens its own directive
tree. Imports resolve to FULL depth. Termination and cycle-safety are guaranteed
independently by import-once collection -- each file is collected exactly once,
keyed by its resolved path -- so a finite file tree always terminates and cycles
and diamonds dedup to a single section.

The OUTPUT format is kanibako's own (the spec does not describe flattening into a
file): rather than splice content in place, each imported file is emitted once as
a labelled ``## <slug>`` section and every reference to it becomes an in-document
fragment link ``[<as-written path>](#<slug>)``. Because the whole tree lands in
one file those fragments resolve for a human reader; the agent ingests the flat
text regardless. Files are imported once, keyed by resolved path, so a file
referenced from several places contributes a single section that all mentions
point at.

Usage: import-directives.py SOURCE DEST
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

CODEX_DOC_LIMIT = 32 * 1024  # codex silently truncates its instruction file here

# An import is ``@`` followed by a path, only at a word boundary so an address
# like ``name@host.com`` is not mistaken for an import. The path charset covers
# every documented example (``@README``, ``@package.json``,
# ``@docs/git-instructions.md``, ``@~/.claude/foo.md``).
IMPORT_RE = re.compile(r"(?<![\w@])@([\w./~-]+)")

# A fenced code block opens/closes with a line of >=3 backticks or tildes,
# optionally indented up to three spaces.
FENCE_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")

_TRAILING_PUNCT = ".,;:!?)]}'\""


def code_span_ranges(line: str) -> list[tuple[int, int]]:
    """Character ranges covered by inline code spans on a line.

    Follows the CommonMark rule: a run of N backticks opens a span that closes at
    the next run of exactly N backticks. Unclosed runs are treated as literal.
    """
    ranges: list[tuple[int, int]] = []
    i, n = 0, len(line)
    while i < n:
        if line[i] != "`":
            i += 1
            continue
        j = i
        while j < n and line[j] == "`":
            j += 1
        ticks = j - i
        k = j
        closed = False
        while k < n:
            if line[k] == "`":
                m = k
                while m < n and line[m] == "`":
                    m += 1
                if m - k == ticks:
                    ranges.append((i, m))
                    i = m
                    closed = True
                    break
                k = m
            else:
                k += 1
        if not closed:
            i = j
    return ranges


class Flattener:
    def __init__(self) -> None:
        self.sections: dict[Path, str] = {}   # resolved path -> flattened body
        self.order: list[Path] = []           # resolved path, first-reference order
        self.started: set[Path] = set()        # collection begun (import-once + cycle guard)
        self.slug_for: dict[Path, str] = {}    # resolved path -> slug
        self.slug_owner: dict[str, Path] = {}  # slug -> resolved path
        self.warnings: list[str] = []

    # -- slugs -------------------------------------------------------------
    def _slugify(self, path: Path) -> str:
        try:
            base = path.relative_to(Path.home()).as_posix()
        except ValueError:
            base = path.as_posix().lstrip("/")
        token = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_")
        return token or "section"

    def slug(self, path: Path) -> str:
        if path in self.slug_for:
            return self.slug_for[path]
        base = self._slugify(path)
        candidate = base
        n = 2
        while candidate in self.slug_owner and self.slug_owner[candidate] != path:
            candidate = f"{base}_{n}"
            n += 1
        self.slug_for[path] = candidate
        self.slug_owner[candidate] = path
        return candidate

    def anchor(self, path: Path) -> str:
        # GitHub-flavoured renderers derive a heading's fragment id by lowercasing
        # and our slug is already restricted to [A-Za-z0-9_], so lowercasing is
        # the whole rule -- the link resolves in-document without renderer-specific
        # logic.
        return self.slug(path).lower()

    # -- resolution --------------------------------------------------------
    def resolve(self, raw: str, importing_file: Path):
        """Return (resolved_path, effective_text, trailing) or None.

        ``trailing`` is any sentence punctuation trimmed off the end so that a
        mention like ``@foo.md.`` at the end of a sentence still resolves.
        """
        text = raw
        trailing = ""
        while True:
            candidate = os.path.expanduser(text)
            p = Path(candidate)
            if not p.is_absolute():
                p = importing_file.parent / candidate
            if p.is_file():
                return p.resolve(), text, trailing
            if text and text[-1] in _TRAILING_PUNCT:
                trailing = text[-1] + trailing
                text = text[:-1]
                continue
            return None

    # -- collection --------------------------------------------------------
    def collect(self, path: Path) -> None:
        if path in self.started:
            return
        self.started.add(path)
        self.order.append(path)
        self.slug(path)  # reserve the slug now so links resolve during recursion
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.warnings.append(f"unreadable: {path}: {exc}")
            self.sections[path] = f"<!-- kanibako: could not read {path}: {exc} -->"
            return
        self.sections[path] = self._process_text(raw, path)

    def _process_text(self, text: str, importing_file: Path) -> str:
        out: list[str] = []
        in_fence = False
        fence_char = ""
        fence_len = 0
        for line in text.splitlines():
            fm = FENCE_RE.match(line)
            if in_fence:
                out.append(line)
                if (
                    fm
                    and fm.group("fence")[0] == fence_char
                    and len(fm.group("fence")) >= fence_len
                    and not fm.group("info").strip()
                ):
                    in_fence = False
                continue
            if fm:
                in_fence = True
                fence_char = fm.group("fence")[0]
                fence_len = len(fm.group("fence"))
                out.append(line)
                continue
            out.append(self._process_line(line, importing_file))
        return "\n".join(out)

    def _process_line(self, line: str, importing_file: Path) -> str:
        spans = code_span_ranges(line)

        def repl(m: "re.Match[str]") -> str:
            if any(s <= m.start() < e for s, e in spans):
                return m.group(0)  # inside a code span -> literal
            resolved = self.resolve(m.group(1), importing_file)
            if resolved is None:
                # Target file missing. Neutralize the mention rather than leave a
                # raw live ``@path`` in the flat output: wrap it in backticks so no
                # agent -- including a claude re-reading the flattened file -- can
                # treat it as a live import, while the path stays visible to the
                # reader. Keeps flattening idempotent (a second pass changes
                # nothing). Trailing sentence punctuation stays outside the ticks.
                as_written = m.group(1)
                trailing = ""
                while as_written and as_written[-1] in _TRAILING_PUNCT:
                    trailing = as_written[-1] + trailing
                    as_written = as_written[:-1]
                return f"`@{as_written}`{trailing}"
            path, as_written, trailing = resolved
            self.collect(path)
            return f"[{as_written}](#{self.anchor(path)}){trailing}"

        return IMPORT_RE.sub(repl, line)

    # -- assembly ----------------------------------------------------------
    def render(self, source: Path) -> str:
        header = (
            "<!-- GENERATED by kanibako (import-directives.py) -- do not edit.\n"
            f"     Flattened from {source} at box start / on reload.\n"
            "     Edit the directive sources under ~/playbook instead; changes\n"
            "     take effect the next time this file is regenerated. -->\n"
        )
        parts = [self.sections[source].rstrip()]
        for path in self.order:
            if path == source:
                continue
            parts.append("")
            parts.append("---")
            parts.append(f"## {self.slug(path)}")
            parts.append("")
            parts.append(self.sections[path].strip())
        return header + "\n".join(parts).rstrip() + "\n"


def flatten(source: str, dest: str | None, *, additional_context: bool = False) -> int:
    """Flatten *source* and deliver the result.

    Three output modes:
      * ``additional_context=True`` — print the flattened content as a Claude/Codex
        ``SessionStart`` hook payload (``hookSpecificOutput.additionalContext``) to
        stdout, for injection into the session (claude/codex delivery).
      * ``dest`` is ``"-"`` or ``None`` — print the raw flattened markdown to stdout.
      * otherwise — write the flattened markdown to the *dest* file, mode 644
        (goose delivery: its ``.additionalContext.md`` context file).
    """
    src = Path(os.path.expanduser(source))
    if not src.is_file():
        sys.stderr.write(f"import-directives: source not found: {src}\n")
        return 2
    src = src.resolve()

    fl = Flattener()
    fl.collect(src)
    result = fl.render(src)

    size = len(result.encode("utf-8"))
    if size > CODEX_DOC_LIMIT:
        fl.warnings.append(
            f"output {size}B exceeds the codex {CODEX_DOC_LIMIT}B cap; codex will truncate"
        )

    if additional_context:
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": result,
                    }
                }
            )
        )
    elif dest is None or dest == "-":
        sys.stdout.write(result)
    else:
        dest_path = Path(os.path.expanduser(dest))
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(result, encoding="utf-8")
        try:
            os.chmod(dest_path, 0o644)
        except OSError:
            pass

    for w in fl.warnings:
        sys.stderr.write(f"import-directives: {w}\n")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--additional-context"]
    additional_context = "--additional-context" in argv[1:]
    if additional_context:
        if len(args) != 1:
            sys.stderr.write("usage: import-directives.py --additional-context SOURCE\n")
            return 2
        return flatten(args[0], None, additional_context=True)
    if len(args) != 2:
        sys.stderr.write("usage: import-directives.py SOURCE DEST   (DEST '-' = stdout)\n")
        return 2
    return flatten(args[0], args[1])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

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

HTML comments are STRIPPED from the output -- an output-format decision of the
same kind as the section/fragment shape above; the import *syntax* still matches
the spec. Directive sources use comments for authoring guidance aimed at whoever
EDITS the template, which is noise in every runtime session and, worse, makes an
example ``@path`` inside a comment resolve as a LIVE import. Stripping removes
both problems. Comments are removed only OUTSIDE fenced code blocks, so a comment
shown *as* example markdown survives; the generated header is added after
processing and is unaffected. Sources keep their comments -- only the flattened
artifact is clean.

In FILE mode the flattener also leaves a MANIFEST -- a receipt naming every file
this render read (with the sha256 of the bytes it read) and every import that
resolved to nothing. The box supervisor polls that receipt and re-flattens when
any of it moves, so a directive edited mid-container-life reaches the agent's
instruction slot instead of sitting stale until the next launch.

Usage: import-directives.py SOURCE DEST [--manifest PATH]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

CODEX_DOC_LIMIT = 32 * 1024  # codex silently truncates its instruction file here

#: Manifest format this flattener emits.  ⚑ The READER (``kanibako.box_supervisor``,
#: ``DIRECTIVE_MANIFEST_VERSION``) hardcodes the version it understands: the two are
#: separate processes and cannot share a constant, so a skew is made SAFE rather than
#: prevented -- an unrecognised version reads as "stale", costing one re-flatten.
MANIFEST_VERSION = 1

# An import is ``@`` followed by a path, only at a word boundary so an address
# like ``name@host.com`` is not mistaken for an import. The path charset covers
# every documented example (``@README``, ``@package.json``,
# ``@docs/git-instructions.md``, ``@~/.claude/foo.md``).
IMPORT_RE = re.compile(r"(?<![\w@])@([\w./~-]+)")

# A fenced code block opens/closes with a line of >=3 backticks or tildes,
# optionally indented up to three spaces.
FENCE_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")

# HTML comment delimiters. Tracked with open/close STATE rather than a per-line
# regex because a comment may span many lines or sit inline mid-line.
COMMENT_OPEN = "<!--"
COMMENT_CLOSE = "-->"

_TRAILING_PUNCT = ".,;:!?)]}'\""


def atomic_write_text(path: Path, data: str, mode: int = 0o644) -> None:
    """Write *data* to *path* via a same-directory temp file + ``os.replace``.

    A harness reading a half-written instruction file is strictly worse than one
    reading a stale file, so neither the flattened output nor its manifest is ever
    visible partially written: a reader sees the old file or the new one.

    ⚑ This duplicates :func:`kanibako._atomic.atomic_write_text` on purpose. This
    script is a STANDALONE box-side artifact -- it runs as ``python3 <path>`` inside
    the box, where the kanibako package is a read-only bind that is deliberately NOT
    on ``PYTHONPATH`` (``box_supervisor.scrub_bootstrap_pythonpath`` strips it before
    any child starts), so it can import nothing from kanibako. Keep the two in step.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp creates 0600; the instruction slot is read by the agent under this
        # same uid but has always been 0644 -- keep it.
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


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


def strip_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Remove HTML-comment spans from *line*.

    Returns the surviving text plus the comment state at end of line. Handles a
    comment that opens and/or closes mid-line, several comments on one line, and
    a comment that spans lines (via the carried ``in_comment`` flag).
    """
    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        if in_comment:
            j = line.find(COMMENT_CLOSE, i)
            if j < 0:
                break                      # rest of the line is inside the comment
            i = j + len(COMMENT_CLOSE)
            in_comment = False
        else:
            j = line.find(COMMENT_OPEN, i)
            if j < 0:
                out.append(line[i:])
                break
            out.append(line[i:j])
            i = j + len(COMMENT_OPEN)
            in_comment = True
    return "".join(out), in_comment


class Flattener:
    def __init__(self) -> None:
        self.sections: dict[Path, str] = {}   # resolved path -> flattened body
        self.order: list[Path] = []           # resolved path, first-reference order
        self.started: set[Path] = set()        # collection begun (import-once + cycle guard)
        self.slug_for: dict[Path, str] = {}    # resolved path -> slug
        self.slug_owner: dict[str, Path] = {}  # slug -> resolved path
        self.warnings: list[str] = []
        # -- manifest bookkeeping (the RECEIPT this render leaves behind) ------
        # ``digests`` is keyed by the SAME resolved path as ``order`` and holds the
        # sha256 of the bytes THIS run read -- taken at read time, not re-read
        # afterwards, so the receipt cannot describe content the render never saw.
        # ``misses`` is the other side: paths an import named that yielded nothing.
        self.digests: dict[Path, str] = {}
        self.misses: list[Path] = []

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

        On a TOTAL miss every candidate path tried is recorded in :attr:`misses`
        (the manifest's absent side). Candidates tried before a HIT are not: only
        the spelling that actually resolved feeds the render, so only it can change
        it. A miss is recorded even though it contributes no content precisely
        because it may LATER contribute some -- see :func:`build_manifest`.
        """
        text = raw
        trailing = ""
        tried: list[Path] = []
        while True:
            candidate = os.path.expanduser(text)
            p = Path(candidate)
            if not p.is_absolute():
                p = importing_file.parent / candidate
            if p.is_file():
                return p.resolve(), text, trailing
            tried.append(p)
            if text and text[-1] in _TRAILING_PUNCT:
                trailing = text[-1] + trailing
                text = text[:-1]
                continue
            self._record_misses(tried)
            return None

    def _record_misses(self, tried: list[Path]) -> None:
        """Record every candidate of one unresolved import, deduped, in first-seen order."""
        for p in tried:
            # ``resolve()`` on a non-existent path still normalises it (and follows
            # symlinks on the ancestors that DO exist), which is what makes the
            # dedup and the watcher's later probe agree on one spelling.
            resolved = p.resolve()
            if resolved not in self.misses:
                self.misses.append(resolved)

    # -- collection --------------------------------------------------------
    def collect(self, path: Path) -> None:
        if path in self.started:
            return
        self.started.add(path)
        self.order.append(path)
        self.slug(path)  # reserve the slug now so links resolve during recursion
        try:
            # BYTES, hashed once, then decoded: the receipt must describe exactly the
            # content that fed this render, and a second read to hash it could see a
            # different file.  ``str.splitlines`` (below) breaks on \r\n and \r just
            # as text mode's newline translation would, so the output is unchanged.
            raw_bytes = path.read_bytes()
        except OSError as exc:
            self.warnings.append(f"unreadable: {path}: {exc}")
            self.sections[path] = f"<!-- kanibako: could not read {path}: {exc} -->"
            return
        self.digests[path] = hashlib.sha256(raw_bytes).hexdigest()
        self.sections[path] = self._process_text(raw_bytes.decode("utf-8"), path)

    def _process_text(self, text: str, importing_file: Path) -> str:
        out: list[str] = []
        in_fence = False
        fence_char = ""
        fence_len = 0
        in_comment = False
        for raw_line in text.splitlines():
            if in_fence:
                # Inside a fence everything is literal -- including any comment
                # delimiters, which is why comment state is not touched here.
                out.append(raw_line)
                fm = FENCE_RE.match(raw_line)
                if (
                    fm
                    and fm.group("fence")[0] == fence_char
                    and len(fm.group("fence")) >= fence_len
                    and not fm.group("info").strip()
                ):
                    in_fence = False
                continue
            # Outside a fence, drop comment content BEFORE anything else, so an
            # example ``@path`` inside a comment never reaches import parsing.
            was_in_comment = in_comment
            line, in_comment = strip_comments(raw_line, in_comment)
            if (was_in_comment or line != raw_line) and not line.strip():
                continue          # the whole line was comment -> emit nothing
            fm = FENCE_RE.match(line)
            if fm:
                in_fence = True
                fence_char = fm.group("fence")[0]
                fence_len = len(fm.group("fence"))
                out.append(line)
                continue
            out.append(self._process_line(line, importing_file))
        if in_comment:
            # An unterminated ``<!--`` swallows the file to EOF: comment state is
            # carried ACROSS lines, so everything after the stray opener -- headings,
            # rules, live imports -- is silently dropped from the flattened output.
            # That is invisible in the artifact (the content simply is not there), so
            # say it out loud rather than let a future directive edit lose a chapter.
            self.warnings.append(
                f"unterminated HTML comment in {importing_file}: everything after "
                "the last '<!--' was dropped (add the closing '-->')"
            )
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
                # SAY SO. Neutralizing the mention makes the failure invisible in the
                # artifact -- the flat file simply lacks the content, and the launch
                # shim's ``|| true`` swallows the exit status -- so a mis-pathed
                # directive tree would otherwise degrade in total silence. This is
                # the only signal that an import went nowhere.
                self.warnings.append(
                    f"unresolved import @{as_written} in {importing_file}"
                )
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
            "     Edit the directive sources under ~/canon instead; changes\n"
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


def build_manifest(fl: Flattener, seed: Path, dest: Path, output: str) -> dict:
    """The RECEIPT for one render: what it read, and what it produced.

    Derived, never authoritative -- losing it costs exactly one re-flatten, and every
    way of failing to read it means the same thing ("re-flatten"), which is what makes
    a watcher safe to run inside PID 1.

    ``inputs`` carries BOTH sides of the collection:

    * a file that contributed content -> ``{path, sha256}`` (the bytes THIS run read);
    * an import that yielded nothing -> ``{path, absent: true}``.

    🛑 The absent side is load-bearing, not decoration. A watcher that re-checks only
    the files it found can never notice a MISSING file appearing, because nothing it
    watches moved -- and a directive that starts existing is exactly the edit a user
    expects to take effect. ``absent`` means "no content was obtained from this path",
    so the watcher's test is "can it be read NOW", which keeps a directory or a
    still-unreadable file from firing every tick forever.

    🛑 No mtime and no size, deliberately: sources span an NFS home and read-only
    package binds, where mtime is not comparable across the boundary and a package
    upgrade can land new content under a preserved one. Recording only what the check
    uses is what stops a later reader reaching for a field that lies.

    ⚑ ``seed``/``dest`` are the paths AS GIVEN (expanded, not symlink-resolved), so
    they compare equal to what the launcher passes the watcher. The seed's resolved
    form appears in ``inputs`` like any other file.
    """
    resolved = set(fl.order)
    inputs: list[dict] = []
    for path in fl.order:
        digest = fl.digests.get(path)
        if digest is None:
            # Collected but unreadable: it contributed no content, so it is an
            # absence that may end -- the same shape, and the same watch, as an
            # import that pointed nowhere.
            inputs.append({"path": str(path), "absent": True})
        else:
            inputs.append({"path": str(path), "sha256": digest})
    for path in fl.misses:
        if path in resolved:
            continue   # the same path resolved elsewhere in this run; it is a hit
        inputs.append({"path": str(path), "absent": True})
    return {
        "version": MANIFEST_VERSION,
        "seed": str(seed),
        "dest": str(dest),
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "inputs": inputs,
    }


def flatten(
    source: str,
    dest: str | None,
    *,
    additional_context: bool = False,
    manifest: str | None = None,
) -> int:
    """Flatten *source* and deliver the result.

    Three output modes:
      * ``additional_context=True`` — print the flattened content as a Claude/Codex
        ``SessionStart`` hook payload (``hookSpecificOutput.additionalContext``) to
        stdout, for injection into the session (claude/codex delivery).
      * ``dest`` is ``"-"`` or ``None`` — print the raw flattened markdown to stdout.
      * otherwise — write the flattened markdown to the *dest* file, mode 644
        (goose delivery: its ``.additionalContext.md`` context file).

    *manifest* (FILE mode only) is where the render's RECEIPT is written; see
    :func:`build_manifest`. The other two modes write no DEST, so they have no
    receipt to leave.
    """
    given = Path(os.path.expanduser(source))
    if not given.is_file():
        sys.stderr.write(f"import-directives: source not found: {given}\n")
        return 2
    src = given.resolve()

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
        if manifest is not None:
            fl.warnings.append(
                "--manifest needs a DEST file to describe; nothing written"
            )
        sys.stdout.write(result)
    else:
        dest_path = Path(os.path.expanduser(dest))
        # RENDERED-OUTPUT GATE: a re-flatten whose result is byte-identical to what is
        # already there writes NOTHING.  A directive edit that does not survive into the
        # flattened form (a comment, whitespace) moves the input hashes but not the
        # artifact, and rewriting an unchanged instruction file only invites a harness
        # to reload it for no reason.  The MANIFEST is still refreshed below — its input
        # hashes DID move, and a receipt left describing the old ones would make every
        # later check read "stale" forever.
        try:
            unchanged = dest_path.read_text(encoding="utf-8") == result
        except (OSError, UnicodeDecodeError):
            unchanged = False
        if not unchanged:
            atomic_write_text(dest_path, result)
        if manifest is not None:
            # DEST FIRST, receipt second, always.  The reverse order can strand a
            # receipt that claims an output which was never written: the watcher
            # would then read "inputs unchanged, DEST differs" -- its hand-edited
            # verdict -- and leave the wrong file in place indefinitely.
            atomic_write_text(
                Path(os.path.expanduser(manifest)),
                json.dumps(build_manifest(fl, given, dest_path, result), indent=2) + "\n",
            )

    for w in fl.warnings:
        sys.stderr.write(f"import-directives: {w}\n")
    return 0


_USAGE = (
    "usage: import-directives.py SOURCE DEST [--manifest PATH]   (DEST '-' = stdout)\n"
    "       import-directives.py --additional-context SOURCE\n"
)


def main(argv: list[str]) -> int:
    rest = argv[1:]
    additional_context = False
    manifest: str | None = None
    args: list[str] = []
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--additional-context":
            additional_context = True
        elif arg == "--manifest":
            if i + 1 >= len(rest):
                sys.stderr.write("import-directives: --manifest needs a path\n")
                sys.stderr.write(_USAGE)
                return 2
            manifest = rest[i + 1]
            i += 1
        else:
            args.append(arg)
        i += 1
    if additional_context:
        # REFUSED, not ignored: the hook mode writes no DEST, so a receipt for it
        # would describe a file nobody maintains.  Say so rather than accept a
        # combination that cannot mean anything.
        if manifest is not None or len(args) != 1:
            sys.stderr.write(_USAGE)
            return 2
        return flatten(args[0], None, additional_context=True)
    if len(args) != 2:
        sys.stderr.write(_USAGE)
        return 2
    return flatten(args[0], args[1], manifest=manifest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

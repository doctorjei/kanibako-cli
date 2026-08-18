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

kanibako adds ONE form on top of that spec: a Markdown link whose TARGET begins
with ``@`` -- ``[Display Text](@path/file.md)``. BOTH forms INCLUDE the file;
only LINKING differs. A bare ``@path`` appends its file's text and creates no
link and no heading (unchanged). The link form additionally gives the included
file a GENERATED HEADING and rewrites the row into an in-document fragment link
that resolves to it, so an authored table of contents survives flattening as a
working table of contents. Since claude's own resolver reads ``@path`` anywhere
on a line, the link form still imports when claude reads the SOURCE directly.

We do NOT honour Claude's documented four-hop depth cap: that bound exists for
Anthropic's context budget, not ours, and kanibako flattens its own directive
tree. Imports resolve to FULL depth. Termination and cycle-safety are guaranteed
independently by import-once collection -- each file is collected exactly once,
keyed by its resolved path -- so a finite file tree always terminates and cycles
and diamonds dedup to a single section.

The OUTPUT format is kanibako's own (the spec does not describe flattening into a
file): rather than splice content in place, the source body is followed by each
imported file's body under its own content, sections separated by ``---``.
A BARE ``@path`` mention becomes an in-document fragment link
``[<as-written path>](#<slug>)`` and generates no heading, so that anchor does
not resolve -- accepted, and unchanged: the artifact is agent-ingested text, and
the link text still names the source it came from. A ``[text](@path)`` link is
the form that DOES generate one: the included file's section is headed
``#`` * depth + " " + ``{number} {display text}``, and the row is rewritten to
point at that heading's GitHub-derived fragment id (:func:`gfm_anchor`). Depth is
the level of the markdown heading ENCLOSING the link's list, plus that row's
nesting RELATIVE TO the shallowest row in the same list; a link outside a list
takes the enclosing level exactly. ⚑ Relative, not absolute: directive numbering
is FILE-LOCAL and restarts inside every file, so a number read as an absolute
depth would emit an H1 in the middle of an H2 chapter -- and, just as wrongly,
bury a `1.1` list two levels under the ``## Contents`` heading it was written
beneath. Two kinds of file contribute NO section: a
functionally-empty one (only comments and whitespace survive processing) and an
imports-only index (nothing but bare import lines) -- classified DURING
processing, never by a regex over the rendered output. Their imports are still
collected and their children emitted transitively, and they are still read,
hashed and recorded in the manifest, so an edit that gives one real content
re-triggers a flatten. Files are imported once, keyed by resolved path, so a file
referenced from several places contributes a single section that all mentions
point at.

⚑ EXCLUSION AND RENUMBERING ARE ONE MECHANISM, which is what makes rendering
TWO-PASS. A ``[text](@path)`` row whose target is excluded DISAPPEARS, and the
surviving rows RENUMBER -- so no number, no heading and no fragment id can be
emitted until every file has been read and classified. Pass one collects and
leaves a placeholder where each row will go; pass two (:meth:`Flattener.render`
-> :meth:`Flattener._finalize_links`) numbers the survivors, assigns the ids and
substitutes. A one-pass emit would produce headings that disagree with their own
table of contents.

HTML comments are STRIPPED from the output -- an output-format decision of the
same kind as the section/fragment shape above; the import *syntax* still matches
the spec. Directive sources use comments for authoring guidance aimed at whoever
EDITS the template, which is noise in every runtime session and, worse, makes an
example ``@path`` inside a comment resolve as a LIVE import. Stripping removes
both problems. Comments are removed only OUTSIDE fenced code blocks, so a comment
shown *as* example markdown survives; the generated header comment is written at
render time, after stripping, which is why it alone survives into the artifact.
Sources keep their comments -- only the flattened artifact is clean.

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

#: Size at which the flattened artifact earns a WARNING -- never a hard gate, and
#: never a claim about one tool. Every harness caps the instruction file it will
#: read, and they do not agree on where: codex truncates at 32KiB, kimi at 40KiB,
#: and a box may run any of them. So this is the SMALLEST cap we know of, held as a
#: conservative "some harness would start dropping content around here" threshold.
#: Naming a specific agent in the message was wrong twice over -- the constant is
#: consulted for every harness, and on a box running a roomier one the warning fired
#: as a false alarm about a tool that was not even present.
#: ⚑ The message below spells this size out in words (Jei's wording, verbatim); the
#: two are kept in step by hand, and by a test that asserts both.
DIRECTIVE_SIZE_WARN_LIMIT = 32 * 1024

#: Manifest format this flattener emits.  ⚑ The READER (``kanibako.box_supervisor``,
#: ``DIRECTIVE_MANIFEST_VERSION``) hardcodes the version it understands: the two are
#: separate processes and cannot share a constant, so a skew is made SAFE rather than
#: prevented -- an unrecognised version reads as "stale", costing one re-flatten.
MANIFEST_VERSION = 1

# An import is ``@`` followed by a path, only at a word boundary so an address
# like ``name@host.com`` is not mistaken for an import. The path charset covers
# every documented example (``@README``, ``@package.json``,
# ``@docs/git-instructions.md``, ``@~/.claude/foo.md``).
IMPORT_PATH = r"[\w./~-]+"
IMPORT_RE = re.compile(rf"(?<![\w@])@(?P<path>{IMPORT_PATH})")

# kanibako's own form: a Markdown link whose TARGET begins with ``@``. The
# leading ``@`` is the whole marker -- an ordinary ``[text](path)`` link is not
# an import and is left alone.
LINK_RE = re.compile(rf"\[(?P<text>[^\]\n]+)\]\(@(?P<lpath>{IMPORT_PATH})\)")

# Both forms in ONE scan, link first so ``[text](@path)`` is consumed whole
# rather than being seen as a bare ``@path`` sitting inside parentheses. The
# branch that matched is told apart by which named group is set.
MENTION_RE = re.compile(f"{LINK_RE.pattern}|{IMPORT_RE.pattern}")

# A table-of-contents ROW: a list number at the start of a line, immediately
# followed by the link. ⚑ This only LEXES the number; every use of its VALUE
# goes through :func:`assign_section_numbers`, the one number seam.
NUMBERED_ROW_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<num>\d+(?:\.\d+)*\.?)(?P<gap>[ \t]+)")

# Placeholder standing in for one not-yet-numbered row between the two render
# passes. ``\x00`` cannot occur in a directive source we would flatten and is
# not whitespace, so a placeholder neither collides with authored text nor
# changes a line's empty/non-empty classification.
_TOKEN = "\x00kanibako-row:{}\x00"
_TOKEN_RE = re.compile(r"\x00kanibako-row:(\d+)\x00")

# An ATX heading: up to three spaces, 1-6 hashes, then a space or end of line.
# ⚑ This is what a generated heading's depth is measured AGAINST, so it is read
# from the INCLUDING file's own text -- after comment stripping and outside
# fences, exactly like an import -- while that file is processed, never by
# re-parsing the rendered output.
ATX_HEADING_RE = re.compile(r"^ {0,3}(?P<hashes>#{1,6})(?:\s|$)")

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


def gfm_anchor(text: str) -> str:
    """Derive a heading's fragment id the way a GitHub-flavoured renderer does.

    Measured against GitHub, and reproduced exactly: lowercase, spaces to ``-``,
    then drop every character that is neither a word character nor ``-``.

    🛑 We control the TARGET we write but NOT the id the renderer computes -- an
    id cannot be declared, only derived from the heading text -- so this function
    is the whole reason a generated link resolves at all. Three consequences that
    look like defects and are not:

      * ``.`` VANISHES, so depth is invisible in the id: ``1.1.2 Baz Man`` and
        ``11.2 Baz Man`` both derive ``112-baz-man`` (see :func:`Flattener._unique_anchor`).
      * ``&`` yields a DOUBLE hyphen -- ``1.1 Identity & Environment`` ->
        ``11-identity--environment`` -- because the spaces around it become
        hyphens and the ``&`` itself is dropped. That is GitHub's behaviour; the
        link resolves.
      * Parentheses are dropped: ``1. Bible (Core)`` -> ``1-bible-core``.
    """
    return re.sub(r"[^\w-]", "", text.strip().lower().replace(" ", "-"))


#: Markdown has no ``#######``. A generated heading is clamped here rather than
#: emitted invalid: past six levels of nesting an included section stops getting
#: DEEPER and sits alongside its parent instead. Depth is a reading aid; a
#: heading that renders as literal text is not.
MAX_HEADING_DEPTH = 6


def assign_section_numbers(
    rows: list[tuple[str | None, bool]],
    enclosing_level: int = 1,
) -> list[tuple[str | None, int] | None]:
    """THE one place a section number is parsed, renumbered or formatted.

    *rows* are ONE LIST's generated rows in document order -- the run of rows
    under a single enclosing heading -- each ``(authored number or None,
    dropped)``; ``None`` marks a link that sits outside a list.
    *enclosing_level* is the level of the markdown heading that list was written
    under. Returns one entry per row: ``None`` if the row is dropped, else
    ``(number text or None, heading depth)``.

    ⚑ SINGLE SEAM, DELIBERATELY (design §5d). The numbers are AUTHORED today and
    may later be COMPUTED from list nesting instead; every caller here asks only
    "what number and what depth does this row get", so that change replaces this
    function's body and touches nothing else. Do not parse, split, join or format
    a number anywhere outside it.

    Four rules live here:

    * **Depth is the ENCLOSING HEADING LEVEL plus the row's nesting RELATIVE to
      the shallowest row in the same list** -- ``enclosing + parts(row) -
      min(parts)``. Clamped to 1..:data:`MAX_HEADING_DEPTH`.
      🛑 RELATIVE, never absolute. Directive numbering is FILE-LOCAL and restarts
      inside every file, so an absolute reading breaks in BOTH directions: a file
      whose table of contents reads ``1.`` ``2.`` ``3.`` under a ``## Library``
      heading would head its includes at H1, ABOVE the section they came from,
      while a ``1.1`` list under a ``## Contents`` heading would be buried at H3
      when its author put it at H2. Subtracting the list's own shallowest depth
      makes the number express nesting WITHIN the list and nothing else.
      ⚑ **Composition is carried by the AUTHORED headings, not computed here.**
      An included file's body is emitted verbatim, so the ``### Rules`` it wrote
      is still ``###`` in the flattened document; measuring against that heading
      is what makes an include from a file that was itself included land at the
      right depth. The one thing this function cannot read off the page is the
      enclosing level for a link with NO heading above it -- there the caller
      supplies the file's OWN base level (see :meth:`Flattener._finalize_links`),
      which is the same section-level rule applied one frame out.
      ⚑ ``min(parts)`` is taken over the list AS AUTHORED, dropped rows included,
      so excluding a chapter never silently promotes its siblings a level.
    * **The terminal dot is a depth MARKER, not inconsistency** -- a single-part
      number carries one (``1.`` ``2.`` ``3.``), a multi-part number does not
      (``1.1`` ``2.4``). It is FORMATTING, applied to whatever number a row ends
      up with, which is what keeps it right across a renumber.
    * **Renumbering is minimal, not positional.** A survivor's last part drops by
      the number of siblings dropped BEFORE it, and descendants follow their
      parent's new number. Authored gaps therefore survive untouched when nothing
      is dropped (``1.1`` beside ``1.1.2`` and ``11.2`` all keep their numbers),
      which a positional resequence would silently rewrite.

    A row outside a list takes the ENCLOSING LEVEL exactly (its relative term is
    zero), so a loose include is headed as a sibling of the section it was
    written in -- which is also what the numbered rows of that same list get when
    they are at its shallowest depth.

    ⚑ RE-DERIVED under this rule, and it still holds: a renumber changes a row's
    DIGITS, never its part COUNT, and ``min(parts)`` is taken over the authored
    list, so **no surviving row's depth moves when a sibling drops** -- the
    terminal dot is right by construction rather than by a rule applied after the
    fact. What DOES move depth is the enclosing level: the identical list read
    under a ``###`` heading heads one level deeper than under a ``##`` one. Depth
    never reaches an id, so no target changes either way. Promoting
    a row whose parent dropped WOULD change depth -- it is deliberately not done,
    because nothing in the design says what number the promoted row should take
    and two survivors would compete for the same one. The formatter is also what
    normalises a malformed authored number: ``1.1.`` renders as ``1.1``.
    """
    def parse(text: str) -> list[int]:
        return [int(part) for part in text.rstrip(".").split(".")]

    def form(parts: list[int]) -> str:
        # The terminal dot, and the only place it is written.
        body = ".".join(str(part) for part in parts)
        return f"{body}." if len(parts) == 1 else body

    parsed = [None if number is None else parse(number) for number, _ in rows]
    depths = [len(parts) for parts in parsed if parts is not None]
    shallowest = min(depths) if depths else 1

    def level(parts_count: int) -> int:
        return min(max(enclosing_level + parts_count - shallowest, 1), MAX_HEADING_DEPTH)

    dropped_before: dict[tuple[int, ...], int] = {}   # parent key -> drops so far
    renumbered: dict[tuple[int, ...], tuple[int, ...]] = {}  # authored -> final
    out: list[tuple[str | None, int] | None] = []
    for (_, dropped), parts in zip(rows, parsed):
        if parts is None:
            out.append(None if dropped else (None, level(shallowest)))
            continue
        parent = tuple(parts[:-1])
        if dropped:
            dropped_before[parent] = dropped_before.get(parent, 0) + 1
            out.append(None)
            continue
        # The parent's FINAL number if the parent row is itself a row of this
        # file that survived; its authored one otherwise (a top-level row has no
        # parent row, and a row whose parent was dropped keeps what it was given
        # rather than inventing a promotion nobody specified).
        final = list(renumbered.get(parent, parent))
        final.append(parts[-1] - dropped_before.get(parent, 0))
        renumbered[tuple(parts)] = tuple(final)
        out.append((form(final), level(len(final))))
    return out


def split_trailing_punct(text: str) -> tuple[str, str]:
    """Split sentence punctuation off the end of a path, e.g. ``foo.md.``."""
    trailing = ""
    while text and text[-1] in _TRAILING_PUNCT:
        trailing = text[-1] + trailing
        text = text[:-1]
    return text, trailing


class _Row:
    """One ``[text](@path)`` occurrence, recorded in pass one, resolved in pass two.

    ⚑ Written out by hand rather than as a ``@dataclass``: this script is loaded
    from its FILE (``spec_from_file_location`` in the box and in the tests), and a
    module loaded that way is not in ``sys.modules``, which is where
    ``dataclasses`` looks its own annotations up -- decorating a class here raises
    ``AttributeError`` before the script can run at all. Measured, not guessed.
    """
    __slots__ = ("container", "target", "text", "number", "gap", "enclosing", "listing")

    def __init__(
        self,
        container: Path,   # the file the row was written in
        target: Path,      # the file it includes
        text: str,         # display text, as authored
        number: str | None,  # authored list number; None if it is not in a list
        gap: str,          # the whitespace between number and link, as authored
        enclosing: int | None,  # level of the heading above it; None = no heading yet
        listing: int,      # which heading's section it sits in, counted per file
    ) -> None:
        self.container = container
        self.target = target
        self.text = text
        self.number = number
        self.gap = gap
        self.enclosing = enclosing
        self.listing = listing


class Flattener:
    def __init__(self) -> None:
        self.sections: dict[Path, str] = {}   # resolved path -> flattened body
        self.order: list[Path] = []           # resolved path, first-reference order
        self.started: set[Path] = set()        # collection begun (import-once + cycle guard)
        self.slug_for: dict[Path, str] = {}    # resolved path -> slug
        self.slug_owner: dict[str, Path] = {}  # slug -> resolved path
        self.warnings: list[str] = []
        # Per-file CLASSIFICATION, decided during processing (see _process_text):
        # "content" gets a section in the render; "empty" and "imports_only"
        # contribute their imports but no section.
        self.kind: dict[Path, str] = {}
        # -- generated rows/headings (the ``[text](@path)`` form) --------------
        # Recorded in pass one, in discovery order, and left as placeholders in
        # the bodies; pass two (:meth:`_finalize_links`) fills the three maps.
        self.rows: list[_Row] = []
        self.replacements: list[str | None] = []      # row index -> text, None = dropped
        self.heading_of: dict[Path, tuple[int, str, str]] = {}  # path -> (depth, text, anchor)
        # First file to name each collected file. It is what a file with no
        # generated heading of its own inherits its base level from, so a bare
        # ``@path`` index sitting deep in the tree does not reset nesting to zero.
        self.included_by: dict[Path, Path] = {}
        self._anchor_seen: dict[str, int] = {}
        self._finalized = False
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

    def _unique_anchor(self, base: str) -> str:
        """Deduplicate a derived id exactly as a GFM renderer does.

        🛑 THE COUNTER STARTS AT 1, NOT 2: the FIRST heading keeps the bare id and
        the SECOND takes ``-1``. Off by one here yields a link that dangles
        silently -- it points at an id the renderer gave to nobody.

        Called in the order the HEADINGS reach the output, because that is the
        order the renderer counts in; it is not the order the rows were found.
        """
        n = self._anchor_seen.get(base, 0)
        self._anchor_seen[base] = n + 1
        return base if n == 0 else f"{base}-{n}"

    def anchor(self, path: Path) -> str:
        """The fragment a BARE ``@path`` mention points at.

        GitHub-flavoured renderers derive a heading's fragment id by lowercasing,
        and the slug is already restricted to ``[A-Za-z0-9_]``, so lowercasing is
        the whole rule. ⚑ Nothing GENERATES a heading with this id -- the bare
        form creates no heading, by design -- so the fragment does not in fact
        resolve; the link text still names the source the section came from,
        which is what the artifact needs. A ``[text](@path)`` row is the form that
        gets a resolving target (:meth:`_finalize_links`).
        """
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
            # The placeholder is comment-only, so the file is excluded from the
            # output like any other functionally-empty file -- deliberate: the
            # stderr warning above is the failure's carrier (SAY-SO), and the
            # manifest still records the path as absent for the watcher.
            self.kind[path] = "empty"
            return
        self.digests[path] = hashlib.sha256(raw_bytes).hexdigest()
        body, kind = self._process_text(raw_bytes.decode("utf-8"), path)
        self.sections[path] = body
        self.kind[path] = kind

    def _process_text(self, text: str, importing_file: Path) -> tuple[str, str]:
        """Process one file's text; return ``(body, kind)``.

        ``kind`` is the file's CLASSIFICATION, decided here -- during processing,
        never by a regex over the rendered output: ``empty`` (no surviving
        non-blank line), ``imports_only`` (>=1 import-only line and nothing
        else) or ``content`` (anything else, including any literal fenced
        line). Only ``content`` files get a section in the render; the other
        two still have their imports collected.
        """
        out: list[str] = []
        in_fence = False
        fence_char = ""
        fence_len = 0
        in_comment = False
        has_content = False
        has_import_only = False
        # The heading a generated row's depth is measured against, tracked as the
        # file is read (never re-parsed off the rendered output). ``None`` until
        # the file's first heading: a row above it has no enclosing section here,
        # and takes the level of the section this FILE was included under instead.
        # ``listing`` counts headings so two lists in one file stay separate --
        # they neither share a shallowest depth nor renumber into each other.
        enclosing: int | None = None
        listing = 0
        for raw_line in text.splitlines():
            if in_fence:
                # Inside a fence everything is literal -- including any comment
                # delimiters, which is why comment state is not touched here --
                # and a non-blank literal line makes the file CONTENT.
                out.append(raw_line)
                if raw_line.strip():
                    has_content = True
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
                has_content = True  # the fence opener itself is literal content
                continue
            hm = ATX_HEADING_RE.match(line)
            if hm:
                enclosing = len(hm.group("hashes"))
                listing += 1
            processed, import_only = self._process_line(
                line, importing_file, enclosing, listing
            )
            out.append(processed)
            if import_only:
                has_import_only = True
            elif processed.strip():
                has_content = True
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
        if has_content:
            kind = "content"
        elif has_import_only:
            kind = "imports_only"
        else:
            kind = "empty"
        return "\n".join(out), kind

    def _process_line(
        self,
        line: str,
        importing_file: Path,
        enclosing: int | None = None,
        listing: int = 0,
    ) -> tuple[str, bool]:
        """Rewrite the imports in *line*; report whether the line is IMPORT-ONLY.

        *enclosing* / *listing* are the heading context :meth:`_process_text`
        carries for this line -- the level of the heading above it and which
        heading's section it is -- recorded on any row found here and consumed by
        :meth:`_finalize_links`. They do not affect import resolution at all.

        Import-only means >=1 RESOLVED BARE import and nothing else: removing the
        resolved mentions (their spans in the ORIGINAL line) leaves only
        whitespace. A line whose import is UNRESOLVED is content -- the
        neutralized `` `@path` `` is the only in-artifact sign of the miss, so a
        collapsed index must not hide it -- and a ``@mention`` inside a code
        span is literal text, hence content too.

        ⚑ A ``[text](@path)`` link is CONTENT, never import-only: the author
        supplied display text, and that text becomes a table-of-contents row in
        the output. Collapsing the file that carries it would delete the very
        table of contents the link form exists to produce.
        """
        spans = code_span_ranges(line)
        row = NUMBERED_ROW_RE.match(line)
        resolved_spans: list[tuple[int, int]] = []
        unresolved = False

        out: list[str] = []
        pos = 0
        for m in MENTION_RE.finditer(line):
            out.append(line[pos:m.start()])
            pos = m.end()
            if any(s <= m.start() < e for s, e in spans):
                out.append(m.group(0))  # inside a code span -> literal
                continue
            is_link = m.group("lpath") is not None
            raw = m.group("lpath") if is_link else m.group("path")
            resolved = self.resolve(raw, importing_file)
            if resolved is None:
                unresolved = True
                # Target file missing. Neutralize the mention rather than leave a
                # raw live ``@path`` in the flat output: wrap it in backticks so no
                # agent -- including a claude re-reading the flattened file -- can
                # treat it as a live import, while the path stays visible to the
                # reader. Keeps flattening idempotent (a second pass changes
                # nothing). Trailing sentence punctuation stays outside the ticks.
                # A LINK with a dead target keeps its brackets around the same
                # neutralized mention, so it is inert in exactly the same way.
                as_written, trailing = split_trailing_punct(raw)
                # SAY SO. Neutralizing the mention makes the failure invisible in the
                # artifact -- the flat file simply lacks the content, and the launch
                # shim's ``|| true`` swallows the exit status -- so a mis-pathed
                # directive tree would otherwise degrade in total silence. This is
                # the only signal that an import went nowhere.
                self.warnings.append(
                    f"unresolved import @{as_written} in {importing_file}"
                )
                neutral = f"`@{as_written}`{trailing}"
                out.append(f"[{m.group('text')}]({neutral})" if is_link else neutral)
                continue
            path, as_written, trailing = resolved
            self.included_by.setdefault(path, importing_file)
            self.collect(path)
            if is_link:
                # Neither the row's final number nor the target's fragment id is
                # knowable yet -- both depend on which files end up excluded, and
                # that is only settled once everything is read. Leave a
                # placeholder for pass two. The number, when there is one, is
                # taken over WITH the row (it is rewritten on renumber), so the
                # placeholder swallows it and only the indent stays.
                number = gap = None
                if row is not None and row.end() == m.start():
                    number, gap = row.group("num"), row.group("gap")
                    out[-1] = line[:row.start("num")]
                self.rows.append(
                    _Row(
                        importing_file, path, m.group("text"), number, gap or "",
                        enclosing, listing,
                    )
                )
                out.append(_TOKEN.format(len(self.rows) - 1) + trailing)
                continue
            resolved_spans.append((m.start(), m.end()))
            out.append(f"[{as_written}](#{self.anchor(path)}){trailing}")
        out.append(line[pos:])
        if unresolved or not resolved_spans:
            return "".join(out), False
        remainder = line
        for start, end in reversed(resolved_spans):
            remainder = remainder[:start] + remainder[end:]
        return "".join(out), not remainder.strip()

    # -- assembly ----------------------------------------------------------
    def _finalize_links(self) -> None:
        """PASS TWO: number the surviving rows, head their targets, assign ids.

        Runs once, after collection, because every one of those three answers
        depends on the full classification: a row whose target is excluded
        disappears, which RENUMBERS its surviving siblings, which changes their
        heading text, which changes the fragment id the row must point at. Doing
        any of it inline would emit headings that disagree with their own table
        of contents.
        """
        if self._finalized:
            return
        self._finalized = True

        # (1) NUMBERS AND DEPTHS, one LIST at a time, walking COLLECTION ORDER.
        #     Per list, because both answers are list-relative: rows renumber
        #     against their own siblings and take their depth from their own
        #     shallowest row, so two lists in one file must not be pooled.
        #     Collection order is what makes a file's own base level available
        #     before its rows come up -- a file is always collected from the file
        #     that names it, so the row carrying its heading is numbered first.
        # (2) HEADING OWNERS, assigned in the same walk. A file is imported ONCE
        #     however many rows point at it, so it gets ONE heading: the first
        #     surviving row to name it. A row inside a file that is itself excluded
        #     never reaches the output, so it cannot own one.
        by_container: dict[Path, dict[int, list[int]]] = {}
        for i, row in enumerate(self.rows):
            by_container.setdefault(row.container, {}).setdefault(row.listing, []).append(i)
        assigned: list[tuple[str | None, int] | None] = [None] * len(self.rows)
        owner: dict[Path, tuple[str, str | None, int]] = {}   # (text, number, depth)
        base_level: dict[Path, int] = {}
        for path in self.order:
            held = owner.get(path)
            if held is not None:
                base_level[path] = held[2]        # the level its own heading takes
            else:
                # No generated heading of its own: its content sits where the file
                # that pulled it in sits. The source has no includer, hence 0.
                includer = self.included_by.get(path)
                base_level[path] = base_level.get(includer, 0) if includer else 0
            listings = by_container.get(path, {})
            for idxs in listings.values():
                # A row with no heading above it in its own file measures against
                # the section THIS FILE was included under -- the same "level of
                # the enclosing section" rule, one frame out. Floored at 1 because
                # the source sits at level 0 and a heading cannot.
                enclosing = self.rows[idxs[0]].enclosing
                if enclosing is None:
                    enclosing = max(base_level[path], 1)
                batch = [
                    (self.rows[i].number, self.kind.get(self.rows[i].target) != "content")
                    for i in idxs
                ]
                for i, result in zip(idxs, assign_section_numbers(batch, enclosing)):
                    assigned[i] = result
            if self.kind.get(path) != "content":
                continue
            for idxs in listings.values():
                for i in idxs:
                    entry = assigned[i]
                    if entry is not None:
                        owner.setdefault(
                            self.rows[i].target, (self.rows[i].text, entry[0], entry[1])
                        )

        # (3) IDS, in the order the headings REACH THE OUTPUT -- which is section
        #     order, not the order the rows were discovered. A renderer counts
        #     duplicates down the rendered document, so a counter assigned in any
        #     other order would disagree with it on the second ``-1``.
        for path in self.order:
            held = owner.get(path)
            if held is None:
                continue
            display, number, depth = held
            text = f"{number} {display}" if number else display
            self.heading_of[path] = (depth, text, self._unique_anchor(gfm_anchor(text)))

        # (4) ROW TEXT. ``None`` means the row is dropped: its target contributes
        #     no section, so there is nothing for it to point at.
        for row, entry in zip(self.rows, assigned):
            head = self.heading_of.get(row.target)
            if entry is None or head is None:
                self.replacements.append(None)
                continue
            prefix = f"{entry[0]}{row.gap}" if entry[0] else ""
            self.replacements.append(f"{prefix}[{row.text}](#{head[2]})")

    def _emit(self, path: Path) -> str:
        """One file's body with its rows substituted and its heading prepended."""
        lines: list[str] = []
        for line in self.sections[path].split("\n"):
            if _TOKEN_RE.search(line) is None:
                lines.append(line)
                continue
            dropped = False

            def replace(m: re.Match[str]) -> str:
                nonlocal dropped
                text = self.replacements[int(m.group(1))]
                if text is None:
                    dropped = True
                    return ""
                return text

            rendered = _TOKEN_RE.sub(replace, line)
            if dropped and not rendered.strip():
                continue     # the whole row was that link: the row DISAPPEARS
            lines.append(rendered)
        body = "\n".join(lines)
        head = self.heading_of.get(path)
        if head is None:
            return body
        depth, text, _anchor = head
        # ALWAYS the hashes THEN a space (CommonMark 4.2): ``##1.1 Foo`` renders
        # as a literal paragraph, not a heading.
        return f"{'#' * depth} {text}\n\n{body.lstrip()}"

    def render(self, source: Path) -> str:
        self._finalize_links()
        header = (
            "<!-- GENERATED by kanibako (import-directives.py) -- do not edit.\n"
            f"     Flattened from {source} at box start / on reload.\n"
            "     Edit the directive sources under ~/canon instead; changes\n"
            "     take effect the next time this file is regenerated. -->\n"
        )
        # Each surviving file is emitted under its own content, ``---`` between
        # sections, headed only if a ``[text](@path)`` row named it -- a file
        # reached by bare ``@path`` alone gets no generated heading, as before.
        # Files classified ``empty`` or ``imports_only`` contribute their imports
        # (collected above) but no section; the source is subject to the same
        # rule, so a collapsed source yields the generated header comment plus
        # the surviving children only.
        bodies: list[str] = []
        if self.kind[source] == "content":
            bodies.append(self._emit(source).rstrip())
        for path in self.order:
            if path == source or self.kind[path] != "content":
                continue
            bodies.append(self._emit(path).strip())
        parts: list[str] = []
        for i, body in enumerate(bodies):
            if i:
                parts.extend(["", "---", ""])
            parts.append(body)
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
    if size > DIRECTIVE_SIZE_WARN_LIMIT:
        # Advisory only: the artifact is still written in full. We cannot know which
        # harness will read it, so the message names none -- see
        # :data:`DIRECTIVE_SIZE_WARN_LIMIT`. Delivered like every other warning:
        # appended here, printed to stderr with the ``import-directives: `` prefix.
        fl.warnings.append(
            "WARNING: agent directives file exceeds 32KiB, the limit for some "
            "harnesses / agents."
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

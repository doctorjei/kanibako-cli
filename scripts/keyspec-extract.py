#!/usr/bin/env python3
"""Drive the keyspec->manifest extraction against an OpenAI-format chat endpoint.

Replaces the 2026-08-20 nine-subagent fan-out with one command.  The prompt is
``keyspec-extraction-BRIEF.md`` VERBATIM -- this file must never paraphrase it --
plus the per-section assignment the brief already presupposes ("your assigned
line range").  One request per spec section, serial.

THREE THINGS HERE ARE LOAD-BEARING, and each is a failure someone already paid
for:

1. SECTION RANGES ARE RE-DERIVED FROM THE HEADINGS ON EVERY RUN.  The ranges the
   nine-extractor run used are stale -- the 2026-08-20 spec-split migration moved
   ~21 KB of prose out of the keyspec and every coordinate after ~386 moved.  The
   IDs are stable; the line numbers are not.  ``_ORACLE_RANGES`` below exists ONLY
   so ``--list-sections`` can show the drift, and is never used to slice.
   ⚑ Heading detection is FENCE-AWARE: most of this spec's key surface lives in
   fenced blocks whose lines start with ``#``, so a naive ``^#`` scan finds
   hundreds of false headings.
   ⚑ A PARENT'S PREAMBLE IS ITS OWN SECTION (``2-pre``), derived the same way.
   Requesting §2a-§2h alone left §2's own 40 lines -- which declare
   ``system.agent`` and the whole persona-store input paragraph -- requested by
   NOTHING, and requesting §2 instead would re-send every one of its children.

2. A 429 IS INDISTINGUISHABLE FROM A BAD EXTRACTION IF YOU ONLY LOOK AT THE BODY.
   Its body parses as an empty result.  So the status code is recorded for every
   exchange, a non-200 NEVER produces a section output file, and "HTTP 200 with an
   empty completion" is reported as its own distinct, loud failure.

3. THE TOKEN COMES FROM THE PERSONA-GRATA STORE, ARM'S-LENGTH, AND IS NEVER
   PERSISTED, LOGGED OR ECHOED.  The store is a LIVE resolution input read fresh
   on every run (``procedures/persona-resolution-model.md``): no import, no adopt,
   no cache, no write-back.  Every message this script prints is scrubbed of the
   bearer before it reaches a stream, tracebacks included.

Stdlib only -- ``urllib.request``, as ``targets.base.http_probe_status`` does.
``src/kanibako/proxy/`` is NOT reusable here: it speaks the ANTHROPIC surface.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "src") not in sys.path:
  sys.path.insert(0, str(_REPO / "src"))

try:
  from kanibako.persona_store import (
    PersonaEntry,
    locate_entry,
    persona_store_root,
    read_persona_bundle,
    resolve_secret_path,
  )
  from kanibako.targets import get_target
except ImportError as exc:  # pragma: no cover - environment problem, not logic
  sys.exit(f"cannot import kanibako (looked in {_REPO / 'src'}): {exc}")

_CANON = Path(os.environ.get("KANI_CANON", "~/canon")).expanduser()
_DEFAULT_SPEC = _CANON / "workbook/specs/settings-keyspace-1.8.0.md"
_DEFAULT_BRIEF = _CANON / "notebook/resources/keyspec-extraction-BRIEF.md"

#: The sections the nine-extractor run carved the keyspec into (§1A rode with §1's
#: extractor), plus ``2-pre`` for the §2 prose that carve-up left unread.  A missing
#: one is a HARD error naming it -- extracting the wrong slice silently is the
#: outcome this list exists to prevent.
_EXPECTED = ("1", "1A", "2-pre", "2a", "2b", "2c", "2d", "2e", "2f", "2g", "2h")

#: REFERENCE ONLY -- the ranges the 2026-08-20 run used, for the drift column of
#: ``--list-sections``.  🛑 NEVER used to slice anything.  They are stale by
#: construction; the spec moves and the headings are the authority.
_ORACLE_RANGES = {
  "1": (274, 389), "1A": (390, 561), "2a": (602, 935), "2b": (936, 956),
  "2c": (957, 1208), "2d": (1209, 1384), "2e": (1385, 1397), "2f": (1398, 1440),
  "2g": (1441, 1468), "2h": (1469, 1587),
}

_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
_SECTION_ID = re.compile(r"^(?:§\s*)?([0-9]+[A-Za-z]*)\.\s")

#: Appended to a parent's id to name its preamble (§2 -> ``2-pre``).  ⚑ The HYPHEN
#: is the point: ``_SECTION_ID`` mints ids out of ``[0-9]+[A-Za-z]*`` and nothing
#: else, so no heading the spec could ever carry spells one of these.  A synthetic
#: id therefore cannot shadow a declared section, nor be shadowed by one.
_PREAMBLE_SUFFIX = "-pre"

#: Statuses worth another attempt.  429 is the one this script is built around.
_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


# --------------------------------------------------------------------------
# The spec -- sections re-derived from headings, never from a stored coordinate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
  """One heading-delimited spec section, sliced at run time."""

  sid: str
  title: str
  start: int  # 1-based, the heading line itself
  end: int    # 1-based, inclusive


def parse_sections(lines: list[str]) -> dict[str, Section]:
  """Map section id -> :class:`Section` for every numbered heading OUTSIDE a fence."""
  heads: list[tuple[int, int, str, str]] = []  # (1-based lineno, level, sid-or-"", title)
  in_fence = False
  for idx, raw in enumerate(lines, start=1):
    if _FENCE.match(raw):
      in_fence = not in_fence
      continue
    if in_fence:
      continue
    match = _HEADING.match(raw)
    if match is None:
      continue
    title = match.group(2).strip()
    numbered_id = _SECTION_ID.match(title)
    heads.append((idx, len(match.group(1)), numbered_id.group(1) if numbered_id else "", title))

  out: dict[str, Section] = {}

  def declare(sec: Section) -> None:
    prior = out.get(sec.sid)
    if prior is not None:
      raise SystemExit(
        f"spec declares section '{sec.sid}' twice (lines {prior.start} and {sec.start}); "
        f"the section ids must be unique for the carve-up to be well defined"
      )
    out[sec.sid] = sec

  for pos, (lineno, level, sid, title) in enumerate(heads):
    if not sid:
      continue
    # ⚑ A section ends at the next heading of the SAME OR HIGHER level, never at
    # the next heading of ANY level: §2d owns four `####` subheadings
    # (`access`, `agent.claude.*`, …) and truncating there drops 112 of its 176
    # lines — most of its key surface — silently.
    end = len(lines)
    child: tuple[int, str] | None = None  # first DEEPER heading: (lineno, its sid or "")
    for nxt_line, nxt_level, nxt_sid, _ in heads[pos + 1:]:
      if nxt_level <= level:
        end = nxt_line - 1
        break
      if child is None:
        child = (nxt_line, nxt_sid)
    declare(Section(sid=sid, title=title, start=lineno, end=end))
    # ⚑ WHERE THE CHILDREN ARE THEMSELVES NUMBERED, the parent's own prose belongs
    # to no child and the parent cannot stand in for them — asking for §2 re-sends
    # all of §2a-§2h. So the preamble, heading line to the line before the first
    # child, becomes a section in its own right. An UNNUMBERED child (§2d's four
    # `####` blocks) claims nothing, so its parent's range already covers the lot
    # and no preamble is minted: a `2d-pre` would only overlap `2d`.
    if child is not None and child[1]:
      declare(Section(
        sid=sid + _PREAMBLE_SUFFIX,
        title=f"{title}  [preamble — before §{child[1]}]",
        start=lineno,
        end=child[0] - 1,
      ))
  return out


def coverage_gaps(
  found: dict[str, Section], wanted: tuple[str, ...]
) -> list[tuple[int, int, str, str]]:
  """Spans inside the requested range that NO requested section asks for.

  Each entry is ``(first, last, sid_before, sid_after)``.  ⚑ This is the property
  the whole carve-up exists to hold: the extraction becomes the manifest oracle, so
  a span nobody requested is key surface nobody read — and an oracle with an
  unextracted hole is worse than none, because it reads as coverage.
  """
  ordered = sorted((found[sid] for sid in wanted), key=lambda s: (s.start, s.end))
  if not ordered:
    return []
  gaps: list[tuple[int, int, str, str]] = []
  reached, holder = ordered[0].end, ordered[0].sid
  for sec in ordered[1:]:
    if sec.start > reached + 1:
      gaps.append((reached + 1, sec.start - 1, holder, sec.sid))
    if sec.end > reached:
      reached, holder = sec.end, sec.sid
  return gaps


def require_expected(found: dict[str, Section], wanted: tuple[str, ...]) -> None:
  """Exit naming every expected section heading the spec no longer carries."""
  missing = [sid for sid in wanted if sid not in found]
  if not missing:
    return
  raise SystemExit(
    "the spec no longer carries a heading for section(s): "
    + ", ".join(f"§{sid}" for sid in missing)
    + "\nThe carve-up is derived from headings, so a rename means this run would "
      "extract the WRONG SLICE. Fix the section ids (or pass --sections) and re-run."
  )


def range_table(found: dict[str, Section], wanted: tuple[str, ...]) -> str:
  """Render the re-derived ranges beside the 2026-08-20 ones, drift column included."""
  rows = [f"{'§':<5} {'derived':>13} {'lines':>6}  {'2026-08-20':>13} {'lines':>6}  drift"]
  for sid in wanted:
    sec = found[sid]
    span = sec.end - sec.start + 1
    old = _ORACLE_RANGES.get(sid)
    if old is None:
      rows.append(f"{sid:<5} {f'{sec.start}-{sec.end}':>13} {span:>6}  {'(none)':>13} {'':>6}")
      continue
    old_span = old[1] - old[0] + 1
    rows.append(
      f"{sid:<5} {f'{sec.start}-{sec.end}':>13} {span:>6}  "
      f"{f'{old[0]}-{old[1]}':>13} {old_span:>6}  {span - old_span:+d}"
    )
  return "\n".join(rows + ["", coverage_summary(found, wanted)])


def coverage_summary(found: dict[str, Section], wanted: tuple[str, ...]) -> str:
  """The line accounting, with every gap named — a hole must announce itself, loudly."""
  if not wanted:
    return "no sections requested"
  lo = min(found[sid].start for sid in wanted)
  hi = max(found[sid].end for sid in wanted)
  gaps = coverage_gaps(found, wanted)
  # A gap is by definition a maximal UNCOVERED interval inside [lo, hi], so the
  # covered count follows from the span without unioning the sections again.
  covered = (hi - lo + 1) - sum(last - first + 1 for first, last, _, _ in gaps)
  head = f"requested {len(wanted)} section(s): {covered} of {hi - lo + 1} lines in {lo}-{hi}"
  if not gaps:
    return f"{head} — contiguous, nothing between them is unrequested"
  return "\n".join([head] + [
    f"  🛑 GAP {first}-{last} ({last - first + 1} lines) between §{before} and §{after}"
    " — requested by NO section"
    for first, last, before, after in gaps
  ])


def numbered(lines: list[str], sec: Section) -> str:
  """The section's text, line-numbered in the same shape the nine extractors read."""
  return "".join(f"{n:>5}\t{lines[n - 1]}" for n in range(sec.start, sec.end + 1))


# --------------------------------------------------------------------------
# The persona -- LIVE store read, token at arm's length
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Connection:
  """Everything one run needs to reach the endpoint, except the bearer itself."""

  ref: str
  url: str
  model: str | None
  token_path: Path


def resolve_ref(ref: str) -> PersonaEntry:
  """Locate the store entry for *ref*, accepting a bare ``<pid>`` iff it is unambiguous."""
  if not any(sep in ref for sep in ("+", "℘")):
    pdir = persona_store_root() / ref
    try:
      harnesses = sorted(p.name for p in pdir.iterdir() if p.is_dir())
    except OSError as exc:
      raise SystemExit(f"persona '{ref}': no store directory at {pdir} ({exc})") from exc
    if not harnesses:
      raise SystemExit(f"persona '{ref}': {pdir} holds no harness directory")
    if len(harnesses) > 1:
      raise SystemExit(
        f"persona '{ref}' is ambiguous -- {pdir} holds {len(harnesses)} harnesses: "
        + ", ".join(harnesses)
        + f"\nName one, e.g. --persona {ref}+{harnesses[0]}"
      )
    ref = f"{ref}+{harnesses[0]}"
  entry = locate_entry(ref)
  if entry is None:
    raise SystemExit(
      f"'{ref}' is not a persona-grata store entry (looked under {persona_store_root()}). "
      f"This script needs the store for the token pointer; a flag cannot supply it."
    )
  return entry


def resolve_connection(
  entry: PersonaEntry, ref: str, *, endpoint: str | None, model: str | None, path: str
) -> Connection:
  """Build the :class:`Connection` from the LIVE store, with the two flag overrides applied."""
  token = resolve_secret_path(entry)
  if token.path is None:
    raise SystemExit(f"persona '{ref}': token pointer unusable -- {token.error}")

  store_endpoint: str | None = None
  store_model: str | None = None
  if endpoint is None or model is None:
    try:
      target = get_target(entry.harness)()
    except KeyError as exc:
      raise SystemExit(
        f"persona '{ref}': no '{entry.harness}' target plugin is installed, so the "
        f"endpoint/model cannot be read out of {entry.config_dir}. Install it, or pass "
        f"--endpoint (and --model). {exc}"
      ) from exc
    except Exception as exc:  # noqa: BLE001 - third-party plugin
      raise SystemExit(
        f"persona '{ref}': the '{entry.harness}' target plugin could not be constructed "
        f"({exc.__class__.__name__}: {exc})"
      ) from exc
    bundle = read_persona_bundle(ref, target)
    if bundle is None:  # unreachable: locate_entry already succeeded
      raise SystemExit(f"persona '{ref}': the store entry vanished mid-run")
    if bundle.reject_reason is not None:
      raise SystemExit(f"persona '{ref}': {bundle.reject_reason}")
    if not bundle.no_reader:
      store_endpoint, store_model = bundle.endpoint, bundle.model

  base = endpoint or store_endpoint
  if not base:
    raise SystemExit(
      f"persona '{ref}': no endpoint -- the '{entry.harness}' config in {entry.config_dir} "
      f"names none (or the harness has no persona reader). Pass --endpoint."
    )
  base = base.rstrip("/")
  url = base if base.endswith(path) else base + path
  return Connection(ref=ref, url=url, model=model or store_model, token_path=token.path)


def read_bearer(token_path: Path) -> str:
  """Read the bearer ONCE for this run; held in memory, written nowhere."""
  try:
    token = token_path.read_text(encoding="utf-8").strip()
  except (OSError, UnicodeDecodeError) as exc:
    raise SystemExit(f"cannot read the persona token file ({exc.__class__.__name__})") from exc
  if not token:
    raise SystemExit("the persona token file is empty")
  return token


def scrub(text: str, secret: str) -> str:
  """Redact *secret* anywhere it appears -- the last gate before anything is printed."""
  return text.replace(secret, "<redacted>") if secret else text


# --------------------------------------------------------------------------
# Transport -- OpenAI chat-completions, serial, status recorded every time
# --------------------------------------------------------------------------


class _NoRedirects(urllib.request.HTTPRedirectHandler):
  """Refuse every redirect: urllib would re-send the `Authorization` bearer cross-origin."""

  def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
    return None


@dataclass(frozen=True)
class Exchange:
  """One HTTP attempt: the status is recorded even when the body is unusable."""

  status: int | None       # None ONLY when no HTTP response was received at all
  body: bytes
  transport_error: str | None
  retry_after: float | None


def post_chat(url: str, payload: dict, bearer: str, timeout: float) -> Exchange:
  """POST *payload* and return the :class:`Exchange`; never raises, never logs the request."""
  data = json.dumps(payload).encode("utf-8")
  req = urllib.request.Request(url, data=data, method="POST")
  req.add_header("Content-Type", "application/json")
  req.add_header("Authorization", f"Bearer {bearer}")
  opener = urllib.request.build_opener(_NoRedirects)
  try:
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310
      return Exchange(int(resp.status), resp.read(), None, _retry_after(resp.headers))
  except urllib.error.HTTPError as exc:
    return Exchange(int(exc.code), exc.read() or b"", None, _retry_after(exc.headers))
  except Exception as exc:  # noqa: BLE001 - contract is never-raises; every transport shape
    return Exchange(None, b"", f"{exc.__class__.__name__}: {exc}", None)


def _retry_after(headers) -> float | None:
  """The ``Retry-After`` header in seconds, when the server sent a usable one."""
  try:
    raw = headers.get("Retry-After")
  except AttributeError:
    return None
  if not raw:
    return None
  try:
    return max(0.0, float(raw))
  except (TypeError, ValueError):
    return None  # HTTP-date form: fall back to our own backoff


def completion_text(body: bytes) -> tuple[str | None, str | None]:
  """Return ``(text, why_unusable)`` for a 200 body -- EXACTLY one side is set.

  ⚑ Every arm here is a DISTINCT loud failure, because a rate-limited body and a
  bad extraction both look like "nothing came back" if they are collapsed.
  """
  if not body.strip():
    return None, f"HTTP 200 but the response body was EMPTY ({len(body)} bytes)"
  try:
    data = json.loads(body)
  except ValueError as exc:
    return None, f"HTTP 200 but the response body is not JSON ({exc})"
  if isinstance(data, dict) and data.get("error"):
    return None, f"HTTP 200 carrying a JSON error object: {json.dumps(data['error'])[:400]}"
  if not isinstance(data, dict):
    return None, f"HTTP 200 but the response JSON is a {type(data).__name__}, not an object"
  choices = data.get("choices")
  if not isinstance(choices, list) or not choices:
    return None, "HTTP 200 but the response carries no 'choices' array"
  message = choices[0].get("message") if isinstance(choices[0], dict) else None
  content = message.get("content") if isinstance(message, dict) else None
  if not isinstance(content, str) or not content.strip():
    finish = choices[0].get("finish_reason") if isinstance(choices[0], dict) else None
    return None, (
      "HTTP 200 but the completion content was EMPTY "
      f"(finish_reason={finish!r}) -- this is NOT a rate limit; the endpoint "
      "accepted the request and returned nothing"
    )
  return content, None


@dataclass
class Attempt:
  """What one attempt did, for the section's meta record."""

  status: int | None
  transport_error: str | None
  bytes_in: int
  seconds: float


def run_section(
  *, conn: Connection, bearer: str, prompt: str, args: argparse.Namespace
) -> tuple[str | None, str | None, list[Attempt]]:
  """Call the endpoint for one section; return ``(text, failure, attempts)``."""
  messages: list[dict[str, str]] = []
  if args.system:
    messages.append({"role": "system", "content": args.system})
  messages.append({"role": "user", "content": prompt})
  payload: dict = {"messages": messages, "max_tokens": args.max_tokens}
  # ⚑ model and temperature are OMITTED when unset, never defaulted to a
  # placeholder: a hardwired-model server rejects an id it does not serve.
  if conn.model:
    payload["model"] = conn.model
  if args.temperature is not None:
    payload["temperature"] = args.temperature

  attempts: list[Attempt] = []
  failure = "no attempt was made"
  for attempt in range(1, args.max_retries + 2):
    started = time.monotonic()
    ex = post_chat(conn.url, payload, bearer, args.timeout)
    elapsed = time.monotonic() - started
    attempts.append(Attempt(ex.status, ex.transport_error, len(ex.body), round(elapsed, 3)))

    if ex.status == 200:
      text, why = completion_text(ex.body)
      if text is not None:
        return text, None, attempts
      failure = why or "unusable 200 response"
      break  # a 200 we cannot use is not a rate limit; retrying hides the cause
    # ⚑ SCRUBBED HERE, not at the call site: an endpoint that echoes the
    # Authorization header into its error body would otherwise leak the bearer
    # through the retry line below, which is printed before anyone sees it.
    if ex.status is None:
      failure = scrub(f"no HTTP response -- {ex.transport_error}", bearer)
    else:
      failure = scrub(f"HTTP {ex.status} -- {_body_excerpt(ex.body)}", bearer)
    retryable = ex.status is None or ex.status in _RETRY_STATUSES
    if not retryable or attempt > args.max_retries:
      break
    delay = ex.retry_after if ex.retry_after is not None else args.gap * (2 ** (attempt - 1))
    print(f"    attempt {attempt}: {failure}; retrying in {delay:.1f}s", file=sys.stderr)
    time.sleep(delay)
  return None, failure, attempts


def _body_excerpt(body: bytes) -> str:
  """A short, safe rendering of an error body for the failure line."""
  if not body:
    return "empty body"
  try:
    return body.decode("utf-8", "replace")[:300].replace("\n", " ")
  except Exception:  # noqa: BLE001 - diagnostics must never raise
    return f"{len(body)} undecodable bytes"


# --------------------------------------------------------------------------
# Merge -- a HEURISTIC key roll-up over the returned prose
# --------------------------------------------------------------------------

_ROW_START = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\|)\s*")
_KEYISH = re.compile(r"`([A-Za-z_][A-Za-z0-9_<>.\-]*(?:\.[A-Za-z0-9_<>.\-]+)+)`")


def harvest_keys(text: str) -> list[str]:
  """Pull the first backticked dotted token off each row-shaped line.

  ⚑ HEURISTIC, and labelled as such wherever it is written: the brief asks for
  prose rows, so this replaces the 2026-08-20 run's HAND transcription with a
  mechanical one.  The raw per-section text is always kept beside it.
  """
  keys: list[str] = []
  seen: set[str] = set()
  for line in text.splitlines():
    if not _ROW_START.match(line):
      continue
    match = _KEYISH.search(line)
    if match and match.group(1) not in seen:
      seen.add(match.group(1))
      keys.append(match.group(1))
  return keys


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
  """The CLI -- this script's only documentation surface."""
  parser = argparse.ArgumentParser(
    prog="keyspec-extract.py",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description=(
      "Run the keyspec->manifest extraction against an OpenAI-format chat-completions\n"
      "endpoint, one request per spec section, serially.\n"
      "\n"
      "The prompt is keyspec-extraction-BRIEF.md VERBATIM plus the per-section\n"
      "assignment it presupposes. Section ranges are RE-DERIVED from the spec's own\n"
      "headings on every run -- any stored coordinate is stale the moment the spec\n"
      "moves -- and a missing expected heading is a hard error, never a wrong slice.\n"
      "\n"
      "Credentials come from the persona-grata store and nowhere else. The store is a\n"
      "LIVE input, read fresh on every run: nothing is imported, cached or written\n"
      "back, and the bearer is scrubbed out of every message this script prints."
    ),
    epilog=(
      "SECTION OUTPUT (per section, under --out):\n"
      "  section-<id>.md          the extractor's text. Written ONLY on a usable 200.\n"
      "  section-<id>.meta.json   status code of every attempt, timings, byte counts.\n"
      "  section-<id>.FAILED.json why it failed. Never mistakable for a result.\n"
      "  RUN.json                 the derived ranges, the connection, the outcome.\n"
      "  keys-merged.txt          heuristic key roll-up; the raw sections are authority.\n"
      "\n"
      "A section with an existing section-<id>.md is SKIPPED (resume); --force re-runs it.\n"
      "\n"
      "FAILURE CLASSES, deliberately kept apart:\n"
      "  HTTP 429 / 5xx / no response  -> retried with backoff, then reported with the code.\n"
      "  HTTP 200, empty completion    -> reported as its own failure, NOT retried.\n"
      "A rate-limited body parses as an empty result; collapsing the two is the bug this\n"
      "script exists to avoid.\n"
      "\n"
      "EXIT: 0 every requested section extracted . 1 one or more failed . 2 bad usage.\n"
      "\n"
      "EXAMPLES:\n"
      "  keyspec-extract.py --list-sections\n"
      "  keyspec-extract.py --persona navigator+codex --out ~/runs/keyspec-01\n"
      "  keyspec-extract.py --persona navigator+codex --sections 2c,2d --force\n"
    ),
  )
  parser.add_argument(
    "--persona", metavar="REF",
    help="persona-grata ref '<pid>+<hid>' (a bare '<pid>' is accepted iff unambiguous). "
         "Supplies the endpoint, the model and the token POINTER. Not needed for "
         "--list-sections or --dry-run.",
  )
  parser.add_argument("--spec", type=Path, default=_DEFAULT_SPEC, help=f"default: {_DEFAULT_SPEC}")
  parser.add_argument(
    "--brief", type=Path, default=_DEFAULT_BRIEF, help=f"default: {_DEFAULT_BRIEF}",
  )
  parser.add_argument(
    "--out", type=Path, default=None,
    help="run directory (default: $TMPDIR/keyspec-extract/<utc stamp>)",
  )
  parser.add_argument(
    "--sections", default=",".join(_EXPECTED),
    help=f"comma-separated section ids (default: {','.join(_EXPECTED)})",
  )
  parser.add_argument(
    "--endpoint", help="override the store's base URL. Must be OpenAI-format (include /v1).",
  )
  parser.add_argument("--model", help="override the store's model id")
  parser.add_argument(
    "--api-path", default="/chat/completions",
    help="appended to the base URL unless already present (default: /chat/completions)",
  )
  parser.add_argument(
    "--system-file", type=Path,
    help="optional system message. ⚑ persona-grata carries NO prompt -- the store holds "
         "endpoint/model/env_key/token only -- so there is no persona prompt to render and "
         "none is hardcoded. Default: no system message, as the nine extractors ran.",
  )
  parser.add_argument("--timeout", type=float, default=300.0, help="per-request seconds")
  parser.add_argument("--max-tokens", type=int, default=8000)
  parser.add_argument(
    "--temperature", type=float, default=None, help="omitted from the payload unless given",
  )
  parser.add_argument("--max-retries", type=int, default=4, help="retries per section")
  parser.add_argument(
    "--gap", type=float, default=2.0,
    help="seconds between requests, and the backoff base (default 2.0; NaviGator is "
         "rpm 120 / max_parallel 10, so this runs serial)",
  )
  parser.add_argument("--force", action="store_true", help="re-run sections already extracted")
  parser.add_argument(
    "--list-sections", action="store_true",
    help="print the re-derived ranges beside the 2026-08-20 ones, plus the line "
         "accounting for the requested set and any gap between two of them, and exit",
  )
  parser.add_argument(
    "--dry-run", action="store_true",
    help="resolve everything and write the prompts to --out, but make no request",
  )
  return parser


def main(argv: list[str]) -> int:
  """Entry point."""
  parser = build_parser()
  args = parser.parse_args(argv[1:])

  try:
    lines = args.spec.open(encoding="utf-8").readlines()
  except OSError as exc:
    raise SystemExit(f"cannot read the spec: {exc}") from exc
  wanted = tuple(s.strip() for s in args.sections.split(",") if s.strip())
  found = parse_sections(lines)
  require_expected(found, wanted)

  if args.list_sections:
    print(f"{args.spec} ({len(lines)} lines)\n")
    print(range_table(found, wanted))
    return 0

  if args.out is None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.out = Path(os.environ.get("TMPDIR", "/tmp")) / "keyspec-extract" / stamp
  args.out.mkdir(parents=True, exist_ok=True)

  try:
    brief = args.brief.read_text(encoding="utf-8")
  except OSError as exc:
    raise SystemExit(f"cannot read the brief: {exc}") from exc
  args.system = args.system_file.read_text(encoding="utf-8") if args.system_file else None

  conn: Connection | None = None
  bearer = ""
  if not args.dry_run:
    if not args.persona:
      parser.error("--persona is required unless --list-sections or --dry-run is given")
    entry = resolve_ref(args.persona)
    ref = f"{entry.persona}+{entry.harness}"
    conn = resolve_connection(
      entry, ref, endpoint=args.endpoint, model=args.model, path=args.api_path,
    )
    bearer = read_bearer(conn.token_path)
    print(f"persona {ref} -> {conn.url}  model={conn.model or '(omitted)'}")

  print(f"{args.spec} ({len(lines)} lines)\n")
  print(range_table(found, wanted))
  print(f"\nrun directory: {args.out}\n")

  results: dict[str, dict] = {}
  failures: list[str] = []
  for sid in wanted:
    sec = found[sid]
    body = args.out / f"section-{sid}.md"
    failed = args.out / f"section-{sid}.FAILED.json"
    prompt = (
      f"{brief}\n\n---\n\n## YOUR ASSIGNED SECTION — §{sid}\n\n"
      f"Spec file: {args.spec}\n"
      f"Your line range: {sec.start}–{sec.end} (inclusive), heading: {sec.title}\n\n"
      f"The full text of your range follows, line-numbered.\n\n{numbered(lines, sec)}"
    )
    if args.dry_run:
      (args.out / f"prompt-{sid}.txt").write_text(prompt, encoding="utf-8")
      print(f"§{sid:<5} lines {sec.start}-{sec.end}  prompt {len(prompt)} chars (dry run)")
      continue
    if body.exists() and not args.force:
      # ⚑ RE-HARVEST, do not merely skip: the merge is rewritten every run, so a
      # resumed section that contributed nothing would silently empty the roll-up.
      kept = body.read_text(encoding="utf-8")
      results[sid] = {"chars": len(kept), "keys": harvest_keys(kept)}
      print(f"§{sid:<5} lines {sec.start}-{sec.end}  SKIPPED (already extracted; --force to redo)")
      continue

    assert conn is not None
    print(f"§{sid:<5} lines {sec.start}-{sec.end}  requesting…", flush=True)
    text, failure, attempts = run_section(conn=conn, bearer=bearer, prompt=prompt, args=args)
    meta = {
      "section": sid, "title": sec.title, "start": sec.start, "end": sec.end,
      "prompt_chars": len(prompt), "model": conn.model, "url": conn.url,
      "attempts": [a.__dict__ for a in attempts],
      "statuses": [a.status for a in attempts],
      "ok": text is not None,
    }
    (args.out / f"section-{sid}.meta.json").write_text(
      json.dumps(meta, indent=2) + "\n", encoding="utf-8",
    )
    if text is None:
      reason = scrub(failure or "unknown", bearer)
      meta["failure"] = reason
      failed.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
      print(f"     FAILED: {reason}", file=sys.stderr)
      failures.append(f"§{sid}: {reason}")
      continue
    failed.unlink(missing_ok=True)
    body.write_text(text, encoding="utf-8")
    keys = harvest_keys(text)
    results[sid] = {"chars": len(text), "keys": keys}
    print(f"     ok: {len(text)} chars, {len(keys)} key-shaped rows")

  if not args.dry_run:
    merged = _write_merge(args.out, wanted, results)
    (args.out / "RUN.json").write_text(
      json.dumps(
        {
          "spec": str(args.spec), "spec_lines": len(lines), "brief": str(args.brief),
          "persona": args.persona, "url": conn.url if conn else None,
          "model": conn.model if conn else None,
          "derived_ranges": {s: [found[s].start, found[s].end] for s in wanted},
          "oracle_ranges_2026_08_20": {
            s: list(_ORACLE_RANGES[s]) for s in wanted if s in _ORACLE_RANGES
          },
          "sections_ok": sorted(results), "failures": failures,
          "merged_keys": merged,
        },
        indent=2,
      ) + "\n",
      encoding="utf-8",
    )
    print(f"\n{merged} merged key-shaped rows (heuristic) across {len(results)} section(s)")
    print("2026-08-20 nine-extractor run reported 106 keys against 96 in the manifest")

  if failures:
    print("\n" + "\n".join(failures), file=sys.stderr)
    print(f"{len(failures)} section(s) FAILED; nothing was written for them.", file=sys.stderr)
    return 1
  return 0


def _write_merge(out: Path, wanted: tuple[str, ...], results: dict[str, dict]) -> int:
  """Write ``keys-merged.txt`` and return the distinct key count."""
  seen: set[str] = set()
  body = [
    "# HEURISTIC key roll-up -- the per-section section-<id>.md files are the authority.",
    "# One line per distinct key-shaped row, in section order.",
    "",
  ]
  for sid in wanted:
    res = results.get(sid)
    if res is None:
      body.append(f"# §{sid}: NOT EXTRACTED IN THIS RUN")
      continue
    body.append(f"# §{sid}: {len(res['keys'])} rows")
    for key in res["keys"]:
      if key not in seen:
        seen.add(key)
        body.append(key)
  (out / "keys-merged.txt").write_text("\n".join(body) + "\n", encoding="utf-8")
  return len(seen)


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))

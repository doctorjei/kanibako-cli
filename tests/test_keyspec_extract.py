"""The keyspec carve-up: every requested section, and NOTHING BETWEEN THEM, gets read.

``scripts/keyspec-extract.py`` slices the keyspace spec into per-section extraction
requests, and its output becomes the ORACLE the manifest-enforcer program is judged
against.  So a span that no requested section asks for is not a cosmetic gap: it is key
surface nobody read, wearing the appearance of coverage.  That is strictly worse than
having no oracle, because nothing downstream can tell the difference.

⚑ THE SHAPE THAT CAUSED IT, and what these tests pin: a parent heading whose children
are themselves numbered sections owns prose of its own, before the first child.  Asking
for the children misses it; asking for the PARENT instead re-sends every child.  It
needs its own id.  A parent whose children are UNNUMBERED is the opposite case — nothing
else claims them, the parent's own range already covers them, and minting a preamble
there would only overlap the parent.

The spec lives in the canon (``~/canon/workbook/specs/``), outside this repo and absent
from CI, so the binding tests here run against a fixture that reproduces both shapes.

Tests stay together because this non-package script is loaded by path. The same import
covers truncation, registered projection, and credential handling; scripts lie outside
both standard mypy scopes.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "keyspec-extract.py"


def _load_script():
  """Import the hyphenated script by path -- it is not a package module."""
  spec = importlib.util.spec_from_file_location("keyspec_extract", _SCRIPT)
  assert spec is not None and spec.loader is not None, f"cannot load {_SCRIPT}"
  module = importlib.util.module_from_spec(spec)
  # ⚑ REGISTERED BEFORE EXECUTION, not after: @dataclass resolves annotations through
  # ``sys.modules[cls.__module__]`` and raises AttributeError on an absent entry.
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


keyspec = _load_script()


#: A miniature of the real spec's structure, and every clause of it is load-bearing:
#: two childless top sections; one parent (§2) with prose of its own, a FENCED block
#: whose lines start with ``#``, and numbered children; and a child (§2b) that owns only
#: UNNUMBERED subheadings.  Heading text is deliberately unlike the real spec's -- this
#: is a shape, not a copy of an authority.
FIXTURE = """# Fixture — NOT the keyspace spec

## 1. FIRST — no children
prose

## 1A. FIRST-A — no children either
prose

## 2. SECOND — the parent
preamble prose that declares a key nothing else mentions
```yaml
# not a heading: this line is inside a fence
### neither is this one
```
more preamble prose

### 2a. SECOND-A
prose

### 2b. SECOND-B — owns UNNUMBERED subheadings only
prose

#### `access` — unnumbered
prose

#### `agent.claude.*` — unnumbered
prose

## 3. THIRD
prose
"""

#: Every numbered leaf plus the preamble -- the set a default run must request.
LEAF_REQUEST = ("1", "1A", "2-pre", "2a", "2b", "3")

#: The same request WITHOUT the preamble: what the carve-up asked for before this was
#: fixed, kept so the gap detector is pinned against a known hole and not only a zero.
REQUEST_MISSING_THE_PREAMBLE = ("1", "1A", "2a", "2b", "3")


@pytest.fixture
def lines() -> list[str]:
  """The fixture spec as ``readlines()`` would hand it over."""
  return FIXTURE.splitlines(keepends=True)


def _line_of(lines: list[str], prefix: str) -> int:
  """The 1-based line number of the only line starting with *prefix*."""
  hits = [n for n, raw in enumerate(lines, start=1) if raw.startswith(prefix)]
  assert len(hits) == 1, f"{prefix!r} matches {len(hits)} lines, expected exactly 1"
  return hits[0]


def test_requested_sections_tile_their_span(lines: list[str]) -> None:
  """THE INVARIANT: nothing between the first and last requested line goes unrequested."""
  found = keyspec.parse_sections(lines)
  absent = [sid for sid in LEAF_REQUEST if sid not in found]
  assert not absent, (
    f"the carve-up has no id for {absent}, so those lines can be requested by nothing"
  )
  assert keyspec.coverage_gaps(found, LEAF_REQUEST) == []


def test_a_gap_between_requested_sections_is_reported_not_swallowed(lines: list[str]) -> None:
  """Drop the preamble and the detector must name the exact span, and who it falls between."""
  found = keyspec.parse_sections(lines)
  parent = _line_of(lines, "## 2.")
  first_child = _line_of(lines, "### 2a.")
  assert keyspec.coverage_gaps(found, REQUEST_MISSING_THE_PREAMBLE) == [
    (parent, first_child - 1, "1A", "2a")
  ]
  summary = keyspec.coverage_summary(found, REQUEST_MISSING_THE_PREAMBLE)
  assert "GAP" in summary and f"{parent}-{first_child - 1}" in summary


def test_preamble_runs_from_the_parent_heading_to_its_first_child(lines: list[str]) -> None:
  """Derived from the headings, exactly as every other range is -- never a stored pair."""
  found = keyspec.parse_sections(lines)
  parent = _line_of(lines, "## 2.")
  first_child = _line_of(lines, "### 2a.")
  assert (found["2-pre"].start, found["2-pre"].end) == (parent, first_child - 1)
  # The fenced `###` line must have been ignored, not taken as the first child.
  fenced = _line_of(lines, "### neither is this one")
  assert found["2-pre"].start < fenced < found["2-pre"].end


def test_the_preamble_and_the_children_reconstruct_the_parent_exactly(lines: list[str]) -> None:
  """The parent is never requested, so its whole range must be covered by ids that are."""
  found = keyspec.parse_sections(lines)
  children = ("2-pre", "2a", "2b")
  assert min(found[sid].start for sid in children) == found["2"].start
  assert max(found[sid].end for sid in children) == found["2"].end
  assert keyspec.coverage_gaps(found, children) == []


def test_unnumbered_children_mint_no_preamble(lines: list[str]) -> None:
  """§2d's shape: its four ``####`` blocks claim nothing, so a ``2b-pre`` would only overlap."""
  found = keyspec.parse_sections(lines)
  assert "2b-pre" not in found, (
    "a parent whose children are unnumbered already covers them; a preamble here would "
    "duplicate lines its own parent requests"
  )
  assert found["2b"].end == _line_of(lines, "## 3.") - 1


def test_a_preamble_id_cannot_collide_with_a_declared_section() -> None:
  """By construction, not by luck: the id grammar cannot mint the suffix's spelling."""
  assert keyspec._SECTION_ID.match(f"2{keyspec._PREAMBLE_SUFFIX}. Anything") is None


def test_the_default_request_covers_the_real_spec() -> None:
  """_EXPECTED against the ACTUAL spec, where it is mounted.

  ⚑ SKIPS OFF-BOX. The canon is not in this repo and is not in CI, so this cannot be the
  test that holds the invariant -- the fixture tests above are. It is here because it is
  the only thing that catches the real spec growing a heading nobody re-checked.
  """
  spec_path = keyspec._DEFAULT_SPEC
  if not spec_path.is_file():
    pytest.skip(f"the keyspace spec is not mounted at {spec_path}")
  spec_lines = spec_path.open(encoding="utf-8").readlines()
  found = keyspec.parse_sections(spec_lines)
  keyspec.require_expected(found, keyspec._EXPECTED)
  assert keyspec.coverage_gaps(found, keyspec._EXPECTED) == []


# --------------------------------------------------------------------------
# The truncation guard: a TRUNCATED completion arrives as a USABLE 200.
#
# ⚑ No network, no server, no temp files -- only ``post_chat`` is replaced, which
# is the whole of this script's transport.  Everything above the seam (the retry
# loop, the attempt record, the verdict) runs for real.
# --------------------------------------------------------------------------

_CONN = keyspec.Connection(
  ref="fixture+harness",
  url="http://endpoint.invalid/v1/chat/completions",
  model=None,
  token_path=Path("/dev/null"),
)


def _args():
  """Real parser defaults, so these pins read ``--max-retries`` from the CLI, not a literal."""
  args = keyspec.build_parser().parse_args([])
  # ⚑ main() derives this from --system-file; the parser declares no `system` of its own.
  args.system = None
  return args


def _usable_200(**choice_extra: object) -> bytes:
  """A 200 body with NON-EMPTY content -- exactly what a truncated reply also looks like."""
  choice: dict = {"message": {"role": "assistant", "content": "- `meta.box.mode`\n"}}
  choice.update(choice_extra)
  return json.dumps({"choices": [choice]}).encode("utf-8")


def _serve(monkeypatch: pytest.MonkeyPatch, body: bytes) -> list[dict]:
  """Answer every request with the same 200 *body*; the returned list logs each call."""
  calls: list[dict] = []

  def fake_post_chat(url: str, payload: dict, bearer: str, timeout: float) -> object:
    calls.append(payload)
    return keyspec.Exchange(200, body, None, None)

  monkeypatch.setattr(keyspec, "post_chat", fake_post_chat)
  return calls


def test_a_truncated_usable_200_fails_the_section_and_is_not_retried(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """THE CASE THE FIELD EXISTS FOR: usable content, and the endpoint says it stopped short."""
  args = _args()
  assert args.max_retries >= 1, "a single-request pin proves nothing if retries are off"
  calls = _serve(monkeypatch, _usable_200(finish_reason="length"))
  text, failure, attempts = keyspec.run_section(
    conn=_CONN,
    bearer="unused",
    prompt="p",
    args=args,
  )
  assert text is None, "a truncated completion must never be written out as a section"
  assert failure is not None and "finish_reason='length'" in failure
  assert len(calls) == len(attempts) == 1, (
    f"max_tokens is unchanged between attempts, so a retry can only truncate again; "
    f"{len(calls)} requests were made against --max-retries {args.max_retries}"
  )
  assert attempts[-1].finish_reason == "length"


def test_a_stop_finish_returns_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
  """The guard must not red-line the working case it lives next to."""
  args = _args()
  _serve(monkeypatch, _usable_200(finish_reason="stop"))
  text, failure, attempts = keyspec.run_section(
    conn=_CONN,
    bearer="unused",
    prompt="p",
    args=args,
  )
  assert failure is None
  assert text is not None and "meta.box.mode" in text
  assert attempts[-1].finish_reason == "stop"


def test_an_absent_finish_reason_is_kept_but_warned_about(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  """UNKNOWN is deliberately NOT fatal -- it would refuse every endpoint that omits the field.

  The warning is the whole of the protection: without it, a clean run's silence reads as a
  confirmed absence of truncation.
  """
  args = _args()
  _serve(monkeypatch, _usable_200())
  text, failure, attempts = keyspec.run_section(
    conn=_CONN,
    bearer="unused",
    prompt="p",
    args=args,
  )
  assert failure is None and text is not None
  assert attempts[-1].finish_reason is None
  assert "UNKNOWN" in capsys.readouterr().err


def test_a_blank_finish_reason_is_unknown_and_still_recorded_verbatim(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  """A field present but empty means what ``null`` means; only the VERDICT normalizes it.

  ⚑ Both halves are the contract: an endpoint that blanks the field is the very endpoint
  the non-fatal-UNKNOWN decision exists to protect, and the meta record still keeps ``""``
  because the evidence is the endpoint's own word, not our reading of it.
  """
  args = _args()
  _serve(monkeypatch, _usable_200(finish_reason=""))
  text, failure, attempts = keyspec.run_section(
    conn=_CONN,
    bearer="unused",
    prompt="p",
    args=args,
  )
  assert failure is None and text is not None, "a blank finish_reason must not fail a section"
  assert attempts[-1].finish_reason == "", "the record keeps the endpoint's own value"
  assert "UNKNOWN" in capsys.readouterr().err


@pytest.mark.parametrize("store_model", ["PERSONA_DEFAULT_MODEL", ""])
def test_a_null_or_empty_store_model_names_no_model_and_the_metadata_serializes(
  monkeypatch: pytest.MonkeyPatch,
  store_model: str,
) -> None:
  """A store ``null`` (the marker) and a store ``""`` both reach the request as NO model.

  ⚑ CI does not type-check ``scripts/``, so this test is the check: read raw, the marker
  survives ``model or store_model`` and raises in ``json.dumps`` when the run's metadata
  is written; the ``""`` would name an empty model.
  """
  from types import SimpleNamespace

  from kanibako.persona_store import PersonaBundle, SecretPathResult
  from kanibako.targets.base import PERSONA_DEFAULT_MODEL

  model = PERSONA_DEFAULT_MODEL if store_model == "PERSONA_DEFAULT_MODEL" else store_model
  bundle = PersonaBundle(endpoint="https://example.invalid", model=model)
  entry = SimpleNamespace(harness="claude", config_dir=Path("/nonexistent"))
  target = object()
  calls: list[str] = []

  def fake_resolve_secret_path(got: object) -> SecretPathResult:
    assert got is entry
    calls.append("resolve_secret_path")
    return SecretPathResult(path=Path("/dev/null"), error=None)

  def fake_get_target(harness: str) -> object:
    assert harness == "claude"
    calls.append("get_target")
    return lambda: target

  def fake_read_persona_bundle(ref: str, got: object) -> PersonaBundle:
    assert ref == "p+claude"
    assert got is target, "the bundle is read through the target the factory built"
    calls.append("read_persona_bundle")
    return bundle

  monkeypatch.setattr(keyspec, "resolve_secret_path", fake_resolve_secret_path)
  monkeypatch.setattr(keyspec, "get_target", fake_get_target)
  monkeypatch.setattr(keyspec, "read_persona_bundle", fake_read_persona_bundle)

  conn = keyspec.resolve_connection(
    entry,
    "p+claude",
    endpoint=None,
    model=None,
    path="/v1/chat/completions",
  )
  assert calls == ["resolve_secret_path", "get_target", "read_persona_bundle"]
  assert conn.model is None
  json.dumps({"model": conn.model, "url": conn.url})


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch):
  from types import SimpleNamespace

  from kanibako.agent_ref import AgentIdentity
  from kanibako.persona_store import RegisteredPersona
  from kanibako.targets.base import PersonaReadOutcome, PersonaSettings

  persona = RegisteredPersona(
    identity=AgentIdentity(
      node="claude",
      registered_name="claude",
      persona="p",
      harness="Goose",
      target_name="goose",
      entry_path=Path("/synthetic-store/claude"),
    ),
    store_root=Path("/selected-store"),
    config={"model": "chosen", "secret": "synthetic-native-secret"},
    auth_env="OPENAI_API_KEY",
    keyless=False,
  )
  calls: list[str] = []

  def project(config, *, source, keyless=False):
    assert config is persona.config
    assert source == "/synthetic-store/claude/agent.cfg"
    calls.append("project")
    return PersonaReadOutcome(
      PersonaSettings("https://endpoint.invalid/v1", config.get("model"), "OPENAI_API_KEY"),
      None,
    )

  def target(name):
    calls.append(name)
    assert name == "goose", "registered spelling must never select the target"
    return lambda: SimpleNamespace(name="goose", project_persona_settings=project)

  def forbidden(*args, **kwargs):
    pytest.fail("registered projection touched legacy paths or PG discovery")

  monkeypatch.setattr(keyspec, "get_target", target)
  monkeypatch.setattr(keyspec, "resolve_secret_path", forbidden)
  monkeypatch.setattr(keyspec, "read_persona_bundle", forbidden)
  monkeypatch.setattr(keyspec.config_io, "load_persona_agent", forbidden)
  monkeypatch.setattr(keyspec.config_io, "persona_store_agents", forbidden)
  return persona, calls


@pytest.mark.parametrize("state", ["missing", "null", "empty", "named"])
def test_registered_projection_uses_resolved_target_and_preserves_model(
  registered,
  state: str,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  from kanibako.targets.base import PERSONA_DEFAULT_MODEL

  persona, calls = registered
  persona.config.pop("model")
  if state != "missing":
    persona.config["model"] = {"null": PERSONA_DEFAULT_MODEL, "empty": "", "named": "m"}[state]
  conn = keyspec.resolve_connection(
    persona, "claude", endpoint=None, model=None, path="/chat/completions"
  )
  assert calls == ["goose", "project"]
  assert conn.token_path is None and conn.registered_name == "claude"
  assert conn.store_root == persona.store_root
  assert "synthetic-native-secret" not in repr(conn)
  assert conn.model == {"missing": None, "null": None, "empty": "", "named": "m"}[state]
  payloads = _serve(monkeypatch, _usable_200(finish_reason="stop"))
  keyspec.run_section(conn=conn, bearer="", prompt="p", args=_args())
  if state in ("missing", "null"):
    assert "model" not in payloads[0]
  else:
    assert payloads[0]["model"] == conn.model


@pytest.mark.parametrize("keyless", [False, True])
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("model", [None, ""])
def test_registered_run_fetches_once_or_never_for_keyless(
  registered,
  keyless: bool,
  failure: bool,
  model: str | None,
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
  capsys: pytest.CaptureFixture[str],
) -> None:
  from dataclasses import replace
  from types import SimpleNamespace

  persona, _ = registered
  persona = replace(persona, keyless=keyless)
  persona.config["model"] = None
  conn = keyspec.resolve_connection(
    persona, "claude", endpoint=None, model=model, path="/chat/completions"
  )
  fetched: list[str] = []
  sent: list[str] = []

  def fetch(name, *, root):
    assert not keyless, "keyless must not fetch"
    assert root == persona.store_root
    fetched.append(name)
    return "synthetic-bearer"

  def post(url, payload, bearer, timeout):
    sent.append(bearer)
    if model is None:
      assert "model" not in payload
    else:
      assert payload["model"] == ""
    if failure:
      return keyspec.Exchange(None, b"", f"echoed {bearer}", None)
    return keyspec.Exchange(200, _usable_200(finish_reason="stop"), None, None)

  monkeypatch.setattr(keyspec.config_io, "fetch_persona_secret", fetch)
  monkeypatch.setattr(
    keyspec, "resolve_ref", lambda ref: SimpleNamespace(persona="p", harness="legacy")
  )
  monkeypatch.setattr(keyspec, "resolve_connection", lambda *a, **kw: conn)
  monkeypatch.setattr(keyspec, "post_chat", post)
  spec = tmp_path / "spec.md"
  spec.write_text(FIXTURE)
  brief = tmp_path / "brief.md"
  brief.write_text("synthetic brief")
  assert keyspec.main(
    [
      "keyspec-extract.py",
      "--persona",
      "legacy",
      "--spec",
      str(spec),
      "--brief",
      str(brief),
      "--out",
      str(tmp_path / "out"),
      "--sections",
      "1,1A",
      "--gap",
      "0",
    ]
  ) == int(failure)
  assert fetched == ([] if keyless else ["claude"])
  attempts = 2 * (_args().max_retries + 1) if failure else 2
  assert sent == ([""] * attempts if keyless else ["synthetic-bearer"] * attempts)
  captured = capsys.readouterr()
  assert f"model={repr(model) if model is not None else '(omitted)'}\n" in captured.out
  assert "synthetic-bearer" not in captured.out + captured.err
  for artifact in (tmp_path / "out").iterdir():
    assert "synthetic-bearer" not in artifact.read_text()


def test_keyless_transport_omits_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
  requests = []

  class Opener:
    def open(self, req, *, timeout):
      requests.append(req)
      raise OSError("synthetic transport failure")

  monkeypatch.setattr(keyspec.urllib.request, "build_opener", lambda *a: Opener())
  keyspec.post_chat("https://endpoint.invalid", {}, "", 1)
  keyspec.post_chat("https://endpoint.invalid", {}, "synthetic-bearer", 1)
  assert requests[0].get_header("Authorization") is None
  assert requests[1].get_header("Authorization") == "Bearer synthetic-bearer"


def test_registered_fetch_failure_hides_exception_and_traceback(
  registered,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import traceback

  persona, _ = registered
  conn = keyspec.resolve_connection(
    persona, "claude", endpoint=None, model=None, path="/chat/completions"
  )

  def fail(name, *, root):
    assert root == persona.store_root
    raise RuntimeError("synthetic-secret-from-manager")

  monkeypatch.setattr(keyspec.config_io, "fetch_persona_secret", fail)
  with pytest.raises(SystemExit, match="cannot fetch") as caught:
    keyspec.read_bearer(conn)
  rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
  assert "synthetic-secret-from-manager" not in rendered
  assert "synthetic-secret-from-manager" not in repr(conn)


def test_registered_projection_failure_hides_native_secret(
  registered,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import traceback

  persona, _ = registered

  def fail(*args):
    raise RuntimeError(persona.config["secret"])

  monkeypatch.setattr(keyspec, "project_registered_persona", fail)
  with pytest.raises(SystemExit, match="cannot project") as caught:
    keyspec.resolve_connection(
      persona, "claude", endpoint=None, model=None, path="/chat/completions"
    )
  assert "synthetic-native-secret" not in "".join(
    traceback.format_exception(caught.type, caught.value, caught.tb)
  )


def test_transport_failure_scrubs_bearer_from_attempt_metadata(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  secret = "synthetic-echoed-bearer"
  monkeypatch.setattr(keyspec, "post_chat", lambda *a: keyspec.Exchange(None, b"", secret, None))
  args = _args()
  args.gap = 0
  _, failure, attempts = keyspec.run_section(conn=_CONN, bearer=secret, prompt="p", args=args)
  assert secret not in json.dumps([a.__dict__ for a in attempts])
  assert secret not in failure
  assert secret not in capsys.readouterr().err


@pytest.mark.parametrize("separator", ["+", "℘"])
def test_legacy_resolution_does_not_discover_registered_personas(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
  separator: str,
) -> None:
  from types import SimpleNamespace

  from kanibako.persona_store import SecretPathResult

  def forbidden(*args, **kwargs):
    pytest.fail("legacy route accessed registered PG API")

  monkeypatch.setattr(keyspec.config_io, "persona_store_agents", forbidden)
  monkeypatch.setattr(keyspec.config_io, "load_persona_agent", forbidden)
  monkeypatch.setattr(keyspec.config_io, "fetch_persona_secret", forbidden)
  entry = SimpleNamespace(harness="legacy")
  monkeypatch.setattr(keyspec, "locate_entry", lambda ref: entry)
  token = tmp_path / "legacy-token"
  token.write_text("synthetic-legacy-token\n")
  monkeypatch.setattr(keyspec, "resolve_secret_path", lambda entry: SecretPathResult(token, None))
  ref = f"p{separator}legacy"
  assert keyspec.resolve_ref(ref) is entry
  conn = keyspec.resolve_connection(
    entry, ref, endpoint="https://endpoint.invalid", model="m", path="/chat/completions"
  )
  assert conn.registered_name is None
  assert keyspec.read_bearer(conn) == "synthetic-legacy-token"
  assert keyspec.read_bearer(token) == "synthetic-legacy-token"


@pytest.mark.parametrize("keyless", [False, True])
@pytest.mark.parametrize("missing_plugin", [False, True])
def test_registered_main_resolves_once_and_fetches_from_captured_store(
  keyless: bool,
  missing_plugin: bool,
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
  capsys: pytest.CaptureFixture[str],
) -> None:
  from unittest.mock import MagicMock

  from kanibako.plugins.claude import ClaudeTarget

  root = tmp_path / "selected-store"
  entry = tmp_path / "external-entry"
  calls: list[tuple] = []
  resolved = {
    "agent": {
      "persona": {"name": "Guide"},
      "harness": {
        "name": "claude",
        "config_store": {
          "env": {"ANTHROPIC_BASE_URL": "https://endpoint.invalid/v1"},
          "model": "chosen",
        },
        "auth_var": "ANTHROPIC_AUTH_TOKEN",
      },
      "secret": None if keyless else {"backend": "synthetic"},
    }
  }

  def agents(*, root):
    calls.append(("agents", root))
    return {"TrailGuide": entry}

  def load(name, *, root):
    calls.append(("load", name, root))
    monkeypatch.setenv("AGENT_STORE_DIR", str(tmp_path / "wrong-store"))
    return entry, resolved

  def target(name):
    calls.append(("target", name))
    assert name == "claude"
    if missing_plugin:
      raise KeyError("synthetic-plugin-secret")
    return ClaudeTarget

  def spawn(argv, **kwargs):
    assert not keyless, "keyless must not run credential retrieval"
    calls.append(("fetch", argv))
    process = MagicMock(returncode=0)
    process.__enter__.return_value = process
    process.communicate.return_value = (b"synthetic-captured-bearer\n", None)
    return process

  def post(url, payload, bearer, timeout):
    calls.append(("post", url, bearer))
    assert payload["model"] == "chosen"
    return keyspec.Exchange(200, _usable_200(finish_reason="stop"), None, None)

  def forbidden(*args, **kwargs):
    pytest.fail("registered CLI entered the legacy store")

  monkeypatch.setattr(keyspec.config_io, "persona_store_location", lambda: root)
  monkeypatch.setattr(keyspec.config_io, "persona_store_agents", agents)
  monkeypatch.setattr(keyspec.config_io, "load_persona_agent", load)
  monkeypatch.setattr(keyspec.config_io.subprocess, "Popen", spawn)
  monkeypatch.setattr(keyspec, "get_target", target)
  monkeypatch.setattr(keyspec, "persona_store_root", forbidden)
  monkeypatch.setattr(keyspec, "locate_entry", forbidden)
  monkeypatch.setattr(keyspec, "resolve_secret_path", forbidden)
  monkeypatch.setattr(keyspec, "post_chat", post)
  spec = tmp_path / "spec.md"
  spec.write_text(FIXTURE)
  brief = tmp_path / "brief.md"
  brief.write_text("synthetic brief")
  out = tmp_path / "out"
  argv = [
    "keyspec-extract.py",
    "--persona",
    "TRAILGUIDE",
    "--spec",
    str(spec),
    "--brief",
    str(brief),
    "--out",
    str(out),
    "--sections",
    "1,1A",
    "--gap",
    "0",
  ]
  if missing_plugin:
    import traceback

    with pytest.raises(SystemExit, match="cannot project") as caught:
      keyspec.main(argv)
    assert "synthetic-plugin-secret" not in "".join(
      traceback.format_exception(caught.type, caught.value, caught.tb)
    )
    assert calls == [("agents", root), ("load", "TrailGuide", root), ("target", "claude")]
    return
  assert keyspec.main(argv) == 0
  assert calls[:3] == [("agents", root), ("load", "TrailGuide", root), ("target", "claude")]
  fetches = [call[1] for call in calls if call[0] == "fetch"]
  assert fetches == (
    []
    if keyless
    else [
      [
        sys.executable,
        "-I",
        "-m",
        "persona_grata",
        "--get-secret=TrailGuide",
        "--store",
        str(root),
      ]
    ]
  )
  assert [call[2] for call in calls if call[0] == "post"] == (
    ["", ""] if keyless else ["synthetic-captured-bearer", "synthetic-captured-bearer"]
  )
  captured = capsys.readouterr()
  assert "persona TrailGuide -> https://endpoint.invalid/v1/chat/completions" in captured.out
  assert "synthetic-captured-bearer" not in captured.out + captured.err
  assert all("synthetic-captured-bearer" not in p.read_text() for p in out.iterdir())


def test_bare_legacy_fallback_requires_registered_absence(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  from kanibako import persona_store

  calls = []
  root = tmp_path / "legacy"
  (root / "Guide" / "claude").mkdir(parents=True)

  def agents(*, root):
    calls.append(root)
    return {}

  monkeypatch.setattr(keyspec.config_io, "persona_store_location", lambda: tmp_path / "pg")
  monkeypatch.setattr(keyspec.config_io, "persona_store_agents", agents)
  monkeypatch.setattr(keyspec, "persona_store_root", lambda: root)
  monkeypatch.setattr(persona_store, "persona_store_root", lambda: root)
  entry = keyspec.resolve_ref("Guide")
  assert isinstance(entry, keyspec.PersonaEntry)
  assert entry.persona == "Guide" and entry.harness == "claude"
  assert calls == [tmp_path / "pg"]


def test_broken_registered_input_refuses_without_legacy_fallback_or_secret_trace(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import traceback

  def broken(*, root):
    raise RuntimeError("synthetic-native-secret")

  def forbidden(*args, **kwargs):
    pytest.fail("broken registered input fell through to legacy")

  monkeypatch.setattr(keyspec.config_io, "persona_store_location", lambda: Path("/synthetic/pg"))
  monkeypatch.setattr(keyspec.config_io, "persona_store_agents", broken)
  monkeypatch.setattr(keyspec, "persona_store_root", forbidden)
  with pytest.raises(SystemExit, match="cannot read the registered persona") as caught:
    keyspec.resolve_ref("Guide")
  assert "synthetic-native-secret" not in "".join(
    traceback.format_exception(caught.type, caught.value, caught.tb)
  )

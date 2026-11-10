"""Released PG reads, independent identity, native projection, and deferred credentials."""

import os
import select
import signal
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from persona_grata import AgentStore, make_agent

from kanibako.errors import ConfigError
from kanibako.persona_store import project_registered_persona, read_registered_persona
from kanibako.plugins.claude.target import ClaudeTarget
from kanibako.plugins.codex.target import CodexTarget
from kanibako.settings.config_io import (
  fetch_persona_secret,
  load_persona_agent,
  persona_store_agents,
  persona_store_location,
)


@pytest.fixture
def pg(tmp_path, monkeypatch):
  root = tmp_path / "agents"
  monkeypatch.setenv("AGENT_STORE_DIR", str(root))
  return AgentStore(root)


def test_empty_reads_do_not_create_store(pg):
  assert persona_store_location() == pg.root
  assert persona_store_agents() == {}
  assert read_registered_persona("scout") is None
  assert not pg.root.exists()


@pytest.mark.parametrize("harness,target", [("claude", ClaudeTarget), ("codex", CodexTarget)])
def test_read_registered_keyless_agent_preserves_identity(pg, harness, target, tmp_path):
  path = pg.create("Scout", make_agent("ollama", harness), path=tmp_path / "kept path")
  before = {p: p.read_bytes() for p in pg.root.rglob("*") if p.is_file()}
  before[path / "agent.cfg"] = (path / "agent.cfg").read_bytes()
  result = read_registered_persona("SCOUT")
  assert result.identity.node == "scout"
  assert result.identity.registered_name == "Scout"
  assert result.identity.designation == ("ollama", harness)
  assert result.identity.target_name == harness
  assert result.identity.entry_path == path
  bundle = project_registered_persona(result, target())
  assert result.keyless
  assert bundle.auth_env == ("ANTHROPIC_AUTH_TOKEN" if harness == "claude" else "")
  assert bundle.token_path is None
  assert bundle.endpoint.startswith("http://")
  assert not any(key.startswith("secret_path.") for key in bundle.to_persona_values())
  assert all(p.read_bytes() == data for p, data in before.items())
  assert not (path / "harness").exists()


@pytest.mark.parametrize("root_spelling", ["/Selected Facade/../Root", "relative Facade/../Root"])
@pytest.mark.parametrize("keyless", [False, True])
def test_read_captures_exact_facade_root_without_reconstruction(
  monkeypatch, root_spelling, keyless
):
  from kanibako.settings import config_io

  root = Path(root_spelling)
  entry = Path("/Elsewhere/Entry Link")
  assert root != entry.parent
  config = {
    "model": "",
    "env": {
      "ANTHROPIC_BASE_URL": "http://synthetic.invalid/v1",
      "ANTHROPIC_AUTH_TOKEN": "synthetic-placeholder",
      "KEEP": "{$RAW}/~",
      "EMPTY": "",
      "DROP": 12,
    },
  }
  resolved = {
    "agent": {
      "persona": {"name": "Guide_2"},
      "harness": {"name": "Claude", "config_store": config, "auth_var": "ANTHROPIC_AUTH_TOKEN"},
      "secret": None if keyless else {"backend": "synthetic"},
    }
  }
  before = deepcopy(resolved)
  forbidden = MagicMock(
    side_effect=AssertionError("unexpected discovery, fetch, write, or normalization")
  )
  discover = MagicMock(side_effect=[root, AssertionError("root rediscovered")])
  agents = MagicMock(return_value={"Scout": Path("/Registry Pointer")})
  load = MagicMock(return_value=(entry, resolved))
  monkeypatch.setattr(config_io, "persona_store_location", discover)
  monkeypatch.setattr(config_io, "persona_store_agents", agents)
  monkeypatch.setattr(config_io, "load_persona_agent", load)
  monkeypatch.setattr(config_io, "fetch_persona_secret", forbidden)
  monkeypatch.setattr(subprocess, "Popen", forbidden)
  with monkeypatch.context() as guarded:
    for name in (
      "open",
      "mkdir",
      "write_text",
      "write_bytes",
      "resolve",
      "absolute",
      "expanduser",
    ):
      guarded.setattr(Path, name, forbidden)
    result = read_registered_persona("SCOUT")
    assert result.store_root is root
    assert result.identity.entry_path is entry
    assert result.identity.registered_name == "Scout"
    assert result.identity.node == "scout"
    assert result.identity.designation == ("Guide_2", "Claude")
    assert result.identity.target_name == "claude"
    assert result.config is config
    assert result.keyless is keyless
    assert result.auth_env == (None if keyless else "ANTHROPIC_AUTH_TOKEN")
    discover.assert_called_once_with()
    agents.assert_called_once_with(root=root)
    load.assert_called_once_with("Scout", root=root)
    assert agents.call_args.kwargs["root"] is root
    assert load.call_args.kwargs["root"] is root
    agents.side_effect = load.side_effect = forbidden
    first = project_registered_persona(result, ClaudeTarget())
    assert project_registered_persona(result, ClaudeTarget()) == first
    assert result.store_root is root
    assert first.endpoint == "http://synthetic.invalid/v1"
    assert first.model == ""
    assert first.auth_env == "ANTHROPIC_AUTH_TOKEN"
    assert first.token_path is None
    assert first.env_dropped == ("DROP",)
    expected_env = {"KEEP": "{$RAW}/~", "EMPTY": ""}
    if keyless:
      expected_env["ANTHROPIC_AUTH_TOKEN"] = "synthetic-placeholder"
    assert dict(first.env) == expected_env
    assert first.to_persona_values() == {
      "endpoint": first.endpoint,
      "model": "",
      **{f"env.{k}": v for k, v in expected_env.items()},
    }
    assert resolved == before
    forbidden.assert_not_called()


@pytest.mark.parametrize("state", ["missing", "config-error", "os-error"])
def test_captured_root_preserves_facade_misses_and_errors(monkeypatch, state):
  from kanibako.settings import config_io

  root = Path("relative Facade/../Root")
  discover = MagicMock(side_effect=[root, AssertionError("root rediscovered")])
  agents = MagicMock(return_value={} if state == "missing" else {"Scout": Path("/Elsewhere")})
  error = (
    ConfigError("synthetic broken store") if state == "config-error" else OSError("synthetic I/O")
  )
  load = MagicMock(side_effect=error)
  fetch = MagicMock(side_effect=AssertionError("unexpected fetch"))
  monkeypatch.setattr(config_io, "persona_store_location", discover)
  monkeypatch.setattr(config_io, "persona_store_agents", agents)
  monkeypatch.setattr(config_io, "load_persona_agent", load)
  monkeypatch.setattr(config_io, "fetch_persona_secret", fetch)
  if state == "missing":
    assert read_registered_persona("SCOUT") is None
    load.assert_not_called()
  else:
    with pytest.raises(ConfigError) as caught:
      read_registered_persona("SCOUT")
    load.assert_called_once_with("Scout", root=root)
    if state == "config-error":
      assert caught.value is error
    else:
      assert str(caught.value) == f"persona store at {root}: cannot read agent 'SCOUT': {error}"
      assert caught.value.__cause__ is error
  discover.assert_called_once_with()
  agents.assert_called_once_with(root=root)
  fetch.assert_not_called()


def test_claude_keyless_keeps_native_placeholder_without_fetch(pg, monkeypatch):
  pg.create("scout", make_agent("ollama", "claude"))
  monkeypatch.setattr(
    "kanibako.settings.config_io.fetch_persona_secret", lambda *_: pytest.fail("fetch")
  )
  result = read_registered_persona("scout")
  assert (
    project_registered_persona(result, ClaudeTarget()).env["ANTHROPIC_AUTH_TOKEN"]
    == "no-token-required"
  )
  assert project_registered_persona(result, ClaudeTarget()).auth_env == "ANTHROPIC_AUTH_TOKEN"


@pytest.mark.parametrize(
  "keyless,native_auth,pg_auth,expected_auth",
  [
    (True, "DYNAMIC_API_KEY", None, "DYNAMIC_API_KEY"),
    (True, "", None, ""),
    (False, "NATIVE_API_KEY", "PG_API_KEY", "PG_API_KEY"),
  ],
)
def test_projection_preserves_native_keyless_name_without_io(
  monkeypatch, keyless, native_auth, pg_auth, expected_auth
):
  from kanibako.agent_ref import AgentIdentity
  from kanibako.persona_store import RegisteredPersona
  from kanibako.settings import config_io

  config = {
    "model": "",
    "model_provider": "synthetic",
    "model_providers": {
      "synthetic": {"base_url": "http://synthetic.invalid/v1", "env_key": native_auth},
    },
  }
  before = deepcopy(config)
  root = Path("relative Facade/../Root")
  identity = AgentIdentity("scout", "Scout", "Guide", "Codex", "codex", Path("/Elsewhere"))
  persona = RegisteredPersona(identity, root, config, pg_auth, keyless)
  forbidden = MagicMock(
    side_effect=AssertionError("projection attempted I/O or credential access")
  )
  for name in (
    "persona_store_location",
    "persona_store_agents",
    "load_persona_agent",
    "fetch_persona_secret",
  ):
    monkeypatch.setattr(config_io, name, forbidden)
  monkeypatch.setattr(subprocess, "Popen", forbidden)
  with monkeypatch.context() as guarded:
    for name in (
      "open",
      "read_text",
      "read_bytes",
      "write_text",
      "write_bytes",
      "stat",
      "resolve",
    ):
      guarded.setattr(Path, name, forbidden)
    bundle = project_registered_persona(persona, CodexTarget())
    assert project_registered_persona(persona, CodexTarget()) == bundle
  assert bundle.auth_env == expected_auth
  assert bundle.endpoint == "http://synthetic.invalid/v1"
  assert bundle.model == ""
  assert bundle.token_path is None
  assert dict(bundle.env) == {}
  assert bundle.to_persona_values() == {"endpoint": bundle.endpoint, "model": ""}
  assert persona.store_root is root
  assert persona.config is config
  assert persona.auth_env == pg_auth
  assert config == before
  forbidden.assert_not_called()


def test_keyed_read_leaves_credential_lookup_to_pg(pg, monkeypatch):
  pg.configure(defaults={"secret": {"backend": "pass"}})
  pg.create("scout", make_agent("minimax", "claude"))
  monkeypatch.setattr(
    "kanibako.settings.config_io.fetch_persona_secret", lambda *_: pytest.fail("fetch")
  )
  result = read_registered_persona("scout")
  assert not result.keyless
  assert project_registered_persona(result, ClaudeTarget()).auth_env == "ANTHROPIC_AUTH_TOKEN"
  assert "ANTHROPIC_AUTH_TOKEN" not in project_registered_persona(result, ClaudeTarget()).env
  assert project_registered_persona(result, ClaudeTarget()).token_path is None
  assert "fetch" not in repr(result)


def test_fetch_uses_named_pg_command(monkeypatch, tmp_path):
  process = MagicMock(returncode=0)
  process.__enter__.return_value = process
  process.communicate.return_value = (b"synthetic-token\n", None)
  spawn = MagicMock(return_value=process)
  monkeypatch.setattr(subprocess, "Popen", spawn)
  assert fetch_persona_secret("Scout", root=tmp_path) == "synthetic-token"
  spawn.assert_called_once_with(
    [sys.executable, "-I", "-m", "persona_grata", "--get-secret=Scout", "--store", str(tmp_path)],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
  )
  process.communicate.assert_called_once_with(timeout=120)


@pytest.fixture
def fake_manager(tmp_path, monkeypatch):
  bindir = tmp_path / "bin"
  bindir.mkdir()
  manager = bindir / "pass"
  manager.write_text(
    "#!/bin/sh\n"
    'printf "%s\\n" "$@" > "$PG_TEST_CALLS"\n'
    'printf "%s\\n" "$PG_TEST_TOKEN"\n'
    'printf "%s\\n" "$PG_TEST_TOKEN" >&2\n'
    'exit "${PG_TEST_STATUS:-0}"\n'
  )
  manager.chmod(0o700)
  calls = tmp_path / "calls"
  monkeypatch.setenv("PATH", str(bindir))
  monkeypatch.setenv("PG_TEST_CALLS", str(calls))
  monkeypatch.setenv("PG_TEST_TOKEN", "synthetic-token")
  return calls


def test_released_pg_fetches_by_registered_name(pg, fake_manager, capsys):
  pg.configure(defaults={"secret": {"backend": "pass"}})
  path = pg.create("Scout", make_agent("minimax", "claude"))
  before = {p: p.read_bytes() for p in pg.root.rglob("*") if p.is_file()}
  assert persona_store_agents() == {"Scout": path}
  selected = read_registered_persona("SCOUT")
  assert fetch_persona_secret(selected.identity.registered_name) == "synthetic-token"
  assert fake_manager.read_text().splitlines() == ["show", "agents/Scout"]
  assert {p: p.read_bytes() for p in pg.root.rglob("*") if p.is_file()} == before
  assert capsys.readouterr() == ("", "")


def test_final_pg_preparation_uses_the_captured_store_once(
  pg, fake_manager, tmp_path, monkeypatch
):
  from kanibako.commands.start import _prepare_resolved_persona_delivery
  from kanibako.settings.settings_categories import (
    MOUNT,
    SECRET_MOUNT_DIR,
    CategoryEntry,
    CredentialState,
    LaunchDeliveries,
    PersonaCredential,
  )
  from kanibako.targets.base import PersonaProbeOutcome

  pg.configure(defaults={"secret": {"backend": "pass"}})
  path = pg.create("Scout", make_agent("minimax", "claude"), path=tmp_path / "elsewhere")
  selected = read_registered_persona("SCOUT")
  monkeypatch.setenv("AGENT_STORE_DIR", str(tmp_path / "wrong-store"))
  var = "ANTHROPIC_AUTH_TOKEN"
  source = PersonaCredential(selected.identity.registered_name, selected.store_root, var, False)
  declaration = CategoryEntry(
    "secret_path",
    "agent",
    f"{SECRET_MOUNT_DIR}/{var}",
    None,
    MOUNT,
    "ro",
    var,
    ("agent", selected.identity.node, "secret_path", var),
    persona_credential=source,
  )
  deliveries = LaunchDeliveries(
    [],
    frozenset(),
    credential_states={var: CredentialState("pg", declaration)},
    active_secret_names={var: [declaration.key]},
  )
  before = {p: p.read_bytes() for root in (pg.root, path) for p in root.rglob("*") if p.is_file()}
  sent = []

  def verify(endpoint, pointer, model, *, env=None, token=None):
    sent.append((pointer, token))
    return PersonaProbeOutcome.passed()

  monkeypatch.setattr(ClaudeTarget, "verify_persona", staticmethod(verify))
  result, payload = _prepare_resolved_persona_delivery(
    selected.identity.node,
    deliveries,
    "https://endpoint.invalid",
    None,
    target=ClaudeTarget(),
    env={},
    cli_env={},
    probe_model=None,
    provider_model=None,
  )
  assert result == ("https://endpoint.invalid", None, None)
  assert fake_manager.read_text().splitlines() == ["show", "agents/Scout"]
  assert sent == [(None, "synthetic-token")]
  assert payload.names == (var,) and payload.values[0].value == b"synthetic-token"
  assert all(p.read_bytes() == value for p, value in before.items())
  assert not (tmp_path / "wrong-store").exists()


def test_named_fetch_uses_explicit_store(pg, fake_manager, tmp_path, monkeypatch):
  other = AgentStore(tmp_path / "other")
  other.configure(defaults={"secret": {"backend": "pass"}})
  other.create("scout", make_agent("minimax", "claude"))
  assert fetch_persona_secret("scout", root=other.root) == "synthetic-token"
  assert not pg.root.exists()
  monkeypatch.setenv("PG_TEST_TOKEN", "changed-token")
  assert fetch_persona_secret("scout", root=other.root) == "changed-token"


@pytest.mark.parametrize("kind", ["missing", "keyless", "manager-failure", "invalid-name"])
def test_released_pg_fetch_failure_is_private(pg, fake_manager, monkeypatch, capsys, kind):
  pg.configure(defaults={"secret": {"backend": "pass"}})
  name = "scout"
  if kind == "keyless":
    import yapyon

    definition = make_agent("ollama", "claude")
    definition.merge(yapyon.parse("agent:\n  secret:= None\n"))
    pg.create(name, definition)
    assert read_registered_persona(name).keyless
  elif kind == "manager-failure":
    pg.create(name, make_agent("minimax", "claude"))
    monkeypatch.setenv("PG_TEST_STATUS", "1")
  elif kind == "invalid-name":
    name = "--help"
  with pytest.raises(ConfigError, match="credential retrieval failed") as error:
    fetch_persona_secret(name)
  assert "synthetic-token" not in str(error.value)
  assert error.value.__cause__ is None
  assert capsys.readouterr() == ("", "")
  if kind != "manager-failure":
    assert not fake_manager.exists()


def test_named_fetch_tracks_pg_rename(pg, fake_manager):
  pg.configure(defaults={"secret": {"backend": "pass"}})
  pg.create("scout", make_agent("minimax", "claude"))
  pg.rename("scout", "renamed")
  with pytest.raises(ConfigError, match="credential retrieval failed"):
    fetch_persona_secret("scout")
  assert not fake_manager.exists()
  assert fetch_persona_secret("renamed") == "synthetic-token"


@pytest.mark.parametrize(
  "output", [b"", b"\n", b"  \n", b"a\nb\n", b"a\x00b\n", b"a\rb\n", b"\xff\n"]
)
def test_named_fetch_rejects_invalid_output(monkeypatch, output):
  process = MagicMock(returncode=0)
  process.__enter__.return_value = process
  process.communicate.return_value = (output, None)
  monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: process)
  with pytest.raises(ConfigError, match="credential") as error:
    fetch_persona_secret("scout")
  assert repr(output) not in str(error.value)


@pytest.mark.parametrize("failure", ["timeout", "execution", "status"])
def test_named_fetch_never_echoes_child_errors(monkeypatch, capsys, failure):
  process = MagicMock(returncode=9, pid=12345)
  process.__enter__.return_value = process
  process.communicate.return_value = (b"synthetic-token", None)
  if failure == "timeout":
    process.communicate.side_effect = [
      subprocess.TimeoutExpired("pg", 120, output=b"synthetic-token", stderr=b"synthetic-token"),
      (b"synthetic-token", None),
    ]
  spawn = MagicMock(return_value=process)
  if failure == "execution":
    spawn.side_effect = OSError("synthetic-token")
  monkeypatch.setattr(subprocess, "Popen", spawn)
  kill = MagicMock()
  monkeypatch.setattr(os, "killpg", kill)
  with pytest.raises(ConfigError, match="credential retrieval") as error:
    fetch_persona_secret("scout")
  import traceback

  assert "synthetic-token" not in "".join(traceback.format_exception(error.value))
  assert capsys.readouterr() == ("", "")
  if failure == "timeout":
    kill.assert_called_once_with(process.pid, signal.SIGKILL)
    assert process.communicate.call_count == 2
  else:
    kill.assert_not_called()


@pytest.mark.parametrize("already_exited", [False, True])
def test_named_fetch_cancellation_reaps_invocation(monkeypatch, already_exited):
  process = MagicMock(pid=12345)
  process.__enter__.return_value = process
  process.communicate.side_effect = [KeyboardInterrupt, (b"", None)]
  monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: process)
  kill = MagicMock(side_effect=ProcessLookupError if already_exited else None)
  monkeypatch.setattr(os, "killpg", kill)
  with pytest.raises(KeyboardInterrupt):
    fetch_persona_secret("scout")
  kill.assert_called_once_with(process.pid, signal.SIGKILL)
  assert process.communicate.call_count == 2


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="requires Linux process-exit handles")
def test_timeout_stops_real_pg_and_manager_children(pg, fake_manager, monkeypatch):
  pg.configure(defaults={"secret": {"backend": "pass"}})
  pg.create("scout", make_agent("minimax", "claude"))
  manager = fake_manager.parent / "bin" / "pass"
  manager.write_text(
    '#!/bin/sh\n/bin/sleep 60 &\nprintf "%s\\n" "$$" "$!" > "$PG_TEST_CALLS"\nwait\n'
  )
  communicate = subprocess.Popen.communicate

  def bounded(self, *args, **kwargs):
    if kwargs.get("timeout") == 120:
      kwargs["timeout"] = 1
    return communicate(self, *args, **kwargs)

  monkeypatch.setattr(subprocess.Popen, "communicate", bounded)
  with pytest.raises(ConfigError, match="timed out"):
    fetch_persona_secret("scout")
  pids = [int(pid) for pid in fake_manager.read_text().splitlines()]
  assert len(pids) == 2
  for pid in pids:
    try:
      descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
      continue
    try:
      assert select.select([descriptor], [], [], 1)[0] == [descriptor]
    finally:
      os.close(descriptor)


def test_identity_read_does_not_inspect_backend_metadata(pg, monkeypatch):
  from kanibako.settings import config_io

  pg.configure(defaults={"secret": {"backend": "pass"}})
  pg.create("scout", make_agent("minimax", "claude"))
  original = config_io.load_persona_agent

  def load(name, *, root=None):
    path, data = original(name, root=root)
    del data["runtime"]
    return path, data

  monkeypatch.setattr(config_io, "load_persona_agent", load)
  assert not read_registered_persona("scout").keyless


def test_names_do_not_alias_same_designation(pg):
  one = pg.create("one", make_agent("ollama", "claude"))
  two = pg.create("two", make_agent("ollama", "claude"))
  assert read_registered_persona("one").identity.entry_path == one
  assert read_registered_persona("two").identity.entry_path == two


def test_case_ambiguity_refuses(pg):
  pg.create("Scout", make_agent("ollama", "claude"))
  pg.create("scout", make_agent("ollama", "claude"))
  with pytest.raises(ConfigError, match="ambiguous by case"):
    read_registered_persona("Scout")


def test_invalid_store_is_not_a_miss(pg):
  pg.root.mkdir()
  pg.registry_path.write_text('format: "broken"\n')
  with pytest.raises(ConfigError, match="unsupported agent store format"):
    read_registered_persona("scout")


@pytest.mark.parametrize("component", ["persona", "harness"])
@pytest.mark.parametrize("name", ["bad-name", "bad.name", "9start", "a" * 65, "かに"])
def test_resolved_component_spelling_refuses(pg, component, name):
  import yapyon

  definition = make_agent("ollama", "claude")
  definition.merge(yapyon.parse(f"agent:\n  {component}:\n    name:= {name!r}\n"))
  pg.create("Scout", definition)
  with pytest.raises(ConfigError, match=f"Scout.*{component} name") as error:
    read_registered_persona("scout")
  assert repr(name) in str(error.value)
  if name == "bad-name":
    assert "try 'bad_name'" in str(error.value)


@pytest.mark.parametrize("component", ["persona", "harness"])
@pytest.mark.parametrize(
  "name", ["shell", "Shell", "SHELL", "sHeLl", "default", "Default", "DEFAULT", "dEfAuLt"]
)
def test_resolved_component_reservation_refuses(pg, component, name):
  import yapyon

  definition = make_agent("ollama", "claude")
  definition.merge(yapyon.parse(f"agent:\n  {component}:\n    name:= {name!r}\n"))
  pg.create("Scout", definition)
  with pytest.raises(ConfigError, match=f"Scout.*{component}.*RESERVED pseudo-agent"):
    read_registered_persona("scout")


@pytest.mark.parametrize("component", ["persona", "harness"])
@pytest.mark.parametrize("name", ["Guide_2", "_guide", "a" * 64, "guide"])
def test_resolved_component_names_stay_distinct(pg, tmp_path, component, name):
  import yapyon

  definition = make_agent("ollama", "claude")
  definition.merge(yapyon.parse(f"agent:\n  {component}:\n    name:= {name!r}\n"))
  path = pg.create("Scout", definition, path=tmp_path / "Entry Path")
  identity = read_registered_persona("scout").identity
  persona, harness = (name, "claude") if component == "persona" else ("ollama", name)
  assert identity.registered_name == "Scout"
  assert identity.node == "scout"
  assert identity.designation == (persona, harness)
  assert identity.target_name == harness.lower()
  assert identity.entry_path == path


@pytest.mark.parametrize("name", [None, ""])
def test_missing_persona_name_refuses(pg, name):
  import yapyon

  definition = make_agent("ollama", "claude")
  definition.merge(yapyon.parse(f"agent:\n  persona:\n    name:= {name!r}\n"))
  pg.create("Scout", definition)
  with pytest.raises(ConfigError, match="Scout.*persona name"):
    read_registered_persona("scout")


@pytest.mark.parametrize(
  "document,reader",
  [
    ("agent", "registered"),
    ("agent", "load"),
    ("registry", "registered"),
    ("registry", "load"),
    ("registry", "agents"),
    ("component", "registered"),
    ("component", "load"),
  ],
)
def test_public_pg_syntax_errors_are_config_errors(pg, monkeypatch, document, reader):
  from yapyon import AkanError

  path = pg.create("Scout", make_agent("ollama", "claude"))
  source = {
    "agent": path / "agent.cfg",
    "registry": pg.registry_path,
    "component": pg.root / "personas" / "ollama.persona.tmpl",
  }[document]
  source.write_text("agent: [\n")
  before = {p: p.read_bytes() for p in pg.root.rglob("*") if p.is_file()}
  monkeypatch.setattr(
    "kanibako.settings.config_io.fetch_persona_secret", lambda *_: pytest.fail("fetch")
  )
  assert persona_store_location() == pg.root
  read = {
    "registered": lambda: read_registered_persona("Scout"),
    "load": lambda: load_persona_agent("Scout", root=pg.root),
    "agents": lambda: persona_store_agents(root=pg.root),
  }[reader]
  with pytest.raises(ConfigError) as error:
    read()
  cause = error.value.__cause__
  assert isinstance(cause, AkanError)
  assert cause.source == str(source)
  assert str(source) in str(error.value)
  assert str(cause) in str(error.value)
  assert {p: p.read_bytes() for p in pg.root.rglob("*") if p.is_file()} == before


def test_wrong_harness_is_not_projected(pg):
  pg.create("scout", make_agent("ollama", "codex"))
  with pytest.raises(ConfigError, match="uses harness 'codex'"):
    project_registered_persona(read_registered_persona("scout"), ClaudeTarget())


def test_public_load_tracks_live_configuration_and_rename(pg):
  path = pg.create("scout", make_agent("ollama", "claude"))
  pg.rename("scout", "renamed")
  loaded_path, data = load_persona_agent("renamed")
  assert loaded_path == path
  assert data["agent"]["name"] == "renamed"
  assert read_registered_persona("scout") is None
  assert read_registered_persona("renamed").identity.entry_path == path


@pytest.mark.parametrize("model", [None, "", "concrete-model"])
def test_projection_preserves_present_model_states(pg, monkeypatch, model):
  from kanibako.settings import config_io
  from kanibako.targets.base import PERSONA_DEFAULT_MODEL

  pg.create("scout", make_agent("ollama", "claude"))
  original = config_io.load_persona_agent

  def load(name, *, root=None):
    path, data = original(name, root=root)
    data["agent"]["harness"]["config_store"]["model"] = model
    return path, data

  monkeypatch.setattr(config_io, "load_persona_agent", load)
  bundle = project_registered_persona(read_registered_persona("scout"), ClaudeTarget())
  assert bundle.model is PERSONA_DEFAULT_MODEL if model is None else bundle.model == model
  assert bundle.to_persona_values()["model"] == model


def test_absent_native_model_contributes_nothing(pg, monkeypatch):
  from kanibako.settings import config_io

  pg.create("scout", make_agent("ollama", "claude"))
  original = config_io.load_persona_agent

  def load(name, *, root=None):
    path, data = original(name, root=root)
    data["agent"]["harness"]["config_store"].pop("model", None)
    return path, data

  monkeypatch.setattr(config_io, "load_persona_agent", load)
  assert (
    "model"
    not in project_registered_persona(
      read_registered_persona("scout"), ClaudeTarget()
    ).to_persona_values()
  )


def test_read_is_live_after_public_update(pg):
  import yapyon

  pg.create("scout", make_agent("minimax", "claude"))
  before = project_registered_persona(read_registered_persona("scout"), ClaudeTarget()).model
  definition = yapyon.parse(
    "agent<base.agent>:\n  persona<minimax.persona>: {}\n"
    '  harness<claude.harness>:\n    config_store: {model: "new-live-model"}\n'
  )
  pg.update("scout", definition)
  after = project_registered_persona(read_registered_persona("scout"), ClaudeTarget()).model
  assert before != after == "new-live-model"


def test_registered_agent_symlink_uses_pg_location(pg, tmp_path):
  real = tmp_path / "real-agent"
  pg.create("scout", make_agent("ollama", "claude"), path=real)
  relocated = tmp_path / "relocated-agent"
  real.rename(relocated)
  real.symlink_to(relocated, target_is_directory=True)
  result = read_registered_persona("scout")
  assert result.identity.entry_path == real
  assert project_registered_persona(result, ClaudeTarget()).endpoint


def test_native_dialect_endpoint_is_not_persona_endpoint(pg):
  pg.create("scout", make_agent("minimax", "claude"))
  result = read_registered_persona("scout")
  bundle = project_registered_persona(result, ClaudeTarget())
  _, data = load_persona_agent("scout")
  assert bundle.endpoint == data["agent"]["harness"]["config_store"]["env"]["ANTHROPIC_BASE_URL"]
  assert bundle.endpoint != data["agent"]["persona"]["mind"]["endpoint"]


def test_missing_and_broken_entries_are_distinct(pg):
  path = pg.create("scout", make_agent("ollama", "claude"))
  (path / "agent.cfg").write_text('agent: "not a mapping"\n')
  assert read_registered_persona("absent") is None
  with pytest.raises(ConfigError, match="scout"):
    read_registered_persona("scout")


def test_default_store_root_is_owned_by_pg(tmp_path, monkeypatch):
  monkeypatch.delenv("AGENT_STORE_DIR", raising=False)
  monkeypatch.setenv("HOME", str(tmp_path))
  monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
  assert persona_store_location() == tmp_path / ".config" / "agents"
  monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
  assert persona_store_location() == tmp_path / "config" / "agents"


def test_identity_read_precedes_target_discovery(pg, monkeypatch):
  pg.create("scout", make_agent("ollama", "codex"))

  def unexpected(*args, **kwargs):
    pytest.fail("read must not discover, instantiate, or project a target")

  monkeypatch.setattr("kanibako.targets.discover_targets", unexpected)
  monkeypatch.setattr("kanibako.targets.resolve_target", unexpected)
  monkeypatch.setattr(CodexTarget, "project_persona_settings", unexpected)
  result = read_registered_persona("scout")
  assert result.identity.harness == "codex"
  assert result.identity.target_name == "codex"


def test_projection_never_rereads_store_or_fetches(pg, monkeypatch):
  from copy import deepcopy
  from kanibako.settings import config_io

  pg.create("scout", make_agent("ollama", "claude"))
  result = read_registered_persona("scout")
  before = deepcopy(result.config)

  def unexpected(*args, **kwargs):
    pytest.fail("projection must use the already-read value")

  for name in ("persona_store_location", "persona_store_agents", "load_persona_agent"):
    monkeypatch.setattr(config_io, name, unexpected)
  monkeypatch.setattr("kanibako.settings.config_io.fetch_persona_secret", unexpected)
  first = project_registered_persona(result, ClaudeTarget())
  second = project_registered_persona(result, ClaudeTarget())
  assert first == second
  assert first.endpoint
  assert result.config == before


def test_projection_retains_read_snapshot_after_public_update(pg):
  import yapyon

  pg.create("scout", make_agent("minimax", "claude"))
  original = read_registered_persona("scout")
  before = project_registered_persona(original, ClaudeTarget())
  pg.update(
    "scout",
    yapyon.parse(
      "agent<base.agent>:\n  persona<minimax.persona>: {}\n"
      '  harness<claude.harness>:\n    config_store: {model: "changed"}\n'
    ),
  )
  assert project_registered_persona(original, ClaudeTarget()) == before
  fresh = read_registered_persona("scout")
  assert project_registered_persona(fresh, ClaudeTarget()).model == "changed"


def test_registered_selection_carries_its_single_public_read(pg, tmp_path, monkeypatch):
  from unittest.mock import Mock

  from kanibako import persona_store
  from kanibako.settings.agent_select import select_agent
  from tests.test_settings.test_agent_select import _proj, _std

  pg.create("Scout", make_agent("ollama", "claude"))
  read = Mock(wraps=persona_store.read_registered_persona)
  monkeypatch.setattr(persona_store, "read_registered_persona", read)
  selection = select_agent(std=_std(tmp_path), proj=_proj(tmp_path), explicit_agent="SCOUT")
  assert selection.node == "scout"
  assert selection.identity.registered_name == "Scout"
  assert selection.identity.target_name == "claude"
  assert selection.registered_persona.identity.entry_path == selection.identity.entry_path
  assert selection.registered_persona.store_root == pg.root
  read.assert_called_once_with("SCOUT")


def test_explicit_plugin_wins_without_reading_colliding_pg_agent(pg, monkeypatch):
  from unittest.mock import Mock

  from kanibako.settings.config import resolve_agent_identity

  pg.create("Claude", make_agent("ollama", "codex"))
  forbidden = Mock(side_effect=AssertionError("plugin selection must not read PG"))
  monkeypatch.setattr("kanibako.persona_store.read_registered_persona", forbidden)
  identity = resolve_agent_identity(explicit_agent="Claude")
  assert identity.node == "claude"
  assert identity.registered_name is None
  assert identity.target_name == "claude"
  forbidden.assert_not_called()


def test_command_read_to_pipe_to_existing_entrypoint(
  pg, fake_manager, std, config, project_dir, tmp_path, monkeypatch, start_mocks
):
  import hashlib
  import json
  import logging
  from unittest.mock import Mock

  from kanibako.commands import start
  from kanibako.secret_delivery import SECRET_READY_ACK, encode_payload
  from kanibako.settings import config_io
  from kanibako.settings.agent_config import agent_settings_path
  from kanibako.settings.agent_select import select_agent
  from kanibako.settings.paths import resolve_project
  from kanibako.settings.settings_categories import PersonaCredential
  from kanibako.targets.base import PersonaProbeOutcome

  pg.configure(defaults={"secret": {"backend": "pass"}})
  pg.create("Scout", make_agent("minimax", "claude"))
  proj = resolve_project(std, config, str(project_dir), initialize=True)
  selected = select_agent(std=std, proj=proj, explicit_agent="Scout")
  persona = selected.registered_persona
  target = ClaudeTarget()
  bundle = project_registered_persona(persona, target)
  credential = PersonaCredential("Scout", persona.store_root, persona.auth_env, False)
  snapshot, deliveries = start._resolve_launch_snapshot(
    std=std,
    proj=proj,
    agent_name=selected.node,
    agent_identity=selected.identity,
    system_settings_path=None,
    agent_cfg_path=agent_settings_path(std.agents, selected.node),
    desc=target.descriptor,
    install=None,
    target=target,
    persona_values=bundle.to_persona_values(),
    persona_credential=credential,
    required_var=persona.auth_env,
    guarantee_create=False,
    cli_level=selected.selection_level,
  )
  fetch = Mock(wraps=config_io.fetch_persona_secret)
  monkeypatch.setattr(config_io, "fetch_persona_secret", fetch)
  monkeypatch.setattr(target, "verify_persona", lambda *a, **kw: PersonaProbeOutcome.passed())
  prepared, payload = start._prepare_resolved_persona_delivery(
    selected.node,
    deliveries,
    bundle.endpoint,
    logging.getLogger(__name__),
    target=target,
    env=start._build_config_env(start._launch_env_map(snapshot)),
    cli_env={},
    probe_model=bundle.model,
    provider_model=bundle.model,
    bundle=bundle,
  )
  assert prepared[1] is None
  fetch.assert_called_once_with("Scout", root=persona.store_root)
  assert fake_manager.read_text().splitlines() == ["show", "agents/Scout"]
  mounts = []
  env, exports = start._assemble_launch_env(
    env_slots=start._launch_env_map(snapshot),
    deliveries=deliveries,
    extra_mounts=mounts,
    logger=logging.getLogger(__name__),
  )
  assert persona.auth_env not in env
  assert persona.auth_env not in exports
  assert not mounts
  assert "synthetic-token" not in repr(snapshot)
  monkeypatch.setattr(
    start, "KANIBAKO_PKG_MOUNT_ROOT", str(Path(start.__file__).resolve().parents[2])
  )
  program, args = start._pg_secret_pipe_shim(
    sys.executable,
    [
      "-I",
      "-c",
      (
        "import os,json,hashlib;closed=[]\n"
        "for fd in (3,4):\n"
        " try:os.fstat(fd)\n"
        " except OSError:closed.append(fd)\n"
        "print(json.dumps([os.getpid(),hashlib.sha256(os.environ['ANTHROPIC_AUTH_TOKEN'].encode()).hexdigest(),closed]));raise SystemExit(23)"
      ),
    ],
  )
  assert program == "python3"
  assert "synthetic-token" not in repr(args)
  r, w = os.pipe()
  ar, aw = os.pipe()
  bootstrap = "import os,sys;r,a=map(int,sys.argv[1:3]);os.dup2(r,3);os.dup2(a,4);[os.close(fd) for fd in (r,a) if fd not in (3,4)];os.execv(sys.executable,[sys.executable,*sys.argv[3:]])"
  process = subprocess.Popen(
    [sys.executable, "-I", "-c", bootstrap, str(r), str(aw), *args],
    pass_fds=(r, aw),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
  )
  os.close(r)
  os.close(aw)
  try:
    os.write(w, encode_payload(payload))
    os.close(w)
    w = -1
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 23
    assert not stderr
    assert os.read(ar, 2) == SECRET_READY_ACK
    assert json.loads(stdout) == [
      process.pid,
      hashlib.sha256(b"synthetic-token").hexdigest(),
      [3, 4],
    ]
  finally:
    if w >= 0:
      os.close(w)
    os.close(ar)
    if process.poll() is None:
      process.kill()
      process.wait(timeout=2)

  from unittest.mock import patch
  from kanibako.settings.settings_launch import AuthSource

  fetch.reset_mock()
  auth = AuthSource(
    tier="global",
    global_enabled=True,
    workset_enabled=False,
    global_sync=False,
    workset_source=None,
  )
  with (
    start_mocks() as mocks,
    patch.object(
      start,
      "_resolve_box_launch_decisions",
      return_value=(auth, bundle.endpoint, bundle.model, {}),
    ),
  ):
    mocks.resolve_agent.return_value = selected
    mocks.target.name = "claude"
    mocks.target.descriptor = target.descriptor
    mocks.target.setting_descriptors.return_value = target.setting_descriptors()
    mocks.target.project_persona_settings = target.project_persona_settings
    mocks.target.verify_persona = target.verify_persona
    mocks.agent_cfg.state.update(endpoint=bundle.endpoint, model=bundle.model)
    fixture_resolve = mocks.resolve_launch_snapshot.side_effect

    def resolved(**kw):
      if kw.get("narrow_bind_dests") is not None:
        return fixture_resolve(**kw)
      if kw.get("realize") is not None:
        kw["realize"](snapshot)
      return snapshot, deliveries

    mocks.resolve_launch_snapshot.side_effect = resolved
    mocks.runtime.run.return_value = 0
    result = start._run_container(
      project_dir=None,
      entrypoint=None,
      image_override=None,
      new_session=False,
      safe_mode=False,
      resume_mode=False,
      extra_args=[],
      no_helpers=True,
    )
    assert result == 0
    launched = mocks.runtime.run.call_args.kwargs
    assert launched["secret_payload"].values[0].value == b"synthetic-token"
    assert launched["entrypoint"] == "python3"
    assert "synthetic-token" not in repr(launched["cli_args"])
    assert persona.auth_env not in launched["env"]
    fetch.assert_called_once_with("Scout", root=persona.store_root)


def test_registered_create_checks_metadata_without_fetching(
  pg, fake_manager, std, config, project_dir
):
  from kanibako.commands.start import persona_create_verdict
  from kanibako.settings.paths import resolve_project

  pg.configure(defaults={"secret": {"backend": "pass"}})
  pg.create("Scout", make_agent("minimax", "claude"))
  proj = resolve_project(std, config, str(project_dir), initialize=True)
  assert persona_create_verdict(std, config, proj, explicit_agent="Scout") is None
  assert not fake_manager.exists()

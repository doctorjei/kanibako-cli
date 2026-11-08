"""Released PG reads, independent identity, native projection, and deferred credentials."""

import os
import select
import signal
import subprocess
import sys
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
  assert bundle.auth_env is None
  assert bundle.token_path is None
  assert bundle.endpoint.startswith("http://")
  assert not any(key.startswith("secret_path.") for key in bundle.to_persona_values())
  assert all(p.read_bytes() == data for p, data in before.items())
  assert not (path / "harness").exists()


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
  assert project_registered_persona(result, ClaudeTarget()).auth_env is None


def test_keyed_read_leaves_credential_lookup_to_pg(pg, monkeypatch):
  pg.configure(secret_backend="pass")
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
  pg.configure(secret_backend="pass")
  path = pg.create("Scout", make_agent("minimax", "claude"))
  before = {p: p.read_bytes() for p in pg.root.rglob("*") if p.is_file()}
  assert persona_store_agents() == {"Scout": path}
  selected = read_registered_persona("SCOUT")
  assert fetch_persona_secret(selected.identity.registered_name) == "synthetic-token"
  assert fake_manager.read_text().splitlines() == ["show", "agents/Scout"]
  assert {p: p.read_bytes() for p in pg.root.rglob("*") if p.is_file()} == before
  assert capsys.readouterr() == ("", "")


def test_named_fetch_uses_explicit_store(pg, fake_manager, tmp_path, monkeypatch):
  other = AgentStore(tmp_path / "other")
  other.configure(secret_backend="pass")
  other.create("scout", make_agent("minimax", "claude"))
  assert fetch_persona_secret("scout", root=other.root) == "synthetic-token"
  assert not pg.root.exists()
  monkeypatch.setenv("PG_TEST_TOKEN", "changed-token")
  assert fetch_persona_secret("scout", root=other.root) == "changed-token"


@pytest.mark.parametrize("kind", ["missing", "keyless", "manager-failure", "invalid-name"])
def test_released_pg_fetch_failure_is_private(pg, fake_manager, monkeypatch, capsys, kind):
  pg.configure(secret_backend="pass")
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
  pg.configure(secret_backend="pass")
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
  pg.configure(secret_backend="pass")
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

  pg.configure(secret_backend="pass")
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
    ("agent", "registered"), ("agent", "load"),
    ("registry", "registered"), ("registry", "load"), ("registry", "agents"),
    ("component", "registered"), ("component", "load"),
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

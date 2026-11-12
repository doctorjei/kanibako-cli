"""The launch's persona verify probe reads the env the BOX receives, not the store's block.

A claude persona whose model is the tier alias ``sonnet`` is sent, in the box, as
``$ANTHROPIC_DEFAULT_SONNET_MODEL``.  The probe used to see only the persona store's
``env:`` block, so a mapping at any other rung (or a keyspace-only persona with no store
block at all) probed the RAW alias, and a 403 on it refused a working box.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from kanibako.settings.config_io import convert_legacy_doc_text, dump_doc, load_doc
from kanibako.plugins.claude import ClaudeTarget
from kanibako.commands.start import (
  _resolve_launch_snapshot as _REAL_FINAL_RESOLVE,
  _resolve_box_launch_decisions as _REAL_DECISIONS,
)

NODE = "navigator+claude"
#: The INTERNAL node id a launch resolves under; files spell it ``NODE``.
NODE_ID = "navigator\u2118claude"
VAR = "ANTHROPIC_DEFAULT_SONNET_MODEL"
MAPPED = "claude-sonnet-4-5-20990101"


def _table(floor, *, cli_env=None):
  """The pre-flight table, off the PRODUCTION chain (build → category entries)."""
  from kanibako.commands.start import _persona_secret_table
  from kanibako.settings.settings_launch import build_launch_snapshot
  from kanibako.settings.settings_resolve import ResolveCtx

  ctx = ResolveCtx(
    agent_name=NODE,
    workset_name="ws",
    host_home="/home/u",
    xdg={"XDG_DATA_HOME": "/data"},
    config={},
  )
  snap = build_launch_snapshot(
    agent_name=NODE,
    ctx=ctx,
    system_path=None,
    agent_path=None,
    workset_path=None,
    box_path=None,
    default_categories=floor,
  )
  return _persona_secret_table(
    snap,
    NODE,
    ctx,
    **({"cli_env": cli_env} if cli_env is not None else {}),
  )


@pytest.mark.parametrize(
  "key",
  [
    f"agent.{NODE}.env.{VAR}",
    f"agent.default.env.{VAR}",
    f"workset.env.{VAR}",
    f"box.env.{VAR}",
    f"system.env.{VAR}",
  ],
)
def test_every_rung_reaches_the_probe_env(key):
  assert _table({key: MAPPED}).env[VAR] == MAPPED


def test_a_per_run_e_reaches_the_probe_env():
  """``-e`` overrides the key's value in the box, so it does in the probe too."""
  table = _table({f"agent.{NODE}.env.{VAR}": "from-key"}, cli_env={VAR: MAPPED})
  assert table.env[VAR] == MAPPED


def test_a_refused_fold_leaves_no_env_and_the_probe_falls_back_to_the_store():
  """Two scopes naming one VAR: the launch refuses later; the pre-flight must not."""
  from kanibako.commands.start import _persona_probe_env

  table = _table({f"system.env.{VAR}": "a", f"box.env.{VAR}": "b"})
  assert table.env is None
  bundle = mock.Mock(env={VAR: "store"})
  assert _persona_probe_env(table, bundle) == {VAR: "store"}
  assert _persona_probe_env(table, None) is None


def _probe_wire_model(std, proj, system_file, agent_file, *, cli_env=None):
  """Run the LAUNCH pre-flight with a real ClaudeTarget; return the model it sent."""
  from kanibako.commands import start as start_cmd
  from tests.support.agent_selection import agent_selection

  target = ClaudeTarget()
  selection = agent_selection(node=NODE_ID, source="settings").selection_level
  _auth, endpoint, model, secrets = start_cmd._resolve_box_launch_decisions(
    std=std,
    proj=proj,
    target=target,
    agent_name=NODE_ID,
    system_settings_path=system_file,
    agent_cfg_path=agent_file,
    selection_level=selection,
    # Only when given, so the base tree's RED is the WIRE model, not a TypeError.
    **({"cli_env": cli_env} if cli_env is not None else {}),
  )
  sent: dict = {}

  def _model_access(url, *, body, **_kw):
    sent.update(body)
    raise RuntimeError("stop after capture")

  with (
    mock.patch(
      "kanibako.plugins.claude.target.probe_general_access",
      return_value=mock.Mock(),
    ),
    mock.patch(
      "kanibako.plugins.claude.target.probe_model_access",
      side_effect=_model_access,
    ),
  ):
    _ep, err, _prov = start_cmd._preflight_persona_load(
      NODE_ID,
      secrets,
      endpoint,
      None,
      target=target,
      keyspace_model=model,
      bundle=None,
      probe=True,
    )
  assert err is None
  return sent.get("model"), secrets


def _persona_files(tmp_path: Path) -> tuple[Path, Path]:
  """A KEYSPACE-ONLY claude persona (no store block) aliased to ``sonnet``.

  The tier mapping sits at the AGENT rung — the persona's own agent file.
  """
  token = tmp_path / "token"
  token.write_text("t0k\n")
  system_file = tmp_path / "settings.yaml"
  system_file.write_text(
    convert_legacy_doc_text(
      f"agent:\n  {NODE}:\n"
      f"    endpoint: https://proxy.example\n"
      f"    model: sonnet\n"
      f"    secret_path:\n      ANTHROPIC_AUTH_TOKEN: {token}\n"
    )
  )
  agent_file = tmp_path / "agent.yaml"
  agent_file.write_text(
    convert_legacy_doc_text(f"agent:\n  {NODE}:\n    env:\n      {VAR}: {MAPPED}\n")
  )
  return system_file, agent_file


def test_the_launch_probe_sends_the_mapped_id_not_the_alias(
  std,
  config,
  project_dir,
  tmp_path: Path,
):
  """RED on the base: the probe sent ``sonnet``; the box sends the mapped id."""
  from kanibako.settings.paths import resolve_project

  proj = resolve_project(std, config, str(project_dir), initialize=True)
  system_file, agent_file = _persona_files(tmp_path)
  wire, _secrets = _probe_wire_model(std, proj, system_file, agent_file)
  assert wire == MAPPED


def test_the_probe_env_equals_the_boxs_collapsed_env(
  std,
  config,
  project_dir,
  tmp_path: Path,
):
  """ONE TRUTH: the pre-flight's value is the whole-box resolve's ``meta.assembly.env``."""
  from kanibako.commands import start as start_cmd
  from tests.support.agent_selection import agent_selection
  from kanibako.settings.paths import resolve_project

  proj = resolve_project(std, config, str(project_dir), initialize=True)
  system_file, agent_file = _persona_files(tmp_path)
  cli_env = {"EXTRA_FROM_E": "1"}
  _wire, secrets = _probe_wire_model(
    std,
    proj,
    system_file,
    agent_file,
    cli_env=cli_env,
  )
  selection = agent_selection(node=NODE_ID, source="settings").selection_level
  snapshot, _deliveries = start_cmd._resolve_launch_snapshot(
    std=std,
    proj=proj,
    agent_name=NODE_ID,
    system_settings_path=system_file,
    agent_cfg_path=agent_file,
    desc=None,
    install=None,
    target=ClaudeTarget(),
    cli_level=selection,
    cli_env=cli_env,
  )
  box_env = start_cmd._build_config_env(start_cmd._launch_env_map(snapshot))
  assert box_env[VAR] == secrets.env[VAR] == MAPPED
  assert box_env["EXTRA_FROM_E"] == secrets.env["EXTRA_FROM_E"] == "1"
  # Every var the pre-flight carries reaches the box with the same value.
  assert {k: box_env[k] for k in secrets.env} == secrets.env


def _final_launch(
  start_mocks,
  std,
  proj,
  system_file,
  agent_file,
  *,
  node=NODE_ID,
  target=None,
  cli_env=None,
  model=None,
  reject=False,
  browser=False,
  explicit_agent=None,
  before_run=None,
):
  from kanibako.commands import start as start_cmd
  from kanibako.targets.base import PersonaProbeOutcome, ProbeEvidence
  from kanibako.settings.config_io import dump_doc, load_doc
  from tests.support.agent_selection import agent_selection

  doc = load_doc(system_file)
  doc.setdefault("agent", {}).setdefault("default", {})["allow_helpers"] = False
  dump_doc(system_file, doc)
  target = target or ClaudeTarget()
  seen = {"events": [], "probes": [], "resolves": []}
  with start_mocks() as m:
    m.resolve_agent.return_value = agent_selection(node=node, source="settings")
    m.target.name = target.name
    m.target.descriptor = target.descriptor
    m.target.setting_descriptors.return_value = target.setting_descriptors()
    m.target.default_envs.return_value = target.default_envs()
    m.effective_transform.return_value = None
    m.agent_cfg.transform_settings.clear()

    def inputs(kw):
      return dict(
        kw,
        std=std,
        proj=proj,
        system_settings_path=system_file,
        agent_cfg_path=agent_file,
        target=target,
      )

    def resolve(**kw):
      if not kw.get("include_base_families", True):
        return _REAL_FINAL_RESOLVE(**kw)
      seen["events"].append("resolve")
      assert kw["guarantee_create"] is False
      assert kw["required_var"] == (target.descriptor.persona.token_var or None)
      result = _REAL_FINAL_RESOLVE(**inputs(kw))
      seen["resolves"].append(result)
      return result

    def decisions(**kw):
      assert kw["resolve_secrets"] is False
      return _REAL_DECISIONS(**inputs(kw))

    def verify(endpoint, pointer, probe_model, *, env=None, token=None):
      seen["events"].append("probe")
      seen["probes"].append((endpoint, pointer, probe_model, dict(env), token))
      if reject:
        return PersonaProbeOutcome.rejected(ProbeEvidence(endpoint, model=probe_model, status=401))
      return PersonaProbeOutcome.passed()

    m.resolve_launch_snapshot.side_effect = resolve
    m.target.prepare_host_binary.side_effect = lambda *a, **kw: seen["events"].append("binary")
    m.target.refresh_host_auth.side_effect = lambda *a, **kw: seen["events"].append("auth")
    m.target.verify_persona = verify
    if before_run is not None:
      before_run(m, seen)
    with (
      mock.patch.object(start_cmd, "_resolve_box_launch_decisions", side_effect=decisions),
      mock.patch.object(
        start_cmd, "_persona_secret_table", side_effect=AssertionError("early arbitration")
      ),
      mock.patch.object(start_cmd.agent_file, "save") as save,
      mock.patch.object(start_cmd, "ensure_persona_share_symlinks") as share,
      mock.patch.multiple(
        start_cmd,
        _upgrade_shell=mock.DEFAULT,
        _write_create_entry=mock.DEFAULT,
        _seed_box_home=mock.DEFAULT,
        _register_new_box=mock.DEFAULT,
        persist_creation_env=mock.DEFAULT,
      ) as artifacts,
    ):
      seen["rc"] = start_cmd._run_container(
        project_dir=None,
        entrypoint=None,
        image_override=None,
        new_session=False,
        safe_mode=False,
        resume_mode=False,
        extra_args=[],
        cli_env=cli_env,
        model_override=model,
        browser=browser,
        explicit_agent=explicit_agent,
      )
      seen["save"] = save.call_count
      seen["share"] = share.call_count
      seen["artifacts"] = {name: value.call_count for name, value in artifacts.items()}
    seen["runtime"] = m.runtime.run.call_args
    seen["initializations"] = [
      call.kwargs.get("initialize") for call in m.resolve_any_project.call_args_list
    ]
    seen["auth"] = m.target.refresh_host_auth.call_count
    seen["prepare_combined"] = m.target.prepare_host.call_count
    seen["provider"] = (
      m.target.deliver_directive_hook.call_args.kwargs.get("model_provider")
      if m.target.deliver_directive_hook.called
      else None
    )
  return seen


def test_final_launch_probe_uses_real_resolve_model_and_env(
  start_mocks,
  std,
  config,
  project_dir,
  tmp_path,
):
  from kanibako.commands.start import _build_config_env, _launch_env_map
  from kanibako.settings.paths import resolve_project

  proj = resolve_project(std, config, str(project_dir), initialize=True)
  system_file, agent_file = _persona_files(tmp_path)
  seen = _final_launch(
    start_mocks,
    std,
    proj,
    system_file,
    agent_file,
    cli_env=[f"{VAR}={MAPPED}", "EXTRA_FROM_E=1"],
    model="launch-model",
  )
  assert seen["rc"] == 0
  assert len(seen["resolves"]) == len(seen["probes"]) == 1
  snapshot, deliveries = seen["resolves"][0]
  endpoint, pointer, model, env, token = seen["probes"][0]
  assert endpoint == "https://proxy.example"
  assert pointer == tmp_path / "token"
  assert model == "launch-model"
  assert env == _build_config_env(_launch_env_map(snapshot))
  assert env[VAR] == MAPPED and env["EXTRA_FROM_E"] == "1"
  assert seen["runtime"].kwargs["env"] == env
  assert token is None and deliveries.credential_states["ANTHROPIC_AUTH_TOKEN"].kind == "file"
  assert seen["events"] == ["binary", "resolve", "probe", "auth"]
  assert seen["prepare_combined"] == 0


@pytest.mark.parametrize("kind", ["cli", "blank", "null", "mask", "absent"])
def test_final_launch_eliminates_sources_before_credential_access(
  kind,
  start_mocks,
  std,
  config,
  project_dir,
  tmp_path,
  monkeypatch,
  capsys,
):
  from kanibako.commands import start as start_cmd
  from kanibako.settings.paths import resolve_project

  proj = resolve_project(std, config, str(project_dir), initialize=True)
  system_file, agent_file = _persona_files(tmp_path)
  token_var = "ANTHROPIC_AUTH_TOKEN"
  cli = (
    [f"{token_var}=literal"] if kind == "cli" else [f"{token_var}=  "] if kind == "blank" else None
  )
  doc = load_doc(system_file)
  if kind == "null":
    doc["agent"][NODE]["secret_path"][token_var] = None
  elif kind == "absent":
    del doc["agent"][NODE]["secret_path"]
  elif kind == "mask":
    doc["box"] = {"masks": {f"/run/kanibako/secrets/{token_var}": True}}
  dump_doc(system_file, doc)
  monkeypatch.setattr(
    start_cmd, "_secret_pointer_usable", mock.Mock(side_effect=AssertionError("eliminated source"))
  )
  real_stat = Path.stat
  real_read = Path.read_text
  eliminated = tmp_path / "token"

  def stat(path, *args, **kwargs):
    if path == eliminated:
      raise AssertionError("eliminated source stat")
    return real_stat(path, *args, **kwargs)

  def read(path, *args, **kwargs):
    if path == eliminated:
      raise AssertionError("eliminated source read")
    return real_read(path, *args, **kwargs)

  monkeypatch.setattr(Path, "stat", stat)
  monkeypatch.setattr(Path, "read_text", read)
  seen = _final_launch(start_mocks, std, proj, system_file, agent_file, cli_env=cli)
  assert len(seen["resolves"]) == 1
  assert seen["prepare_combined"] == 0
  assert start_cmd._secret_pointer_usable.call_count == 0
  if kind in ("mask", "absent"):
    assert seen["rc"] == 1
    assert seen["probes"] == [] and seen["runtime"] is None
    assert seen["auth"] == seen["save"] == seen["share"] == 0
  else:
    assert seen["rc"] == 0
    assert len(seen["probes"]) == (0 if kind == "blank" else 1)
    assert seen["auth"] == 1
    if kind == "cli":
      assert seen["probes"][0][4] == "literal"
    elif kind == "null":
      assert seen["probes"][0][1] is None and seen["probes"][0][4] is None
    else:
      assert "UNVERIFIED" in capsys.readouterr().err


def test_final_refusal_precedes_auth_and_persona_artifacts(
  start_mocks,
  std,
  config,
  project_dir,
  tmp_path,
):
  from kanibako.settings.paths import resolve_project

  proj = resolve_project(std, config, str(project_dir), initialize=True)
  system_file, agent_file = _persona_files(tmp_path)

  def first_use(m, seen):
    m.agent_config_path.exists.return_value = False
    m.target.generate_agent_config.return_value = m.agent_cfg
    m.proj.is_new = True
    m.runtime.rm.side_effect = AssertionError("cleanup before verdict")

  seen = _final_launch(
    start_mocks,
    std,
    proj,
    system_file,
    agent_file,
    reject=True,
    cli_env=["EXTRA_FROM_E=1"],
    explicit_agent="navigator+claude",
    before_run=first_use,
  )
  assert seen["rc"] == 1
  assert seen["initializations"] and True not in seen["initializations"]
  assert not any(seen["artifacts"].values())
  assert len(seen["resolves"]) == len(seen["probes"]) == 1
  assert seen["events"] == ["binary", "resolve", "probe"]
  assert seen["auth"] == seen["save"] == seen["share"] == 0
  assert seen["runtime"] is None


@pytest.mark.parametrize("stored_model", [None, ""])
def test_final_codex_probe_model_is_ephemeral_provider_model_is_stored(
  stored_model,
  start_mocks,
  std,
  config,
  project_dir,
  tmp_path,
):
  from kanibako.plugins.codex import CodexTarget
  from kanibako.settings.config_io import dump_doc
  from kanibako.settings.paths import resolve_project

  proj = resolve_project(std, config, str(project_dir), initialize=True)
  system_file, agent_file = tmp_path / "system.yaml", tmp_path / "agent.yaml"
  token = tmp_path / "token"
  token.write_text("test-material")
  node = "navigator℘codex"
  dump_doc(system_file, {"system": {"secret_path": {"UNRELATED": str(token)}}})
  dump_doc(
    agent_file,
    {
      "self": {
        "endpoint": "https://proxy.example",
        "model": stored_model,
        "secret_path": {"OWN_KEY": str(token)},
      }
    },
  )
  seen = _final_launch(
    start_mocks,
    std,
    proj,
    system_file,
    agent_file,
    node=node,
    target=CodexTarget(),
    model="ephemeral-model",
  )
  assert seen["rc"] == 0
  assert len(seen["probes"]) == len(seen["resolves"]) == 1
  assert seen["probes"][0][2] == "ephemeral-model"
  assert seen["provider"].model == stored_model
  assert seen["provider"].env_key == "OWN_KEY"
  assert seen["provider"].base_url == "https://proxy.example"


@pytest.mark.parametrize("names", [(), ("ONE", "TWO")])
def test_final_codex_requires_one_own_declaration(
  names,
  start_mocks,
  std,
  config,
  project_dir,
  tmp_path,
):
  from kanibako.plugins.codex import CodexTarget
  from kanibako.settings.config_io import dump_doc
  from kanibako.settings.paths import resolve_project

  proj = resolve_project(std, config, str(project_dir), initialize=True)
  system_file, agent_file = tmp_path / "system.yaml", tmp_path / "agent.yaml"
  token = tmp_path / "token"
  token.write_text("test-material")
  dump_doc(system_file, {"system": {"secret_path": {"UNRELATED": str(token)}}})
  dump_doc(
    agent_file,
    {
      "self": {
        "endpoint": "https://proxy.example",
        "secret_path": {name: str(token) for name in names},
      }
    },
  )
  seen = _final_launch(
    start_mocks, std, proj, system_file, agent_file, node="navigator℘codex", target=CodexTarget()
  )
  assert seen["rc"] == 1
  assert seen["probes"] == [] and seen["runtime"] is None
  assert seen["auth"] == seen["save"] == seen["share"] == 0


@pytest.mark.parametrize("conflict", ["browser", "secret-twin"])
def test_final_configuration_refusal_precedes_blank_credential_warning(
  conflict,
  start_mocks,
  std,
  config,
  project_dir,
  tmp_path,
  capsys,
):
  from kanibako.errors import ConfigError
  from kanibako.settings.config_io import dump_doc, load_doc
  from kanibako.settings.paths import resolve_project
  from kanibako.settings.settings_resolve import SettingsError

  proj = resolve_project(std, config, str(project_dir), initialize=True)
  system_file, agent_file = _persona_files(tmp_path)
  doc = load_doc(system_file)
  var = "BROWSER_WS_ENDPOINT" if conflict == "browser" else "ANTHROPIC_AUTH_TOKEN"
  doc["box"] = {"env": {var: "declared"}}
  dump_doc(system_file, doc)
  captured = {}

  def capture(m, seen):
    captured.update(m=m, seen=seen)

  with pytest.raises((ConfigError, SettingsError)):
    _final_launch(
      start_mocks,
      std,
      proj,
      system_file,
      agent_file,
      cli_env=["ANTHROPIC_AUTH_TOKEN= "],
      browser=conflict == "browser",
      before_run=capture,
    )
  assert captured["seen"]["events"] == ["binary", "resolve"]
  captured["m"].target.refresh_host_auth.assert_not_called()
  captured["m"].runtime.run.assert_not_called()
  assert "UNVERIFIED" not in capsys.readouterr().err

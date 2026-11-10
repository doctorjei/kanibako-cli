"""Box-info consumes selected identity without reopening the persona store."""

import argparse
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from kanibako import cli, persona_store
from kanibako.agent_ref import GENERAL_SLOT, AgentIdentity
from kanibako.commands import start
from kanibako.commands.box import _parser
from kanibako.errors import ConfigError
from kanibako.settings.agent_config import agent_settings_path
from kanibako.settings import agent_select, config_io
from kanibako.settings.agent_select import AgentSelection
from kanibako.settings.config import load_config
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.paths import box_workset_settings_paths, load_std_paths, resolve_project
from kanibako.settings.settings_launch import (
  ResolveSubject,
  box_scalar_config,
  build_launch_snapshot,
  effective_behavior,
  resolve_inputs,
)
from kanibako.settings.settings_prefs import default_valid_agents
from tests.support.agent_selection import agent_selection


@pytest.fixture
def info_box(config_file, tmp_home):
  config = load_config(config_file)
  std = load_std_paths(config)
  project_path = str(tmp_home / "project")
  resolve_project(std, config, project_path, initialize=True)
  proj = resolve_project(std, config, project_path, initialize=False)
  with (
    patch.object(_parser, "load_std_paths", return_value=std),
    patch.object(_parser, "resolve_box_target", return_value=proj),
    patch.object(_parser, "_check_container_running", return_value=(False, "not running")),
    patch(
      "kanibako.persona_store.locate_entry",
      side_effect=AssertionError("box info must not look up PG locally"),
    ) as locate,
    patch(
      "kanibako.persona_store.read_registered_persona",
      side_effect=AssertionError("box info must not read PG"),
    ) as registered,
    patch(
      "kanibako.commands.start._persona_bundle_for",
      side_effect=AssertionError("box info must not read the legacy persona store"),
    ) as legacy,
  ):
    yield std, proj
    locate.assert_not_called()
    registered.assert_not_called()
    legacy.assert_not_called()


@pytest.mark.parametrize("node,harness", [("claude", "goose"), ("scout", "claude")])
@pytest.mark.parametrize("floor", ["name", "path", "settings"])
def test_registered_identity_drives_target_and_printed_image(
  info_box, tmp_path, capsys, node, harness, floor
):
  std, proj = info_box
  selection = AgentSelection(
    identity=AgentIdentity(
      node=node,
      registered_name="TrailGuide",
      persona="Guide",
      harness=harness.title(),
      target_name=harness,
      entry_path=tmp_path / "PG" / "TrailGuide",
    ),
    source="settings",
  )
  agent_path = agent_settings_path(std.agents, node)
  dump_doc(agent_path, {"box": {"image": f"{{meta.agent.{node}.{floor}}}"}})
  expected = {
    "name": "TrailGuide",
    "path": str(std.agents / node),
    "settings": str(agent_path),
  }[floor]
  with (
    patch("kanibako.settings.agent_select.select_agent", return_value=selection) as select,
    patch.object(_parser, "resolve_target", wraps=_parser.resolve_target) as target,
    patch.object(start, "_box_scalars", wraps=start._box_scalars) as scalars,
  ):
    assert _parser.run_info(argparse.Namespace(path=str(proj.project_path))) == 0
  select.assert_called_once_with(std=std, proj=proj)
  target.assert_called_once_with(harness)
  assert scalars.call_args.args[2] == node
  assert scalars.call_args.kwargs["agent_identity"] is selection.identity
  rows = dict(line.strip().split(":", 1) for line in capsys.readouterr().out.splitlines())
  assert rows["Image"].strip() == expected
  assert rows["Agent"].strip() == f"{node}  (from: settings)"
  assert rows["Credentials"].strip() == "n/a (no credentials file)"


@pytest.mark.parametrize("node", ["claude", "Guide℘claude", "shell", ""])
def test_native_legacy_and_shell_selection(info_box, capsys, node):
  std, proj = info_box
  selection = agent_selection(node=node, source="settings")
  with (
    patch("kanibako.settings.agent_select.select_agent", return_value=selection) as select,
    patch.object(_parser, "resolve_target", wraps=_parser.resolve_target) as target,
    patch.object(start, "_box_scalars", wraps=start._box_scalars) as scalars,
  ):
    assert _parser.run_info(argparse.Namespace(path=str(proj.project_path))) == 0
  select.assert_called_once_with(std=std, proj=proj)
  if node:
    target.assert_called_once_with(selection.identity.target_name)
  else:
    target.assert_not_called()
  assert scalars.call_args.args[2] == (node or GENERAL_SLOT)
  assert scalars.call_args.kwargs["agent_identity"] is (selection.identity if node else None)
  display = node.replace("℘", "+") if node else "none — plain shell"
  assert f"{display}  (from: settings)" in capsys.readouterr().out


@pytest.mark.parametrize(
  "failure,expected",
  [
    (ConfigError("selection unavailable"), "n/a (unresolved)"),
    (
      ConfigError("RETIRED box.agent_name; use pref.system.agent"),
      "n/a (RETIRED box.agent_name; use pref.system.agent)",
    ),
    (None, "scout  (from: settings)"),
  ],
  ids=["selection-error", "retired-error", "missing-plugin"],
)
def test_informational_failures_keep_general_fallback(info_box, capsys, failure, expected):
  std, proj = info_box
  selection = AgentSelection(
    identity=AgentIdentity("scout", "Scout", "Guide", "Missing", "missing", None),
    source="settings",
  )
  with (
    patch(
      "kanibako.settings.agent_select.select_agent", return_value=selection, side_effect=failure
    ) as select,
    patch.object(_parser, "resolve_target", side_effect=KeyError("plugin unavailable")) as target,
    patch.object(start, "_box_scalars", wraps=start._box_scalars) as scalars,
  ):
    assert _parser.run_info(argparse.Namespace(path=str(proj.project_path))) == 0
  select.assert_called_once_with(std=std, proj=proj)
  if failure is None:
    target.assert_called_once_with("missing")
  else:
    target.assert_not_called()
  assert scalars.call_args.args[2] == GENERAL_SLOT
  assert scalars.call_args.kwargs["agent_identity"] is None
  output = capsys.readouterr().out
  assert expected in output
  assert "n/a (no target)" in output
  assert "Image:" in output


@pytest.fixture
def live_info_box(std, config_file, tmp_home, monkeypatch):
  project = tmp_home / "live-info"
  project.mkdir()
  proj = resolve_project(std, load_config(config_file), str(project), initialize=True)
  path, _ = box_workset_settings_paths(proj)
  doc = load_doc(path)
  doc["pref"] = {"system": {"agent": "TrailGuide"}}
  doc["box"] = {"image": "{agent.trailguide.model}"}
  dump_doc(path, doc)
  config = {
    "model": "live-image",
    "env": {"ANTHROPIC_BASE_URL": "https://live.invalid/v1", "LABEL": "live-env"},
  }
  root = tmp_home / "PG-root"
  entry = tmp_home / "unrelated-entry" / "TrailGuide"
  data = {
    "agent": {
      "persona": {"name": "Navigator"},
      "harness": {"name": "claude", "config_store": config, "auth_var": "TOKEN"},
      "secret": "synthetic-manager",
    }
  }
  location = Mock(side_effect=lambda: Path(os.environ["AGENT_STORE_DIR"]))
  lists = Mock(return_value={"TrailGuide": entry})
  loads = Mock(side_effect=lambda *_a, **_k: (entry, data))
  fetch = Mock(side_effect=AssertionError("info fetched a credential"))
  projection = Mock(wraps=persona_store.project_registered_persona)
  select = Mock(wraps=agent_select.select_agent)
  scalars = Mock(wraps=start._box_scalars)
  resolve_target = _parser.resolve_target

  def choose_target(name):
    monkeypatch.setenv("AGENT_STORE_DIR", str(tmp_home / "changed-root"))
    return resolve_target(name)

  target = Mock(side_effect=choose_target)
  monkeypatch.setattr(config_io, "persona_store_location", location)
  monkeypatch.setattr(config_io, "persona_store_agents", lists)
  monkeypatch.setattr(config_io, "load_persona_agent", loads)
  monkeypatch.setattr(config_io, "fetch_persona_secret", fetch)
  monkeypatch.setattr(persona_store, "project_registered_persona", projection)
  monkeypatch.setattr(
    persona_store, "locate_entry", Mock(side_effect=AssertionError("info rediscovery"))
  )
  monkeypatch.setattr(
    start, "_persona_bundle_for", Mock(side_effect=AssertionError("info legacy read"))
  )
  monkeypatch.setattr(agent_select, "select_agent", select)
  monkeypatch.setattr(start, "_box_scalars", scalars)
  monkeypatch.setattr(_parser, "resolve_target", target)
  monkeypatch.setattr(
    _parser, "_check_container_running", Mock(return_value=(False, "not running"))
  )
  return SimpleNamespace(
    std=std,
    proj=proj,
    path=path,
    project=project,
    home=tmp_home,
    root=root,
    entry=entry,
    config=config,
    data=data,
    location=location,
    lists=lists,
    loads=loads,
    fetch=fetch,
    projection=projection,
    select=select,
    scalars=scalars,
    target=target,
    monkeypatch=monkeypatch,
  )


def _info_cli(box, capsys, *, status=0):
  box.monkeypatch.setenv("AGENT_STORE_DIR", str(box.root))
  before = {
    str(p): (
      p.lstat().st_mode,
      str(p.readlink()) if p.is_symlink() else p.read_bytes() if p.is_file() else None,
    )
    for p in box.home.rglob("*")
  }
  with pytest.raises(SystemExit) as result:
    cli.main(["box", "info", str(box.project)])
  captured = capsys.readouterr()
  assert result.value.code == status, captured
  assert {
    str(p): (
      p.lstat().st_mode,
      str(p.readlink()) if p.is_symlink() else p.read_bytes() if p.is_file() else None,
    )
    for p in box.home.rglob("*")
  } == before
  box.fetch.assert_not_called()
  return captured


def test_public_info_captured_image_and_independent_snapshot(live_info_box, capsys):
  box = live_info_box
  captured = _info_cli(box, capsys)
  assert not captured.err
  rows = dict(line.strip().split(":", 1) for line in captured.out.splitlines())
  assert rows["Image"].strip() == "live-image", captured.out
  assert rows["Agent"].strip() == "trailguide  (from: settings)"
  box.location.assert_called_once_with()
  box.lists.assert_called_once_with(root=box.root)
  box.loads.assert_called_once_with("TrailGuide", root=box.root)
  selected_std, selected_proj = box.scalars.call_args.args[:2]
  box.select.assert_called_once_with(std=selected_std, proj=selected_proj)
  assert selected_proj.project_path == box.project and not selected_proj.is_new
  assert selected_std.agents == box.std.agents and selected_std.settings == box.std.settings
  box.target.assert_called_once_with("claude")
  box.projection.assert_called_once()
  registered, target = box.projection.call_args.args
  assert registered.store_root == box.root and registered.identity.entry_path == box.entry
  assert Path(os.environ["AGENT_STORE_DIR"]) != box.root
  assert target.name == "claude"
  box.scalars.assert_called_once()
  assert box.scalars.call_args.kwargs["agent_identity"] is registered.identity
  values = {"model": "live-image", "endpoint": "https://live.invalid/v1", "env.LABEL": "live-env"}
  assert box.scalars.call_args.kwargs["persona_values"] == values
  inputs = resolve_inputs(
    subject=ResolveSubject.BOX,
    std=selected_std,
    proj=selected_proj,
    agent_name="trailguide",
    agent_identity=registered.identity,
    system_path=selected_std.settings,
  )
  snapshot = build_launch_snapshot(
    **inputs.as_kwargs(),
    agent_name="trailguide",
    agent_path=agent_settings_path(box.std.agents, "trailguide"),
    persona_values=values,
    valid_agents=default_valid_agents().with_harnesses({"trailguide": "claude"}),
  )
  assert (
    effective_behavior(snapshot, active_agent="trailguide", keys=["model"])["model"]
    == "live-image"
  )
  assert rows["Image"].strip() == box_scalar_config(snapshot).box_image
  assert box.loads.call_count == box.projection.call_count == 1
  with capsys.disabled():
    print(
      "INFO_RECEIPT actual-cli real-claude image=live-image same-file-snapshot=live-image reads=1 projections=1 captured-root no-fetch no-writes"
    )


def test_info_operations_are_fresh_and_agent_file_wins(live_info_box, capsys):
  box = live_info_box
  assert "live-image" in _info_cli(box, capsys).out
  box.config["model"] = "fresh-image"
  assert "fresh-image" in _info_cli(box, capsys).out
  path = agent_settings_path(box.std.agents, "trailguide")
  dump_doc(path, {"self": {"model": "file-image"}})
  out = _info_cli(box, capsys).out
  assert "file-image" in out and "fresh-image" not in out
  assert box.loads.call_count == box.projection.call_count == box.select.call_count == 3
  assert [call.kwargs for call in box.loads.call_args_list] == [{"root": box.root}] * 3


@pytest.mark.parametrize(
  "model", ["missing", None, "", r"{{agent.default.model}} {$HOME} ~ \\ literal"]
)
def test_info_model_states_and_verbatim_values(live_info_box, capsys, model):
  box = live_info_box
  doc = load_doc(box.path)
  doc["box"] = {
    "image": "fixed-image" if model in ("missing", None, "") else "{agent.trailguide.model}"
  }
  dump_doc(box.path, doc)
  dump_doc(box.std.settings, {"agent": {"default": {"model": "fallback-model"}}})
  if model == "missing":
    del box.config["model"]
  else:
    box.config["model"] = model
  box.config["env"]["LABEL"] = r"{{data}} {$HOME} ~ \\"
  out = _info_cli(box, capsys).out
  values = box.scalars.call_args.kwargs["persona_values"]
  if model == "missing":
    assert "model" not in values
  else:
    assert values["model"] == model
  assert values["env.LABEL"] == box.config["env"]["LABEL"]
  registered = box.projection.call_args.args[0]
  inputs = resolve_inputs(
    subject=ResolveSubject.BOX,
    std=box.std,
    proj=box.proj,
    agent_name="trailguide",
    agent_identity=registered.identity,
    system_path=box.std.settings,
  )
  expected_values = {
    "endpoint": "https://live.invalid/v1",
    "env.LABEL": box.config["env"]["LABEL"],
  }
  if model != "missing":
    expected_values["model"] = model
  snapshot = build_launch_snapshot(
    **inputs.as_kwargs(),
    agent_name="trailguide",
    agent_path=agent_settings_path(box.std.agents, "trailguide"),
    persona_values=expected_values,
    valid_agents=default_valid_agents().with_harnesses({"trailguide": "claude"}),
  )
  behavior = effective_behavior(snapshot, active_agent="trailguide", keys=["model"])
  if model is None:
    assert "model" not in behavior
  else:
    assert behavior["model"] == ("fallback-model" if model == "missing" else model)
  rows = dict(line.strip().split(":", 1) for line in out.splitlines())
  assert rows["Image"].strip() == box_scalar_config(snapshot).box_image
  assert box.loads.call_count == box.projection.call_count == 1


@pytest.mark.parametrize("failure", ["missing-env", "invalid-endpoint", "unsupported-goose"])
def test_info_projection_refusal_never_prints_success(live_info_box, capsys, failure):
  box = live_info_box
  doc = load_doc(box.path)
  doc["box"] = {"image": "fixed-image"}
  dump_doc(box.path, doc)
  if failure == "missing-env":
    del box.config["env"]
    message = "has no 'env' object"
  elif failure == "invalid-endpoint":
    box.config["env"]["ANTHROPIC_BASE_URL"] = "not-a-url"
    message = "not-a-url"
  else:
    box.data["agent"]["harness"]["name"] = "goose"
    message = "goose harness has no native persona projection"
  captured = _info_cli(box, capsys, status=1)
  assert message in captured.err
  assert not captured.out
  box.scalars.assert_not_called()
  box.loads.assert_called_once()
  box.projection.assert_called_once()


@pytest.mark.parametrize(
  "failure", [ConfigError("target unavailable"), KeyError("missing plugin")]
)
def test_info_captured_input_target_failure_still_degrades(live_info_box, capsys, failure):
  box = live_info_box
  doc = load_doc(box.path)
  doc["box"] = {"image": "fallback-image"}
  dump_doc(box.path, doc)
  box.target.side_effect = failure
  captured = _info_cli(box, capsys)
  assert "fallback-image" in captured.out and "n/a (no target)" in captured.out
  assert not captured.err
  box.loads.assert_called_once()
  box.projection.assert_not_called()
  assert box.scalars.call_args.args[2] == GENERAL_SLOT
  assert box.scalars.call_args.kwargs["agent_identity"] is None
  assert box.scalars.call_args.kwargs.get("persona_values") is None

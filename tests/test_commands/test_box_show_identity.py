"""Stored display consumes one selection; Goose projection here is synthetic."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanibako import cli, persona_store
from kanibako.commands import start
from kanibako.settings import config_io
from kanibako.settings.agent_config import agent_settings_path
from kanibako.settings.config import load_config
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.paths import box_workset_settings_paths, resolve_project
from kanibako.targets.base import PERSONA_DEFAULT_MODEL, PersonaReadOutcome, PersonaSettings


@pytest.fixture
def display_box(std, config_file, tmp_home, monkeypatch, request):
  harness = getattr(request, "param", "goose")
  project = tmp_home / "display-box"
  project.mkdir()
  proj = resolve_project(std, load_config(config_file), str(project), initialize=True)
  path, _ = box_workset_settings_paths(proj)
  doc = load_doc(path)
  doc["pref"] = {"system": {"agent": "TrailGuide"}}
  doc["box"] = {"image": "{agent.trailguide.model}"}
  dump_doc(path, doc)
  config = {
    "model": "live-model",
    "env": {"ANTHROPIC_BASE_URL": "https://user:password@live.invalid/v1", "LABEL": "live-env"},
  }
  root = tmp_home / "PG-root"
  entry = tmp_home / "separate-entry" / "TrailGuide"
  data = {
    "agent": {
      "persona": {"name": "Navigator"},
      "harness": {
        "name": harness,
        "config_store": config,
        "auth_var": "TOKEN",
      },
      "secret": "synthetic-manager",
    }
  }
  lists = Mock(return_value={"TrailGuide": entry})
  loads = Mock(side_effect=lambda *_a, **_k: (entry, data))
  fetch = Mock(side_effect=AssertionError("show fetched a credential"))
  monkeypatch.setattr(config_io, "persona_store_location", lambda: root)
  monkeypatch.setattr(config_io, "persona_store_agents", lists)
  monkeypatch.setattr(config_io, "load_persona_agent", loads)
  monkeypatch.setattr(config_io, "fetch_persona_secret", fetch)
  if harness == "goose":
    from kanibako.plugins.goose.target import GooseTarget

    # Boundary stand-in, identical in the frozen control; not shipped Goose support.
    def synthetic_projection(self, config, *, source, keyless=False):
      model = config.get("model")
      if "model" in config and model is None:
        model = PERSONA_DEFAULT_MODEL
      return PersonaReadOutcome(
        PersonaSettings(
          config["env"]["ANTHROPIC_BASE_URL"], model, "TOKEN", {"LABEL": config["env"]["LABEL"]}
        ),
        None,
      )

    monkeypatch.setattr(GooseTarget, "project_persona_settings", synthetic_projection)
  projection = Mock(wraps=persona_store.project_registered_persona)
  monkeypatch.setattr(persona_store, "project_registered_persona", projection)
  selected = Mock(wraps=start.resolve_stored_box_snapshot)
  monkeypatch.setattr(start, "resolve_stored_box_snapshot", selected)
  target = Mock(wraps=start.resolve_target)
  monkeypatch.setattr(start, "resolve_target", target)
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
    lists=lists,
    loads=loads,
    fetch=fetch,
    projection=projection,
    selected=selected,
    target=target,
    harness=harness,
  )


def _show(box, capsys):
  before = {
    str(p): (
      p.lstat().st_mode,
      str(p.readlink()) if p.is_symlink() else p.read_bytes() if p.is_file() else None,
    )
    for p in box.home.rglob("*")
  }
  with pytest.raises(SystemExit) as result:
    cli.main(["box", "show", str(box.project), "--effective"])
  assert result.value.code == 0
  out = capsys.readouterr().out
  assert {
    str(p): (
      p.lstat().st_mode,
      str(p.readlink()) if p.is_symlink() else p.read_bytes() if p.is_file() else None,
    )
    for p in box.home.rglob("*")
  } == before
  box.fetch.assert_not_called()
  return out


@pytest.mark.parametrize("display_box", ["goose", "claude"], indirect=True)
def test_public_show_captured_registered_input(display_box, capsys):
  box = display_box
  out = _show(box, capsys)
  # These independent printed values are also the pre-D behavioral negative control.
  assert "model = live-model" in out, out
  assert "endpoint = https://<redacted>@live.invalid/v1" in out, out
  assert "  env LABEL = live-env" in out, out
  assert "box_image = live-model" in out, out
  assert "password" not in out
  assert "env GOOSE_MODE =" not in out and "env ANTHROPIC_BASE_URL =" not in out
  box.lists.assert_called_once_with(root=box.root)
  box.loads.assert_called_once_with("TrailGuide", root=box.root)
  box.projection.assert_called_once()
  box.target.assert_called_once_with(box.harness)
  registered, target = box.projection.call_args.args
  assert registered.identity.node == "trailguide"
  assert registered.identity.target_name == target.name == box.harness
  assert registered.identity.entry_path == box.entry
  assert registered.store_root == box.root
  stored = box.selected.call_args.args[2]
  assert stored.selection.identity is registered.identity
  assert stored.node == "trailguide" and stored.target is target
  assert stored.agent_cfg_path == agent_settings_path(box.std.agents, "trailguide")
  assert stored.persona_values["model"] == "live-model"
  assert stored.persona_credential.store_root == box.root
  assert not agent_settings_path(box.std.agents, "trailguide").exists()
  with capsys.disabled():
    print(
      f"PUBLIC_RECEIPT harness={box.harness} model=live-model endpoint=scrubbed env=live-env scalar=live-model reads=1 projections=1"
    )


@pytest.mark.parametrize("display_box", ["goose", "claude"], indirect=True)
def test_fresh_operation_and_agent_file_precedence(display_box, capsys):
  box = display_box
  assert "model = live-model" in _show(box, capsys)
  box.config["model"] = "fresh-model"
  box.config["env"]["LABEL"] = "fresh-env"
  out = _show(box, capsys)
  assert "model = fresh-model" in out and "box_image = fresh-model" in out
  assert "env LABEL = fresh-env" in out
  path = agent_settings_path(box.std.agents, "trailguide")
  path.parent.mkdir(parents=True, exist_ok=True)
  state = {"model": "file-model", "endpoint": "https://file.invalid", "env": {"LABEL": "file-env"}}
  if box.harness == "goose":
    state["provider"] = "openai"
  dump_doc(path, {"self": state})
  out = _show(box, capsys)
  assert "model = file-model" in out and "box_image = file-model" in out
  assert "endpoint = https://file.invalid" in out and "env LABEL = file-env" in out
  assert box.loads.call_count == box.projection.call_count == 3
  with capsys.disabled():
    print(f"FRESH_FILE_RECEIPT harness={box.harness} operations=3 reads=3 projections=3")


@pytest.mark.parametrize("display_box", ["claude"], indirect=True)
def test_registered_agent_file_rejects_foreign_plugin_leaf(display_box, capsys):
  box = display_box
  path = agent_settings_path(box.std.agents, "trailguide")
  path.parent.mkdir(parents=True, exist_ok=True)
  dump_doc(path, {"self": {"provider": "openai"}})
  with pytest.raises(SystemExit) as result:
    cli.main(["box", "show", str(box.project), "--effective"])
  assert result.value.code == 1
  err = capsys.readouterr().err
  assert "provider" in err and str(path) in err
  box.fetch.assert_not_called()
  with capsys.disabled():
    print("LOCAL_VOCABULARY_RECEIPT target=claude foreign-provider exit=1")


@pytest.mark.parametrize("display_box", ["claude"], indirect=True)
@pytest.mark.parametrize("model", ["missing", None, ""])
def test_missing_null_empty_models(display_box, model, capsys):
  box = display_box
  doc = load_doc(box.path)
  doc["box"] = {"image": "fixed-image"}
  dump_doc(box.path, doc)
  dump_doc(box.std.settings, {"agent": {"default": {"model": "fallback-model"}}})
  if model == "missing":
    del box.config["model"]
  else:
    box.config["model"] = model
  out = _show(box, capsys)
  box.loads.assert_called_once()
  box.projection.assert_called_once()
  bundle = box.projection.call_args.args[0]
  assert ("model" in bundle.config) == (model != "missing")
  assert "live-model" not in out
  values = box.selected.call_args.args[2].persona_values
  if model == "missing":
    assert "model" not in values
    assert "model = fallback-model" in out, out
  else:
    assert values["model"] == model
    assert "model = fallback-model" not in out
    if model is None:
      assert not any(line.strip().startswith("model =") for line in out.splitlines())
    else:
      assert any(line.strip() == "model =" for line in out.splitlines())


def test_category_collision_keeps_focused_behavior(display_box, capsys):
  box = display_box
  doc = load_doc(box.path)
  doc["box"] = {"bindings": {"ro": {"/opt/twin": ["/srv/box"]}}}
  dump_doc(box.path, doc)
  dump_doc(box.std.settings, {"system": {"bindings": {"ro": {"/opt/twin": ["/srv/system"]}}}})
  out = _show(box, capsys)
  assert "model = live-model" in out, out
  assert "/opt/twin" in out and "collides" in out, out
  box.loads.assert_called_once()
  box.projection.assert_called_once()


def test_native_goose_projection_remains_refused(display_box, monkeypatch, capsys):
  from kanibako.plugins.goose.target import GooseTarget
  from kanibako.targets.base import Target

  box = display_box
  monkeypatch.setattr(GooseTarget, "project_persona_settings", Target.project_persona_settings)
  with pytest.raises(SystemExit) as result:
    cli.main(["box", "show", str(box.project), "--effective"])
  assert result.value.code == 1
  captured = capsys.readouterr()
  assert "goose harness has no native persona projection" in captured.err
  assert "box_image" not in captured.out
  box.fetch.assert_not_called()
  with capsys.disabled():
    print("UNSUPPORTED_GOOSE_RECEIPT exit=1 no-native-projection no-shell-success")


@pytest.mark.parametrize("selection", ["claude", "shell", None, "missing_harness"])
def test_native_pseudo_unset_and_selection_error(
  std, config_file, tmp_home, monkeypatch, capsys, selection
):
  project = tmp_home / "compat-box"
  project.mkdir()
  proj = resolve_project(std, load_config(config_file), str(project), initialize=True)
  path, _ = box_workset_settings_paths(proj)
  dump_doc(std.settings, {"system": {"agent": selection}})
  dump_doc(path, {"box": {"image": "compat-image"}})
  if selection == "missing_harness":
    monkeypatch.setattr(config_io, "persona_store_agents", lambda **_k: {})
  else:
    monkeypatch.setattr(
      config_io, "persona_store_agents", Mock(side_effect=AssertionError("native PG read"))
    )
  with pytest.raises(SystemExit) as result:
    cli.main(["box", "show", str(project), "--effective"])
  assert result.value.code == 0
  out = capsys.readouterr().out
  assert "box_image = compat-image" in out
  carrier = start.stored_box_agent(std, proj)
  if selection in (None, "missing_harness"):
    assert carrier.target is None
  if selection == "missing_harness":
    assert carrier.selection is None and carrier.node == "shell"


def test_legacy_selection_projects_once(std, config_file, tmp_home, monkeypatch, capsys):
  project = tmp_home / "legacy-box"
  project.mkdir()
  proj = resolve_project(std, load_config(config_file), str(project), initialize=True)
  path, _ = box_workset_settings_paths(proj)
  dump_doc(
    path,
    {
      "pref": {"system": {"agent": "Navigator+claude"}},
      "box": {"image": "{agent.Navigator℘claude.model}"},
    },
  )
  reader = Mock(
    return_value=persona_store.PersonaBundle(
      endpoint="https://legacy.invalid", model="legacy-model", env={"LABEL": "legacy-env"}
    )
  )
  monkeypatch.setattr(start, "_persona_bundle_for", reader)
  box = SimpleNamespace(home=tmp_home, project=project, fetch=Mock())
  out = _show(box, capsys)
  assert "model = legacy-model" in out and "box_image = legacy-model" in out
  assert "endpoint = https://legacy.invalid" in out and "env LABEL = legacy-env" in out
  reader.assert_called_once()
  assert reader.call_args.args[0] == "Navigator℘claude"


def test_target_failure_drops_identity(display_box, monkeypatch, capsys):
  from kanibako.errors import ConfigError

  box = display_box
  monkeypatch.setattr(start, "resolve_target", Mock(side_effect=ConfigError("unavailable target")))
  carrier = start.stored_box_agent(box.std, box.proj)
  assert carrier.selection is None and carrier.node == "shell" and carrier.target is None
  assert carrier.persona_values is carrier.persona_credential is None
  box.projection.assert_not_called()
  box.fetch.assert_not_called()
  doc = load_doc(box.path)
  doc["box"] = {"image": "fallback-image"}
  dump_doc(box.path, doc)
  out = _show(box, capsys)
  assert "box_image = fallback-image" in out
  assert "model = live-model" not in out
  box.projection.assert_not_called()

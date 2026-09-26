"""The auth SHARING chain answers in the MAIN launch resolve, in every mode (spec §2c).

Keyspec §2c declares ``workset.auth.path`` (``@meta.workset.path/auth`` for primary and
named, ``<None>`` for standalone) and ``meta.box.auth.workset_path``
(``@workset.auth.path/@system.agent``, ``<None>`` for standalone). Only the two auth
resolves used to fold ``auth_chain_floor``; the main launch resolve folded none, so a
user ``@``-ref to either key resolved against nothing there.

⚑ THE ORACLE FOR THE VALUE is the CREDENTIAL route, not a restated path: the main
snapshot must name the same per-agent directory ``resolve_auth_source`` hands credsync.
Whether the floor matches the spec is kinemata's (``auth-chain`` / ``auth-chain-meta``).
"""

from __future__ import annotations

import logging

import pytest

from kanibako.commands import start as start_cmd
from kanibako.project.workset import add_project, create_workset
from kanibako.settings.agent_select import AgentSelection
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.paths import (
  WorksetSpec,
  box_workset_settings_paths,
  resolve_project,
  resolve_standalone_project,
  resolve_workset_project,
)
from kanibako.settings.settings_launch import auth_chain_floor, snapshot_leaf

MODES = ("primary", "named", "standalone")
NODE = "claude"
WS_AUTH_PATH = "workset.auth.path"
BOX_AUTH_PATH = "meta.box.auth.workset_path"


@pytest.fixture
def primary_proj(std, config, project_dir):
  return resolve_project(std, config, str(project_dir), initialize=True)


@pytest.fixture
def named_proj(std, config, tmp_home):
  workset = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
  source = tmp_home / "original-project"
  source.mkdir()
  add_project(workset, "cool-app", source)
  return resolve_workset_project(
    WorksetSpec.from_workset(workset), "cool-app", std, config, initialize=True,
  )


@pytest.fixture
def standalone_proj(std, config, project_dir, credentials_dir):
  return resolve_standalone_project(std, config, str(project_dir), initialize=True)


def _selection():
  """The §1A selection level a launch installs, off the production dataclass."""
  return AgentSelection(node=NODE, source="settings").selection_level


def _main_snapshot(std, proj):
  """The MAIN launch resolve, as ``_run_container`` drives it (selection included)."""
  snapshot, _deliveries = start_cmd._resolve_launch_snapshot(
    std=std, proj=proj, agent_name=NODE,
    system_settings_path=None, agent_cfg_path=None,
    desc=None, install=None, target=None, agent_cfg=None,
    cli_level=_selection(),
  )
  return snapshot


def _credential_source(std, proj):
  """The per-agent workset dir the CREDENTIAL route resolves — the outside oracle."""
  return start_cmd._resolve_box_auth_source(
    std=std, proj=proj, agent_name=NODE,
    system_settings_path=None, agent_cfg_path=None, selection_level=_selection(),
  ).workset_source


class TestTheMainResolveFoldsTheAuthChain:

  @pytest.mark.parametrize("mode", MODES)
  def test_every_chain_key_answers(self, mode, request, std):
    """Every key the chain floor supplies for *mode* is PRESENT in the main snapshot."""
    proj = request.getfixturevalue(f"{mode}_proj")
    chain = set(auth_chain_floor(mode=mode, agent_name=NODE))
    # Reds on its own emptiness (P15): the two §2c rows must be in the corpus.
    assert {WS_AUTH_PATH, BOX_AUTH_PATH} <= chain
    snapshot = _main_snapshot(std, proj)
    absent = sorted(k for k in chain if snapshot_leaf(snapshot, k) is __MISSING__)
    assert not absent, f"{mode}: absent from the MAIN launch snapshot: {absent}"

  @pytest.mark.parametrize("mode", ("primary", "named"))
  def test_workset_modes_resolve_the_spec_formulas(self, mode, request, std):
    """``@meta.workset.path/auth`` and ``@workset.auth.path/@system.agent`` (§2c)."""
    proj = request.getfixturevalue(f"{mode}_proj")
    snapshot = _main_snapshot(std, proj)
    ws_auth = snapshot_leaf(snapshot, WS_AUTH_PATH)
    assert ws_auth == f"{snapshot_leaf(snapshot, 'meta.workset.path')}/auth"
    assert snapshot_leaf(snapshot, BOX_AUTH_PATH) == f"{ws_auth}/{NODE}"
    assert snapshot_leaf(snapshot, BOX_AUTH_PATH) == _credential_source(std, proj)

  def test_standalone_supplies_both_as_present_none(self, standalone_proj, std):
    """§2c STANDALONE declares both ``<None>`` — present, not omitted."""
    snapshot = _main_snapshot(std, standalone_proj)
    assert snapshot_leaf(snapshot, WS_AUTH_PATH) is None
    assert snapshot_leaf(snapshot, BOX_AUTH_PATH) is None
    assert _credential_source(std, standalone_proj) is None


class TestTheCreateTimeSyncCarriesTheSelection:

  def test_the_per_agent_dir_is_the_selected_node(
    self, primary_proj, std, monkeypatch,
  ):
    """The create-time sync resolves the per-agent dir of the SELECTED node (P7)."""
    seen = []
    monkeypatch.setattr(
      start_cmd, "_apply_synced_copies",
      lambda *, snapshot, **_kw: seen.append(snapshot),
    )
    start_cmd._sync_box_at_create(
      std=std, proj=primary_proj, agent_name=NODE,
      global_config_path=None, agent_config_path=None,
      logger=logging.getLogger("test"), selection_level=_selection(),
    )
    (snapshot,) = seen
    assert snapshot_leaf(snapshot, BOX_AUTH_PATH) == _credential_source(
      std, primary_proj,
    )


class TestTheCreateTimeSeedCarriesTheChain:

  def test_a_user_seed_under_the_auth_dir_copies_from_it(self, primary_proj, std):
    """A user ``seeded`` row sourced at ``@workset.auth.path/x`` names ``<ws>/auth/x``."""
    box_file, _workset_file = box_workset_settings_paths(primary_proj)
    box_file.parent.mkdir(parents=True, exist_ok=True)
    box_file.write_text('box:\n  seeded:\n    "~/probe": ["@workset.auth.path/x"]\n')
    snapshot = start_cmd._apply_init_seeds(
      std=std, proj=primary_proj, agent_name=NODE,
      global_config_path=None, agent_config_path=None,
      logger=logging.getLogger("test"), selection_level=_selection(),
    )
    sources = [
      row.src for row in start_cmd._snapshot_assembly_seeded(snapshot) or []
      if row.dest.endswith("/probe")
    ]
    assert sources == [f"{snapshot_leaf(snapshot, WS_AUTH_PATH)}/x"]
    assert snapshot_leaf(snapshot, BOX_AUTH_PATH) == _credential_source(std, primary_proj)


class TestTheSelectionCannotBeOmitted:
  def test_the_launch_resolve_refuses_a_call_without_cli_level(self, primary_proj, std):
    """``cli_level`` is REQUIRED (P3): a whole-box caller cannot fold the chain without it."""
    with pytest.raises(TypeError, match="cli_level"):
      start_cmd._resolve_launch_snapshot(
        std=std, proj=primary_proj, agent_name=NODE,
        system_settings_path=None, agent_cfg_path=None,
        desc=None, install=None, target=None, agent_cfg=None,
      )


class TestEveryResolveFoldsTheChain:
  """The image / helper resolves fold the chain too — no caller-kind conditional."""

  @pytest.mark.parametrize("mode", ("primary", "named"))
  def test_a_narrow_resolve_names_the_same_per_agent_dir(self, mode, request, std):
    """A NARROW resolve (the image / helper shape) carries the SELECTED node's dir.

    INVERT: key the chain on ``narrow_bind_dests is None`` again and the leaf is
    absent here.
    """
    proj = request.getfixturevalue(f"{mode}_proj")
    snapshot, _deliveries = start_cmd._resolve_launch_snapshot(
      std=std, proj=proj, agent_name=NODE,
      system_settings_path=None, agent_cfg_path=None,
      desc=None, install=None, target=None,
      include_base_families=False, narrow_bind_dests=frozenset(),
      cli_level=_selection(),
    )
    assert snapshot_leaf(snapshot, BOX_AUTH_PATH) == _credential_source(std, proj)

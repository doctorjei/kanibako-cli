"""Set time resolves a ref into each floor fragment as the launch does (design 1A, N4)."""

from __future__ import annotations

import pytest

from kanibako.project.workset import create_workset
from kanibako.settings import config_interface as ci
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.settings_expand import expand
from kanibako.settings.settings_launch import (
  SYSTEM_SCALAR_FLOOR,
  ResolveSubject,
  build_launch_snapshot,
  resolve_inputs,
)

_AGENT = "claude"
#: The key each candidate is stored at: a workset-scope leaf with no path rule of its own.
_KEY = "workset.env.KANI_PROBE"
#: The ``pref:`` request that puts ``agent.<_AGENT>.model`` on the workset's cascade.
_PREF = {"agent": {_AGENT: {"model": "opus"}}}


def _candidates(target) -> dict[str, str]:
  """One reference per floor fragment, each derived from the fragment itself."""
  return {
    "system scalar floor": f"@{next(iter(SYSTEM_SCALAR_FLOOR))}/x",
    "system path tier": f"@{next(iter(target.system_floor))}/x",
    "workset anchor": "@meta.workset.path/x",
    "workset pref": f"@agent.{_AGENT}.model/x",
  }


def _leaf(store, key: str):
  node = store
  for seg in key.split("."):
    node = dict.__getitem__(node, seg)
  return node


@pytest.fixture
def workset(std, tmp_home):
  """A working set whose file requests one agent leaf through ``pref:``."""
  ws = create_workset("floorws", tmp_home / "floorws", std)
  inputs = resolve_inputs(
    subject=ResolveSubject.WORKSET, std=std, ws=ws, agent_name=_AGENT,
    system_path=std.settings,
  )
  path = inputs.cascade_workset_path
  doc = load_doc(path) if path.exists() else {}
  dump_doc(path, {**(doc or {}), "pref": _PREF})
  return ws


def _target(std, ws):
  return ci._set_time_target(
    std=std, proj=None, ws=ws, agent_name=_AGENT, system_path=std.settings,
  )


def _set_time_value(target, value: str):
  snapshot, ctx = ci._set_time_snapshot(target=target, agent_name=_AGENT, agent_path=None)
  candidate = ci._clone_keystore(snapshot)
  ci._set_leaf(candidate, _KEY.split("."), value)
  expanded, errors = expand(candidate, ctx, collect_errors=True)
  assert _KEY not in errors, errors[_KEY]
  return _leaf(expanded, _KEY)


def _launch_value(target, value: str):
  path = target.cascade_workset_path
  doc = load_doc(path)
  dump_doc(path, {**doc, "workset": {"env": {_KEY.rsplit(".", 1)[1]: value}}})
  snapshot = build_launch_snapshot(
    **target.as_kwargs(), agent_name=_AGENT, agent_path=None,
    default_categories=dict(target.system_floor),
  )
  return _leaf(snapshot, _KEY)


@pytest.mark.parametrize("fragment", [
  "system scalar floor", "system path tier", "workset anchor", "workset pref",
])
def test_set_time_resolves_each_floor_fragment_as_the_launch_does(fragment, std, workset):
  target = _target(std, workset)
  value = _candidates(target)[fragment]
  at_set = _set_time_value(target, value)
  at_launch = _launch_value(target, value)
  assert at_set == at_launch
  assert "@" not in str(at_set), at_set


def test_set_time_keeps_the_box_scalar_floor_at_a_workset_target(std, workset):
  """``@box.image`` resolves at a working set's ``set``, as it did before the shared fold."""
  assert "@" not in str(_set_time_value(_target(std, workset), "@box.image"))

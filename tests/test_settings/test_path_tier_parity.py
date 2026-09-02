"""TIER PARITY — the path tier a user may SET must equal the path tier LAUNCH resolves.

⚑ THE PROPERTY.  A path-tier key is resolvable at SET time (``config set`` can accept a
value naming it, and an ``@``-ref to it expands) and resolvable at LAUNCH time (a stored
``@``-ref to it expands when the box actually starts).  Those two must name the SAME SET
OF KEYS.  Where they differ, one of the two carriers is wrong — and the code cannot tell
you which, because neither side has ever compared itself to the other.  That is the
project's named recurring defect class: TWO CARRIERS OF ONE SHAPE, DISAGREEING, WITH
NOTHING COMPARING THEM.  This file is the comparison.

⚑ THE FOUR CARRIERS, in the two pairs asserted below.

* ``system.*`` SET side — ``config_interface._path_tier_split()[1]``.  DERIVED: it
  iterates the whole resolve of :data:`SYSTEM_PATH_DEFAULTS` and filters to declared
  keys, so a new declared key reaches it with no edit.
* ``system.*`` LAUNCH side — ``paths.system_path_floor(std)``.  HALF HAND-NAMED: a
  literal ``_FLOOR_ROOT_KEYS`` tuple plus a derived ``system.channels.`` prefix rule.
  The hand-named half is the half that can fall behind.
* ``config.*`` SET side — ``config_interface._path_tier_split()[0]``.  DERIVED, same
  iteration.
* ``config.*`` LAUNCH side — ``agent_select.launch_resolve_ctx(...).config``.  FULLY
  HAND-WRITTEN: five string literals in a dict display.

⚑⚑ WHY EFFECT-BASED AND NOT A LITERAL EXPECTED SET.  Writing the expected keys out by
hand here would make this file a FIFTH carrier of the same shape, which is the defect,
not a test of it.  Every assertion below compares two live carriers to each other, and
the anti-vacuity arm pins ONE side of each pair to the declared table so a filter bug
that emptied BOTH sides cannot pass silently.

⚑ THIS FILE WENT GREEN BY THE CODE MOVING, WHICH IS THE ONLY WAY IT MAY EVER GO GREEN.
It found two live defects and both are now fixed at their carrier: the ``system.*`` pair
was 11 vs 8 (``system.backup``, ``system.cache`` and ``system.runtime`` reached SET time
and no floor) and the ``config.*`` pair was 6 vs 5 (``config.journal``).  Both hand-written
carriers are now DERIVED from their declared table, so a key added later reaches both sides
with no edit here.  🛑 Do not repair a future failure by narrowing an assertion or by
exempting a key — a red here means a carrier fell behind, and the carrier is what moves.

⚑ Both halves rest on ``[R143]``: *"if it has a default value, yes, thay value should be
placed in the keystore."*  A declared key carrying a default must resolve, universally —
which is why no allowlist or discriminator appears anywhere below.

Indent note: 2 spaces, matching ``test_agent_select.py`` and the project style.
"""

from __future__ import annotations

import pytest

from kanibako.project.workset import add_project, create_workset
from kanibako.settings import agent_select, config_interface, paths
from kanibako.settings.paths import (
  WorksetSpec,
  resolve_project,
  resolve_standalone_project,
  resolve_workset_project,
)
from kanibako.settings.bootstrap import CONFIG_PATH_DEFAULTS, SYSTEM_PATH_DEFAULTS


# ---------------------------------------------------------------------------
# Fixtures: one resolved proj per mode.  ⚑ Declared locally, which is the house
# pattern for these three — ``test_channels/test_channel_keys.py`` says so in its own
# comment, and importing a sibling module's fixture trips ``F811`` on every parameter
# that uses it.
# ---------------------------------------------------------------------------

@pytest.fixture
def named_proj(std, config, tmp_home):
  ws_root = tmp_home / "worksets" / "my-set"
  workset = create_workset("my-set", ws_root, std)
  source = tmp_home / "original-project"
  source.mkdir()
  add_project(workset, "cool-app", source)
  return resolve_workset_project(
    WorksetSpec.from_workset(workset), "cool-app", std, config, initialize=True,
  )


@pytest.fixture
def primary_proj(std, config, project_dir):
  return resolve_project(std, config, str(project_dir), initialize=True)


@pytest.fixture
def standalone_proj(std, config, project_dir, credentials_dir):
  return resolve_standalone_project(std, config, str(project_dir), initialize=True)


def _report(label: str, set_time: set[str], launch: set[str]) -> str:
  """The failure text: both key sets in full, then the two one-sided differences."""
  return (
    f"{label} TIER PARITY BROKEN\n"
    f"  SET-time keys   ({len(set_time)}): {sorted(set_time)}\n"
    f"  LAUNCH keys     ({len(launch)}): {sorted(launch)}\n"
    f"  settable but never floored: {sorted(set_time - launch)}\n"
    f"  floored but not settable:   {sorted(launch - set_time)}"
  )


class TestAntiVacuity:
  """MANDATORY, and it runs first: a pair of empty sets is equal and proves nothing.

  Each case pins ONE side of one pair to the DECLARED table it is derived from.  If a
  filter bug emptied both sides of a parity assertion, these fail instead of it going
  quietly green.
  """

  def test_the_declared_tables_are_themselves_non_empty(self):
    assert SYSTEM_PATH_DEFAULTS, "SYSTEM_PATH_DEFAULTS is empty"
    assert CONFIG_PATH_DEFAULTS, "CONFIG_PATH_DEFAULTS is empty"

  def test_the_set_time_system_tier_is_the_whole_declared_table(self, config_file):
    """The SET side is the derived one, so it is the side that can be pinned.

    ⚑ ``config_file`` (hence ``tmp_home``) is REQUIRED: ``_path_tier_split`` reads the
    REAL ``$XDG_CONFIG_HOME/kanibako_config.yaml`` unless the environment is isolated,
    so without it this case is answered by whatever the host happens to have.
    """
    _, floor = config_interface._path_tier_split()
    assert set(floor) == set(SYSTEM_PATH_DEFAULTS), (
      "the set-time system.* tier no longer equals its own declared table; "
      f"missing={sorted(set(SYSTEM_PATH_DEFAULTS) - set(floor))} "
      f"extra={sorted(set(floor) - set(SYSTEM_PATH_DEFAULTS))}"
    )

  def test_the_set_time_config_tier_is_the_whole_declared_table(self, config_file):
    """⚑ ``config_file`` for the same reason as the case above — host isolation."""
    foundation, _ = config_interface._path_tier_split()
    assert set(foundation) == set(CONFIG_PATH_DEFAULTS), (
      "the set-time config.* foundation no longer equals its own declared table; "
      f"missing={sorted(set(CONFIG_PATH_DEFAULTS) - set(foundation))} "
      f"extra={sorted(set(foundation) - set(CONFIG_PATH_DEFAULTS))}"
    )

  def test_the_launch_side_of_each_pair_is_non_empty(self, std, primary_proj):
    assert paths.system_path_floor(std), "system_path_floor returned nothing"
    assert agent_select.launch_resolve_ctx(
      std, primary_proj, "claude",
    ).config, "launch_resolve_ctx carried no config.* entries"


class TestSystemTierParity:
  """``_path_tier_split()[1]`` (derived) vs ``system_path_floor`` (half hand-named).

  ⚑ NOT parametrized by box mode, and that is a measurement rather than an oversight:
  ``system_path_floor`` takes ``std`` alone and ``_path_tier_split`` takes nothing at
  all, so neither side has a per-project input to vary.  Three copies of one comparison
  would be theater.
  """

  def test_every_settable_system_path_key_reaches_the_launch_floor(self, std):
    _, set_time = config_interface._path_tier_split()
    launch = paths.system_path_floor(std)
    assert set(set_time), "set-time system.* tier is empty"
    assert set(launch), "launch system.* floor is empty"
    assert set(set_time) == set(launch), _report("system.*", set(set_time), set(launch))


class TestConfigTierParity:
  """``_path_tier_split()[0]`` (derived) vs ``launch_resolve_ctx().config`` (hand-written).

  ⚑ Parametrized across all three modes because ``launch_resolve_ctx`` DOES take the
  project — it reads ``proj.group`` for the workset name — so a mode-dependent divergence
  is possible here in a way it is not one layer up.
  """

  @pytest.mark.parametrize(
    "proj_fixture", ["primary_proj", "named_proj", "standalone_proj"],
  )
  def test_every_settable_config_key_reaches_the_launch_ctx(
    self, request, std, proj_fixture,
  ):
    proj = request.getfixturevalue(proj_fixture)
    set_time, _ = config_interface._path_tier_split()
    launch = agent_select.launch_resolve_ctx(std, proj, "claude").config
    assert set(set_time), "set-time config.* foundation is empty"
    assert set(launch), "launch config.* ctx is empty"
    assert set(set_time) == set(launch), _report(
      f"config.* ({proj_fixture})", set(set_time), set(launch),
    )

  def test_the_selection_pass_carries_the_same_config_keys(self, std, primary_proj):
    """⚑ ``agent_name=None`` is the SELECTION pass — same ctx builder, same tier.

    Asserted separately because the selection pre-pass runs BEFORE an agent is known,
    and it is the arm a reader is most likely to assume is a different, smaller ctx.
    """
    set_time, _ = config_interface._path_tier_split()
    launch = agent_select.launch_resolve_ctx(std, primary_proj, None).config
    assert set(set_time) == set(launch), _report(
      "config.* (selection pass)", set(set_time), set(launch),
    )

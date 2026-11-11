"""Agent SELECTION — the P7 seam (spec §1A / §2b / §2g / §2h).

Each discriminator pins selection behavior against its named mutation.

Covers:

* the selection ORDER — ``system.agent`` < workset pref < box pref < ``--agent``;
* the THREE-state read (name / present-``None`` / absent), because *no default is
  set* and *nothing was ever set* are DIFFERENT answers and get DIFFERENT refusals
  — the installed-agent count rule that used to answer the third state was retired
  2026-09-19 (spec §2b);
* the §1A SELECTION LEVEL that keeps ``{system.agent}`` equal to the node that runs;
* the RETIRED-key refusals (``box.agent_name`` / ``system.default_agent``, M-4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config_io import dump_doc, parse_doc_text

from kanibako.settings.agent_select import AgentSelection, SELECTION_KEY
from tests.support.agent_selection import agent_selection
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.settings_assemble import refuse_retired_keys
from kanibako.settings.settings_launch import build_launch_snapshot, resolve_selected_agent
from kanibako.settings.settings_resolve import ResolveCtx, SettingsError
from kanibako.settings.bootstrap import CONFIG_PATH_DEFAULTS
from tests.support.early import early_record

AGENTS = frozenset({"claude", "goose", "codex"})


def _ctx(agent_name: str | None = None) -> ResolveCtx:
  return ResolveCtx(
    agent_name=agent_name,
    workset_name=None,
    host_home="/home/host",
    xdg={"XDG_DATA_HOME": "/data"},
  )


def _yaml(path: Path, data: dict) -> Path:
  dump_doc(path, data)
  return path


def _select(tmp_path: Path, *, system=None, workset=None, box=None) -> object:
  def w(name, data):
    return _yaml(tmp_path / f"{name}.yaml", data) if data is not None else None

  return resolve_selected_agent(
    ctx=_ctx(),
    system_path=w("system", system),
    workset_path=w("workset", workset),
    box_path=w("box", box),
    valid_agents=AGENTS,
  )


# --------------------------------------------------------------------------- #
# The selection ORDER (spec §2h)                                    #
# --------------------------------------------------------------------------- #


class TestSelectionOrder:
  def test_the_stored_system_agent_is_the_default(self, tmp_path):
    assert _select(tmp_path, system={"system": {"agent": "claude"}}) == "claude"

  def test_a_workset_pref_beats_the_stored_default(self, tmp_path):
    assert (
      _select(
        tmp_path,
        system={"system": {"agent": "claude"}},
        workset={"pref": {"system": {"agent": "goose"}}},
      )
      == "goose"
    )

  def test_a_box_pref_beats_a_workset_pref(self, tmp_path):
    """§1A — box beats workset by ASSIGNMENT ORDER.
    INVERT: swap the two overlays' splice positions -> this reddens."""
    assert (
      _select(
        tmp_path,
        system={"system": {"agent": "claude"}},
        workset={"pref": {"system": {"agent": "goose"}}},
        box={"pref": {"system": {"agent": "codex"}}},
      )
      == "codex"
    )

  def test_a_box_file_cannot_set_system_agent_directly(self, tmp_path):
    """An upward write is DROPPED at assembly (§0 directional enforcement) —
    the pref is the only channel. INVERT: keep a box file's ``system:`` table
    and the box could silently repoint a system-scope key."""
    assert (
      _select(
        tmp_path,
        system={"system": {"agent": "claude"}},
        box={"system": {"agent": "goose"}},
      )
      == "claude"
    )


# --------------------------------------------------------------------------- #
# The THREE-state read — "no default set" is NOT "unset" (spec §2b)            #
# --------------------------------------------------------------------------- #


class TestSelectionThreeState:
  def test_absent_everywhere_is_MISSING_not_none(self, tmp_path):
    """``__MISSING__`` (nothing set) must stay distinguishable from ``None``
    (an explicit null): each gets its OWN refusal — *setup has never run* vs
    *no default is set* — and collapsing them prints the wrong one."""
    assert _select(tmp_path) is __MISSING__

  def test_a_null_pref_is_KEPT_as_present_none(self, tmp_path):
    """⚑ The THREE-STATE distinction, and the silent-failure hazard §2h names.

    ``pref.system.agent: null`` installs present-``None`` VERBATIM, and
    present-``None`` on a SCALAR leaf is KEPT (``_resolve_present_none``), so a
    box can decline the system default rather than inherit it — which the
    retired ``box.agent_name`` could not express.

    INVERT: ``if value is None: continue`` anywhere on the pref/selection path
    (the most natural guard to write) -> this returns ``__MISSING__``, and the
    box is told to run ``setup`` when what it actually says is *no default*.
    """
    got = _select(
      tmp_path,
      system={"system": {"agent": "claude"}},
      box={"pref": {"system": {"agent": None}}},
    )
    assert got is None
    assert got is not __MISSING__

  def test_a_workset_null_pref_suppresses_for_its_boxes(self, tmp_path):
    assert (
      _select(
        tmp_path,
        system={"system": {"agent": "claude"}},
        workset={"pref": {"system": {"agent": None}}},
      )
      is None
    )


# --------------------------------------------------------------------------- #
# Failure handling — loud where it must be, quiet where it must be             #
# --------------------------------------------------------------------------- #


class TestSelectionFailures:
  def test_an_unresolvable_selection_errors_naming_the_key(self, tmp_path):
    """§2h: *"We don't want to just moving on with bad settings."*

    INVERT: swallow the lenient-expand error map and the launch silently
    becomes a NO-AGENT box (or the system default) instead of failing.
    """
    with pytest.raises(SettingsError) as ei:
      _select(
        tmp_path,
        box={"pref": {"system": {"agent": "{meta.nope.dangling}"}}},
      )
    assert SELECTION_KEY in str(ei.value)

  def test_an_unrelated_defect_does_not_decide_which_agent_runs(self, tmp_path):
    """⚑ WHY THE SELECTION PASS EXPANDS LENIENTLY.

    This pass has no active agent yet, so ``{$AGENT}`` cannot resolve. A
    perfectly legitimate ``{$AGENT}`` in an UNRELATED value must not abort
    selection for a box whose real launch resolves it fine.

    INVERT: use strict ``expand`` here -> SettingsError, and a box with any
    ``{$AGENT}``-bearing setting can no longer start at all.
    """
    assert (
      _select(
        tmp_path,
        system={
          "system": {"agent": "claude", "cache": "/c/{$AGENT}"},
        },
      )
      == "claude"
    )


# --------------------------------------------------------------------------- #
# The §1A SELECTION LEVEL — {system.agent} equals the node that RUNS            #
# --------------------------------------------------------------------------- #


class TestSelectionLevel:
  def test_the_selection_level_outranks_a_contrary_pref(self, tmp_path):
    """⚑ THE F2 INCOHERENCE, CLOSED.

    ``--agent claude`` with ``pref.system.agent: goose`` must not leave the
    snapshot saying goose while claude runs — three consumers dereference
    ``{system.agent}`` (both re-pointed §2c anchors, and C-CANON's agent
    chapter next).

    INVERT: drop the ``selection_level`` splice -> the snapshot says goose.
    """
    box = _yaml(
      tmp_path / "box.yaml",
      {"pref": {"system": {"agent": "goose"}}},
    )
    snap = build_launch_snapshot(
      agent_name="claude",
      ctx=_ctx("claude"),
      system_path=None,
      agent_path=None,
      workset_path=None,
      box_path=box,
      valid_agents=AGENTS,
      cli_level={"system.agent": "claude"},
    )
    assert snap.system.agent == "claude"

  def test_without_the_selection_level_the_pref_would_win(self, tmp_path):
    """The MEASURED baseline for the test above — this is what the launch
    would report if the level were dropped."""
    box = _yaml(
      tmp_path / "box.yaml",
      {"pref": {"system": {"agent": "goose"}}},
    )
    snap = build_launch_snapshot(
      agent_name="claude",
      ctx=_ctx("claude"),
      system_path=None,
      agent_path=None,
      workset_path=None,
      box_path=box,
      valid_agents=AGENTS,
    )
    assert snap.system.agent == "goose"

  def test_the_selection_level_supplies_a_cli_named_agent(self, tmp_path):
    """⚑ The THIRD incoherence: a box launched on ``--agent claude`` with
    NOTHING stored runs claude while ``system.agent`` is ABSENT, so every
    ``{system.agent}`` dereference would coerce to ``""``. Installing the
    resolved selection ALWAYS covers it.

    ⚑ This used to be written about the commonest host — one agent installed,
    nothing stored — which the installed-count rule autopicked. That host
    REFUSES now (spec §2b), so the incoherence is reached by ``--agent``."""
    snap = build_launch_snapshot(
      agent_name="claude",
      ctx=_ctx("claude"),
      system_path=None,
      agent_path=None,
      workset_path=None,
      box_path=None,
      valid_agents=AGENTS,
      cli_level={"system.agent": "claude"},
    )
    assert snap.system.agent == "claude"

  def test_a_nodeless_box_installs_nothing(self):
    """A node-less selection must leave ``system.agent`` absent — pinning it to
    the ``general`` template slot would make the box look agent-bearing.

    ⚑ ``select_agent`` no longer PRODUCES this shape (a null selection refuses
    now), and ``shell`` will NOT restore it — keyspec §2b/§2d make the
    plain-shell box a NAMED node (``"shell"``), so a D2 selection is
    node-BEARING. The property is pinned on the dataclass anyway: it is the
    floor under a two-vocabulary seam whose failure was measured (bifrost
    E-NULL), it costs one assertion, and an untested branch is how the empty
    node reached ``resolve_target`` the first time.
    """
    assert agent_selection(node="", source="suppressed").selection_level is None
    assert agent_selection(node="claude", source="cli").selection_level == {
      "system.agent": "claude",
    }

  def test_the_workset_auth_anchor_follows_the_selection(self, tmp_path):
    """The re-pointed §2c anchor resolves through ``{system.agent}``, so a
    ``--agent`` launch must land in THAT agent's credential dir.

    INVERT: interpolate the agent name into the anchor again and this passes
    for the wrong reason (it would follow ``agent_name``, not the key).
    """
    from kanibako.settings.settings_launch import auth_chain_floor

    box = _yaml(
      tmp_path / "box.yaml",
      {"pref": {"system": {"agent": "goose"}}},
    )
    snap = build_launch_snapshot(
      agent_name="claude",
      ctx=_ctx("claude"),
      system_path=None,
      agent_path=None,
      workset_path=None,
      box_path=box,
      valid_agents=AGENTS,
      auth_chain=auth_chain_floor(mode="primary", agent_name="claude"),
      cli_level={"system.agent": "claude"},
    )
    assert snap.meta.box.auth.workset_path.endswith("/claude")


# --------------------------------------------------------------------------- #
# RETIRED spellings — refuse by name (M-4; the ruled-in zero-migration check)  #
# --------------------------------------------------------------------------- #


class TestRetiredKeyRefusal:
  def test_box_agent_name_is_refused_with_the_cure(self, tmp_path):
    """⚑ The upgrade story. Without this the box SILENTLY runs the system
    default — with that agent's CREDENTIALS seeded into it.

    INVERT: drop the check (or warn instead of raise) -> a goose box quietly
    becomes a claude box.
    """
    f = _yaml(tmp_path / "box.yaml", {"box": {"agent_name": "goose"}})
    with pytest.raises(SettingsError) as ei:
      refuse_retired_keys(parse_doc_text(f.read_text()), level="box", path=f)
    msg = str(ei.value)
    assert "box.agent_name" in msg
    assert "RETIRED" in msg
    assert "RULE CHANGED" in msg  # it is not "your config is wrong"
    assert "pref.system.agent=goose" in msg
    assert str(f) in msg  # names the FILE

  def test_box_level_cure_interpolates_the_box_argument(self, tmp_path):
    """``box set`` needs the box positional to be copy-pasteable
    from outside the box's own cwd — the caller supplies it via *box_ref*."""
    f = _yaml(tmp_path / "box.yaml", {"box": {"agent_name": "goose"}})
    with pytest.raises(SettingsError) as ei:
      refuse_retired_keys(
        parse_doc_text(f.read_text()),
        level="box",
        path=f,
        box_ref="myproj",
      )
    msg = str(ei.value)
    assert "kanibako box set myproj pref.system.agent=goose" in msg
    # 🛑 The cure NAMES AN AGENT and stops. It used to offer `--null
    # pref.system.agent` "for a no-agent box"; a null selection REFUSES to
    # launch now (spec §2b), so that half sent the reader to a second error.
    assert "--null" not in msg

  def test_box_name_is_ignored_off_the_box_level(self, tmp_path):
    """No SINGLE box is being refused for at workset/system/base/agent scope, so a
    *box_ref* passed in anyway must not leak into the cure."""
    f = _yaml(tmp_path / "workset.yaml", {"box": {"agent_name": "goose"}})
    with pytest.raises(SettingsError) as ei:
      refuse_retired_keys(
        parse_doc_text(f.read_text()),
        level="workset",
        path=f,
        box_ref="myproj",
      )
    msg = str(ei.value)
    assert "myproj" not in msg

  def test_a_stored_bool_is_quoted_AS_YAML_SPELLS_IT(self, tmp_path):
    """The message quotes the user's own leaf back at them, so it has to be
    the spelling their FILE holds — ``true``, never Python's ``True``.  The
    same derivation the behavior refusal uses (``_stored_spelling``).

    INVERT: quote the value through bare ``str()`` and this reddens.
    """
    f = _yaml(tmp_path / "box.yaml", {"box": {"agent_name": True}})
    with pytest.raises(SettingsError) as ei:
      refuse_retired_keys(parse_doc_text(f.read_text()), level="box", path=f)
    msg = str(ei.value)
    assert "pref.system.agent=true" in msg
    assert "True" not in msg

  def test_system_default_agent_is_refused_with_the_cure(self, tmp_path):
    f = _yaml(
      tmp_path / "system.yaml",
      {"agent": {"default": {"default_agent": "claude"}}},
    )
    with pytest.raises(SettingsError) as ei:
      refuse_retired_keys(parse_doc_text(f.read_text()), level="system", path=f)
    msg = str(ei.value)
    assert "system.default_agent" in msg
    assert "system set system.agent=claude" in msg

  def test_a_clean_file_passes(self, tmp_path):
    f = _yaml(tmp_path / "box.yaml", {"pref": {"system": {"agent": "goose"}}})
    refuse_retired_keys(parse_doc_text(f.read_text()), level="box", path=f)

  def test_an_unrelated_agent_default_table_is_not_refused(self, tmp_path):
    """SCOPE IS TIGHT: only the two retired spellings. A normal
    ``agent.default.model`` must be untouched."""
    f = _yaml(tmp_path / "system.yaml", {"agent": {"default": {"model": "opus"}}})
    refuse_retired_keys(parse_doc_text(f.read_text()), level="system", path=f)


# --------------------------------------------------------------------------- #
# END-TO-END through select_agent — the SEAM, not the helper                   #
# --------------------------------------------------------------------------- #
#
# ⚑ The tests above call ``refuse_retired_keys`` / ``resolve_selected_agent``
# DIRECTLY, so deleting the refusal LOOP from ``select_agent`` would redden none of
# them — and ``tests/conftest.py``'s ``start_mocks`` patches ``select_agent``
# wholesale; launch tests cannot catch it. These drive the real seam.


class _FakeGroup:
  is_default = True
  name = "__PRIMARY__"

  def __init__(self, root):
    self.root = root


def _proj(tmp_path, *, name: "str | None" = "myproj"):
  """The minimal ``ProjectPaths`` shape ``select_agent`` reads.

  It needs exactly: ``mode`` / ``metadata_path`` / ``group`` (for
  ``box_workset_settings_paths``) and ``name`` (threaded to the box-level retired-key
  cure — ``None``/``""`` is the real NAMELESS-box case, spec:
  ``settings/paths.py`` falls back to a short hash for one when it is falsy).
  """
  from types import SimpleNamespace

  from kanibako.settings.paths import BoxMode

  meta = tmp_path / "box"
  meta.mkdir(parents=True, exist_ok=True)
  ws = tmp_path / "ws"
  (ws / "boxes").mkdir(parents=True, exist_ok=True)
  return SimpleNamespace(
    mode=BoxMode.primary,
    metadata_path=meta,
    group=_FakeGroup(ws),
    name=name,
  )


def _std(tmp_path):
  """The ``StandardPaths`` DOUBLE ``launch_resolve_ctx`` reads.

  ⚑ IT CARRIES THE WHOLE LAYER-1 TIER, not the subset some caller happened to read.
  ``paths.host_config_map`` derives ``ctx.config`` from ``CONFIG_PATH_DEFAULTS``, so a
  double one field short raises ``AttributeError`` at every ctx build. It used to hold
  exactly the five fields the ctx builder spelled by hand, which made it a FOURTH
  carrier of the same key set — and the one that would have hidden the widening.

  ⚑ The completeness check below is the point: a Layer-1 key declared tomorrow fails
  HERE, naming itself, instead of reddening every seam test with an opaque attribute
  error on a name the reader has to go look up.
  """
  from types import SimpleNamespace

  data = tmp_path / "data"
  (data / "global").mkdir(parents=True, exist_ok=True)
  std = SimpleNamespace(
    settings=data / "global" / "settings.yaml",
    data=data,
    data_home=data,
    agents=data / "agents",
    registry=data / "global" / "registry.yaml",
    primary_workset=tmp_path / "ws",
    journal=data / "global" / "journal.yaml",
    early_system=early_record(tmp_path),
  )
  missing = [k for k in CONFIG_PATH_DEFAULTS if not hasattr(std, k.split(".", 1)[1])]
  assert not missing, f"the std double is short of the Layer-1 tier: {missing}"
  return std


class TestSelectAgentSeam:
  def test_retired_box_agent_name_in_a_file_is_refused(self, tmp_path, monkeypatch):
    """⚑ THE UPGRADE STORY, through the REAL seam.

    MUTATION: delete the ``refuse_retired_keys`` loop from ``select_agent``
    and this test goes GREEN-with-the-wrong-agent — the box silently launches
    ``claude`` (the stored default) instead of the ``goose`` it asked for, with
    claude's credentials. That is the whole reason the refusal exists, and it
    is why this must be pinned at the seam rather than on the helper.
    """
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object, "goose": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    dump_doc(std.settings, {"system": {"agent": "claude"}})
    # The RETIRED spelling, in the box tier.
    box_file, _ws = _box_workset(proj)
    dump_doc(box_file, {"box": {"agent_name": "goose"}})

    with pytest.raises(SettingsError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    msg = str(ei.value)
    assert "box.agent_name" in msg
    assert "pref.system.agent=goose" in msg
    assert str(box_file) in msg

  def test_retired_system_default_agent_is_refused(self, tmp_path, monkeypatch):
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    dump_doc(std.settings, {"agent": {"default": {"default_agent": "claude"}}})
    with pytest.raises(SettingsError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    assert "system.default_agent" in str(ei.value)

  def test_a_dropped_agent_table_in_the_box_file_produces_no_cure(
    self,
    tmp_path,
    monkeypatch,
  ):
    """🛑 A CURE FOR A NO-OP IS WORSE THAN NO CURE — at THIS seam too.

    ``agent.default.default_agent`` is the one retired spelling this scan
    reaches under a CONTAINING scope's table, so in a box file directional
    enforcement (spec §0) had already dropped the whole ``agent:`` table before
    the merge. Refusing on it sent the user to run ``system set
    system.agent=…`` over a line whose deletion changes nothing, and the box
    selected exactly the same agent either way.

    ⚑ MEASURED, and it is why the pin lives here rather than only at the
    resolve seam: a box file carrying just this table puts NOTHING undeclared
    into the snapshot, so ``settings_launch``'s §0 refusal never fires and
    never runs its own retirement scan. This was the only voice that file had.

    ⚑ The narrowing cannot be over-read: the ``box.*`` spellings are not under
    a containing scope's table, so ``test_retired_box_agent_name_in_a_file_is_
    refused`` above still refuses at this very tier.
    """
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    dump_doc(std.settings, {"system": {"agent": "claude"}})
    box_file, _ws = _box_workset(proj)
    dump_doc(box_file, {"agent": {"default": {"default_agent": "goose"}}})
    sel = select_agent(std=std, proj=proj, explicit_agent=None)
    assert (sel.node, sel.source) == ("claude", "settings")

  def test_a_present_null_retired_key_is_also_refused(self, tmp_path, monkeypatch):
    """A ``box: {agent_name: null}`` leaf is STILL the retired key — a
    3-state-aware check must not conflate PRESENT-null with ABSENT (the same
    conflation §2h warns about for prefs). MUTATION: probe with ``is None``
    and this reddens."""
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    box_file, _ws = _box_workset(proj)
    box_file.write_text("box:\n  agent_name:\n")
    with pytest.raises(SettingsError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    assert "box.agent_name" in str(ei.value)

  def test_a_clean_box_selects_through_the_seam(self, tmp_path, monkeypatch):
    """The seam's happy path: a box REQUEST wins over the stored default and
    comes back as an ``AgentSelection``."""
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object, "goose": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    dump_doc(std.settings, {"system": {"agent": "claude"}})
    box_file, _ws = _box_workset(proj)
    dump_doc(box_file, {"pref": {"system": {"agent": "goose"}}})

    sel = select_agent(std=std, proj=proj, explicit_agent=None)
    assert (sel.node, sel.source) == ("goose", "settings")
    assert sel.selection_level == {"system.agent": "goose"}

  def test_explicit_agent_beats_the_request_at_the_seam(self, tmp_path, monkeypatch):
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object, "goose": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    box_file, _ws = _box_workset(proj)
    dump_doc(box_file, {"pref": {"system": {"agent": "goose"}}})
    sel = select_agent(std=std, proj=proj, explicit_agent="claude")
    assert (sel.node, sel.source) == ("claude", "cli")

  @pytest.mark.parametrize(
    "spelling",
    ["null", "Null", "NULL", "~", "", "None"],
    ids=["null", "Null", "NULL", "tilde", "blank-after-colon", "python-None"],
  )
  def test_a_null_request_refuses_saying_no_default_is_set(
    self,
    tmp_path,
    monkeypatch,
    spelling,
  ):
    """A present-``None`` selection REFUSES — it does not give a plain shell.

    🛑 Spec §2b: ``null`` means *no default is set*, so an agent must be named
    explicitly. ⚑ No RELEASED version ever gave a plain shell this way — ``pref.*``
    does not exist in v1.7.2 (``git grep -c "pref\\.system\\.agent" v1.7.2`` is
    zero), so this spelling was born and retired inside the 1.8.0 rc series. The
    plain-shell box is reached BY NAME: ``--agent shell`` /
    ``pref.system.agent: shell`` (the retired underscore spelling — MIGRATION.md
    § "The plain-shell store is").

    ⚑ Every spelling here is one YAML reads as Python ``None`` — including a key
    left BLANK after its colon, which makes this state reachable by typo. The
    refusal therefore has to name the state, not echo a value.

    MUTATION: return ``AgentSelection(node="", source="suppressed")`` again and
    every row launches a plain shell where the user asked for an agent.
    """
    from kanibako.errors import AgentNoDefaultError
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    dump_doc(std.settings, {"system": {"agent": "claude"}})
    box_file, _ws = _box_workset(proj)
    box_file.write_text(f"pref:\n  system:\n    agent: {spelling}\n")
    with pytest.raises(AgentNoDefaultError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    msg = str(ei.value)
    assert "No default agent is set" in msg
    assert "--agent <name>" in msg
    assert "pref.system.agent=<name>" in msg
    # 🛑 NOT the other refusal: this one must never send the reader to setup.
    assert "kanibako setup" not in msg

  @pytest.mark.parametrize("spelling", ["'None'", "none"], ids=["quoted-None", "none"])
  def test_a_quoted_none_or_lowercase_none_is_a_NAME_not_the_null_state(
    self,
    tmp_path,
    monkeypatch,
    spelling,
  ):
    """A plain ``None`` is the null (above); a quoted ``'None'`` or ``none`` is a STRING.

    It asks for an agent LITERALLY CALLED that and is told it is not installed — a
    third failure, never confusable with the null refusal; this pins the fork.
    """
    from kanibako.errors import AgentNoDefaultError, AgentNotInstalledError
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    box_file, _ws = _box_workset(proj)
    box_file.write_text(f"pref:\n  system:\n    agent: {spelling}\n")
    with pytest.raises(AgentNotInstalledError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    msg = str(ei.value)
    assert f"'{spelling.strip(chr(39))}' is not installed" in msg
    assert "No default agent" not in msg
    assert not isinstance(ei.value, AgentNoDefaultError)

  def test_an_unset_key_refuses_and_names_setup(self, tmp_path, monkeypatch):
    """Nothing set it anywhere ⇒ the OTHER refusal, even with ONE agent installed.

    🛑 This is where the installed-agent count rule used to autopick. Spec §2b:
    unset means setup has never chosen one, so the cure is ``kanibako setup``.

    MUTATION: restore the count rule and this returns ``("claude", "autopick")``.
    """
    from kanibako.errors import AgentUnsetError
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    with pytest.raises(AgentUnsetError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    msg = str(ei.value)
    assert "system.agent is unset" in msg
    assert "kanibako setup" in msg
    # 🛑 NOT the other refusal: the two are deliberately different sentences.
    assert "No default agent is set:" not in msg


class TestABlankExplicitRefIsGivenNotUnset:
  """``--agent ""`` is a ref the user TYPED, so the cascade never answers it.

  The seam decides for itself whether the cascade is consulted AT ALL, and it
  can answer the whole question without ever reaching the arbiter — so a truthy
  "was one given?" test here was not merely redundant with
  ``resolve_agent``'s: it was the only thing standing between a blank flag and
  a silent answer.
  """

  @pytest.mark.parametrize("blank", ["", "  ", "\t\n"])
  def test_a_blank_ref_is_refused_where_a_null_pref_would_answer(
    self,
    tmp_path,
    monkeypatch,
    blank,
  ):
    """MUTATION: restore ``if not explicit_agent:`` and the file's null request
    answers a question the command line asked — the reader gets
    ``AgentNoDefaultError`` about their settings file for a flag they typed.
    The companion above
    (``test_a_null_request_refuses_saying_no_default_is_set``) drives the SAME
    tree with no flag and must keep giving THAT refusal.
    """
    from kanibako.errors import ConfigError
    from kanibako.settings.agent_select import select_agent

    from tests.support.agent_refs import blank_ref_refusal

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    dump_doc(std.settings, {"system": {"agent": "claude"}})
    box_file, _ws = _box_workset(proj)
    box_file.write_text("pref:\n  system:\n    agent:\n")

    with pytest.raises(ConfigError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=blank)
    assert str(ei.value) == blank_ref_refusal()

  def test_a_blank_ref_is_refused_where_a_request_would_have_won(
    self,
    tmp_path,
    monkeypatch,
  ):
    """The other half: a box that HAS a working request still refuses.

    Without this, a reader could conclude the refusal belongs to the
    suppression arm rather than to the flag.
    """
    from kanibako.errors import ConfigError
    from kanibako.settings.agent_select import select_agent

    from tests.support.agent_refs import blank_ref_refusal

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object, "goose": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    box_file, _ws = _box_workset(proj)
    dump_doc(box_file, {"pref": {"system": {"agent": "goose"}}})

    with pytest.raises(ConfigError) as ei:
      select_agent(std=std, proj=proj, explicit_agent="")
    assert str(ei.value) == blank_ref_refusal()
    # Control: the same tree, no flag, resolves the request it carries.
    assert select_agent(std=std, proj=proj, explicit_agent=None).node == "goose"

  def test_a_blank_STORED_agent_is_refused_through_the_seam(
    self,
    tmp_path,
    monkeypatch,
  ):
    """A terminal ``""`` in a settings file is a value too (spec §2h).

    MUTATION: restore the ``or`` fall-through in ``resolve_agent`` and the
    blank line the user wrote is read as an absence instead of refused.
    """
    from kanibako.errors import ConfigError
    from kanibako.settings.agent_select import select_agent

    from tests.support.agent_refs import blank_ref_refusal

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path)
    dump_doc(std.settings, {"system": {"agent": ""}})

    with pytest.raises(ConfigError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    assert str(ei.value) == blank_ref_refusal()
    # Control: with the key ABSENT — the real "unset" — the seam refuses too,
    # by the OTHER message. Three states, and a blank is none of them.
    from kanibako.errors import AgentUnsetError

    dump_doc(std.settings, {"system": {}})
    with pytest.raises(AgentUnsetError):
      select_agent(std=std, proj=proj, explicit_agent=None)


# --------------------------------------------------------------------------- #
# The BOX ARGUMENT in the cure — the ruling, through the REAL seam             #
# --------------------------------------------------------------------------- #
#
# ⚑ ``TestRetiredKeyCureIsLevelAppropriate`` (and ``settings_assemble``'s own
# mutation-proved unit tests) drove ``retired_key_cure`` / ``refuse_retired_keys``
# DIRECTLY — that machinery is correct but was NOT WIRED: the production caller,
# ``select_agent``, never passed ``box_ref``, so the user-visible cure line stayed
# the bare form regardless. These three drive ``select_agent`` itself, so deleting
# its ``box_ref=project_cure_ref(proj) if proj.name else None`` argument to
# ``read_settings_files`` reddens them even though every test above stays green.


class TestSelectAgentSeamBoxArgument:
  def test_a_named_box_gets_the_box_argument_in_the_cure(self, tmp_path, monkeypatch):
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object, "goose": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path, name="myproj")
    box_file, _ws = _box_workset(proj)
    dump_doc(box_file, {"box": {"agent_name": "goose"}})
    with pytest.raises(SettingsError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    msg = str(ei.value)
    assert "kanibako box set myproj pref.system.agent=goose" in msg
    # 🛑 The cure NAMES AN AGENT and stops. It used to offer `--null
    # pref.system.agent` "for a no-agent box"; a null selection REFUSES to
    # launch now (spec §2b), so that half sent the reader to a second error.
    assert "--null" not in msg

  def test_a_nameless_box_falls_back_to_the_subject_PLACEHOLDER(
    self,
    tmp_path,
    monkeypatch,
  ):
    """``proj.name`` can be falsy (spec: the addressable-name-less case
    ``settings/paths.py`` covers with a short-hash DISPLAY fallback, which is
    not a ``box set`` positional).

    ⮕ **SUBJECT CHANGED.** This used to pin a degrade to the BARE form. The
    bare form parses, which is the trap: ``box set`` takes its arguments as a
    list, so the key alone is accepted and the write lands on whatever box the
    reader's cwd resolves to — a different box, silently. Unknown subject ⇒ the
    ``<box>`` placeholder, the choice ``_retired_mirror_cure`` already made.
    Still no ``None`` and no doubled space.
    """
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object, "goose": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path, name=None)
    box_file, _ws = _box_workset(proj)
    dump_doc(box_file, {"box": {"agent_name": "goose"}})
    with pytest.raises(SettingsError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    msg = str(ei.value)
    assert "kanibako box set <box> pref.system.agent=goose" in msg
    assert "--null" not in msg
    assert "kanibako box set  pref.system.agent=goose" not in msg  # no doubled space
    assert "None" not in msg

  def test_a_non_box_level_refusal_never_leaks_a_box_argument(self, tmp_path, monkeypatch):
    """``system.default_agent`` is a SYSTEM-tier refusal — a named box in scope
    must not leak into a cure that names no single box."""
    from kanibako.settings.agent_select import select_agent

    monkeypatch.setattr(
      "kanibako.targets.discover_targets",
      lambda *a, **k: {"claude": object},
    )
    std, proj = _std(tmp_path), _proj(tmp_path, name="myproj")
    dump_doc(std.settings, {"agent": {"default": {"default_agent": "claude"}}})
    with pytest.raises(SettingsError) as ei:
      select_agent(std=std, proj=proj, explicit_agent=None)
    msg = str(ei.value)
    assert "system.default_agent" in msg
    assert "myproj" not in msg


def _box_workset(proj):
  from kanibako.settings.paths import box_workset_settings_paths

  box_file, ws_file = box_workset_settings_paths(proj)
  box_file.parent.mkdir(parents=True, exist_ok=True)
  return box_file, ws_file


class TestRetiredKeyCureIsLevelAppropriate:
  """SHOULD-4: the cure must be a fix the reader can actually apply.

  A pref may be written ONLY in a workset or box file (spec §2h), so a
  ``box.agent_name`` found in a SYSTEM / AGENT / BASE file has no legal pref
  equivalent — M-4 says FLAG it, do not relocate it. Prescribing `box set pref…`
  there would be a cure that cannot work.
  """

  def _msg(self, tmp_path, level, data):
    f = _yaml(tmp_path / f"{level}.yaml", data)
    with pytest.raises(SettingsError) as ei:
      refuse_retired_keys(parse_doc_text(f.read_text()), level=level, path=f)
    return str(ei.value)

  def test_box_and_workset_get_the_pref_cure(self, tmp_path):
    """⚑ THE VERB IS THE LEVEL. A workset file's cure is ``workset set``, not
    ``box set`` — the levels share the §2h permission, never the command."""
    for level in ("box", "workset"):
      msg = self._msg(tmp_path, level, {"box": {"agent_name": "goose"}})
      assert f"kanibako {level} set <{level}> pref.system.agent=goose" in msg
      assert "no-agent box" not in msg

  def test_the_workset_cure_names_the_workset_verb_AND_a_subject(self, tmp_path):
    """DEFECT: the scalar cure hardcoded ``box set`` at BOTH pref-legal levels,
    so a workset file was handed the wrong verb with no subject at all —
    ``kanibako box set pref.system.agent=goose``, which parses and writes to a
    BOX. The sibling ``_retired_mirror_cure`` never had the bug; this pins the
    two functions to the same shape.

    INVERT: hardcode ``box`` in ``retired_key_cure`` again and this reddens.
    """
    msg = self._msg(tmp_path, "workset", {"box": {"agent_name": "goose"}})
    cure = msg.split("Fix: ", 1)[1].splitlines()[0].strip()
    assert cure.startswith("kanibako workset set <workset> pref.system.agent=goose")
    assert "--null" not in cure
    # The BOX verb must not appear at all at this level.
    assert "kanibako box set" not in cure

  def test_system_and_base_get_the_flag_dont_relocate_cure(self, tmp_path):
    for level in ("system", "base", "agent"):
      msg = self._msg(tmp_path, level, {"box": {"agent_name": "goose"}})
      assert "REMOVE it" in msg
      assert f"NO equivalent at {level} scope" in msg
      # It still says what to do INSTEAD, at both plausible intents. No
      # single box is in scope here, so the box arm carries the placeholder.
      assert "system set system.agent=goose" in msg
      assert "box set <box> pref.system.agent=goose" in msg

  @pytest.mark.parametrize("level", ["system", "base", "agent"])
  @pytest.mark.parametrize(
    "data",
    [
      {"box": {"agent_name": "goose"}},
      {"box": {"agent": {"model": "x"}}},
    ],
  )
  def test_a_removal_cure_is_not_introduced_as_a_set(self, tmp_path, level, data):
    msg = self._msg(tmp_path, level, data)
    assert "Fix: REMOVE it" in msg
    assert "The fix below is a `set`" not in msg
    assert "The fix below starts with that removal" in msg

  def test_a_set_cure_is_still_introduced_as_a_set(self, tmp_path):
    msg = self._msg(tmp_path, "box", {"box": {"agent_name": "goose"}})
    assert "The fix below is a `set`" in msg

  def test_the_system_default_cure_is_level_independent(self, tmp_path):
    for level in ("system", "box"):
      msg = self._msg(
        tmp_path,
        level,
        {"agent": {"default": {"default_agent": "claude"}}},
      )
      assert "system set system.agent=claude" in msg


class TestSuppressedAuthPathIsUnreachable:
  """The E-NULL defect's SECOND symptom, and why it cannot recur.

  A suppressed box installs NO selection level (correctly — ``system.agent`` must
  stay absent), so ``meta.box.auth.workset_path`` = ``{workset.auth.path}/{system.agent}``
  resolves to the COLLAPSED ``<auth>/``. On bifrost that value was live because an
  agent was actually launched; with the launch honest it is INERT, and these pin
  why: no agent ⇒ no capability ⇒ the workset tier is off ⇒ ``resolve_auth_source``
  scrubs the source. The collapsed string never reaches a consumer.
  """

  def _auth(self, *, agent_name, selection_level, support):
    """*support* mirrors what the REAL floor computes.

    ``settings_launch.resolve_inputs`` derives ``meta.agent.<a>.auth.share_support``
    from the resolved target's DESCRIPTOR, and for a no-agent box (``"shell"``
    / blank) there is no target — ``resolve_target("shell")`` raises KeyError
    and the floor records ``False``. Passing ``True`` there would be the test
    lying about the host, and it is exactly what made an earlier draft of this
    test "prove" a collapse that the real path cannot produce.
    """
    from kanibako.settings.settings_launch import (
      auth_chain_floor,
      build_launch_snapshot,
      resolve_auth_source,
    )

    snap = build_launch_snapshot(
      agent_name=agent_name,
      ctx=_ctx(agent_name or None),
      system_path=None,
      agent_path=None,
      workset_path=None,
      box_path=None,
      auth_chain=auth_chain_floor(mode="primary", agent_name=agent_name),
      meta_runtime={"meta.workset.path": "/ws"},
      meta_identity=(
        {f"meta.agent.{agent_name}.auth.share_support": support} if agent_name else {}
      ),
      cli_level=selection_level,
    )
    return snap, resolve_auth_source(snap, mode="primary")

  @pytest.mark.parametrize("agent_name", ["shell", ""])
  def test_the_collapsed_path_never_reaches_a_consumer(self, agent_name):
    """Both no-agent shapes: the LAUNCH's ``"shell"`` slot, and a blank name.

    ⚑ The blank case used to CRASH here — ``{meta.agent..auth.share_support}`` is
    a malformed ref, and the strict ``as_bool`` rejected the leftover string
    ("expected bool, got str"). No caller passed blank, but P7 made ``""`` a
    meaningful value (the suppression), so the floor now pins ``False`` for it.
    """
    snap, auth = self._auth(
      agent_name=agent_name,
      selection_level=None,
      support=False,
    )
    # The RAW key does collapse — that is the embedded-empty rule (spec §0)…
    assert snap.meta.box.auth.workset_path == "/ws/auth/"
    # …but no agent ⇒ no capability ⇒ workset tier OFF ⇒ source scrubbed.
    assert auth.workset_enabled is False
    assert auth.workset_source is None
    assert auth.creds_shared is False

  def test_an_agent_box_still_gets_its_per_agent_dir(self):
    """The DISCRIMINATOR — the scrub above must come from "no agent", not from
    a broken auth chain."""
    _snap, auth = self._auth(
      agent_name="claude",
      selection_level={"system.agent": "claude"},
      support=True,
    )
    assert auth.workset_source == "/ws/auth/claude"
    assert auth.creds_shared is True


def test_selection_retains_identity_without_reparsing_name(tmp_path, monkeypatch):
  from kanibako.agent_ref import AgentIdentity
  from kanibako.settings.agent_select import select_agent

  identity = AgentIdentity(
    node="scout",
    registered_name="Scout",
    persona="ollama",
    harness="codex",
    target_name="codex",
    entry_path=tmp_path / "unrelated-path",
  )
  calls = []

  def resolve(**kwargs):
    calls.append(kwargs)
    return identity

  monkeypatch.setattr("kanibako.settings.config.resolve_agent_identity", resolve)
  selected = select_agent(std=_std(tmp_path), proj=_proj(tmp_path), explicit_agent="Scout")
  assert isinstance(selected, AgentSelection)
  assert selected.identity is identity
  assert selected.node == "scout"
  assert selected.identity.target_name == "codex"
  assert selected.selection_level == {SELECTION_KEY: "scout"}
  assert calls == [{"explicit_agent": "Scout", "requested": None, "registered_persona_out": []}]


def test_selection_node_has_no_independent_mutable_carrier():
  from dataclasses import FrozenInstanceError, fields

  selected = agent_selection(node="shell", source="cli")
  assert "node" not in {field.name for field in fields(selected)}
  assert selected.identity.harness is None
  assert selected.identity.target_name == "shell"
  with pytest.raises(FrozenInstanceError):
    selected.node = "claude"


def test_selection_requires_captured_input_to_own_its_identity():
  from dataclasses import replace

  from kanibako.agent_ref import AgentIdentity
  from kanibako.persona_store import RegisteredPersona

  identity = AgentIdentity("scout", "Scout", "Guide", "claude", "claude", Path("/PG/Scout"))
  registered = RegisteredPersona(identity, Path("/PG/Root"), {}, None, True)
  selected = AgentSelection(identity, "cli", registered)
  assert selected.registered_persona is registered
  with pytest.raises(ValueError, match="does not own"):
    AgentSelection(replace(identity, node="other"), "cli", registered)


class TestSelectedRegisteredPrefs:
  @pytest.fixture
  def public_input(self, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from kanibako import persona_store
    from kanibako.settings import config, config_io, settings_prefs

    std, proj = _std(tmp_path), _proj(tmp_path)
    box, workset = _box_workset(proj)
    document = {
      "agent": {
        "persona": {"name": "Guide"},
        "harness": {
          "name": "claude",
          "config_store": {"env": {"ANTHROPIC_BASE_URL": "https://first.invalid"}},
          "auth_var": "ANTHROPIC_AUTH_TOKEN",
        },
        "secret": {"backend": "synthetic"},
      }
    }
    monkeypatch.setattr(config_io, "persona_store_location", lambda: tmp_path / "pg")
    monkeypatch.setattr(
      config_io, "persona_store_agents", lambda **kw: {"Scout": tmp_path / "entry"}
    )
    read = Mock(return_value=(tmp_path / "entry", document))
    monkeypatch.setattr(config_io, "load_persona_agent", read)
    for owner, name in (
      (config_io, "fetch_persona_secret"),
      (persona_store, "project_registered_persona"),
      (persona_store, "read_persona_bundle"),
    ):
      monkeypatch.setattr(owner, name, Mock(side_effect=AssertionError("unexpected PG operation")))
    classify = Mock(wraps=config.resolve_agent_identity)
    collect = Mock(wraps=settings_prefs.collect_prefs)
    judge = Mock(wraps=settings_prefs.apply_prefs)
    monkeypatch.setattr(config, "resolve_agent_identity", classify)
    monkeypatch.setattr(settings_prefs, "collect_prefs", collect)
    monkeypatch.setattr(settings_prefs, "apply_prefs", judge)
    config_io.dump_doc(std.settings, {"system": {"agent": "Scout"}})
    config_io.dump_doc(box, {"pref": {"agent": {"scout": {"model": "box-model"}}}})
    return SimpleNamespace(
      std=std,
      proj=proj,
      box=box,
      workset=workset,
      read=read,
      classify=classify,
      collect=collect,
      judge=judge,
      document=document,
    )

  def test_selected_pref_validates_after_one_public_classification(self, public_input):
    from kanibako.settings.agent_select import select_agent

    env = public_input
    before = {path: path.read_bytes() for path in (env.std.settings, env.box)}
    selected = select_agent(std=env.std, proj=env.proj)
    assert selected.node == "scout"
    assert selected.identity.target_name == "claude"
    assert selected.registered_persona.identity is selected.identity
    assert selected.registered_persona.store_root == env.std.settings.parents[2] / "pg"
    assert selected.registered_persona.config == env.document["agent"]["harness"]["config_store"]
    env.read.assert_called_once_with("Scout", root=selected.registered_persona.store_root)
    assert env.classify.call_count == env.collect.call_count == env.judge.call_count == 1
    names = env.judge.call_args.kwargs["valid_agents"]
    assert names.leaf_map.agent_harnesses == {"scout": "claude"}
    assert {path: path.read_bytes() for path in before} == before

  def test_separate_operations_capture_fresh_input(self, public_input):
    from copy import deepcopy

    from kanibako.settings.agent_select import select_agent

    env = public_input
    later = deepcopy(env.document)
    later["agent"]["harness"]["config_store"]["env"]["ANTHROPIC_BASE_URL"] = (
      "https://later.invalid"
    )
    env.read.side_effect = [
      (env.std.settings.parent / "first", env.document),
      (env.std.settings.parent / "second", later),
    ]
    first = select_agent(std=env.std, proj=env.proj)
    second = select_agent(std=env.std, proj=env.proj)
    assert first.node == second.node == "scout"
    assert first.registered_persona.config["env"]["ANTHROPIC_BASE_URL"] == "https://first.invalid"
    assert second.registered_persona.config["env"]["ANTHROPIC_BASE_URL"] == "https://later.invalid"
    assert first.registered_persona.identity is first.identity
    assert second.registered_persona.identity is second.identity
    assert env.read.call_count == env.classify.call_count == env.collect.call_count == 2

  @pytest.mark.parametrize("value", [None, "", "another-model"])
  def test_pref_value_states_do_not_change_captured_selection(self, public_input, value):
    from kanibako.settings import config_io
    from kanibako.settings.agent_select import select_agent

    env = public_input
    config_io.dump_doc(env.box, {"pref": {"agent": {"scout": {"model": value}}}})
    selected = select_agent(std=env.std, proj=env.proj)
    assert selected.node == "scout"
    assert env.judge.call_args.args[0][0].value == value
    assert env.read.call_count == env.classify.call_count == env.collect.call_count == 1

  @pytest.mark.writes_undeclared(
    "pref.agent.claude.provider", reason="Intentional foreign-leaf refusal"
  )
  @pytest.mark.parametrize("selection", [None, "", "missing-plugin", "{meta.nope.dangling}"])
  def test_known_pref_refusal_precedes_classification(self, public_input, selection):
    from kanibako.settings import config_io
    from kanibako.settings.agent_select import select_agent

    env = public_input
    config_io.dump_doc(env.std.settings, {"system": {"agent": selection}})
    config_io.dump_doc(env.box, {"pref": {"agent": {"claude": {"provider": "bad"}}}})
    with pytest.raises(SettingsError, match="claude.provider"):
      select_agent(std=env.std, proj=env.proj)
    env.classify.assert_not_called()
    env.read.assert_not_called()

  @pytest.mark.writes_undeclared(
    "pref.agent.claude.provider",
    "agent.claude.provider",
    reason="Intentional ordering counterexample",
  )
  def test_later_known_request_does_not_preempt_unknown_prefix(self, public_input):
    from kanibako.settings.agent_select import select_agent

    env = public_input
    env.box.write_text(
      "pref:\n  agent:\n    scout:\n      provider: first\n    claude:\n      provider: second\n"
    )
    with pytest.raises(SettingsError, match="scout.provider"):
      select_agent(std=env.std, proj=env.proj)
    env.classify.assert_called_once()
    env.read.assert_called_once()

  @pytest.mark.parametrize("node,leaf", [("scout", "provider"), ("inactive", "model")])
  def test_invalid_pref_never_escapes_selection(self, public_input, node, leaf):
    from kanibako.settings import config_io
    from kanibako.settings.agent_select import select_agent

    env = public_input
    config_io.dump_doc(env.box, {"pref": {"agent": {node: {leaf: "invalid"}}}})
    before = env.box.read_bytes()
    with pytest.raises(SettingsError, match=leaf):
      select_agent(std=env.std, proj=env.proj)
    assert env.read.call_count == env.classify.call_count == 1
    assert env.box.read_bytes() == before

  @pytest.mark.parametrize("explicit", [None, "claude"])
  def test_installed_plugin_ignores_colliding_broken_pg(self, public_input, explicit, monkeypatch):
    from unittest.mock import Mock

    from kanibako.settings import config_io
    from kanibako.settings.agent_select import select_agent

    env = public_input
    env.read.side_effect = AssertionError("broken store reached")
    pg = Mock(side_effect=AssertionError("PG classifier reached"))
    monkeypatch.setattr("kanibako.persona_store.read_registered_persona", pg)
    config_io.dump_doc(env.std.settings, {"system": {"agent": "claude"}})
    config_io.dump_doc(env.box, {"pref": {"agent": {"claude": {"model": "own"}}}})
    selected = select_agent(std=env.std, proj=env.proj, explicit_agent=explicit)
    assert selected.node == "claude"
    assert selected.registered_persona is None
    env.read.assert_not_called()
    env.classify.assert_called_once()
    assert env.collect.call_count == (0 if explicit else 1)
    pg.assert_not_called()

  def test_broken_registered_input_keeps_its_typed_error(self, public_input):
    from kanibako.errors import ConfigError
    from kanibako.settings.agent_select import select_agent

    env = public_input
    error = ConfigError("broken captured public input")
    env.read.side_effect = error
    with pytest.raises(ConfigError) as raised:
      select_agent(std=env.std, proj=env.proj)
    assert raised.value is error
    env.read.assert_called_once()
    env.classify.assert_called_once()
    env.judge.assert_not_called()


@pytest.mark.parametrize("transitive", [False, True])
@pytest.mark.parametrize("value", ["goose", None, ""])
def test_selection_keeps_nonselection_pref_dependencies(tmp_path, monkeypatch, transitive, value):
  from kanibako.errors import AgentNoDefaultError, ConfigError
  from kanibako.settings import config_io
  from kanibako.settings.agent_select import select_agent

  std, proj = _std(tmp_path), _proj(tmp_path)
  box, _workset = _box_workset(proj)
  defaults = {"model": "claude"}
  if transitive:
    defaults["env"] = {"CHOICE": "{agent.default.model}"}
  config_io.dump_doc(
    std.settings,
    {
      "system": {"agent": "{agent.default.env.CHOICE}" if transitive else "{agent.default.model}"},
      "agent": {"default": defaults},
    },
  )
  config_io.dump_doc(box, {"pref": {"agent": {"default": {"model": value}}}})
  monkeypatch.setattr(
    "kanibako.persona_store.read_registered_persona", lambda *a, **k: pytest.fail("PG read")
  )
  before = {path: path.read_bytes() for path in (std.settings, box)}
  if value is None:
    with pytest.raises(AgentNoDefaultError):
      select_agent(std=std, proj=proj)
  elif value == "":
    with pytest.raises(ConfigError):
      select_agent(std=std, proj=proj)
  else:
    assert select_agent(std=std, proj=proj).node == "goose"
  assert {path: path.read_bytes() for path in before} == before


@pytest.mark.usefixtures("config_file")
def test_invalid_selected_pref_refuses_before_create_journal(std, tmp_home, monkeypatch):
  from unittest.mock import Mock

  from kanibako.commands import start
  from kanibako.commands.box import _parser as box
  from kanibako.settings import config_io
  from kanibako.settings.config import WORKSET_META_FILE
  from tests.test_commands.test_create_identity import _create_args, _public_persona

  config_io.dump_doc(std.settings, {"system": {"agent": "Scout"}})
  config_io.dump_doc(
    std.primary_workset / WORKSET_META_FILE, {"pref": {"agent": {"scout": {"provider": "bad"}}}}
  )
  monkeypatch.setattr(config_io, "persona_store_location", lambda: tmp_home / "pg")
  monkeypatch.setattr(
    config_io, "persona_store_agents", lambda **kw: {"Scout": tmp_home / "entry"}
  )
  read = Mock(return_value=(tmp_home / "entry", _public_persona()))
  monkeypatch.setattr(config_io, "load_persona_agent", read)
  claim = Mock(side_effect=AssertionError("journal reached"))
  materialize = Mock(side_effect=AssertionError("materialization reached"))
  seed = Mock(side_effect=AssertionError("seed reached"))
  monkeypatch.setattr(box, "_claim_create_entry", claim)
  monkeypatch.setattr(box, "_new_box_undo", materialize)
  monkeypatch.setattr(start, "seed_new_box", seed)
  path = tmp_home / "new-parent" / "box"
  with pytest.raises(SettingsError, match="provider"):
    box.run_create(_create_args(path, agent=None))
  assert read.call_count == 1
  claim.assert_not_called()
  materialize.assert_not_called()
  seed.assert_not_called()
  assert not path.parent.exists()
  assert not std.journal.exists()

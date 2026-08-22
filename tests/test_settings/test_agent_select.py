"""Agent SELECTION — the P7 seam (spec §1A / §2b / §2g / §2h).

The phase that changes WHICH AGENT RUNS, so every test here is written as a
discriminator: each names the mutation it reddens under.

Covers:

* the selection ORDER — ``system.agent`` < workset pref < box pref < ``--agent``;
* the THREE-state read (name / present-``None`` suppression / absent), because the
  NO-AGENT box (D-M6) and "nothing was ever set" are DIFFERENT answers;
* the §1A SELECTION LEVEL that keeps ``@system.agent`` equal to the node that runs;
* the RETIRED-key refusals (``box.agent_name`` / ``system.default_agent``, M-4).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.settings.agent_select import AgentSelection, SELECTION_KEY
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.settings_assemble import refuse_retired_keys
from kanibako.settings.settings_launch import build_launch_snapshot, resolve_selected_agent
from kanibako.settings.settings_resolve import ResolveCtx, SettingsError

AGENTS = frozenset({"claude", "goose", "codex"})


def _ctx(agent_name: str | None = None) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent_name,
        workset_name=None,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


def _yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False))
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
        assert _select(
            tmp_path,
            system={"system": {"agent": "claude"}},
            workset={"pref": {"system": {"agent": "goose"}}},
        ) == "goose"

    def test_a_box_pref_beats_a_workset_pref(self, tmp_path):
        """§1A — box beats workset by ASSIGNMENT ORDER.
        INVERT: swap the two overlays' splice positions -> this reddens."""
        assert _select(
            tmp_path,
            system={"system": {"agent": "claude"}},
            workset={"pref": {"system": {"agent": "goose"}}},
            box={"pref": {"system": {"agent": "codex"}}},
        ) == "codex"

    def test_a_box_file_cannot_set_system_agent_directly(self, tmp_path):
        """An upward write is DROPPED at assembly (§0 directional enforcement) —
        the pref is the only channel. INVERT: keep a box file's ``system:`` table
        and the box could silently repoint a system-scope key."""
        assert _select(
            tmp_path,
            system={"system": {"agent": "claude"}},
            box={"system": {"agent": "goose"}},
        ) == "claude"


# --------------------------------------------------------------------------- #
# The THREE-state read — the NO-AGENT box is NOT "unset" (D-M6)                #
# --------------------------------------------------------------------------- #


class TestSelectionThreeState:
    def test_absent_everywhere_is_MISSING_not_none(self, tmp_path):
        """``__MISSING__`` (nothing set) must stay distinguishable from ``None``
        (explicitly suppressed): the first falls through to the installed-count
        rule, the second is a NO-AGENT box."""
        assert _select(tmp_path) is __MISSING__

    def test_a_null_pref_is_KEPT_as_present_none(self, tmp_path):
        """⚑ The D-M6 capability GAIN, and the silent-failure hazard §2h names.

        ``pref.system.agent: null`` installs present-``None`` VERBATIM, and
        present-``None`` on a SCALAR leaf is KEPT (``_resolve_present_none``), so a
        box can opt OUT of an agent even while a system default is set — which
        the retired ``box.agent_name`` could not express.

        INVERT: ``if value is None: continue`` anywhere on the pref/selection path
        (the most natural guard to write) -> this returns ``__MISSING__`` and the box
        silently launches the system default instead of a plain shell.
        """
        got = _select(
            tmp_path,
            system={"system": {"agent": "claude"}},
            box={"pref": {"system": {"agent": None}}},
        )
        assert got is None
        assert got is not __MISSING__

    def test_a_workset_null_pref_suppresses_for_its_boxes(self, tmp_path):
        assert _select(
            tmp_path,
            system={"system": {"agent": "claude"}},
            workset={"pref": {"system": {"agent": None}}},
        ) is None


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
                box={"pref": {"system": {"agent": "@meta.nope.dangling"}}},
            )
        assert SELECTION_KEY in str(ei.value)

    def test_an_unrelated_defect_does_not_decide_which_agent_runs(self, tmp_path):
        """⚑ WHY THE SELECTION PASS EXPANDS LENIENTLY.

        This pass has no active agent yet, so ``$AGENT`` cannot resolve. A
        perfectly legitimate ``$AGENT`` in an UNRELATED value must not abort
        selection for a box whose real launch resolves it fine.

        INVERT: use strict ``expand`` here -> SettingsError, and a box with any
        ``$AGENT``-bearing setting can no longer start at all.
        """
        assert _select(
            tmp_path,
            system={
                "system": {"agent": "claude", "cache": "/c/$AGENT"},
            },
        ) == "claude"


# --------------------------------------------------------------------------- #
# The §1A SELECTION LEVEL — @system.agent equals the node that RUNS            #
# --------------------------------------------------------------------------- #


class TestSelectionLevel:
    def test_the_selection_level_outranks_a_contrary_pref(self, tmp_path):
        """⚑ THE F2 INCOHERENCE, CLOSED.

        ``--agent claude`` with ``pref.system.agent: goose`` must not leave the
        snapshot saying goose while claude runs — three consumers dereference
        ``@system.agent`` (both re-pointed §2c anchors, and C-CANON's agent
        chapter next).

        INVERT: drop the ``selection_level`` splice -> the snapshot says goose.
        """
        box = _yaml(
            tmp_path / "box.yaml", {"pref": {"system": {"agent": "goose"}}},
        )
        snap = build_launch_snapshot(
            agent_name="claude", ctx=_ctx("claude"),
            system_path=None, agent_path=None, workset_path=None, box_path=box,
            valid_agents=AGENTS,
            cli_level={"system.agent": "claude"},
        )
        assert snap.system.agent == "claude"

    def test_without_the_selection_level_the_pref_would_win(self, tmp_path):
        """The MEASURED baseline for the test above — this is what the launch
        would report if the level were dropped."""
        box = _yaml(
            tmp_path / "box.yaml", {"pref": {"system": {"agent": "goose"}}},
        )
        snap = build_launch_snapshot(
            agent_name="claude", ctx=_ctx("claude"),
            system_path=None, agent_path=None, workset_path=None, box_path=box,
            valid_agents=AGENTS,
        )
        assert snap.system.agent == "goose"

    def test_the_selection_level_supplies_an_autopicked_agent(self, tmp_path):
        """⚑ The THIRD incoherence (not just ``--agent``): on the commonest host —
        ONE agent installed, nothing stored — the box runs claude while
        ``system.agent`` is ABSENT, so every ``@system.agent`` dereference would
        coerce to ``""``. Installing the resolved selection ALWAYS covers it."""
        snap = build_launch_snapshot(
            agent_name="claude", ctx=_ctx("claude"),
            system_path=None, agent_path=None, workset_path=None, box_path=None,
            valid_agents=AGENTS,
            cli_level={"system.agent": "claude"},
        )
        assert snap.system.agent == "claude"

    def test_a_no_agent_box_installs_nothing(self):
        """A suppressed box must leave ``system.agent`` absent — pinning it to the
        ``general`` template slot would make the box look agent-bearing."""
        assert AgentSelection(node="", source="suppressed").selection_level is None
        assert AgentSelection(node="claude", source="cli").selection_level == {
            "system.agent": "claude",
        }

    def test_the_workset_auth_anchor_follows_the_selection(self, tmp_path):
        """The re-pointed §2c anchor resolves through ``@system.agent``, so a
        ``--agent`` launch must land in THAT agent's credential dir.

        INVERT: interpolate the agent name into the anchor again and this passes
        for the wrong reason (it would follow ``agent_name``, not the key).
        """
        from kanibako.settings.settings_launch import auth_chain_floor

        box = _yaml(
            tmp_path / "box.yaml", {"pref": {"system": {"agent": "goose"}}},
        )
        snap = build_launch_snapshot(
            agent_name="claude", ctx=_ctx("claude"),
            system_path=None, agent_path=None, workset_path=None, box_path=box,
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
            refuse_retired_keys(yaml.safe_load(f.read_text()), level="box", path=f)
        msg = str(ei.value)
        assert "box.agent_name" in msg
        assert "RETIRED" in msg
        assert "RULE CHANGED" in msg          # it is not "your config is wrong"
        assert "pref.system.agent=goose" in msg
        assert str(f) in msg                  # names the FILE

    def test_box_level_cure_interpolates_the_box_argument(self, tmp_path):
        """Jei's own ruling: ``box set`` needs the box positional to be copy-pasteable
        from outside the box's own cwd — the caller supplies it via *box_name*."""
        f = _yaml(tmp_path / "box.yaml", {"box": {"agent_name": "goose"}})
        with pytest.raises(SettingsError) as ei:
            refuse_retired_keys(
                yaml.safe_load(f.read_text()), level="box", path=f, box_name="myproj",
            )
        msg = str(ei.value)
        assert "kanibako box set myproj pref.system.agent=goose" in msg
        assert "kanibako box set myproj --null pref.system.agent" in msg

    def test_box_name_is_ignored_off_the_box_level(self, tmp_path):
        """No SINGLE box is being refused for at workset/system/base/agent scope, so a
        *box_name* passed in anyway must not leak into the cure."""
        f = _yaml(tmp_path / "workset.yaml", {"box": {"agent_name": "goose"}})
        with pytest.raises(SettingsError) as ei:
            refuse_retired_keys(
                yaml.safe_load(f.read_text()), level="workset", path=f, box_name="myproj",
            )
        msg = str(ei.value)
        assert "myproj" not in msg

    def test_system_default_agent_is_refused_with_the_cure(self, tmp_path):
        f = _yaml(
            tmp_path / "system.yaml",
            {"agent": {"default": {"default_agent": "claude"}}},
        )
        with pytest.raises(SettingsError) as ei:
            refuse_retired_keys(yaml.safe_load(f.read_text()), level="system", path=f)
        msg = str(ei.value)
        assert "system.default_agent" in msg
        assert "system set system.agent=claude" in msg

    def test_a_clean_file_passes(self, tmp_path):
        f = _yaml(tmp_path / "box.yaml", {"pref": {"system": {"agent": "goose"}}})
        refuse_retired_keys(yaml.safe_load(f.read_text()), level="box", path=f)

    def test_an_unrelated_agent_default_table_is_not_refused(self, tmp_path):
        """SCOPE IS TIGHT: only the two retired spellings. A normal
        ``agent.default.model`` must be untouched."""
        f = _yaml(tmp_path / "system.yaml", {"agent": {"default": {"model": "opus"}}})
        refuse_retired_keys(yaml.safe_load(f.read_text()), level="system", path=f)


# --------------------------------------------------------------------------- #
# END-TO-END through select_agent — the SEAM, not the helper                   #
# --------------------------------------------------------------------------- #
#
# ⚑ The tests above call ``refuse_retired_keys`` / ``resolve_selected_agent``
# DIRECTLY, so deleting the refusal LOOP from ``select_agent`` would redden none of
# them — and ``tests/conftest.py``'s ``start_mocks`` patches ``select_agent``
# wholesale, so the launch tests cannot catch it either. These drive the real seam.


class _FakeGroup:
    is_default = True
    name = "__PRIMARY__"

    def __init__(self, root):
        self.root = root


def _proj(tmp_path, *, name: "str | None" = "myproj"):
    """The minimal ``ProjectPaths`` shape ``select_agent`` reads.

    It needs exactly: ``mode`` / ``metadata_path`` / ``group`` (for
    ``box_workset_settings_paths``), ``project_path`` (passed to the
    installed-set probe) and ``name`` (threaded to the box-level retired-key
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
        project_path=tmp_path / "proj",
        name=name,
    )


def _std(tmp_path):
    from types import SimpleNamespace

    data = tmp_path / "data"
    (data / "global").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        settings=data / "global" / "settings.yaml",
        data=data,
        data_home=data,
        agents=data / "agents",
        registry=data / "global" / "registry.yaml",
        primary_workset=tmp_path / "ws",
    )


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
        std.settings.write_text(yaml.safe_dump({"system": {"agent": "claude"}}))
        # The RETIRED spelling, in the box tier.
        box_file, _ws = _box_workset(proj)
        box_file.write_text(yaml.safe_dump({"box": {"agent_name": "goose"}}))

        with pytest.raises(SettingsError) as ei:
            select_agent(std=std, proj=proj, explicit_agent=None)
        msg = str(ei.value)
        assert "box.agent_name" in msg
        assert "pref.system.agent=goose" in msg
        assert str(box_file) in msg

    def test_retired_system_default_agent_is_refused(self, tmp_path, monkeypatch):
        from kanibako.settings.agent_select import select_agent

        monkeypatch.setattr(
            "kanibako.targets.discover_targets", lambda *a, **k: {"claude": object},
        )
        std, proj = _std(tmp_path), _proj(tmp_path)
        std.settings.write_text(
            yaml.safe_dump({"agent": {"default": {"default_agent": "claude"}}}),
        )
        with pytest.raises(SettingsError) as ei:
            select_agent(std=std, proj=proj, explicit_agent=None)
        assert "system.default_agent" in str(ei.value)

    def test_a_present_null_retired_key_is_also_refused(self, tmp_path, monkeypatch):
        """A ``box: {agent_name: null}`` leaf is STILL the retired key — a
        3-state-aware check must not conflate PRESENT-null with ABSENT (the same
        conflation §2h warns about for prefs). MUTATION: probe with ``is None``
        and this reddens."""
        from kanibako.settings.agent_select import select_agent

        monkeypatch.setattr(
            "kanibako.targets.discover_targets", lambda *a, **k: {"claude": object},
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
        std.settings.write_text(yaml.safe_dump({"system": {"agent": "claude"}}))
        box_file, _ws = _box_workset(proj)
        box_file.write_text(yaml.safe_dump({"pref": {"system": {"agent": "goose"}}}))

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
        box_file.write_text(yaml.safe_dump({"pref": {"system": {"agent": "goose"}}}))
        sel = select_agent(std=std, proj=proj, explicit_agent="claude")
        assert (sel.node, sel.source) == ("claude", "cli")

    def test_a_null_request_gives_a_no_agent_box_at_the_seam(
        self, tmp_path, monkeypatch,
    ):
        from kanibako.settings.agent_select import select_agent

        monkeypatch.setattr(
            "kanibako.targets.discover_targets", lambda *a, **k: {"claude": object},
        )
        std, proj = _std(tmp_path), _proj(tmp_path)
        std.settings.write_text(yaml.safe_dump({"system": {"agent": "claude"}}))
        box_file, _ws = _box_workset(proj)
        box_file.write_text("pref:\n  system:\n    agent:\n")
        sel = select_agent(std=std, proj=proj, explicit_agent=None)
        assert (sel.node, sel.source) == ("", "suppressed")
        assert sel.selection_level is None

    def test_autopick_reports_its_source(self, tmp_path, monkeypatch):
        from kanibako.settings.agent_select import select_agent

        monkeypatch.setattr(
            "kanibako.targets.discover_targets", lambda *a, **k: {"claude": object},
        )
        std, proj = _std(tmp_path), _proj(tmp_path)
        sel = select_agent(std=std, proj=proj, explicit_agent=None)
        assert (sel.node, sel.source) == ("claude", "autopick")


# --------------------------------------------------------------------------- #
# The BOX ARGUMENT in the cure — Jei's ruling, through the REAL seam           #
# --------------------------------------------------------------------------- #
#
# ⚑ ``TestRetiredKeyCureIsLevelAppropriate`` (and ``settings_assemble``'s own
# mutation-proved unit tests) drove ``_retired_key_cure`` / ``refuse_retired_keys``
# DIRECTLY — that machinery is correct but was NOT WIRED: the production caller,
# ``select_agent``, never passed ``box_name``, so the user-visible cure line stayed
# the bare form regardless. These three drive ``select_agent`` itself, so deleting
# the ``box_name=proj.name if level == "box" else None`` splice from its refusal
# loop reddens them even though every test above stays green.


class TestSelectAgentSeamBoxArgument:
    def test_a_named_box_gets_the_box_argument_in_the_cure(self, tmp_path, monkeypatch):
        from kanibako.settings.agent_select import select_agent

        monkeypatch.setattr(
            "kanibako.targets.discover_targets",
            lambda *a, **k: {"claude": object, "goose": object},
        )
        std, proj = _std(tmp_path), _proj(tmp_path, name="myproj")
        box_file, _ws = _box_workset(proj)
        box_file.write_text(yaml.safe_dump({"box": {"agent_name": "goose"}}))
        with pytest.raises(SettingsError) as ei:
            select_agent(std=std, proj=proj, explicit_agent=None)
        msg = str(ei.value)
        assert "kanibako box set myproj pref.system.agent=goose" in msg
        assert "kanibako box set myproj --null pref.system.agent" in msg

    def test_a_nameless_box_falls_back_to_the_subject_PLACEHOLDER(
        self, tmp_path, monkeypatch,
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
        box_file.write_text(yaml.safe_dump({"box": {"agent_name": "goose"}}))
        with pytest.raises(SettingsError) as ei:
            select_agent(std=std, proj=proj, explicit_agent=None)
        msg = str(ei.value)
        assert "kanibako box set <box> pref.system.agent=goose" in msg
        assert "kanibako box set <box> --null pref.system.agent" in msg
        assert "kanibako box set  pref.system.agent=goose" not in msg  # no doubled space
        assert "None" not in msg

    def test_a_non_box_level_refusal_never_leaks_a_box_argument(self, tmp_path, monkeypatch):
        """``system.default_agent`` is a SYSTEM-tier refusal — a named box in scope
        must not leak into a cure that names no single box."""
        from kanibako.settings.agent_select import select_agent

        monkeypatch.setattr(
            "kanibako.targets.discover_targets", lambda *a, **k: {"claude": object},
        )
        std, proj = _std(tmp_path), _proj(tmp_path, name="myproj")
        std.settings.write_text(
            yaml.safe_dump({"agent": {"default": {"default_agent": "claude"}}}),
        )
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
            refuse_retired_keys(yaml.safe_load(f.read_text()), level=level, path=f)
        return str(ei.value)

    def test_box_and_workset_get_the_pref_cure(self, tmp_path):
        """⚑ THE VERB IS THE LEVEL. A workset file's cure is ``workset set``, not
        ``box set`` — the levels share the §2h permission, never the command."""
        for level in ("box", "workset"):
            msg = self._msg(tmp_path, level, {"box": {"agent_name": "goose"}})
            assert f"kanibako {level} set <{level}> pref.system.agent=goose" in msg
            assert "no-agent box" in msg

    def test_the_workset_cure_names_the_workset_verb_AND_a_subject(self, tmp_path):
        """DEFECT: the scalar cure hardcoded ``box set`` at BOTH pref-legal levels,
        so a workset file was handed the wrong verb with no subject at all —
        ``kanibako box set pref.system.agent=goose``, which parses and writes to a
        BOX. The sibling ``_retired_mirror_cure`` never had the bug; this pins the
        two functions to the same shape.

        INVERT: hardcode ``box`` in ``_retired_key_cure`` again and this reddens.
        """
        msg = self._msg(tmp_path, "workset", {"box": {"agent_name": "goose"}})
        cure = msg.split("Fix: ", 1)[1].splitlines()[0].strip()
        assert cure.startswith("kanibako workset set <workset> pref.system.agent=goose")
        assert "kanibako workset set <workset> --null pref.system.agent" in cure
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

    def test_the_system_default_cure_is_level_independent(self, tmp_path):
        for level in ("system", "box"):
            msg = self._msg(
                tmp_path, level, {"agent": {"default": {"default_agent": "claude"}}},
            )
            assert "system set system.agent=claude" in msg


class TestNoAgentAuthPathIsUnreachable:
    """The E-NULL defect's SECOND symptom, and why it cannot recur.

    A suppressed box installs NO selection level (correctly — ``system.agent`` must
    stay absent), so ``meta.box.auth.workset_path`` = ``@workset.auth.path/@system.agent``
    resolves to the COLLAPSED ``<auth>/``. On bifrost that value was live because an
    agent was actually launched; with the launch honest it is INERT, and these pin
    why: no agent ⇒ no capability ⇒ the workset tier is off ⇒ ``resolve_auth_source``
    scrubs the source. The collapsed string never reaches a consumer.
    """

    def _auth(self, *, agent_name, selection_level, support):
        """*support* mirrors what the REAL floor computes.

        ``_launch_snapshot_inputs`` derives ``meta.agent.<a>.auth.share_support``
        from the resolved target's DESCRIPTOR, and for a no-agent box (``"general"``
        / blank) there is no target — ``resolve_target("general")`` raises KeyError
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
            agent_name=agent_name, ctx=_ctx(agent_name or None),
            system_path=None, agent_path=None, workset_path=None, box_path=None,
            auth_chain=auth_chain_floor(mode="primary", agent_name=agent_name),
            meta_runtime={"meta.workset.path": "/ws"},
            meta_identity=(
                {f"meta.agent.{agent_name}.auth.share_support": support}
                if agent_name else {}
            ),
            cli_level=selection_level,
        )
        return snap, resolve_auth_source(snap, mode="primary")

    @pytest.mark.parametrize("agent_name", ["general", ""])
    def test_the_collapsed_path_never_reaches_a_consumer(self, agent_name):
        """Both no-agent shapes: the LAUNCH's ``"general"`` slot, and a blank name.

        ⚑ The blank case used to CRASH here — ``@meta.agent..auth.share_support`` is
        a malformed ref, and the strict ``as_bool`` rejected the leftover string
        ("expected bool, got str"). No caller passed blank, but P7 made ``""`` a
        meaningful value (the suppression), so the floor now pins ``False`` for it.
        """
        snap, auth = self._auth(
            agent_name=agent_name, selection_level=None, support=False,
        )
        # The RAW key does collapse — that is the §6b embedded-empty rule…
        assert snap.meta.box.auth.workset_path == "/ws/auth/"
        # …but no agent ⇒ no capability ⇒ workset tier OFF ⇒ source scrubbed.
        assert auth.workset_enabled is False
        assert auth.workset_source is None
        assert auth.creds_shared is False

    def test_an_agent_box_still_gets_its_per_agent_dir(self):
        """The DISCRIMINATOR — the scrub above must come from "no agent", not from
        a broken auth chain."""
        _snap, auth = self._auth(
            agent_name="claude", selection_level={"system.agent": "claude"},
            support=True,
        )
        assert auth.workset_source == "/ws/auth/claude"
        assert auth.creds_shared is True

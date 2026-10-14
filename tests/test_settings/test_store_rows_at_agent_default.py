"""R1 for the agent STORE rows: core's ``template`` / ``canon`` / ``seeded`` live at ``agent.default``.

Keyspec §2d *Default settings*: the core seeds every ``agent.default`` value first, the
agent's own values go on top, and the user re-overrides with ``agent.<agent>.<key>``. Core
used to write these three per NODE at ``agent.<id>``, the slot the §2d pick reads first,
so a user's ``agent.default`` value never reached a true agent.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from kanibako.launch.templates import agent_template_defaults, template_seed_defaults
from kanibako.settings.core_defaults import canon_default_categories
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.paths import BoxMode
from kanibako.settings.settings_keyspace import key_validity
from kanibako.settings.settings_launch import build_launch_snapshot, snapshot_leaf
from kanibako.settings.settings_prefs import AgentNames
from kanibako.settings.settings_resolve import ResolveCtx

_AGENTS = AgentNames({"claude"}, leaf_map={"claude": frozenset()})


def _snap(tmp_path: Path, system: dict, floor: dict):
    sys_p = tmp_path / "system.yaml"
    sys_p.write_text(yaml.safe_dump(system))
    return build_launch_snapshot(
        agent_name="claude",
        ctx=ResolveCtx(
            agent_name="claude", workset_name=None, host_home="/home/host",
            xdg={"XDG_DATA_HOME": "/data"}, config={"config.agents": "/agents"},
        ),
        system_path=sys_p, agent_path=None, workset_path=None, box_path=None,
        default_categories=floor, valid_agents=_AGENTS,
    )


def _seed_floor() -> dict:
    proj = SimpleNamespace(mode=BoxMode.standalone)
    return template_seed_defaults(proj, "claude")  # type: ignore[arg-type]


def _seed_src(snap) -> str:
    """The layer-2 seed source the §2d pick hands the collapse, expanded."""
    for tier in ("claude", "default"):
        node = snapshot_leaf(snap, f"agent.{tier}.seeded")
        if node is not __MISSING__ and node is not None:
            entry = dict.get(node, "~/", __MISSING__)
            if entry is __MISSING__:
                entry = dict.get(node, "/home/agent", __MISSING__)
            if entry is not __MISSING__:
                return str(getattr(entry, "src", entry))
    raise AssertionError("no layer-2 seed entry")


def test_a_user_agent_default_template_reaches_a_true_agent(tmp_path):
    snap = _snap(
        tmp_path, {"agent": {"default": {"template": "/USER/tpl"}}}, _seed_floor(),
    )
    assert snapshot_leaf(snap, "meta.box.agent.template") == "/USER/tpl"
    assert snapshot_leaf(snap, "agent.claude.template") == "/USER/tpl"
    assert _seed_src(snap) == "/USER/tpl/box/home"


def test_the_agents_own_template_still_beats_agent_default(tmp_path):
    snap = _snap(tmp_path, {"agent": {
        "default": {"template": "/USER/tpl"}, "claude": {"template": "/CLAUDE/tpl"},
    }}, _seed_floor())
    assert snapshot_leaf(snap, "meta.box.agent.template") == "/CLAUDE/tpl"
    assert _seed_src(snap) == "/CLAUDE/tpl/box/home"


def test_with_no_user_value_the_node_store_still_seeds(tmp_path):
    snap = _snap(tmp_path, {}, _seed_floor())
    assert snapshot_leaf(snap, "meta.box.agent.template") == "/agents/claude/template"
    assert _seed_src(snap) == "/agents/claude/template/box/home"


def test_core_writes_the_store_rows_at_agent_default():
    defs = agent_template_defaults("claude")
    assert defs["agent.default.template"] == "{config.agents}/claude/template"
    assert defs["agent.claude.template"] == "{agent.default.template}"


def test_a_user_agent_default_canon_reaches_an_agent_with_its_own_canon(tmp_path):
    """The case that used to lose the user's value: the node's own ``canon/`` exists."""
    (tmp_path / "agents" / "claude" / "canon").mkdir(parents=True)
    floor = canon_default_categories(SimpleNamespace(agents=tmp_path / "agents"), "claude")  # type: ignore[arg-type]
    assert floor["agent.default.canon"] == "{config.agents}/claude/canon"
    snap = _snap(tmp_path, {"agent": {"default": {"canon": "/USER/canon"}}}, floor)
    assert snapshot_leaf(snap, "meta.box.agent.canon") == "/USER/canon"


def test_without_a_user_value_an_own_canon_dir_is_still_bound(tmp_path):
    (tmp_path / "agents" / "claude" / "canon").mkdir(parents=True)
    floor = canon_default_categories(SimpleNamespace(agents=tmp_path / "agents"), "claude")  # type: ignore[arg-type]
    snap = _snap(tmp_path, {}, floor)
    assert snapshot_leaf(snap, "meta.box.agent.canon") == "/agents/claude/canon"


def test_the_default_pseudo_agent_meta_rows_are_retired():
    """His spec declares neither ``meta.agent.default.name`` nor ``.path``."""
    assert key_validity("meta.agent.default.name", valid_agents={"claude"}) is not None
    assert key_validity("meta.agent.default.path", valid_agents={"claude"}) is not None

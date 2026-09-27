"""Plugin-declared BEHAVIOR defaults live in the shipped YAML, not in plugin code.

DEFAULTS-1 D1-7 (the owner's ruling, *"nothing declared in plugin CODE"*): the floor
value of every ``agent.<agent>.<key>`` behavior key — the models, goose's provider, the
endpoints, claude's transform — used to be a ``default=`` literal inside each plugin's
``setting_descriptors()``.  It is a
``behavior:`` row in ``<agent>-defaults.yaml`` now, and ``setting_descriptors()``
returns what the loader read.

⚑ Sibling of ``test_agent_envs.py``, which did the same for the plugins' env literals.
The RULE (what the loader accepts and refuses) is pinned over synthetic files in
``tests/test_settings/test_agent_defaults.py::TestLoadBehavior``; what THIS file pins is
the SHIPPED tables and the absence of a second declaration site.

⚑ These are the values the launch floors on: ``start.py`` places
``descriptor_floor(target.setting_descriptors())`` at ``agent.<active>`` (above the core
§2d backstop at ``agent.default``), so a wrong row here is a wrong floor for every box of
that agent.  A row that sets no value (``UNSET``, Q105) is left out of that floor.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kanibako.commands.start import _LaunchRealizer
from kanibako.plugins.claude import ClaudeTarget
from kanibako.plugins.codex import CodexTarget
from kanibako.plugins.goose import GooseTarget
from kanibako.settings import core_defaults
from kanibako.settings.settings_launch import build_launch_snapshot, effective_behavior
from kanibako.settings.settings_resolve import UNSET, ResolveCtx
from kanibako.targets.assembly import assemble_argv
from kanibako.targets.base import descriptor_floor

_TARGETS = {
    "claude": ClaudeTarget,
    "codex": CodexTarget,
    "goose": GooseTarget,
}

#: The SHIPPED floor, key → default, in declaration order.  Changing one of these is a
#: change to what every box of that agent runs at when the user has set nothing.
#: ⚑ ``label`` LEADS EVERY ROW SINCE D8b (2026-09-15).  The agent's human-readable
#: description is a declared §2d key now, and its floor is the plugin's own declaration —
#: it is what ``kanibako agent info`` prints.  Drop a plugin's row and that agent reads the
#: all-agents backstop, ``Agent Description (None)``, which is the display regression
#: shipping these three rows exists to prevent.
#: ⚑ ``UNSET`` = the row sets no value and the key inherits ``agent.default`` (Q105).
_SHIPPED: dict[str, list[tuple[str, object]]] = {
    "claude": [
        ("label", "Claude Code"), ("model", UNSET), ("endpoint", UNSET),
        ("transform", "tweakcc"),
    ],
    "codex": [("label", "Codex CLI"), ("model", UNSET), ("endpoint", UNSET)],
    "goose": [
        ("label", "Goose Harness"), ("provider", None), ("model", UNSET),
        ("endpoint", UNSET),
    ],
}


def _module_source(agent: str) -> ast.Module:
    """Parse the plugin's ``target.py`` — the module that must carry no literal."""
    module = __import__(f"kanibako.plugins.{agent}.target", fromlist=["target"])
    return ast.parse(Path(module.__file__).read_text())


@pytest.mark.parametrize("agent", sorted(_SHIPPED))
def test_a_plugin_behavior_default_lives_in_the_yaml_not_the_code(agent: str) -> None:
    """No plugin module CONSTRUCTS a ``TargetSetting`` any more.

    The loader is the only builder, so a floor value cannot be reintroduced beside
    the file that already declares it.  ⚑ Checked over the WHOLE module, not just
    ``setting_descriptors``: a helper that assembled the list somewhere else would be
    the same second declaration site.

    (Mutation: restore ``TargetSetting(key="model", …, default="opus")`` in claude's
    ``setting_descriptors`` → RED here, by name.)
    """
    built = [
        node for node in ast.walk(_module_source(agent))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TargetSetting"
    ]
    assert built == [], (
        f"{agent}/target.py constructs {len(built)} TargetSetting(s) in code; "
        f"behavior keys and their floors are declared in {agent}-defaults.yaml's "
        f"'behavior:' section (D1-7)"
    )


@pytest.mark.parametrize("agent", sorted(_SHIPPED))
def test_setting_descriptors_carries_no_default_keyword(agent: str) -> None:
    """The narrower half of the rule, stated where a reader will look for it.

    ``setting_descriptors`` passes NO ``default=`` to anything — including a helper
    or a dataclass ``replace`` that the construction check above would not catch.
    """
    (fn,) = [
        node for node in ast.walk(_module_source(agent))
        if isinstance(node, ast.FunctionDef) and node.name == "setting_descriptors"
    ]
    defaults = [
        kw for call in ast.walk(fn)
        if isinstance(call, ast.Call)
        for kw in call.keywords
        if kw.arg == "default"
    ]
    assert defaults == [], (
        f"{agent}/target.py's setting_descriptors passes a 'default=' — the floor "
        f"is declared in {agent}-defaults.yaml (D1-7)"
    )


@pytest.mark.parametrize("agent", sorted(_SHIPPED))
def test_the_shipped_floor_is_what_the_yaml_declares(agent: str) -> None:
    """The floor the launch reads, key and value, in declaration order."""
    got = [(d.key, d.default) for d in _TARGETS[agent]().setting_descriptors()]
    assert got == _SHIPPED[agent]


@pytest.mark.parametrize("agent", sorted(_SHIPPED))
def test_every_shipped_behavior_row_describes_itself(agent: str) -> None:
    """``kanibako config`` shows the description; a blank one is a defect."""
    for d in _TARGETS[agent]().setting_descriptors():
        assert d.description.strip(), f"{agent}.{d.key} has no description"


@pytest.mark.parametrize("agent", sorted(_TARGETS))
def test_no_shipped_plugin_imposes_a_model(agent: str) -> None:
    """KANIBAKO IMPOSES NO MODEL — every plugin's ``model`` row sets no value.

    Spec §2d: ``agent.<agent>.model`` has *"no plugin default — inherits
    agent.default.model"*, whose ``<None>`` means the harness's built-in default.  So
    the row is ``UNSET``: it stays out of the plugin floor, and a user's
    ``agent.default.model`` reaches the agent.

    ⚑ THIS IS THE RULE, NOT A VALUE.  ``_SHIPPED`` above is an inventory: it reds
    when a listed row changes, but it can say nothing about a plugin nobody added to
    it.  This one reds for ANY model default, ``<None>`` included, on ANY shipped
    plugin.

    ⚑ The corpus is ``_TARGETS`` — three classes imported BY NAME at module scope —
    and not the plugin discovery registry, on purpose: a discovery-derived corpus
    can come back empty (nothing installed) and pass vacuously, whereas a missing
    import here reds at collection (P15).

    ⚑ The key must stay DECLARED, which is why this asserts an ``UNSET`` value and
    not an ABSENT row: the row carries the key's description for ``config``.

    (Mutation: put ``default: opus`` back on claude's ``behavior:`` model row → RED
    here by name; delete the row entirely → RED here on the declared-key assert.)
    """
    floors = {d.key: d.default for d in _TARGETS[agent]().setting_descriptors()}
    assert "model" in floors, (
        f"{agent} declares no 'model' behavior row; the key stays DECLARED so "
        f"'config' can describe it"
    )
    assert floors["model"] is UNSET, (
        f"{agent} ships a model default {floors['model']!r}; spec §2d gives "
        f"agent.<agent>.model no plugin default — it inherits agent.default.model"
    )


def test_goose_pins_no_provider() -> None:
    """goose's ``provider`` floor is ``<None>``, and that is load-bearing.

    A ``<None>`` floor is omitted by the launch — so goose's own ``config.yaml``
    (from ``goose configure``, persisted by the home bind) keeps owning the provider,
    exactly as on the host.  Any non-null value here would override the user's own config on EVERY launch (spec §2d, the goose
    section: kanibako *"does NOT impose or pre-declare provider/model"*).

    The key stays DECLARED, so an explicit ``agent.goose.provider`` still wins the
    cascade and IS emitted.  ⚑ ``model`` is not asserted here any more: it is no
    longer a goose peculiarity but the ALL-AGENTS rule above.
    """
    floors = {d.key: d.default for d in GooseTarget().setting_descriptors()}
    assert floors["provider"] is None


def test_a_behavior_key_with_no_realization_row_is_still_declared() -> None:
    """The two key sets differ, which is why the floor is its OWN section.

    claude's ``transform`` is realized on NO channel (kanibako's patch pipeline
    consumes it) and codex's ``endpoint`` is delivered by a ``config.toml`` rewrite
    rather than a ``SettingArg``.  Folding the floor into ``descriptor.settings:``
    would have left both with nowhere to live.
    """
    claude = ClaudeTarget()
    assert "transform" in {d.key for d in claude.setting_descriptors()}
    assert "transform" not in {s.setting_key for s in claude.descriptor.settings}

    codex = CodexTarget()
    assert "endpoint" in {d.key for d in codex.setting_descriptors()}
    assert "endpoint" not in {s.setting_key for s in codex.descriptor.settings}


#: The keys each shipped plugin leaves to ``agent.default`` (Q105).
_INHERITED = {
    agent: [key for key, value in rows if value is UNSET]
    for agent, rows in _SHIPPED.items()
}


def _snapshot(agent: str, system_file: Path):
    """The launch's behavior snapshot for *agent* over its REAL floors, both tiers."""
    return build_launch_snapshot(
        agent_name=agent,
        ctx=ResolveCtx(
            agent_name=agent, workset_name=None, host_home="/home/host",
            xdg={"XDG_DATA_HOME": "/data"},
        ),
        system_path=system_file,
        agent_path=None,
        workset_path=None,
        box_path=None,
        behavior_floor=core_defaults.behavior_defaults(),
        agent_behavior_floor=descriptor_floor(_TARGETS[agent]().setting_descriptors()),
    )


@pytest.mark.parametrize("agent", sorted(_TARGETS))
def test_an_inherited_row_lets_a_users_agent_default_through(
    agent: str, tmp_path: Path,
) -> None:
    """Q105: a row with no ``default:`` puts nothing at ``agent.<agent>``.

    The §2d pick then falls through to ``agent.default``, so a user's
    ``agent.default.<key>`` answers for every inherited key.

    (Negative control: ship ``default: ""`` on the model row → the slot holds ``""``
    and RED on the ``not in slot``.)
    """
    keys = _INHERITED[agent]
    assert keys, f"{agent} inherits nothing; the corpus is empty"
    system_file = tmp_path / "settings.yaml"
    system_file.write_text(
        "agent:\n  default:\n" + "".join(f"    {k}: mine\n" for k in keys)
    )
    snap = _snapshot(agent, system_file)
    slot = snap.agent.get(agent, {})
    eff = effective_behavior(snap, active_agent=agent)
    for key in keys:
        assert key not in slot, (agent, key, slot)
        assert eff[key] == "mine", (agent, key, eff)


@pytest.mark.parametrize("agent", sorted(_TARGETS))
def test_a_users_agent_default_model_is_delivered(agent: str, tmp_path: Path) -> None:
    """A user's ``agent.default.model`` reaches the harness: claude/codex ``--model``,
    goose ``GOOSE_MODEL`` — the realization the plugin's descriptor declares."""
    system_file = tmp_path / "settings.yaml"
    system_file.write_text("agent:\n  default:\n    model: m-default\n")
    target = _TARGETS[agent]()
    realized = _LaunchRealizer(
        desc=target.descriptor, agent_id=agent, safe_mode=False, autonomous=False,
    )(_snapshot(agent, system_file))
    if agent == "goose":
        assert realized.env.get("GOOSE_MODEL") == "m-default", realized.env
        return
    argv = assemble_argv(
        target.descriptor, mode_fragment=None, access="full",
        setting_values=realized.effective_state, extra_args=[],
    )
    assert argv[argv.index("--model") + 1] == "m-default", argv


@pytest.mark.parametrize("agent", sorted(_TARGETS))
def test_nothing_set_delivers_no_model(agent: str, tmp_path: Path) -> None:
    """With no model set anywhere, ``agent.default.model``'s ``<None>`` answers and the
    harness gets no model at all (its built-in default)."""
    system_file = tmp_path / "settings.yaml"
    system_file.write_text("{}\n")
    target = _TARGETS[agent]()
    realized = _LaunchRealizer(
        desc=target.descriptor, agent_id=agent, safe_mode=False, autonomous=False,
    )(_snapshot(agent, system_file))
    assert "model" not in realized.effective_state
    assert "GOOSE_MODEL" not in realized.env
    argv = assemble_argv(
        target.descriptor, mode_fragment=None, access="full",
        setting_values=realized.effective_state, extra_args=[],
    )
    assert "--model" not in argv, argv


@pytest.mark.parametrize("agent", sorted(_TARGETS))
def test_a_users_per_agent_setting_beats_their_agent_default(
    agent: str, tmp_path: Path,
) -> None:
    """A user's ``agent.<agent>.model`` wins over their ``agent.default.model``."""
    system_file = tmp_path / "settings.yaml"
    system_file.write_text(
        f"agent:\n  default:\n    model: general\n  {agent}:\n    model: picked\n"
    )
    eff = effective_behavior(_snapshot(agent, system_file), active_agent=agent)
    assert eff["model"] == "picked"


@pytest.mark.parametrize("agent", ["claude", "goose"])
@pytest.mark.parametrize("scope", ["default", "own"])
def test_the_inherited_endpoint_keeps_suppress_and_delivery_in_step(
    agent: str, scope: str, std, config, project_dir, tmp_path: Path,
) -> None:
    """The endpoint ⇒ suppress-sync read and the endpoint env delivery pick alike.

    ``_resolve_box_launch_decisions`` answers the endpoint that suppresses the host
    OAuth sync; the main launch snapshot answers the one ``assemble_env`` delivers
    (``ANTHROPIC_BASE_URL`` / ``OPENAI_HOST``).  The plugin row sets no endpoint, so a
    user's ``agent.default.endpoint`` and ``agent.<agent>.endpoint`` each reach BOTH.  A split would send the host
    token to a third-party endpoint, or suppress the login for a box that uses none.
    """
    from kanibako.commands import start as start_cmd
    from kanibako.settings.paths import resolve_project
    from kanibako.settings.agent_select import AgentSelection

    node = "default" if scope == "default" else agent
    system_file = tmp_path / "settings.yaml"
    system_file.write_text(
        f"agent:\n  {node}:\n    endpoint: https://elsewhere.example\n"
    )
    proj = resolve_project(std, config, str(project_dir), initialize=True)
    target = _TARGETS[agent]()
    selection = AgentSelection(node=agent, source="settings").selection_level
    _auth, suppressing, _model = start_cmd._resolve_box_launch_decisions(
        std=std, proj=proj, target=target, agent_name=agent, agent_cfg=None,
        system_settings_path=system_file, agent_cfg_path=None,
        selection_level=selection,
    )
    snapshot, _deliveries = start_cmd._resolve_launch_snapshot(
        std=std, proj=proj, agent_name=agent, system_settings_path=system_file,
        agent_cfg_path=None, desc=None, install=None, target=target,
        agent_cfg=None, cli_level=selection,
    )
    delivered = effective_behavior(snapshot, active_agent=agent).get("endpoint")
    assert suppressing == "https://elsewhere.example"
    assert delivered == "https://elsewhere.example"

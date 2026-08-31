"""Plugin-declared BEHAVIOR defaults live in the shipped YAML, not in plugin code.

DEFAULTS-1 D1-7 (the owner's ruling, *"nothing declared in plugin CODE"*): the floor
value of every ``agent.<agent>.<key>`` behavior key — claude's model, goose's three
deliberately-empty ones, codex's model, the endpoints, claude's transform — used to be a
``default=`` literal inside each plugin's ``setting_descriptors()``.  It is a
``behavior:`` row in ``<agent>-defaults.yaml`` now, and ``setting_descriptors()``
returns what the loader read.

⚑ Sibling of ``test_agent_envs.py``, which did the same for the plugins' env literals.
The RULE (what the loader accepts and refuses) is pinned over synthetic files in
``tests/test_settings/test_agent_defaults.py::TestLoadBehavior``; what THIS file pins is
the SHIPPED tables and the absence of a second declaration site.

⚑ These are the values the launch floors on: ``start.py`` builds the behavior floor as
``{d.key: d.default for d in target.setting_descriptors()}`` (under the core §2d
backstop), so a wrong row here is a wrong floor for every box of that agent.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kanibako.plugins.claude import ClaudeTarget
from kanibako.plugins.codex import CodexTarget
from kanibako.plugins.goose import GooseTarget

_TARGETS = {
    "claude": ClaudeTarget,
    "codex": CodexTarget,
    "goose": GooseTarget,
}

#: The SHIPPED floor, key → default, in declaration order.  Changing one of these is a
#: change to what every box of that agent runs at when the user has set nothing.
_SHIPPED: dict[str, list[tuple[str, str]]] = {
    "claude": [("model", ""), ("endpoint", ""), ("transform", "tweakcc")],
    "codex": [("model", ""), ("endpoint", "")],
    "goose": [("provider", ""), ("model", ""), ("endpoint", "")],
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
    """KANIBAKO IMPOSES NO MODEL — every plugin's ``model`` floor is EMPTY.

    Spec §2d ships ``agent.default.model | <None>`` and each per-agent row as
    *"default <None> (use <agent>'s built-in default)"*.  An empty floor resolves to
    ``""``, which ``assemble_argv`` / ``assemble_env`` OMIT (``if value:``), so
    kanibako puts nothing on the argv and the harness picks for itself.

    ⚑ THIS IS THE RULE, NOT A VALUE.  ``_SHIPPED`` above is an inventory: it reds
    when a listed row changes, but it can say nothing about a plugin nobody added to
    it.  This one reds for ANY opinionated model default on ANY shipped plugin,
    which is the property the ruling actually asserts — so it is the test that
    catches the reintroduction, and ``_SHIPPED`` only records what the tables hold.

    ⚑ The corpus is ``_TARGETS`` — three classes imported BY NAME at module scope —
    and not the plugin discovery registry, on purpose: a discovery-derived corpus
    can come back empty (nothing installed) and pass vacuously, whereas a missing
    import here reds at collection (P15).

    ⚑ The key must stay DECLARED, which is why this asserts an EMPTY value and not
    an ABSENT key: deleting the row would take ``model`` off ``setting_descriptors``
    and ``config set agent.<node>.model`` would refuse a real key.

    (Mutation: put ``default: opus`` back on claude's ``behavior:`` model row → RED
    here by name; delete the row entirely → RED here on the declared-key assert.)
    """
    floors = {d.key: d.default for d in _TARGETS[agent]().setting_descriptors()}
    assert "model" in floors, (
        f"{agent} declares no 'model' behavior row; the key stays DECLARED with an "
        f"EMPTY floor so an explicit agent.<node>.model still resolves"
    )
    assert floors["model"] == "", (
        f"{agent} ships an opinionated model floor {floors['model']!r}; kanibako "
        f"imposes no model (spec §2d agent.<agent>.model | <None>) — each harness "
        f"uses its own built-in default until the user sets one"
    )


def test_goose_pins_no_provider() -> None:
    """goose's ``provider`` floor is the EMPTY STRING, and that is load-bearing.

    An empty floor resolves to ``""``, which ``assemble_env`` omits (``if value:``) —
    so goose's own ``config.yaml`` (from ``goose configure``, persisted by the home
    bind) keeps owning the provider, exactly as on the host.  Any non-empty value
    here would override the user's own config on EVERY launch (spec §2d, the goose
    section: kanibako *"does NOT impose or pre-declare provider/model"*).

    The key stays DECLARED, so an explicit ``agent.goose.provider`` still wins the
    cascade and IS emitted.  ⚑ ``model`` is not asserted here any more: it is no
    longer a goose peculiarity but the ALL-AGENTS rule above.
    """
    floors = {d.key: d.default for d in GooseTarget().setting_descriptors()}
    assert floors["provider"] == ""


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

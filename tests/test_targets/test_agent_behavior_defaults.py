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
    "claude": [("model", "opus"), ("endpoint", ""), ("transform", "tweakcc")],
    "codex": [("model", "gpt-5.5"), ("endpoint", "")],
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


def test_goose_pins_no_provider_or_model() -> None:
    """goose's three floors are the EMPTY STRING, and that is load-bearing.

    An empty floor resolves to ``""``, which ``assemble_env`` omits (``if value:``) —
    so goose's own ``config.yaml`` (from ``goose configure``, persisted by the home
    bind) keeps owning the provider/model, exactly as on the host.  Any non-empty
    value here would override the user's own config on EVERY launch (spec §2d, the
    goose section: kanibako *"does NOT impose or pre-declare provider/model"*).

    The keys stay DECLARED, so an explicit ``agent.goose.model`` still wins the
    cascade and IS emitted.
    """
    floors = {d.key: d.default for d in GooseTarget().setting_descriptors()}
    assert floors == {"provider": "", "model": "", "endpoint": ""}


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

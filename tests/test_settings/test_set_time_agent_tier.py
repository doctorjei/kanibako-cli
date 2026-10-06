"""Keyspec §2a: ``set`` validates against the target's FULL cascade, agent tiers included, so a
``{meta.box.agent.<key>}`` mirror ref whose key lives only in an agent tier resolves at set time
as it does at launch; a mirror ref to a key no tier holds is still refused as dangling."""

from __future__ import annotations

import pytest
import yaml

_AGENT = "claude"


def _main(argv, capsys):
    """``cli.main`` to completion; return ``(exit_code, stdout + stderr)``."""
    from kanibako import cli

    try:
        cli.main(argv)
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    cap = capsys.readouterr()
    return code, cap.out + cap.err


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc))


@pytest.fixture
def std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    std = load_std_paths(load_config(config_file))
    _write(std.settings, {"system": {"agent": _AGENT}})
    return std


@pytest.fixture
def box(std, config_file, tmp_home, credentials_dir):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import resolve_project

    project_dir = str(tmp_home / "project")
    resolve_project(std, load_config(config_file), project_dir=project_dir, initialize=True)
    return project_dir


@pytest.fixture
def workset(std, tmp_home):
    from kanibako.project.workset import create_workset

    create_workset("ws1", tmp_home / "ws1", std)
    return "ws1"


def _agent_file(std, doc):
    from kanibako.settings.agent_config import agent_settings_path

    _write(agent_settings_path(std.agents, _AGENT), doc)


#: Each agent tier a launch reads, holding ``canon`` and nothing else does.
_TIERS = {
    "agent file": ("agent", {"self": {"canon": "/c"}}),
    "agent file agent.default": ("agent", {"agent": {"default": {"canon": "/c"}}}),
    "system agent.default": ("system", {"agent": {"default": {"canon": "/c"}}}),
}


def _seed(std, tier):
    where, doc = _TIERS[tier]
    if where == "agent":
        _agent_file(std, doc)
    else:
        _write(std.settings, {"system": {"agent": _AGENT}, **doc})


def _behavior_floor(which):
    from kanibako.settings.core_defaults import behavior_defaults
    from kanibako.targets import resolve_target
    from kanibako.targets.base import descriptor_floor

    if which == "core":
        return behavior_defaults()
    return descriptor_floor(resolve_target(_AGENT).setting_descriptors())


def _set_argv(scope, target, kv):
    return [scope, "set", *([target] if target else []), kv]


@pytest.fixture(params=["box", "workset", "system"])
def door(request, std):
    """``(scope, target)`` for the noun's ``set``; the edited key is ``box.env.FOO`` at each."""
    scope = request.param
    if scope == "system":
        return scope, None
    return scope, request.getfixturevalue(scope)


class TestAMirrorRefIntoAnAgentTierIsAccepted:
    @pytest.mark.parametrize("tier", sorted(_TIERS))
    def test_accepted(self, door, std, tier, capsys):
        _seed(std, tier)
        code, out = _main(_set_argv(*door, "box.env.FOO={meta.box.agent.canon}/x"), capsys)
        assert code == 0, out
        assert out.strip() == "Set box.env.FOO={meta.box.agent.canon}/x", out

    @pytest.mark.parametrize("floor", ["core", "plugin"])
    def test_a_key_only_a_behavior_floor_sets(self, box, floor, capsys):
        """The launch's two behavior floors: core's at ``agent.default``, the plugin's at
        ``agent.<active>``."""
        key = next(k for k, v in _behavior_floor(floor).items() if isinstance(v, str))
        kv = f"box.env.FOO={{meta.box.agent.{key}}}/x"
        code, out = _main(_set_argv("box", box, kv), capsys)
        assert code == 0, out
        assert out.strip() == f"Set {kv}", out

    def test_a_legitimate_agent_file_is_not_called_a_non_key(self, box, std, capsys):
        _agent_file(std, {"self": {"canon": "/c", "endpoint": "https://x"}})
        code, out = _main(_set_argv("box", box, "box.env.FOO={meta.box.agent.canon}/x"), capsys)
        assert code == 0, out
        assert "not a key" not in out and "not keys" not in out, out


class TestADanglingMirrorRefIsStillRefused:
    def test_an_undeclared_mirror_key(self, door, std, capsys):
        _agent_file(std, {"self": {"canon": "/c"}})
        code, out = _main(_set_argv(*door, "box.env.FOO={meta.box.agent.nope}/x"), capsys)
        assert code == 1 and "dangling" in out and "meta.box.agent.nope" in out, out

    def test_a_declared_key_no_tier_holds(self, box, std, capsys):
        code, out = _main(_set_argv("box", box, "box.env.FOO={meta.box.agent.canon}/x"), capsys)
        assert code == 1 and "dangling" in out and "meta.box.agent.canon" in out, out

    def test_an_agent_file_that_does_not_read_leaves_the_ref_dangling(self, box, std, capsys):
        _agent_file(std, {"self": {"canon": "/c"}, "stray": 1})
        code, out = _main(_set_argv("box", box, "box.env.FOO={meta.box.agent.canon}/x"), capsys)
        assert code == 1 and "meta.box.agent.canon" in out, out
        assert "Traceback" not in out, out

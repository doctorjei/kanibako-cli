"""Tests for the behavior-settings loader + cascade resolver (sub-step 2c).

Cascade (MOST-SPECIFIC last in override terms; resolver walks most-specific
first):

    settings_base < system < agent.<agent> < workset < box < settings_required

The two ``/etc/kanibako/settings_{base,required}.yaml`` layers are absent in the
test environment (verified by the suite), so these tests exercise the cascade
over the system/agent/workset/box tiers plus the optional *floor* defaults.
"""

from __future__ import annotations

from kanibako.config import load_settings
from kanibako.settings_resolve import SettingsResolver, _Unset


def _write_agent(path, agent_name, **kv):
    """Write a config file with an ``agent.<agent_name>`` table of *kv*."""
    lines = ["agent:", f"  {agent_name}:"]
    for k, v in kv.items():
        lines.append(f"    {k}: {v}")
    path.write_text("\n".join(lines) + "\n")


class TestCascadeOrder:
    def test_box_overrides_workset_agent_system(self, tmp_path):
        sys_p = tmp_path / "system.yaml"
        ws_p = tmp_path / "workset.yaml"
        box_p = tmp_path / "box.yaml"
        _write_agent(sys_p, "claude", model="sys")
        _write_agent(ws_p, "claude", model="ws")
        _write_agent(box_p, "claude", model="box")

        r = load_settings(
            "claude",
            system_path=sys_p,
            agent_state={"model": "agentstate"},
            workset_path=ws_p,
            box_path=box_p,
        )
        # box is most-specific (below the absent required cap) → it wins.
        assert r.get("model") == "box"

    def test_workset_overrides_agent_system(self, tmp_path):
        sys_p = tmp_path / "system.yaml"
        ws_p = tmp_path / "workset.yaml"
        _write_agent(sys_p, "claude", model="sys")
        _write_agent(ws_p, "claude", model="ws")
        r = load_settings(
            "claude",
            system_path=sys_p,
            agent_state={"model": "agentstate"},
            workset_path=ws_p,
        )
        assert r.get("model") == "ws"

    def test_agent_overrides_system(self, tmp_path):
        sys_p = tmp_path / "system.yaml"
        _write_agent(sys_p, "claude", model="sys")
        r = load_settings(
            "claude",
            system_path=sys_p,
            agent_state={"model": "agentstate"},
        )
        assert r.get("model") == "agentstate"

    def test_system_overrides_base_floor(self, tmp_path):
        sys_p = tmp_path / "system.yaml"
        _write_agent(sys_p, "claude", model="sys")
        # floor is the settings_base level's declared default (lowest authority).
        r = load_settings(
            "claude",
            system_path=sys_p,
            floor={"model": "default"},
        )
        assert r.get("model") == "sys"

    def test_floor_is_ultimate_fallback(self, tmp_path):
        # Nothing set anywhere → the declared default floor wins.
        r = load_settings(
            "claude",
            system_path=None,
            floor={"model": "default", "autonomy": "off"},
        )
        assert r.get("model") == "default"
        assert r.get("autonomy") == "off"


class TestGetSemantics:
    def test_get_most_specific_set_value(self, tmp_path):
        sys_p = tmp_path / "system.yaml"
        box_p = tmp_path / "box.yaml"
        _write_agent(sys_p, "claude", model="sys", autonomy="sys-auto")
        _write_agent(box_p, "claude", model="box")
        r = load_settings(
            "claude", system_path=sys_p, box_path=box_p,
        )
        # model overridden at box; autonomy only set at system.
        assert r.get("model") == "box"
        assert r.get("autonomy") == "sys-auto"

    def test_get_unset_returns_default_arg(self, tmp_path):
        r = load_settings("claude", system_path=None)
        assert r.get("nonexistent") is None
        assert r.get("nonexistent", "fallback") == "fallback"

    def test_terminal_empty_suppresses_floor(self, tmp_path):
        # An explicit "" at box is terminal: it wins and does NOT fall through
        # to the floor default.
        box_p = tmp_path / "box.yaml"
        box_p.write_text('agent:\n  claude:\n    model: ""\n')
        r = load_settings(
            "claude", system_path=None, box_path=box_p,
            floor={"model": "default"},
        )
        assert r.get("model") == ""

    def test_agent_default_tier_layered_under_agent_specific(self, tmp_path):
        # read_agent_settings layers agent.default < agent.<agent> within ONE
        # file; the per-agent value wins.
        p = tmp_path / "system.yaml"
        p.write_text(
            "agent:\n"
            "  default:\n"
            "    model: any\n"
            "    autonomy: any-auto\n"
            "  claude:\n"
            "    model: claude-specific\n"
        )
        r = load_settings("claude", system_path=p)
        assert r.get("model") == "claude-specific"
        assert r.get("autonomy") == "any-auto"


class TestAbsentLayersSkipped:
    def test_absent_optional_layers_contribute_nothing(self, tmp_path):
        sys_p = tmp_path / "system.yaml"
        _write_agent(sys_p, "claude", model="sys")
        # workset_path/box_path/agent_path None, and /etc layers absent.
        r = load_settings("claude", system_path=sys_p)
        assert r.get("model") == "sys"

    def test_all_layers_absent_yields_empty(self, tmp_path):
        r = load_settings("claude", system_path=tmp_path / "nope.yaml")
        assert r.effective() == {}
        assert r.keys() == set()

    def test_unreadable_layer_swallowed(self, tmp_path):
        # A path that is a directory (read fails) contributes nothing rather
        # than raising.
        bad = tmp_path / "adir"
        bad.mkdir()
        sys_p = tmp_path / "system.yaml"
        _write_agent(sys_p, "claude", model="sys")
        r = load_settings("claude", system_path=sys_p, box_path=bad)
        assert r.get("model") == "sys"


class TestResolverApi:
    def test_returns_settings_resolver(self, tmp_path):
        r = load_settings("claude", system_path=None)
        assert isinstance(r, SettingsResolver)

    def test_resolve_exposes_provenance(self, tmp_path):
        sys_p = tmp_path / "system.yaml"
        box_p = tmp_path / "box.yaml"
        _write_agent(sys_p, "claude", model="sys")
        _write_agent(box_p, "claude", model="box")
        r = load_settings("claude", system_path=sys_p, box_path=box_p)
        rv = r.resolve("model")
        assert not isinstance(rv, _Unset)
        assert rv.value == "box"
        assert rv.level == "box"

    def test_effective_collapses_cascade(self, tmp_path):
        sys_p = tmp_path / "system.yaml"
        box_p = tmp_path / "box.yaml"
        _write_agent(sys_p, "claude", model="sys", autonomy="sys-a")
        _write_agent(box_p, "claude", model="box")
        r = load_settings(
            "claude", system_path=sys_p, box_path=box_p,
            floor={"model": "def", "extra": "floored"},
        )
        eff = r.effective()
        assert eff == {
            "model": "box",
            "autonomy": "sys-a",
            "extra": "floored",
        }

    def test_categories_is_phase4_stub(self, tmp_path):
        r = load_settings("claude", system_path=None)
        try:
            r.categories()
        except NotImplementedError:
            pass
        else:
            raise AssertionError("categories() should raise NotImplementedError")

    def test_agent_path_overlays_agent_state(self, tmp_path):
        # The agent tier = agent_state overlaid by an explicit agent file.
        agent_p = tmp_path / "agent.yaml"
        _write_agent(agent_p, "claude", model="from-file")
        r = load_settings(
            "claude",
            system_path=None,
            agent_state={"model": "from-state", "autonomy": "state-a"},
            agent_path=agent_p,
        )
        assert r.get("model") == "from-file"  # file overlays state
        assert r.get("autonomy") == "state-a"  # state-only key survives


class TestRequiredCap:
    """The settings_required tier sits ABOVE box (decision D).

    The /etc required file is absent in tests, so we verify the LEVEL ORDER
    directly via the resolver the loader builds: ``settings_required`` is the
    first (most-specific) level.
    """

    def test_required_is_most_specific_level(self, tmp_path):
        r = load_settings("claude", system_path=None)
        assert r.levels[0].name == "settings_required"
        assert r.levels[1].name == "box"
        # base is least-specific (last).
        assert r.levels[-1].name == "settings_base"

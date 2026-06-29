"""Tests for resource scoping: kanibako mounts and effective-state precedence.

The ``_build_resource_mounts`` / ``ResourceMapping`` / ``ResourceScope`` resource
abstraction was DELETED in 1.6.0 (Part 3b): every shipped mapping was PROJECT
(lives in the box home bind, no mount), and claude's only shared dirs (plugins +
cache) are now ``agent.shared.*`` category entries.  Those tests are removed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from kanibako.agent_config import AgentConfig
from kanibako.targets.base import TargetSetting


class TestKanibakoMounts:
    """Tests for _kanibako_mounts() in start.py."""

    def test_returns_two_mounts(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        assert len(mounts) == 2

    def test_package_mount_destination(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        pkg_mount = mounts[0]
        assert pkg_mount.destination == "/opt/kanibako/kanibako"
        assert pkg_mount.options == "ro"

    def test_entry_script_mount_destination(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        entry_mount = mounts[1]
        assert entry_mount.destination == "/home/agent/.local/bin/kanibako"
        assert entry_mount.options == "ro"

    def test_package_source_is_kanibako_dir(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        pkg_mount = mounts[0]
        # Source should be the kanibako package directory
        assert pkg_mount.source.is_dir()
        assert (pkg_mount.source / "__init__.py").is_file()

    def test_entry_script_source_exists(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        entry_mount = mounts[1]
        assert entry_mount.source.is_file()
        content = entry_mount.source.read_text()
        assert "kanibako.cli" in content

    def test_routed_kani_categories_match_hardwired(self):
        """Phase B: the routed kani binds carry the SAME sources/dests/options.

        The box launch now routes the kanibako CLI binds through
        ``core_defaults.kani_default_categories`` (the category resolver) instead
        of the hardwired ``_kanibako_mounts`` ``-v`` list (which survives only as
        the in-helper-container source resolver).  Lock that the two agree
        byte-for-byte so the routing is a pure refactor.
        """
        from kanibako import core_defaults
        from kanibako.commands.start import _kanibako_mounts

        hardwired = _kanibako_mounts()
        cats = core_defaults.kani_default_categories()

        assert cats["box.bindings.ro.kani_pkg"] == (
            str(hardwired[0].source), "/opt/kanibako/kanibako", "ro",
        )
        # box_dest carries ~ (expanded by the resolver) — same /home/agent dest.
        assert cats["box.bindings.ro.kani_bin"] == (
            str(hardwired[1].source), "~/.local/bin/kanibako", "ro",
        )


class TestBuildEffectiveState:
    """Tests for the ``config --effective`` behavior read
    (``start._effective_behavior_for_display``, block 7c — the snapshot-based
    successor to the retired ``_build_effective_state`` precedence walk). The
    discriminated ``agent.<name>.*`` / ``agent.default.*`` file shapes
    (``write_agent_setting``) feed the snapshot; the §2d active-over-default pick
    yields the launch-correct effective state."""

    def _make_target(self, descriptors, name="claude"):
        target = MagicMock()
        target.setting_descriptors.return_value = descriptors
        target.name = name
        return target

    def _make_global_config(self, tmp_path, settings=None):
        """Create a minimal global kanibako_config.yaml, optionally with [agent]."""
        from kanibako.config import write_agent_setting

        global_toml = tmp_path / "kanibako_config.yaml"
        global_toml.write_text("")
        if settings:
            for k, v in settings.items():
                write_agent_setting(global_toml, k, v, "claude")
        return global_toml

    def _make_workset_config(self, tmp_path, settings=None):
        """Create a minimal workset config.yaml, optionally with [agent]."""
        from kanibako.config import write_agent_setting

        tmp_path.mkdir(parents=True, exist_ok=True)
        ws_toml = tmp_path / "config.yaml"
        ws_toml.write_text("")
        if settings:
            for k, v in settings.items():
                write_agent_setting(ws_toml, k, v, "claude")
        return ws_toml

    def _make_project_toml(self, tmp_path, settings=None):
        """Create a minimal settings.yaml, optionally with [agent] overrides."""
        from kanibako.config import write_project_meta, write_agent_setting

        tmp_path.mkdir(parents=True, exist_ok=True)
        project_toml = tmp_path / "settings.yaml"
        write_project_meta(
            project_toml,
            mode="primary",
            workspace="/w", shell="/s", vault_ro="/ro", vault_rw="/rw",
        )
        if settings:
            for k, v in settings.items():
                write_agent_setting(project_toml, k, v, "claude")
        return project_toml

    def test_target_defaults_only(self, tmp_path):
        """When agent has no state and no project overrides, target defaults apply."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
            TargetSetting(key="access", description="Access", default="permissive"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig()  # empty state
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result == {"model": "opus", "access": "permissive"}

    def test_agent_overrides_default(self, tmp_path):
        """Agent config state overrides target defaults."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig(state={"model": "sonnet"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result["model"] == "sonnet"

    def test_project_override_wins(self, tmp_path):
        """Project overrides take highest precedence."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig(state={"model": "sonnet"})
        project_toml = self._make_project_toml(tmp_path, settings={"model": "haiku"})

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result["model"] == "haiku"

    def test_agent_state_passthrough_for_undeclared_keys(self, tmp_path):
        """Undeclared keys from agent state are passed through."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig(state={"model": "sonnet", "custom_key": "custom_value"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result["model"] == "sonnet"
        assert result["custom_key"] == "custom_value"

    def test_no_descriptors_returns_agent_state(self, tmp_path):
        """When target has no setting_descriptors, return agent state as-is."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        target = self._make_target([])  # no descriptors
        agent_cfg = AgentConfig(state={"model": "opus", "access": "permissive"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result == {"model": "opus", "access": "permissive"}

    def test_system_level_provides_value(self, tmp_path):
        """System [crab] (global kanibako_config.yaml) supplies a value when nothing
        more specific sets it."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig()  # empty state
        project_toml = self._make_project_toml(tmp_path)
        global_toml = self._make_global_config(tmp_path, settings={"model": "sonnet"})

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=global_toml
        )
        # System set value beats the target-default floor.
        assert result["model"] == "sonnet"

    def test_precedence_box_workset_crab_system(self, tmp_path):
        """Precedence is box > workset > crab > system; system beats the floor.

        Levels are most-specific-first ``[box, workset, crab, system]``, so a
        value set at the workset level beats one set in crab state.
        """
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
            TargetSetting(key="access", description="Access", default="permissive"),
        ]
        target = self._make_target(descriptors)
        global_toml = self._make_global_config(
            tmp_path, settings={"model": "sys-model", "access": "default"}
        )
        # workset config lives in its own dir to avoid colliding filenames.
        ws_toml = self._make_workset_config(
            tmp_path / "ws", settings={"model": "ws-model"}
        )

        # crab state also sets model — but workset is more specific, so workset
        # wins.  access is left for the system level only.
        agent_cfg = AgentConfig(state={"model": "crab-model"})
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        project_toml = self._make_project_toml(proj_dir)

        result = _build_effective_state(
            target,
            agent_cfg,
            project_toml,
            global_config_path=global_toml,
            workset_config_path=ws_toml,
        )
        # model: box unset → workset (more specific than crab/system) wins.
        assert result["model"] == "ws-model"
        # access: only system sets it; nothing more specific does, so the
        # system set value wins over the "permissive" floor.
        assert result["access"] == "default"

        # Now set model at the box level too → box beats workset.
        box_toml = self._make_project_toml(
            tmp_path / "proj2", settings={"model": "box-model"}
        )
        result2 = _build_effective_state(
            target,
            agent_cfg,
            box_toml,
            global_config_path=global_toml,
            workset_config_path=ws_toml,
        )
        assert result2["model"] == "box-model"

    def test_empty_string_is_terminal(self, tmp_path):
        """An explicit '' at a level suppresses fall-through to the floor."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        # crab state explicitly clears model.
        agent_cfg = AgentConfig(state={"model": ""})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        # Terminal "" — does not fall back to the "opus" floor.
        assert result["model"] == ""

    def test_box_override_does_not_bleed_across_agents(self, tmp_path):
        """B3 regression: a box override set under agent.claude must NOT apply
        when the effective state is resolved for agent goose (and vice-versa)."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state
        from kanibako.config import write_agent_setting, write_project_meta

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        # An override written while the box was on claude.
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        project_toml = proj_dir / "settings.yaml"
        write_project_meta(
            project_toml,
            mode="primary",
            workspace="/w", shell="/s", vault_ro="/ro", vault_rw="/rw",
        )
        write_agent_setting(project_toml, "model", "sonnet", "claude")
        agent_cfg = AgentConfig()

        # claude sees its override.
        claude = self._make_target(descriptors, name="claude")
        res_claude = _build_effective_state(
            claude, agent_cfg, project_toml, global_config_path=None
        )
        assert res_claude["model"] == "sonnet"

        # goose does NOT — it falls back to its declared default floor.
        goose = self._make_target(descriptors, name="goose")
        res_goose = _build_effective_state(
            goose, agent_cfg, project_toml, global_config_path=None
        )
        assert res_goose["model"] == "opus"

    def test_default_tier_applies_to_all_agents_unless_overridden(self, tmp_path):
        """agent.default applies to every agent; agent.<name> overrides it."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state
        from kanibako.config import write_agent_setting, write_project_meta

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        project_toml = proj_dir / "settings.yaml"
        write_project_meta(
            project_toml,
            mode="primary",
            workspace="/w", shell="/s", vault_ro="/ro", vault_rw="/rw",
        )
        # Any-agent default, plus a claude-specific override.
        write_agent_setting(project_toml, "model", "haiku", "default")
        write_agent_setting(project_toml, "model", "sonnet", "claude")
        agent_cfg = AgentConfig()

        claude = self._make_target(descriptors, name="claude")
        res_claude = _build_effective_state(
            claude, agent_cfg, project_toml, global_config_path=None
        )
        assert res_claude["model"] == "sonnet"  # agent-specific wins

        goose = self._make_target(descriptors, name="goose")
        res_goose = _build_effective_state(
            goose, agent_cfg, project_toml, global_config_path=None
        )
        assert res_goose["model"] == "haiku"  # default tier applies

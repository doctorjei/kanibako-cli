"""Tests for resource scoping: _build_resource_mounts, resource overrides, and effective state."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from kanibako.crabs import CrabConfig
from kanibako.targets.base import ResourceMapping, ResourceScope, TargetSetting


class TestBuildResourceMounts:
    """Tests for _build_resource_mounts() in start.py."""

    def _make_proj(self, tmp_path):
        """Create a minimal ProjectPaths-like object."""
        metadata = tmp_path / "metadata"
        metadata.mkdir()
        shell = tmp_path / "shell"
        shell.mkdir()
        (shell / ".claude").mkdir()
        global_shared = tmp_path / "shared" / "global"
        global_shared.mkdir(parents=True)
        # Write an empty project.yaml so read_resource_overrides finds it.
        (metadata / "project.yaml").write_text(
            'project:\n  mode: "default"\n  layout: "default"\n'
            '  enable_vault: true\n  group_auth: true\n'
            'resolved:\n  workspace: "/w"\n  shell: "/s"\n'
            '  vault_ro: "/ro"\n  vault_rw: "/rw"\n'
            '  metadata: ""\n  project_hash: ""\n'
            '  global_shared: ""\n  local_shared: ""\n'
        )
        return SimpleNamespace(
            metadata_path=metadata,
            shell_path=shell,
            global_shared_path=global_shared,
        )

    def _make_target(self, mappings):
        target = MagicMock()
        target.resource_mappings.return_value = mappings
        target.config_dir_name = ".claude"
        return target

    def test_shared_resource_creates_mount(self, tmp_path):
        from kanibako.commands.start import _build_resource_mounts

        proj = self._make_proj(tmp_path)
        mappings = [ResourceMapping("plugins/", ResourceScope.SHARED, "Plugin binaries")]
        target = self._make_target(mappings)

        mounts = _build_resource_mounts(proj, target, "claude")
        assert len(mounts) == 1
        assert mounts[0].destination == "/home/agent/.claude/plugins/"
        assert "claude/plugins" in str(mounts[0].source)

    def test_project_resource_no_mount(self, tmp_path):
        from kanibako.commands.start import _build_resource_mounts

        proj = self._make_proj(tmp_path)
        mappings = [ResourceMapping("projects/", ResourceScope.PROJECT, "Session data")]
        target = self._make_target(mappings)

        mounts = _build_resource_mounts(proj, target, "claude")
        assert len(mounts) == 0

    def test_seeded_resource_copies_on_first_init(self, tmp_path):
        from kanibako.commands.start import _build_resource_mounts

        proj = self._make_proj(tmp_path)
        # Create a seed file in the shared base.
        seed_dir = proj.global_shared_path / "claude"
        seed_file = seed_dir / "settings.json"
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text('{"key": "value"}')

        mappings = [ResourceMapping("settings.json", ResourceScope.SEEDED, "Settings")]
        target = self._make_target(mappings)

        mounts = _build_resource_mounts(proj, target, "claude")
        # SEEDED doesn't create a mount.
        assert len(mounts) == 0
        # But the file should be copied into the local shell.
        local = proj.shell_path / ".claude" / "settings.json"
        assert local.is_file()
        assert local.read_text() == '{"key": "value"}'

    def test_seeded_resource_noop_when_local_exists(self, tmp_path):
        from kanibako.commands.start import _build_resource_mounts

        proj = self._make_proj(tmp_path)
        # Local already has the file.
        local = proj.shell_path / ".claude" / "settings.json"
        local.write_text('{"local": true}')

        # Shared has a different version.
        seed_dir = proj.global_shared_path / "claude"
        seed_file = seed_dir / "settings.json"
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text('{"shared": true}')

        mappings = [ResourceMapping("settings.json", ResourceScope.SEEDED, "Settings")]
        target = self._make_target(mappings)

        _build_resource_mounts(proj, target, "claude")
        # Local should NOT be overwritten.
        assert local.read_text() == '{"local": true}'

    def test_no_mappings_returns_empty(self, tmp_path):
        from kanibako.commands.start import _build_resource_mounts

        proj = self._make_proj(tmp_path)
        target = self._make_target([])

        mounts = _build_resource_mounts(proj, target, "claude")
        assert mounts == []

    def test_shared_file_resource_creates_file_not_dir(self, tmp_path):
        """A SHARED resource without trailing slash is created as a file, not a directory."""
        from kanibako.commands.start import _build_resource_mounts

        proj = self._make_proj(tmp_path)
        mappings = [ResourceMapping("stats-cache.json", ResourceScope.SHARED, "Stats")]
        target = self._make_target(mappings)

        mounts = _build_resource_mounts(proj, target, "claude")
        assert len(mounts) == 1
        assert mounts[0].destination == "/home/agent/.claude/stats-cache.json"
        source = mounts[0].source
        assert source.is_file(), f"Expected file, got directory: {source}"

    def test_no_shared_base_returns_empty(self, tmp_path):
        from kanibako.commands.start import _build_resource_mounts

        proj = self._make_proj(tmp_path)
        proj.global_shared_path = None
        mappings = [ResourceMapping("plugins/", ResourceScope.SHARED, "Plugins")]
        target = self._make_target(mappings)

        mounts = _build_resource_mounts(proj, target, "claude")
        assert mounts == []


class TestResourceOverrideInMounts:
    """Test that resource overrides change mount behavior."""

    def _make_proj(self, tmp_path):
        metadata = tmp_path / "metadata"
        metadata.mkdir()
        shell = tmp_path / "shell"
        shell.mkdir()
        (shell / ".claude").mkdir()
        global_shared = tmp_path / "shared" / "global"
        global_shared.mkdir(parents=True)
        return SimpleNamespace(
            metadata_path=metadata,
            shell_path=shell,
            global_shared_path=global_shared,
        )

    def test_override_shared_to_project(self, tmp_path):
        """Override a SHARED resource to PROJECT — no mount should be created."""
        from kanibako.commands.start import _build_resource_mounts
        from kanibako.config import write_project_meta, write_resource_override

        proj = self._make_proj(tmp_path)
        project_toml = proj.metadata_path / "project.yaml"
        write_project_meta(
            project_toml,
            mode="default", layout="default",
            workspace="/w", shell="/s", vault_ro="/ro", vault_rw="/rw",
        )
        write_resource_override(project_toml, "plugins/", "project")

        mappings = [ResourceMapping("plugins/", ResourceScope.SHARED, "Plugins")]
        target = MagicMock()
        target.resource_mappings.return_value = mappings
        target.config_dir_name = ".claude"

        mounts = _build_resource_mounts(proj, target, "claude")
        assert len(mounts) == 0

    def test_override_project_to_shared(self, tmp_path):
        """Override a PROJECT resource to SHARED — mount should be created."""
        from kanibako.commands.start import _build_resource_mounts
        from kanibako.config import write_project_meta, write_resource_override

        proj = self._make_proj(tmp_path)
        project_toml = proj.metadata_path / "project.yaml"
        write_project_meta(
            project_toml,
            mode="default", layout="default",
            workspace="/w", shell="/s", vault_ro="/ro", vault_rw="/rw",
        )
        write_resource_override(project_toml, "projects/", "shared")

        mappings = [ResourceMapping("projects/", ResourceScope.PROJECT, "Session data")]
        target = MagicMock()
        target.resource_mappings.return_value = mappings
        target.config_dir_name = ".claude"

        mounts = _build_resource_mounts(proj, target, "claude")
        assert len(mounts) == 1
        assert mounts[0].destination == "/home/agent/.claude/projects/"

    def test_invalid_path_rejected_by_cli(self, tmp_path):
        """Resource override with a path not in resource_mappings should be rejected by CLI."""
        # This tests the CLI validation in #12B — just verify read_resource_overrides works.
        from kanibako.config import read_resource_overrides, write_resource_override

        project_toml = tmp_path / "project.yaml"
        project_toml.write_text(
            'project:\n  mode: "default"\n  layout: "default"\n'
            '  enable_vault: true\n  group_auth: true\n'
            'resolved:\n  workspace: "/w"\n  shell: "/s"\n'
            '  vault_ro: "/ro"\n  vault_rw: "/rw"\n'
            '  metadata: ""\n  project_hash: ""\n'
            '  global_shared: ""\n  local_shared: ""\n'
        )
        write_resource_override(project_toml, "nonexistent/", "shared")
        overrides = read_resource_overrides(project_toml)
        assert overrides == {"nonexistent/": "shared"}


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


class TestBuildEffectiveState:
    """Tests for _build_effective_state() precedence walk in start.py."""

    def _make_target(self, descriptors):
        target = MagicMock()
        target.setting_descriptors.return_value = descriptors
        target.name = "claude"
        return target

    def _make_global_config(self, tmp_path, settings=None):
        """Create a minimal global kanibako.yaml, optionally with [crab]."""
        from kanibako.config import write_crab_setting

        global_toml = tmp_path / "kanibako.yaml"
        global_toml.write_text("")
        if settings:
            for k, v in settings.items():
                write_crab_setting(global_toml, k, v)
        return global_toml

    def _make_workset_config(self, tmp_path, settings=None):
        """Create a minimal workset config.yaml, optionally with [crab]."""
        from kanibako.config import write_crab_setting

        tmp_path.mkdir(parents=True, exist_ok=True)
        ws_toml = tmp_path / "config.yaml"
        ws_toml.write_text("")
        if settings:
            for k, v in settings.items():
                write_crab_setting(ws_toml, k, v)
        return ws_toml

    def _make_project_toml(self, tmp_path, settings=None):
        """Create a minimal project.yaml, optionally with [crab] overrides."""
        from kanibako.config import write_project_meta, write_crab_setting

        tmp_path.mkdir(parents=True, exist_ok=True)
        project_toml = tmp_path / "project.yaml"
        write_project_meta(
            project_toml,
            mode="default", layout="default",
            workspace="/w", shell="/s", vault_ro="/ro", vault_rw="/rw",
        )
        if settings:
            for k, v in settings.items():
                write_crab_setting(project_toml, k, v)
        return project_toml

    def test_target_defaults_only(self, tmp_path):
        """When agent has no state and no project overrides, target defaults apply."""
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
            TargetSetting(key="access", description="Access", default="permissive"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = CrabConfig()  # empty state
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result == {"model": "opus", "access": "permissive"}

    def test_agent_overrides_default(self, tmp_path):
        """Agent config state overrides target defaults."""
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = CrabConfig(state={"model": "sonnet"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result["model"] == "sonnet"

    def test_project_override_wins(self, tmp_path):
        """Project overrides take highest precedence."""
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = CrabConfig(state={"model": "sonnet"})
        project_toml = self._make_project_toml(tmp_path, settings={"model": "haiku"})

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result["model"] == "haiku"

    def test_agent_state_passthrough_for_undeclared_keys(self, tmp_path):
        """Undeclared keys from agent state are passed through."""
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = CrabConfig(state={"model": "sonnet", "custom_key": "custom_value"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result["model"] == "sonnet"
        assert result["custom_key"] == "custom_value"

    def test_no_descriptors_returns_agent_state(self, tmp_path):
        """When target has no setting_descriptors, return agent state as-is."""
        from kanibako.commands.start import _build_effective_state

        target = self._make_target([])  # no descriptors
        agent_cfg = CrabConfig(state={"model": "opus", "access": "permissive"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        assert result == {"model": "opus", "access": "permissive"}

    def test_system_level_provides_value(self, tmp_path):
        """System [crab] (global kanibako.yaml) supplies a value when nothing
        more specific sets it."""
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = CrabConfig()  # empty state
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
        from kanibako.commands.start import _build_effective_state

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
        agent_cfg = CrabConfig(state={"model": "crab-model"})
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
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        # crab state explicitly clears model.
        agent_cfg = CrabConfig(state={"model": ""})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, global_config_path=None
        )
        # Terminal "" — does not fall back to the "opus" floor.
        assert result["model"] == ""

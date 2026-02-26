"""Tests for resource scoping: _build_resource_mounts, resource overrides, and effective state."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from kanibako.agents import AgentConfig
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
        # Write an empty project.toml so read_resource_overrides finds it.
        (metadata / "project.toml").write_text(
            '[project]\nmode = "account_centric"\nlayout = "default"\n'
            'vault_enabled = true\nauth = "shared"\n\n'
            '[resolved]\nworkspace = "/w"\nshell = "/s"\n'
            'vault_ro = "/ro"\nvault_rw = "/rw"\n'
            'metadata = ""\nproject_hash = ""\n'
            'global_shared = ""\nlocal_shared = ""\n'
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
        project_toml = proj.metadata_path / "project.toml"
        write_project_meta(
            project_toml,
            mode="account_centric", layout="default",
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
        project_toml = proj.metadata_path / "project.toml"
        write_project_meta(
            project_toml,
            mode="account_centric", layout="default",
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

        project_toml = tmp_path / "project.toml"
        project_toml.write_text(
            '[project]\nmode = "account_centric"\nlayout = "default"\n'
            'vault_enabled = true\nauth = "shared"\n\n'
            '[resolved]\nworkspace = "/w"\nshell = "/s"\n'
            'vault_ro = "/ro"\nvault_rw = "/rw"\n'
            'metadata = ""\nproject_hash = ""\n'
            'global_shared = ""\nlocal_shared = ""\n'
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
    """Tests for _build_effective_state() 3-tier merge in start.py."""

    def _make_target(self, descriptors):
        target = MagicMock()
        target.setting_descriptors.return_value = descriptors
        return target

    def _make_project_toml(self, tmp_path, settings=None):
        """Create a minimal project.toml, optionally with [target_settings]."""
        from kanibako.config import write_project_meta, write_target_setting

        project_toml = tmp_path / "project.toml"
        write_project_meta(
            project_toml,
            mode="account_centric", layout="default",
            workspace="/w", shell="/s", vault_ro="/ro", vault_rw="/rw",
        )
        if settings:
            for k, v in settings.items():
                write_target_setting(project_toml, k, v)
        return project_toml

    def test_target_defaults_only(self, tmp_path):
        """When agent has no state and no project overrides, target defaults apply."""
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
            TargetSetting(key="access", description="Access", default="permissive"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig()  # empty state
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(target, agent_cfg, project_toml)
        assert result == {"model": "opus", "access": "permissive"}

    def test_agent_overrides_default(self, tmp_path):
        """Agent config state overrides target defaults."""
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig(state={"model": "sonnet"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(target, agent_cfg, project_toml)
        assert result["model"] == "sonnet"

    def test_project_override_wins(self, tmp_path):
        """Project overrides take highest precedence."""
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig(state={"model": "sonnet"})
        project_toml = self._make_project_toml(tmp_path, settings={"model": "haiku"})

        result = _build_effective_state(target, agent_cfg, project_toml)
        assert result["model"] == "haiku"

    def test_agent_state_passthrough_for_undeclared_keys(self, tmp_path):
        """Undeclared keys from agent state are passed through."""
        from kanibako.commands.start import _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig(state={"model": "sonnet", "custom_key": "custom_value"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(target, agent_cfg, project_toml)
        assert result["model"] == "sonnet"
        assert result["custom_key"] == "custom_value"

    def test_no_descriptors_returns_agent_state(self, tmp_path):
        """When target has no setting_descriptors, return agent state as-is."""
        from kanibako.commands.start import _build_effective_state

        target = self._make_target([])  # no descriptors
        agent_cfg = AgentConfig(state={"model": "opus", "access": "permissive"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(target, agent_cfg, project_toml)
        assert result == {"model": "opus", "access": "permissive"}

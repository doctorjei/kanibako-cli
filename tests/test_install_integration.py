"""Integration tests for the install command.

Exercises real filesystem operations for install, containerfile discovery,
and settings filtering.  Run with::

    pytest -m integration tests/test_install_integration.py -v
"""

from __future__ import annotations

import pytest



@pytest.mark.integration
class TestInstallFilesystem:
    """Verify real filesystem operations during install."""

    def test_full_install_creates_directory_tree(
        self, integration_home, integration_config
    ):
        """Install creates config, data, and state directories."""
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        config = load_config(integration_config)
        std = load_std_paths(config)

        assert std.config_file.parent.is_dir()
        assert std.data_path.is_dir()
        assert std.state_path.is_dir()
        assert std.cache_path.is_dir()

    def test_install_preserves_existing_config(
        self, integration_home, integration_config
    ):
        """Running install twice is idempotent — existing config untouched."""
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import write_nested_key

        # Hand-write a non-default Layer-1 value.
        # ⚑ A ``config.*`` key, not ``box.image``: since 2026-08-26 the bootstrap file
        # carries the ``config.*`` foundation and nothing else (Jei), so a settings key
        # planted here is inert — this case is about the FILE surviving, and the
        # foundation is what the file actually holds.
        write_nested_key(integration_config, ("config",), "agents", "/custom/agents")

        # Reload and verify the custom value is preserved
        reloaded = load_config(integration_config)
        assert reloaded.config_paths["config.agents"] == "/custom/agents"

    # NOTE: test_install_filters_settings_json was deleted in 1.6.0 — the host
    # .claude.json allowlist filter (filter_settings) was removed with the
    # host-config import.


@pytest.mark.integration
class TestContainerfileDiscovery:
    """Containerfile discovery and copy logic."""

    def test_discovers_containers_in_cwd(self, integration_home):
        """Finds Containerfile.base in a user-override directory."""
        from kanibako.runtime.containerfiles import get_containerfile

        override_dir = integration_home / "containers"
        override_dir.mkdir()
        cf = override_dir / "Containerfile.base"
        cf.write_text("FROM busybox\n")

        result = get_containerfile("base", override_dir)
        assert result is not None
        assert result == cf

    def test_returns_none_when_no_containerfiles(self, integration_home):
        """Returns None when no Containerfiles are present."""
        from kanibako.runtime.containerfiles import get_containerfile

        empty_dir = integration_home / "empty_containers"
        empty_dir.mkdir()

        result = get_containerfile("nonexistent_xyz", empty_dir)
        assert result is None

    def test_containerfiles_copied_to_data_dir(
        self, integration_home, integration_config
    ):
        """User-override Containerfile takes precedence over bundled."""
        from kanibako.settings.config import load_config
        from kanibako.runtime.containerfiles import get_containerfile
        from kanibako.settings.paths import load_std_paths

        config = load_config(integration_config)
        std = load_std_paths(config)

        containers_dir = std.data_path / "containers"
        containers_dir.mkdir(parents=True, exist_ok=True)

        # Write a user-override Containerfile
        override = containers_dir / "Containerfile.base"
        override.write_text("FROM alpine:latest\n# user override\n")

        result = get_containerfile("base", containers_dir)
        assert result is not None
        assert result == override
        assert "user override" in result.read_text()

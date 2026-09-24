"""Integration tests for install (the lazy first-run init) and containerfile discovery.

Exercises real filesystem operations.  Run with::

    pytest -m integration tests/test_install_integration.py -v
"""

from __future__ import annotations

import pytest



@pytest.mark.integration
class TestInstallFilesystem:
    """Verify real filesystem operations during install."""

    def test_first_run_install_stamps_the_host_stores(self, integration_home):
        """First-run init writes the config file and the spec's install-row stores.

        ⚑ DRIVES THE REAL VERB. kanibako has no ``install`` command: install is the
        lazy first-run init (``cli._ensure_initialized``), which every non-exempt
        command runs before dispatch. The system design spec's OUT-OF-BOX TEMPLATES
        table names what its "install / setup" row copies INTO:
        ``global/template/*``, ``agents/default`` and ``global/canon/handbook``.
        Those are asserted here, plus the config file whose existence is init's
        own done-marker.

        ⚑ NO ``integration_config`` FIXTURE: it writes the config file first, and
        init returns early once that file exists, so it would never run.

        🛑 STATE AND CACHE ARE NOT ASSERTED, because install does not create them.
        This test used to call ``load_std_paths`` and assert all four roots were
        directories. That only held while ``load_std_paths`` ran eager ``mkdir``s,
        and that block was removed on purpose: one unusable stored value bricked
        every command. Each store is now created where it is first used, and
        ``test_paths.py::TestLoadStdPaths::test_resolves_without_creating`` pins
        that resolving creates nothing.
        """
        from kanibako.cli import _ensure_initialized
        from kanibako.launch.templates import (
            AGENT_MOULD_DIRNAME,
            PACKAGED_BOX_TEMPLATE,
            PACKAGED_WORKSET_TEMPLATE,
        )
        from kanibako.settings.config import load_config, user_config_file
        from kanibako.settings.paths import load_std_paths

        cf = user_config_file()
        # Anti-vacuity: nothing is on disk before the verb runs, so every
        # presence below was put there by init.
        assert not cf.exists()

        _ensure_initialized()

        assert cf.is_file()
        std = load_std_paths(load_config(cf))
        # The paths come from the resolved keys, not from hard-coded leaves, so
        # this follows the directories the user's config actually names.
        for mould in (PACKAGED_BOX_TEMPLATE, PACKAGED_WORKSET_TEMPLATE,
                      AGENT_MOULD_DIRNAME):
            assert (std.template / mould).is_dir(), mould
        assert (std.agents / "default").is_dir()
        assert (std.canon / "handbook").is_dir()
        # The stamp copies files, not just directories: a copy that silently
        # found no packaged source would still leave these directories behind.
        assert any(p.is_file() for p in (std.template / PACKAGED_BOX_TEMPLATE).rglob("*"))
        assert any(p.is_file() for p in (std.canon / "handbook").rglob("*"))

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

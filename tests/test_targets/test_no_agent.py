"""Tests for NoAgentTarget."""

from __future__ import annotations

from pathlib import Path

from kanibako.targets.no_agent import NoAgentTarget


class TestNoAgentTarget:
    def setup_method(self):
        self.target = NoAgentTarget()

    def test_name(self):
        assert self.target.name == "no_agent"

    def test_display_name(self):
        assert self.target.display_name == "Shell"

    def test_has_binary_false(self):
        assert self.target.has_binary is False

    def test_detect_returns_none(self):
        assert self.target.detect() is None

    def test_binary_mounts_empty(self):
        assert self.target.binary_mounts(None) == []

    def test_init_home_is_noop(self, tmp_path):
        """init_home should not create any files."""
        home = tmp_path / "shell"
        home.mkdir()
        self.target.init_home(home)
        # Only the dir we created should exist
        assert list(home.iterdir()) == []

    def test_init_home_distinct_auth(self, tmp_path):
        home = tmp_path / "shell"
        home.mkdir()
        self.target.init_home(home, auth="distinct")
        assert list(home.iterdir()) == []

    def test_refresh_credentials_is_noop(self, tmp_path):
        self.target.refresh_credentials(tmp_path)

    def test_writeback_credentials_is_noop(self, tmp_path):
        self.target.writeback_credentials(tmp_path)

    def test_build_cli_args_empty(self):
        result = self.target.build_cli_args(
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=["--foo"],
        )
        assert result == []

    def test_check_auth_returns_true(self):
        assert self.target.check_auth() is True

    def test_resource_mappings_empty(self):
        assert self.target.resource_mappings() == []

    def test_apply_state_returns_empty(self):
        cli_args, env_vars = self.target.apply_state({"model": "opus"})
        assert cli_args == []
        assert env_vars == {}

    def test_generate_agent_config(self):
        cfg = self.target.generate_agent_config()
        assert cfg.name == "Shell"
        assert cfg.shell == "standard"
        assert cfg.default_args == []
        assert cfg.state == {}
        assert cfg.shared_caches == {}


class TestNoAgentTargetImport:
    def test_importable_from_package(self):
        from kanibako.targets import NoAgentTarget
        t = NoAgentTarget()
        assert t.name == "no_agent"

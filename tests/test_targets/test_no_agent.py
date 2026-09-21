"""Tests for NoAgentTarget."""

from __future__ import annotations

from kanibako.targets.no_agent import NoAgentTarget


class TestNoAgentTarget:
    def setup_method(self):
        self.target = NoAgentTarget()

    def test_name(self):
        # ⚑ ``shell`` — the §2d pseudo-agent's OWN slot ([R174]/[R175], Q22).
        # The class and module keep their names (a different-sized rename,
        # owned by the scrub row); the registry/store/cascade spelling is what
        # moved.
        assert self.target.name == "shell"

    def test_display_name(self):
        assert self.target.display_name == "Shell"

    def test_has_binary_false(self):
        assert self.target.has_binary is False

    def test_detect_returns_none(self):
        assert self.target.detect() is None

    def test_refresh_credentials_is_noop(self, tmp_path):
        self.target.refresh_credentials(tmp_path)

    def test_writeback_credentials_is_noop(self, tmp_path):
        self.target.writeback_credentials(tmp_path)

    def test_check_auth_returns_true(self):
        assert self.target.check_auth() is True

    def test_default_shares_empty(self):
        assert self.target.default_common() == {}

    def test_generate_agent_config(self):
        # ⚑ EMPTY (D8b): the per-agent settings file holds user intent only, and the
        # ``name="Shell"`` this used to carry was not a settings key.  The FILE declares
        # no ``label`` either; the box reads the shell tier floor's ``agent.shell.label``.
        cfg = self.target.generate_agent_config()
        assert cfg.run_args is None
        assert cfg.state == {}


class TestNoAgentTargetImport:
    def test_importable_from_package(self):
        from kanibako.targets import NoAgentTarget
        t = NoAgentTarget()
        assert t.name == "shell"

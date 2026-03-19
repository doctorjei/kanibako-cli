"""Tests for target discovery and resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.targets import discover_targets, get_target, resolve_target
from kanibako.targets.base import AgentInstall, Target
from kanibako.targets.no_agent import NoAgentTarget


class _FakeTarget(Target):
    """Minimal concrete Target for testing."""

    _detect_result: AgentInstall | None = None

    @property
    def name(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake Agent"

    def detect(self):
        return self._detect_result

    def binary_mounts(self, install):
        return []

    def init_home(self, home):
        pass

    def refresh_credentials(self, home):
        pass

    def writeback_credentials(self, home):
        pass

    def build_cli_args(self, **kwargs):
        return []


class _DetectableTarget(_FakeTarget):
    """Target whose detect() returns a valid install."""

    @property
    def name(self) -> str:
        return "detectable"

    @property
    def display_name(self) -> str:
        return "Detectable Agent"

    def detect(self):
        return AgentInstall(name="detectable", binary=Path("/bin/x"), install_dir=Path("/opt/x"))


def _mock_entry_point(name: str, cls: type) -> MagicMock:
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = cls
    return ep


class TestDiscoverTargets:
    def test_discovers_registered_targets(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            targets = discover_targets()
        assert "fake" in targets
        assert targets["fake"] is _FakeTarget

    def test_empty_when_no_targets(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert targets == {}

    def test_multiple_targets(self):
        ep1 = _mock_entry_point("a", _FakeTarget)
        ep2 = _mock_entry_point("b", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep1, ep2]):
            targets = discover_targets()
        assert len(targets) == 2
        assert "a" in targets
        assert "b" in targets


class TestGetTarget:
    def test_found(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            cls = get_target("fake")
        assert cls is _FakeTarget

    def test_not_found(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            with pytest.raises(KeyError, match="Unknown target 'nope'"):
                get_target("nope")


class TestResolveTarget:
    def test_resolve_by_name(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            t = resolve_target("fake")
        assert isinstance(t, _FakeTarget)

    def test_resolve_by_name_not_found(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            with pytest.raises(KeyError):
                resolve_target("missing")

    def test_auto_detect(self):
        ep = _mock_entry_point("detectable", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            t = resolve_target()
        assert isinstance(t, _DetectableTarget)

    def test_auto_detect_skips_undetectable(self):
        ep1 = _mock_entry_point("fake", _FakeTarget)
        ep2 = _mock_entry_point("detectable", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep1, ep2]):
            t = resolve_target()
        assert isinstance(t, _DetectableTarget)

    def test_auto_detect_none_found_returns_no_agent(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            t = resolve_target()
        assert isinstance(t, NoAgentTarget)

    def test_auto_detect_empty_returns_no_agent(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            t = resolve_target()
        assert isinstance(t, NoAgentTarget)


# ── Helpers for file-drop plugin tests ──────────────────────────────

_PLUGIN_SOURCE = '''\
from kanibako.targets.base import Target


class MyFilePlugin(Target):
    @property
    def name(self):
        return "{name}"

    @property
    def display_name(self):
        return "File Plugin {name}"

    def detect(self):
        return None

    def binary_mounts(self, install):
        return []

    def init_home(self, home):
        pass

    def refresh_credentials(self, home):
        pass

    def writeback_credentials(self, home):
        pass

    def build_cli_args(self, **kwargs):
        return []
'''


def _write_plugin(directory: Path, filename: str, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(_PLUGIN_SOURCE.format(name=name))


class TestDirectoryPluginDiscovery:
    """Tests for file-drop plugin directories."""

    def test_discover_user_dir_plugins(self, tmp_path, monkeypatch):
        """Plugins in user data dir are discovered."""
        user_plugins = tmp_path / "kanibako" / "plugins"
        _write_plugin(user_plugins, "myplugin.py", "myplugin")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert "myplugin" in targets

    def test_discover_project_dir_plugins(self, tmp_path):
        """Plugins in project .kanibako/plugins/ are discovered."""
        proj = tmp_path / "myproject"
        proj_plugins = proj / ".kanibako" / "plugins"
        _write_plugin(proj_plugins, "projplugin.py", "projplugin")

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets(project_path=proj)
        assert "projplugin" in targets

    def test_project_plugin_overrides_user_plugin(self, tmp_path, monkeypatch):
        """Project-level plugin overrides user-level with same name."""
        # User plugin named "shared" from user_shared.py
        user_plugins = tmp_path / "data" / "kanibako" / "plugins"
        _write_plugin(user_plugins, "user_shared.py", "shared")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        # Project plugin also named "shared" from proj_shared.py
        proj = tmp_path / "project"
        proj_plugins = proj / ".kanibako" / "plugins"
        _write_plugin(proj_plugins, "proj_shared.py", "shared")

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets(project_path=proj)

        assert "shared" in targets
        # The class should come from the project dir, not user dir.
        # Different filenames produce different module names.
        cls = targets["shared"]
        assert cls.__module__ == "kanibako_plugin_proj_shared"

    def test_underscore_files_skipped(self, tmp_path, monkeypatch):
        """Files starting with _ are not loaded as plugins."""
        user_plugins = tmp_path / "kanibako" / "plugins"
        _write_plugin(user_plugins, "_private.py", "private")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert "private" not in targets

    def test_invalid_plugin_gracefully_handled(self, tmp_path, monkeypatch):
        """Invalid Python files don't crash discovery."""
        user_plugins = tmp_path / "kanibako" / "plugins"
        user_plugins.mkdir(parents=True)
        (user_plugins / "broken.py").write_text("raise RuntimeError('boom')")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        with patch("kanibako.targets.entry_points", return_value=[]):
            # Should not raise
            targets = discover_targets()
        assert "broken" not in targets

    def test_discover_targets_default_no_project(self):
        """discover_targets() without project_path works (backward compat)."""
        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        # Should not raise; may be empty or contain module-scanned targets
        assert isinstance(targets, dict)

    def test_nonexistent_directory_is_ignored(self, tmp_path, monkeypatch):
        """Nonexistent plugin directories are silently skipped."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nonexistent"))

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert isinstance(targets, dict)

    def test_get_target_with_project_path(self, tmp_path):
        """get_target accepts project_path parameter."""
        proj = tmp_path / "proj"
        proj_plugins = proj / ".kanibako" / "plugins"
        _write_plugin(proj_plugins, "custom.py", "custom")

        with patch("kanibako.targets.entry_points", return_value=[]):
            cls = get_target("custom", project_path=proj)
        assert cls is not None

    def test_resolve_target_with_project_path(self, tmp_path):
        """resolve_target passes project_path through."""
        proj = tmp_path / "proj"
        proj_plugins = proj / ".kanibako" / "plugins"
        _write_plugin(proj_plugins, "myplugin.py", "myplugin")

        with patch("kanibako.targets.entry_points", return_value=[]):
            # resolve by name
            t = resolve_target("myplugin", project_path=proj)
        assert t.name == "myplugin"

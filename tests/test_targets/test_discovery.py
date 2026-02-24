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

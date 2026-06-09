"""Tests for target base classes: Mount, AgentInstall, Target ABC."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.targets.base import AgentInstall, Mount, ResourceMapping, ResourceScope, Target


class TestResourceScope:
    def test_enum_values(self):
        assert ResourceScope.SHARED.value == "shared"
        assert ResourceScope.PROJECT.value == "project"
        assert ResourceScope.SEEDED.value == "seeded"


class TestResourceMapping:
    def test_fields(self):
        rm = ResourceMapping(
            path="plugins/",
            scope=ResourceScope.SHARED,
            description="Plugin binaries and registry",
        )
        assert rm.path == "plugins/"
        assert rm.scope == ResourceScope.SHARED
        assert rm.description == "Plugin binaries and registry"

    def test_frozen(self):
        rm = ResourceMapping(
            path="plugins/",
            scope=ResourceScope.SHARED,
            description="test",
        )
        with pytest.raises(AttributeError):
            rm.path = "other/"  # type: ignore[misc]

    def test_no_description(self):
        rm = ResourceMapping(path="cache/", scope=ResourceScope.SHARED)
        assert rm.description == ""


class TestMount:
    def test_to_volume_arg_simple(self):
        m = Mount(source=Path("/host/dir"), destination="/container/dir")
        assert m.to_volume_arg() == "/host/dir:/container/dir"

    def test_to_volume_arg_with_options(self):
        m = Mount(source=Path("/host/dir"), destination="/container/dir", options="ro")
        assert m.to_volume_arg() == "/host/dir:/container/dir:ro"

    def test_to_volume_arg_complex_options(self):
        m = Mount(source=Path("/a"), destination="/b", options="Z,U")
        assert m.to_volume_arg() == "/a:/b:Z,U"

    def test_frozen(self):
        m = Mount(source=Path("/a"), destination="/b")
        with pytest.raises(AttributeError):
            m.source = Path("/c")  # type: ignore[misc]


class TestAgentInstall:
    def test_fields(self):
        ai = AgentInstall(
            name="claude",
            binary=Path("/usr/bin/claude"),
            install_dir=Path("/opt/claude"),
        )
        assert ai.name == "claude"
        assert ai.binary == Path("/usr/bin/claude")
        assert ai.install_dir == Path("/opt/claude")


class TestTargetABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            Target()  # type: ignore[abstract]

    def test_concrete_subclass(self):
        class DummyTarget(Target):
            @property
            def name(self) -> str:
                return "dummy"

            @property
            def display_name(self) -> str:
                return "Dummy Agent"

            def detect(self):
                return None

            def binary_mounts(self, install):
                return []

            def init_home(self, home, *, group_auth=True):
                pass

            def refresh_credentials(self, home):
                pass

            def writeback_credentials(self, home):
                pass

            def build_cli_args(self, **kwargs):
                return []

        t = DummyTarget()
        assert t.name == "dummy"
        assert t.display_name == "Dummy Agent"
        assert t.detect() is None
        assert t.binary_mounts(None) == []
        assert t.check_auth() is True  # default no-op returns True
        assert t.resource_mappings() == []

    def test_default_resource_mappings(self):
        """Default resource_mappings returns empty list."""

        class MinimalTarget(Target):
            @property
            def name(self) -> str:
                return "minimal"

            @property
            def display_name(self) -> str:
                return "Minimal"

            def detect(self):
                return None

            def binary_mounts(self, install):
                return []

            def init_home(self, home, *, group_auth=True):
                pass

            def refresh_credentials(self, home):
                pass

            def writeback_credentials(self, home):
                pass

            def build_cli_args(self, **kwargs):
                return []

        t = MinimalTarget()
        assert t.resource_mappings() == []

    def test_abstract_methods_enforced(self):
        """Target subclass missing abstract methods cannot be instantiated."""

        class IncompleteTarget(Target):
            @property
            def name(self):
                return "x"

            @property
            def display_name(self):
                return "X"

        with pytest.raises(TypeError):
            IncompleteTarget()  # type: ignore[abstract]


class TestGenerateCrabConfig:
    """Tests for Target.generate_crab_config() default implementation."""

    def test_default_returns_crab_config(self):
        class SimpleTarget(Target):
            @property
            def name(self) -> str:
                return "simple"

            @property
            def display_name(self) -> str:
                return "Simple Agent"

            def detect(self):
                return None

            def binary_mounts(self, install):
                return []

            def init_home(self, home, *, group_auth=True):
                pass

            def refresh_credentials(self, home):
                pass

            def writeback_credentials(self, home):
                pass

            def build_cli_args(self, **kwargs):
                return []

        t = SimpleTarget()
        cfg = t.generate_crab_config()
        assert cfg.name == "Simple Agent"
        assert cfg.shell == "standard"
        assert cfg.run_args == []
        assert cfg.state == {}
        assert cfg.shared_caches == {}


class TestApplyState:
    """Tests for Target.apply_state() default implementation."""

    def test_default_returns_empty(self):
        class SimpleTarget(Target):
            @property
            def name(self) -> str:
                return "simple"

            @property
            def display_name(self) -> str:
                return "Simple Agent"

            def detect(self):
                return None

            def binary_mounts(self, install):
                return []

            def init_home(self, home, *, group_auth=True):
                pass

            def refresh_credentials(self, home):
                pass

            def writeback_credentials(self, home):
                pass

            def build_cli_args(self, **kwargs):
                return []

        t = SimpleTarget()
        cli_args, env_vars = t.apply_state({"model": "opus"})
        assert cli_args == []
        assert env_vars == {}


class TestPublicExports:
    def test_resource_types_importable_from_package(self):
        from kanibako.targets import ResourceMapping, ResourceScope
        assert ResourceScope.SHARED.value == "shared"
        rm = ResourceMapping(path="x", scope=ResourceScope.PROJECT)
        assert rm.path == "x"

"""Tests for target base classes: Mount, AgentInstall, Target ABC."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    Binding,
    BindScope,
    Cadence,
    Channel,
    CredFileSpec,
    HostSrcOrigin,
    Mount,
    Operation,
    PluginDescriptor,
    ResourceMapping,
    ResourceScope,
    SafeBypass,
    SettingArg,
    Target,
    _validate_agent_binary,
)


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


class TestValidateAgentBinary:
    """Tests for the shared _validate_agent_binary launch/diagnose helper."""

    def test_valid_executable_returns_none(self, tmp_path):
        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\nexec real \"$@\"\n")
        binary.chmod(0o755)
        assert _validate_agent_binary(binary) is None

    def test_native_binary_returns_none(self, tmp_path):
        """A non-zero executable with ELF-like (non-NUL) leading bytes is fine."""
        binary = tmp_path / "claude"
        binary.write_bytes(b"\x7fELF\x02\x01\x01\x00rest-of-binary")
        binary.chmod(0o755)
        assert _validate_agent_binary(binary) is None

    def test_zero_byte_returns_reason(self, tmp_path):
        binary = tmp_path / "claude"
        binary.touch()  # 0 bytes
        binary.chmod(0o755)
        reason = _validate_agent_binary(binary)
        assert reason is not None
        assert "0 bytes" in reason
        assert str(binary) in reason

    def test_non_executable_returns_reason(self, tmp_path):
        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o644)  # no exec bit
        reason = _validate_agent_binary(binary)
        assert reason is not None
        assert "not executable" in reason
        assert str(binary) in reason

    def test_missing_returns_reason(self, tmp_path):
        binary = tmp_path / "nope"
        reason = _validate_agent_binary(binary)
        assert reason is not None
        assert "not found" in reason

    def test_all_nul_leading_bytes_returns_reason(self, tmp_path):
        """Non-empty but truncated/corrupt (all-NUL head) -> rejected, no ELF needed."""
        binary = tmp_path / "claude"
        binary.write_bytes(b"\x00\x00\x00\x00")
        binary.chmod(0o755)
        reason = _validate_agent_binary(binary)
        assert reason is not None
        assert "corrupt" in reason


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

    def test_default_seeds(self):
        """Default default_seeds() returns empty dict (no seeds)."""

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
        assert t.default_seeds() == {}

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


class TestResourceMappingBase:
    """The additive `base` anchor field on ResourceMapping."""

    def test_base_defaults_empty(self):
        rm = ResourceMapping(path="plugins/", scope=ResourceScope.SHARED)
        assert rm.base == ""

    def test_base_preserved_when_set(self):
        rm = ResourceMapping(
            path="sessions",
            scope=ResourceScope.PROJECT,
            base=".local/share/goose",
        )
        assert rm.base == ".local/share/goose"
        assert rm.path == "sessions"


class TestPluginDescriptorDataclasses:
    """Construction, defaults, and frozen-ness of the declarative descriptor vocabulary."""

    def test_enum_values(self):
        assert BindKind.FILE.value == "file"
        assert BindKind.DIR.value == "dir"
        assert HostSrcOrigin.LAUNCHER.value == "launcher"
        assert HostSrcOrigin.SHARED_STORE.value == "shared_store"
        assert HostSrcOrigin.LITERAL.value == "literal"
        assert BindScope.AGENT_CRITICAL.value == "agent_critical"
        assert BindScope.AGENT.value == "agent"
        assert Channel.FLAG.value == "flag"
        assert Channel.ENV.value == "env"
        assert Cadence.SYNC.value == "sync"
        assert Cadence.SEED_ONCE.value == "seed_once"

    def test_binding_required_fields_and_defaults(self):
        b = Binding(
            key="binary",
            origin=HostSrcOrigin.BINARY,
            box_dest="/usr/local/bin/claude",
            kind=BindKind.FILE,
            scope=BindScope.AGENT_CRITICAL,
        )
        assert b.key == "binary"
        assert b.origin is HostSrcOrigin.BINARY
        assert b.box_dest == "/usr/local/bin/claude"
        assert b.kind is BindKind.FILE
        assert b.scope is BindScope.AGENT_CRITICAL
        # defaults
        assert b.ro is True
        assert b.src_rel == ""
        assert b.literal_src is None

    def test_binding_frozen(self):
        b = Binding(
            key="binary",
            origin=HostSrcOrigin.BINARY,
            box_dest="/usr/local/bin/claude",
            kind=BindKind.FILE,
            scope=BindScope.AGENT_CRITICAL,
        )
        with pytest.raises(FrozenInstanceError):
            b.ro = False  # type: ignore[misc]

    def test_setting_arg_flag_and_env(self):
        flag = SettingArg(setting_key="model", channel=Channel.FLAG, flag=("--model",))
        assert flag.flag == ("--model",)
        assert flag.env_var == ""
        env = SettingArg(setting_key="model", channel=Channel.ENV, env_var="GOOSE_MODEL")
        assert env.env_var == "GOOSE_MODEL"
        assert env.flag == ()

    def test_safe_bypass_defaults(self):
        sb = SafeBypass(channel=Channel.FLAG, flag=("--dangerously-skip-permissions",))
        assert sb.flag == ("--dangerously-skip-permissions",)
        assert sb.env_var == ""
        assert sb.setting_key == ""

    def test_operation_fragment(self):
        op = Operation(fragment=("--print",))
        assert op.fragment == ("--print",)
        with pytest.raises(FrozenInstanceError):
            op.fragment = ("x",)  # type: ignore[misc]

    def test_cred_file_spec_defaults(self):
        cf = CredFileSpec(
            home_rel=".claude/.credentials.json",
            host_rel=".claude/.credentials.json",
        )
        assert cf.cadence is Cadence.SYNC
        assert cf.mtime_gate is True
        assert cf.filtered is False

    def test_plugin_descriptor_minimal_defaults(self):
        d = PluginDescriptor(
            command=("claude",),
            bindings=(
                Binding(
                    key="binary",
                    origin=HostSrcOrigin.BINARY,
                    box_dest="/usr/local/bin/claude",
                    kind=BindKind.FILE,
                    scope=BindScope.AGENT_CRITICAL,
                ),
            ),
            mode={"start": (), "continue": ("--continue",)},
        )
        assert d.operations == {}
        assert d.safe_bypass is None
        assert d.settings == ()
        assert d.container_env == {}
        assert d.cred_files == ()
        assert d.host_prep is False
        assert d.init_dirs == ()

    def test_plugin_descriptor_claude_column(self):
        """A descriptor mirroring the validated claude column."""
        d = PluginDescriptor(
            command=("claude",),
            bindings=(
                Binding(
                    key="launcher",
                    origin=HostSrcOrigin.LAUNCHER,
                    box_dest="/usr/local/bin/claude",
                    kind=BindKind.FILE,
                    scope=BindScope.AGENT_CRITICAL,
                ),
            ),
            mode={"start": (), "continue": ("--continue",)},
            operations={"exec": Operation(fragment=("--print",))},
            safe_bypass=SafeBypass(
                channel=Channel.FLAG,
                flag=("--dangerously-skip-permissions",),
                setting_key="access",
            ),
            settings=(SettingArg(setting_key="model", channel=Channel.FLAG, flag=("--model",)),),
            cred_files=(
                CredFileSpec(
                    home_rel=".claude/.credentials.json",
                    host_rel=".claude/.credentials.json",
                    cadence=Cadence.SYNC,
                ),
                CredFileSpec(
                    home_rel=".claude/settings.json",
                    host_rel=".claude/settings.json",
                    cadence=Cadence.SEED_ONCE,
                ),
            ),
            init_dirs=(".claude",),
        )
        assert d.command == ("claude",)
        assert len(d.bindings) == 1
        assert d.bindings[0].scope is BindScope.AGENT_CRITICAL
        assert d.mode["continue"] == ("--continue",)
        assert d.operations["exec"].fragment == ("--print",)
        assert d.safe_bypass is not None and d.safe_bypass.setting_key == "access"
        assert d.cred_files[0].cadence is Cadence.SYNC
        assert d.cred_files[1].cadence is Cadence.SEED_ONCE
        assert d.init_dirs == (".claude",)

    def test_plugin_descriptor_frozen(self):
        d = PluginDescriptor(
            command=("claude",),
            bindings=(
                Binding(
                    key="binary",
                    origin=HostSrcOrigin.BINARY,
                    box_dest="/usr/local/bin/claude",
                    kind=BindKind.FILE,
                    scope=BindScope.AGENT_CRITICAL,
                ),
            ),
            mode={"start": ()},
        )
        with pytest.raises(FrozenInstanceError):
            d.host_prep = True  # type: ignore[misc]

    def test_default_descriptor_is_none(self):
        class NoDescTarget(Target):
            @property
            def name(self) -> str:
                return "nd"

            @property
            def display_name(self) -> str:
                return "No Descriptor"

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

        assert NoDescTarget().descriptor is None

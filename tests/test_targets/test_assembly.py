"""Tests for kanibako.targets.assembly — declarative launch assembly.

Covers every public function plus the BindingSourceError safe-fail.  All assembly
helpers are pure (only Path.exists() touches the filesystem), so tests build
descriptors inline and use tmp_path for real bind sources.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.targets.assembly import (
    BindingSourceError,
    assemble_argv,
    assemble_env,
    descriptor_mounts,
    effective_safe_mode_off,
    entrypoint,
    resolve_binding_source,
    resolve_mode,
)
from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    Binding,
    BindScope,
    Channel,
    HostSrcOrigin,
    Mount,
    Operation,
    PluginDescriptor,
    SafeBypass,
    SettingArg,
)

# --------------------------------------------------------------------------- #
# Descriptor fixtures (claude- and goose-shaped)                              #
# --------------------------------------------------------------------------- #


def _claude_descriptor() -> PluginDescriptor:
    """A claude-shaped descriptor: FLAG safe-bypass + FLAG model setting."""
    return PluginDescriptor(
        command=("claude",),
        bindings=(),
        mode={
            "start": (),
            "continue": ("--continue",),
            "resume": ("--resume",),
        },
        operations={"exec": Operation(fragment=("--print",))},
        safe_bypass=SafeBypass(
            channel=Channel.FLAG,
            flag=("--dangerously-skip-permissions",),
            setting_key="access",
        ),
        settings=(SettingArg(setting_key="model", channel=Channel.FLAG, flag=("--model",)),),
        container_env={"DISABLE_AUTOUPDATER": "1"},
    )


def _goose_descriptor() -> PluginDescriptor:
    """A goose-shaped descriptor: ENV safe-bypass + ENV model setting."""
    return PluginDescriptor(
        command=("goose",),
        bindings=(),
        mode={
            "start": ("session",),
            "continue": ("session", "--resume"),
        },
        operations={"exec": Operation(fragment=("run", "-t"))},
        safe_bypass=SafeBypass(
            channel=Channel.ENV,
            env_var="GOOSE_MODE",
            env_value="auto",
        ),
        settings=(
            SettingArg(setting_key="model", channel=Channel.ENV, env_var="GOOSE_MODEL"),
        ),
        container_env={"GOOSE_DISABLE_KEYRING": "1"},
    )


def _bare_descriptor() -> PluginDescriptor:
    """A descriptor with only {start} — no continue, no resume picker."""
    return PluginDescriptor(
        command=("agent",),
        bindings=(),
        mode={"start": ()},
    )


# --------------------------------------------------------------------------- #
# resolve_mode                                                                #
# --------------------------------------------------------------------------- #

_FULL_MODES = ("start", "continue", "resume")


def test_resolve_mode_resume_when_available() -> None:
    assert (
        resolve_mode(
            resume_mode=True,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            available_modes=_FULL_MODES,
        )
        == "resume"
    )


def test_resolve_mode_resume_falls_to_start_when_no_picker() -> None:
    # Descriptor without a "resume" or "continue" key (bare {start}): -R has
    # nowhere to go and there is no continue mode -> start (step 4).
    assert (
        resolve_mode(
            resume_mode=True,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            available_modes=("start",),
        )
        == "start"
    )


def test_resolve_mode_resume_no_picker_with_continue_falls_to_continue() -> None:
    # Per the lifted claude algorithm: when "resume" is unavailable, resume_mode
    # does NOT force skip_continue, so an available "continue" still wins.
    assert (
        resolve_mode(
            resume_mode=True,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            available_modes=("start", "continue"),
        )
        == "continue"
    )


def test_resolve_mode_default_is_continue() -> None:
    assert (
        resolve_mode(
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            available_modes=_FULL_MODES,
        )
        == "continue"
    )


def test_resolve_mode_new_session_forces_start() -> None:
    assert (
        resolve_mode(
            resume_mode=False,
            new_session=True,
            is_new_project=False,
            extra_args=[],
            available_modes=_FULL_MODES,
        )
        == "start"
    )


def test_resolve_mode_new_project_forces_start() -> None:
    assert (
        resolve_mode(
            resume_mode=False,
            new_session=False,
            is_new_project=True,
            extra_args=[],
            available_modes=_FULL_MODES,
        )
        == "start"
    )


@pytest.mark.parametrize("flag", ["--resume", "-r"])
def test_resolve_mode_explicit_resume_in_extra_forces_start(flag: str) -> None:
    assert (
        resolve_mode(
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[flag],
            available_modes=_FULL_MODES,
        )
        == "start"
    )


def test_resolve_mode_bare_descriptor_always_start() -> None:
    bare = ("start",)
    for resume in (True, False):
        assert (
            resolve_mode(
                resume_mode=resume,
                new_session=False,
                is_new_project=False,
                extra_args=[],
                available_modes=bare,
            )
            == "start"
        )


# --------------------------------------------------------------------------- #
# effective_safe_mode_off                                                     #
# --------------------------------------------------------------------------- #


def test_safe_off_secure_is_false() -> None:
    assert effective_safe_mode_off(secure=True, autonomous=False) is False


def test_safe_off_autonomous_is_true() -> None:
    assert effective_safe_mode_off(secure=False, autonomous=True) is True


def test_safe_off_default_is_true() -> None:
    assert effective_safe_mode_off(secure=False, autonomous=False) is True


def test_safe_off_persisted_permissive_is_true() -> None:
    assert (
        effective_safe_mode_off(secure=False, autonomous=False, persisted_access="permissive")
        is True
    )


def test_safe_off_persisted_restricted_is_false() -> None:
    assert (
        effective_safe_mode_off(secure=False, autonomous=False, persisted_access="restricted")
        is False
    )


def test_safe_off_secure_beats_persisted_permissive() -> None:
    assert (
        effective_safe_mode_off(secure=True, autonomous=False, persisted_access="permissive")
        is False
    )


def test_safe_off_unknown_persisted_falls_to_default_true() -> None:
    assert (
        effective_safe_mode_off(secure=False, autonomous=False, persisted_access="bogus")
        is True
    )


# --------------------------------------------------------------------------- #
# entrypoint                                                                  #
# --------------------------------------------------------------------------- #


def test_entrypoint_returns_command_zero() -> None:
    assert entrypoint(_claude_descriptor()) == "claude"
    assert entrypoint(_goose_descriptor()) == "goose"


# --------------------------------------------------------------------------- #
# assemble_argv                                                               #
# --------------------------------------------------------------------------- #


def test_argv_claude_continue_safe_off_with_model_and_extra() -> None:
    d = _claude_descriptor()
    argv = assemble_argv(
        d,
        mode_key="continue",
        safe_mode_off=True,
        setting_values={"model": "opus"},
        extra_args=["--foo", "bar"],
    )
    # command[0] ("claude") excluded; mode, FLAG safe-bypass, FLAG model, extra.
    assert "claude" not in argv
    assert argv == [
        "--continue",
        "--dangerously-skip-permissions",
        "--model",
        "opus",
        "--foo",
        "bar",
    ]


def test_argv_claude_safe_on_omits_bypass_flag() -> None:
    d = _claude_descriptor()
    argv = assemble_argv(
        d,
        mode_key="continue",
        safe_mode_off=False,
        setting_values={},
        extra_args=[],
    )
    assert argv == ["--continue"]
    assert "--dangerously-skip-permissions" not in argv


def test_argv_model_absent_when_value_falsy() -> None:
    d = _claude_descriptor()
    argv = assemble_argv(
        d,
        mode_key="start",
        safe_mode_off=False,
        setting_values={"model": ""},
        extra_args=[],
    )
    # start mode = empty fragment, no model -> empty argv.
    assert argv == []


def test_argv_start_mode_empty_fragment() -> None:
    d = _claude_descriptor()
    argv = assemble_argv(
        d,
        mode_key="start",
        safe_mode_off=True,
        setting_values={},
        extra_args=["x"],
    )
    assert argv == ["--dangerously-skip-permissions", "x"]


def test_argv_goose_env_channels_not_in_argv() -> None:
    d = _goose_descriptor()
    argv = assemble_argv(
        d,
        mode_key="continue",
        safe_mode_off=True,
        setting_values={"model": "gpt-4o"},
        extra_args=[],
    )
    # ENV safe-bypass and ENV model never appear in argv; mode does.
    assert argv == ["session", "--resume"]
    assert "GOOSE_MODE" not in argv
    assert "GOOSE_MODEL" not in argv
    assert "gpt-4o" not in argv


def test_argv_op_path_uses_fragment_no_mode() -> None:
    d = _claude_descriptor()
    argv = assemble_argv(
        d,
        mode_key="continue",  # must be ignored when op is set
        safe_mode_off=True,
        setting_values={"model": "opus"},
        op="exec",
        extra_args=["hello"],
    )
    # op fragment replaces mode; safe-bypass + model + extra still apply.
    assert argv == ["--print", "--dangerously-skip-permissions", "--model", "opus", "hello"]
    assert "--continue" not in argv


def test_argv_goose_op_fragment() -> None:
    d = _goose_descriptor()
    argv = assemble_argv(
        d,
        mode_key=None,
        safe_mode_off=False,
        setting_values={},
        op="exec",
        extra_args=["do this"],
    )
    assert argv == ["run", "-t", "do this"]


def test_argv_flag_secure_emission_on_safe_on() -> None:
    # A FLAG safe-bypass with a secure_flag emits it on safe-ON (symmetric to
    # the OFF flag). Completes the model for a future FLAG agent whose default
    # is unsafe; no current consumer ships secure_flag.
    d = PluginDescriptor(
        command=("agent",),
        bindings=(),
        mode={"start": ()},
        safe_bypass=SafeBypass(
            channel=Channel.FLAG,
            flag=("--yolo",),
            secure_flag=("--ask-every-time",),
        ),
    )
    off = assemble_argv(d, mode_key="start", safe_mode_off=True, setting_values={}, extra_args=[])
    on = assemble_argv(d, mode_key="start", safe_mode_off=False, setting_values={}, extra_args=[])
    assert off == ["--yolo"]
    assert on == ["--ask-every-time"]


def test_argv_claude_safe_on_emits_nothing_when_secure_flag_empty() -> None:
    # claude leaves secure_flag empty -> nothing emitted on -S (default-safe).
    d = _claude_descriptor()
    on = assemble_argv(d, mode_key="continue", safe_mode_off=False, setting_values={}, extra_args=[])
    assert on == ["--continue"]
    assert "--dangerously-skip-permissions" not in on


def test_argv_command_tail_included_when_multi_element() -> None:
    d = PluginDescriptor(
        command=("npx", "claude"),
        bindings=(),
        mode={"start": ()},
    )
    argv = assemble_argv(
        d,
        mode_key="start",
        safe_mode_off=False,
        setting_values={},
        extra_args=[],
    )
    # command[0] ("npx") excluded; command[1:] ("claude") included.
    assert argv == ["claude"]


# --------------------------------------------------------------------------- #
# assemble_env                                                                #
# --------------------------------------------------------------------------- #


def test_env_base_container_env() -> None:
    d = _claude_descriptor()
    env = assemble_env(d, safe_mode_off=False, setting_values={})
    assert env == {"DISABLE_AUTOUPDATER": "1"}


def test_env_claude_flag_settings_not_in_env() -> None:
    d = _claude_descriptor()
    env = assemble_env(d, safe_mode_off=True, setting_values={"model": "opus"})
    # FLAG channels never land in env; only base container_env present.
    assert env == {"DISABLE_AUTOUPDATER": "1"}
    assert "model" not in env


def test_env_goose_safe_off_sets_goose_mode_auto() -> None:
    d = _goose_descriptor()
    env = assemble_env(d, safe_mode_off=True, setting_values={})
    assert env["GOOSE_MODE"] == "auto"


def test_env_goose_safe_on_omits_goose_mode() -> None:
    d = _goose_descriptor()
    env = assemble_env(d, safe_mode_off=False, setting_values={})
    assert "GOOSE_MODE" not in env
    assert env == {"GOOSE_DISABLE_KEYRING": "1"}


def test_env_goose_model_env_set() -> None:
    d = _goose_descriptor()
    env = assemble_env(d, safe_mode_off=False, setting_values={"model": "gpt-4o"})
    assert env["GOOSE_MODEL"] == "gpt-4o"


def test_env_goose_model_absent_when_value_falsy() -> None:
    d = _goose_descriptor()
    env = assemble_env(d, safe_mode_off=False, setting_values={"model": ""})
    assert "GOOSE_MODEL" not in env


def _claude_endpoint_descriptor() -> PluginDescriptor:
    """Claude descriptor mirroring the shipped one: FLAG model + ENV endpoint."""
    return PluginDescriptor(
        command=("claude",),
        bindings=(),
        mode={"start": (), "continue": ("--continue",)},
        settings=(
            SettingArg(setting_key="model", channel=Channel.FLAG, flag=("--model",)),
            SettingArg(
                setting_key="endpoint",
                channel=Channel.ENV,
                env_var="ANTHROPIC_BASE_URL",
            ),
        ),
        container_env={"DISABLE_AUTOUPDATER": "1"},
    )


def test_env_claude_endpoint_emits_base_url_when_set() -> None:
    # Block B: endpoint (ENV channel) → ANTHROPIC_BASE_URL when the resolved
    # agent.<node>.endpoint is set (a rider pointing at an alternate endpoint).
    d = _claude_endpoint_descriptor()
    env = assemble_env(
        d, safe_mode_off=True,
        setting_values={"model": "opus", "endpoint": "http://localhost:8080"},
    )
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:8080"


def test_env_claude_endpoint_absent_when_none() -> None:
    # <None> case: unset endpoint (absent / empty) emits NO ANTHROPIC_BASE_URL —
    # bare claude is byte-identical to today. Mutation check: the ONLY difference
    # vs the set case is the env key, so its absence here is non-vacuous.
    d = _claude_endpoint_descriptor()
    env_absent = assemble_env(d, safe_mode_off=True, setting_values={"model": "opus"})
    env_empty = assemble_env(
        d, safe_mode_off=True, setting_values={"model": "opus", "endpoint": ""},
    )
    assert "ANTHROPIC_BASE_URL" not in env_absent
    assert "ANTHROPIC_BASE_URL" not in env_empty
    # Bare env is exactly the base container_env — no endpoint leakage.
    assert env_absent == {"DISABLE_AUTOUPDATER": "1"}
    assert env_empty == {"DISABLE_AUTOUPDATER": "1"}


def test_argv_claude_endpoint_never_in_argv() -> None:
    # endpoint is ENV-only; it must never appear on the argv even when set.
    from kanibako.targets.assembly import assemble_argv

    d = _claude_endpoint_descriptor()
    argv = assemble_argv(
        d, mode_key="start", safe_mode_off=True,
        setting_values={"model": "opus", "endpoint": "http://localhost:8080"},
        op=None, extra_args=[],
    )
    assert "http://localhost:8080" not in argv
    assert "ANTHROPIC_BASE_URL" not in argv
    # model FLAG still emits normally.
    assert "--model" in argv and "opus" in argv


def test_env_goose_secure_emits_goose_mode_approve_on_safe_on() -> None:
    # The A1 fix: an ENV safe-bypass with secure_env_value emits the restrictive
    # value on safe-ON (goose GOOSE_MODE=approve), because goose's unset default
    # is itself "auto" (unsafe). Safe-OFF still emits the OFF value ("auto").
    d = PluginDescriptor(
        command=("goose",),
        bindings=(),
        mode={"start": ("session",)},
        safe_bypass=SafeBypass(
            channel=Channel.ENV,
            env_var="GOOSE_MODE",
            env_value="auto",
            secure_env_value="approve",
        ),
    )
    env_off = assemble_env(d, safe_mode_off=True, setting_values={})
    env_on = assemble_env(d, safe_mode_off=False, setting_values={})
    assert env_off["GOOSE_MODE"] == "auto"
    assert env_on["GOOSE_MODE"] == "approve"


def test_env_secure_emits_nothing_when_secure_env_value_empty() -> None:
    # claude/codex shape: ENV safe-bypass (or none) with no secure_env_value ->
    # nothing emitted on safe-ON. The goose fixture here has empty secure value.
    d = _goose_descriptor()  # env_value="auto", secure_env_value="" (default)
    env_on = assemble_env(d, safe_mode_off=False, setting_values={})
    assert "GOOSE_MODE" not in env_on
    assert env_on == {"GOOSE_DISABLE_KEYRING": "1"}


def test_env_safe_bypass_uses_default_auto_when_env_value_empty() -> None:
    d = PluginDescriptor(
        command=("x",),
        bindings=(),
        mode={"start": ()},
        safe_bypass=SafeBypass(channel=Channel.ENV, env_var="X_MODE"),  # no env_value
    )
    env = assemble_env(d, safe_mode_off=True, setting_values={})
    assert env["X_MODE"] == "auto"


# --------------------------------------------------------------------------- #
# resolve_binding_source                                                      #
# --------------------------------------------------------------------------- #


def _install(tmp_path: Path) -> AgentInstall:
    return AgentInstall(
        name="agent",
        binary=tmp_path / "bin" / "agent",
        install_dir=tmp_path / "install",
        launcher=tmp_path / "launcher" / "agent",
    )


def _binding(origin: HostSrcOrigin, **kw: object) -> Binding:
    defaults: dict[str, object] = {
        "key": "k",
        "origin": origin,
        "box_dest": "/box/dest",
        "kind": BindKind.FILE,
        "scope": BindScope.AGENT_CRITICAL,
    }
    defaults.update(kw)
    return Binding(**defaults)  # type: ignore[arg-type]


def test_resolve_source_launcher(tmp_path: Path) -> None:
    install = _install(tmp_path)
    b = _binding(HostSrcOrigin.LAUNCHER)
    assert resolve_binding_source(b, install) == install.launcher


def test_resolve_source_launcher_falls_back_to_binary(tmp_path: Path) -> None:
    install = AgentInstall(
        name="agent",
        binary=tmp_path / "bin" / "agent",
        install_dir=tmp_path / "install",
        launcher=None,
    )
    b = _binding(HostSrcOrigin.LAUNCHER)
    assert resolve_binding_source(b, install) == install.binary


def test_resolve_source_install_dir(tmp_path: Path) -> None:
    install = _install(tmp_path)
    b = _binding(HostSrcOrigin.INSTALL_DIR)
    assert resolve_binding_source(b, install) == install.install_dir


def test_resolve_source_binary(tmp_path: Path) -> None:
    install = _install(tmp_path)
    b = _binding(HostSrcOrigin.BINARY)
    assert resolve_binding_source(b, install) == install.binary


def test_resolve_source_literal(tmp_path: Path) -> None:
    install = _install(tmp_path)
    lit = tmp_path / "literal" / "thing"
    b = _binding(HostSrcOrigin.LITERAL, literal_src=lit)
    assert resolve_binding_source(b, install) == lit


def test_resolve_source_override_wins(tmp_path: Path) -> None:
    install = _install(tmp_path)
    b = _binding(HostSrcOrigin.BINARY)
    assert resolve_binding_source(b, install, override="/custom/path") == Path("/custom/path")


# --------------------------------------------------------------------------- #
# descriptor_mounts                                                           #
# --------------------------------------------------------------------------- #


def test_mounts_agent_critical_existing_src_yields_ro_mount(tmp_path: Path) -> None:
    src = tmp_path / "agent"
    src.write_text("#!/bin/sh\n")
    install = AgentInstall(name="a", binary=src, install_dir=tmp_path, launcher=None)
    d = PluginDescriptor(
        command=("a",),
        bindings=(
            Binding(
                key="binary",
                origin=HostSrcOrigin.BINARY,
                box_dest="/usr/local/bin/agent",
                kind=BindKind.FILE,
                scope=BindScope.AGENT_CRITICAL,
                ro=True,
            ),
        ),
        mode={"start": ()},
    )
    mounts = descriptor_mounts(d, install)
    assert mounts == [Mount(src, "/usr/local/bin/agent", "ro")]


def test_mounts_agent_critical_missing_src_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    install = AgentInstall(name="a", binary=missing, install_dir=tmp_path, launcher=None)
    d = PluginDescriptor(
        command=("a",),
        bindings=(
            Binding(
                key="binary",
                origin=HostSrcOrigin.BINARY,
                box_dest="/usr/local/bin/agent",
                kind=BindKind.FILE,
                scope=BindScope.AGENT_CRITICAL,
            ),
        ),
        mode={"start": ()},
    )
    with pytest.raises(BindingSourceError) as exc:
        descriptor_mounts(d, install)
    assert "binary" in str(exc.value)


def test_mounts_agent_critical_unresolvable_src_raises(tmp_path: Path) -> None:
    install = _install(tmp_path)
    d = PluginDescriptor(
        command=("a",),
        bindings=(
            Binding(
                key="lit_thing",
                origin=HostSrcOrigin.LITERAL,
                literal_src=None,  # unresolvable
                box_dest="/box/x",
                kind=BindKind.DIR,
                scope=BindScope.AGENT_CRITICAL,
            ),
        ),
        mode={"start": ()},
    )
    # LITERAL with no literal_src -> None -> raises for AGENT_CRITICAL.
    with pytest.raises(BindingSourceError):
        descriptor_mounts(d, install)


def test_mounts_agent_missing_src_skipped(tmp_path: Path) -> None:
    missing = tmp_path / "store" / "plugins"  # exists=False for the share
    install = _install(tmp_path)
    d = PluginDescriptor(
        command=("a",),
        bindings=(
            Binding(
                key="plugins",
                origin=HostSrcOrigin.LITERAL,
                literal_src=missing,
                box_dest="/box/plugins",
                kind=BindKind.DIR,
                scope=BindScope.AGENT,
                ro=False,
            ),
        ),
        mode={"start": ()},
    )
    mounts = descriptor_mounts(d, install)
    assert mounts == []


def test_mounts_agent_existing_src_rw_option(tmp_path: Path) -> None:
    share = tmp_path / "store" / "plugins"
    share.mkdir(parents=True)
    install = _install(tmp_path)
    d = PluginDescriptor(
        command=("a",),
        bindings=(
            Binding(
                key="plugins",
                origin=HostSrcOrigin.LITERAL,
                literal_src=share,
                box_dest="/box/plugins",
                kind=BindKind.DIR,
                scope=BindScope.AGENT,
                ro=False,
            ),
        ),
        mode={"start": ()},
    )
    mounts = descriptor_mounts(d, install)
    assert mounts == [Mount(share, "/box/plugins", "")]


def test_mounts_order_preserved_and_override_applied(tmp_path: Path) -> None:
    bin_src = tmp_path / "agent"
    bin_src.write_text("x")
    override_src = tmp_path / "override_share"
    override_src.mkdir()
    install = AgentInstall(name="a", binary=bin_src, install_dir=tmp_path, launcher=None)
    d = PluginDescriptor(
        command=("a",),
        bindings=(
            Binding(
                key="binary",
                origin=HostSrcOrigin.BINARY,
                box_dest="/box/bin",
                kind=BindKind.FILE,
                scope=BindScope.AGENT_CRITICAL,
                ro=True,
            ),
            Binding(
                key="share",
                origin=HostSrcOrigin.LITERAL,
                literal_src=tmp_path / "unused",
                box_dest="/box/share",
                kind=BindKind.DIR,
                scope=BindScope.AGENT,
                ro=False,
            ),
        ),
        mode={"start": ()},
    )
    mounts = descriptor_mounts(d, install, overrides={"share": str(override_src)})
    assert mounts == [
        Mount(bin_src, "/box/bin", "ro"),
        Mount(override_src, "/box/share", ""),
    ]

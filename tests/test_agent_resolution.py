"""Tests for the cascade agent resolver + installed-count rule (W1 Phase C1)."""

from __future__ import annotations

import pytest

from kanibako.config import (
    config_file_path,
    load_config,
    resolve_agent,
    write_agent_setting,
)
from kanibako.errors import (
    AgentNotInstalledError,
    NoAgentInstalledError,
    NoAgentSelectedError,
)
from kanibako.install_method import (
    detect_install_method,
    install_command,
)
from kanibako.paths import load_std_paths, xdg

# Exact Gate-2a locked wording (must match resolve_agent verbatim).
GATE_2A = (
    "No agent selected; run 'kanibako setup' to select one or "
    "'kanibako shell' to access the container via command shell."
)


def _patch_targets(monkeypatch, names: list[str]) -> None:
    """Patch discover_targets (config.py lazily imports it from kanibako.targets)."""
    targets = {n: object for n in names}
    monkeypatch.setattr(
        "kanibako.targets.discover_targets", lambda *a, **k: dict(targets)
    )


def _no_default(monkeypatch) -> None:
    """Force the system-default tier to None (disk-light precedence tests)."""
    monkeypatch.setattr("kanibako.config.read_default_agent", lambda *a, **k: None)


# ---------------------------------------------------------------------------
# 1. Cascade precedence
# ---------------------------------------------------------------------------


def test_precedence_cascade(monkeypatch):
    # All candidate names installed so a resolved name validates.
    _patch_targets(monkeypatch, ["claude", "goose", "codex", "sysdef"])
    monkeypatch.setattr(
        "kanibako.config.read_default_agent", lambda *a, **k: "sysdef"
    )

    def res(**kw):
        base = dict(
            explicit_agent=None,
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
        base.update(kw)
        return resolve_agent(**base)

    # explicit beats box beats workset beats system-default.
    assert (
        res(explicit_agent="claude", box_agent_name="goose", workset_agent="codex")
        == "claude"
    )
    # box beats workset beats system-default (explicit absent).
    assert res(box_agent_name="goose", workset_agent="codex") == "goose"
    # workset beats system-default (explicit + box absent).
    assert res(workset_agent="codex") == "codex"
    # system-default wins when all higher tiers absent.
    assert res() == "sysdef"


# ---------------------------------------------------------------------------
# 2. Absent everywhere + exactly 1 installed
# ---------------------------------------------------------------------------


def test_single_installed_autopick(monkeypatch):
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(
            explicit_agent=None,
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
        == "claude"
    )


# ---------------------------------------------------------------------------
# 3. Absent everywhere + 0 installed -> Gate-2b (NoAgentInstalledError)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "envset,substring",
    [
        ({"PIPX_HOME": "/p"}, "pipx inject kanibako-cli kanibako-agent-claude"),
        ({"UV_TOOL_DIR": "/u"}, "uv tool install kanibako-cli --with kanibako-agent-claude"),
        ({}, "pip install kanibako-agent-claude"),
    ],
)
def test_zero_installed_gate2b(monkeypatch, envset, substring):
    _patch_targets(monkeypatch, [])
    _no_default(monkeypatch)
    for var in ("PIPX_HOME", "PIPX_BIN_DIR", "UV_TOOL_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.prefix", "/usr")
    monkeypatch.setattr("kanibako.install_method.is_externally_managed", lambda: False)
    for k, v in envset.items():
        monkeypatch.setenv(k, v)

    with pytest.raises(NoAgentInstalledError) as ei:
        resolve_agent(
            explicit_agent=None,
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
    msg = str(ei.value)
    assert "No agent plugins are installed" in msg
    assert substring in msg
    assert "Access via shell: kanibako shell" in msg


# ---------------------------------------------------------------------------
# 4. Absent everywhere + 2+ installed -> Gate-2a (NoAgentSelectedError)
# ---------------------------------------------------------------------------


def test_multi_installed_gate2a(monkeypatch):
    _patch_targets(monkeypatch, ["claude", "goose"])
    _no_default(monkeypatch)
    with pytest.raises(NoAgentSelectedError) as ei:
        resolve_agent(
            explicit_agent=None,
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
    assert str(ei.value) == GATE_2A


# ---------------------------------------------------------------------------
# 4b. Pseudo/catch-all agents (no_agent, general) excluded from installed-count
# ---------------------------------------------------------------------------


def test_one_real_plus_pseudo_autopicks_real(monkeypatch):
    # One real agent + the built-in shell fallback (and the catch-all label)
    # must be UNAMBIGUOUS — the real agent is auto-picked, not Gate-2a.
    _patch_targets(monkeypatch, ["claude", "no_agent", "general"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(
            explicit_agent=None,
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
        == "claude"
    )


def test_two_real_plus_pseudo_still_gate2a(monkeypatch):
    # Two real agents + a pseudo agent -> still ambiguous -> Gate-2a.
    _patch_targets(monkeypatch, ["claude", "goose", "no_agent"])
    _no_default(monkeypatch)
    with pytest.raises(NoAgentSelectedError):
        resolve_agent(
            explicit_agent=None,
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )


def test_only_pseudo_installed_gate2b(monkeypatch):
    # Zero REAL agents (only the pseudo/no_agent target) -> Gate-2b, NOT
    # "use no_agent".
    _patch_targets(monkeypatch, ["no_agent", "general"])
    _no_default(monkeypatch)
    for var in ("PIPX_HOME", "PIPX_BIN_DIR", "UV_TOOL_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.prefix", "/usr")
    monkeypatch.setattr("kanibako.install_method.is_externally_managed", lambda: False)
    with pytest.raises(NoAgentInstalledError):
        resolve_agent(
            explicit_agent=None,
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )


def test_explicit_pseudo_agent_still_selectable(monkeypatch):
    # A pseudo agent stays EXPLICITLY selectable (--agent no_agent), even though
    # it is excluded from the implicit count.
    _patch_targets(monkeypatch, ["claude", "no_agent"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(
            explicit_agent="no_agent",
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
        == "no_agent"
    )


# ---------------------------------------------------------------------------
# 5. Name resolves but adapter not installed -> AgentNotInstalledError
# ---------------------------------------------------------------------------


def test_resolved_name_not_installed(monkeypatch):
    _patch_targets(monkeypatch, ["goose"])  # claude NOT present
    _no_default(monkeypatch)
    monkeypatch.delenv("PIPX_HOME", raising=False)
    monkeypatch.delenv("PIPX_BIN_DIR", raising=False)
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setattr("sys.prefix", "/usr")
    monkeypatch.setattr("kanibako.install_method.is_externally_managed", lambda: False)
    with pytest.raises(AgentNotInstalledError) as ei:
        resolve_agent(
            explicit_agent=None,
            box_agent_name="claude",
            workset_agent=None,
            system_default_path=None,
        )
    msg = str(ei.value)
    assert "claude" in msg
    assert "pip install kanibako-agent-claude" in msg
    assert "kanibako agent list" in msg


# ---------------------------------------------------------------------------
# 5a. Persona refs (persona+harness) — Block A: validate the HARNESS, return NODE
# ---------------------------------------------------------------------------


def test_persona_explicit_returns_node_name(monkeypatch):
    # A persona ref validates the HARNESS (claude) ∈ installed, and RETURNS the
    # canonical node-name (persona℘harness), NOT the composite or the harness.
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(
            explicit_agent="navigator+claude",
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
        == "navigator℘claude"
    )


def test_persona_canonical_separator_accepted(monkeypatch):
    # The ℘ literal is accepted on input too and returns the same node.
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(
            explicit_agent="navigator℘claude",
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
        == "navigator℘claude"
    )


def test_persona_harness_not_installed_errors_on_harness(monkeypatch):
    # The composite persona is free-form; the error must name the HARNESS, not
    # the whole ref (the harness is what needs installing).
    _patch_targets(monkeypatch, ["goose"])  # claude NOT installed
    _no_default(monkeypatch)
    monkeypatch.delenv("PIPX_HOME", raising=False)
    monkeypatch.delenv("PIPX_BIN_DIR", raising=False)
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setattr("sys.prefix", "/usr")
    monkeypatch.setattr(
        "kanibako.install_method.is_externally_managed", lambda: False
    )
    with pytest.raises(AgentNotInstalledError) as ei:
        resolve_agent(
            explicit_agent="navigator+claude",
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
    msg = str(ei.value)
    # Names the harness for the install hint; does NOT leak the persona/node.
    assert "kanibako-agent-claude" in msg
    assert "navigator" not in msg


def test_bare_claude_unchanged_with_persona_support(monkeypatch):
    # BACKWARD-COMPAT: a bare ref still returns the bare name byte-for-byte.
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(
            explicit_agent="claude",
            box_agent_name=None,
            workset_agent=None,
            system_default_path=None,
        )
        == "claude"
    )


def test_persona_box_tier_canonicalized(monkeypatch):
    # A persona ref supplied at the BOX tier (not just explicit) is canonicalised.
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(
            explicit_agent=None,
            box_agent_name="navigator+claude",
            workset_agent=None,
            system_default_path=None,
        )
        == "navigator℘claude"
    )


# ---------------------------------------------------------------------------
# 5b. System-default tier round-trip through a real settings file
# ---------------------------------------------------------------------------


def test_system_default_real_file(tmp_home, config_file, monkeypatch):
    """Exercise read_default_agent against an on-disk system settings file."""
    _patch_targets(monkeypatch, ["claude"])
    cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    ssp = load_std_paths(load_config(cf)).settings
    # system.default_agent lives under agent.default.default_agent.
    write_agent_setting(ssp, "default_agent", "claude", "default")
    name = resolve_agent(
        explicit_agent=None,
        box_agent_name=None,
        workset_agent=None,
        system_default_path=ssp,
    )
    assert name == "claude"


# ---------------------------------------------------------------------------
# 6. detect_install_method + install_command
# ---------------------------------------------------------------------------


def test_detect_install_method_env(monkeypatch):
    for var in ("PIPX_HOME", "PIPX_BIN_DIR", "UV_TOOL_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.prefix", "/usr")

    monkeypatch.setenv("PIPX_HOME", "/x")
    assert detect_install_method() == "pipx"
    monkeypatch.delenv("PIPX_HOME")

    monkeypatch.setenv("UV_TOOL_DIR", "/y")
    assert detect_install_method() == "uv"
    monkeypatch.delenv("UV_TOOL_DIR")

    assert detect_install_method() == "pip"


def test_detect_install_method_prefix(monkeypatch):
    for var in ("PIPX_HOME", "PIPX_BIN_DIR", "UV_TOOL_DIR"):
        monkeypatch.delenv(var, raising=False)
    # pipx via prefix path component.
    monkeypatch.setattr("sys.prefix", "/home/u/.local/pipx/venvs/kanibako-cli")
    assert detect_install_method() == "pipx"
    # uv via prefix path component + "tools".
    monkeypatch.setattr("sys.prefix", "/home/u/.local/share/uv/tools/kanibako-cli")
    assert detect_install_method() == "uv"
    # plain prefix -> pip.
    monkeypatch.setattr("sys.prefix", "/usr")
    assert detect_install_method() == "pip"


def test_install_command_per_method(monkeypatch):
    for var in ("PIPX_HOME", "PIPX_BIN_DIR", "UV_TOOL_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.prefix", "/usr")
    monkeypatch.setattr("kanibako.install_method.is_externally_managed", lambda: False)

    monkeypatch.setenv("PIPX_HOME", "/x")
    assert install_command("pkg") == "pipx inject kanibako-cli pkg"
    monkeypatch.delenv("PIPX_HOME")

    monkeypatch.setenv("UV_TOOL_DIR", "/y")
    assert install_command("pkg") == "uv tool install kanibako-cli --with pkg"
    monkeypatch.delenv("UV_TOOL_DIR")

    assert install_command("pkg") == "pip install pkg"


def test_install_command_externally_managed(monkeypatch):
    for var in ("PIPX_HOME", "PIPX_BIN_DIR", "UV_TOOL_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.prefix", "/usr")
    monkeypatch.setattr("kanibako.install_method.is_externally_managed", lambda: True)
    assert install_command("pkg") == "pip install pkg --break-system-packages"


# ---------------------------------------------------------------------------
# 7. Two-pass: resolve the NAME (resolve_agent), then read behavior off the ONE
#    launch snapshot (build_launch_snapshot + effective_behavior) — the block-7c
#    replacement for the retired resolve_and_load_settings/SettingsResolver chain.
#    These pin the same precedence (box beats the agent default) on the LIVE path.
# ---------------------------------------------------------------------------


def _two_pass_behavior(*, agent_state, box_path=None):
    """PASS 1: resolve the agent name. PASS 2: read behavior off the snapshot.

    Mirrors the launch flow: the per-agent FILE state rides ``agent_state`` (→
    ``agent.<active>`` slot) and a box settings file rides ``box_path`` (its
    discriminated ``agent.<name>.*`` table, MORE specific than the agent state)."""
    from kanibako.settings_launch import (
        build_launch_snapshot,
        effective_behavior,
    )
    from kanibako.settings_resolve import ResolveCtx

    name = resolve_agent(
        explicit_agent="claude",
        box_agent_name=None,
        workset_agent=None,
        system_default_path=None,
    )
    ctx = ResolveCtx(
        agent_name=name, workset_name=None, host_home="/home/agent", xdg={},
    )
    snap = build_launch_snapshot(
        agent_name=name, ctx=ctx,
        system_path=None, agent_path=None, workset_path=None, box_path=box_path,
        agent_state=agent_state,
    )
    return name, effective_behavior(snap, active_agent=name)


def test_two_pass_box_beats_agent(tmp_home, config_file, monkeypatch):
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)

    # Box settings file supplies model under agent.<name> (read_agent_settings shape).
    box_path = tmp_home / "box_settings.yaml"
    write_agent_setting(box_path, "model", "box-wins", "claude")

    name, eff = _two_pass_behavior(
        agent_state={"model": "agent-default"},  # the agent tier
        box_path=box_path,
    )
    assert name == "claude"
    # Box (more specific) wins over the agent-state default.
    assert eff["model"] == "box-wins"


def test_two_pass_agent_default_when_no_box(tmp_home, config_file, monkeypatch):
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    name, eff = _two_pass_behavior(agent_state={"model": "agent-default"})
    assert name == "claude"
    # With no box override the agent-state value is the effective one.
    assert eff["model"] == "agent-default"

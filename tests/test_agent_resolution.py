"""Tests for the cascade agent resolver + installed-count rule (W1 Phase C1)."""

from __future__ import annotations

import pytest

from kanibako.config import (
    config_file_path,
    load_config,
    resolve_agent,
    resolve_and_load_settings,
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
            box_agent=None,
            workset_agent=None,
            system_default_path=None,
        )
        base.update(kw)
        return resolve_agent(**base)

    # explicit beats box beats workset beats system-default.
    assert (
        res(explicit_agent="claude", box_agent="goose", workset_agent="codex")
        == "claude"
    )
    # box beats workset beats system-default (explicit absent).
    assert res(box_agent="goose", workset_agent="codex") == "goose"
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
            box_agent=None,
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
            box_agent=None,
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
            box_agent=None,
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
            box_agent=None,
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
            box_agent=None,
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
            box_agent=None,
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
            box_agent=None,
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
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setattr("sys.prefix", "/usr")
    monkeypatch.setattr("kanibako.install_method.is_externally_managed", lambda: False)
    with pytest.raises(AgentNotInstalledError) as ei:
        resolve_agent(
            explicit_agent=None,
            box_agent="claude",
            workset_agent=None,
            system_default_path=None,
        )
    msg = str(ei.value)
    assert "claude" in msg
    assert "pip install kanibako-agent-claude" in msg
    assert "kanibako agent list" in msg


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
        box_agent=None,
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
# 7. Two-pass loader: box override beats agent default
# ---------------------------------------------------------------------------


def test_two_pass_box_beats_agent(tmp_home, config_file, monkeypatch):
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)

    # Box settings file supplies model under agent.<name> (read_agent_settings shape).
    box_path = tmp_home / "box_settings.yaml"
    write_agent_setting(box_path, "model", "box-wins", "claude")

    name, resolver = resolve_and_load_settings(
        explicit_agent="claude",
        box_agent=None,
        workset_agent=None,
        system_default_path=None,
        agent_state={"model": "agent-default"},  # the agent tier
        box_path=box_path,
    )
    assert name == "claude"
    rv = resolver.resolve("model")
    assert rv.value == "box-wins"
    assert rv.level == "box"


def test_two_pass_agent_default_when_no_box(tmp_home, config_file, monkeypatch):
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    name, resolver = resolve_and_load_settings(
        explicit_agent="claude",
        box_agent=None,
        workset_agent=None,
        system_default_path=None,
        agent_state={"model": "agent-default"},
    )
    assert name == "claude"
    assert resolver.get("model") == "agent-default"

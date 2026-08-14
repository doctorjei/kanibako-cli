"""Tests for the agent SELECTION seam + installed-count rule.

⮕ **P7 (spec §1A / §2g / §2h).** The cascade moved OUT of ``config.resolve_agent``:
``system.agent`` and the workset/box ``pref.system.agent`` requests are resolved
off the settings snapshot (``settings_launch.resolve_selected_agent``), and
``resolve_agent`` keeps only what is NOT a key — name validation, persona-ref
canonicalisation and the installed-count rule. The retired ``box.agent_name`` /
``workset_agent`` / ``system_default_path`` parameters are gone.
"""

from __future__ import annotations

import pytest

from kanibako.settings.config import (
    config_file_path,
    load_config,
    read_system_agent,
    resolve_agent,
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
from kanibako.settings.paths import load_std_paths, xdg

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
    """No-op kept for readability: ``resolve_agent`` no longer reads any file.

    The stored ``system.agent`` reaches it only as the caller-supplied
    *requested* value (P7), so "no system default" is simply omitting it.
    """


# ---------------------------------------------------------------------------
# 1. Cascade precedence
# ---------------------------------------------------------------------------


def test_precedence_explicit_beats_requested(monkeypatch):
    """§1A: the CLI level outranks whatever the settings cascade resolved."""
    _patch_targets(monkeypatch, ["claude", "goose"])
    assert resolve_agent(explicit_agent="claude", requested="goose") == "claude"
    assert resolve_agent(explicit_agent=None, requested="goose") == "goose"
    assert resolve_agent(explicit_agent="", requested="goose") == "goose"


# ---------------------------------------------------------------------------
# 2. Absent everywhere + exactly 1 installed
# ---------------------------------------------------------------------------


def test_single_installed_autopick(monkeypatch):
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(explicit_agent=None, requested=None)
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
        resolve_agent(explicit_agent=None, requested=None)
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
        resolve_agent(explicit_agent=None, requested=None)
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
        resolve_agent(explicit_agent=None, requested=None)
        == "claude"
    )


def test_two_real_plus_pseudo_still_gate2a(monkeypatch):
    # Two real agents + a pseudo agent -> still ambiguous -> Gate-2a.
    _patch_targets(monkeypatch, ["claude", "goose", "no_agent"])
    _no_default(monkeypatch)
    with pytest.raises(NoAgentSelectedError):
        resolve_agent(explicit_agent=None, requested=None)


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
        resolve_agent(explicit_agent=None, requested=None)


def test_explicit_pseudo_agent_still_selectable(monkeypatch):
    # A pseudo agent stays EXPLICITLY selectable (--agent no_agent), even though
    # it is excluded from the implicit count.
    _patch_targets(monkeypatch, ["claude", "no_agent"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(explicit_agent="no_agent", requested=None)
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
        resolve_agent(explicit_agent=None, requested="claude")
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
        resolve_agent(explicit_agent="navigator+claude", requested=None)
        == "navigator℘claude"
    )


def test_persona_canonical_separator_accepted(monkeypatch):
    # The ℘ literal is accepted on input too and returns the same node.
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(explicit_agent="navigator℘claude", requested=None)
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
        resolve_agent(explicit_agent="navigator+claude", requested=None)
    msg = str(ei.value)
    # Names the harness for the install hint; does NOT leak the persona/node.
    assert "kanibako-agent-claude" in msg
    assert "navigator" not in msg


def test_bare_claude_unchanged_with_persona_support(monkeypatch):
    # BACKWARD-COMPAT: a bare ref still returns the bare name byte-for-byte.
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(explicit_agent="claude", requested=None)
        == "claude"
    )


def test_persona_box_tier_canonicalized(monkeypatch):
    # A persona ref supplied at the BOX tier (not just explicit) is canonicalised.
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(explicit_agent=None, requested="navigator+claude")
        == "navigator℘claude"
    )


# ---------------------------------------------------------------------------
# 5b. System-default tier round-trip through a real settings file
# ---------------------------------------------------------------------------


def test_system_agent_round_trips_through_the_system_table(
    tmp_home, config_file, monkeypatch,
):
    """``system.agent`` stores in the ``system:`` table and reads back (P7).

    INVERT: write it to the retired ``agent.default.default_agent`` location and
    ``read_system_agent`` returns None -> this reddens.
    """
    from kanibako.settings.config_interface import set_config_value
    from kanibako.settings.config_keys import ConfigLevel

    _patch_targets(monkeypatch, ["claude"])
    cf = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    ssp = load_std_paths(load_config(cf)).settings
    ssp.parent.mkdir(parents=True, exist_ok=True)
    msg = set_config_value(
        "system.agent", "claude",
        config_path=cf, system_settings_path=ssp,
        command_scope=ConfigLevel.system,
    )
    assert msg.startswith("Set "), msg
    # Stored where the SYSTEM settings tier reads it — not in agent.default.
    from kanibako.settings.config_io import load_doc
    assert load_doc(ssp)["system"]["agent"] == "claude"
    assert read_system_agent(ssp) == "claude"
    # …and it validates through the arbiter exactly like any other name.
    assert resolve_agent(explicit_agent=None, requested="claude") == "claude"


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
    from kanibako.settings.agent_file import state_level
    from kanibako.settings.settings_launch import (
        build_launch_snapshot,
        effective_behavior,
    )
    from kanibako.settings.settings_resolve import ResolveCtx

    name = resolve_agent(explicit_agent="claude", requested=None)
    ctx = ResolveCtx(
        agent_name=name, workset_name=None, host_home="/home/agent", xdg={},
    )
    snap = build_launch_snapshot(
        agent_name=name, ctx=ctx,
        system_path=None, agent_path=None, workset_path=None, box_path=box_path,
        # Wrapped as the production producers do (C-2): the level carries the
        # node it merges under, which is the name PASS 1 just resolved.
        agent_state=state_level(agent_state, node=name),
    )
    return name, effective_behavior(snap, active_agent=name)


def test_two_pass_box_pref_beats_agent(tmp_home, config_file, monkeypatch):
    from kanibako.settings.config_io import dump_doc

    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)

    # A box tweaks its active agent with the §2h REQUEST pref.agent.<a>.<key>.
    # (A box file may NOT set agent.<name>.* directly: that is an upward write
    # dropped at RESOLVE, spec §0. ⮕ P7: this used to use the box.agent.* mirror,
    # which spec §2b RETIRED — the pref is the replacement, and it targets the
    # agent tier properly instead of smuggling a box-scope key into it.)
    box_path = tmp_home / "box_settings.yaml"
    dump_doc(box_path, {"pref": {"agent": {"claude": {"model": "box-wins"}}}})

    name, eff = _two_pass_behavior(
        agent_state={"model": "agent-default"},  # the agent tier
        box_path=box_path,
    )
    assert name == "claude"
    # The box's request (more specific) wins over the agent-state default.
    assert eff["model"] == "box-wins"


def test_two_pass_agent_default_when_no_box(tmp_home, config_file, monkeypatch):
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    name, eff = _two_pass_behavior(agent_state={"model": "agent-default"})
    assert name == "claude"
    # With no box override the agent-state value is the effective one.
    assert eff["model"] == "agent-default"

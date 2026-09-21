"""Tests for the agent SELECTION seam — validate a name, or REFUSE.

⮕ **P7 (spec §1A / §2g / §2h).** The cascade moved OUT of ``config.resolve_agent``:
``system.agent`` and the workset/box ``pref.system.agent`` requests are resolved
off the settings snapshot (``settings_launch.resolve_selected_agent``), and
``resolve_agent`` keeps only what is NOT a key — name validation and persona-ref
canonicalisation. The retired ``box.agent_name`` / ``workset_agent`` /
``system_default_path`` parameters are gone.

🛑 **THERE IS NO INSTALLED-AGENT COUNT RULE** (retired 2026-09-19, his ruling; spec
§2b). ``resolve_agent`` never picks — the installed set answers *"is this name
installed?"* and nothing else. The tests below assert that the count is not read,
at every count, which is the property the rule's deletion has to hold.
"""

from __future__ import annotations

import pytest

from kanibako.settings.config import (
    config_file_path,
    load_config,
    read_system_agent,
    resolve_agent,
)
from kanibako.errors import AgentNotInstalledError, AgentUnsetError
from kanibako.install_method import (
    detect_install_method,
    install_command,
)
from kanibako.settings.paths import load_std_paths, xdg

# Exact UNSET refusal wording (must match resolve_agent verbatim).
UNSET_REFUSAL = (
    "No default agent is configured (system.agent is unset).\n"
    "Choose one with:\n"
    "  kanibako setup\n"
    "Or name one for a single run with '--agent <name>'.\n"
    "'kanibako shell' reaches the box's container without an agent."
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


@pytest.mark.parametrize("blank", ["", "  ", "\t\n"])
def test_a_blank_tier_is_a_value_not_an_unset(monkeypatch, blank):
    """A PRESENT tier resolves even when it is blank — it does not fall through.

    Spec §2h names three downstream idioms and keeps them apart: present-``None``,
    terminal ``""`` (**≠ unset**), and the COPY-disable sentinel.  Absence is
    already spelled ``None`` in both arguments, so a blank one is a VALUE, and
    the only legal reading of a value at this key is "an agent ref" — which the
    grammar refuses.

    MUTATION: restore ``_clean(explicit_agent) or _clean(requested)`` and the
    first half silently launches ``goose`` — the agent the user did not type.
    """
    from kanibako.errors import ConfigError

    from tests.support.agent_refs import blank_ref_refusal

    _patch_targets(monkeypatch, ["claude", "goose"])
    with pytest.raises(ConfigError) as ei:
        resolve_agent(explicit_agent=blank, requested="goose")
    assert str(ei.value) == blank_ref_refusal()

    # The STORED tier answers the same way.
    _patch_targets(monkeypatch, ["claude"])
    with pytest.raises(ConfigError) as ei:
        resolve_agent(explicit_agent=None, requested=blank)
    assert str(ei.value) == blank_ref_refusal()
    # Control: the SAME call with the tier ABSENT refuses too, by a DIFFERENT
    # message — blank is an illegal ref, absent is "nobody chose".
    with pytest.raises(AgentUnsetError):
        resolve_agent(explicit_agent=None, requested=None)


# ---------------------------------------------------------------------------
# 2. Nothing resolved -> the UNSET refusal, AT EVERY INSTALLED COUNT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "installed",
    [
        [],                                   # was Gate-2b
        ["claude"],                           # was the autopick
        ["claude", "goose"],                  # was Gate-2a
        ["claude", "goose", "codex"],
        ["shell"],                              # the seeded built-in, alone
        ["claude", "shell"],                    # one plugin plus the built-in
    ],
    ids=["zero", "one", "two", "three", "pseudo-only", "one-plus-pseudo"],
)
def test_unset_refuses_whatever_is_installed(monkeypatch, installed):
    """Nothing named an agent ⇒ one refusal, and the COUNT does not change it.

    🛑 **This is the retired count rule's grave** (his ruling, 2026-09-19; spec §2b).
    The ``["claude"]`` row is the one that used to launch: exactly one installed
    plugin was auto-picked, and a user on a single-agent host never ran ``setup``.

    MUTATION: restore ``if len(real_installed) == 1: return next(iter(...))`` and
    the ``one`` and ``one-plus-pseudo`` rows go green-by-launching instead of
    refusing — which is exactly the behaviour being deleted.
    """
    _patch_targets(monkeypatch, installed)
    _no_default(monkeypatch)
    with pytest.raises(AgentUnsetError) as ei:
        resolve_agent(explicit_agent=None, requested=None)
    assert str(ei.value) == UNSET_REFUSAL


def test_unset_refusal_names_setup_and_not_an_install_command(monkeypatch):
    """The cure is ``kanibako setup``, even with NO plugin installed at all.

    The retired zero-installed arm printed a ``pip install …`` line instead; it is
    ``setup``'s job to say that now (it prints ``No agent plugins installed.``), and
    the launch has one answer rather than one per count.
    """
    _patch_targets(monkeypatch, [])
    _no_default(monkeypatch)
    with pytest.raises(AgentUnsetError) as ei:
        resolve_agent(explicit_agent=None, requested=None)
    msg = str(ei.value)
    assert "kanibako setup" in msg
    assert "pip install" not in msg
    assert "No agent plugins are installed" not in msg


@pytest.mark.parametrize("spelling", ["shell", "Shell", "SHELL"])
def test_explicit_shell_resolves_without_consulting_installed(
    monkeypatch, spelling,
):
    # ``shell`` is SELECTABLE though not claimable: it names the built-in
    # occupying its own slot (spec §2b, [R175]), so it resolves WITHOUT
    # consulting the installed set — even a host with only ``claude`` answers
    # it.  Fold-to-compare ([R172]): any case reaches the lowercase node.
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert resolve_agent(explicit_agent=spelling, requested=None) == "shell"


def test_explicit_no_agent_is_not_installed(monkeypatch):
    # [R174]: ``no_agent`` does not exist — a token reaching the code is an
    # error, never a translation.  It parses as a name like any other and then
    # fails the installed-set lookup it was never registered in.
    _patch_targets(monkeypatch, ["claude", "shell"])
    _no_default(monkeypatch)
    with pytest.raises(AgentNotInstalledError):
        resolve_agent(explicit_agent="no_agent", requested=None)


# ---------------------------------------------------------------------------
# 5. Name resolves but adapter not installed -> AgentNotInstalledError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "envset,substring",
    [
        ({"PIPX_HOME": "/p"}, "pipx inject kanibako-cli kanibako-agent-claude"),
        ({"UV_TOOL_DIR": "/u"}, "uv tool install kanibako-cli --with kanibako-agent-claude"),
        ({}, "pip install kanibako-agent-claude"),
    ],
)
def test_resolved_name_not_installed(monkeypatch, envset, substring):
    """A NAMED agent that is not installed says how to install it, per install mode.

    ⚑ This is the only refusal left that reads the installed set, and it reads it to
    answer *"is this name there?"* — never *"how many are there?"*.
    """
    _patch_targets(monkeypatch, ["goose"])  # claude NOT present
    _no_default(monkeypatch)
    monkeypatch.delenv("PIPX_HOME", raising=False)
    monkeypatch.delenv("PIPX_BIN_DIR", raising=False)
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setattr("sys.prefix", "/usr")
    monkeypatch.setattr("kanibako.install_method.is_externally_managed", lambda: False)
    for k, v in envset.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(AgentNotInstalledError) as ei:
        resolve_agent(explicit_agent=None, requested="claude")
    msg = str(ei.value)
    assert "claude" in msg
    assert substring in msg
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


def test_a_typed_case_variant_reaches_the_agent_and_returns_the_NODE(monkeypatch):
    """``[R173]``: ``system.agent`` and ``--agent`` carry a NAME; the node is lowercase.

    This is the hop where the one becomes the other.  Exact matching answered
    ``--agent Claude`` with *"Agent 'Claude' is not installed"* — the installed
    plugin, named in the case its own docs use.  Folding only the LOOKUP would be
    worse: the launch would then key ``agents/Claude/`` and ``agent.Claude.*``,
    spelling one agent's store two ways.
    """
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert resolve_agent(explicit_agent="Claude", requested=None) == "claude"
    assert resolve_agent(explicit_agent="CLAUDE", requested=None) == "claude"
    assert resolve_agent(explicit_agent=None, requested="Claude") == "claude"


def test_a_case_variant_folds_the_HARNESS_and_leaves_the_PERSONA_alone(monkeypatch):
    """Only the harness segment is this ruling's to touch.

    ``[R173]`` splits an agent into a case-carrying NAME and a lowercase NODE; a
    PERSONA has no plugin to declare it, and whether it folds is still open
    (``Q35``).  So the substitution replaces exactly the harness.
    """
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    assert (
        resolve_agent(explicit_agent="Navigator+Claude", requested=None)
        == "Navigator℘claude"
    )


def test_an_unrelated_name_is_still_refused(monkeypatch):
    """Non-vacuity: the fold widened the match, it did not disable the check."""
    _patch_targets(monkeypatch, ["claude"])
    _no_default(monkeypatch)
    with pytest.raises(AgentNotInstalledError):
        resolve_agent(explicit_agent="clauded", requested=None)


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
    from kanibako.settings.agent_config import AgentConfig
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
        agent_state=state_level(
            AgentConfig(state=dict(agent_state or {})), node=name,
        ),
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

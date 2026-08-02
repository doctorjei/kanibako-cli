"""Tests for kanibako.targets.assembly — declarative launch assembly.

Covers every public function plus the BindingSourceError safe-fail.  All assembly
helpers are pure (only Path.exists() touches the filesystem), so tests build
descriptors inline and use tmp_path for real bind sources.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.errors import ConfigError
from kanibako.targets.assembly import (
    BindingSourceError,
    access_row,
    assemble_argv,
    assemble_env,
    descriptor_mounts,
    effective_access,
    entrypoint,
    resolve_access_tier,
    resolve_binding_source,
    resolve_mode,
)
from kanibako.targets.base import (
    AccessRealization,
    AccessTierRow,
    AgentInstall,
    BindKind,
    Binding,
    BindScope,
    Channel,
    HostSrcOrigin,
    Mount,
    Operation,
    PluginDescriptor,
    SettingArg,
)

# --------------------------------------------------------------------------- #
# Descriptor fixtures (claude- and goose-shaped)                              #
# --------------------------------------------------------------------------- #


def _claude_descriptor() -> PluginDescriptor:
    """A claude-shaped descriptor: FLAG access rows + FLAG model setting."""
    return PluginDescriptor(
        command=("claude",),
        bindings=(),
        mode={
            "start": (),
            "continue": ("--continue",),
            "resume": ("--resume",),
        },
        operations={"exec": Operation(fragment=("--print",))},
        access_realization=AccessRealization(
            channel=Channel.FLAG,
            restricted=AccessTierRow(),                       # emit nothing
            editing=AccessTierRow(
                flag=("--permission-mode", "acceptEdits"),
            ),
            full=AccessTierRow(flag=("--dangerously-skip-permissions",)),
            setting_key="access",
        ),
        settings=(SettingArg(setting_key="model", channel=Channel.FLAG, flag=("--model",)),),
        container_env={"DISABLE_AUTOUPDATER": "1"},
    )


def _goose_descriptor() -> PluginDescriptor:
    """A goose-shaped descriptor: ENV access rows + ENV model setting.

    Mirrors the SHIPPED goose rows, ``editing`` INCLUDED-BY-ABSENCE: goose has no
    realization for the middle tier, so the row is missing and the launch refuses
    it (R-41 / the B7b goose ruling).
    """
    return PluginDescriptor(
        command=("goose",),
        bindings=(),
        mode={
            "start": ("session",),
            "continue": ("session", "--resume"),
        },
        operations={"exec": Operation(fragment=("run", "-t"))},
        access_realization=AccessRealization(
            channel=Channel.ENV,
            env_var="GOOSE_MODE",
            restricted=AccessTierRow(env_value="approve"),
            full=AccessTierRow(env_value="auto"),
            setting_key="access",
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
# resolve_new_session — DELETED in P8                                          #
# --------------------------------------------------------------------------- #
#
# The fold *"the per-launch -N/-C/-R flags over the persisted ``continue_mode``
# key"* moved onto the §1A CLI LEVEL: ``build_cli_level`` installs
# ``agent.<active>.continue_mode`` (``-N`` ⇒ False, ``-C``/``-R`` ⇒ True) above every
# settings file and pref, and the launch reads the resolved key. The coverage this
# block held — every flag state against every stored value, INCLUDING the
# equivalence with the old fold and the picker-less ``-R`` case — now lives in
# ``tests/test_settings_cli_level.py``, exercised through that level.


# --------------------------------------------------------------------------- #
# resolve_access_tier / effective_access  (R-41 — the permission TIER)        #
# --------------------------------------------------------------------------- #


def test_access_unset_defaults_to_full() -> None:
    # R-41's ruled default: today's behaviour preserved (the box IS the
    # containment boundary).  ``None`` — the key absent from the cascade — is
    # the ONE spelling of "unset"; see the next test for ``""``.
    assert resolve_access_tier(None) == "full"


def test_access_empty_string_is_refused_not_treated_as_unset() -> None:
    """``access: ""`` is an INVALID VALUE, not "unset" — it must REFUSE.

    R-41: an unknown stored value is rejected and NEVER treated as permissive.
    ``""`` is not a member of the tier enum, so the only reading that obeys the
    rule is the same loud refusal every other non-tier value gets.

    ⚑ This is the one direction that mattered: the launch reads the key with
    ``dict.get``, so an ABSENT key already arrives as ``None``.  The only route
    to a ``""`` here is a hand-edited settings file — both set paths refuse the
    value — i.e. precisely the case this second fence exists for.  Folding it
    into the default arm would have made the sole reachable route to the
    permissive default one no validator ever approved.
    """
    with pytest.raises(ConfigError) as exc:
        resolve_access_tier("")
    msg = str(exc.value)
    assert "access" in msg
    assert "restricted | editing | full" in msg
    assert "''" in msg
    assert "never treated as" in msg


@pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
def test_access_declared_tiers_pass_through(tier: str) -> None:
    assert resolve_access_tier(tier) == tier


@pytest.mark.parametrize("bogus", ["fll", "FULL", "true", "yolo", "secure"])
def test_access_unknown_value_is_refused_never_permissive(bogus: str) -> None:
    """THE permission-axis guard: an unknown value RAISES, naming key + legals.

    Never coerced, never defaulted — a typo must not decide whether the agent
    prompts.  ``FULL`` is in the list on purpose: matching is EXACT, because the
    stored value and the resolver must agree byte for byte.
    """
    with pytest.raises(ConfigError) as exc:
        resolve_access_tier(bogus)
    msg = str(exc.value)
    assert "access" in msg
    assert "restricted | editing | full" in msg
    assert repr(bogus) in msg


def test_effective_access_secure_flag_is_restricted() -> None:
    assert effective_access(secure=True, autonomous=False) == "restricted"


def test_effective_access_autonomous_flag_is_full() -> None:
    assert effective_access(secure=False, autonomous=True) == "full"


def test_effective_access_no_flag_no_key_is_full() -> None:
    assert effective_access(secure=False, autonomous=False) == "full"


@pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
def test_effective_access_no_flag_uses_the_resolved_key(tier: str) -> None:
    assert effective_access(secure=False, autonomous=False, access=tier) == tier


@pytest.mark.parametrize("stored", ["restricted", "editing", "full"])
def test_effective_access_secure_beats_every_stored_tier(stored: str) -> None:
    assert effective_access(secure=True, autonomous=False, access=stored) == "restricted"


@pytest.mark.parametrize("stored", ["restricted", "editing", "full"])
def test_effective_access_autonomous_beats_every_stored_tier(stored: str) -> None:
    assert effective_access(secure=False, autonomous=True, access=stored) == "full"


# --------------------------------------------------------------------------- #
# access_row — the UN-RENDERED TIER rule                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
def test_access_row_returns_the_declared_row(tier: str) -> None:
    d = _claude_descriptor()
    assert access_row(d, tier) is d.access_realization.row(tier)


def test_access_row_none_for_descriptor_without_access_realization() -> None:
    # An agent with NO permission surface at all: no row, no refusal.
    assert access_row(_bare_descriptor(), "editing") is None


def test_access_row_refuses_a_tier_the_harness_cannot_render() -> None:
    """goose + ``editing``: REFUSE, never substitute (R-41 / B7b ruling).

    Substituting ``auto`` would over-permit; substituting ``approve`` would
    deliver prompt-on-every-edit while reporting success.  Both lie about what
    the user asked for, so the launch stops and names goose's real tiers.
    """
    d = _goose_descriptor()
    with pytest.raises(ConfigError) as exc:
        access_row(d, "editing", agent="goose")
    msg = str(exc.value)
    assert "editing" in msg
    assert "goose" in msg
    assert "restricted | full" in msg


def test_access_row_refuses_an_unknown_tier() -> None:
    with pytest.raises(ConfigError):
        access_row(_claude_descriptor(), "bogus")


@pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
def test_access_row_zero_rows_is_diagnosed_as_plugin_version_skew(tier: str) -> None:
    """A ``access_realization`` with NO rows means PLUGIN VERSION SKEW — say so.

    This is exactly the shape a kanibako-agent-* wheel published BEFORE the
    ``access`` tiers produces: its defaults file carries the retired
    ``flag``/``secure_flag`` block with no ``tiers:``, which loads to a
    :class:`AccessRealization` whose every row is ``None``.  The generic refusal would
    read "this agent cannot render that tier … Legal tiers: (none)", blaming the
    HARNESS for an install problem and pointing the user at a capability limit
    that does not exist.  No tier is renderable, so the message must name the
    real cause and the real cure.
    """
    d = PluginDescriptor(
        command=("claude",),
        bindings=(),
        mode={"start": ()},
        access_realization=AccessRealization(channel=Channel.FLAG, setting_key="access"),
    )
    with pytest.raises(ConfigError) as exc:
        access_row(d, tier, agent="claude")
    msg = str(exc.value)
    assert "VERSION SKEW" in msg
    assert "kanibako-agent-" in msg
    assert "claude" in msg
    # ...and NOT the capability-limit wording, which would misdirect.
    assert "no realization for it" not in msg
    assert "(none)" not in msg


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
        mode_fragment=d.mode["continue"],
        access="full",
        setting_values={"model": "opus"},
        extra_args=["--foo", "bar"],
    )
    # command[0] ("claude") excluded; mode, FLAG access row, FLAG model, extra.
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
        mode_fragment=d.mode["continue"],
        access="restricted",
        setting_values={},
        extra_args=[],
    )
    assert argv == ["--continue"]
    assert "--dangerously-skip-permissions" not in argv


def test_argv_model_absent_when_value_falsy() -> None:
    d = _claude_descriptor()
    argv = assemble_argv(
        d,
        mode_fragment=d.mode["start"],
        access="restricted",
        setting_values={"model": ""},
        extra_args=[],
    )
    # start mode = empty fragment, no model -> empty argv.
    assert argv == []


def test_argv_start_mode_empty_fragment() -> None:
    d = _claude_descriptor()
    argv = assemble_argv(
        d,
        mode_fragment=d.mode["start"],
        access="full",
        setting_values={},
        extra_args=["x"],
    )
    assert argv == ["--dangerously-skip-permissions", "x"]


def test_argv_goose_env_channels_not_in_argv() -> None:
    d = _goose_descriptor()
    argv = assemble_argv(
        d,
        mode_fragment=d.mode["continue"],
        access="full",
        setting_values={"model": "gpt-4o"},
        extra_args=[],
    )
    # ENV access row and ENV model never appear in argv; mode does.
    assert argv == ["session", "--resume"]
    assert "GOOSE_MODE" not in argv
    assert "GOOSE_MODEL" not in argv
    assert "gpt-4o" not in argv


def test_argv_op_path_uses_fragment_no_mode() -> None:
    d = _claude_descriptor()
    argv = assemble_argv(
        d,
        mode_fragment=d.mode["continue"],  # must be ignored when op_fragment set
        access="full",
        setting_values={"model": "opus"},
        op_fragment=d.operations["exec"].fragment,
        extra_args=["hello"],
    )
    # op fragment replaces mode; access row + model + extra still apply.
    assert argv == ["--print", "--dangerously-skip-permissions", "--model", "opus", "hello"]
    assert "--continue" not in argv


def test_argv_goose_op_fragment() -> None:
    d = _goose_descriptor()
    argv = assemble_argv(
        d,
        mode_fragment=None,
        access="restricted",
        setting_values={},
        op_fragment=d.operations["exec"].fragment,
        extra_args=["do this"],
    )
    assert argv == ["run", "-t", "do this"]


def test_argv_flag_per_tier_emits_its_own_row() -> None:
    # A FLAG harness that declares a NON-EMPTY row at every tier (the shape a
    # future agent whose own default is unsafe needs): each tier emits ITS row
    # and nothing else — no polarity, no fallback.
    d = PluginDescriptor(
        command=("agent",),
        bindings=(),
        mode={"start": ()},
        access_realization=AccessRealization(
            channel=Channel.FLAG,
            restricted=AccessTierRow(flag=("--ask-every-time",)),
            editing=AccessTierRow(flag=("--edits-ok",)),
            full=AccessTierRow(flag=("--yolo",)),
            setting_key="access",
        ),
    )

    def argv_at(tier: str) -> list[str]:
        return assemble_argv(
            d, mode_fragment=d.mode["start"], access=tier,
            setting_values={}, extra_args=[],
        )

    assert argv_at("full") == ["--yolo"]
    assert argv_at("editing") == ["--edits-ok"]
    assert argv_at("restricted") == ["--ask-every-time"]


def test_argv_claude_editing_emits_accept_edits_not_the_bypass() -> None:
    # The MIDDLE tier on the ARGV path (R-41 / D-7, verified vs claude 2.1.220's
    # --permission-mode choices).  Explicitly asserts the bypass flag is ABSENT:
    # the failure this axis fears is `editing` quietly meaning `full`.
    d = _claude_descriptor()
    argv = assemble_argv(
        d, mode_fragment=d.mode["continue"], access="editing",
        setting_values={}, extra_args=[],
    )
    assert argv == ["--continue", "--permission-mode", "acceptEdits"]
    assert "--dangerously-skip-permissions" not in argv


def test_argv_claude_restricted_emits_nothing_from_an_empty_row() -> None:
    # claude's `restricted` row is EMPTY -> emit nothing (its own default already
    # prompts).  An empty row is a DECLARED realization, unlike a MISSING one.
    d = _claude_descriptor()
    on = assemble_argv(
        d, mode_fragment=d.mode["continue"], access="restricted",
        setting_values={}, extra_args=[],
    )
    assert on == ["--continue"]
    assert "--dangerously-skip-permissions" not in on
    assert "--permission-mode" not in on


def test_argv_refuses_a_tier_the_harness_cannot_render() -> None:
    # The un-rendered-tier rule reaches the ARGV seam too (a caller that skipped
    # the launch gate must still not get a silent substitution).  A FLAG harness
    # that renders only the two extremes: `editing` REFUSES rather than falling
    # through to an empty argv, which would silently be that harness's OWN
    # default rather than the tier asked for.
    d = PluginDescriptor(
        command=("agent",),
        bindings=(),
        mode={"start": ()},
        access_realization=AccessRealization(
            channel=Channel.FLAG,
            restricted=AccessTierRow(),
            full=AccessTierRow(flag=("--yolo",)),
            setting_key="access",
        ),
    )
    with pytest.raises(ConfigError) as exc:
        assemble_argv(
            d, mode_fragment=d.mode["start"], access="editing",
            setting_values={}, extra_args=[], agent="agent",
        )
    assert "restricted | full" in str(exc.value)


def test_argv_command_tail_included_when_multi_element() -> None:
    d = PluginDescriptor(
        command=("npx", "claude"),
        bindings=(),
        mode={"start": ()},
    )
    argv = assemble_argv(
        d,
        mode_fragment=d.mode["start"],
        access="restricted",
        setting_values={},
        extra_args=[],
    )
    # command[0] ("npx") excluded; command[1:] ("claude") included.
    assert argv == ["claude"]


def test_argv_single_source_fragment_wins_over_descriptor() -> None:
    """B5 single-source pin: the argv splices the PASSED fragment (the keyspace
    value), never a descriptor-read one.

    If ``assemble_argv`` ever regrows a ``descriptor.mode`` /
    ``descriptor.operations`` direct read, a snapshot value that DIVERGES from
    the descriptor could no longer reach argv — so we pass fragments that
    deliberately diverge from the descriptor's and assert the passed values win
    and the descriptor's never appear.
    """
    d = _claude_descriptor()  # descriptor says continue = ("--continue",)
    argv = assemble_argv(
        d,
        mode_fragment=["--SNAPSHOT-CONTINUE"],
        access="restricted",
        setting_values={},
        extra_args=[],
    )
    assert argv == ["--SNAPSHOT-CONTINUE"]
    assert "--continue" not in argv
    # Same for the one-shot op fragment (descriptor says exec = ("--print",)).
    argv = assemble_argv(
        d,
        mode_fragment=["--SNAPSHOT-CONTINUE"],
        access="restricted",
        setting_values={},
        op_fragment=["--SNAPSHOT-EXEC"],
        extra_args=[],
    )
    assert argv == ["--SNAPSHOT-EXEC"]
    assert "--print" not in argv and "--continue" not in argv


def test_assemble_argv_has_no_descriptor_grammar_params() -> None:
    """B5 reintroduction guard: the signature carries NO ``mode_key`` / ``op``
    descriptor-lookup parameters — fragments are passed in from the keyspace.

    (A revived ``mode_key=``/``op=`` lookup param is the cheapest way the
    descriptor-direct read could come back; this pins the seam's shape.)
    """
    import inspect

    params = inspect.signature(assemble_argv).parameters
    assert "mode_key" not in params and "op" not in params
    assert "mode_fragment" in params and "op_fragment" in params


# --------------------------------------------------------------------------- #
# assemble_env                                                                #
# --------------------------------------------------------------------------- #


def test_env_base_container_env() -> None:
    d = _claude_descriptor()
    env = assemble_env(d, access="restricted", setting_values={})
    assert env == {"DISABLE_AUTOUPDATER": "1"}


def test_env_claude_flag_settings_not_in_env() -> None:
    d = _claude_descriptor()
    env = assemble_env(d, access="full", setting_values={"model": "opus"})
    # FLAG channels never land in env; only base container_env present.
    assert env == {"DISABLE_AUTOUPDATER": "1"}
    assert "model" not in env


def test_env_goose_safe_off_sets_goose_mode_auto() -> None:
    d = _goose_descriptor()
    env = assemble_env(d, access="full", setting_values={})
    assert env["GOOSE_MODE"] == "auto"


@pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
def test_env_flag_channel_harness_emits_no_permission_env_at_any_tier(
    tier: str,
) -> None:
    # CHANNEL SEPARATION, at every tier: a FLAG harness's permission realization
    # never leaks into the container env.  (Replaces the pre-R-41
    # "safe-ON omits GOOSE_MODE" test, whose premise — an ENV harness with no
    # restrictive value — is exactly the shape the tier rows abolished: goose's
    # restricted row now EMITS `approve`, asserted just below.)
    d = _claude_descriptor()
    assert assemble_env(d, access=tier, setting_values={}) == {
        "DISABLE_AUTOUPDATER": "1",
    }


def test_env_goose_model_env_set() -> None:
    d = _goose_descriptor()
    env = assemble_env(d, access="restricted", setting_values={"model": "gpt-4o"})
    assert env["GOOSE_MODEL"] == "gpt-4o"


def test_env_goose_model_absent_when_value_falsy() -> None:
    d = _goose_descriptor()
    env = assemble_env(d, access="restricted", setting_values={"model": ""})
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
    # agent.<node>.endpoint is set (a persona pointing at an alternate endpoint).
    d = _claude_endpoint_descriptor()
    env = assemble_env(
        d, access="full",
        setting_values={"model": "opus", "endpoint": "http://localhost:8080"},
    )
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:8080"


def test_env_claude_endpoint_absent_when_none() -> None:
    # <None> case: unset endpoint (absent / empty) emits NO ANTHROPIC_BASE_URL —
    # bare claude is byte-identical to today. Mutation check: the ONLY difference
    # vs the set case is the env key, so its absence here is non-vacuous.
    d = _claude_endpoint_descriptor()
    env_absent = assemble_env(d, access="full", setting_values={"model": "opus"})
    env_empty = assemble_env(
        d, access="full", setting_values={"model": "opus", "endpoint": ""},
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
        d, mode_fragment=d.mode["start"], access="full",
        setting_values={"model": "opus", "endpoint": "http://localhost:8080"},
        op_fragment=None, extra_args=[],
    )
    assert "http://localhost:8080" not in argv
    assert "ANTHROPIC_BASE_URL" not in argv
    # model FLAG still emits normally.
    assert "--model" in argv and "opus" in argv


def test_env_goose_restricted_emits_approve_explicitly() -> None:
    # goose's unset GOOSE_MODE default is itself "auto" (permissive), so the
    # restrictive tier MUST emit a value — "emit nothing" here would BE the
    # bypass.  Both renderable tiers are asserted from the shipped-shaped fixture.
    d = _goose_descriptor()
    assert assemble_env(d, access="full", setting_values={})["GOOSE_MODE"] == "auto"
    assert (
        assemble_env(d, access="restricted", setting_values={})["GOOSE_MODE"]
        == "approve"
    )


def test_env_refuses_a_tier_the_harness_cannot_render() -> None:
    # goose + editing on the ENV seam: REFUSE.  ⚑ Non-vacuous in the dangerous
    # direction — falling through would emit NO GOOSE_MODE, which goose reads as
    # "auto", i.e. the full bypass under the name `editing`.
    d = _goose_descriptor()
    with pytest.raises(ConfigError) as exc:
        assemble_env(d, access="editing", setting_values={}, agent="goose")
    assert "restricted | full" in str(exc.value)


def test_env_empty_row_emits_nothing() -> None:
    # An ENV harness whose declared row carries no value emits NOTHING.
    # ⚑ The implicit ``env_value or "auto"`` fallback is DELETED: "auto" is
    # goose's vocabulary, and agent-specific knowledge does not belong in the
    # agent-agnostic assembler.  A harness that needs a value declares it.
    d = PluginDescriptor(
        command=("x",),
        bindings=(),
        mode={"start": ()},
        access_realization=AccessRealization(
            channel=Channel.ENV, env_var="X_MODE",
            full=AccessTierRow(),  # declared, but empty
            setting_key="access",
        ),
    )
    assert assemble_env(d, access="full", setting_values={}) == {}


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

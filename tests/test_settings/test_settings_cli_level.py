"""P8 — the §1A CLI LEVEL: the builder, the guard, and the fold equivalence.

Spec §1A. Three groups:

* **builder** — what :func:`kanibako.settings.settings_cli_level.build_cli_level` installs,
  and (as importantly) what it does NOT;
* **guard** — the refusals §1A asks for, each NAMING the offending key (§0);
* **equivalence** — the flag folds this phase deleted from the call sites must
  produce byte-identical launch decisions for EVERY flag/stored-value combination.
  That last group is the real bar: P8 is a mechanism change, so the observable
  behaviour must not move.
"""

from __future__ import annotations

import pytest

from kanibako.settings.settings_cli_level import (
    CLI_SHADOWED_KEYS,
    SELECTION_KEY,
    build_cli_level,
    guard_cli_level,
)
from kanibako.settings.settings_resolve import SettingsError

ACTIVE = "claude"


# --------------------------------------------------------------------------- #
# builder                                                                     #
# --------------------------------------------------------------------------- #


def test_empty_level_is_none_not_an_empty_dict() -> None:
    """Nothing to install → ``None``, so callers keep passing ``None`` unchanged."""
    assert build_cli_level() is None
    assert build_cli_level(selection=None, active_agent=ACTIVE) is None


def test_selection_passes_through_verbatim() -> None:
    """P7's level is carried untouched — the selection is installed ALWAYS."""
    assert build_cli_level(selection={SELECTION_KEY: "goose"}) == {
        SELECTION_KEY: "goose"
    }


def test_model_installs_the_active_agent_slot() -> None:
    level = build_cli_level(active_agent=ACTIVE, model="opus")
    assert level == {f"agent.{ACTIVE}.model": "opus"}


def test_model_is_spelled_against_the_active_slot_not_default() -> None:
    """⚑ R1: ``agent.<active>.model``, never ``agent.default.model``.

    ``effective_behavior`` does the §2d active-over-default pick AFTER the merge, so
    a CLI value at ``agent.default.*`` would lose to any file's ``agent.<active>.*``
    even from level index 0 — which would contradict "the highest, above
    everything". The precedence consequence is proven in
    ``tests/test_settings_launch.py``; this pins the spelling at the source.
    """
    level = build_cli_level(active_agent=ACTIVE, model="opus") or {}
    assert "agent.default.model" not in level


def test_absent_model_flag_installs_nothing() -> None:
    """Absent ≠ ``""``. A flag not given must not be laundered into a terminal."""
    assert build_cli_level(active_agent=ACTIVE, model=None) is None
    assert build_cli_level(active_agent=ACTIVE, model="") is None


@pytest.mark.parametrize(
    "flags,expected",
    [
        ({"new_session": True}, False),
        ({"continue_session": True}, True),
        ({"resume": True}, True),
    ],
)
def test_mode_flags_fold_into_continue_mode(flags, expected) -> None:
    level = build_cli_level(active_agent=ACTIVE, **flags)
    assert level == {f"agent.{ACTIVE}.continue_mode": expected}


def test_no_mode_flag_leaves_continue_mode_absent() -> None:
    """No flag ⇒ the stored/default value stands; the level must not pin it."""
    assert build_cli_level(active_agent=ACTIVE, model="opus") == {
        f"agent.{ACTIVE}.model": "opus"
    }


def test_resume_installs_true_not_absent() -> None:
    """⚑ Load-bearing: ``-R`` must install True, not "nothing".

    ``assembly.resolve_mode`` falls through its picker arm for a descriptor with no
    ``resume`` mode (goose/codex) and then keys on ``skip_continue``. If ``-R``
    installed nothing, a box with a stored ``continue_mode: false`` would resolve
    fresh and ``-R`` would silently become ``start``.
    """
    assert build_cli_level(active_agent=ACTIVE, resume=True) == {
        f"agent.{ACTIVE}.continue_mode": True
    }


def test_no_active_agent_drops_the_agent_scope_flags() -> None:
    """A shell / ``--entrypoint`` box has no agent slot to spell against.

    It resolves under the ``"general"`` template slot, which is NOT an agent, so
    ``agent.general.*`` would fabricate a key the closed keyspace does not declare.
    The flags are dropped, not fabricated — and the selection still rides.
    """
    level = build_cli_level(
        selection={SELECTION_KEY: "claude"},
        active_agent=None,
        model="opus",
        new_session=True,
    )
    assert level == {SELECTION_KEY: "claude"}


def test_selection_and_flags_compose_into_one_level() -> None:
    assert build_cli_level(
        selection={SELECTION_KEY: ACTIVE},
        active_agent=ACTIVE,
        model="opus",
        continue_session=True,
    ) == {
        SELECTION_KEY: ACTIVE,
        f"agent.{ACTIVE}.model": "opus",
        f"agent.{ACTIVE}.continue_mode": True,
    }


def test_declared_table_lists_only_wired_flags() -> None:
    """The table is a contract: every entry must be one the builder can produce.

    An unwired entry would read as a promise (the reason ``settings_keyspace``
    deleted its unused ``CATEGORY_SCOPES``). ``--image`` / ``--share-images`` /
    ``-S``/``-A`` are deliberately ABSENT — see the module docstring.
    """
    assert set(CLI_SHADOWED_KEYS) == {"--agent", "-M/--model", "-N/-C/-R"}
    built = build_cli_level(
        selection={SELECTION_KEY: ACTIVE},
        active_agent=ACTIVE,
        model="opus",
        new_session=True,
    )
    assert set(built or {}) == {
        tmpl.replace("<agent>", ACTIVE) for tmpl in CLI_SHADOWED_KEYS.values()
    }


# --------------------------------------------------------------------------- #
# guard                                                                       #
# --------------------------------------------------------------------------- #


def test_guard_accepts_the_wired_keys() -> None:
    guard_cli_level(
        {
            SELECTION_KEY: ACTIVE,
            f"agent.{ACTIVE}.model": "opus",
            f"agent.{ACTIVE}.continue_mode": True,
        },
        active_agent=ACTIVE,
    )


def test_guard_is_a_noop_for_an_absent_level() -> None:
    guard_cli_level(None)
    guard_cli_level({})


def test_guard_accepts_a_declared_unwired_key() -> None:
    """The guard tests VALIDITY, not wiring.

    ``box.image`` is a declared, non-locator key with no CLI wiring yet (P8b). The
    guard must not double as a wiring list — that would make it refuse the very key
    the next phase installs.
    """
    guard_cli_level({"box.image": "ghcr.io/x:y"}, active_agent=ACTIVE)


@pytest.mark.parametrize(
    "key",
    [
        "box.nonsense",          # undeclared box leaf
        "agent.zippity.model",   # undeclared agent discriminator
        "wibble",                # undeclared namespace
    ],
)
def test_guard_refuses_an_undeclared_key(key) -> None:
    with pytest.raises(SettingsError) as exc:
        guard_cli_level({key: "x"}, active_agent=ACTIVE)
    assert key in str(exc.value)  # §0: the error NAMES the offending key


@pytest.mark.parametrize(
    "key", ["meta.box.path", "config.data", "pref.system.agent"],
)
def test_guard_refuses_the_categorical_tiers(key) -> None:
    with pytest.raises(SettingsError) as exc:
        guard_cli_level({key: "x"}, active_agent=ACTIVE)
    assert key in str(exc.value)


@pytest.mark.parametrize("key", ["workset.boxes", "workset.kuid"])
def test_guard_refuses_the_locator_closure(key) -> None:
    """The arm §1A explicitly asks for.

    These are plain settable workset keys, so ``key_validity`` accepts them; only
    the locator arm bars them. A CLI ``workset.boxes`` re-points ``meta.box.path``
    → the ``box.bindings.rw.home`` host_src → mount-over-the-box-home.
    """
    with pytest.raises(SettingsError) as exc:
        guard_cli_level({key: "/tmp/x"}, active_agent=ACTIVE)
    msg = str(exc.value)
    assert key in msg and "LOCATOR" in msg


def test_guard_permits_the_selection_key_despite_it_selecting_a_file() -> None:
    """``system.agent`` selects ``meta.agent.<a>.settings`` yet is NOT locator-class.

    It is deliberately excluded from ``LOCATOR_CLOSURE`` (an agent file may not
    carry prefs, so re-selecting one introduces no new requests). A guard that
    refused it would break P7 — pin that it does not.
    """
    guard_cli_level({SELECTION_KEY: "goose"}, active_agent=ACTIVE)


def test_guard_uses_the_active_agent_without_plugin_discovery() -> None:
    """A persona NODE is valid by virtue of having been resolved.

    The guard injects ``{active_agent}`` rather than discovering plugins, so a
    flag-free launch pays nothing and a persona node is not refused for being
    absent from the installed set.
    """
    node = "navigator℘claude"
    guard_cli_level({f"agent.{node}.model": "opus"}, active_agent=node)
    # ... and a DIFFERENT agent is still refused (the injection is not a bypass).
    with pytest.raises(SettingsError):
        guard_cli_level({"agent.someoneelse.model": "opus"}, active_agent=node)


def test_the_active_agent_is_unioned_into_a_supplied_valid_set() -> None:
    """A caller-supplied *valid_agents* must not veto the ACTIVE agent.

    ``build_launch_snapshot`` forwards whatever ``valid_agents`` it was given (tests
    and the pref path supply their own). If that set were used ALONE, a persona box
    whose node is absent from it would have its own ``-M`` refused — a false
    negative on a value the launch just resolved. The active agent is valid by
    construction, so it is unioned in; everything else still obeys the set.
    """
    node = "navigator℘claude"
    guard_cli_level(
        {f"agent.{node}.model": "opus"},
        active_agent=node,
        valid_agents={"claude", "goose"},
    )
    with pytest.raises(SettingsError):
        guard_cli_level(
            {"agent.codex.model": "x"},
            active_agent=node,
            valid_agents={"claude", "goose"},
        )


# --------------------------------------------------------------------------- #
# equivalence — the P8 bar: the deleted folds must not move behaviour          #
# --------------------------------------------------------------------------- #


def _old_resolve_new_session(
    *, new_session: bool, continue_override: bool, resume_mode: bool,
    continue_mode: bool = True,
) -> bool:
    """``assembly.resolve_new_session`` as it stood at ``cd251aa``, reproduced.

    Deleted from ``src`` by P8; kept HERE as the equivalence oracle so the claim
    "the fold moved, the behaviour did not" is checked against the real prior code
    rather than against a restatement of the new code.
    """
    if new_session:
        return True
    if continue_override or resume_mode:
        return False
    return not continue_mode


_FLAG_STATES = [
    ("none", {}),
    ("-N", {"new_session": True}),
    ("-C", {"continue_session": True}),
    ("-R", {"resume": True}),
]


@pytest.mark.parametrize("label,flags", _FLAG_STATES)
@pytest.mark.parametrize("stored", [None, True, False])
def test_continue_fold_equivalence(label, flags, stored) -> None:
    """12 cases: the CLI level + ``not continue_mode`` == the old flag fold.

    The launch used to compute ``effective_new_session`` from the flags AND the
    stored key. It now reads ONE resolved key. For every flag state against every
    stored value the two must agree, or P8 changed what a box does.
    """
    level = build_cli_level(active_agent=ACTIVE, **flags) or {}
    # The cascade result: the CLI level wins where present, else the stored value,
    # else the spec §2d default (True).
    resolved = level.get(
        f"agent.{ACTIVE}.continue_mode",
        True if stored is None else stored,
    )
    new_way = not resolved

    old_way = _old_resolve_new_session(
        new_session=flags.get("new_session", False),
        continue_override=flags.get("continue_session", False),
        resume_mode=flags.get("resume", False),
        continue_mode=True if stored is None else stored,
    )
    assert new_way is old_way, f"{label} with stored={stored}"


@pytest.mark.parametrize("label,flags", _FLAG_STATES)
@pytest.mark.parametrize("stored", [None, True, False])
def test_continue_fold_equivalence_reaches_the_same_mode_key(
    label, flags, stored,
) -> None:
    """The same 12 cases carried through ``resolve_mode`` to the launch GRAMMAR.

    Equivalence of the intermediate bool is necessary but not sufficient — ``-R``
    also feeds ``resolve_mode`` directly. Checked against BOTH a full-mode
    descriptor and a picker-less one (goose/codex), which is where dropping the
    ``-R`` ⇒ ``continue_mode: True`` install would have shown up.
    """
    from kanibako.targets.assembly import resolve_mode

    for modes in (("start", "continue", "resume"), ("start", "continue")):
        level = build_cli_level(active_agent=ACTIVE, **flags) or {}
        resolved = level.get(
            f"agent.{ACTIVE}.continue_mode",
            True if stored is None else stored,
        )
        new_mode = resolve_mode(
            resume_mode=flags.get("resume", False),
            new_session=not resolved,
            is_new_project=False,
            extra_args=[],
            available_modes=modes,
        )
        old_mode = resolve_mode(
            resume_mode=flags.get("resume", False),
            new_session=_old_resolve_new_session(
                new_session=flags.get("new_session", False),
                continue_override=flags.get("continue_session", False),
                resume_mode=flags.get("resume", False),
                continue_mode=True if stored is None else stored,
            ),
            is_new_project=False,
            extra_args=[],
            available_modes=modes,
        )
        assert new_mode == old_mode, f"{label}/{stored}/{modes}"


@pytest.mark.parametrize("flag_model", [None, "sonnet"])
@pytest.mark.parametrize("stored_model", [None, "opus"])
def test_model_fold_equivalence(flag_model, stored_model) -> None:
    """The CLI level == the deleted ``effective_state["model"] = model_override``.

    OLD: resolve the cascade, then overwrite the dict when ``-M`` was given.
    NEW: install ``agent.<active>.model`` at level 0 and read the cascade once.
    """
    level = build_cli_level(active_agent=ACTIVE, model=flag_model) or {}
    new_way = level.get(f"agent.{ACTIVE}.model", stored_model)

    old_way = stored_model
    if flag_model:
        old_way = flag_model

    assert new_way == old_way

# --------------------------------------------------------------------------- #
# The retired-BEHAVIOR cure names the node the entry is STORED under
# --------------------------------------------------------------------------- #
#
# A stored ``auto_approve`` is cured under the agent its OWN key path names. The
# resolved agent is a different thing: a box carrying ``pref.agent.shell.auto_approve``
# under a claude resolve is refused with a cure that writes ``claude``'s tier and leaves
# the stale entry in place, so the box comes up on the default tier anyway — the same
# silent regression in the UNSAFE direction the refusal exists to stop. The one shape
# that stores no node, the agent file's own root, keeps the resolved agent.
#
# ``key_validity`` is the oracle the cure-runs checks ask: a printed cure that names no
# real key is a command that cannot be pasted.

import pytest

from kanibako.settings.settings_assemble import refuse_retired_behavior_keys
from kanibako.settings.settings_keyspace import key_validity
from kanibako.settings.settings_resolve import SettingsError

#: The installed agents the keyspace is asked about; ``shell`` is a PSEUDO-agent and is
#: deliberately absent — it is judged by its own §2d fence, not by this set.
AGENTS = frozenset({"claude", "codex", "goose"})


#: Every node name the keyspace can name: an installed agent, or a PSEUDO-agent (which
#: is judged by its own §2d fence rather than by *valid_agents*).
_KNOWN_NODES = frozenset({"claude", "codex", "goose", "shell"})


def _node(key: str) -> str:
    """The ``agent.<node>`` segment of *key* — the tier the write would land on.

    A §2h request spells the same target one segment deeper (``pref.agent.<node>.<leaf>``),
    so the ``pref.`` prefix is dropped before the node is read.
    """
    segments = key.split(".")[1:] if key.startswith("pref.") else key.split(".")
    return segments[-2]


def _cure(raw, *, level, subject=None, box_name=None, path=None) -> str:
    """The ``Fix:`` line *raw* is refused with."""
    with pytest.raises(SettingsError) as exc:
        refuse_retired_behavior_keys(
            raw, level=level, path=path, subject=subject, box_name=box_name,
        )
    return str(exc.value).split("Fix: ", 1)[1].splitlines()[0].strip()


# --------------------------------------------------------------------------- #
# One per shape, at the level that shape is legal at
# --------------------------------------------------------------------------- #


def test_the_box_pref_cure_names_the_stored_node_not_the_resolved_agent() -> None:
    """RED at base: it printed ``pref.agent.claude.access`` — the agent being resolved."""
    assert _cure(
        {"pref": {"agent": {"shell": {"auto_approve": True}}}},
        level="box", subject="claude",
    ) == "kanibako box set <box> pref.agent.shell.access=full"


def test_the_workset_pref_cure_names_the_stored_node_not_the_resolved_agent() -> None:
    assert _cure(
        {"pref": {"agent": {"shell": {"auto_approve": True}}}},
        level="workset", subject="claude",
    ) == "kanibako workset set <workset> pref.agent.shell.access=full"


def test_the_box_cure_still_carries_the_box_positional_the_caller_knows() -> None:
    """The box name is the verb's positional; the pref KEY's node comes from the entry."""
    assert _cure(
        {"pref": {"agent": {"shell": {"auto_approve": True}}}},
        level="box", subject="claude", box_name="myproj",
    ) == "kanibako box set myproj pref.agent.shell.access=full"


def test_the_system_cure_names_the_node_it_omitted_entirely() -> None:
    """RED at base: it printed a bare ``system set access=``, which sets the any-agent
    ``default`` tier, not the tier of the agent the entry is stored under."""
    assert _cure(
        {"agent": {"claude": {"auto_approve": False}}},
        level="system", subject="shell",
    ) == "kanibako system set agent.claude.access=restricted"


def test_the_base_cure_names_the_stored_node_too() -> None:
    assert _cure(
        {"agent": {"claude": {"auto_approve": False}}},
        level="base", subject="shell",
    ) == "kanibako system set agent.claude.access=restricted"


def test_the_agent_files_root_still_names_the_resolved_agent() -> None:
    """The root stores no node — it IS the agent file's own tier — so the resolved agent
    is the right subject here, and this is the one shape that keeps it."""
    assert _cure(
        {"self": {"auto_approve": False, "model": "opus"}},
        level="agent", subject="claude",
    ) == "kanibako agent set claude access=restricted"


def test_the_agent_files_own_agent_table_names_its_stored_node() -> None:
    """RED at base: the file is ``claude``'s and the entry is ``goose``'s, and the cure
    wrote ``claude``'s root tier."""
    assert _cure(
        {"agent": {"goose": {"auto_approve": False}}},
        level="agent", subject="claude",
    ) == "kanibako agent set goose access=restricted"


def test_an_unparseable_value_still_names_the_stored_node() -> None:
    """The tier is a choice the user makes; the node is not."""
    assert _cure(
        {"pref": {"agent": {"shell": {"auto_approve": "sortof"}}}},
        level="box", subject="claude",
    ) == "kanibako box set <box> pref.agent.shell.access=<restricted|editing|full>"


# --------------------------------------------------------------------------- #
# The cure RUNS: the key it prints is a key
# --------------------------------------------------------------------------- #


def test_the_pref_cure_prints_a_key_the_keyspace_accepts() -> None:
    cure = _cure(
        {"pref": {"agent": {"shell": {"auto_approve": True}}}},
        level="box", subject="claude",
    )
    target = cure.split()[-1].split("=", 1)[0]
    assert key_validity(target, valid_agents=AGENTS) is None, target
    assert _node(target) in _KNOWN_NODES, target


def test_an_identity_free_resolve_still_gets_a_pasteable_pref_cure() -> None:
    """RED at base. The launch's identity-free seam (``settings_launch.
    _refuse_retired_spelling``) has no resolved agent to offer, so *subject* is ``None``
    and the cure fell back to the ``<agent>`` placeholder — which is not a key at all, so
    the line a user pastes is refused rather than applied. The stored node is available
    even with no identity."""
    cure = _cure(
        {"pref": {"agent": {"shell": {"auto_approve": True}}}},
        level="box", subject=None,
    )
    assert cure == "kanibako box set <box> pref.agent.shell.access=full"
    target = cure.split()[-1].split("=", 1)[0]
    assert key_validity(target, valid_agents=AGENTS) is None, target


def test_the_system_cure_prints_a_key_the_keyspace_accepts() -> None:
    cure = _cure(
        {"agent": {"claude": {"auto_approve": False}}},
        level="system", subject="shell",
    )
    target = cure.split()[-1].split("=", 1)[0]
    assert key_validity(target, valid_agents=AGENTS) is None, target
    # ⚑ ``key_validity`` judges the canonical key, not the system verb's bare shorthand.
    assert key_validity("access", valid_agents=AGENTS) is not None
    assert _node(target) in _KNOWN_NODES, target


def test_a_pseudo_agent_entry_is_cured_under_its_own_name() -> None:
    """``shell`` is a PSEUDO-agent: it is not in *valid_agents*, and it is judged by its
    own §2d fence — which declares ``access``. So the stored name is both the right
    subject and a legal one, and a no-plugin install can still follow the cure."""
    cure = _cure(
        {"pref": {"agent": {"shell": {"auto_approve": True}}}},
        level="box", subject="claude",
    )
    assert "pref.agent.shell.access" in cure
    assert key_validity("agent.shell.access", valid_agents=AGENTS) is None


def test_a_clean_file_still_passes() -> None:
    """Non-vacuity: the successor key under the same nodes is not refused."""
    refuse_retired_behavior_keys(
        {
            "agent": {"claude": {"access": "restricted"}, "goose": {"model": "o"}},
            "self": {"access": "full"},
            "pref": {"agent": {"shell": {"access": "editing"}}},
        },
        level="box", path=None,
    )


# --------------------------------------------------------------------------- #
# The two steps, in an order that works when run as printed
# --------------------------------------------------------------------------- #


def _refusal(raw, *, level, path, subject=None, box_name=None) -> str:
    """The WHOLE message *raw* is refused with — both steps, in the order printed."""
    with pytest.raises(SettingsError) as exc:
        refuse_retired_behavior_keys(
            raw, level=level, path=path, subject=subject, box_name=box_name,
        )
    return str(exc.value)


@pytest.mark.parametrize("level", ["base", "system", "workset", "box"])
def test_the_delete_comes_BEFORE_the_set(level, tmp_path) -> None:
    """The ``set`` validates the file it is about to write, so the stale entry refuses
    the write while it is still there; keyspec §2a makes that a plain ERROR, not a warning
    ("a bad entry outside the edited value's upstream chain"). Deleting first is what
    makes the printed sequence runnable top to bottom."""
    msg = _refusal(
        {"agent": {"claude": {"auto_approve": True}}},
        level=level, path=tmp_path / "s.yaml", subject="claude",
    )
    delete_at = msg.index(f"from {tmp_path / 's.yaml'} FIRST")
    assert "Delete the `agent.claude.auto_approve` entry" in msg
    assert delete_at < msg.index("Fix: "), "the delete must be printed BEFORE the set"


def test_the_reserved_any_agent_tier_is_set_by_its_BARE_key() -> None:
    """``default`` is the RESERVED any-agent tier, not a persona node: the system verb
    refuses ``agent.default.access`` and names the bare key instead, so a cure that
    spelled the node out is a command the CLI rejects."""
    assert _cure(
        {"agent": {"default": {"auto_approve": True}}},
        level="system", subject="claude",
    ) == "kanibako system set access=full"
    assert _cure(
        {"agent": {"default": {"auto_approve": True}}},
        level="base", subject="claude",
    ) == "kanibako system set access=full"


def test_a_persona_node_at_system_level_still_names_the_node() -> None:
    """The bare key is the any-agent DEFAULT; a persona's own tier is spelled under it."""
    assert _cure(
        {"agent": {"claude": {"auto_approve": True}}},
        level="system", subject="shell",
    ) == "kanibako system set agent.claude.access=full"


def test_a_pref_request_names_the_reserved_tier_normally() -> None:
    """A §2h request spells its node as data, so the reserved tier needs no bare key."""
    assert _cure(
        {"pref": {"agent": {"default": {"auto_approve": True}}}},
        level="box", subject="claude",
    ) == "kanibako box set <box> pref.agent.default.access=full"

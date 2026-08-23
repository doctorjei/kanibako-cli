"""Every DECLARED agent leaf reaches every surface that claims to serve it.

⚑ THE DEFECT CLASS THIS EXISTS TO MAKE IMPOSSIBLE (P15): the settable surface for
``agent.default.<leaf>`` was a HAND-KEPT copy of the declared leaf set, in two places
(``config_keys._is_agent_setting`` and ``_PERSONA_STATE_LEAVES``), and BOTH fell behind
:data:`~kanibako.settings.settings_keyspace.DECLARED_AGENT_LEAVES`.  The result:
``agent.default.{run_args,transform,transform_settings}`` answered "unknown config key"
at every spelling, and ``agent.default.{template,canon}`` answered a refusal whose CURE
— "set the any-agent default with the bare key" — itself answered "unknown config key".
A refusal that prescribes a failing command is worse than no cure at all.

⚑ THE TWO SURFACES ARE DERIVED NOW, so this file guards what derivation cannot: the
places a ruling forbade deriving (``KNOWN_CONFIG_KEYS``, whose quarantine block records
that generating it was PROPOSED AND DECLINED), and the agreement between the KEYSPACE's
view of a table-valued leaf and the FILE's.

⚑ DERIVED, NEVER LISTED (P13): the subject is ``DECLARED_AGENT_LEAVES`` itself, so a leaf
declared tomorrow is covered with no edit here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config_keys import (
    KNOWN_CONFIG_KEYS,
    _is_agent_setting,
    _parse_persona_agent_key,
    agent_leaf_table_error,
    is_known_key,
)
from kanibako.settings.settings_keyspace import (
    DECLARED_AGENT_LEAVES,
    SCALAR_AGENT_LEAVES,
    TABLE_VALUED_AGENT_LEAVES,
)


class TestTheDerivationIsNotVacuous:
    """Each guard below must have something to guard."""

    def test_the_declared_set_is_not_empty(self):
        assert DECLARED_AGENT_LEAVES

    def test_the_split_partitions_the_declared_set(self):
        """No leaf may be BOTH settable and table-valued, and none may be neither."""
        assert SCALAR_AGENT_LEAVES | TABLE_VALUED_AGENT_LEAVES == DECLARED_AGENT_LEAVES
        assert not (SCALAR_AGENT_LEAVES & TABLE_VALUED_AGENT_LEAVES)

    def test_both_halves_have_members(self):
        """A split with an empty side would let one guard pass vacuously."""
        assert SCALAR_AGENT_LEAVES
        assert TABLE_VALUED_AGENT_LEAVES


class TestEveryDeclaredLeafIsRecognised:

    def test_the_bare_spelling_is_a_known_key(self):
        """``system_cmd``'s ``get`` arm gates on ``is_known_key``: a leaf missing from
        it is a DECLARED key the CLI answers "unknown config key" for (spec §0).

        ⚑ THIS IS THE GUARD ``KNOWN_CONFIG_KEYS`` CANNOT GET BY CONSTRUCTION.  Deriving
        that set from the declaration SoT was proposed and DECLINED (its quarantine
        block), so completeness is asserted here instead of built in.
        """
        missing = sorted(DECLARED_AGENT_LEAVES - KNOWN_CONFIG_KEYS)
        assert not missing, (
            "declared agent leaves the CLI does not recognise in their BARE spelling "
            f"— add them to KNOWN_CONFIG_KEYS: {missing}"
        )

    def test_the_per_persona_spelling_parses(self):
        """``agent.<node>.<leaf>`` must reach the persona route, not fall through."""
        unparsed = sorted(
            leaf for leaf in DECLARED_AGENT_LEAVES
            if _parse_persona_agent_key(f"agent.claude.{leaf}") != ("claude", leaf)
        )
        assert not unparsed, unparsed

    def test_both_spellings_pass_the_read_gate(self):
        for leaf in sorted(DECLARED_AGENT_LEAVES):
            assert is_known_key(leaf), leaf
            assert is_known_key(f"agent.claude.{leaf}"), leaf


class TestTheScalarHalfIsSettable:

    def test_every_scalar_leaf_claims_the_bare_agent_branch(self):
        """``_is_agent_setting`` IS the bare ``agent.default`` write route."""
        assert {leaf for leaf in SCALAR_AGENT_LEAVES if _is_agent_setting(leaf)} == (
            SCALAR_AGENT_LEAVES
        )

    def test_a_table_valued_leaf_does_not(self):
        assert not any(_is_agent_setting(leaf) for leaf in TABLE_VALUED_AGENT_LEAVES)

    def test_a_bare_set_of_every_scalar_leaf_is_accepted(self, tmp_path):
        """The EFFECT, not the predicate: run the real verb.

        ⚑ MUTATION: put the old six-name literal back in ``_is_agent_setting`` ->
        ``template``, ``canon``, ``run_args`` and ``transform`` are refused and this
        dies with their names in the message.
        """
        from tests.test_settings.test_config_dest_parity import Bench
        from kanibako.settings.config_keys import ConfigLevel

        refused = {}
        for i, leaf in enumerate(sorted(SCALAR_AGENT_LEAVES)):
            value = "full" if leaf == "access" else "probe"
            msg = Bench(tmp_path / f"s{i}").set(ConfigLevel.system, leaf, value)
            if msg.startswith("Error:"):
                refused[leaf] = msg
        assert not refused, refused


class TestTheTableHalfIsRefusedBySHAPE:
    """A declared key the CLI cannot write is refused BY NAME, never as unknown (§0)."""

    @pytest.mark.parametrize("verb", ["set", "reset"])
    def test_both_spellings_get_the_shape_refusal(self, verb):
        for leaf in sorted(TABLE_VALUED_AGENT_LEAVES):
            for spelling in (leaf, f"agent.default.{leaf}", f"agent.claude.{leaf}"):
                msg = agent_leaf_table_error(spelling, verb=verb)
                assert msg is not None, spelling
                assert spelling in msg, msg
                assert "TABLE" in msg, msg
                assert "unknown config key" not in msg, msg

    def test_the_refusal_does_not_claim_a_neighbour(self):
        """⚑ It must fire on the AGENT leaf, not on anything ending in the word."""
        for leaf in sorted(TABLE_VALUED_AGENT_LEAVES):
            for other in (f"box.{leaf}", f"workset.{leaf}", f"system.{leaf}"):
                assert agent_leaf_table_error(other, verb="set") is None, other

    def test_a_scalar_leaf_is_never_caught_by_it(self):
        for leaf in sorted(SCALAR_AGENT_LEAVES):
            assert agent_leaf_table_error(leaf, verb="set") is None, leaf

    def test_the_pref_door_is_shut_too(self, tmp_path):
        """⚑ A ``pref.*`` REQUEST is the SECOND door onto the same crash.

        The preamble guard gates on the key as TYPED, so ``pref.agent.<a>.<leaf>`` walks
        past it; a pref is INSTALLED at its target during resolution (spec §2h), which
        means the scalar arrives where the map belongs anyway.  Measured accepted before
        the target-side check landed.
        """
        from tests.test_settings.test_config_dest_parity import Bench
        from kanibako.settings.config_keys import ConfigLevel

        for i, leaf in enumerate(sorted(TABLE_VALUED_AGENT_LEAVES)):
            msg = Bench(tmp_path / f"p{i}").set(
                ConfigLevel.box, f"pref.agent.claude.{leaf}", "probe",
            )
            assert msg.startswith("Error:"), (leaf, msg)
            assert "TABLE" in msg, msg

    def test_a_scalar_leaf_pref_still_works(self, tmp_path):
        """The other half: shutting that door may not shut the legal requests beside it."""
        from tests.test_settings.test_config_dest_parity import Bench
        from kanibako.settings.config_keys import ConfigLevel

        refused = {}
        for i, leaf in enumerate(sorted(SCALAR_AGENT_LEAVES)):
            value = "full" if leaf == "access" else "probe"
            msg = Bench(tmp_path / f"q{i}").set(
                ConfigLevel.box, f"pref.agent.claude.{leaf}", value,
            )
            if msg.startswith("Error:"):
                refused[leaf] = msg
        assert not refused, refused


class TestTheKeyspaceAndTheFileAgree:
    """The SAME fact, spelled in two layers — pinned against each other.

    :data:`TABLE_VALUED_AGENT_LEAVES` is the KEYSPACE's statement that a leaf holds a
    map; ``agent_file.table_value_error`` is the per-agent FILE's.  They are separate
    because they answer for different storage, and that is exactly why they can drift —
    so neither is allowed to move alone.
    """

    def test_the_file_refuses_exactly_the_table_valued_leaves(self, tmp_path):
        from kanibako.settings.agent_file import table_value_error

        path = Path(tmp_path) / "agent.yaml"
        refused = {
            leaf for leaf in DECLARED_AGENT_LEAVES
            if table_value_error(leaf, path=path, verb="set") is not None
        }
        assert refused == TABLE_VALUED_AGENT_LEAVES, (
            "the keyspace and the agent file disagree about which declared agent "
            f"leaves hold a table: file says {sorted(refused)}, keyspace says "
            f"{sorted(TABLE_VALUED_AGENT_LEAVES)}"
        )

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

⚑⚑ AND THE DEFECT RECURRED A THIRD TIME, one layer out: ``DECLARED_AGENT_LEAVES`` is not
the whole vocabulary either.  §0 puts the AGENT SPECIFICS in the PLUGINS, so the real set
is core UNIONED with ``setting_descriptors()``; ``_PERSONA_STATE_LEAVES`` read only the
core half while ``agent_key_reason`` read the union, and the two disagreed about
``agent.goose.provider``.  ``TestThePerNodeVocabularyIsTheAgentVerbs`` is the guard for
THAT layer, and it does not depend on which plugins happen to be installed.
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


class TestTheTableHalfIsREADABLE_JustNotHere:
    """The READ half of the same rule: refused by NAME, and told WHERE the value lives.

    ⚑ THE WRITE HALF ALREADY DID THIS and the read half did not.  ``config set`` refuses
    the bare spelling through ``agent_leaf_table_error``, naming the key and the file to
    edit; ``box get <box> transform_settings`` answered *"'transform_settings' is not a
    declared namespace"* and pointed nowhere — true of the bare token, useless to the
    user, and its generic cure prescribed DELETING an entry that is legitimate one scope
    up.  :data:`KNOWN_CONFIG_KEYS`' own comment had promised otherwise since 2026-08-23
    ("so the READ gate admits it and the refusal can name the shape instead of denying
    the key exists"); only the write half kept it.

    ⚑ NOTHING IS TAKEN AWAY: no value was ever returned at this spelling.  What changes
    is one printed line, at the same rc 1.
    """

    @pytest.mark.parametrize("scope_token,agent", [("box", "claude"), ("workset", None)])
    def test_the_bare_read_refusal_names_the_agent_noun(self, scope_token, agent):
        # MUTATION-PROVED: drop the ``table_leaf_read_cure(...) or`` from
        # ``scope_read_key_error`` and both rows red on the missing "agent noun" alone.
        from kanibako.settings.config_keys import ConfigLevel, scope_read_key_error

        for leaf in sorted(TABLE_VALUED_AGENT_LEAVES):
            msg = scope_read_key_error(
                leaf, ConfigLevel[scope_token], active_agent=agent,
            )
            assert msg is not None, leaf
            # ⚑ THE KEY STAYS FIRST — ``cli.main`` prints ``Error: {e}``, so the address
            # may only follow the §0 reason, never lead it (the 0c4fa47 / 891a3b7 shape).
            assert msg.startswith(f"Error: '{leaf}' cannot be read:"), msg
            assert msg.index("spec §0") < msg.index("agent noun"), msg
            assert f"kanibako agent get {agent or '<agent>'} {leaf}" in msg, msg
            # ...and the DELETION cure is the WRONG answer here, so it is not offered.
            assert "removing it means editing that file by hand" not in msg, msg

    def test_the_cure_it_names_is_a_LEGAL_read(self):
        """A refusal that prescribes a failing command is worse than no cure at all.

        ⚑ This is the file's opening complaint, applied to the cure this module now
        prints: ``agent get <node> <leaf>`` must be admitted by the ``agent`` noun's own
        read gate for every leaf the refusal redirects there.
        """
        from kanibako.settings.config_keys import agent_read_key_error

        for leaf in sorted(TABLE_VALUED_AGENT_LEAVES):
            assert agent_read_key_error("claude", leaf) is None, leaf

    def test_a_SCALAR_leaf_is_never_caught_by_it(self):
        """The bare scalar read at box scope still resolves through the pref redirect."""
        from kanibako.settings.config_keys import ConfigLevel, scope_read_key_error

        for leaf in sorted(SCALAR_AGENT_LEAVES):
            assert scope_read_key_error(
                leaf, ConfigLevel.box, active_agent="claude",
            ) is None, leaf

    def test_a_neighbour_does_not_inherit_the_cure(self):
        """⚑ The BARE spelling only — the twin of ``test_the_refusal_does_not_claim_a
        _neighbour`` above, plus one case the write half does not have.

        ``agent.<bogus>.<leaf>`` is refused about its NODE, and a shape cure appended to
        that would answer a question the user did not ask.
        """
        from kanibako.settings.config_keys import ConfigLevel, scope_read_key_error

        for leaf in sorted(TABLE_VALUED_AGENT_LEAVES):
            for other in (f"box.{leaf}", f"workset.{leaf}", f"agent.bogus.{leaf}"):
                msg = scope_read_key_error(
                    other, ConfigLevel.box, active_agent="claude",
                ) or ""
                assert "agent noun" not in msg, (other, msg)


class TestThePerNodeVocabularyIsTheAgentVerbs:
    """ONE vocabulary, two doors — the CONFIG engine's recogniser and the ``agent`` gate.

    ⚑ MEASURED BEFORE THE FIX (2026-08-29), on a store built through the product path:
    ``kanibako agent set goose provider=openrouter`` stored the value and
    ``kanibako agent get goose provider`` read it back, while
    ``kanibako system set agent.goose.provider=x`` answered *"Error: unknown config key"*
    at rc 1 and ``kanibako system get agent.goose.provider`` answered *"(not set)"* at
    rc 0 OVER THAT STORED VALUE.  A declared key refused by name at one door, and a
    fabricated answer masking real data at another — spec §0 twice.

    ⚑ THE VOCABULARY IS INJECTED, NOT READ OFF THIS MACHINE.  A guard that needs goose
    installed passes vacuously wherever it is not (P15: the check must red on its own
    emptiness), and the fact under test — the two doors share ONE leaf set — is a fact
    about the wiring, not about the plugin set.
    """

    SYNTHETIC = "probe_only_leaf"

    @pytest.fixture
    def one_plugin_leaf(self, monkeypatch):
        """Make the PLUGIN half declare exactly :attr:`SYNTHETIC`, and nothing else."""
        from kanibako.settings import settings_prefs

        agents = settings_prefs.AgentNames(("goose",), leaves={self.SYNTHETIC})
        monkeypatch.setattr(
            settings_prefs, "default_valid_agents", lambda: agents,
        )
        return self.SYNTHETIC

    def test_the_injected_leaf_is_outside_the_core_contract(self, one_plugin_leaf):
        """NON-VACUITY: a synthetic name core already declared would prove nothing."""
        assert one_plugin_leaf not in DECLARED_AGENT_LEAVES

    def test_a_plugin_leaf_reaches_the_per_node_recogniser(self, one_plugin_leaf):
        """⚑ MUTATION: put ``DECLARED_AGENT_LEAVES`` back in ``_PERSONA_STATE_LEAVES``
        and this dies while every core row in this file stays green."""
        assert _parse_persona_agent_key(f"agent.goose.{one_plugin_leaf}") == (
            "goose", one_plugin_leaf,
        )

    def test_the_agent_verbs_gate_agrees_with_it(self, one_plugin_leaf):
        from kanibako.settings.config_keys import agent_key_reason

        assert agent_key_reason("goose", one_plugin_leaf) is None

    def test_the_any_agent_TIER_spelling_agrees_too(self, one_plugin_leaf):
        """``agent.default.<leaf>`` is a key under ``key_class``, so it must have a slot.

        Left on the core set this returns ``None``, the READ falls to the persona branch
        for the reserved ``default`` node, and a stored value answers "(not set)" — the
        exact §0 fabrication ``agent_default_tier_leaf`` was written to stop.
        """
        from kanibako.settings.config_keys import agent_default_tier_leaf

        assert agent_default_tier_leaf(
            f"agent.default.{one_plugin_leaf}"
        ) == one_plugin_leaf

    def test_a_leaf_NOBODY_declares_reaches_neither(self, one_plugin_leaf):
        """THE OTHER DIRECTION: widening the vocabulary may not open it (spec §0)."""
        from kanibako.settings.config_keys import agent_key_reason

        undeclared = "not_declared_by_anyone"
        assert undeclared not in DECLARED_AGENT_LEAVES
        assert _parse_persona_agent_key(f"agent.goose.{undeclared}") is None
        reason = agent_key_reason("goose", undeclared)
        assert reason is not None and undeclared in reason

    def test_the_env_SECTION_form_is_still_not_a_tier_leaf(self, one_plugin_leaf):
        """The dotted ``env.<VAR>`` tail is a different family and stays unclaimed."""
        from kanibako.settings.config_keys import agent_default_tier_leaf

        assert agent_default_tier_leaf("agent.default.env.FOO") is None

    def test_the_verbs_round_trip_a_plugin_leaf_at_the_per_node_spelling(
        self, tmp_path,
    ):
        """THE EFFECT, not the predicate: set / get / reset through the real engine.

        ⚑ THE REAL VOCABULARY HERE, not the injected one, and deliberately: this row
        writes, and the keystore census judges a written key against the keyspace as the
        INSTALLED plugins declare it.  A synthetic leaf would be a genuinely undeclared
        write.  The rows above carry the environment-independent half.
        ⚑ DERIVED (P13) — whichever leaf the installed plugins add, never a name.
        ⚑ ``set`` answered *"Error: unknown config key"* at rc 1 before the fix, and
        ``get`` answered "(not set)" over the stored value.
        """
        from tests.test_settings.test_config_dest_parity import Bench
        from kanibako.settings.config_keys import ConfigLevel, plugin_declared_leaves

        extra = sorted(plugin_declared_leaves() - DECLARED_AGENT_LEAVES)
        if not extra:
            pytest.skip("no installed plugin declares a leaf outside the core contract")
        bench = Bench(tmp_path)
        key = f"agent.goose.{extra[0]}"

        msg = bench.set(ConfigLevel.system, key, "value-1")
        assert not msg.startswith("Error:"), msg
        assert bench.get(ConfigLevel.system, key) == "value-1"
        assert "Cleared" in bench.reset(ConfigLevel.system, key)
        assert bench.get(ConfigLevel.system, key) is None

    def test_an_undeclared_leaf_is_still_refused_by_the_verb(
        self, tmp_path, one_plugin_leaf,
    ):
        from tests.test_settings.test_config_dest_parity import Bench
        from kanibako.settings.config_keys import ConfigLevel

        msg = Bench(tmp_path).set(
            ConfigLevel.system, "agent.goose.not_declared_by_anyone", "x",
        )
        assert msg.startswith("Error:"), msg


class TestTheWideningDidNotREARM_PluginDiscovery:
    """The cost half of the fix, pinned where it is decided (P15).

    ⚑ **A COST RULE WITH A CORRECTNESS BILL, and this file's change is exactly the one
    that could re-arm it.** Answering "is this a plugin leaf" IMPORTS and instantiates
    every installed plugin, and those modules parse YAML in their module bodies — it was
    measured 2026-08-25 at ``+67 ms`` on every settings-resolving command, 73% of the
    whole resolve, and fixed by making the union LAZY. Nothing about the ANSWERS below
    would move if the question were put eagerly again, so no other row in this file
    would red — which is why these exist.

    ⚑ The twin lives in ``test_settings_keyspace.py`` (``_NeverAsk``) and pins the same
    deferral inside ``key_class``. This one pins ``config_keys``' side of it: the
    recogniser, and the ``agent`` noun's gate.
    """

    @pytest.fixture
    def discovery_reds(self, monkeypatch):
        """Make plugin discovery RAISE, so ASKING it at all is visible.

        ⚑ It raises rather than answering empty: a stub that answered would let a
        re-materialised union pass while paying the cost this forbids.
        """
        from kanibako.settings import settings_prefs

        def _never():
            raise AssertionError("plugin discovery was asked")

        monkeypatch.setattr(settings_prefs, "default_valid_agents", _never)

    @pytest.mark.parametrize("key", [
        # Not an agent path at all — the parser leaves before any leaf question.
        "box.image", "system.template", "model", "workset.channels.chat",
        # An agent path whose leaf the CORE §2d contract already declares.
        "agent.claude.model", "agent.default.access", "agent.claude.transform_settings",
        # The ``env.`` section arm, answered by its own position rule.
        "agent.claude.env.FOO",
    ])
    def test_a_key_the_CORE_contract_answers_never_asks_the_plugins(
        self, key, discovery_reds,
    ):
        _parse_persona_agent_key(key)

    @pytest.mark.parametrize("tail", ["model", "access", "run_args", "name"])
    def test_the_agent_gate_answers_a_core_leaf_without_discovery(
        self, tail, discovery_reds,
    ):
        """⚑ NEW GUARANTEE, not a preserved one: this gate used to call
        ``default_valid_agents()`` unconditionally, so every ``agent`` verb paid for
        discovery even on ``model``. ``name`` rides the identity allowlist and never
        reaches the keyspace at all."""
        from kanibako.settings.config_keys import agent_key_reason

        assert agent_key_reason("claude", tail) is None

    def test_a_leaf_ONLY_a_plugin_can_declare_DOES_ask(self, discovery_reds):
        """THE OTHER DIRECTION, without which the rows above would pass on a
        recogniser that had simply stopped consulting the plugins."""
        with pytest.raises(AssertionError, match="plugin discovery was asked"):
            _parse_persona_agent_key("agent.goose.a_leaf_core_cannot_answer")


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

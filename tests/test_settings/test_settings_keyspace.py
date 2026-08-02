"""Tests for the CLOSED-KEYSPACE declaration and validator (spec §0).

The conformance test at the bottom is the tripwire that tells P7 / the canon
phase they have finished their renames: it asserts the CLI-settable surface is a
SUBSET of the declared keyspace, exempting exactly the keys the arc is
mid-retiring.
"""

from __future__ import annotations

import pytest

from kanibako.settings.settings_keyspace import (
    DECLARED_AGENT_LEAVES,
    RESERVED_LEAF_NAMES,
    RETIRING_KEYS,
    is_valid_agent_segment,
    key_validity,
)

AGENTS = frozenset({"claude", "codex", "goose", "navigator℘claude"})


def valid(key: str) -> bool:
    return key_validity(key, valid_agents=AGENTS) is None


def reason(key: str) -> str:
    r = key_validity(key, valid_agents=AGENTS)
    assert r is not None, f"expected {key!r} to be INVALID"
    return r


# ---------------------------------------------------------------------------
# The AUTHORITATIVE surface: system.agent + agent.<agent>.** (spec §2h allowlist)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "system.agent",
    "agent.claude.model",
    "agent.claude.access",
    "agent.claude.allow_helpers",
    "agent.claude.continue_mode",
    "agent.claude.bootstrap",
    "agent.claude.run_args",
    "agent.claude.transform_settings",
    "agent.claude.endpoint",
    "agent.claude.template",
    "agent.claude.canon",
    "agent.default.access",
    "agent.navigator℘claude.model",
    "agent.claude.bindings.ro.share",
    "agent.claude.bindings.rw.thing",
    "agent.claude.common.plugins",
    "agent.claude.caches.transform",
    "agent.claude.seeded.template",
    "agent.claude.synced.credentials",
    "agent.claude.masks",
    "agent.claude.env.DISABLE_AUTOUPDATER",
    "agent.claude.secret_path.ANTHROPIC_AUTH_TOKEN",
])
def test_authoritative_agent_surface_is_valid(key):
    assert valid(key), reason(key)


def test_new_name_in_a_parametric_family_is_legal():
    """§2h — VALIDITY, not EXISTENCE.

    A NEW name inside a parametric family is exactly what a user may want to
    add via a pref. An EXISTENCE test would permit only modifying keys that
    already hold a value, which is the reading Jei rejected.
    """
    assert valid("agent.claude.bindings.rw.boooooo")
    assert valid("agent.claude.common.brand_new_thing")
    assert valid("box.caches.never_seen_before")
    assert valid("pref.agent.claude.bindings.rw.boooooo")


def test_fabrication_is_still_rejected():
    assert "not a valid agent" in reason("agent.zippity.wibble")
    assert "not a declared agent key" in reason("agent.claude.notakey")


def test_bare_agent_key_is_not_a_key():
    """§0 / §2d — the agent tier is DISCRIMINATED; a bare
    ``agent.<category>.<name>`` must be REFUSED, not quietly widened."""
    assert not valid("agent.bindings.rw.x")
    assert not valid("agent.common.plugins")
    assert not valid("agent.model")


def test_arm_less_binding_is_not_a_key():
    """spec §2d — bindings are declared per ARM."""
    r = reason("agent.claude.bindings.thing")
    assert "per ARM" in r
    assert not valid("box.bindings.thing")


def test_reserved_leaf_names_rejected():
    """spec §0 — a leaf may not be named after a public dict method."""
    assert "RESERVED" in reason("box.env.get")
    assert "RESERVED" in reason("agent.claude.common.items")
    assert "dunder" in reason("box.env.__init__")


def test_is_valid_agent_segment_accepts_default_and_members():
    assert is_valid_agent_segment("default", AGENTS)
    assert is_valid_agent_segment("claude", AGENTS)
    assert not is_valid_agent_segment("zippity", AGENTS)


def test_non_active_agent_is_valid():
    """§2h — the test is 'is it a VALID agent', NOT 'is it the ACTIVE
    agent': pre-configuring an agent you may switch to is allowed."""
    assert valid("agent.goose.model")
    assert valid("agent.codex.access")


# ---------------------------------------------------------------------------
# The SUPPORTING surface (message quality)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "config.data", "config.settings", "config.agents",
    "config.primary_workset", "config.registry", "config.journal",
    "system.channelroot", "system.template", "system.canon", "system.backup",
    "system.cache", "system.runtime", "system.setup_completed",
    "system.channels.common", "system.channels.chat", "system.channels.broadcast",
    "system.channels.mailboxes", "system.channels.share",
    "system.auth.share_allowed",
    "box.image", "box.share_images", "box.images_store", "box.canon",
    "box.shell", "box.enable_vault",
    "box.auth.global_enabled", "box.auth.workset_enabled",
    "workset.workspaces", "workset.boxes", "workset.logs", "workset.registry",
    "workset.template", "workset.canon", "workset.kuid",
    "workset.skip_kuid_check", "workset.vault_ro", "workset.vault_rw",
    "workset.channelroot",
    "workset.auth.share_allowed", "workset.auth.global_sync", "workset.auth.path",
    "workset.channels.common", "workset.channels.chat",
    "workset.channels.broadcast", "workset.channels.share",
    "workset.channels.mailboxes", "workset.channels.share_global",
    "box.bindings.rw.home", "box.bindings.ro.vault", "box.masks",
    "box.env.MYVAR", "box.secret_path.TOKEN",
])
def test_supporting_surface_is_valid(key):
    assert valid(key), reason(key)


@pytest.mark.parametrize("key", [
    "meta.runtime.ws_root", "meta.runtime.ws_name", "meta.runtime.project_type",
    "meta.workset.path", "meta.workset.name", "meta.workset.settings",
    "meta.box.path", "meta.box.name", "meta.box.mode", "meta.box.workspace",
    "meta.box.settings", "meta.box.inbox", "meta.box.share_global",
    "meta.box.share_workset", "meta.box.auth.workset_path",
    "meta.box.agent.model", "meta.box.agent.common.plugins",
    "meta.agent.claude.name", "meta.agent.claude.path",
    "meta.agent.claude.settings", "meta.agent.claude.mode",
    "meta.agent.claude.exec", "meta.agent.claude.auth.share_support",
])
def test_meta_families_are_valid(key):
    assert valid(key), reason(key)


def test_the_cut_meta_derived_family_is_refused():
    """R-8 (option 2) — the ``meta.derived.*`` key family is CUT: it is not a
    key, and the refusal is the ORDINARY unknown-meta-group refusal."""
    assert "not a declared meta group" in reason("meta.derived.x")
    assert "not a declared meta group" in reason(
        "meta.derived.agent.claude.common.plugins"
    )


def test_the_reserved_binding_derivations_node_is_not_a_key():
    """R-8 / D-4 — ``binding_derivations`` is the reserved INTERNAL snapshot
    node (manifest ``not_keys.reserved_internal``): refused by the closed head
    dispatch by construction, so it can never be re-claimed as a key."""
    assert "not a declared namespace" in reason("binding_derivations.x")
    assert "not a declared namespace" in reason(
        "binding_derivations.agent.claude.common.plugins"
    )


def test_unknown_namespace_rejected():
    assert "not a declared namespace" in reason("zippity.wibble")


def test_workset_channel_addresses_into_the_system_stores_are_keys():
    """R-35 (spec §2c) — ``workset.channels.{mailboxes,share_global}`` are KEYS.

    Mailboxes still AGGREGATE at system only (§2f); the workset leaf is the
    workset's declared ADDRESS into that aggregate
    (``@system.channels.mailboxes/@meta.workset.name``), present in EVERY mode.
    This test previously pinned the EXCLUSION of ``workset.channels.mailboxes``
    — that pin mis-read aggregation-at-system as absence-from-the-family, and
    made the validity table refuse a leaf the launch floor accepted (R-35,
    RATIFIED: fix the CODE).
    """
    assert valid("system.channels.mailboxes")
    assert valid("workset.channels.mailboxes")
    assert valid("workset.channels.share_global")
    # ``share_global`` is the WORKSET-scope address; at system scope the store
    # is plain ``share`` — the workset spelling is not a system leaf.
    assert not valid("system.channels.share_global")


def test_launch_floor_and_validity_table_agree_on_workset_channel_leaves():
    """R-35's actual failure mode: the launch floor's allowlist and the
    validity table are the SAME question asked at two seams, and they had
    drifted apart (``mailboxes`` accepted by the floor, refused by the table).
    Pin them EQUAL so neither declaration can move alone."""
    from kanibako.settings.settings_keyspace import (
        DECLARED_WORKSET_CHANNEL_LEAVES,
    )
    from kanibako.settings.settings_launch import _WORKSET_CHANNEL_LEAVES

    assert _WORKSET_CHANNEL_LEAVES == DECLARED_WORKSET_CHANNEL_LEAVES


def test_pref_validity_delegates_to_the_target():
    assert valid("pref.system.agent")
    assert valid("pref.agent.claude.model")
    assert "its target is not a declared key" in reason("pref.agent.zippity.x")


def test_pref_of_pref_is_not_a_key():
    """§2h categorical tier — request-of-request has no termination argument."""
    assert "termination argument" in reason("pref.pref.system.agent")


def test_empty_and_malformed():
    assert key_validity("", valid_agents=AGENTS) is not None
    assert "empty path segment" in reason("box..image")


# ---------------------------------------------------------------------------
# ⚑ THE CONFORMANCE TRIPWIRE
# ---------------------------------------------------------------------------

def test_known_config_keys_are_valid_under_the_validator():
    """Every CLI-settable key must be a KEY — exempting only the retiring three.

    ⚑ The two lists answer DIFFERENT questions (``settings_keyspace`` = "is this
    a key?"; ``KNOWN_CONFIG_KEYS`` = "where does a set of it land?"), so this
    asserts ONE direction only: settable ⊆ declared. The validator is
    deliberately the superset.

    ⚑ If a FOURTH key needs exempting, that is a spec/settable-surface
    disagreement this arc has not accounted for — STOP and report it rather than
    widening :data:`RETIRING_KEYS`.
    """
    from kanibako.settings.config_keys import KNOWN_CONFIG_KEYS

    # The bare scalars are the CLI's shorthand for the any-agent
    # ``agent.default.<key>`` tier (config_keys: "the bare key is the
    # any-agent agent.default tier"), so they are validated in that form.
    offenders = {}
    for key in KNOWN_CONFIG_KEYS:
        if key in RETIRING_KEYS:
            continue
        probe = key if "." in key else f"agent.default.{key}"
        r = key_validity(probe, valid_agents=AGENTS)
        if r is not None:
            offenders[key] = r
    assert not offenders, (
        "settable keys that are not declared keys (widen the spec or fix the "
        f"settable surface — do NOT widen RETIRING_KEYS): {offenders}"
    )


def test_retiring_keys_is_empty():
    """Pins the exemption set EMPTY so re-growing it is a deliberate, reviewed act.

    ⮕ P7 removed ``box.agent_name`` (RETIRED → ``pref.system.agent``, §2b) and
    ``system.default_agent`` (RENAMED → ``system.agent``, §2g); the C-CANON seeds
    half landed M-11 (``system.base_template`` → ``system.template``), which was the
    last one. The "closed-keyspace resolve enforcement" follow-on is GATED on this
    set being empty, so it is now unblocked from this side.

    ⚑ A new entry here means the spec and the settable surface have diverged
    somewhere unaccounted for — STOP and report, do not widen the set.
    """
    assert RETIRING_KEYS == frozenset()


def test_the_retired_base_template_spelling_is_gone():
    """M-11 is a RENAME, not an alias: the old spelling must be neither settable
    nor a declared key, so ``config set system.base_template`` refuses."""
    from kanibako.settings.config_keys import KNOWN_CONFIG_KEYS

    assert "system.base_template" not in KNOWN_CONFIG_KEYS
    assert "system.template" in KNOWN_CONFIG_KEYS
    assert "system.canon" in KNOWN_CONFIG_KEYS
    assert key_validity("system.base_template", valid_agents=AGENTS) is not None
    assert key_validity("system.template", valid_agents=AGENTS) is None
    assert key_validity("system.canon", valid_agents=AGENTS) is None


def test_retiring_keys_are_all_invalid_today():
    """Each exemption must actually be invalid — an exemption for a key that
    validates is dead weight that would hide a real regression."""
    for key in RETIRING_KEYS:
        assert key_validity(key, valid_agents=AGENTS) is not None, key


def test_declared_agent_leaves_cover_the_spec_2d_default_tier():
    """Spot-check against spec §2d so a silent deletion is caught."""
    assert {
        "access", "allow_helpers", "continue_mode", "bootstrap", "model",
        "run_args", "transform_settings", "endpoint", "template", "canon",
    } <= DECLARED_AGENT_LEAVES


def test_retired_auto_approve_is_not_a_key():
    """R-41 RETIRED the boolean spelling: it is UNDECLARED, so the closed
    keyspace must REFUSE it by name (spec §0).  A stored one is separately
    refused at launch with the mapping — see
    ``settings_assemble.refuse_retired_behavior_keys``."""
    assert "auto_approve" not in DECLARED_AGENT_LEAVES
    for key in (
        "agent.claude.auto_approve",
        "agent.default.auto_approve",
        "pref.agent.claude.auto_approve",
    ):
        assert key_validity(key, valid_agents=AGENTS) is not None, key


def test_access_tier_vocabulary_is_declared_once():
    """The enum + its default live beside the leaf they belong to, so the
    settable surface, the launch resolver and the plugin descriptors read ONE
    list (R-41)."""
    from kanibako.settings.settings_keyspace import ACCESS_DEFAULT, ACCESS_TIERS

    assert ACCESS_TIERS == ("restricted", "editing", "full")
    assert ACCESS_DEFAULT == "full"
    assert ACCESS_DEFAULT in ACCESS_TIERS


def test_reserved_names_match_the_keystore_write_time_set():
    """SHOULD-6 drift guard, asserted as a TEST as well as a module assert.

    Two copies of one collision-safety floor that disagree is worse than one: a
    name accepted by the validator and rejected by the store fails deep in the
    write with no reference to the key the user typed.
    """
    from kanibako.settings.settings_store import _RESERVED_KEY_NAMES

    assert RESERVED_LEAF_NAMES == _RESERVED_KEY_NAMES

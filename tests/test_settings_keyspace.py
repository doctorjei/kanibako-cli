"""Tests for the CLOSED-KEYSPACE declaration and validator (spec §0).

The conformance test at the bottom is the tripwire that tells P7 / the canon
phase they have finished their renames: it asserts the CLI-settable surface is a
SUBSET of the declared keyspace, exempting exactly the keys the arc is
mid-retiring.
"""

from __future__ import annotations

import pytest

from kanibako.settings_keyspace import (
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
    "agent.claude.auto_approve",
    "agent.claude.allow_helpers",
    "agent.claude.continue_mode",
    "agent.claude.bootstrap",
    "agent.claude.run_args",
    "agent.claude.transform_settings",
    "agent.claude.endpoint",
    "agent.claude.template",
    "agent.claude.canon",
    "agent.default.auto_approve",
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
    """§2h L1225-1228 — VALIDITY, not EXISTENCE.

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
    """§0 L21 / §2d — the agent tier is DISCRIMINATED; a bare
    ``agent.<category>.<name>`` must be REFUSED, not quietly widened."""
    assert not valid("agent.bindings.rw.x")
    assert not valid("agent.common.plugins")
    assert not valid("agent.model")


def test_arm_less_binding_is_not_a_key():
    """spec §2d L960-964 — bindings are declared per ARM."""
    r = reason("agent.claude.bindings.thing")
    assert "per ARM" in r
    assert not valid("box.bindings.thing")


def test_reserved_leaf_names_rejected():
    """spec §0 L168-173 — a leaf may not be named after a public dict method."""
    assert "RESERVED" in reason("box.env.get")
    assert "RESERVED" in reason("agent.claude.common.items")
    assert "dunder" in reason("box.env.__init__")


def test_is_valid_agent_segment_accepts_default_and_members():
    assert is_valid_agent_segment("default", AGENTS)
    assert is_valid_agent_segment("claude", AGENTS)
    assert not is_valid_agent_segment("zippity", AGENTS)


def test_non_active_agent_is_valid():
    """§2h L1221 — the test is 'is it a VALID agent', NOT 'is it the ACTIVE
    agent': pre-configuring an agent you may switch to is allowed."""
    assert valid("agent.goose.model")
    assert valid("agent.codex.auto_approve")


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
    "meta.derived.agent.claude.common.plugins",
    "meta.derived.workset.caches.build",
])
def test_meta_families_are_valid(key):
    assert valid(key), reason(key)


def test_meta_derived_rejects_an_invalid_declaration_key():
    """spec §0 L94-100 — meta.derived.<declaration-key>; the declaration must
    itself be a key, else the derived name means nothing."""
    assert "declaration key is invalid" in reason("meta.derived.zippity.wibble")


def test_unknown_namespace_rejected():
    assert "not a declared namespace" in reason("zippity.wibble")


def test_workset_has_no_mailboxes_channel():
    """§2f L1139 — mailboxes aggregate at SYSTEM only."""
    assert valid("system.channels.mailboxes")
    assert not valid("workset.channels.mailboxes")


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
    from kanibako.config_interface import KNOWN_CONFIG_KEYS

    # The bare scalars are the CLI's shorthand for the any-agent
    # ``agent.default.<key>`` tier (config_interface: "the bare key is the
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


def test_retiring_keys_are_exactly_the_three_known_renames():
    """Pins the exemption set so growing it is a deliberate, reviewed act."""
    assert RETIRING_KEYS == frozenset({
        "box.agent_name", "system.default_agent", "system.base_template",
    })


def test_retiring_keys_are_all_invalid_today():
    """Each exemption must actually be invalid — an exemption for a key that
    validates is dead weight that would hide a real regression."""
    for key in RETIRING_KEYS:
        assert key_validity(key, valid_agents=AGENTS) is not None, key


def test_declared_agent_leaves_cover_the_spec_2d_default_tier():
    """Spot-check against spec §2d L957-1013 so a silent deletion is caught."""
    assert {
        "auto_approve", "allow_helpers", "continue_mode", "bootstrap", "model",
        "run_args", "transform_settings", "endpoint", "template", "canon",
    } <= DECLARED_AGENT_LEAVES


def test_reserved_names_match_the_keystore_write_time_set():
    """SHOULD-6 drift guard, asserted as a TEST as well as a module assert.

    Two copies of one collision-safety floor that disagree is worse than one: a
    name accepted by the validator and rejected by the store fails deep in the
    write with no reference to the key the user typed.
    """
    from kanibako.settings_store import _RESERVED_KEY_NAMES

    assert RESERVED_LEAF_NAMES == _RESERVED_KEY_NAMES

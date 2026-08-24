"""Tests for the CLOSED-KEYSPACE declaration and validator (spec §0).

The conformance test at the bottom is the tripwire that tells P7 / the canon
phase they have finished their renames: it asserts the CLI-settable surface is a
SUBSET of the declared keyspace, exempting exactly the keys the arc is
mid-retiring.
"""

from __future__ import annotations

import pytest

from kanibako.settings.keyspace_manifest import manifest_doc
from kanibako.settings.settings_keyspace import (
    DECLARED_AGENT_LEAVES,
    DECLARED_META_ASSEMBLY_LEAVES,
    DECLARED_META_RUNTIME_LEAVES,
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
    "agent.claude.transform",
    "agent.claude.transform_settings",
    "agent.claude.endpoint",
    "agent.claude.template",
    "agent.claude.canon",
    "agent.default.access",
    "agent.navigator℘claude.model",
    "agent.claude.bindings.ro",
    "agent.claude.bindings.rw",
    # ⚑ TERMINAL and DEST-KEYED since 2026-08-08c: the category token IS the
    # whole key. No entry name follows it — see the terminal tests below.
    "agent.claude.common",
    "agent.claude.caches",
    "agent.claude.seeded",
    "agent.claude.synced",
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

    ⚑ The families that still carry a free <name> are the VAR-keyed ones
    (``env`` / ``secret_path``) and the agent discriminator. The bind-shaped
    CATEGORIES no longer do: all six went TERMINAL and DEST-KEYED (the two
    ``bindings`` arms at R-5/R-10, then ``caches``/``seeded``/``common``/
    ``synced`` on 2026-08-08c), so the destination is DATA inside the value and
    the free name it replaced is gone from the keyspace entirely. That is not a
    narrowing of §2h — a user may still add a destination nobody declared; it is
    simply no longer expressed as a key segment.
    """
    assert valid("agent.claude.env.BRAND_NEW_THING")
    assert valid("box.secret_path.NEVER_SEEN_BEFORE")
    assert valid("pref.agent.claude.env.BRAND_NEW_THING")
    # ⚑ NOT a bind-shaped category, in EITHER of its two terminal depths.
    assert not valid("agent.claude.bindings.rw.boooooo")
    assert not valid("agent.claude.common.brand_new_thing")
    assert not valid("box.caches.never_seen_before")


def test_fabrication_is_still_rejected():
    assert "not a valid agent" in reason("agent.zippity.wibble")
    assert "not a declared agent key" in reason("agent.claude.notakey")


def test_bare_agent_key_is_not_a_key():
    """§0 / §2d — the agent tier is DISCRIMINATED; a bare
    ``agent.<category>.<name>`` must be REFUSED, not quietly widened."""
    assert not valid("agent.bindings.rw.x")
    assert not valid("agent.common")
    assert not valid("agent.model")


def test_arm_less_binding_is_not_a_key():
    """spec §2d — bindings are declared per ARM."""
    r = reason("agent.claude.bindings.thing")
    assert "per ARM" in r
    assert not valid("box.bindings.thing")
    # The bare category root is not a key either.
    assert "per ARM" in reason("box.bindings")


# ---------------------------------------------------------------------------
# TERMINAL, DEST-KEYED categories (spec §2a; disk-store R-5/R-10, 2026-08-06c)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "box.bindings.ro", "box.bindings.rw",
    "system.bindings.ro", "workset.bindings.rw",
    "agent.default.bindings.ro", "agent.claude.bindings.rw",
])
def test_a_bindings_arm_is_a_key_on_its_own(key):
    """R-5 — the ARM is the whole of the key; its VALUE is the dest-keyed map."""
    assert valid(key), reason(key)


@pytest.mark.parametrize("key", [
    "box.bindings.ro.vault",
    "box.bindings.rw.home",
    "workset.bindings.rw.data",
    "agent.claude.bindings.ro.launcher",
    "agent.default.bindings.rw.thing",
    # A real destination — dots, slashes and all. It is DATA, never a key.
    "box.bindings.ro.~/.claude/settings.json",
    # ⚑ THE NON-VACUITY CASE. Under the pre-P4' parser this tail was ALREADY
    # refused — by ``leaf_name_reason``, as a RESERVED dict-method name. So it is
    # refused before AND after, and only the REASON tells the two apart. Without
    # the message assertions below, this row would survive the mutation and the
    # proof would be worthless (the exact failure mode this arc has already hit
    # once). See the mutation note in the docstring.
    "box.bindings.ro.get",
])
def test_a_name_or_dest_under_a_bindings_arm_is_not_a_key(key):
    """⚑ THE R-5/R-10 REFUSAL. Nothing may follow ``bindings.{ro,rw}``.

    Two things are asserted, and the SECOND is what makes this test non-vacuous:
    the key is refused AND the reason says why — that the arm is TERMINAL and the
    entries are destinations inside its VALUE. Without the discriminator this
    would also pass under the old parser, which refused several of these for a
    completely different reason (``leaf_name_reason``, wrong arm token, …).

    ⚑ MUTATION: delete the ``if len(rest) == 2: return None`` / terminal-tail
    refusal pair in ``settings_keyspace._category_reason`` and restore
    ``len(rest) < 3`` — every key here becomes VALID and this test dies. Nothing
    else in the suite emits the word TERMINAL for a bindings key.
    """
    r = reason(key)
    assert "TERMINAL" in r, r
    assert "destinations inside its value" in r, r
    assert "no entry NAME" in r or "no entry name" in r.lower(), r


@pytest.mark.parametrize("key", [
    "box.common", "box.caches", "box.seeded", "box.synced",
    "system.caches", "workset.seeded",
    "agent.default.common", "agent.claude.synced",
])
def test_a_dest_keyed_category_is_a_key_on_its_own(key):
    """2026-08-08c — the category TOKEN is the whole of the key; its VALUE is the
    dest-keyed map. The same rule R-5 gave the ``bindings`` arms, one segment
    shallower."""
    assert valid(key), reason(key)


@pytest.mark.parametrize("key", [
    "box.common.plugins",
    "box.caches.pip",
    "system.seeded.template",
    "workset.synced.credentials",
    "agent.claude.common.plugins",
    "agent.default.caches.transform",
    # A real destination — dots, slashes and all. It is DATA, never a key.
    "agent.claude.common.~/.claude/plugins",
    # ⚑ THE NON-VACUITY CASE, exactly as for the bindings arms: this tail was
    # ALREADY refused under the name-keyed parser — by ``leaf_name_reason``, as a
    # RESERVED dict-method name. Only the REASON tells the two parsers apart, so
    # the message assertions below are what make this row prove anything.
    "agent.claude.common.items",
])
def test_a_name_under_a_dest_keyed_category_is_not_a_key(key):
    """⚑ THE 2026-08-08c REFUSAL. Nothing may follow ``caches``/``seeded``/
    ``common``/``synced``.

    ⚑ MUTATION: in ``settings_keyspace._category_reason``, delete the
    ``if len(rest) == 1: return None`` / refusal pair in the four-category branch
    and fall through to the trailing ``return None`` — every key here becomes
    VALID and this test dies. Nothing else in the suite emits the word TERMINAL
    for one of these four.
    """
    r = reason(key)
    assert "TERMINAL" in r, r
    assert "destinations inside its value" in r, r
    assert "have no NAME" in r or "no entry name" in r.lower(), r


def test_the_terminal_categories_are_declared_in_one_place():
    """ALL SEVEN dest-keyed categories are the SAME shape, and the parser and the
    pref walker must agree on which keys they are — see
    ``settings_prefs._flatten_pref_node``. ONE constant; the two predicates over it
    (:func:`is_terminal_category_tail` here, :func:`is_terminal_category_key` in the
    class below) differ only in WHERE the tail may sit, never in WHICH tails there
    are.

    ⚑ The seven sit at TWO depths: a ``bindings`` ARM is terminal at
    ``bindings.{ro,rw}``, while ``masks`` and the four category tokens are
    terminal ONE SEGMENT SHALLOWER. The tail matcher must handle both, which is
    why it compares a SUFFIX rather than a fixed length.
    """
    from kanibako.settings.settings_keyspace import (
        TERMINAL_CATEGORY_TAILS,
        is_terminal_category_tail,
    )

    assert TERMINAL_CATEGORY_TAILS == frozenset({
        ("masks",), ("bindings", "ro"), ("bindings", "rw"),
        ("caches",), ("seeded",), ("common",), ("synced",),
    })
    assert is_terminal_category_tail(("box", "masks"))
    assert is_terminal_category_tail(("agent", "claude", "bindings", "rw"))
    assert is_terminal_category_tail(("bindings", "ro"))
    # The four that went terminal on 2026-08-08c, at every scope depth.
    assert is_terminal_category_tail(("box", "common"))
    assert is_terminal_category_tail(("agent", "claude", "caches"))
    assert is_terminal_category_tail(("workset", "seeded"))
    assert is_terminal_category_tail(("synced",))
    # NOT terminal: a bare arm root, a tail that runs PAST a terminal key, an
    # arm-shaped tail that is not a bindings arm, and the empty tail.
    assert not is_terminal_category_tail(("box", "bindings"))
    assert not is_terminal_category_tail(("box", "masks", "ro"))
    assert not is_terminal_category_tail(("box", "common", "plugins"))
    assert not is_terminal_category_tail(("ro",))
    assert not is_terminal_category_tail(())


class TestTerminalCategoryKeyMatchesOnPosition:
    """``is_terminal_category_key`` answers on the category's POSITION.

    ⚑⚑ THE WHOLE POINT IS THAT IT IS NOT :func:`is_terminal_category_tail`. That one
    is a SUFFIX test, correct for a caller holding a TAIL and WRONG for a caller
    holding a whole key: ``system.channels.common`` / ``workset.channels.common`` are
    the CHANNEL type-roots (spec §2c/§2f/§2g), ordinary path SCALARS that merely END
    in a category token, and the suffix test claims them while their siblings
    ``…channels.chat`` / ``…channels.share`` fall through — one family, two rules.
    Spec §2a names the discriminator itself: *"the discriminator is the ``channels.``
    segment, which the channel form always carries and the category form never
    does"* — a category token is a category only where the SCOPE ends.

    ⚑ These tests came from ``test_commands/test_start.py`` (QC), where the predicate
    was a private copy. They moved WITH it; what stayed there is the FOLD behaviour
    that copy was bought for.
    """

    @staticmethod
    def _pred(key):
        from kanibako.settings.settings_keyspace import is_terminal_category_key

        return is_terminal_category_key(key)

    def test_the_true_set_is_exactly_a_scope_plus_a_declared_tail(self):
        """DERIVED on both axes — the enumeration is not written down here.

        The categories come from ``settings_keyspace.TERMINAL_CATEGORY_TAILS`` and
        the scopes from ``kb_store.SCOPE_CONTAINMENT``, so a seventh category
        or a fifth scope is covered by this test the day it is declared. That is
        the property the last flip lacked: four separate defects came from lookups
        keyed on a spelling that changed, each frozen where the declaration moved.
        """
        from kanibako.settings.kb_store import SCOPE_CONTAINMENT
        from kanibako.settings.settings_keyspace import TERMINAL_CATEGORY_TAILS

        for tail in TERMINAL_CATEGORY_TAILS:
            cat = ".".join(tail)
            for scope in SCOPE_CONTAINMENT:
                if scope == "agent":
                    # The agent tier is DISCRIMINATED (spec §0/§2d): the key is
                    # ``agent.<node>.<category>``, a BARE ``agent.<category>`` is
                    # not a key at all, and one segment DEEPER than the node is
                    # the false-positive class.
                    assert self._pred(f"agent.claude.{cat}")
                    assert self._pred(f"agent.default.{cat}")
                    assert not self._pred(f"agent.{cat}")
                    assert not self._pred(f"agent.claude.channels.{cat}")
                    continue
                assert self._pred(f"{scope}.{cat}")
                # ONE SEGMENT DEEPER is never a category key, whatever the
                # intervening token: that is the false-positive class.
                assert not self._pred(f"{scope}.channels.{cat}")
                assert not self._pred(f"{scope}.auth.{cat}")
            # Unscoped, and the ``pref``/``meta`` mirrors, are out of this
            # function's domain — the launch floor is keyed by SCOPE alone.
            assert not self._pred(cat)
            assert not self._pred(f"pref.box.{cat}")
            assert not self._pred(f"meta.box.agent.{cat}")

    def test_a_channels_type_root_fails_the_predicate_itself(self):
        """The two enumerated false positives, excluded BY CONSTRUCTION.

        Named rather than derived because these two are the whole reason the
        predicate changed shape: both are ``type: path`` in the manifest, i.e.
        SCALARS, and both answered True to the tail match. The sibling
        ``<scope>.common`` MOUNT category — one word, the other sense (spec §2a
        "ONE WORD, ``common``, for both senses") — must still answer True, or the
        fix would have closed the hole by breaking the category.
        """
        assert not self._pred("system.channels.common")
        assert not self._pred("workset.channels.common")
        assert self._pred("system.common")
        assert self._pred("workset.common")

    def test_a_persona_node_is_one_segment_and_the_grammar_enforces_it(self):
        """The agent scope is EXACTLY two segments, and that is not an assumption.

        ``agent_ref.parse_agent_ref`` admits only alphanumerics plus ``-``/``_`` in
        a segment and carries ``_DOT_HINT`` — *"'.' is reserved as the settings
        key-path separator and cannot appear in an agent name"* — so a persona node
        cannot widen ``agent.<node>`` beyond two segments. The premise is asserted here
        rather than trusted, because if the grammar ever admitted a dot this
        predicate would start answering False for that box's agent-scope binds and
        drop them into last-wins, silently deleting an earlier family's map.

        (⚑ ``settings_categories.AGENT_BIND_KEY_RE``'s comment claims the opposite,
        citing ``navigator.v2℘claude``. Its non-greedy node costs nothing, but the
        prose is wrong against this grammar — reported, not edited: that module is
        not this seam.)
        """
        from kanibako.agent_ref import canonicalize_agent_ref
        from kanibako.errors import ConfigError

        # The canonical node of a real persona ref is ONE segment...
        assert canonicalize_agent_ref("navigator+claude") == "navigator℘claude"
        assert self._pred("agent.navigator℘claude.seeded")
        assert self._pred("agent.navigator℘claude.bindings.ro")
        # ...and a dotted persona is refused at the grammar, not later.
        with pytest.raises(ConfigError):
            canonicalize_agent_ref("navigator.v2+claude")

    def test_the_two_predicates_answer_DIFFERENT_questions(self):
        """The pair is the point: same SET, different DOMAIN.

        Both read :data:`TERMINAL_CATEGORY_TAILS`, so they can never disagree about
        WHICH categories are terminal. They disagree about WHERE one may appear, and
        the types say which question a call site is asking — segments for a tail, a
        dotted string for a key. Deleting either and pointing its callers at the
        other reintroduces one of the two defects.

        ⚑ MUTATION: make ``is_terminal_category_key`` delegate to
        ``is_terminal_category_tail(key.split("."))`` -> the ``channels`` rows below
        flip and this dies. Make the tail predicate demand a scope -> the bare-tail
        rows die, and ``agent_defaults.load_category_binds`` (which is handed a BARE
        category token, never a key) starts refusing every plugin declaration.
        """
        from kanibako.settings.settings_keyspace import is_terminal_category_tail

        # A whole key whose category sits where the SCOPE ends: BOTH say yes.
        for key in ("box.masks", "system.bindings.rw", "agent.claude.caches",
                    "workset.seeded", "box.common", "system.synced"):
            assert is_terminal_category_tail(key.split(".")), key
            assert self._pred(key), key
        # A BARE tail — what a plugin declaration carries: only the TAIL test.
        for tail in ("caches", "bindings.ro", "masks"):
            assert is_terminal_category_tail(tail.split(".")), tail
            assert not self._pred(tail), tail
        # A leaf that merely ENDS in a category token: only the tail test, and that
        # is the defect. ⚑ The meta pair joined this class on 2026-08-10b, when the
        # collapse's copy output split into leaves NAMED for the categories they
        # carry — a second, independent way for the suffix test to be fooled, and
        # the reason the position predicate is the one every key-shaped call site
        # uses. Neither reaches a tail call site: those are handed a plugin's bare
        # category token or a ``pref:`` child, never a ``meta.*`` key.
        for key in ("system.channels.common", "workset.channels.common",
                    "meta.assembly.seeded", "meta.assembly.synced"):
            assert is_terminal_category_tail(key.split(".")), key
            assert not self._pred(key), key

    def test_the_channel_type_roots_classify_UNIFORMLY(self):
        """One family, one answer — enumerated, not hand-listed.

        ``common`` is the ONLY channel type whose name collides with a category
        token, so under the suffix test exactly one member of each ``channels``
        family answered differently from its siblings. Deriving the members from the
        manifest means a channel type named after a future category is covered the
        day it is declared.
        """
        doc = manifest_doc()
        families: dict[str, list[str]] = {}
        for key in doc["keys"]:
            head, _, rest = str(key).partition(".channels.")
            if rest and "." not in rest:
                families.setdefault(head, []).append(str(key))
        assert set(families) == {"system", "workset"}, families
        for scope, members in families.items():
            # Every member is a channel type-root, so every member is False.
            assert not any(self._pred(k) for k in members), (scope, members)
            # And the collision is real: without it this test proves nothing.
            assert f"{scope}.channels.common" in members, members

    def test_no_DECLARED_key_gains_the_category_family(self):
        """The accepted set only ever SHRANK: nothing new was let in.

        Enumerated over every spelling the ratified manifest declares — the corpus
        that makes the claim checkable rather than asserted. ``ADDED`` must be empty,
        and the removals must be exactly the keys that merely END in a category
        token: the two channel type-roots, and the two collapse-output leaves the
        2026-08-10b split NAMED for the categories they carry. Both classes are
        DECLARED keys that are not category keys, which is the whole reason the
        position predicate exists.
        """
        from kanibako.settings.settings_keyspace import is_terminal_category_tail

        declared = {str(k) for k in manifest_doc()["keys"]}
        suffix = {k for k in declared if is_terminal_category_tail(k.split("."))}
        position = {k for k in declared if self._pred(k)}
        assert position - suffix == set()
        assert suffix - position == {
            "system.channels.common", "workset.channels.common",
            "meta.assembly.seeded", "meta.assembly.synced",
        }


def test_the_bind_shaped_terminal_mirror_cannot_drift():
    """``settings_categories._TERMINAL_BIND_CATEGORIES`` is a MIRROR of the constant
    above, spelled again because that module is deliberately stdlib-only (it imports
    only stdlib + the expression engine, so it cannot import this one). Pin the two
    equal on the BIND-SHAPED members, so a category that goes dest-keyed cannot be
    declared terminal in one place and left per-entry-keyed in the other.

    ⚑ ``masks`` is terminal but NOT bind-shaped, so it is deliberately absent from
    the mirror — the comparison excludes it rather than pretending it is missing.
    """
    from kanibako.settings.settings_categories import (
        _BIND_CATEGORIES,
        _NON_TERMINAL_BIND_CATEGORIES,
        _TERMINAL_BIND_CATEGORIES,
    )
    from kanibako.settings.settings_keyspace import TERMINAL_CATEGORY_TAILS

    # The keyspace's terminal tails, restricted to the bind-shaped categories.
    bind_shaped_terminal = {
        cat for cat in _BIND_CATEGORIES
        if tuple(cat.split(".")) in TERMINAL_CATEGORY_TAILS
    }
    assert set(_TERMINAL_BIND_CATEGORIES) == bind_shaped_terminal
    # ...and the complement really is a complement (no member in both, none lost).
    assert not set(_TERMINAL_BIND_CATEGORIES) & set(_NON_TERMINAL_BIND_CATEGORIES)
    assert (
        set(_TERMINAL_BIND_CATEGORIES) | set(_NON_TERMINAL_BIND_CATEGORIES)
        == set(_BIND_CATEGORIES)
    )


def test_masks_and_bindings_arms_refuse_a_tail_the_same_way():
    """Two dest-keyed categories, ONE story (Code Convention 0). Both say the
    entries are destinations inside the VALUE, not key segments."""
    for key in ("box.masks./some/path", "box.bindings.ro./some/path"):
        r = reason(key)
        assert "not a key" in r
        assert "destinations" in r or "box destinations" in r


def test_reserved_leaf_names_rejected():
    """spec §0 — a leaf may not be named after a public dict method.

    ⚑ The probes are VAR-keyed (``env`` / ``secret_path``) because those are the
    only families left whose free segment is a KEY segment and therefore reaches
    ``leaf_name_reason``. A reserved name under a bind-shaped category is now
    refused one step earlier, as a tail past a TERMINAL key — pinned by
    ``test_a_name_under_a_dest_keyed_category_is_not_a_key``, whose non-vacuity
    case is exactly ``…common.items``.
    """
    assert "RESERVED" in reason("box.env.get")
    assert "RESERVED" in reason("agent.claude.env.items")
    assert "RESERVED" in reason("agent.claude.secret_path.copy")
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
    "box.bindings.rw", "box.bindings.ro", "box.masks",
    "box.env.MYVAR", "box.secret_path.TOKEN",
])
def test_supporting_surface_is_valid(key):
    assert valid(key), reason(key)


@pytest.mark.parametrize("key", [
    "meta.runtime.ws_root", "meta.runtime.ws_name", "meta.runtime.project_type",
    "meta.assembly.bindings", "meta.assembly.seeded", "meta.assembly.synced",
    "meta.assembly.env",
    "meta.workset.path", "meta.workset.name", "meta.workset.settings",
    "meta.box.path", "meta.box.name", "meta.box.mode", "meta.box.workspace",
    "meta.box.settings", "meta.box.inbox", "meta.box.share_global",
    "meta.box.share_workset", "meta.box.auth.workset_path",
    "meta.box.home", "meta.box.container_name", "meta.box.helper_num",
    "meta.box.agent.model", "meta.box.agent.common",
    "meta.agent.claude.name", "meta.agent.claude.path",
    "meta.agent.claude.settings", "meta.agent.claude.mode",
    "meta.agent.claude.exec", "meta.agent.claude.auth.share_support",
])
def test_meta_families_are_valid(key):
    assert valid(key), reason(key)


# ---------------------------------------------------------------------------
# Manifest <-> declaration drift guards
# ---------------------------------------------------------------------------

def _manifest_leaves(prefix: str) -> set[str]:
    """The manifest's DIRECT leaves under *prefix* — nested rows dropped.

    ⚑ The manifest is read through :func:`kanibako.settings.keyspace_manifest
    .manifest_doc`, which resolves the INSTALLED package data rather than a
    repo-relative path — that is what makes a guard here a statement about the
    ARTEFACT rather than about this checkout.  Four ad-hoc ``importlib.resources``
    reads (three here, one in ``test_config_dest_parity``) each restated that
    property in their own docstring; the loader now carries it once.
    """
    tails = {
        str(k)[len(prefix):] for k in manifest_doc()["keys"] if str(k).startswith(prefix)
    }
    return {t for t in tails if "." not in t}


def test_the_manifest_reserved_leaf_names_match_the_code():
    """The reserved-name floor (spec §0) has a THIRD carrier — pin it to the code.

    ``settings_keyspace.RESERVED_LEAF_NAMES`` IS ``KeyStore.RESERVED_KEY_NAMES``
    (an alias — identity pinned by
    ``test_the_keyspace_floor_is_the_store_set_not_a_copy``), so the two CODE
    carriers cannot drift apart. The manifest is a SEPARATE, hand-maintained
    carrier that ships in the wheel as release authority, and until this case
    NOTHING read it: ruling 25 (2026-08-12) added the store's own two public
    members to the code and the manifest silently stayed at the 11 ``dict``
    methods, with the whole gate green.

    ⚑ Asserted against ``KeyStore.RESERVED_KEY_NAMES``, never the alias — the
    alias would be comparing the honest source to itself under a second name.
    """
    from kanibako.settings.keystore import KeyStore

    manifest = set(manifest_doc()["policy"]["reserved_leaf_names"])
    code = set(KeyStore.RESERVED_KEY_NAMES)
    # Named in BOTH directions: a bare set == set on 13 strings says nothing
    # actionable at the moment it fires.
    assert manifest == code, (
        f"reserved_leaf_names drift — in code not manifest: "
        f"{sorted(code - manifest)}; in manifest not code: "
        f"{sorted(manifest - code)}"
    )


# ---------------------------------------------------------------------------
# The COLLAPSE outputs — spec §1A, ratified 2026-08-08f, MOVED to
# ``meta.assembly.*`` on 2026-08-09
# ---------------------------------------------------------------------------

#: ``meta.assembly.{bindings,seeded,synced,env}``. The collapse writes ALL FOUR on the
#: launch path (``commands/start.py._install_assembly_collapse``). They are declared
#: because under the closed keyspace (spec §0) an undeclared key is not a key and
#: reading one is an error — so the declaration is what makes the name legal, and
#: it is the ONLY thing that changed. Nothing here asserts a value.
#: ⚑ ``env`` joined 2026-08-14: the env VARIABLES realize through the same collapse
#: the mounts do (system-first, write-once), so their arbitrated result is a member
#: of this group and not of the launch seam's return carrier.
COLLAPSE_LEAVES = ("bindings", "seeded", "synced", "env")

#: The 2026-08-10b split, stated as the names it RETIRED. ``copies`` was renamed to
#: ``seeded`` and ``backup`` — reserved for the writeback — was retired outright,
#: one sync list serving both directions. ``meta.assembly.*`` post-dates
#: ``v1.8.0-rc1``, so the break ships clean: no alias, no shim, and the old
#: spellings must be NOT KEYS rather than quietly still working.
RETIRED_ASSEMBLY_LEAVES = ("copies", "backup")


@pytest.mark.parametrize("group, declared", [
    ("runtime", DECLARED_META_RUNTIME_LEAVES),
    ("assembly", DECLARED_META_ASSEMBLY_LEAVES),
])
def test_a_flat_meta_declaration_matches_the_manifest(group, declared):
    """The two declaration sites are ONE question asked twice — pin them EQUAL.

    ``settings_keyspace`` decides whether a spelling is a key; the manifest is the
    ratified registry the spec projects onto. A leaf added to one and not the other
    is the exact drift these families had no guard against.

    ⚑ Parametrising over the PAIR is what makes the 2026-08-09 move safe:
    ``meta.assembly`` arrived as a new family, and a drift guard that covered only
    ``meta.runtime`` would have said nothing about it. The ``meta.box`` sibling
    keeps its own case below because it carries an extra claim about its
    unproduced leaves, not because the helper cannot reach it.
    """
    assert _manifest_leaves(f"meta.{group}.") == set(declared)


def test_the_collapse_outputs_are_declared_under_assembly_only():
    """The 2026-08-09 MOVE, on the manifest side: under ``assembly``, gone from
    ``runtime``."""
    assert set(COLLAPSE_LEAVES) <= _manifest_leaves("meta.assembly.")
    assert set(COLLAPSE_LEAVES) & _manifest_leaves("meta.runtime.") == set()


@pytest.mark.parametrize("leaf", COLLAPSE_LEAVES)
def test_the_runtime_spelling_of_a_collapse_output_is_refused_by_name(leaf):
    """The MOVE stated as a NEGATIVE: ``meta.runtime.bindings`` is not a key.

    Without this the change is indistinguishable from an ADD — declaring the three
    under ``meta.assembly`` while leaving them in
    ``DECLARED_META_RUNTIME_LEAVES`` leaves every positive case green and both
    spellings working, which is the clean break silently not happening. ⚑ Refused
    BY NAME (spec §0), so the assertion is on the reason text carrying the key.
    """
    key = f"meta.runtime.{leaf}"
    assert key in reason(key)


@pytest.mark.parametrize("leaf", RETIRED_ASSEMBLY_LEAVES)
def test_a_retired_assembly_spelling_is_refused_by_name(leaf):
    """The 2026-08-10b SPLIT stated as a negative, exactly as the 2026-08-09 MOVE is.

    Without this the rename is indistinguishable from an ADD: declaring ``seeded``
    and ``synced`` while leaving ``copies`` and ``backup`` in
    ``DECLARED_META_ASSEMBLY_LEAVES`` leaves every positive case green and both
    spellings working, which is the clean break silently not happening.
    """
    key = f"meta.assembly.{leaf}"
    assert not valid(key)
    assert key in reason(key)


@pytest.mark.parametrize("leaf", RETIRED_ASSEMBLY_LEAVES)
def test_a_retired_assembly_spelling_is_gone_from_the_manifest_too(leaf):
    """Both declaration sites drop it, or the drift guard above is the only witness."""
    assert leaf not in _manifest_leaves("meta.assembly.")


@pytest.mark.parametrize("leaf", COLLAPSE_LEAVES)
def test_a_collapse_output_is_indistinguishable_from_a_produced_sibling(leaf):
    """UNIFORMITY, stated as sibling-equality rather than against a literal.

    The honest read behaviour is that NO ``meta.*`` key has a CLI read surface at
    all: ``is_known_key`` gates on the SETTABLE set and ``meta.*`` is ``set: never``,
    so ``system get meta.runtime.ws_root`` — a key that IS produced — answers
    "unknown config key" exactly as an unproduced one does. Having no producer
    therefore costs these four nothing, and this case says so in the form that
    survives the gate being fixed: whatever the produced sibling answers, they
    answer. It goes RED the moment one of them is special-cased in either
    direction — a fabricated placeholder value on the read side, or a bespoke
    refusal naming them.
    """
    from kanibako.settings.config_keys import is_known_key

    assert valid(f"meta.assembly.{leaf}"), reason(f"meta.assembly.{leaf}")
    assert is_known_key(f"meta.assembly.{leaf}") == is_known_key(
        "meta.runtime.ws_root"
    )


# ---------------------------------------------------------------------------
# The UNPRODUCED meta.box leaves — spec §2c
# ---------------------------------------------------------------------------

#: The ``meta.box`` leaves the manifest and the spec declare while NOTHING writes
#: them: ``container_name`` renders in ``utils.container_name_for`` off ``proj``
#: attrs rather than the store, and ``helper_num`` travels as a structured field in
#: helper messages.
#: ⚑ ``home`` WAS HERE AND IS NOT ANY MORE. It IS produced —
#: ``settings_launch.workset_anchor_floor`` writes it beside the ``@meta.box.path``
#: it derives from (A9, ``b6ad780``) — and since cutover 6-H it is the only route to
#: the box home there is: the ``core-defaults`` row that used to bind home is gone,
#: and the assembly seam reads this key. Declared-but-unproduced is what this tuple
#: is for, and home no longer qualifies.
UNPRODUCED_BOX_LEAVES = ("container_name", "helper_num")


def test_the_meta_box_declaration_matches_the_manifest():
    """The ``meta.box`` half of the same drift guard — RED before 2026-08-08g.

    ⚑ DIRECT leaves only: ``meta.box`` also carries ``auth.workset_path`` and the
    ``agent.*`` mirror, which ``key_validity`` dispatches on separate arms and which
    ``DECLARED_META_BOX_LEAVES`` deliberately does not hold.
    """
    from kanibako.settings.settings_keyspace import DECLARED_META_BOX_LEAVES

    manifest = _manifest_leaves("meta.box.")
    assert manifest == set(DECLARED_META_BOX_LEAVES)
    assert set(UNPRODUCED_BOX_LEAVES) <= manifest


@pytest.mark.parametrize("leaf", UNPRODUCED_BOX_LEAVES)
def test_an_unproduced_box_leaf_is_indistinguishable_from_a_produced_sibling(leaf):
    """Sibling-equality again, against ``meta.box.path`` — which IS produced.

    ``meta.box.path`` is written by ``settings_launch.workset_anchor_floor``; these
    are written by nothing. That difference must not reach the read surface,
    and today it cannot: ``is_known_key`` gates on the SETTABLE set and every
    ``meta.*`` key is ``set: never``, so ``system get`` answers "unknown config key"
    for the produced anchor and for these alike (verified at the CLI). The case goes
    RED if one is ever special-cased in either direction.
    """
    from kanibako.settings.config_keys import is_known_key

    assert valid(f"meta.box.{leaf}"), reason(f"meta.box.{leaf}")
    assert is_known_key(f"meta.box.{leaf}") == is_known_key("meta.box.path")


def test_the_cut_meta_derived_family_is_refused():
    """R-8 (option 2) — the ``meta.derived.*`` key family is CUT: it is not a
    key, and the refusal is the ORDINARY unknown-meta-group refusal."""
    assert "not a declared meta group" in reason("meta.derived.x")
    assert "not a declared meta group" in reason(
        "meta.derived.agent.claude.common"
    )


def test_the_reserved_binding_derivations_node_is_not_a_key():
    """R-8 / D-4 — ``binding_derivations`` is the reserved INTERNAL snapshot
    node (manifest ``not_keys.reserved_internal``): refused by the closed head
    dispatch by construction, so it can never be re-claimed as a key."""
    assert "not a declared namespace" in reason("binding_derivations.x")
    assert "not a declared namespace" in reason(
        "binding_derivations.agent.claude.common"
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
        "run_args", "transform", "transform_settings", "endpoint", "template",
        "canon",
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
    """The enum + its default read as ONE vocabulary beside the leaf (R-41).

    ⚑ The DEFAULT is no longer a constant here: D1-2 moved the VALUE into
    ``core-defaults.yaml``'s ``agent_default:`` section, so this module exposes an
    ACCESSOR (``access_default``) rather than ``ACCESS_DEFAULT``. The enum stays a
    constant — it is the closed vocabulary, not a default. What this pins is that
    the settable surface, the launch resolver and the plugin descriptors still
    reach ONE list and ONE default read.
    """
    from kanibako.settings import settings_keyspace
    from kanibako.settings.settings_keyspace import ACCESS_TIERS, access_default

    assert ACCESS_TIERS == ("restricted", "editing", "full")
    assert access_default() == "full"
    assert access_default() in ACCESS_TIERS
    assert not hasattr(settings_keyspace, "ACCESS_DEFAULT"), (
        "the constant is retired — a restored one would be a SECOND spelling of a "
        "value that now lives in core-defaults.yaml"
    )


def test_the_keyspace_floor_is_the_store_set_not_a_copy():
    """SHOULD-6 — ONE collision-safety floor, read here under a local name.

    ⚑ Equality is NOT the property: an alias makes ``==`` unfailable, and a
    reintroduced literal copy would satisfy it on the day it is written. What can
    still fail is (1) IDENTITY — a copy, however faithful, breaks it — and (2)
    that the validator actually consults that object, walked over the WHOLE set
    rather than the spot names of ``test_reserved_leaf_names_rejected``.
    """
    from kanibako.settings.keystore import KeyStore

    assert RESERVED_LEAF_NAMES is KeyStore.RESERVED_KEY_NAMES
    for name in KeyStore.RESERVED_KEY_NAMES:
        r = reason(f"box.env.{name}")
        assert "RESERVED" in r, name
        # (3) and the reason must be TRUE of every name it is walked over. The set is
        # no longer DICT method names alone — ``RESERVED_KEY_NAMES`` is a class
        # attribute and ``insert_segments`` is a KeyStore method, not a dict one — so
        # calling the offender a dict method is false for two members of this loop.
        assert "dict method" not in r, name


# ---------------------------------------------------------------------------
# THREE CLASSES: KEY vs declared NAMESPACE vs UNDECLARED (spec §0)
# ---------------------------------------------------------------------------
#
# A path fails to be a key for two OPPOSITE reasons, and the store-path classifier
# is the one consumer that must tell them apart. Before ``key_class`` existed it
# could not, so it substituted DEPTH — rescuing a namespace at depth 1 and reporting
# ``meta.box.agent`` / ``agent.<agent>`` as fabrications whenever their last child
# was legitimately dropped.


def _probe_store(tree: dict) -> "object":
    """A :class:`KeyStore` shaped like *tree* — nested dicts become nested nodes."""
    from kanibako.settings.keystore import KeyStore

    store = KeyStore()
    for key, value in tree.items():
        store[key] = _probe_store(value) if isinstance(value, dict) else value
    return store


def _findings(tree: dict) -> set[str]:
    """The dotted paths ``undeclared_store_paths`` reports for *tree*."""
    from kanibako.settings.settings_keyspace import (
        key_class,
        render_store_path,
        undeclared_store_paths,
    )

    def oracle(path: str):
        return key_class(path, valid_agents=AGENTS)

    return {
        render_store_path(segments, judgement.key_len)
        for segments, judgement in undeclared_store_paths(
            _probe_store(tree), oracle=oracle,
        )
    }


def _manifest_prefix_cases() -> list[tuple[str, str]]:
    """Every ``(declared key, proper prefix)`` pair the ratified manifest implies.

    ⚑ DERIVED FROM THE RULE, NEVER LISTED (P13). The rule is "a declared key hangs
    under declared interiors", so the corpus is generated from the manifest's own key
    rows: a shape change moves the corpus with it, where a hand-kept list of
    namespace names would silently go stale the next time one moved.
    ``<agent>`` is substituted; a row whose LEAF is a placeholder still contributes
    its real prefixes.
    """
    cases: list[tuple[str, str]] = []
    for row in manifest_doc()["keys"]:
        key = str(row).replace("<agent>", "claude")
        segments = key.split(".")
        for cut in range(1, len(segments)):
            prefix = ".".join(segments[:cut])
            if "<" in prefix:
                continue
            cases.append((key, prefix))
    return cases


@pytest.mark.parametrize("key,prefix", _manifest_prefix_cases())
def test_every_prefix_of_a_declared_key_is_a_NAMESPACE_or_a_KEY(key, prefix):
    """No declared key hangs under something the keyspace calls a fabrication.

    ⚑ THE ANTI-REGRESSION FOR THE DEPTH PROXY. Under the old ``len(segments) == 1``
    rule this held for ``box`` and failed for ``box.auth``, ``agent.claude`` and
    ``meta.box.agent`` — every interior below the first segment.

    INVERT: make any interior branch return ``UNDECLARED`` again and the prefixes
    beneath it red, naming the key that would have been refused.
    """
    from kanibako.settings.settings_keyspace import KeyClass, key_class

    judged = key_class(prefix, valid_agents=AGENTS)
    assert judged.cls in (KeyClass.NAMESPACE, KeyClass.KEY), (
        f"{prefix!r} is a proper prefix of the declared key {key!r}, so it cannot be "
        f"UNDECLARED — {judged.reason}"
    )


def test_the_prefix_corpus_is_not_vacuous():
    """⚑ The pin above must red on its own EMPTINESS (P15).

    A generator that silently produced nothing would make every run green while
    checking no prefix at all. Assert it reaches past the first segment, which is the
    depth the old rule could not see.
    """
    cases = _manifest_prefix_cases()
    assert len(cases) > 100, len(cases)
    assert any(prefix.count(".") >= 2 for _, prefix in cases), "no deep prefix"


@pytest.mark.parametrize("path", [
    "box", "system", "workset", "agent", "meta", "config", "pref",
    "meta.box", "meta.agent", "meta.runtime", "meta.assembly", "meta.workset",
    "meta.agent.claude", "meta.box.agent",
    "agent.claude", "agent.default",
    "box.bindings", "box.env", "box.secret_path",
    "system.channels", "workset.channels",
    "pref.box", "pref.agent.claude",
    # The mirror spans TWO declared sources, so BOTH ends of it are interiors:
    # the ``meta.agent`` tier that owns the capability, and the box-side mirror of
    # it ([R141] as amended; spec :1081).
    "box.auth", "system.auth", "workset.auth",
    "meta.box.auth", "meta.agent.claude.auth", "meta.box.agent.auth",
])
def test_a_declared_interior_is_a_NAMESPACE(path):
    """Each is a grouping declared keys hang under — spec :42 calls ``meta.*`` a
    *"TOP-LEVEL, protected (read-only) namespace, grouped as meta.<scope>.*"*, and
    the same is true one tier down.
    """
    from kanibako.settings.settings_keyspace import KeyClass, key_class

    assert key_class(path, valid_agents=AGENTS).cls is KeyClass.NAMESPACE


@pytest.mark.parametrize("path", [
    "box.zippity", "box.zippity.deeper", "agent.claude.zippity",
    "meta.box.zippity", "meta.zippity", "zippity",
])
def test_a_fabrication_is_UNDECLARED_not_a_NAMESPACE(path):
    """The half that must NOT widen: a namespace class is for what the SPEC declares,
    never for anything that merely happens to be a node."""
    from kanibako.settings.settings_keyspace import KeyClass, key_class

    assert key_class(path, valid_agents=AGENTS).cls is KeyClass.UNDECLARED


def test_key_validity_still_refuses_every_non_key():
    """⚑ THE SET BOUNDARIES SEE NO CHANGE. At a write site a NAMESPACE and a
    fabrication are equally not-a-key, so both must still come back as a REASON —
    ``config set box.auth=…`` may not start succeeding because ``box.auth`` gained a
    class."""
    from kanibako.settings.settings_keyspace import KeyClass, key_class

    for path in ("box", "meta.box.agent", "agent.claude", "box.env", "box.zippity"):
        assert key_class(path, valid_agents=AGENTS).cls is not KeyClass.KEY
        assert key_validity(path, valid_agents=AGENTS) is not None, path


# ---------------------------------------------------------------------------
# The store classifier: a NODE at a namespace is structure, a SCALAR is not
# ---------------------------------------------------------------------------

def test_an_empty_declared_interior_is_not_a_finding():
    """⚑ THE MEASURED REGRESSION. Both paths reached a real launch snapshot EMPTY —
    ``meta.box.agent`` when §6b dropped its only child (a dangling ``@``-ref
    referent), ``agent.claude`` when a ``--null`` pref suppressed its only entry —
    and the classifier reported each as an undeclared key.

    INVERT: restore the depth rule and both red, which is the state that would have
    refused a legal box the day resolve enforcement was armed.
    """
    assert _findings({"meta": {"box": {"agent": {}}}}) == set()
    assert _findings({"agent": {"claude": {}}}) == set()
    assert _findings({"box": {}, "meta": {}, "agent": {}, "config": {}}) == set()


@pytest.mark.writes_undeclared(
    "box",
    reason="the SCALAR at a scope root is the subject: this is the one case that "
           "proves undeclared_store_paths still REPORTS, so it has to make the "
           "write the keyspace refuses.",
)
def test_undeclared_store_paths_REPORTS_a_scalar_at_a_namespace():
    """⚑⚑ THE LIVENESS PIN FOR :func:`undeclared_store_paths` ITSELF.

    Every other driver of it asserts ``== set()``, so the whole harness passes
    vacuously if the function stops reporting: narrow its filter from
    ``FINDING_VERDICTS`` back to ``{Verdict.UNDECLARED}`` and scalar-at-a-namespace
    detection vanishes while every one of those assertions stays GREEN. That line is
    on the PRODUCTION path — ``settings_keyspace_probe.observe`` is the instrument
    the resolve-seam blast radius gets measured with, so a silent narrowing there
    would hand the arm decision a number nobody measured.

    ⚑ Pinning ``Verdict.NAMESPACE in FINDING_VERDICTS`` is NOT this: that pins the
    CONSTANT, and the defect is the function no longer consulting it.

    MUTATION: narrow the filter at ``undeclared_store_paths`` and this reddens,
    ``{'box'} != set()``.
    """
    assert _findings({"box": "a scalar"}) == {"box"}


def _rescued(*rows: tuple[tuple[str, ...], bool]) -> set[tuple[str, ...]]:
    """The paths ``container_notes`` rescues, given ``(segments, is_node)`` rows.

    ⚑ Judged and rescued WITHOUT building a KeyStore, deliberately. A negative case
    has to name a fabrication, and writing one into a real store would trip the
    pytest write census — whose ``writes_undeclared`` marker is a SESSION-global that
    another file pins by exact equality. The classifier is pure; drive it directly.
    """
    from kanibako.settings.settings_keyspace import (
        StoreNode,
        classify_store_path,
        container_notes,
        key_class,
    )

    def oracle(path: str):
        return key_class(path, valid_agents=AGENTS)

    return set(container_notes({
        segments: StoreNode(
            classify_store_path(segments, oracle=oracle).verdict, is_node,
        )
        for segments, is_node in rows
    }))


def _verdict(*segments: str) -> str:
    from kanibako.settings.settings_keyspace import classify_store_path, key_class

    return classify_store_path(
        segments, oracle=lambda p: key_class(p, valid_agents=AGENTS),
    ).verdict


def test_a_SCALAR_at_a_declared_interior_is_still_a_finding():
    """The ``is_node`` guard, at every depth — this is what keeps the fix a
    CORRECTNESS change rather than a widening.

    A namespace is a NODE. A value sitting on one is an undeclared SHAPE, so the
    rescue must decline it whether it sits at depth 1 (where the old rule could see
    it) or deeper (where it could not) — and it must still FAIL, which is why the
    surviving ``NAMESPACE`` verdict is a member of ``FINDING_VERDICTS``.
    """
    from kanibako.settings.settings_keyspace import FINDING_VERDICTS, Verdict

    for path in (("box",), ("meta", "box", "agent"), ("agent", "claude")):
        assert _verdict(*path) == Verdict.NAMESPACE, path
        assert _rescued((path, True)) == {path}, f"a NODE at {path} is structure"
        assert _rescued((path, False)) == set(), f"a SCALAR at {path} is not"
        assert Verdict.NAMESPACE in FINDING_VERDICTS


def test_a_fabricated_subtree_is_still_a_finding_empty_or_not():
    """Nothing declared beneath it and no declaration of its own ⇒ not scaffolding."""
    from kanibako.settings.settings_keyspace import Verdict

    assert _verdict("box", "zippity") == Verdict.UNDECLARED
    # ⚑ Not rescued EITHER WAY, and the second case says why the carriers rule cannot
    # launder a fabrication: nothing under a fabricated node classifies DECLARED, so
    # it never becomes a carrier however much is written beneath it.
    assert _rescued((("box", "zippity"), True)) == set()
    assert _verdict("box", "zippity", "image") == Verdict.UNDECLARED
    assert _rescued(
        (("box", "zippity"), True), (("box", "zippity", "image"), False),
    ) == set()


def test_a_populated_interior_carries_its_children_clean():
    """The positive control: a real snapshot shape reports nothing."""
    assert _findings({
        "box": {"image": "x"},
        "meta": {"box": {"path": "/p", "agent": {"model": "m"}}},
    }) == set()


# ---------------------------------------------------------------------------
# The agent tier's leaf VOCABULARY, where the plugin is not here to declare it
# ---------------------------------------------------------------------------
#
# ⚑ THE DISCRIMINATOR AND ITS LEAVES ARE A DEPENDENT PAIR. §0 makes agent specifics
# PLUGIN-declared, so an agent whose plugin this machine cannot read has no leaf
# vocabulary to be judged against — and judging one anyway refuses a key that IS
# declared. The measured case: goose declares ``provider`` through
# ``setting_descriptors()``, and ``agent.goose.provider`` was reported "not a
# declared agent key" on a machine with only claude installed.

_KNOWN = frozenset({"claude", "default"})


def _class(key: str, **kwargs):
    from kanibako.settings.settings_keyspace import key_class

    return key_class(key, valid_agents=AGENTS, **kwargs)


def test_an_unverifiable_agents_leaf_is_conceded_not_refused():
    """The defect: a DECLARED plugin key refused for want of the plugin.

    ``agent_leaves`` here holds only what a claude-only install could read, which is
    exactly the position a shared config lands in.
    """
    from kanibako.settings.settings_keyspace import KeyClass

    judged = _class(
        "agent.goose.provider",
        agent_leaves=frozenset(),
        agents_with_known_leaves=_KNOWN,
    )
    assert judged.cls is KeyClass.KEY, judged.reason


def test_a_verifiable_agents_undeclared_leaf_still_refuses():
    """The other direction, and the one that keeps this a concession rather than a
    hole: where the vocabulary IS known, §0 is enforced against it exactly as before.
    """
    from kanibako.settings.settings_keyspace import KeyClass

    judged = _class(
        "agent.claude.zippity",
        agent_leaves=frozenset(),
        agents_with_known_leaves=_KNOWN,
    )
    assert judged.cls is KeyClass.UNDECLARED
    assert "not a declared agent key" in judged.reason


def test_the_all_agents_tier_is_never_conceded():
    """``agent.default`` is CORE's tier, not a plugin's.

    Conceding it would unarm §0 across the whole ``agent.default.*`` subtree — which
    is where the behavior floor lands — for every install that lacks any one plugin.
    """
    from kanibako.settings.settings_keyspace import KeyClass

    assert _class(
        "agent.default.zippity",
        agent_leaves=frozenset(),
        agents_with_known_leaves=frozenset(),
    ).cls is KeyClass.UNDECLARED


def test_conceding_a_vocabulary_concedes_nothing_else():
    """SHAPE and the reserved-name floor survive the concession.

    The vocabulary is the plugin's; the tail SHAPE (§2d: one leaf, or a §2a category)
    and the store-collision floor (§0 reserved names) are core's, and neither becomes
    unknowable because a plugin is absent.
    """
    from kanibako.settings.settings_keyspace import KeyClass

    unknown = dict(agent_leaves=frozenset(), agents_with_known_leaves=frozenset())
    # A multi-segment tail is not an agent key whoever declares the leaves.
    assert _class("agent.goose.a.b", **unknown).cls is KeyClass.UNDECLARED
    # A category token still routes to the category rules.
    assert _class("agent.goose.bindings", **unknown).cls is KeyClass.NAMESPACE
    # A reserved leaf name would shadow a real attribute on the resolved store.
    assert _class("agent.goose.items", **unknown).cls is KeyClass.UNDECLARED
    # And the tier itself is still a tier.
    assert _class("agent.goose", **unknown).cls is KeyClass.NAMESPACE


def _category_tokens() -> "frozenset[str]":
    """The §2a category tokens, READ FROM THE DECLARATIONS (P13).

    ⚑ NOT from ``_is_category_token``, which is the property under test — deriving a
    corpus from it would empty the corpus of exactly the token that broke (P15).
    Three independent declarations cover seven of the nine families; ``env`` is the
    one with no declaring constant to read, so it is named HERE rather than taken
    from the code this pins.
    """
    from kanibako.settings.settings_categories import CONCRETE_CATEGORIES
    from kanibako.settings.settings_keyspace import (
        BIND_CATEGORIES,
        TERMINAL_CATEGORY_TAILS,
    )

    declared = (
        {c.split(".")[0] for c in BIND_CATEGORIES}
        | {tail[0] for tail in TERMINAL_CATEGORY_TAILS}
        | {c.split(".")[0] for c in CONCRETE_CATEGORIES}
    )
    return frozenset(declared | {"env"})


def test_a_category_token_under_agent_is_never_conceded(monkeypatch):
    """🛑 THE CONCESSION'S TEST IS "COULD THIS BE AN AGENT", NOT "IS IT INSTALLED".

    ``agent.common.plugins`` is the UNDISCRIMINATED relic MIGRATION.md §2.11 tells
    users to grep their stores for. Under the install-state test it classified KEY —
    because nothing declares an agent named ``common`` — so the concession un-armed
    §0 over the exact shape the migration says stops a resolve. The category tokens
    are a KNOWN FINITE SET; there is nothing unknowable about them to concede.

    ⚑ Through the RESOLVE seam's oracle, where the agent NAME is conceded too. With
    a narrow ``valid_agents`` every case here would refuse on the name instead and
    the pin would pass without touching the concession at all.
    """
    from kanibako.settings import settings_keyspace_probe as probe
    from kanibako.settings.settings_keyspace import KeyClass

    monkeypatch.setattr(
        probe, "_PLUGINS", probe._Plugins(frozenset(), frozenset({"claude"})),
    )
    tokens = _category_tokens()
    # ⚑ REDS ON ITS OWN EMPTINESS: a declaration rename that emptied the corpus
    # would otherwise pass this vacuously (P15).
    assert len(tokens) >= 8, sorted(tokens)
    for token in sorted(tokens):
        judged = probe.declared_keyspace_oracle(f"agent.{token}.zippity")
        assert judged.cls is KeyClass.UNDECLARED, f"agent.{token}.zippity: {judged}"


def test_an_uninstalled_agents_leaf_is_still_conceded(monkeypatch):
    """The OTHER direction, so the narrowing above cannot be over-read.

    An agent this machine has never heard of is UNENUMERABLE — the concession's
    whole reason — so a real harness's declared leaf still resolves where its plugin
    is absent. ⚑ THE RESIDUAL RIDES ON THIS AND IS IRREDUCIBLE: at the RESOLVE seam
    the agent NAME is conceded too (``ANY_AGENT``), and a plausible-looking typo is
    indistinguishable there from an uninstalled harness — so ``agent.clade.zippity``
    resolves as well. Stated in CHANGELOG.md rather than hidden.

    ⚑ Through the SEAM's own oracle, because that is where both concessions meet;
    ``key_class`` with a narrow ``valid_agents`` would refuse ``clade`` on its NAME
    and never reach the leaf question this is about.
    """
    from kanibako.settings import settings_keyspace_probe as probe
    from kanibako.settings.settings_keyspace import KeyClass

    monkeypatch.setattr(
        probe, "_PLUGINS", probe._Plugins(frozenset(), frozenset({"claude"})),
    )
    assert probe.declared_keyspace_oracle(
        "agent.goose.provider"
    ).cls is KeyClass.KEY
    assert probe.declared_keyspace_oracle(
        "agent.clade.zippity"
    ).cls is KeyClass.KEY
    # …and the narrowing still holds under the SAME permissive name oracle.
    assert probe.declared_keyspace_oracle(
        "agent.common.plugins"
    ).cls is KeyClass.UNDECLARED


def test_the_meta_agent_tier_is_not_conceded():
    """🛑 ``meta.agent.<agent>.*`` is core-declared, so there is nothing to concede.

    No plugin extends that tier's leaves, so its vocabulary is knowable whether or
    not the plugin is installed. Extending the concession to it would give away a
    subtree for a reason that does not apply to it.
    """
    from kanibako.settings.settings_keyspace import KeyClass

    assert _class(
        "meta.agent.goose.zippity",
        agent_leaves=frozenset(),
        agents_with_known_leaves=frozenset(),
    ).cls is KeyClass.UNDECLARED


def test_the_resolve_oracle_answers_known_by_HARNESS(monkeypatch):
    """The seam's supplier: a PERSONA is known iff its harness is.

    A persona takes its leaves from the harness right of the separator, so asking
    about the node name would concede every persona on the machine.
    """
    from kanibako.settings import settings_keyspace_probe as probe

    monkeypatch.setattr(
        probe, "_PLUGINS", probe._Plugins(frozenset(), frozenset({"claude"})),
    )
    known = probe.KNOWN_LEAF_AGENTS
    assert "claude" in known
    assert "navigator℘claude" in known
    assert "goose" not in known
    assert "navigator℘goose" not in known
    # ⚑ ``default`` is deliberately NOT a member: ``key_class`` never asks about it,
    # so the all-agents tier's standing stays the keyspace's rather than a
    # supplier's, and every supplier gets it right by not having to know it.
    assert "default" not in known

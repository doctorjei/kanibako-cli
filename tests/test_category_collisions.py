"""The spec §0 ``box_dest`` COLLISION TABLE and the derived-binding materialisation.

One test per table row, plus the behaviours the table does not mention and which
must survive it byte-for-byte (the pure-``seeded`` overlay, the credential gate
ordering, the ``synced``↔``binding`` error, the ``secret_path`` per-VAR cascade).

⚑ EVERY case here is MUTATION-PROVEN: each one reddens when its own branch in
``settings_categories`` is inverted, and stays green when the others are. A test
that passes because the rule is incidentally satisfied proves nothing, and the
five-row table is exactly the shape that invites incidental passes.

The table (spec §0, 2026-07-29) replaced the flat authority ladder
``seed < cache < binding < common < synced < masks``:

===  =============================================  =========================
row  case                                            outcome
===  =============================================  =========================
1    two CONCRETE declarations at one dest           ERROR, always
2    ``masks`` at a dest a binding occupies          OVERRIDE
3    an ABSTRACTION extending onto an occupied dest  ERROR — refuse the
                                                     EXTENSION
4    abstraction vs abstraction, DIFFERENT scopes    scope precedence, SILENT
5    abstraction vs abstraction, SAME scope          existing order + WARN
===  =============================================  =========================
"""

from __future__ import annotations

import logging

import pytest

from kanibako.errors import CategoryCollisionError, ConfigError
from kanibako.settings_categories import (
    SECRET_MOUNT_DIR,
    CategoryEntry,
    _bind_options,
    _DELIVERY,
    derive_binding_keys,
    reconcile_categories,
)

DEST = "/g/x"


def entry(
    category: str,
    *,
    name: str,
    scope: str = "box",
    box_dest: str = DEST,
    host_src: str = "/h",
    is_credential: bool = False,
) -> CategoryEntry:
    """One CategoryEntry with a DISCRIMINATED declaration key.

    The key is what the table's outcomes and messages are stated in terms of, so
    two entries at one dest must never share one — hence *name* is required.
    """
    delivery = _DELIVERY[category]
    scope_token = "agent" if scope.startswith("agent") else scope
    return CategoryEntry(
        category=category,
        scope=scope_token,
        box_dest=box_dest,
        host_src=None if category in ("masks", "env") else host_src,
        delivery=delivery,
        options=_bind_options(category) if delivery == "MOUNT" else "",
        name=name,
        key=f"{scope}.{category}.{name}",
        is_credential=is_credential,
    )


def categories(rec) -> list[str]:
    return [e.category for e in (*rec.mounts, *rec.copies)]


# --------------------------------------------------------------------------- #
# T1 — the SHIPPED default set fires nothing (the bridge to the real world)     #
# --------------------------------------------------------------------------- #


class TestShippedDefaultsAreQuiet:
    """T1 — the synthetic rules below must not fire on a real install.

    Every other test here drives ``reconcile_categories`` with CONSTRUCTED
    entries, which proves the rules are enforced but says nothing about whether
    they fire in practice. This one resolves the REAL shipped defaults through
    the REAL pipeline (see ``tests/test_categories_live.py`` for the per-mode
    probe it borrows) and asserts zero errors and zero warnings — which is also
    the M-7 real-world exposure check.
    """

    def test_every_mode_and_agent_shape_resolves_clean(self, tmp_path):
        from tests.test_categories_live import _probe_cases, _probe_snapshot

        from kanibako.settings_launch import snapshot_category_entries

        for mode, proj, ws_root, hl in _probe_cases(tmp_path):
            snap, ctx = _probe_snapshot(mode, proj, ws_root, hl)
            for agent in ("claude", "no_agent"):
                rec = reconcile_categories(
                    snapshot_category_entries(snap, active_agent=agent, box_ctx=ctx)
                )
                assert rec.warnings == (), (mode, agent, rec.warnings)
                assert rec.mounts, (mode, agent)

    def test_every_shipped_dest_is_occupied_at_most_once(self, tmp_path):
        """The property row 1 turns into an error — asserted on real data.

        If a shipped default set ever grows a duplicate destination, this fails
        HERE (naming the dest) rather than as a launch crash in the field.
        """
        from tests.test_categories_live import _probe_cases, _probe_snapshot

        from kanibako.settings_launch import snapshot_category_entries

        for mode, proj, ws_root, hl in _probe_cases(tmp_path):
            snap, ctx = _probe_snapshot(mode, proj, ws_root, hl)
            entries = snapshot_category_entries(
                snap, active_agent="claude", box_ctx=ctx,
            )
            dests = [e.box_dest for e in entries if e.delivery == "MOUNT"]
            assert len(dests) == len(set(dests)), (mode, sorted(dests))


# --------------------------------------------------------------------------- #
# Row 1 — two CONCRETE declarations at one dest                                #
# --------------------------------------------------------------------------- #


class TestRow1BindingVsBinding:
    """T2 — ERROR, always, any scope, any mode."""

    def test_ro_and_rw_at_one_dest_same_scope_raises(self):
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.ro", name="vault", host_src="/srv/shared"),
                entry("bindings.rw", name="mine", host_src="/home/jei/vault"),
            ])
        assert exc.value.kind == "binding_vs_binding"
        assert exc.value.box_dest == DEST

    def test_cross_scope_raises_instead_of_letting_the_box_win(self):
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.ro", name="vault", scope="system",
                      host_src="/srv/vaults/shared"),
                entry("bindings.rw", name="vault", scope="box",
                      host_src="/home/jei/vault"),
            ])
        assert exc.value.kind == "binding_vs_binding"

    def test_message_names_the_dest_both_keys_and_the_remedy(self):
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.ro", name="vault", scope="system",
                      host_src="/srv/vaults/shared"),
                entry("bindings.rw", name="vault", scope="box",
                      host_src="/home/jei/vault"),
            ])
        text = str(exc.value)
        assert DEST in text
        assert "system.bindings.ro.vault" in text
        assert "box.bindings.rw.vault" in text
        assert "/srv/vaults/shared" in text
        # The remedy is the non-obvious part (§0), and it is the YAML edit rather
        # than a CLI verb, because no suppression verb exists.
        assert "SUPPRESS" in text
        assert "vault: null" in text
        assert exc.value.entries == (
            ("system.bindings.ro.vault", "/srv/vaults/shared"),
            ("box.bindings.rw.vault", "/home/jei/vault"),
        )

    def test_a_lone_binding_is_not_a_collision(self):
        rec = reconcile_categories([entry("bindings.rw", name="home")])
        assert categories(rec) == ["bindings.rw"]


class TestRow1SecretPathCarveOut:
    """T10 — the per-VAR cascade §2a documents as a FEATURE survives row 1.

    Two ``secret_path`` entries at one dest are always the SAME VAR arriving
    from two scopes (the dest is ``SECRET_MOUNT_DIR/{VAR}`` by construction), so
    they are a cascade override, not two names contending for one destination.
    """

    def test_box_overrides_workset_for_one_var_without_erroring(self):
        dest = f"{SECRET_MOUNT_DIR}/TOK"
        rec = reconcile_categories([
            entry("secret_path", name="TOK", scope="agent.claude",
                  box_dest=dest, host_src="/agent/tok"),
            entry("secret_path", name="TOK", scope="box",
                  box_dest=dest, host_src="/box/tok"),
        ])
        assert [m.host_src for m in rec.mounts] == ["/box/tok"]
        assert rec.warnings == ()

    def test_a_binding_into_the_secrets_dir_still_collides(self):
        """The carve-out is per-VAR, not a blanket exemption."""
        dest = f"{SECRET_MOUNT_DIR}/TOK"
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("secret_path", name="TOK", box_dest=dest),
                entry("bindings.rw", name="sneaky", box_dest=dest),
            ])
        assert exc.value.kind == "binding_vs_binding"


# --------------------------------------------------------------------------- #
# Row 2 — masks OVERRIDE                                                       #
# --------------------------------------------------------------------------- #


class TestRow2MaskOverrides:
    """T3 — a mask at a bound dest wins, with no error and no warning.

    ⚑ Every case here puts the mask at the LEAST specific scope and FIRST in the
    input, so neither scope precedence nor input order can hand it the win. A
    mask at box scope listed last would pass with the override rule DELETED —
    the mutation matrix caught exactly that, and this is the repair.
    """

    def test_mask_over_a_binding_wins_silently(self):
        rec = reconcile_categories([
            entry("masks", name=DEST, scope="system"),
            entry("bindings.rw", name="ws", scope="box"),
        ])
        assert categories(rec) == ["masks"]
        assert [m.scope for m in rec.mounts] == ["system"]
        assert rec.warnings == ()

    def test_mask_over_an_abstraction_wins_silently(self):
        rec = reconcile_categories([
            entry("masks", name=DEST, scope="system"),
            entry("common", name="plugins", scope="box"),
        ])
        assert categories(rec) == ["masks"]
        assert [m.scope for m in rec.mounts] == ["system"]
        assert rec.warnings == ()


# --------------------------------------------------------------------------- #
# Row 3 — an abstraction EXTENDING onto an occupied dest                       #
# --------------------------------------------------------------------------- #


class TestRow3ExtensionOntoOccupied:
    """T4/T5 — ERROR refusing the EXTENSION; the explicit binding is the BASE."""

    @pytest.mark.parametrize("abstract", ["common", "caches"])
    def test_abstraction_onto_a_binding_raises(self, abstract):
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.rw", name="claude_plugins", host_src="/base"),
                entry(abstract, name="plugins", scope="agent.claude",
                      host_src="/ext"),
            ])
        assert exc.value.kind == "extension_onto_occupied"

    @pytest.mark.parametrize("abstract", ["common", "caches"])
    def test_the_refused_side_is_the_EXTENSION_not_the_base(self, abstract):
        """T5's direction assertion — "I wrote it down literally" wins.

        A test that only asserted "something raised" would stay green if the
        rule refused the BASE instead, which is the opposite of §0.
        """
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.rw", name="claude_plugins", host_src="/base"),
                entry(abstract, name="plugins", scope="agent.claude",
                      host_src="/ext"),
            ])
        # entries[0] is the refused EXTENSION, entries[1] the surviving BASE.
        assert exc.value.entries[0][0] == f"agent.claude.{abstract}.plugins"
        assert exc.value.entries[1][0] == "box.bindings.rw.claude_plugins"
        text = str(exc.value)
        assert text.startswith(f"'agent.claude.{abstract}.plugins' extends onto")
        assert "already binds" in text
        assert "the derived\nextension is refused" in text

    def test_message_carries_the_rule_changed_paragraph_and_the_remedy(self):
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.rw", name="claude_plugins"),
                entry("common", name="plugins", scope="agent.claude"),
            ])
        text = str(exc.value)
        assert "THIS RULE CHANGED IN kanibako 1.8.0" in text
        assert "SUPPRESS" in text
        assert "claude_plugins: null" in text

    def test_an_abstraction_alone_at_a_dest_is_fine(self):
        rec = reconcile_categories([entry("common", name="plugins")])
        assert categories(rec) == ["common"]
        assert rec.warnings == ()


# --------------------------------------------------------------------------- #
# Rows 4/5 — abstraction vs abstraction                                        #
# --------------------------------------------------------------------------- #


class TestRow4CrossScopeIsSilent:
    """T6 — scope precedence decides and nothing is said."""

    def test_box_beats_system_across_categories_silently(self):
        rec = reconcile_categories([
            entry("common", name="a", scope="system", host_src="/sys"),
            entry("caches", name="b", scope="box", host_src="/box"),
        ])
        assert [m.host_src for m in rec.mounts] == ["/box"]
        assert rec.warnings == ()

    def test_scope_precedence_beats_input_order(self):
        """The caller's list order must not be able to override row 4.

        ``reconcile_categories`` takes an arbitrary list; only the live adapter
        happens to hand it apply-ordered.
        """
        rec = reconcile_categories([
            entry("caches", name="b", scope="box", host_src="/box"),
            entry("common", name="a", scope="system", host_src="/sys"),
        ])
        assert [m.host_src for m in rec.mounts] == ["/box"]
        assert rec.warnings == ()

    def test_agent_default_and_active_count_as_ONE_scope(self):
        """D5 — pinned EITHER WAY so the choice is visible and mutable.

        §2a lists ``agent.default`` and ``agent.<active>`` as separate scopes,
        which would make this a row-4 (silent) case. The code carries ONE bare
        ``agent`` precedence token, and two entries surviving the active-over-
        default pick at one dest are two different NAMES in one effective agent
        view — an ambiguity the user must resolve. So P5 treats the whole agent
        tier as ONE scope and WARNS, which is the LOUD direction. If Jei rules
        the other way, this assertion is the single place that flips.
        """
        rec = reconcile_categories([
            entry("common", name="a", scope="agent.default", host_src="/def"),
            entry("caches", name="b", scope="agent.claude", host_src="/act"),
        ])
        assert len(rec.warnings) == 1
        assert rec.warnings[0].scope == "agent"
        assert set(rec.warnings[0].loser_keys) == {"agent.default.common.a"}


class TestRow5SameScopeWarns:
    """T7 — proceed on the existing ordering, and say so."""

    def test_same_scope_pair_survives_with_exactly_one_warning(self):
        rec = reconcile_categories([
            entry("caches", name="build", scope="box", host_src="/cache"),
            entry("common", name="buildcache", scope="box", host_src="/common"),
        ])
        assert len(rec.mounts) == 1
        assert len(rec.warnings) == 1
        w = rec.warnings[0]
        assert w.box_dest == DEST
        assert w.scope == "box"
        assert w.winner_key == "box.common.buildcache"
        assert w.loser_keys == ("box.caches.build",)
        assert "repeats every launch" in w.message()
        assert "box.caches.build" in w.message()

    def test_same_category_same_scope_also_warns(self):
        rec = reconcile_categories([
            entry("common", name="a", scope="box"),
            entry("common", name="b", scope="box"),
        ])
        assert len(rec.warnings) == 1
        assert rec.warnings[0].loser_keys == ("box.common.a",)

    def test_a_lower_scopes_own_ambiguity_is_masked_by_row_4(self):
        """Row 4 makes the whole lower scope lose, so its internal order
        decided nothing and naming a "winner" there would be a lie."""
        rec = reconcile_categories([
            entry("common", name="a", scope="system"),
            entry("caches", name="b", scope="system"),
            entry("common", name="c", scope="box"),
        ])
        assert len(rec.mounts) == 1
        assert rec.warnings == ()

    def test_the_resolver_itself_has_no_memory(self):
        """T8 (resolver half) — purity: the records come back EVERY call.

        Suppression is the emitter's job; a resolver that remembered would make
        the "every launch" requirement unimplementable from the outside.
        """
        entries = [
            entry("caches", name="build", scope="box"),
            entry("common", name="buildcache", scope="box"),
        ]
        first = reconcile_categories(entries)
        second = reconcile_categories(entries)
        assert len(first.warnings) == 1
        assert first.warnings == second.warnings


class TestCollisionWarningEmission:
    """T8/T9 — the emitter: once per process, and it comes back after a reset."""

    @pytest.fixture(autouse=True)
    def _clean(self):
        from kanibako.commands.start import reset_collision_warnings

        reset_collision_warnings()
        yield
        reset_collision_warnings()

    def _collision(self):
        from kanibako.settings_categories import CategoryCollision

        return CategoryCollision(
            box_dest=DEST, scope="box", winner_key="box.common.b",
            loser_keys=("box.caches.a",),
        )

    def test_five_resolves_in_one_launch_log_one_line(self, caplog):
        """``_resolve_launch_snapshot`` runs up to five times per start."""
        from kanibako.commands.start import emit_collision_warnings

        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                emit_collision_warnings([self._collision()])
        lines = [r for r in caplog.records if DEST in r.getMessage()]
        assert len(lines) == 1

    def test_a_new_process_warns_again(self, caplog):
        """The memo is process-scoped; there is no marker file or registry flag,
        so the NEXT launch warns again — which is what §0 requires."""
        from kanibako.commands.start import (
            emit_collision_warnings,
            reset_collision_warnings,
        )

        with caplog.at_level(logging.WARNING):
            emit_collision_warnings([self._collision()])
            reset_collision_warnings()  # stands in for a fresh process
            emit_collision_warnings([self._collision()])
        lines = [r for r in caplog.records if DEST in r.getMessage()]
        assert len(lines) == 2

    def test_distinct_collisions_are_not_collapsed_into_each_other(self, caplog):
        from kanibako.commands.start import emit_collision_warnings
        from kanibako.settings_categories import CategoryCollision

        other = CategoryCollision(
            box_dest="/g/y", scope="box", winner_key="box.common.d",
            loser_keys=("box.caches.c",),
        )
        with caplog.at_level(logging.WARNING):
            emit_collision_warnings([self._collision(), other])
        assert len([r for r in caplog.records if "/g/" in r.getMessage()]) == 2


# --------------------------------------------------------------------------- #
# The behaviours the table does not mention and which must survive it          #
# --------------------------------------------------------------------------- #


class TestPreservedCopyAndCrossDeliveryRules:
    def test_pure_seeded_group_keeps_every_layer(self):
        """T11 — the template trio. Copies OVERLAY; they do not shadow."""
        rec = reconcile_categories([
            entry("seeded", name="template", scope="system", host_src="/base"),
            entry("seeded", name="template", scope="agent.claude", host_src="/ag"),
            entry("seeded", name="template", scope="workset", host_src="/ws"),
        ])
        assert [c.host_src for c in rec.copies] == ["/base", "/ag", "/ws"]
        assert rec.warnings == ()

    def test_synced_vs_binding_at_one_dest_is_still_a_config_error(self):
        with pytest.raises(ConfigError) as exc:
            reconcile_categories([
                entry("synced", name="creds"),
                entry("bindings.rw", name="home"),
            ])
        assert exc.value.kind == "synced_vs_binding"
        assert DEST in str(exc.value)

    def test_the_unchanged_rule_carries_NO_rule_changed_paragraph(self):
        """T13 — putting it on a rule that did not change trains readers to
        skip it, so its absence here is a requirement, not an oversight."""
        with pytest.raises(ConfigError) as exc:
            reconcile_categories([
                entry("synced", name="creds"),
                entry("bindings.rw", name="home"),
            ])
        assert "THIS RULE CHANGED" not in str(exc.value)

    def test_the_synced_winner_is_the_MOST_SPECIFIC_scope(self):
        """N3 — this is the CREDENTIAL pick, so getting it wrong is not a
        cosmetic ordering bug: it copies the WRONG credentials into the box.

        Two ``synced`` entries at one dest are resolved by scope precedence, box
        over workset over system — the same cascade every other override obeys.
        Asserted with the box entry FIRST in the input so a fallback to plain
        input order cannot pass this by accident.
        """
        rec = reconcile_categories([
            entry("synced", name="creds", scope="box", host_src="/box/creds"),
            entry("synced", name="creds", scope="system", host_src="/sys/creds"),
        ])
        assert [c.host_src for c in rec.copies] == ["/box/creds"]

    def test_the_synced_winner_beats_a_seeded_at_the_same_dest(self):
        """A ``synced`` cred copy-sync is not a layer — it REPLACES the seeded
        copies at its dest, from ANY scope (an inode-swap cannot co-exist with a
        file another layer also writes)."""
        rec = reconcile_categories([
            entry("synced", name="creds", scope="system", host_src="/sys/creds"),
            entry("seeded", name="tpl", scope="box", host_src="/box/tpl"),
        ])
        assert [c.host_src for c in rec.copies] == ["/sys/creds"]

    def test_synced_still_outranks_a_non_mask_mount(self):
        rec = reconcile_categories([
            entry("common", name="a"),
            entry("synced", name="creds"),
        ])
        assert categories(rec) == ["synced"]

    def test_a_mount_still_outranks_a_seeded_copy(self):
        rec = reconcile_categories([
            entry("seeded", name="tpl"),
            entry("caches", name="c"),
        ])
        assert categories(rec) == ["caches"]

    def test_masks_still_outranks_synced(self):
        rec = reconcile_categories([
            entry("masks", name=DEST, scope="system"),
            entry("synced", name="creds", scope="box"),
        ])
        assert categories(rec) == ["masks"]

    def test_env_never_participates_in_a_dest_collision(self):
        rec = reconcile_categories([
            entry("env", name="PATH", box_dest="PATH"),
            entry("bindings.rw", name="home", box_dest="PATH"),
        ])
        assert len(rec.envs) == 1
        assert len(rec.mounts) == 1


class TestCredentialGateRunsFirst:
    """T12 — before collision resolution, so a suppressed cred cannot error."""

    def test_private_box_does_not_error_on_a_suppressed_synced(self):
        rec = reconcile_categories(
            [entry("synced", name="creds"), entry("bindings.rw", name="home")],
            deliver_creds=False,
        )
        assert categories(rec) == ["bindings.rw"]

    def test_private_box_drops_a_credential_seed_but_keeps_a_plain_one(self):
        rec = reconcile_categories(
            [
                entry("seeded", name="creds", is_credential=True),
                entry("seeded", name="tpl"),
            ],
            deliver_creds=False,
        )
        assert [c.name for c in rec.copies] == ["tpl"]


# --------------------------------------------------------------------------- #
# T14 — the derived-binding materialisation                                    #
# --------------------------------------------------------------------------- #


class TestDeriveBindingKeys:
    def test_every_abstract_declaration_gets_a_meta_derived_key(self):
        derived = derive_binding_keys([
            entry("common", name="plugins", scope="agent.claude",
                  host_src="/store/common/plugins"),
            entry("caches", name="build", scope="workset", host_src="/c"),
            entry("seeded", name="template", scope="system", host_src="/t"),
        ])
        assert set(derived) == {
            "meta.derived.agent.claude.common.plugins",
            "meta.derived.workset.caches.build",
            "meta.derived.system.seeded.template",
        }
        bind = derived["meta.derived.agent.claude.common.plugins"]
        assert bind.host == "/store/common/plugins"
        assert bind.box == DEST

    def test_a_CONCRETE_binding_gets_nothing(self):
        """M13's target: the derivation is filed under ``meta.derived``, never
        into ``<scope>.bindings.rw``.

        ⚑ Not because the reconcile would break — this map never feeds back into
        the entry list, so nothing would misresolve. Because a key sitting in the
        CONCRETE category that no user wrote and that emits no mount is a forgery
        of the one layer §0 calls the source of truth, and every reader would
        have to learn "some bindings.rw are real and some are shadows".
        """
        assert derive_binding_keys([
            entry("bindings.rw", name="home"),
            entry("bindings.ro", name="vault"),
            entry("masks", name=DEST),
            entry("synced", name="creds"),
            entry("env", name="PATH", box_dest="PATH"),
        ]) == {}

    def test_a_LOSING_declaration_is_materialised_too(self):
        """§0's purpose is "a user can see WHY a mount exists" — a losing
        declaration's derivation is exactly what explains the warning that
        names it, so hiding it would defeat the point."""
        entries = [
            entry("caches", name="build", scope="box", host_src="/loser"),
            entry("common", name="buildcache", scope="box", host_src="/winner"),
        ]
        assert reconcile_categories(entries).warnings  # the loser did lose
        derived = derive_binding_keys(entries)
        assert derived["meta.derived.box.caches.build"].host == "/loser"

    def test_the_derivation_is_idempotent(self):
        entries = [entry("common", name="plugins", scope="box")]
        assert derive_binding_keys(entries) == derive_binding_keys(entries)

    def test_the_launch_seam_installs_them_under_meta_derived(self):
        """The keys land where the READ lens finds them, at the one seam."""
        from kanibako.commands.start import _install_derived_bindings
        from kanibako.settings_store import KeyStore
        from kanibako.settings_views import derived_bindings

        snapshot = KeyStore()
        _install_derived_bindings(snapshot, derive_binding_keys([
            entry("common", name="plugins", scope="agent.claude", host_src="/p"),
        ]))
        node = dict.__getitem__(dict.__getitem__(snapshot, "meta"), "derived")
        assert derived_bindings(node) == {
            "agent.claude.common.plugins": derive_binding_keys([
                entry("common", name="plugins", scope="agent.claude",
                      host_src="/p"),
            ])["meta.derived.agent.claude.common.plugins"],
        }


# --------------------------------------------------------------------------- #
# T18 — the DELETION instrument                                                #
# --------------------------------------------------------------------------- #


def test_the_flat_authority_ladder_is_gone():
    """A behavioural test can pass while a dead ladder rots in place.

    ``_CATEGORY_AUTHORITY`` was a TOTAL order over categories. The §0 table is
    not a permutation of it — it is a different shape (layer membership + scope,
    with three outcomes instead of one), so keeping the dict around would leave a
    structure that reads like the authority is still total: two models of one
    fact, which is how the next reader learns the wrong one.
    """
    from kanibako import settings_categories

    assert not hasattr(settings_categories, "_CATEGORY_AUTHORITY")


class TestRemedyTextIsHonestAboutWhatItCanKnow:
    """The remedy is the non-obvious part (§0), so it must not overstate itself."""

    def test_row1_labels_the_yaml_block_as_a_choice(self):
        """Row 1 has two PEERS — the resolver cannot know which one the user
        wants to keep, so prescribing one would be a guess dressed as advice."""
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.ro", name="vault", scope="system"),
                entry("bindings.rw", name="vault", scope="box"),
            ])
        text = str(exc.value)
        assert "Either entry may be the one you keep" in text
        assert "use whichever key you do NOT want" in text

    def test_row3_does_not_label_it_a_choice(self):
        """Row 3's occupant is DETERMINED — the base always survives."""
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.rw", name="home", scope="box"),
                entry("common", name="plugins", scope="agent.claude"),
            ])
        assert "Either entry may be the one you keep" not in str(exc.value)

    def test_an_agent_scope_occupant_names_the_per_agent_file_spelling(self):
        """The per-agent file spells its own node ``self.<node>``; the canonical
        ``agent.<node>`` form is what a CONTAINING scope's file writes. Printing
        one without the other hands the reader an edit that silently no-ops."""
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.ro", name="a", scope="agent.claude"),
                entry("bindings.rw", name="b", scope="agent.claude"),
            ])
        text = str(exc.value)
        assert "self.claude" in text
        assert "agent:\n  claude:\n    bindings:\n      ro:\n        a: null" in text

    def test_a_box_scope_occupant_gets_no_agent_caveat(self):
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.ro", name="a", scope="box"),
                entry("bindings.rw", name="b", scope="box"),
            ])
        assert "self." not in str(exc.value)


# ---------------------------------------------------------------------------
# A collision on a PREF-INSTALLED declaration must name the REQUEST
# ---------------------------------------------------------------------------

class TestPrefOriginEnrichment:
    """A collision names the DECLARATION key. When a pref installed it, that key
    is one the user never wrote and cannot write — so the message must also name
    the request, or it sends them looking for a key in none of their files."""

    def test_the_error_names_the_installing_pref(self, tmp_path):
        """INVERT: drop the enrichment -> reddens."""
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.errors import CategoryCollisionError
        from kanibako.settings_prefs import PrefRequest

        src = Path(tmp_path) / "box.yaml"
        exc = CategoryCollisionError(
            "two declarations at /home/agent/workspace",
            kind="extension_onto_occupied",
            box_dest="/home/agent/workspace",
            entries=(("agent.claude.common.newthing", "/src"),
                     ("box.bindings.rw.workspace", "/proj")),
        )
        prefs = [PrefRequest(
            target="agent.claude.common.newthing", value=("/src", "~/workspace"),
            level="box", source=src,
        )]
        out = _annotate_pref_origin(exc, prefs)
        text = str(out)
        assert "agent.claude.common.newthing' was installed by" in text
        assert "'pref.agent.claude.common.newthing'" in text
        assert "box settings file" in text
        assert str(src) in text
        assert "edit or remove that request" in text
        # The structured fields survive so downstream consumers still work.
        assert out.kind == exc.kind and out.box_dest == exc.box_dest

    def test_an_unrelated_collision_is_returned_unchanged(self):
        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.errors import CategoryCollisionError

        exc = CategoryCollisionError(
            "boom", kind="binding_vs_binding", box_dest="/d",
            entries=(("box.bindings.ro.a", "/s"),),
        )
        assert _annotate_pref_origin(exc, []) is exc


class TestPrefOriginOnTheAdapterRaise:
    """MUST-1(b) — a pref-installed key can also kill the launch through the
    category ADAPTER (a malformed shape), which raises a plain SettingsError
    with no structured participants. That path must name the REQUEST too."""

    def test_a_plain_settings_error_is_annotated_from_the_message(self, tmp_path):
        """INVERT: drop the message-matching branch -> reddens."""
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.settings_prefs import PrefRequest
        from kanibako.settings_resolve import SettingsError

        src = Path(tmp_path) / "box.yaml"
        exc = SettingsError(
            "category agent.claude.common.x is str, expected a Bind "
            "(present-None binds are omitted at build, §3/§6e)"
        )
        prefs = [PrefRequest(
            target="agent.claude.common.x", value="just-a-string",
            level="box", source=src,
        )]
        out = _annotate_pref_origin(exc, prefs)
        text = str(out)
        assert isinstance(out, SettingsError)
        assert "expected a Bind" in text                 # the original diagnosis
        assert "'agent.claude.common.x' was installed by" in text
        assert "'pref.agent.claude.common.x'" in text
        assert str(src) in text

    def test_an_unrelated_settings_error_is_returned_unchanged(self):
        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.settings_resolve import SettingsError

        exc = SettingsError("something else entirely")
        assert _annotate_pref_origin(exc, []) is exc

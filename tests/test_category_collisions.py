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

⚑⚑ WHOSE TRUTH THIS IS, SINCE CUTOVER 2c — READ BEFORE APPLYING ANY OUTCOME
BELOW TO A REAL BOX. ``reconcile_categories`` is a PURE helper and is no longer
authoritative for BOX ASSEMBLY. What a box is assembled from — its bindings, its
seed list and its sync list — is decided by the assembly COLLAPSE
(``settings.store_collapse.collapse_store_shapes``, installed on the launch path
by ``commands.start._install_assembly_collapse``), and the collapse's refusals
are the launch's. The helper KEEPS RUNNING by design; that is not a migration and
not a second opinion. It is retired at cutover step 5, together with its
arbitration half and never apart from it — NOT here.

So every case below drives a FUNCTION, and only some of them still describe what
a user meets. What the helper still decides on the live path:

* ``envs`` — the collapse produces none (``CollapsedStore`` is bindings + seeded
  + synced), leaving this its sole producer;
* ⚑ **CUTOVER 5-0 TOOK A LINE OUT OF THIS LIST.** The row-5 SAME-SCOPE
  ``warnings`` channel was here, because the helper was its only producer.
  ``commands.start.emit_collision_warnings`` is still the ONE emission seam, but
  ``_install_assembly_collapse`` now feeds it the ``store_shape`` producer's
  warnings as well, so the channel no longer depends on this helper. (What one
  ambiguity reaching that seam TWICE does — nothing: the memo is keyed on the
  ``CategoryCollision`` both arms build — is pinned by
  ``TestTheCollapseRouteFeedsTheSameChannel`` below.)
* rows 1 and 3, which RAISE inside ``commands.start._resolve_launch_snapshot``
  BEFORE the collapse is reached, so their message and remedy text are still
  exactly what a user is handed.

What it no longer decides is row 4's SILENT cross-scope pick: the collapse
refuses two binds at one dest whatever their scopes
(``store_collapse._refuse_bind_over_bind``), so no user can reach the pick even
though the function still performs it. Those tests stay because the behaviour
stays; their outcome is a helper's, not a box's. ⚑ Do not "correct" them by
inverting an assertion — the function is unchanged and the inversion would be
false.
"""

from __future__ import annotations

import logging

import pytest
import yaml

from kanibako.errors import CategoryCollisionError, ConfigError
from kanibako.settings.settings_categories import (
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

    ⚑⚑ **QUARANTINE — THESE KEY STRINGS ARE NOT THE LIVE SPELLING.** In
    production the last segment of a declaration key IS the destination (R-10):
    ``_emit_bind_map`` passes one map key as both ``name`` and the key tail, so a
    real key reads ``box.bindings.rw.~/workspace``, never ``box.bindings.rw.vault``.
    The synthetic entries below still carry a distinct NAME token. That is DRIFT
    left by the 2026-08-08c dest-keying flip, not a design: ``reconcile_categories``
    and ``derive_binding_keys`` are pure functions over these fields and never read
    the ``name``/``box_dest`` relationship, so nothing here raised and nothing went
    red. **Do not copy these key strings anywhere as examples of a real key.**

    Two consequences worth knowing while reading this file, both recorded rather
    than papered over:

    * no case here exercises the LIVE shape of a collision, where the two
      participants' keys end in the SAME segment and differ only by scope and/or
      category;
    * ``test_same_category_same_scope_also_warns`` drives two entries at one dest
      in ONE category at ONE scope. Under dest-keying that is one key holding one
      entry per destination, so the arrangement can no longer arise at all — the
      reachable row-5 case is the DIFFERENT-category pair its sibling test covers.

    Repairing this means re-basing ~20 key/remedy-text assertions and deciding
    what replaces that dissolved case; it is tracked separately and deliberately
    NOT folded into the pref-origin repair below.
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
        key_segments=(*scope.split("."), *category.split("."), name),
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

    ⚑ It certifies the RECONCILE arm of that install, and only that arm. Whether
    the same shipped set survives the ASSEMBLY COLLAPSE — the route that decides
    the install since cutover 2c — is a separate question, and neither this file
    nor the per-mode probe it borrows asks it.
    """

    def test_every_mode_and_agent_shape_resolves_clean(self, tmp_path):
        from tests.test_categories_live import _probe_cases, _probe_snapshot

        from kanibako.settings.settings_launch import snapshot_category_entries

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

        from kanibako.settings.settings_launch import snapshot_category_entries

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
    """T6 — scope precedence decides and nothing is said, IN THE HELPER.

    ⚑⚑ THE OUTCOME THIS CLASS NAMES IS NO LONGER REACHABLE FROM A LAUNCH (see the
    module docstring). The two CROSS-SCOPE cases below are exactly the
    arrangement the assembly collapse refuses — two binds at one dest, whatever
    their scopes (``store_collapse._refuse_bind_over_bind``) — so a real box
    never gets the silent pick; it fails to assemble instead. The function still
    performs the pick, and that is what is asserted here, deliberately and
    unchanged: this is the pure helper's behaviour, not a box's.

    ⚑ The caveat is per-CASE, not per-class:
    ``test_agent_default_and_active_count_as_ONE_scope`` is NOT a cross-scope
    case in the collapse's terms — see its own docstring.
    """

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

        ⚑ THE CANARY, and it is pinning a PURE FUNCTION on purpose. It calls the
        helper directly, so what it proves — that the helper's own answer does
        not depend on input order — is untouched by the collapse taking over box
        assembly. A cutover step that expects this to change is reading it as an
        assembly claim; it is not one.
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

        ⚑ The collapse reads it the same way, which is why the class-level
        caveat does not reach this case: both entries carry the one ``agent``
        scope token, so they fold into a single scope's shape and never meet
        ``_refuse_bind_over_bind``. Nothing refuses this arrangement, and the
        warning below stays the only announcement the user gets.
        """
        rec = reconcile_categories([
            entry("common", name="a", scope="agent.default", host_src="/def"),
            entry("caches", name="b", scope="agent.claude", host_src="/act"),
        ])
        assert len(rec.warnings) == 1
        assert rec.warnings[0].scope == "agent"
        assert set(rec.warnings[0].loser_keys) == {"agent.default.common.a"}


class TestRow5SameScopeWarns:
    """T7 — proceed on the existing ordering, and say so.

    ⚑ Row 5 is the arm of the helper that is STILL USER-VISIBLE: the warnings it
    returns are emitted on the live launch path
    (``commands.start.emit_collision_warnings``) and that emission survives until
    cutover step 5 retires the helper. Read these as live behaviour — the module
    docstring's caveat is about row 4, not this row.

    ⚑ What they are no longer the SOLE source of is the channel itself (5-0): the
    ``store_shape`` producer computes the same ambiguity per scope and
    ``_install_assembly_collapse`` feeds it to the same seam. So a case going red
    here means the HELPER stopped warning, not that the user stopped being told.
    """

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
        decided nothing and naming a "winner" there would be a lie.

        ⚑ A row-4 shape, so the class-level caveat on
        ``TestRow4CrossScopeIsSilent`` applies here and not the one above it:
        three binds at one dest across two scopes is an arrangement the collapse
        refuses outright, so the silence asserted below is the helper's alone.
        """
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
        from kanibako.settings.settings_categories import CategoryCollision

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
        from kanibako.settings.settings_categories import CategoryCollision

        other = CategoryCollision(
            box_dest="/g/y", scope="box", winner_key="box.common.d",
            loser_keys=("box.caches.c",),
        )
        with caplog.at_level(logging.WARNING):
            emit_collision_warnings([self._collision(), other])
        assert len([r for r in caplog.records if "/g/" in r.getMessage()]) == 2


class TestTheCollapseRouteFeedsTheSameChannel:
    """CUTOVER 5-0 — the row-5 warning has a home in the NEW route, and only one.

    Step 5 deletes ``reconcile_categories``' arbitration half together with the
    ``emit_collision_warnings(reconciled.warnings)`` call in
    ``_resolve_launch_snapshot``. These two say what has to be true for that to be a
    deletion rather than a regression: the collapse route announces the ambiguity BY
    ITSELF, and with BOTH feeds live one ambiguity is still one line — because the two
    build the SAME ``CategoryCollision``, and the emitter's memo is keyed on it.

    ⚑ Both drive ``_install_assembly_collapse`` itself, not the producer beneath it: what
    is under test is the WIRING, so a test that called ``build_store_shape_set`` and
    emitted its warnings by hand would stay green with the wiring ripped out.
    """

    @pytest.fixture(autouse=True)
    def _clean(self):
        """The memo is PROCESS state — reset it around each case, both sides."""
        from kanibako.commands.start import reset_collision_warnings

        reset_collision_warnings()
        yield
        reset_collision_warnings()

    def _ambiguous(self):
        """One same-scope abstraction ambiguity at ``DEST``, and no home bind.

        No home means ``whole_box=False`` — the narrow shape — which is enough: the
        shapes are built, and the warnings with them, before the bind fold the home
        gates.
        """
        return [
            entry("caches", name="build", scope="box", host_src="/cache"),
            entry("common", name="buildcache", scope="box", host_src="/common"),
        ]

    def _lines(self, caplog):
        return [r.getMessage() for r in caplog.records if DEST in r.getMessage()]

    def test_the_collapse_route_ALONE_announces_the_ambiguity(self, caplog):
        """Delete the reconcile feed tomorrow and the user still hears about it.

        MUTATION ANCHOR: drop the ``emit_collision_warnings(shapes.warnings)`` call from
        ``_install_assembly_collapse`` and this fails on ``assert 0 == 1`` — nothing
        else in ``src/`` emits a collision.
        """
        from kanibako.commands.start import _install_assembly_collapse
        from kanibako.settings.keystore import KeyStore

        with caplog.at_level(logging.WARNING):
            _install_assembly_collapse(
                KeyStore(), self._ambiguous(), whole_box=False,
            )

        assert len(self._lines(caplog)) == 1
        # The LOSER is the actionable half — it names the declaration to edit.
        assert "box.caches.build" in self._lines(caplog)[0]

    def test_BOTH_feeds_live_still_produce_EXACTLY_ONE_line(self, caplog):
        """The ordering rule the plan states: never two lines for one ambiguity.

        Driven in the live order — the reconcile emission first, the collapse install
        after — because that is the order ``_resolve_launch_snapshot`` runs them in.

        MUTATION ANCHOR: make the two arms disagree about the pair the memo is keyed on
        (e.g. give ``store_shape``'s ``CategoryCollision`` a different ``scope``) and
        this fails on ``assert 2 == 1``.
        """
        from kanibako.commands.start import (
            _install_assembly_collapse,
            emit_collision_warnings,
        )
        from kanibako.settings.keystore import KeyStore

        entries = self._ambiguous()
        with caplog.at_level(logging.WARNING):
            emit_collision_warnings(reconcile_categories(entries).warnings)
            _install_assembly_collapse(KeyStore(), entries, whole_box=False)

        assert len(self._lines(caplog)) == 1

    def test_a_MASKED_destinations_ambiguity_is_announced_where_it_once_was_not(
        self, caplog,
    ):
        """The one place 5-0 changed what a user sees, pinned (CHANGELOG, Unreleased).

        A ``masks`` entry at the dest makes the RECONCILE silent about the pair beneath
        it (§0 row 2 returns the mask and no warnings, from any scope). The producer
        folds each scope alone, so it still reports the pair — and the pair is still
        ambiguous, mask or no mask.

        MUTATION ANCHOR: filter ``shapes.warnings`` down to what the reconcile also
        reports before emitting, and this fails on ``assert 0 == 1`` while both tests
        above stay green — which is exactly the shape of edit this exists to catch.
        """
        from kanibako.commands.start import (
            _install_assembly_collapse,
            emit_collision_warnings,
        )
        from kanibako.settings.keystore import KeyStore

        entries = [*self._ambiguous(), entry("masks", name="hide", scope="box")]
        reconciled = reconcile_categories(entries)
        assert reconciled.warnings == (), "the reconcile arm must be the silent one here"

        with caplog.at_level(logging.WARNING):
            emit_collision_warnings(reconciled.warnings)
            _install_assembly_collapse(KeyStore(), entries, whole_box=False)

        assert len(self._lines(caplog)) == 1


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
    def test_every_abstract_declaration_gets_a_binding_derivations_entry(self):
        derived = derive_binding_keys([
            entry("common", name="plugins", scope="agent.claude",
                  host_src="/store/common/plugins"),
            entry("caches", name="build", scope="workset", host_src="/c"),
            entry("seeded", name="template", scope="system", host_src="/t"),
        ])
        assert set(derived) == {
            ("binding_derivations", "agent", "claude", "common", "plugins"),
            ("binding_derivations", "workset", "caches", "build"),
            ("binding_derivations", "system", "seeded", "template"),
        }
        bind = derived[
            ("binding_derivations", "agent", "claude", "common", "plugins")
        ]
        assert bind.host == "/store/common/plugins"
        assert bind.box == DEST

    def test_a_CONCRETE_binding_gets_nothing(self):
        """M13's target: the derivation is filed under ``binding_derivations``,
        never into ``<scope>.bindings.rw``.

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
        assert derived[
            ("binding_derivations", "box", "caches", "build")
        ].host == "/loser"

    def test_the_derivation_is_idempotent(self):
        entries = [entry("common", name="plugins", scope="box")]
        assert derive_binding_keys(entries) == derive_binding_keys(entries)

    def test_the_launch_seam_installs_them_under_binding_derivations(self):
        """The entries land where the READ lens finds them — under the reserved
        ``binding_derivations`` node at the SNAPSHOT ROOT (R-8) — at the one
        seam."""
        from kanibako.commands.start import _install_derived_bindings
        from kanibako.settings.keystore import KeyStore
        from kanibako.settings.settings_views import derived_bindings

        snapshot = KeyStore()
        _install_derived_bindings(snapshot, derive_binding_keys([
            entry("common", name="plugins", scope="agent.claude", host_src="/p"),
        ]))
        node = dict.__getitem__(snapshot, "binding_derivations")
        assert derived_bindings(node) == {
            "agent.claude.common.plugins": derive_binding_keys([
                entry("common", name="plugins", scope="agent.claude",
                      host_src="/p"),
            ])[("binding_derivations", "agent", "claude", "common", "plugins")],
        }

    def test_a_dotted_DESTINATION_installs_as_ONE_node(self):
        """⚑ The dest is DATA and real dests carry dots — it may not shatter.

        Pre-fix the map was keyed by the DOTTED declaration key and installed with
        ``insert_dotted``, so ``/home/agent/.claude/plugins`` nested as
        ``'/home/agent/' → 'claude/plugins'``: two tree levels where the
        declaration has one dest.
        """
        from kanibako.commands.start import _install_derived_bindings
        from kanibako.settings.kb_store import Bind
        from kanibako.settings.keystore import KeyStore

        dest = "/home/agent/.claude/plugins"
        snapshot = KeyStore()
        _install_derived_bindings(snapshot, derive_binding_keys([
            entry("common", name=dest, box_dest=dest, scope="agent.claude",
                  host_src="/store/plugins"),
        ]))
        node = dict.__getitem__(snapshot, "binding_derivations")
        common = node["agent"]["claude"]["common"]
        assert list(dict.keys(common)) == [dest]
        assert dict.__getitem__(common, dest) == Bind(
            host="/store/plugins", box=dest, opts=_bind_options("common"),
        )

    def test_two_dotted_dests_that_would_NEST_once_split_both_survive(self):
        """⚑⚑ THE DATA LOSS, pinned. ``~/.cache/uv`` and ``~/.cache/uv.lock``
        split into paths where the first's LEAF is the second's parent NODE, so
        installing the second used to replace the first's derivation — no error,
        no diff, one declaration simply gone from the node."""
        from kanibako.commands.start import _install_derived_bindings
        from kanibako.settings.keystore import KeyStore

        first, second = "~/.cache/uv", "~/.cache/uv.lock"
        snapshot = KeyStore()
        _install_derived_bindings(snapshot, derive_binding_keys([
            entry("caches", name=first, box_dest=first, host_src="/h/uv"),
            entry("caches", name=second, box_dest=second, host_src="/h/lock"),
        ]))
        caches = dict.__getitem__(snapshot, "binding_derivations")["box"]["caches"]
        assert set(dict.keys(caches)) == {first, second}
        assert dict.__getitem__(caches, first).host == "/h/uv"
        assert dict.__getitem__(caches, second).host == "/h/lock"


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
    from kanibako.settings import settings_categories

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


def _remedy_block(text: str, scope: str) -> str:
    """The YAML block of a remedy — the ``<scope>:`` line plus its indented tail."""
    lines = text.splitlines()
    start = lines.index(f"{scope}:")
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith(" "):
            break
        block.append(line)
    return "\n".join(block)


class TestRemedyBlockKeysTheDestWhole:
    """A dest is DATA: the remedy must key it WHOLE, never split it on ``.``.

    ⚑ Every OTHER case in this file uses a dot-free name, so all of them were
    green while the block shattered ``/home/agent/.claude/plugins`` into two tree
    levels and printed a path that is not addressable at all (the keyspace is
    CLOSED). These two drive the LIVE shape — the last segment IS the
    destination (R-10) — one per caller of ``_suppress_then_add``.
    """

    def test_row1_prints_the_dotted_dest_as_one_key(self):
        dest = "/home/agent/.claude/plugins"
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.ro", name=dest, box_dest=dest, scope="box",
                      host_src="/srv/plugins"),
                entry("bindings.rw", name=dest, box_dest=dest, scope="box",
                      host_src="/home/jei/plugins"),
            ])
        assert (
            f"box:\n  bindings:\n    ro:\n      {dest}: null"
        ) in str(exc.value)

    def test_row3_block_parses_back_to_the_real_declaration(self):
        """The oracle is the READER: ``parse_bind_map`` takes the map key whole
        and keeps a ``null`` value as the per-entry reset."""
        dest = "~/.cache/uv"
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                entry("bindings.rw", name=dest, box_dest=dest, scope="box",
                      host_src="/var/cache/uv"),
                entry("caches", name=dest, box_dest=dest, scope="agent.claude",
                      host_src="/ext"),
            ])
        block = yaml.safe_load(_remedy_block(str(exc.value), "box"))
        assert block == {"box": {"bindings": {"rw": {dest: None}}}}


# ---------------------------------------------------------------------------
# A collision on a PREF-INSTALLED declaration must name the REQUEST
# ---------------------------------------------------------------------------
#
# ⚑⚑ THE FIXTURES BELOW WERE REWRITTEN 2026-08-08c AND THE OLD ONES WERE GREEN.
# They spelled the pref target per-NAME (``pref.agent.claude.common.newthing``)
# and carried a 2-element value ``("/src", "~/workspace")``. Both dissolved when
# ``common`` / ``caches`` / ``seeded`` / ``synced`` joined ``bindings.{ro,rw}``
# and ``masks`` as TERMINAL dest-keyed keys:
#
# * ``key_reason`` now REFUSES ``agent.claude.common.<name>`` outright, so
#   ``apply_prefs`` would never have let that request through — the test drove a
#   ``PrefRequest`` no collector can produce;
# * the flip PRESERVED ARITY. A 2-element bind used to mean ``(host, box)``; a
#   ``BindEntry`` means ``(src, opts)``. ``"~/workspace"`` sat where OPTIONS
#   belong and nothing raised, because these tests never read the value at all.
#
# THE OLD GREEN WAS NOT EVIDENCE. Neither test exercised the shape it named, and
# the enrichment they nominally covered had in fact stopped firing for all seven
# dest-keyed categories. The negative cases below exist because the obvious
# repair — a prefix match on the target — is green against the positive cases and
# MISATTRIBUTES against these.


def _pref_map(**entries):
    """A dest-keyed category VALUE, shaped as ``_file_partial`` would parse it.

    A ``KeyStore`` node of ``dest -> BindEntry(src, opts)``. Written as a helper
    because the destinations are paths (``~/plugins``) and cannot be keyword
    names — the caller passes ``{dest: BindEntry(...)}`` through ``**``-unpacking
    of a literal dict instead.
    """
    from kanibako.settings.keystore import KeyStore

    return KeyStore(entries)


class TestPrefOriginEnrichment:
    """A collision names the DECLARATION key plus the entry's DEST. When a pref
    installed it, that key is one the user never wrote and cannot write — the
    dest lives INSIDE the value the pref carries — so the message must also name
    the request, or it sends them looking for a key in none of their files."""

    def test_the_error_names_the_installing_pref(self, tmp_path):
        """INVERT: drop the enrichment -> reddens."""
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.errors import CategoryCollisionError
        from kanibako.settings.kb_store import BindEntry
        from kanibako.settings.settings_prefs import PrefRequest

        src = Path(tmp_path) / "box.yaml"
        exc = CategoryCollisionError(
            "two declarations at /home/agent/workspace",
            kind="extension_onto_occupied",
            box_dest="/home/agent/workspace",
            entries=(("agent.claude.common.~/workspace", "/src"),
                     ("box.bindings.rw.~/workspace", "/proj")),
        )
        prefs = [PrefRequest(
            target="agent.claude.common",
            value=_pref_map(**{"~/workspace": BindEntry("/src", None)}),
            level="box", source=src,
        )]
        out = _annotate_pref_origin(exc, prefs)
        text = str(out)
        # The ENTRY key is named in full (target + dest); the REQUEST is named in
        # the only spelling the user can write.
        assert "agent.claude.common.~/workspace' was installed by" in text
        assert "'pref.agent.claude.common'" in text
        assert "box settings file" in text
        assert str(src) in text
        assert "edit or remove that request" in text
        # The structured fields survive so downstream consumers still work.
        assert out.kind == exc.kind and out.box_dest == exc.box_dest

    def test_a_pref_on_the_category_that_omits_the_dest_is_not_blamed(self, tmp_path):
        """THE MISATTRIBUTION GUARD — a prefix match on the target passes the
        test above and FAILS here.

        The declaration key's category was pref-targeted, but this request never
        mentions the colliding destination: that entry came from the agent
        settings file, the launch floor, or another level entirely. Naming this
        file would send the user to edit a line that has nothing to do with the
        collision, which is worse than saying nothing.
        """
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.errors import CategoryCollisionError
        from kanibako.settings.kb_store import BindEntry
        from kanibako.settings.settings_prefs import PrefRequest

        exc = CategoryCollisionError(
            "two declarations at /home/agent/workspace",
            kind="extension_onto_occupied",
            box_dest="/home/agent/workspace",
            entries=(("agent.claude.common.~/workspace", "/src"),
                     ("box.bindings.rw.~/workspace", "/proj")),
        )
        prefs = [PrefRequest(
            target="agent.claude.common",
            value=_pref_map(**{"~/elsewhere": BindEntry("/other", None)}),
            level="box", source=Path(tmp_path) / "box.yaml",
        )]
        assert _annotate_pref_origin(exc, prefs) is exc

    def test_the_level_that_DECLARES_the_dest_wins_not_the_last_one(self, tmp_path):
        """Two requests on ONE category from two levels are told apart by which
        one declares the destination — not by overlay order.

        Box prefs outrank workset prefs, so a bare last-wins pick over everything
        targeting the category names the BOX file here. But the box request
        declares a different destination; the colliding entry can only have come
        from the workset one.
        """
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.errors import CategoryCollisionError
        from kanibako.settings.kb_store import BindEntry
        from kanibako.settings.settings_prefs import PrefRequest

        ws = Path(tmp_path) / "workset.yaml"
        box = Path(tmp_path) / "box.yaml"
        exc = CategoryCollisionError(
            "two declarations at /home/agent/workspace",
            kind="extension_onto_occupied",
            box_dest="/home/agent/workspace",
            entries=(("agent.claude.common.~/workspace", "/src"),
                     ("box.bindings.rw.~/workspace", "/proj")),
        )
        # APPLICATION ORDER: workset first, box second (``collect_prefs``).
        prefs = [
            PrefRequest(
                target="agent.claude.common",
                value=_pref_map(**{"~/workspace": BindEntry("/src", None)}),
                level="workset", source=ws,
            ),
            PrefRequest(
                target="agent.claude.common",
                value=_pref_map(**{"~/elsewhere": BindEntry("/other", None)}),
                level="box", source=box,
            ),
        ]
        text = str(_annotate_pref_origin(exc, prefs))
        assert "workset settings file" in text
        assert str(ws) in text
        assert str(box) not in text

    def test_when_both_levels_declare_the_dest_the_BOX_request_is_named(
        self, tmp_path,
    ):
        """Last-wins is still the tie-break, because that is the overlay order:
        ``BOX_PREFS`` sits above ``WORKSET_PREFS`` (spec §1A)."""
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.errors import CategoryCollisionError
        from kanibako.settings.kb_store import BindEntry
        from kanibako.settings.settings_prefs import PrefRequest

        ws = Path(tmp_path) / "workset.yaml"
        box = Path(tmp_path) / "box.yaml"
        exc = CategoryCollisionError(
            "two declarations at /home/agent/workspace",
            kind="extension_onto_occupied",
            box_dest="/home/agent/workspace",
            entries=(("agent.claude.common.~/workspace", "/src"),),
        )
        prefs = [
            PrefRequest(
                target="agent.claude.common",
                value=_pref_map(**{"~/workspace": BindEntry("/ws", None)}),
                level="workset", source=ws,
            ),
            PrefRequest(
                target="agent.claude.common",
                value=_pref_map(**{"~/workspace": BindEntry("/box", None)}),
                level="box", source=box,
            ),
        ]
        text = str(_annotate_pref_origin(exc, prefs))
        assert "box settings file" in text
        assert str(box) in text
        assert str(ws) not in text

    def test_a_SUPPRESSING_null_entry_is_never_an_origin(self, tmp_path):
        """Present-``None`` at a dest REMOVES the entry (§2h / §6e); it installs
        nothing. A surviving entry at that dest is somebody else's, so blaming
        the suppression would point at the one line that was trying to get rid
        of it."""
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.errors import CategoryCollisionError
        from kanibako.settings.settings_prefs import PrefRequest

        exc = CategoryCollisionError(
            "two declarations at /home/agent/workspace",
            kind="extension_onto_occupied",
            box_dest="/home/agent/workspace",
            entries=(("agent.claude.common.~/workspace", "/src"),),
        )
        prefs = [PrefRequest(
            target="agent.claude.common",
            value=_pref_map(**{"~/workspace": None}),
            level="box", source=Path(tmp_path) / "box.yaml",
        )]
        assert _annotate_pref_origin(exc, prefs) is exc

    def test_a_per_VAR_target_still_matches_the_key_EXACTLY(self, tmp_path):
        """``env.<VAR>`` / ``secret_path.<VAR>`` did NOT go dest-keyed: ``<VAR>``
        IS a key segment, so target and declaration key are the same string.
        Pinned here because the dest-keyed repair must not disturb it."""
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.errors import CategoryCollisionError
        from kanibako.settings.settings_prefs import PrefRequest

        src = Path(tmp_path) / "box.yaml"
        exc = CategoryCollisionError(
            "two declarations at /run/secrets/TOK",
            kind="binding_vs_binding",
            box_dest=f"{SECRET_MOUNT_DIR}/TOK",
            entries=(("agent.claude.secret_path.TOK", "/h/tok"),
                     ("box.bindings.rw.~/tok", "/h/other")),
        )
        prefs = [PrefRequest(
            target="agent.claude.secret_path.TOK", value="/h/tok",
            level="box", source=src,
        )]
        text = str(_annotate_pref_origin(exc, prefs))
        assert "'agent.claude.secret_path.TOK' was installed by" in text
        assert "'pref.agent.claude.secret_path.TOK'" in text

    def test_an_unrelated_collision_is_returned_unchanged(self):
        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.errors import CategoryCollisionError

        exc = CategoryCollisionError(
            "boom", kind="binding_vs_binding", box_dest="/d",
            entries=(("box.bindings.ro.~/a", "/s"),),
        )
        assert _annotate_pref_origin(exc, []) is exc


class TestPrefOriginOnTheAdapterRaise:
    """MUST-1(b) — a pref-installed key can also kill the launch through the
    category ADAPTER (a malformed shape), which raises a plain SettingsError
    with no structured participants. That path must name the REQUEST too.

    ⚑ The adapter has TWO raises a pref can reach and they are COMPLEMENTARY,
    which is why one matching rule covers both: ``_emit_bind_map`` fires per LEAF
    and names ``<target>.<dest>`` (reachable only when the value IS a map), while
    ``_assert_declared_categories`` fires at the CATEGORY ROOT and names the bare
    ``<target>`` (reachable only when the value is NOT a map). One test each.
    """

    def test_a_malformed_LEAF_is_annotated_from_the_message(self, tmp_path):
        """INVERT: drop the message-matching branch -> reddens."""
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.settings.settings_prefs import PrefRequest
        from kanibako.settings.settings_resolve import SettingsError

        src = Path(tmp_path) / "box.yaml"
        exc = SettingsError(
            "category agent.claude.common.~/plugins is str, expected a "
            "BindEntry (common is dest-keyed: the map key is the destination; "
            "present-None binds are omitted at build, §3/§6e)"
        )
        prefs = [PrefRequest(
            target="agent.claude.common",
            value=_pref_map(**{"~/plugins": "just-a-string"}),
            level="box", source=src,
        )]
        out = _annotate_pref_origin(exc, prefs)
        text = str(out)
        assert isinstance(out, SettingsError)
        assert "expected a BindEntry" in text            # the original diagnosis
        assert "'agent.claude.common.~/plugins' was installed by" in text
        assert "'pref.agent.claude.common'" in text
        assert str(src) in text

    def test_a_CATEGORY_ROOT_raise_names_the_bare_target(self, tmp_path):
        """``pref.agent.claude.common: "oops"`` installs a scalar where a
        dest-keyed map belongs. There is no dest to name on either side, and the
        bare target is what the adapter's own message carries."""
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.settings.settings_prefs import PrefRequest
        from kanibako.settings.settings_resolve import SettingsError

        src = Path(tmp_path) / "box.yaml"
        exc = SettingsError(
            "agent.claude.common is a value at a CATEGORY ROOT (str: 'oops'), "
            "which is not a declared key; declare it as a map keyed by box "
            "destination, {box_dest: [src[, options]]} (spec §2a / §2d)"
        )
        prefs = [PrefRequest(
            target="agent.claude.common", value="oops", level="box", source=src,
        )]
        text = str(_annotate_pref_origin(exc, prefs))
        assert "'agent.claude.common' was installed by" in text
        assert "'pref.agent.claude.common'" in text
        assert str(src) in text

    def test_a_pref_that_omits_the_named_dest_is_not_blamed(self, tmp_path):
        """The text branch's guard, mirroring the structured one. A bare
        ``req.target in text`` substring test passes the LEAF case above and
        fires here too — the message names ``agent.claude.common`` either way."""
        from pathlib import Path

        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.settings.kb_store import BindEntry
        from kanibako.settings.settings_prefs import PrefRequest

        from kanibako.settings.settings_resolve import SettingsError

        exc = SettingsError(
            "category agent.claude.common.~/plugins is str, expected a BindEntry"
        )
        prefs = [PrefRequest(
            target="agent.claude.common",
            value=_pref_map(**{"~/elsewhere": BindEntry("/other", None)}),
            level="box", source=Path(tmp_path) / "box.yaml",
        )]
        assert _annotate_pref_origin(exc, prefs) is exc

    def test_an_unrelated_settings_error_is_returned_unchanged(self):
        from kanibako.commands.start import _annotate_pref_origin
        from kanibako.settings.settings_resolve import SettingsError

        exc = SettingsError("something else entirely")
        assert _annotate_pref_origin(exc, []) is exc

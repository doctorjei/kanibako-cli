"""The spec §0 ``box_dest`` COLLISION TABLE and the derived-binding materialisation.

One test per table row, plus the behaviours the table does not mention and which
must survive it byte-for-byte (the pure-``seeded`` overlay, the credential gate
ordering, the ``secret_path`` per-VAR cascade).

⚑ EVERY case here is MUTATION-PROVEN: each one reddens when its own branch is
inverted, and stays green when the others are. A test that passes because the rule
is incidentally satisfied proves nothing, and the five-row table is exactly the shape
that invites incidental passes.

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

⚑⚑ **WHO DECIDES WHICH ROW — READ THIS BEFORE ADDING A CASE.** Until cutover 6-R3
a single cross-scope helper (``reconcile_categories``) applied the whole table, and
most cases here drove it. It is DELETED. The table is applied by THREE seams, each
holding the inputs its own rows need, and a case belongs to the seam that owns it:

* the per-scope ``store_shape`` PRODUCER — rows 3 and 5, and row 1's SAME-SCOPE
  case, through the two public raisers. Its own file is
  ``tests/test_settings/test_store_shape.py``.
* the assembly COLLAPSE — rows 2 and 4 and row 1's CROSS-SCOPE case, over the
  scopes folded onto pid 0. Its own file is
  ``tests/test_settings/test_store_collapse.py``; its wiring into a launch is
  ``tests/test_commands/test_start_assembly.py``.
* the LAUNCH SEAM's two functions here — ``secret_path_deliveries`` (a
  ``secret_path`` dest, which has no arm in the store shape so the collapse never
  sees it) and ``narrow_table_winners`` (a narrow resolve's own injected table,
  where the collapse returns early). Both are pinned in THIS file, below.

🕯️ WHAT 6-R3 RETIRED HERE, all of it with a named successor at its own site:
``TestRow2MaskOverrides`` · ``TestRow4CrossScopeIsSilent`` (bar its D5 case) ·
``TestRow5SameScopeProceedsOnTheExistingOrdering`` ·
``TestPreservedCopyAndCrossDeliveryRules`` (bar two cases recomposed onto the
collapse and the carrier) · ``TestRow1SecretPathCarveOut`` ·
``test_there_is_NO_SECOND_FEED_left_to_add`` · and the three 6-R1/6-R2 equivalence
canaries. What survives states the SAME claims against the seam that now decides
them — never a rebase that would have one function assert it equals itself.

⚑ The MESSAGES are single-sourced in ``raise_binding_vs_binding`` and
``raise_extension_onto_occupied`` (three callers each), so every message case below
drives the RAISER directly: that is where the text is, and a second copy of it is
the drift the extraction exists to prevent.
"""

from __future__ import annotations

import logging

import pytest
import yaml

from kanibako.errors import CategoryCollisionError
from kanibako.settings.settings_categories import (
    SECRET_MOUNT_DIR,
    CategoryEntry,
    _bind_options,
    _DELIVERY,
    derive_binding_keys,
    gate_credential_delivery,
    narrow_table_winners,
    raise_binding_vs_binding,
    raise_extension_onto_occupied,
    secret_path_deliveries,
    secret_path_winners,
)
from kanibako.settings.store_shape import build_store_shape_set

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
    left by the 2026-08-08c dest-keying flip, not a design: the seams below and
    ``derive_binding_keys`` are pure functions over these fields and never read the
    ``name``/``box_dest`` relationship, so nothing here raised and nothing went
    red. **Do not copy these key strings anywhere as examples of a real key.**

    Two consequences worth knowing while reading this file, both recorded rather
    than papered over:

    * no case here exercises the LIVE shape of a collision, where the two
      participants' keys end in the SAME segment and differ only by scope and/or
      category;
    * the row-5 same-category case (two entries at one dest in ONE category at ONE
      scope) cannot arise under dest-keying at all — one key holds one entry per
      destination — so the reachable row-5 case is the DIFFERENT-category pair,
      pinned on the producer in ``test_store_shape.py``.

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


def bound(entries) -> "dict[str, str]":
    """``{dest: src}`` for everything the PRODUCER folds into a scope\'s bind arms.

    The successor to the retired reconcile\'s mount list, for the cases that ask
    "what occupies this destination". It reads ONE scope\'s shape, because that is
    the unit the producer is responsible for; a cross-scope question belongs to the
    collapse and to its own file.
    """
    shape = build_store_shape_set(entries)["box"]
    return {
        dest: e.src for arm in (shape.ro, shape.rw) for dest, e in arm.items()
    }


# --------------------------------------------------------------------------- #
# T1 — the SHIPPED default set fires nothing (the bridge to the real world)     #
# --------------------------------------------------------------------------- #


class TestShippedDefaultsAreQuiet:
    """T1 — the synthetic rules below must not fire on a real install.

    Every other test here drives a seam with CONSTRUCTED entries, which proves the
    rules are enforced but says nothing about whether they fire in practice. This one resolves the REAL shipped defaults through
    the REAL pipeline (see ``tests/test_categories_live.py`` for the per-mode
    probe it borrows) and asserts zero errors and zero warnings — which is also
    the M-7 real-world exposure check.

    ⚑⚑ THE ERROR HALF NOW ASKS THE ROUTE THAT DECIDES THE INSTALL. It used to
    certify the retired cross-scope helper's arm and only that, and said so — the
    question it left open was whether the same shipped set survives the ASSEMBLY
    COLLAPSE. 6-R3 closed the gap by necessity: the helper is gone, so the
    recomposition drives ``_install_assembly_collapse`` and asserts a real box's
    bind map comes out. That is strictly the stronger claim.

    ⚑ The WARNING half is asked of the PRODUCER (cutover 5-1c retargeted it).
    That is not a widening for its own sake: the producer is what a user actually
    hears from now, so "a real install prints no collision warning" is only a
    true claim if it is asked there. It folds each scope ALONE, so it is also the
    STRICTER of the two arms — the reconcile's row-2/row-4 silences do not apply.
    """

    def test_every_mode_and_agent_shape_resolves_clean(self, tmp_path):
        from tests.test_categories_live import _probe_cases, _probe_snapshot

        from kanibako.commands.start import _install_assembly_collapse
        from kanibako.settings.settings_launch import snapshot_category_entries

        for mode, proj, ws_root, hl in _probe_cases(tmp_path):
            snap, ctx = _probe_snapshot(mode, proj, ws_root, hl)
            for agent in ("claude", "no_agent"):
                entries = snapshot_category_entries(
                    snap, active_agent=agent, box_ctx=ctx,
                )
                produced = build_store_shape_set(entries)
                assert produced.warnings == (), (mode, agent, produced.warnings)
                # The COLLAPSE, on the real shipped set: it must fold without
                # refusing, and produce a non-empty bind map.
                _install_assembly_collapse(snap, entries, whole_box=True)
                assert dict.get(
                    dict.get(dict.get(snap, "meta"), "assembly"), "bindings",
                ), (mode, agent)

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
    """T2 — ERROR, always, any scope, any mode.

    ⚑ SPLIT ACROSS ITS TWO DECIDERS AT 6-R3. The SAME-SCOPE case is the producer\'s
    and is driven here; the CROSS-SCOPE case is the COLLAPSE\'s and is pinned in
    ``tests/test_settings/test_store_collapse.py::TestHomeIsPidZero``
    (``test_a_SECOND_bind_at_home_is_refused`` and its two remedy siblings), which is
    the same ``_refuse_bind_over_bind`` for every dest. ⚑
    ``test_cross_scope_raises_instead_of_letting_the_box_win`` DIED there rather than
    being rebased here: this file has no scope fold to drive it through.

    The MESSAGE is the raiser\'s, so it is asked of the raiser.
    """

    def test_ro_and_rw_at_one_dest_same_scope_raises(self):
        with pytest.raises(CategoryCollisionError) as exc:
            build_store_shape_set([
                entry("bindings.ro", name="vault", host_src="/srv/shared"),
                entry("bindings.rw", name="mine", host_src="/home/jei/vault"),
            ])
        assert exc.value.kind == "binding_vs_binding"
        assert exc.value.box_dest == DEST

    def test_message_names_the_dest_both_keys_and_the_remedy(self):
        with pytest.raises(CategoryCollisionError) as exc:
            raise_binding_vs_binding(DEST, [
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
        assert bound([entry("bindings.rw", name="home")]) == {DEST: "/h"}


# 🕯️ ``TestRow1SecretPathCarveOut`` DIED AT 6-R3. Both its cases drove the retired
# helper, and both claims are carried WHOLE by the two classes below, which drive the
# seam that decides them: the per-VAR override is
# ``TestSecretPathWinnersAreTheSeamsPerVarPick::
# test_the_box_pointer_beats_the_workset_one_for_the_same_VAR`` (in BOTH input orders,
# which the retired case did not do), and "the carve-out is per-VAR, not a blanket
# exemption" is ``TestSecretDestContentionMovedToTheSeam::
# test_a_bind_at_a_secret_dest_REFUSES_naming_both_declarations``. The D2 carve-out
# documentation itself moved with the pick — see ``secret_path_winners``' docstring.


class TestSecretPathWinnersAreTheSeamsPerVarPick:
    """T10b — the carve-out's SUCCESSOR: ``secret_path_winners`` (cutover 6-R1).

    The launch seam builds the per-VAR pick itself now, so that it survives the
    reconcile's retirement.  Spec §2a is the oracle, in its own words: *"a box
    ``secret_path.<VAR>`` overrides a workset's pointer for the same VAR"*.

    ⚑⚑ THE PICK IS SCOPE-DRIVEN, AND ONE INPUT ORDER CANNOT PROVE THAT.  With the
    box entry LAST, a naive "take the last" implements the same outcome; with it
    FIRST, "take the first" does.  So the SAME pair is driven BOTH ways below and
    the box pointer must win either way — that pair of cases is what makes the two
    obvious neuterings (``group[0]`` / ``group[-1]``) each redden one case.
    MUTATION-PROVEN, both directions.

    ⚑ WHAT IS NOT HERE, DELIBERATELY: a ``bindings.*`` aimed into the secrets dir
    (the sibling case above) is a CROSS-CATEGORY refusal the §0 table still owns —
    ``secret_path_winners`` answers WHICH POINTER WINS FOR A VAR and nothing else.
    """

    def _pair(self, *, box_first: bool):
        dest = f"{SECRET_MOUNT_DIR}/TOK"
        workset = entry("secret_path", name="TOK", scope="workset",
                        box_dest=dest, host_src="/workset/tok")
        box = entry("secret_path", name="TOK", scope="box",
                    box_dest=dest, host_src="/box/tok")
        return [box, workset] if box_first else [workset, box]

    @pytest.mark.parametrize("box_first", [False, True])
    def test_the_box_pointer_beats_the_workset_one_for_the_same_VAR(self, box_first):
        """Spec §2a, in both input orders — scope precedence, never arrival order."""
        winners = secret_path_winners(self._pair(box_first=box_first))
        assert [(e.name, e.host_src) for e in winners] == [("TOK", "/box/tok")]

    def test_a_second_VAR_at_the_LOSING_scope_survives_untouched(self):
        """The pick is PER VAR: overriding TOK must not take SECOND with it."""
        second = entry("secret_path", name="SECOND", scope="workset",
                       box_dest=f"{SECRET_MOUNT_DIR}/SECOND", host_src="/workset/second")
        winners = secret_path_winners([*self._pair(box_first=False), second])
        assert [(e.name, e.host_src) for e in winners] == [
            ("SECOND", "/workset/second"), ("TOK", "/box/tok"),
        ]

    def test_nothing_but_secret_path_reaches_the_pick(self):
        """Handed the WHOLE gated list at the seam, it takes only its own category."""
        winners = secret_path_winners([
            entry("bindings.rw", name="w", box_dest="/g/w"),
            entry("env", name="FOO", box_dest="FOO"),
            entry("seeded", name="s", box_dest="~/s"),
            *self._pair(box_first=False),
        ])
        assert [e.category for e in winners] == ["secret_path"]

    # 🕯️ ``test_it_matches_what_the_reconcile_yields`` DIED AT 6-R3 — a 6-R1 CANARY
    # whose oracle WAS the retired route. Rebasing it would assert the pick equals
    # itself. What it protected (that flipping ``_emit_secret_mounts`` onto the
    # carrier changed nothing a box receives) was its whole job and that flip has
    # landed; the SPEC oracle underneath survives above, in its own words.


class TestSecretDestContentionMovedToTheSeam:
    """T10c — §0's CROSS-CATEGORY answer for a secret dest, at its new home (6-R2).

    ``secret_path`` carries no arm in the disk-store shape (producer DESIGN §7.4),
    so the COLLAPSE never sees a secret and cannot answer "does anything else
    contend for this destination".  The only place that answered was the by-dest
    reconcile, retired at 6-R3 — so ``secret_path_deliveries`` answers it now, over
    the same entry list, with the same rows and the same messages.

    ⚑ THIS IS THE LIVE ANSWER, not a stand-in: since 6-R3 there is nothing above it
    at the seam, so what these cases drive is what a box meets.

    🔬 MEASURED AT 6-R2 on the LIVE seam, and BOTH outcomes are preserved, not
    invented: a ``bindings.rw`` at ``SECRET_MOUNT_DIR/TOK`` raised
    ``binding_vs_binding`` naming both keys; a ``masks`` at the same dest raised
    NOTHING, dropped the secret so the VAR was never delivered, and left a tmpfs at
    the dest, with no log line at any level.
    """

    DEST = f"{SECRET_MOUNT_DIR}/TOK"

    def _secret(self, **kw):
        return entry("secret_path", name="TOK", box_dest=self.DEST, **kw)

    def test_a_bind_at_a_secret_dest_REFUSES_naming_both_declarations(self):
        """Row 1 — and the message must carry BOTH participants, as it did."""
        entries = [
            entry("bindings.rw", name="sneaky", box_dest=self.DEST),
            self._secret(),
        ]
        with pytest.raises(CategoryCollisionError) as exc:
            secret_path_deliveries(entries)
        assert exc.value.kind == "binding_vs_binding"
        assert exc.value.box_dest == self.DEST
        # ⚑ The pin that a one-participant message would fail: BOTH keys, in the
        # retired route's order (the contending bind first, the suppression block's
        # subject), so the text a user is handed is unchanged.
        assert [k for k, _src in exc.value.entries] == [
            "box.bindings.rw.sneaky", "box.secret_path.TOK",
        ]

    # 🕯️ ``test_the_reconcile_and_the_seam_refuse_the_SAME_configuration`` DIED AT
    # 6-R3 — a 6-R2 canary whose oracle was the retired route. The sibling above
    # asserts the kind, the dest AND both participant keys directly, which is every
    # field the canary compared.

    def test_an_ABSTRACTION_onto_a_secret_dest_refuses_as_row_3(self):
        """Row 3 keeps its own message: the base survives, the extension is named."""
        entries = [self._secret(), entry("caches", name="c", box_dest=self.DEST)]
        with pytest.raises(CategoryCollisionError) as exc:
            secret_path_deliveries(entries)
        assert exc.value.kind == "extension_onto_occupied"

    def test_a_mask_at_a_secret_dest_takes_it_SILENTLY(self, caplog):
        """Row 2 — the measured outcome: no raise, no log, the VAR not delivered."""
        entries = [self._secret(), entry("masks", name="m", box_dest=self.DEST)]
        with caplog.at_level(logging.DEBUG):
            assert secret_path_deliveries(entries) == []
        assert caplog.records == []

    def test_a_mask_takes_ONLY_the_dest_it_names(self):
        """Per VAR, like the pick itself: masking TOK must not take SECOND."""
        second_dest = f"{SECRET_MOUNT_DIR}/SECOND"
        entries = [
            self._secret(),
            entry("masks", name="m", box_dest=self.DEST),
            entry("secret_path", name="SECOND", box_dest=second_dest),
        ]
        assert [e.name for e in secret_path_deliveries(entries)] == ["SECOND"]

    def test_a_mask_over_the_secrets_DIRECTORY_is_not_a_contender(self):
        """MEASURED: exact dest only — the secret mounts INSIDE the tmpfs, as before."""
        entries = [
            self._secret(),
            entry("masks", name="m", box_dest=SECRET_MOUNT_DIR),
        ]
        assert [e.name for e in secret_path_deliveries(entries)] == ["TOK"]

    # 🕯️ ``test_it_drops_exactly_what_the_reconcile_drops`` DIED AT 6-R3 — the other
    # 6-R2 canary. The DROP itself is asserted by the three mask cases above, each
    # against the measured outcome rather than against a second implementation.


class TestANarrowResolveEmitsOnlyItsOwnTable:
    """T10d — the narrow-resolve DISSOLUTION (cutover 6-R2), §0 at its new seam.

    A narrow resolve (the images / helper-hub tables) carries only its own injected
    table but resolves the user's whole CASCADE, so a user declaration reaches it.
    Emitting those rows is the D1 defect — the main path already emits every one of
    them from the collapse.  ``narrow_table_winners`` filters to the table's own
    dests, which DELETES the exposure rather than arbitrating it (P4).

    ⚑ At a dest that IS the table's, two rows still have to be decided, and a narrow
    resolve has nobody else to ask: the per-scope producer already raised the
    SAME-scope pair (it runs above the collapse's ``whole_box`` gate), and the
    CROSS-scope pair is the collapse's, which returns early here.
    """

    TABLE = "/home/agent/.kanibako/state/helper.sock"
    DESTS = frozenset({TABLE})

    def _table_row(self):
        return entry("bindings.rw", name="helper_sock", box_dest=self.TABLE)

    def test_a_user_row_at_ANOTHER_dest_is_dropped(self):
        """The dissolution itself: only the table's dests survive."""
        winners = narrow_table_winners(
            [self._table_row(), entry("bindings.ro", name="u", box_dest="/g/user")],
            self.DESTS,
        )
        assert [e.box_dest for e in winners] == [self.TABLE]

    def test_a_COPY_at_a_table_dest_is_not_a_mount_and_never_emits(self):
        """Only MOUNT deliveries reach an emitter; a ``seeded`` is a copy."""
        winners = narrow_table_winners(
            [entry("seeded", name="s", box_dest=self.TABLE)], self.DESTS,
        )
        assert winners == []

    def test_TWO_rows_at_a_table_dest_REFUSE(self):
        """The plan's own counter-example: a bare filter would pick by INSERTION ORDER.

        A user ``workset.bindings.rw`` at an internal dest and the table's own row
        are two mounts at one destination — an error in every scope combination.
        RED if the refusal is dropped: the map silently keeps whichever went in last.
        """
        rows = [
            self._table_row(),
            entry("bindings.rw", name="mine", scope="workset", box_dest=self.TABLE),
        ]
        with pytest.raises(CategoryCollisionError) as exc:
            narrow_table_winners(rows, self.DESTS)
        assert exc.value.kind == "binding_vs_binding"
        assert exc.value.box_dest == self.TABLE

    def test_an_ABSTRACTION_onto_a_table_dest_refuses_as_row_3(self):
        rows = [self._table_row(), entry("common", name="c", box_dest=self.TABLE)]
        with pytest.raises(CategoryCollisionError) as exc:
            narrow_table_winners(rows, self.DESTS)
        assert exc.value.kind == "extension_onto_occupied"

    def test_a_MASK_at_a_table_dest_OVERRIDES_it_rather_than_refusing(self):
        """§0 row 2 is not suspended here — a mask is the INVERSE of a bind.

        ⚑ This is the case a blanket ">1 row is an error" would have broken, and it
        is the shipped outcome: a mask at a helper dest suppressed that bind under
        the flat authority ladder too.
        """
        rows = [self._table_row(), entry("masks", name="m", box_dest=self.TABLE)]
        winners = narrow_table_winners(rows, self.DESTS)
        assert [e.category for e in winners] == ["masks"]


# --------------------------------------------------------------------------- #
# Row 2 — masks OVERRIDE                                                       #
# --------------------------------------------------------------------------- #


# 🕯️ ``TestRow2MaskOverrides`` DIED AT 6-R3, with the cross-scope helper it drove.
# Row 2 is the COLLAPSE's: a mask SWEEPS what it covers when it folds last, and the
# fold refuses a bind arriving INSIDE an existing mask. Both are pinned where the
# collapse is — ``tests/test_settings/test_store_collapse.py`` — and their wiring
# into a launch, including the mask arm reading the SAME map as the mount arm, is
# ``tests/test_commands/test_start_assembly.py::TestTheMaskArm``. Row 2 at a SECRET
# dest is the one arm the collapse cannot see, and it stays here
# (``TestSecretDestContentionMovedToTheSeam::test_a_mask_at_a_secret_dest_takes_it_SILENTLY``);
# row 2 at a NARROW table's dest likewise
# (``TestANarrowResolveEmitsOnlyItsOwnTable::test_a_MASK_at_a_table_dest_OVERRIDES_it_rather_than_refusing``).


# --------------------------------------------------------------------------- #
# Row 3 — an abstraction EXTENDING onto an occupied dest                       #
# --------------------------------------------------------------------------- #


class TestRow3ExtensionOntoOccupied:
    """T4/T5 — ERROR refusing the EXTENSION; the explicit binding is the BASE.

    ⚑ THE DECISION IS THE PRODUCER'S (row 3 is decidable inside ONE scope), so the
    first case drives it with a SAME-SCOPE pair. The DIRECTION and the MESSAGE are
    the RAISER's single-sourced text, so the rest drive the raiser and keep the
    cross-scope key spellings a real message carries. ⚑ Recomposed at 6-R3; the
    retired helper used to do both halves.
    """

    @pytest.mark.parametrize("abstract", ["common", "caches"])
    def test_abstraction_onto_a_binding_raises(self, abstract):
        with pytest.raises(CategoryCollisionError) as exc:
            build_store_shape_set([
                entry("bindings.rw", name="claude_plugins", host_src="/base"),
                entry(abstract, name="plugins", host_src="/ext"),
            ])
        assert exc.value.kind == "extension_onto_occupied"

    @pytest.mark.parametrize("abstract", ["common", "caches"])
    def test_the_refused_side_is_the_EXTENSION_not_the_base(self, abstract):
        """T5's direction assertion — "I wrote it down literally" wins.

        A test that only asserted "something raised" would stay green if the
        rule refused the BASE instead, which is the opposite of §0.
        """
        with pytest.raises(CategoryCollisionError) as exc:
            raise_extension_onto_occupied(
                DEST,
                extension=entry(abstract, name="plugins", scope="agent.claude",
                                host_src="/ext"),
                base=entry("bindings.rw", name="claude_plugins",
                           host_src="/base"),
            )
        # entries[0] is the refused EXTENSION, entries[1] the surviving BASE.
        assert exc.value.entries[0][0] == f"agent.claude.{abstract}.plugins"
        assert exc.value.entries[1][0] == "box.bindings.rw.claude_plugins"
        text = str(exc.value)
        assert text.startswith(f"'agent.claude.{abstract}.plugins' extends onto")
        assert "already binds" in text
        assert "the derived\nextension is refused" in text

    def test_message_carries_the_rule_changed_paragraph_and_the_remedy(self):
        with pytest.raises(CategoryCollisionError) as exc:
            raise_extension_onto_occupied(
                DEST,
                extension=entry("common", name="plugins", scope="agent.claude"),
                base=entry("bindings.rw", name="claude_plugins"),
            )
        text = str(exc.value)
        assert "THIS RULE CHANGED IN kanibako 1.8.0" in text
        assert "SUPPRESS" in text
        assert "claude_plugins: null" in text

    def test_an_abstraction_alone_at_a_dest_is_fine(self):
        # ⚑ The ``rec.warnings == ()`` line here was RETIRED at 5-1c and was
        # vacuous before it: one declaration at one dest meets no row at all.
        assert bound([entry("common", name="plugins")]) == {DEST: "/h"}


# --------------------------------------------------------------------------- #
# Rows 4/5 — abstraction vs abstraction                                        #
# --------------------------------------------------------------------------- #


# 🕯️ ``TestRow4CrossScopeIsSilent``'s TWO CROSS-SCOPE CASES DIED AT 6-R3, and their
# outcome had already stopped being reachable from a launch: row 4's silent pick was
# the retired helper's, while the COLLAPSE refuses two binds at one dest whatever
# their scopes (``store_collapse._refuse_bind_over_bind``) — so a real box never got
# the pick, it failed to assemble. With the helper gone the outcome exists nowhere and
# there is nothing left to assert. The refusal that replaced it is pinned in
# ``tests/test_settings/test_store_collapse.py``; that a cross-scope abstraction pair
# produces NO WARNING is pinned in
# ``test_store_shape.py::TestPerScope::test_a_cross_scope_abstraction_pair_is_left_whole``.
#
# ⚑ ONE CASE SURVIVES, and it was never a cross-scope case in the collapse's terms:
# D5 below, whose two entries carry the ONE bare ``agent`` token and so fold into a
# single scope's shape. It drove ``build_store_shape_set`` already.


class TestTheAgentTierIsONEScope:
    """D5 — ``agent.default`` and ``agent.<active>`` count as ONE scope, and WARN."""

    def test_agent_default_and_active_count_as_ONE_scope(self):
        """D5 — pinned EITHER WAY so the choice is visible and mutable.

        §2a lists ``agent.default`` and ``agent.<active>`` as separate scopes,
        which would make this a row-4 (silent) case. The code carries ONE bare
        ``agent`` precedence token, and two entries surviving the active-over-
        default pick at one dest are two different NAMES in one effective agent
        view — an ambiguity the user must resolve. So P5 treats the whole agent
        tier as ONE scope and WARNS, which is the LOUD direction. If Jei rules
        the other way, this assertion is the single place that flips.

        ⚑ The collapse reads it the same way, and that is why this case outlived
        the cross-scope ones beside it: both entries carry the one ``agent`` scope
        token, so they fold into a single scope's shape and never meet
        ``_refuse_bind_over_bind``. Nothing refuses this arrangement, and the
        warning below stays the only announcement the user gets.

        ⚑ RETARGETED AT 5-1c to the arm that now builds the warning. The
        DECISION is unchanged and this is still the ONE place it flips — but it
        is asked of ``build_store_shape_set``, because that is what a user hears
        from. ``test_store_shape.py`` pins the bare-token FOLD
        (``test_the_agent_scope_folds_under_its_bare_token``) and never this: two
        agent-tier declarations at ONE dest warning as ONE scope is pinned only
        here, so retiring the case would drop D5 entirely.
        """
        from kanibako.settings.store_shape import build_store_shape_set

        entries = [
            entry("common", name="a", scope="agent.default", host_src="/def"),
            entry("caches", name="b", scope="agent.claude", host_src="/act"),
        ]
        produced = build_store_shape_set(entries)
        assert len(produced.warnings) == 1
        assert produced.warnings[0].scope == "agent"
        assert set(produced.warnings[0].loser_keys) == {"agent.default.common.a"}
        # …and the PICK proceeds: the active slot's row is what the arm carries.
        assert produced["agent"].rw[DEST].src == "/act"


# 🕯️ ``TestRow5SameScopeProceedsOnTheExistingOrdering`` DIED AT 6-R3. It held the
# PROCEED half of row 5 for the retired helper; the WARN half had already moved to the
# per-scope producer at 5-1c, and the PROCEED half moved with it. Both are pinned
# together, where one function decides them:
# ``tests/test_settings/test_store_shape.py::TestWithinScopeRows::
# test_row5_two_abstractions_at_one_dest_warn_and_the_last_wins`` (the winner AND the
# warning's fields) and ``::test_row5_warns_every_launch_and_never_raises`` (purity
# and the message text).
#
# Its second case — ``test_a_lower_scopes_own_ambiguity_still_resolves_to_ONE_mount``
# — was a ROW 4 shape and dies for the same reason row 4 does: the helper's pick over
# three binds at one dest across two scopes, an arrangement the collapse refuses
# outright, so no box could reach the outcome it asserted. ⚑ Its retired
# ``rec.warnings == ()`` half must still NOT be retargeted at the producer: the
# producer folds ``system`` ALONE, sees two abstractions there and DOES warn — one of
# the two MEASURED divergences recorded at cutover 5-0.


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
    """CUTOVER 5-0/5-1c — the row-5 warning has a home in the NEW route, and ONLY one.

    5-0 wired ``emit_collision_warnings(shapes.warnings)`` into
    ``_install_assembly_collapse`` beside the reconcile's own feed; 5-1c then deleted
    that feed and the ``ReconciledCategories.warnings`` field behind it. These cases
    say what has to be true for that to have been a deletion rather than a regression:
    the collapse route announces the ambiguity BY ITSELF, there is no second feed left
    to announce it again, and the case 5-0 newly made audible still is.

    ⚑ Each drives ``_install_assembly_collapse`` itself, not the producer beneath it:
    what is under test is the WIRING, so a test that called ``build_store_shape_set``
    and emitted its warnings by hand would stay green with the wiring ripped out.

    ⚑ ``test_BOTH_feeds_live_still_produce_EXACTLY_ONE_line`` was RETIRED HERE at 5-1c,
    and deliberately not replaced by a green-but-vacuous version of itself: its subject
    was two live feeds, and there are not two. It was the SAFETY PROOF FOR THIS
    DELETION and its job is done. What replaced it is
    ``test_there_is_NO_SECOND_FEED_left_to_add`` below — the same guarantee stated
    structurally instead of by coincidence. (Two feeds printed one line only because
    both arms happened to build an EQUAL ``CategoryCollision`` and the emitter memoises
    on ``(box_dest, scope)``; that was a property of the two constructions, never of
    the channel.)
    """

    @pytest.fixture(autouse=True)
    def _clean(self):
        """The memo is PROCESS state — reset it around each case, both sides."""
        from kanibako.commands.start import reset_collision_warnings

        reset_collision_warnings()
        yield
        reset_collision_warnings()

    def _ambiguous(self):
        """One same-scope abstraction ambiguity at ``DEST``, driven NARROW.

        The callers pass ``whole_box=False`` explicitly, and that is enough: the
        shapes are built, and the warnings with them, ABOVE the gate — a warning is a
        property of what was DECLARED, not of whether an assembly follows.
        ⚑ The gate has been ``whole_box`` itself since cutover 6-H; it never reads the
        entry list for a home bind, because home is no longer a declaration at all.
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

    # 🕯️ ``test_there_is_NO_SECOND_FEED_left_to_add`` DIED AT 6-R3, WITH THE
    # DATACLASS IT INSPECTED. It asserted that ``ReconciledCategories`` had no
    # ``warnings`` field and exactly three — making the second feed UNAVAILABLE
    # rather than merely unused (P3). The whole class is deleted now, which is the
    # same guarantee one level stronger and unrepresentable as a field check. The
    # structural guard moved to ``test_the_retired_routes_are_GONE`` at the foot of
    # this file, which asserts the symbols themselves are absent.

    def test_a_MASKED_destinations_ambiguity_is_announced_where_it_once_was_not(
        self, caplog,
    ):
        """The one place 5-0 changed what a user sees, pinned (CHANGELOG, Unreleased).

        A ``masks`` entry at the dest made the RECONCILE silent about the pair beneath
        it (§0 row 2 returned the mask and no warnings, from any scope), so before 5-0
        this launch printed nothing and still worked. The producer folds each scope
        alone, so it reports the pair — and the pair is still ambiguous, mask or no
        mask.

        ⚑ 5-1c dropped this case's reconcile scaffolding. It used to assert
        ``reconciled.warnings == ()`` first, to show WHICH arm was the silent one;
        with the reconcile channel gone that assertion has no subject, and the claim
        it set up — that the line comes from the producer — is what the surviving body
        drives directly.

        MUTATION ANCHOR: filter ``shapes.warnings`` down to what a mask leaves
        unshadowed before emitting, and this fails on ``assert 0 == 1`` while
        ``test_the_collapse_route_ALONE_announces_the_ambiguity`` stays green — which
        is exactly the shape of edit this exists to catch.
        """
        from kanibako.commands.start import _install_assembly_collapse
        from kanibako.settings.keystore import KeyStore

        entries = [*self._ambiguous(), entry("masks", name="hide", scope="box")]

        with caplog.at_level(logging.WARNING):
            _install_assembly_collapse(KeyStore(), entries, whole_box=False)

        assert len(self._lines(caplog)) == 1


# --------------------------------------------------------------------------- #
# The behaviours the table does not mention and which must survive it          #
# --------------------------------------------------------------------------- #


class TestPreservedCopyRules:
    """The copy layer — not in the §0 table, and it outlived the table's helper.

    🕯️ THE CROSS-DELIVERY HALF OF THIS CLASS DIED AT 6-R3, and not merely because
    the helper did: the ladder it pinned was RULED THE OTHER WAY (Jei, 2026-08-12 —
    *"don't check for sync. Let it clobber whatever it wants."*). ``synced`` no
    longer REPLACES a ``seeded`` at one dest, a ``synced`` winner is not picked by
    scope precedence, and nothing prunes a copy for sharing a destination with a
    mount. The surviving rule is the collapse's and is stated where it lives:

    * ``tests/test_settings/test_store_collapse.py::TestNothingPrunesACopy`` — a
      copy at a mount's dest keeps BOTH, at every scope;
    * ``::TestTheSyncArmIsAPlainConcatenation`` — the sync arm arbitrates nothing:
      every row survives in scope order, which is the direct contradiction of the
      retired ``_resolve_copy_group`` pick these cases asserted.

    ⚑ Retiring ``test_the_synced_winner_is_the_MOST_SPECIFIC_scope`` and
    ``test_the_synced_winner_beats_a_seeded_at_the_same_dest`` is the point, not a
    loss: rebasing them would have pinned an outcome the ruling deleted.
    """

    def test_pure_seeded_group_keeps_every_layer(self):
        """T11 — the template trio. Copies OVERLAY; they do not shadow.

        ⚑ RECOMPOSED ONTO THE COLLAPSE's seed arm (6-R3), which is what a box is
        seeded from. Same claim, and now against the route that performs it.
        """
        from kanibako.settings.store_collapse import collapse_seeded

        # ⚑ A dest INSIDE home: seeds apply to the home bind alone, so the collapse
        # refuses one outside it (``_refuse_seed_outside_home``). The retired helper
        # had no home to answer against and took any dest.
        dest = "/home/agent/template"
        collapsed = collapse_seeded(build_store_shape_set([
            entry("seeded", name=dest, box_dest=dest, scope="system",
                  host_src="/base"),
            entry("seeded", name=dest, box_dest=dest, scope="agent.claude",
                  host_src="/ag"),
            entry("seeded", name=dest, box_dest=dest, scope="workset",
                  host_src="/ws"),
        ]))
        assert [c.src for c in collapsed] == ["/base", "/ag", "/ws"]

    def test_env_never_participates_in_a_dest_collision(self):
        """An env VAR name equal to a path dest is not a destination at all.

        🛑 THE CLAIM IS NOW ABOUT THE SLOT SPACES, NOT ABOUT TWO CARRIERS. It used
        to read "``env`` leaves on ``LaunchDeliveries.envs`` and the bind leaves on
        the collapse, so the clash cannot be expressed" — and the carrier half of
        that is gone: BOTH now fold in the collapse, off the ONE entry list. What
        keeps them apart is what always did, and it survives the merge intact: the
        arbitrated spaces are DIFFERENT. A bind is arbitrated by DESTINATION and an
        env var by VARIABLE NAME, so ``PATH`` the variable and ``PATH`` the
        destination are two slots that share a spelling and nothing else.

        ⚑ The mutation this kills: an env row folded into a bind arm (or a bind row
        into the env slots) makes the two contend, and one of them then loses or
        refuses on a name collision that means nothing.
        """
        from kanibako.settings.store_collapse import collapse_env

        entries = [
            entry("env", name="PATH", box_dest="PATH"),
            entry("bindings.rw", name="home", box_dest="PATH"),
        ]
        # One entry list, two folds, neither aware of the other's ``PATH``.
        slots = collapse_env(entries)
        # The VARIABLE slot, and it came from the env KEY — one row, not two.
        assert list(slots) == ["PATH"]
        assert slots["PATH"].key.endswith("env.PATH")
        # The DESTINATION, undisturbed by a variable that shares its spelling.
        assert bound(entries) == {"PATH": "/h"}


class TestCredentialGateRunsFirst:
    """T12 — the gate runs ABOVE every consumer, so a suppressed cred cannot error.

    ⚑ CUTOVER STEP 4 MOVED IT OUT: delivery policy belongs to the launch seam
    (``commands.start._resolve_launch_snapshot``), above every consumer of the entry
    list, and no consumer applies a gate of its own. These cases drive the
    PRODUCTION COMPOSITION — ``build_store_shape_set(gate_credential_delivery(...))``
    — which is what makes "gate first, fold second" true BY CONSTRUCTION.

    ⚑ RE-POINTED AT THE PRODUCER at 6-R3 (it was the retired helper). The LIVE
    composition, end to end through a real resolve, is pinned in
    ``tests/test_commands/test_start_assembly.py::TestTheCredentialGateReachesTheCollapse``,
    which carries the MEASURED mutation showing exactly which pins redden.
    """

    @staticmethod
    def _copies(entries):
        shape = build_store_shape_set(entries)["box"]
        return shape.seed, shape.sync

    def test_private_box_does_not_error_on_a_suppressed_synced(self):
        entries = gate_credential_delivery(
            [entry("synced", name="creds"), entry("bindings.rw", name="home")],
            False,
        )
        seed, sync = self._copies(entries)
        assert sync == []
        assert bound(entries) == {DEST: "/h"}

    def test_private_box_drops_a_credential_seed_but_keeps_a_plain_one(self):
        seed, _sync = self._copies(gate_credential_delivery(
            [
                entry("seeded", name="creds", box_dest="/g/cred",
                      is_credential=True),
                entry("seeded", name="tpl", box_dest="/g/tpl"),
            ],
            False,
        ))
        assert [c.dest for c in seed] == ["/g/tpl"]

    def test_the_producer_does_not_gate_credentials_the_gate_is_the_callers(self):
        """UNGATED in ⇒ credentials OUT — no consumer has a delivery policy left.

        The pin for cutover step 4's whole point: hand the producer the very entries
        a PRIVATE box must not receive, WITHOUT the gate, and they survive. Any
        internal re-gate reddens this — which is the guard, because a second
        application of the rule is how two launch consumers come to describe
        differently private boxes.
        """
        seed, sync = self._copies([
            entry("synced", name="creds", box_dest="/g/sync"),
            entry("seeded", name="credseed", box_dest="/g/seed",
                  is_credential=True),
        ])
        assert [c.dest for c in sync] == ["/g/sync"]
        assert [c.dest for c in seed] == ["/g/seed"]


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
        names it, so hiding it would defeat the point.

        ⚑ The "did it actually lose?" precondition used to be read off a warnings
        field; 5-1c retired that field, so it is read off the WINNER instead — which
        says the same thing more directly. ⚑ The winner comes from the PRODUCER
        since 6-R3, which is what decides a same-scope pair.
        """
        entries = [
            entry("caches", name="build", scope="box", host_src="/loser"),
            entry("common", name="buildcache", scope="box", host_src="/winner"),
        ]
        # The loser did lose: one bind occupies the dest and it is the other one.
        assert bound(entries) == {DEST: "/winner"}
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


def test_the_retired_routes_are_GONE():
    """🛑 CUTOVER 6-R3, STATED STRUCTURALLY — the same reasoning as the ladder above.

    The single cross-scope pass and its result type are DELETED, not merely unused.
    Two implementations of one table print one answer only for as long as they stay
    byte-equal, and this file spent three cutover steps carrying canaries to prove
    they did. Making the second one UNAVAILABLE is what closes that (P3): re-adding
    it means re-adding a module-level function and a dataclass, which is a visible
    design act rather than a one-line edit.

    ⚑ THIS REPLACES ``test_there_is_NO_SECOND_FEED_left_to_add``, which asserted the
    absence of ONE FIELD on the class this asserts the absence of.

    MUTATION ANCHOR: restore either symbol and this fails, naming it.
    """
    from kanibako.settings import settings_categories

    for name in (
        "reconcile_categories", "ReconciledCategories",
        "_resolve_dest_group", "_resolve_mount_group", "_resolve_copy_group",
        "_DISABLE_SENTINEL",
    ):
        assert not hasattr(settings_categories, name), name


class TestRemedyTextIsHonestAboutWhatItCanKnow:
    """The remedy is the non-obvious part (§0), so it must not overstate itself.

    ⚑ EVERY CASE DRIVES A RAISER (6-R3). The text is written ONCE, in the two public
    raisers, and read by three callers each; asking a caller for it would pin the
    caller\'s route rather than the sentence, and any of the three would do equally
    badly. The retired helper was simply the caller these used to reach it through.
    """

    def test_row1_labels_the_yaml_block_as_a_choice(self):
        """Row 1 has two PEERS — the resolver cannot know which one the user
        wants to keep, so prescribing one would be a guess dressed as advice."""
        with pytest.raises(CategoryCollisionError) as exc:
            raise_binding_vs_binding(DEST, [
                entry("bindings.ro", name="vault", scope="system"),
                entry("bindings.rw", name="vault", scope="box"),
            ])
        text = str(exc.value)
        assert "Either entry may be the one you keep" in text
        assert "use whichever key you do NOT want" in text

    def test_row3_does_not_label_it_a_choice(self):
        """Row 3's occupant is DETERMINED — the base always survives."""
        with pytest.raises(CategoryCollisionError) as exc:
            raise_extension_onto_occupied(
                DEST,
                extension=entry("common", name="plugins", scope="agent.claude"),
                base=entry("bindings.rw", name="home", scope="box"),
            )
        assert "Either entry may be the one you keep" not in str(exc.value)

    def test_an_agent_scope_occupant_names_the_per_agent_file_spelling(self):
        """The per-agent file has NO node level: its root ``self:`` IS ``agent.<node>``,
        so the table is spelled ``self.bindings.ro``. The canonical ``agent.<node>`` form
        is what a CONTAINING scope's file writes. Printing one without the other hands
        the reader an edit that silently no-ops.

        ⚑ The node LEFT the spelling with the S2 flatten ([spec:15-21, "self"])
        — a nested ``self.claude.bindings`` is now refused by name, so a caveat still printing it
        would be teaching the one shape the launch rejects."""
        with pytest.raises(CategoryCollisionError) as exc:
            raise_binding_vs_binding(DEST, [
                entry("bindings.ro", name="a", scope="agent.claude"),
                entry("bindings.rw", name="b", scope="agent.claude"),
            ])
        text = str(exc.value)
        assert "self.bindings.ro" in text
        assert "self.claude" not in text
        assert "agent:\n  claude:\n    bindings:\n      ro:\n        a: null" in text

    def test_a_box_scope_occupant_gets_no_agent_caveat(self):
        with pytest.raises(CategoryCollisionError) as exc:
            raise_binding_vs_binding(DEST, [
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
            raise_binding_vs_binding(dest, [
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
            raise_extension_onto_occupied(
                dest,
                extension=entry("caches", name=dest, box_dest=dest,
                                scope="agent.claude", host_src="/ext"),
                base=entry("bindings.rw", name=dest, box_dest=dest, scope="box",
                           host_src="/var/cache/uv"),
            )
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

    def test_a_pref_installed_env_key_is_named_in_the_REALIZED_TWIN_refusal(
        self, tmp_path,
    ):
        """MBR-1 P4c-2: the realization refusal names a key a ``pref`` can install.

        ⚑ ``pref.agent.<agent>.env.<VAR>`` is a legal request and its target IS the
        entry key (``pref_entry_keys`` — ``<VAR>`` is a key SEGMENT, not a dest), so
        a user whose only file says ``pref.agent.goose.env.GOOSE_MODEL`` would
        otherwise be told to delete ``agent.goose.env.GOOSE_MODEL``, which appears in
        none of their files. This is the TEXT arm: the refusal is a plain
        ``SettingsError`` with no structured participants, matched on its message.
        """
        from pathlib import Path

        from kanibako.commands.start import (
            _annotate_pref_origin,
            _refuse_realized_twin,
        )
        from kanibako.settings.settings_prefs import PrefRequest
        from kanibako.settings.settings_resolve import SettingsError

        box = Path(tmp_path) / "box.yaml"
        with pytest.raises(SettingsError) as excinfo:
            _refuse_realized_twin(
                "GOOSE_MODEL", "agent.goose.env.GOOSE_MODEL",
                agent_id="goose", driving_key="model", is_access=False,
            )
        prefs = [PrefRequest(
            target="agent.goose.env.GOOSE_MODEL", value="mine",
            level="box", source=box,
        )]
        text = str(_annotate_pref_origin(excinfo.value, prefs))
        assert "box settings file" in text
        assert str(box) in text

    def test_an_unrelated_pref_does_not_claim_the_REALIZED_TWIN_refusal(
        self, tmp_path,
    ):
        """The NEGATIVE CONTROL: matching is on the KEY, never on "a pref exists"."""
        from pathlib import Path

        from kanibako.commands.start import (
            _annotate_pref_origin,
            _refuse_realized_twin,
        )
        from kanibako.settings.settings_prefs import PrefRequest
        from kanibako.settings.settings_resolve import SettingsError

        with pytest.raises(SettingsError) as excinfo:
            _refuse_realized_twin(
                "GOOSE_MODEL", "agent.goose.env.GOOSE_MODEL",
                agent_id="goose", driving_key="model", is_access=False,
            )
        prefs = [PrefRequest(
            target="agent.goose.env.EDITOR", value="vim",
            level="box", source=Path(tmp_path) / "box.yaml",
        )]
        assert _annotate_pref_origin(excinfo.value, prefs) is excinfo.value

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


class TestThePrefOriginReachesTheLIVEPATH:
  """🛑 CUTOVER 6-R3 — the enrichment on the REAL resolve, not on the function.

  Every case above hands ``_annotate_pref_origin`` an exception it built itself, so
  all of them stay green with the CALL SITE deleted. That was tolerable while the
  retired by-dest reconcile raised FIRST on the live path and its ``try`` was the
  only one there was: deleting it moved the refusal into three OTHER callees, and a
  wrap left around the wrong one downgrades every pref-caused collision message
  silently — no test would have noticed.

  So this one drives ``kanibako start``'s own resolve end to end: a ``pref`` in a
  BOX settings file installs a declaration that collides with the box's own, and the
  error a user would receive must name the request.

  🛑 MUTATION ANCHOR, PROVED: delete the ``except (CategoryCollisionError,
  SettingsError)`` wrap from ``_resolve_launch_snapshot`` and this fails on the
  ``was installed by`` assertion while every case above stays green.

  ⚑ SAME SCOPE on purpose. Both declarations land in ``agent``, so the PER-SCOPE
  PRODUCER raises the §0 row-3 refusal with its participants STRUCTURED — which is
  the arm that carries the declaration keys the enrichment matches against. A
  cross-scope pair reaches the COLLAPSE instead, whose message structurally cannot
  name a declaration key (``store_collapse._refuse_bind_over_bind``'s own docstring
  says so), so it would pin nothing about this wire.

  ⚑ AND THE AGENT TIER IS WHERE A ``pref`` CAN AIM: §2h's allowlist admits only
  ``system.agent`` and ``agent.<agent>.<key>``, so ``pref.agent.claude.common`` is
  the request, and the occupant it extends onto is declared at the same tier.
  """

  DEST = "~/prefcollide"

  def test_a_pref_installed_collision_NAMES_THE_REQUEST_on_a_real_resolve(
    self, std, config, project_dir, tmp_path,
  ):
    import yaml

    from kanibako.commands.start import _resolve_launch_snapshot
    from kanibako.errors import CategoryCollisionError
    from kanibako.settings.paths import resolve_project
    from kanibako.targets.no_agent import NoAgentTarget

    src = tmp_path / "collide"
    src.mkdir()
    proj = resolve_project(std, config, str(project_dir), initialize=True)
    proj.metadata_path.mkdir(parents=True, exist_ok=True)
    (proj.metadata_path / "box.yaml").write_text(yaml.safe_dump({
      "pref": {"agent": {"claude": {"common": {self.DEST: [str(src)]}}}},
    }))

    with pytest.raises(CategoryCollisionError) as exc:
      _resolve_launch_snapshot(
        std=std, proj=proj, agent_name="claude",
        system_settings_path=None, agent_cfg_path=None,
        desc=None, install=None, target=NoAgentTarget(), agent_cfg=None,
        deliver_creds=True,
        # The OCCUPANT, at the same tier: an explicit agent-scope binding the
        # pref-installed ``common`` then extends onto.
        extra_default_categories={
          "agent.claude.bindings.rw": {self.DEST: (str(src),)},
        },
      )

    text = str(exc.value)
    # The ENTRY key the user cannot write, and the REQUEST they can.
    assert "was installed by" in text, text
    assert "'pref.agent.claude.common'" in text, text
    assert "edit or remove that request" in text, text
    # The structured fields survive the enrichment, as the pure-function cases pin.
    assert exc.value.kind == "extension_onto_occupied"

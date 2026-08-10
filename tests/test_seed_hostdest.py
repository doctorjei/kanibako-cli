"""Where a COPY lands, the OPTIONAL bind, and the handbook binds.

WHY THIS MODULE EXISTS
----------------------
Every test here pins a failure that nothing else would catch, and each is about
WHERE a delivery lands or WHETHER it is emitted at all:

1. **The ``/home/agent`` host-home collision, and the ONE translator that closes
   it.**  On a host whose user home IS ``/home/agent`` a HOST store path and a
   GUEST box path are textually indistinguishable, so a host-spelled dest fed to
   the guest translator is re-rooted under the box home — content written
   somewhere nothing reads, and the copy REPORTS SUCCESS.  ⚑ The dev box and the
   seadog LXC test envs are exactly such hosts; bifrost (``kanibako``) is not, so
   the two environments we test in disagree about which failure mode appears.
   That is the worst possible property for a bug, and it is why the two halves
   below are pinned from opposite sides.
2. **Reconcile at ONE destination.**  With one dest space, a copy and a mount
   that share a dest string genuinely ARE one dest, so they meet in
   ``_resolve_dest_group`` and the cross-delivery ladder decides between them.
3. **Skip-if-absent.**  Without it, every box with no workset/box chapter — i.e.
   almost every box — prints two ro-drop warnings on every launch, which is the noise
   that teaches users to ignore warnings.
4. **The handbook depth order and the agent-tier fallback**, both of which fail
   INVISIBLY: a depth-sort regression mounts the chapters before their parents, and a
   wrong agent tier means ``agent.default`` never fires.

⚑⚑ ONE DEST SPACE, TWO DELIVERIES (spec §0; 2026-08-08c).  EVERY category
destination is GUEST-spelled now, copies included, and the seed dest was
respelled from the absolute host path ``@meta.box.path/home`` to the guest
``~/``.  So the collision in §1 is closed AT THE SOURCE, and the three things
that used to discriminate the two spaces — ``CategoryEntry.dest_space``,
``snapshot_category_entries(host_dest_keys=…)`` and ``templates.seed_keys_of`` —
are DELETED.  Do not reintroduce them under any name; a host-spelled category
dest would be a guest path taken for a host one, which is exactly the bug above.

⚑ WHAT STILL CARRIES A HOST DEST.  Exactly one thing, and it is NOT a category
dest: ``@box.canon/handbook``, the HOST template location nothing in the box
reads (§5C-RULING), copied by ``launch.templates.install_box_handbook_template``
through ``start._host_copy_dest``'s containment guard.  §1 pins that guard from
the host side and the seed's route through ``container._guest_dest_to_host``
from the guest side; between them no copy on either path can land off-target.

⚑ THE FILE NAME IS STALE — ``hostdest`` no longer names the subject.  The rename
is boarded separately; do not infer from the name that a host dest is still a
category concern.

Host-side only; no podman.  The physical mount is the e2e's job.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kanibako.settings import core_defaults
from kanibako.settings.settings_categories import (
    CategoryEntry,
    reconcile_categories,
)
from kanibako.settings.settings_launch import build_launch_snapshot, snapshot_category_entries
from kanibako.settings.settings_resolve import GUEST_HOME, ResolveCtx


def _ctx() -> ResolveCtx:
    return ResolveCtx(
        agent_name="claude",
        workset_name=None,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


# ===========================================================================
# 1. Where a COPY lands: the host-template guard, and the ONE guest translator.
# ===========================================================================


class TestHostCopyDest:
    """``commands.start._host_copy_dest`` — the host arm and its §2a containment."""

    def test_a_host_dest_under_slash_home_agent_is_NOT_mapped_into_the_box_home(
        self, tmp_path,
    ):
        """⚑⚑ THE COLLISION, pinned.

        A box store whose path begins with ``/home/agent/`` (the dev box, a seadog
        LXC) produces host dests that START WITH THE GUEST HOME PREFIX.  The guest
        translator would map such a dest to ``shell_path/<remainder>`` — inside the
        box home — and report success.  The host arm must return the path VERBATIM.
        """
        from kanibako.commands.start import _host_copy_dest

        # A box store spelled exactly like the collision case.
        box_root = Path(GUEST_HOME) / ".local/share/kanibako/boxes/demo"
        dest = _host_copy_dest(
            str(box_root / "canon" / "handbook"), box_root,
            label="handbook template", name="@box.canon/handbook",
            logger=logging.getLogger("t"),
        )
        assert dest == box_root / "canon" / "handbook"
        # Specifically: NOT re-rooted anywhere, and still under the box STORE.
        assert str(dest).startswith(str(box_root))

    def test_a_dest_outside_the_box_store_is_refused(self, tmp_path, caplog):
        """§2a enforcement point 2: the ``..`` escape.  A mis-declared dest costs its
        own seed, not the box's other seeds — hence a warning + None, not a raise."""
        from kanibako.commands.start import _host_copy_dest

        box_root = tmp_path / "boxes" / "demo"
        box_root.mkdir(parents=True)
        with caplog.at_level(logging.WARNING):
            dest = _host_copy_dest(
                str(tmp_path / "elsewhere"), box_root,
                label="seed", name="handbook", logger=logging.getLogger("t"),
            )
        assert dest is None
        assert "outside the box store" in caplog.text

    def test_the_box_store_root_itself_is_contained(self, tmp_path):
        from kanibako.commands.start import _host_copy_dest

        box_root = tmp_path / "boxes" / "demo"
        box_root.mkdir(parents=True)
        assert _host_copy_dest(
            str(box_root), box_root,
            label="seed", name="x", logger=logging.getLogger("t"),
        ) == box_root


class TestSeedRoutesThroughTheOneGuestTranslator:
    """End-to-end through the REAL seed seam: the §2a seed dests are GUEST-spelled
    and go THROUGH ``container._guest_dest_to_host``, which is the ONE thing that
    decides where a seed lands.

    ⚑⚑ INVERTED 2026-08-08c, deliberately.  This class used to guarantee the
    OPPOSITE — that the three §2a keys NEVER touch the translator — and that was
    true while the seed dest was the absolute HOST path ``@meta.box.path/home``,
    which the translator would have re-rooted under the box home.  The respell to
    the guest ``~/`` made routing through the translator the CORRECT behaviour, so
    the guarantee is replaced rather than dropped: the same mis-landing bug is now
    pinned by asserting the translator is asked AND answers with the box home.
    """

    def test_the_seed_dest_routes_through_the_translator_onto_the_box_home(
        self, std, config, project_dir, monkeypatch,
    ):
        """⚑⚑ THE COLLISION, pinned from the guest side.

        Three assertions, and all three are needed:

        1. the §2a seed dest REACHES the one translator (the opposite of the
           pre-respell guarantee), with ``map_home_root=True`` — without that flag
           the bare guest home returns ``None`` and the seed is skipped;
        2. the translator ANSWERS with ``proj.shell_path`` — the box home — and
           not ``None``, not a path re-rooted a level deeper;
        3. the seeded content actually lands on disk at that path.

        ⚑ WHY THIS IS NON-VACUOUS.  The spy DELEGATES to the real translator and
        asserts its RETURN VALUE, so every way the original bug manifests fails
        here: ``None`` fails (2) and skips the copy, so (3) fails too; the
        host-home re-rooting (``shell_path/.local/share/kanibako/boxes/…/home``,
        what a host-spelled dest produced on a ``/home/agent`` host) fails (2) on
        inequality and (3) because nothing is at the real home; an off-by-one
        ``shell_path/home`` fails (2) and is caught again by the explicit check
        below.  A spy that merely RECORDED calls, or an assertion that only
        checked the content landed, would each miss one of those.  The failure
        mode being excluded is the one that REPORTS SUCCESS.
        """
        from kanibako.commands import start as start_mod
        from kanibako.settings.paths import resolve_project
        from kanibako.launch.templates import install_packaged_templates

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        install_packaged_templates(std, ["claude"])

        real = start_mod._guest_dest_to_host
        calls: list[tuple[str, bool, Path | None]] = []

        def _spy(dest, shell_path, project_path, *, map_home_root=False):
            out = real(dest, shell_path, project_path, map_home_root=map_home_root)
            calls.append((dest, map_home_root, out))
            return out

        monkeypatch.setattr(start_mod, "_guest_dest_to_host", _spy)

        class _T:
            name = "claude"

            def default_seeds(self):
                return {}

        start_mod._apply_init_seeds(
            std=std, proj=proj, agent_name="claude", target=_T(),
            global_config_path=std.settings,
            agent_config_path=std.agents / "claude" / "settings.yaml",
            logger=logging.getLogger("t"), deliver_creds=True,
        )

        # (1) The seed dest reached the ONE translator, guest-spelled as the bare
        # box home, with the home-root mapping enabled.
        home_calls = [c for c in calls if c[0].rstrip("/") == GUEST_HOME]
        assert home_calls, [c[0] for c in calls]
        assert all(map_home_root for _d, map_home_root, _o in home_calls), calls

        # (2) ...and it answered with the box home ITSELF.
        assert {out for _d, _m, out in home_calls} == {proj.shell_path}, calls
        # Named separately because it is the exact shape of the old bug: a dest
        # the translator recognized but re-rooted one level too deep.
        assert not (proj.shell_path / "home").exists()

        # (3) ...and the seed content is on disk there.
        assert (
            proj.shell_path / "canon" / "notebook" / "MY_CONTENTS.md"
        ).is_file()


# ===========================================================================
# 2. Reconcile at ONE destination.
# ===========================================================================


def _entry(**kw) -> CategoryEntry:
    # ⚑ Dest-keyed (R-3/R-10): an entry's NAME is its box destination and the key
    # is the arm plus that dest. Callers that care override both.
    base = dict(
        category="seeded", scope="system", box_dest="/x", host_src="/src",
        delivery="COPY", options="", name="/x",
        key_segments=("system", "seeded", "/x"),
    )
    base.update(kw)
    return CategoryEntry(**base)  # type: ignore[arg-type]


class TestReconcileAtOneDestination:
    """A COPY and a MOUNT that name one destination ARE one destination.

    ⚑⚑ THE PREMISE OF THE OLD CLASS DISSOLVED (2026-08-08c).  It pinned that
    reconcile grouped on ``(dest_space, box_dest)`` so that a HOST copy and a
    GUEST mount sharing a dest STRING stayed two independent groups.  There is
    one dest space now (spec §0), ``CategoryEntry.dest_space`` is deleted, and
    ``reconcile_categories`` keys on the bare ``box_dest`` — so the two entries
    meet in ``_resolve_dest_group`` and the cross-delivery ladder picks between
    them.  That is the intended consequence of collapsing the key.

    ⚑⚑ THIS PINS TODAY'S BEHAVIOUR AND DELIBERATELY DOES NOT ASSERT AN ERROR.
    A separately-planned later step ("the collapse") makes two entries at one
    point an ERROR; that is not this pass.  When it lands, these two tests are
    the ones that must be re-derived — do not read their current expectations as
    an argument against making it an error.
    """

    def test_a_box_store_shaped_dest_gets_no_special_treatment(self):
        """A dest that LOOKS like a host box-store path is an ordinary guest dest.

        ⚑ This is what is left of the old host/guest split, and it is worth
        keeping: the string below is exactly the shape the retired host spelling
        produced on a ``/home/agent`` host.  Nothing may re-derive a space from
        it — no prefix heuristic, no special case — so it reconciles precisely
        like any other dest: one group, and the mount beats the ``seeded`` copy.
        """
        shared = f"{GUEST_HOME}/.local/share/kanibako/boxes/demo/home"
        copy = _entry(box_dest=shared, name=shared,
                      key_segments=("system", "seeded", shared))
        mount = _entry(
            category="bindings.ro", box_dest=shared, delivery="MOUNT",
            options="ro", name=shared,
            key_segments=("box", "bindings", "ro", shared),
            scope="box",
        )
        rec = reconcile_categories([copy, mount])
        assert rec.copies == [], rec.copies
        assert [m.key for m in rec.mounts] == [f"box.bindings.ro.{shared}"]

    def test_an_ordinary_guest_dest_reconciles_the_same_way(self):
        """The plain case, unchanged: a MOUNT beats a ``seeded`` COPY at one dest."""
        shared = f"{GUEST_HOME}/thing"
        copy = _entry(box_dest=shared, name=shared,
                      key_segments=("system", "seeded", shared))
        mount = _entry(
            category="bindings.ro", box_dest=shared, delivery="MOUNT",
            options="ro", name=shared,
            key_segments=("box", "bindings", "ro", shared),
            scope="box",
        )
        rec = reconcile_categories([copy, mount])
        assert rec.copies == []
        assert len(rec.mounts) == 1


# ===========================================================================
# 3. The OPTIONAL (skip-if-absent) bind.
# ===========================================================================


class TestOptionalBindEmission:
    """The skip-if-absent policy reaches the emitter as a DEST SET, not as a field.

    ⚑ Cutover step 3 (producer DESIGN §9.1): ``CategoryEntry.optional`` cannot
    survive the fold into ``CollapsedBind(src, opts)``, so the decision travels as
    a parameter spelled in the one thing the collapsed map keeps — the destination.
    """

    _DEST = f"{GUEST_HOME}/canon/x"

    def _emit(self, *, skip_if_absent, caplog, optional: bool = False):
        from kanibako.commands.start import _emit_category_mounts

        entry = _entry(
            category="bindings.ro", scope="box", box_dest=self._DEST,
            host_src="/definitely/not/here", delivery="MOUNT", options="ro",
            # ⚑ Dest-keyed (R-3/R-10): an entry's NAME is its box destination and
            # the key is the arm plus that dest. The retired ``canon_hb_box``
            # spelling is kept out of even a hand-built fixture — a stale form in a
            # test reads as precedent (CONVENTIONS §0).
            name=self._DEST,
            key_segments=("box", "bindings", "ro", self._DEST),
            optional=optional,
        )
        rec = reconcile_categories([entry])
        with caplog.at_level(logging.WARNING):
            mounts = _emit_category_mounts(
                rec, label="category", skip_if_absent=skip_if_absent,
            )
        return mounts

    def test_a_dest_in_the_skip_set_emits_no_mount_and_no_warning(self, caplog):
        emitted = self._emit(skip_if_absent=frozenset({self._DEST}), caplog=caplog)
        assert emitted == []
        assert caplog.records == [], [r.message for r in caplog.records]

    def test_a_dest_outside_the_skip_set_still_warns(self, caplog):
        assert self._emit(skip_if_absent=frozenset(), caplog=caplog) == []
        assert any("does not exist" in r.message for r in caplog.records)

    def test_the_default_is_EMPTY_so_an_unpassed_policy_never_softens_a_drop(
        self, caplog,
    ):
        """⚑ The parameter defaults empty deliberately: warn-and-drop is L7's
        answer, and a caller that states no policy must get it."""
        from kanibako.commands.start import _emit_category_mounts

        entry = _entry(
            category="bindings.ro", scope="box", box_dest=self._DEST,
            host_src="/definitely/not/here", delivery="MOUNT", options="ro",
            name=self._DEST,
            key_segments=("box", "bindings", "ro", self._DEST),
        )
        with caplog.at_level(logging.WARNING):
            assert _emit_category_mounts(
                reconcile_categories([entry]), label="category",
            ) == []
        assert any("does not exist" in r.message for r in caplog.records)

    def test_the_entry_FIELD_no_longer_decides(self, caplog):
        """⚑ MUTATION GUARD. ``optional=True`` with the dest outside the set must
        WARN — if this goes green the emitter is still reading the field, and the
        guard will vanish the moment the fold drops it."""
        emitted = self._emit(
            skip_if_absent=frozenset(), caplog=caplog, optional=True,
        )
        assert emitted == []
        assert any("does not exist" in r.message for r in caplog.records)

    def test_a_key_spelled_skip_set_matches_NOTHING(self, caplog):
        """⚑⚑ THE HISTORICAL BUG, in its second home. ``critical_keys`` was once
        built from key NAMES and matched nothing, silently degrading every critical
        bind. A key-spelled ``skip_if_absent`` fails the same way — loudly here."""
        emitted = self._emit(
            skip_if_absent=frozenset({f"box.bindings.ro.{self._DEST}"}),
            caplog=caplog,
        )
        assert emitted == []
        assert any("does not exist" in r.message for r in caplog.records)


class TestReadOnlyIsDecidedByTokenNotEquality:
    """A ``ro`` entry is DROPPED when its source is missing — however options are spelled.

    ⚑ THIS IS THE LIVE LAUNCH PATH.  ``_emit_category_mounts`` used to ask
    ``e.options != "ro"``, which is a rw answer for any FOLDED read-only spelling
    (``"ro,Z"``).  A rw answer here does not merely mis-label: it takes the
    guarantee-create arm and ``mkdir``s a host directory the user never declared,
    in place of the ro-drop.  The fold keeps ARITY, so nothing else goes red.

    ⚑ The arm is chosen from OPTIONS, never from the category name — each fixture
    below spells the category that matches its options so the two never disagree.
    """

    def _emit(self, *, category: str, options: str, host_src: str, caplog):
        from kanibako.commands.start import _emit_category_mounts

        dest = f"{GUEST_HOME}/folded"
        arm = category.rsplit(".", 1)[-1]
        entry = _entry(
            category=category, scope="box", box_dest=dest,
            host_src=host_src, delivery="MOUNT", options=options, name=dest,
            key_segments=("box", "bindings", arm, dest),
        )
        with caplog.at_level(logging.WARNING):
            return _emit_category_mounts(reconcile_categories([entry]), label="folded")

    @pytest.mark.parametrize("options", ["ro", "ro,Z", "Z,U,ro", " ro "])
    def test_missing_source_is_dropped_for_every_ro_spelling(self, options, tmp_path, caplog):
        absent = tmp_path / "never-created"
        mounts = self._emit(
            category="bindings.ro", options=options, host_src=str(absent), caplog=caplog,
        )
        assert mounts == []
        assert any("does not exist" in r.message for r in caplog.records)
        assert not absent.exists(), "a ro source must be DROPPED, never guarantee-created"

    # ⚑ ``nodirop`` CONTAINS ``ro``; a substring test would call it read-only and
    # skip the guarantee-create.  That is why the predicate is a token test.
    @pytest.mark.parametrize("options", ["Z,U", "", "nodirop"])
    def test_a_non_ro_entry_still_takes_the_guarantee_create_arm(self, options, tmp_path, caplog):
        absent = tmp_path / "made-by-l7"
        mounts = self._emit(
            category="bindings.rw", options=options, host_src=str(absent), caplog=caplog,
        )
        assert len(mounts) == 1
        assert absent.is_dir(), "a rw source is created by L7 guarantee-create"


# ===========================================================================
# 4. The handbook binds: declaration, ordering, and the agent tier.
# ===========================================================================


class _Std:
    """A minimal ``StandardPaths`` stand-in — only ``agents`` is read."""

    def __init__(self, agents: Path) -> None:
        self.agents = agents


class TestCanonDefaultCategories:
    def test_declares_the_five_sibling_binds(self, tmp_path):
        """⚑ Re-derived for dest-keying (R-3/R-5/R-11, P6).

        The five binds are now ENTRIES of the ONE terminal ``box.bindings.ro``
        arm, keyed by their absolutized guest DESTINATIONS — the ``canon_hb_*``
        names are retired outright (R-10), so the destination is what identifies a
        chapter and the assertion is re-derived onto it rather than weakened.
        """
        cats = core_defaults.canon_default_categories(_Std(tmp_path), "claude")
        assert {k for k in cats if k.startswith("box.")} == {"box.bindings.ro"}
        assert set(cats["box.bindings.ro"]) == {
            f"{GUEST_HOME}/canon/handbook/SYS_CONTENTS.md",
            f"{GUEST_HOME}/canon/handbook/general",
            f"{GUEST_HOME}/canon/handbook/agent",
            f"{GUEST_HOME}/canon/handbook/workset",
            f"{GUEST_HOME}/canon/handbook/box",
        }
        # The retired whole-dir spelling must never come back (J-7). Under
        # dest-keying that is no longer a NAME that could reappear — it is the
        # handbook ROOT, and a bind there is what would swallow the five siblings.
        assert f"{GUEST_HOME}/canon/handbook" not in cats["box.bindings.ro"]

    def test_sources_are_the_scope_canon_keys(self, tmp_path):
        arm = core_defaults.canon_default_categories(
            _Std(tmp_path), "claude",
        )["box.bindings.ro"]
        # Slot 0 is still the host_src; only the destination moved out of the tuple.
        assert arm[f"{GUEST_HOME}/canon/handbook/workset"][0] == (
            "@workset.canon/handbook"
        )
        assert arm[f"{GUEST_HOME}/canon/handbook/box"][0] == "@box.canon/handbook"
        assert arm[f"{GUEST_HOME}/canon/handbook/general"][0] == (
            "@system.canon/handbook/general"
        )

    def test_the_guest_dests_carry_no_agent_segment(self, tmp_path):
        """§2d "storage is varied, binding is not": the agent chapter is stored per
        agent node but always ARRIVES at ``~/canon/handbook/agent``.

        ⚑ Re-derived: the destination is the arm's map KEY now (with the ``~``
        absolutized by R-11), so this is a property of the KEY — and the whole arm
        can be swept for a leaked node segment rather than one entry checked.
        """
        arm = core_defaults.canon_default_categories(
            _Std(tmp_path), "raiju℘claude",
        )["box.bindings.ro"]
        assert f"{GUEST_HOME}/canon/handbook/agent" in arm
        assert not [
            d for d in arm if "raiju" in d or "claude" in d or "℘" in d
        ], arm

    def test_a_node_without_its_own_canon_falls_back_to_the_default_tier(
        self, tmp_path,
    ):
        """⚑ J-1's beneficiary case: a PERSONA has no package, so nothing stamps a
        chapter into its store — it must read the DEFAULT agent's chapter, not
        nothing."""
        cats = core_defaults.canon_default_categories(_Std(tmp_path), "raiju℘claude")
        assert cats["agent.default.canon"] == "@config.agents/default/canon"
        assert cats["agent.raiju℘claude.canon"] == "@agent.default.canon"

    def test_a_node_whose_store_provides_a_canon_uses_its_own(self, tmp_path):
        (tmp_path / "claude" / "canon").mkdir(parents=True)
        cats = core_defaults.canon_default_categories(_Std(tmp_path), "claude")
        assert cats["agent.claude.canon"] == "@config.agents/claude/canon"
        # The default tier is STILL declared — it is the fallback for everyone else.
        assert cats["agent.default.canon"] == "@config.agents/default/canon"

    def test_a_no_agent_box_emits_no_agent_chapter_and_no_agent_key(self, tmp_path):
        """A dangling embedded ref would coerce to ``""`` and yield the degenerate
        host path ``/handbook`` (§6b) — so the entry is OMITTED, not emptied."""
        cats = core_defaults.canon_default_categories(_Std(tmp_path), None)
        # ⚑⚑ Re-derived because the old form went VACUOUS, not merely stale: with
        # the ``canon_hb_agent`` NAME retired (R-10) there is no
        # ``box.bindings.ro.canon_hb_agent`` key for ANY box, so the assertion
        # passed whether or not the chapter was emitted. The live property is the
        # absence of the chapter's DESTINATION from the arm.
        assert f"{GUEST_HOME}/canon/handbook/agent" not in cats["box.bindings.ro"]
        # ...and the four non-agent chapters are still there, so this is proving an
        # omission rather than an empty table.
        assert len(cats["box.bindings.ro"]) == 4
        assert not any(k.startswith("agent.") for k in cats)

    def test_only_the_three_chapters_are_optional(self):
        """H6 — the optional set holds FULL declared keys, now DEST-spelled.

        ``settings_launch._emit_bind`` matches this frozenset against the key it
        builds for each entry, so re-spelling the producer without re-spelling this
        set would silently make every chapter non-optional (a missing workset or
        box handbook would start warning on every launch).
        """
        assert core_defaults.canon_optional_bind_keys() == {
            f"box.bindings.ro.{GUEST_HOME}/canon/handbook/agent",
            f"box.bindings.ro.{GUEST_HOME}/canon/handbook/workset",
            f"box.bindings.ro.{GUEST_HOME}/canon/handbook/box",
        }

    def test_the_same_three_chapters_are_the_skip_if_absent_DESTS(self):
        """The EMITTER's view of the same rows (cutover step 3, producer §9.1).

        ⚑ Spelled as DESTS because that is what the collapsed bind map is keyed by;
        a key-spelled set handed to ``_emit_category_mounts`` would match nothing
        and every chapter-less workset would warn on every launch — the failure
        ``critical_keys`` already paid for once.
        """
        assert core_defaults.canon_optional_bind_dests() == {
            f"{GUEST_HOME}/canon/handbook/agent",
            f"{GUEST_HOME}/canon/handbook/workset",
            f"{GUEST_HOME}/canon/handbook/box",
        }

    def test_the_canon_binds_are_not_config_set_repointable(self, tmp_path):
        """Decision 3: the user's repoint route is the ``<scope>.canon`` KEY, never
        the handbook bind, because two spellings for one repoint is the shape
        convention 0 forbids.

        ⚑⚑ RE-DERIVED, because the old form went VACUOUS AND THEN UNBUILDABLE. It
        asserted that no key of ``core_defaults.core_default_bind_keys()`` contained
        the substring ``canon_hb`` — already tautological once R-10 retired the
        ``canon_hb_*`` NAMES (no key of any producer could contain it), and the
        registry itself is now gone with the set-time floor thread. Pin the LIVE
        pair instead: the bind spelling is REFUSED and the ``canon`` key WORKS. Both
        halves are needed — a refusal with no working alternative would just be a
        removed feature.
        """
        from kanibako.settings.config_interface import set_config_value
        from kanibako.settings.config_keys import ConfigLevel

        box_f = tmp_path / "box-settings.yaml"
        dest = f"{GUEST_HOME}/canon/handbook/box"
        refused = set_config_value(
            f"box.bindings.ro.{dest}", "/elsewhere",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_box_path=box_f,
        )
        assert refused.startswith("Error:"), refused
        assert "RETIRED" in refused, refused
        assert not box_f.exists()  # a refused write creates nothing

        ok = set_config_value(
            "box.canon", "/my/contribution/root",
            config_path=box_f, command_scope=ConfigLevel.box,
            cascade_box_path=box_f,
        )
        assert not ok.startswith("Error:"), ok


class TestHandbookMountOrdering:
    def test_contents_and_chapters_reconcile_in_ascending_depth(self, tmp_path):
        """A depth-sort regression is otherwise SILENT. Under J-7 the chapters are
        siblings rather than nested, so ordering is no longer load-bearing for
        correctness — but the ordering itself is still what a future non-sibling
        layout would depend on, and it is free to pin."""
        arm = core_defaults.canon_default_categories(
            _Std(tmp_path), "claude",
        )["box.bindings.ro"]
        # Give every source a real dir/file so nothing is dropped at emission.
        # ⚑ Re-derived for dest-keying: the floor entry is ``dest -> (src, opts)``,
        # so the per-entry stand-in dir is named from the DESTINATION's last
        # segment (the five are siblings, so those are unique) rather than from a
        # retired ``canon_hb_*`` key tail.
        resolved: dict = {"box.bindings.ro": {}}
        for dest, (_ref, opts) in arm.items():
            src = tmp_path / "src" / dest.rsplit("/", 1)[-1]
            src.mkdir(parents=True, exist_ok=True)
            resolved["box.bindings.ro"][dest] = (str(src), opts)
        snap = build_launch_snapshot(
            agent_name="claude", ctx=_ctx(),
            system_path=None, agent_path=None, workset_path=None, box_path=None,
            default_categories=resolved,
        )
        entries = snapshot_category_entries(
            snap, active_agent="claude", box_ctx=_ctx(),
        )
        rec = reconcile_categories(entries)
        dests = [m.box_dest for m in rec.mounts]
        depths = [d.count("/") for d in dests]
        assert depths == sorted(depths), dests
        assert f"{GUEST_HOME}/canon/handbook/box" in dests


@pytest.mark.parametrize("scope", ["box", "agent", "workset"])
def test_every_scope_whitelist_denies_settings_yaml(scope, tmp_path):
    """The one DENY that is a CORRECTNESS property at EVERY scope: ``settings.yaml``
    is that scope's own cascade level."""
    from kanibako.errors import TemplateScopeError
    from kanibako.launch.templates import copy_tree

    src = tmp_path / "src"
    src.mkdir()
    (src / "settings.yaml").write_text("x: 1\n")
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(TemplateScopeError):
        copy_tree(src, dest, scope=scope)

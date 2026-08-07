"""The HOST-space dest contract, the OPTIONAL bind, and the handbook binds.

WHY THIS MODULE EXISTS
----------------------
The C-CANON seeds half added ONE contract — *"a copy dest may be HOST-space, and a
bind may be OPTIONAL"* — and every test here pins a failure that contract closes and
that nothing else would catch:

1. **The ``/home/agent`` host-home collision.**  A HOST store path and a GUEST box
   path are textually indistinguishable, and on a host whose user home IS
   ``/home/agent`` they can collide outright.  Fed to the guest translator, a host
   dest is re-rooted under the box home — content written somewhere nothing reads,
   and the copy REPORTS SUCCESS.  ⚑ The dev box and the seadog LXC test envs are
   exactly such hosts; bifrost (``kanibako``) is not, so the two environments we
   test in disagree about which failure mode appears.  That is the worst possible
   property for a bug, and the reason ``dest_space`` is a FIELD and not a heuristic.
2. **Reconcile grouping.**  Keyed on the bare dest, a host COPY and a guest MOUNT
   that share a dest STRING become one group and "every mount beats ``seeded``"
   silently eats the seed.
3. **Skip-if-absent.**  Without it, every box with no workset/box chapter — i.e.
   almost every box — prints two ro-drop warnings on every launch, which is the noise
   that teaches users to ignore warnings.
4. **The handbook depth order and the agent-tier fallback**, both of which fail
   INVISIBLY: a depth-sort regression mounts the chapters before their parents, and a
   wrong agent tier means ``agent.default`` never fires.

⚑ WHAT CARRIES A HOST DEST TODAY (2026-08-07g).  The ``seeded`` category's host arm
is the box HOME trio (``@meta.box.path/home``).  ``@box.canon/handbook`` LEFT the
category with Jei's HOST-templates ruling and is filled by
``launch.templates.install_box_handbook_template``, which reaches the same
``start._host_copy_dest`` containment check from the other side — so §1 below still
covers both, and the discriminator (``CategoryEntry.dest_space`` /
``templates.seed_keys_of``) is still live for the home trio.

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
# 1. The HOST-space applier branch.
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


class TestSeedRoutesRoundTheGuestTranslator:
    """End-to-end through the REAL seed seam: the three §2a keys never touch
    ``_guest_dest_to_host``, so no amount of guest-translation weirdness can move
    them."""

    def test_guest_translator_is_not_consulted_for_the_seed_layers(
        self, std, config, project_dir, monkeypatch,
    ):
        from kanibako.commands.start import _apply_init_seeds
        from kanibako.settings.paths import resolve_project
        from kanibako.launch.templates import install_packaged_templates

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        install_packaged_templates(std, ["claude"])

        seen: list[str] = []

        def _spy(dest, shell_path, project_path, *, map_home_root=False):
            seen.append(dest)
            return None  # a translation failure must NOT affect the seed layers

        monkeypatch.setattr(
            "kanibako.commands.start._guest_dest_to_host", _spy,
        )

        class _T:
            name = "claude"

            def default_seeds(self):
                return {}

        _apply_init_seeds(
            std=std, proj=proj, agent_name="claude", target=_T(),
            global_config_path=std.settings,
            agent_config_path=std.agents / "claude" / "settings.yaml",
            logger=logging.getLogger("t"), deliver_creds=True,
        )
        store = proj.shell_path.parent
        assert (store / "home" / "canon" / "notebook" / "MY_CONTENTS.md").is_file()
        # The translator was never asked about a §2a seed dest.  ⚑ The spy RETURNS
        # None, so a §2a dest routed through it would deliver NOTHING — the assert
        # above is what makes this one non-vacuous.
        assert not any(d.endswith("/home") for d in seen), seen


# ===========================================================================
# 2. Reconcile grouping on (dest_space, box_dest).
# ===========================================================================


def _entry(**kw) -> CategoryEntry:
    base = dict(
        category="seeded", scope="system", box_dest="/x", host_src="/src",
        delivery="COPY", options="", name="n", key="system.seeded.n",
    )
    base.update(kw)
    return CategoryEntry(**base)  # type: ignore[arg-type]


class TestReconcileGroupsOnTheSpace:
    def test_a_host_copy_and_a_guest_mount_sharing_a_dest_string_stay_two(self):
        """⚑ Keyed on the bare dest these collapse into ONE group and the copy-vs-
        mount rule silently drops the seed — the collision at the reconcile layer."""
        shared = f"{GUEST_HOME}/.local/share/kanibako/boxes/demo/home"
        copy = _entry(box_dest=shared, dest_space="host")
        mount = _entry(
            category="bindings.ro", box_dest=shared, dest_space="guest",
            delivery="MOUNT", options="ro", key="box.bindings.ro.n",
            scope="box",
        )
        rec = reconcile_categories([copy, mount])
        assert len(rec.copies) == 1, rec.copies
        assert len(rec.mounts) == 1, rec.mounts

    def test_same_space_same_dest_still_reconciles_as_before(self):
        """No behavior change for the ordinary case: a guest COPY and a guest MOUNT
        at one dest keep the old outcome (every mount beats ``seeded``)."""
        shared = f"{GUEST_HOME}/thing"
        copy = _entry(box_dest=shared)
        mount = _entry(
            category="bindings.ro", box_dest=shared, delivery="MOUNT",
            options="ro", key=f"box.bindings.ro.{shared}", scope="box",
        )
        rec = reconcile_categories([copy, mount])
        assert rec.copies == []
        assert len(rec.mounts) == 1


# ===========================================================================
# 3. The OPTIONAL (skip-if-absent) bind.
# ===========================================================================


class TestOptionalBindEmission:
    def _emit(self, *, optional: bool, caplog):
        from kanibako.commands.start import _emit_category_mounts

        entry = _entry(
            category="bindings.ro", scope="box", box_dest=f"{GUEST_HOME}/canon/x",
            host_src="/definitely/not/here", delivery="MOUNT", options="ro",
            # ⚑ Dest-keyed (R-3/R-10): an entry's NAME is its box destination and
            # the key is the arm plus that dest. The retired ``canon_hb_box``
            # spelling is kept out of even a hand-built fixture — a stale form in a
            # test reads as precedent (CONVENTIONS §0).
            name=f"{GUEST_HOME}/canon/x",
            key=f"box.bindings.ro.{GUEST_HOME}/canon/x",
            optional=optional,
        )
        rec = reconcile_categories([entry])
        with caplog.at_level(logging.WARNING):
            mounts = _emit_category_mounts(rec, label="category")
        return mounts

    def test_optional_missing_source_emits_no_mount_and_no_warning(self, caplog):
        assert self._emit(optional=True, caplog=caplog) == []
        assert caplog.records == [], [r.message for r in caplog.records]

    def test_non_optional_missing_source_still_warns(self, caplog):
        assert self._emit(optional=False, caplog=caplog) == []
        assert any("does not exist" in r.message for r in caplog.records)


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

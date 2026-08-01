"""The HOST-space dest contract, the OPTIONAL bind, and the handbook binds.

WHY THIS MODULE EXISTS
----------------------
The C-CANON seeds half added ONE contract — *"a copy dest may be HOST-space, and a
bind may be OPTIONAL"* — and every test here pins a failure that contract closes and
that nothing else would catch:

1. **The ``/home/agent`` host-home collision.**  A HOST store path and a GUEST box
   path are textually indistinguishable, and on a host whose user home IS
   ``/home/agent`` they can collide outright.  Fed to the guest translator, the box's
   handbook chapter is written somewhere nothing reads — and the copy REPORTS
   SUCCESS.  ⚑ The dev box and the seadog LXC test envs are exactly such hosts;
   bifrost (``kanibako``) is not, so the two environments we test in disagree about
   which failure mode appears.  That is the worst possible property for a bug, and
   the reason ``dest_space`` is a FIELD and not a heuristic.
2. **Reconcile grouping.**  Keyed on the bare dest, a host COPY and a guest MOUNT
   that share a dest STRING become one group and "every mount beats ``seeded``"
   silently eats the seed.
3. **Skip-if-absent.**  Without it, every box with no workset/box chapter — i.e.
   almost every box — prints two ro-drop warnings on every launch, which is the noise
   that teaches users to ignore warnings.
4. **The handbook depth order and the agent-tier fallback**, both of which fail
   INVISIBLY: a depth-sort regression mounts the chapters before their parents, and a
   wrong agent tier means ``agent.default`` never fires.

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
            label="seed", name="handbook", logger=logging.getLogger("t"),
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
    """End-to-end through the REAL seed seam: the six §2a keys never touch
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
        assert (store / "canon" / "handbook" / "directives" / "SYS_BOX.md").is_file()
        assert (store / "home" / "canon" / "notebook" / "MY_CONTENTS.md").is_file()
        # The translator was never asked about a §2a seed dest.
        assert not any("canon/handbook" in d or d.endswith("/home") for d in seen), seen


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
        shared = f"{GUEST_HOME}/.local/share/kanibako/boxes/demo/canon/handbook"
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
            options="ro", key="box.bindings.ro.n", scope="box",
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
            name="canon_hb_box", key="box.bindings.ro.canon_hb_box",
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
        cats = core_defaults.canon_default_categories(_Std(tmp_path), "claude")
        assert {k for k in cats if k.startswith("box.")} == {
            "box.bindings.ro.canon_hb_contents",
            "box.bindings.ro.canon_hb_general",
            "box.bindings.ro.canon_hb_agent",
            "box.bindings.ro.canon_hb_workset",
            "box.bindings.ro.canon_hb_box",
        }
        # The retired whole-dir spelling must never come back (J-7).
        assert "box.bindings.ro.canon_handbook" not in cats

    def test_sources_are_the_scope_canon_keys(self, tmp_path):
        cats = core_defaults.canon_default_categories(_Std(tmp_path), "claude")
        assert cats["box.bindings.ro.canon_hb_workset"][0] == (
            "@workset.canon/handbook"
        )
        assert cats["box.bindings.ro.canon_hb_box"][0] == "@box.canon/handbook"
        assert cats["box.bindings.ro.canon_hb_general"][0] == (
            "@system.canon/handbook/general"
        )

    def test_the_guest_dests_carry_no_agent_segment(self, tmp_path):
        """§2d "storage is varied, binding is not": the agent chapter is stored per
        agent node but always ARRIVES at ``~/canon/handbook/agent``."""
        cats = core_defaults.canon_default_categories(_Std(tmp_path), "raiju℘claude")
        assert cats["box.bindings.ro.canon_hb_agent"][1] == "~/canon/handbook/agent"

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
        assert "box.bindings.ro.canon_hb_agent" not in cats
        assert not any(k.startswith("agent.") for k in cats)

    def test_only_the_three_chapters_are_optional(self):
        assert core_defaults.canon_optional_bind_keys() == {
            "box.bindings.ro.canon_hb_agent",
            "box.bindings.ro.canon_hb_workset",
            "box.bindings.ro.canon_hb_box",
        }

    def test_the_canon_binds_are_not_config_set_repointable(self):
        """Decision 3: they live in their OWN ``canon:`` section, so they never enter
        the set-time floor registry (which mirrors ``core:`` only) — exactly like the
        channel / helper / kani_pkg / images_conf binds. The user's repoint route is
        the ``<scope>.canon`` KEY, and two spellings for one repoint is the shape
        convention 0 forbids."""
        floor = core_defaults.core_default_bind_keys()
        assert not any("canon_hb" in k for k in floor), floor


class TestHandbookMountOrdering:
    def test_contents_and_chapters_reconcile_in_ascending_depth(self, tmp_path):
        """A depth-sort regression is otherwise SILENT. Under J-7 the chapters are
        siblings rather than nested, so ordering is no longer load-bearing for
        correctness — but the ordering itself is still what a future non-sibling
        layout would depend on, and it is free to pin."""
        cats = core_defaults.canon_default_categories(_Std(tmp_path), "claude")
        binds = {k: v for k, v in cats.items() if k.startswith("box.")}
        # Give every source a real dir/file so nothing is dropped at emission.
        resolved = {}
        for key, (_ref, dest, opts) in binds.items():
            src = tmp_path / "src" / key.rsplit(".", 1)[-1]
            src.mkdir(parents=True, exist_ok=True)
            resolved[key] = (str(src), dest, opts)
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

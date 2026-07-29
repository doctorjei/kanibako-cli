"""Instruction-delivery redesign — increment 2 (deliver the new files).

Two delivery mechanisms land the redesigned playbook tree into a box:

* the RO built-in rom tree (``data/global/rom``) is enumerated PER FILE by
  ``core_defaults.rom_default_categories`` and each shipped file is BIND-MOUNTED
  read-only, live from the installed package, at its mirrored ``~`` path — so the
  ``KANIBAKO.md`` guide lands ro at ``~/playbook/kanibako/directives/KANIBAKO.md``
  (generalizing the retired single ``playbook_kanibako`` whole-dir RO bind); and
* the writable user tree (``data/global/template/playbook``) is SEEDED
  create-if-absent through the existing base-template layer, so
  ``~/playbook/CONTENTS.md`` (+ the scoped directive skeleton) lands at box create.

These tests pin (A) the per-file rom binds enumerate correctly + the guide
reconciles to a ``ro`` Mount at the right slot, and (B) the base-template seed
source now deposits ``playbook/CONTENTS.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kanibako import core_defaults, templates
from kanibako.core_defaults import ROM_GUIDE_REL
from kanibako.paths import resolve_project
from kanibako.settings_categories import reconcile_categories
from kanibako.settings_launch import build_launch_snapshot, snapshot_category_entries
from kanibako.settings_resolve import GUEST_HOME, ResolveCtx
from kanibako.templates import _packaged_base_template, install_packaged_templates


def _ctx() -> ResolveCtx:
    return ResolveCtx(
        agent_name="claude",
        workset_name=None,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


# The 4 shipped rom files (dev __pycache__/*.pyc is EXCLUDED by _is_shipped_content).
_ROM_DESTS = {
    "~/playbook/kanibako/directives/KANIBAKO.md",
    "~/playbook/kanibako/scripts/clear.py",
    "~/playbook/kanibako/scripts/import-directives.py",
    "~/playbook/kanibako/scripts/start.py",
}
_GUIDE_DEST = "~/playbook/kanibako/directives/KANIBAKO.md"


class TestRomBinds:
    """(A) The RO built-in rom tree is enumerated per FILE as ``box.bindings.ro.*``
    binds and the KANIBAKO.md guide reconciles to a read-only Mount at its mirror."""

    def test_enumerates_every_rom_file_ro(self):
        """One RO bind per shipped rom file, at its mirrored ``~`` dest; the dev
        ``__pycache__/*.pyc`` beside the scripts is EXCLUDED (4 files, not 5)."""
        cats = core_defaults.rom_default_categories()
        assert len(cats) == 4, "expected 4 shipped rom files (pyc excluded)"
        dests = {dest for _, dest, _ in cats.values()}
        assert dests == _ROM_DESTS
        assert all(opt == "ro" for _, _, opt in cats.values())
        assert not any(dest.endswith(".pyc") for dest in dests), "no .pyc bind"

    def test_keys_deterministic_and_prefixed(self):
        """Keys are a stable ``rom_<slug>_<hash>`` set, identical across runs."""
        first = core_defaults.rom_default_categories()
        second = core_defaults.rom_default_categories()
        assert first == second, "rom bind keys must be deterministic"
        assert all(k.startswith("box.bindings.ro.rom_") for k in first)

    def test_guide_bind_present_at_expected_dest(self):
        """The load-bearing guide is bound ro at exactly its mirror path, sourced
        from the packaged rom file (never a copy)."""
        cats = core_defaults.rom_default_categories()
        matches = [
            (k, v) for k, v in cats.items() if v[1] == _GUIDE_DEST
        ]
        assert len(matches) == 1, "exactly one guide bind expected"
        _key, (host_src, dest, options) = matches[0]
        assert options == "ro"
        assert dest == _GUIDE_DEST
        assert host_src.endswith(
            "global/rom/playbook/kanibako/directives/KANIBAKO.md"
        )

    def test_no_dir_level_bind_emitted(self):
        """Every emitted dest is a FILE mirror — NEVER a containing dir (a dir-level
        RO bind would shadow the writable ``~/playbook``)."""
        cats = core_defaults.rom_default_categories()
        dests = {dest for _, dest, _ in cats.values()}
        containing_dirs = {
            "~/playbook",
            "~/playbook/kanibako",
            "~/playbook/kanibako/scripts",
            "~/playbook/kanibako/directives",
        }
        assert dests.isdisjoint(containing_dirs)

    def test_guide_reconciles_to_ro_mount_at_slot(self):
        """The guide bind resolves through the launch cascade to a RO Mount whose
        box-side dest is the guest ``~/playbook/kanibako/directives/KANIBAKO.md``."""
        cats = dict(core_defaults.rom_default_categories())
        snap = build_launch_snapshot(
            agent_name="claude",
            ctx=_ctx(),
            system_path=None,
            agent_path=None,
            workset_path=None,
            box_path=None,
            default_categories=cats,
        )
        entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
        rec = reconcile_categories(entries)

        by_dest = {m.box_dest: m for m in rec.mounts}
        guide_box_dest = f"{GUEST_HOME}/playbook/kanibako/directives/KANIBAKO.md"
        assert guide_box_dest in by_dest, "guide bind not reconciled"
        m = by_dest[guide_box_dest]
        assert m.scope == "box"
        assert m.category == "bindings.ro"
        assert m.options == "ro"

    def test_fail_closed_when_guide_missing(self, monkeypatch):
        """A populated rom root that omits the guide RAISES — never a silent
        guide-less box (guards the empty-glob / wrong-root / over-broad-filter class)."""
        def _no_guide(root: Path) -> list[tuple[str, Path]]:
            return [("playbook/other.md", Path("/pkg/rom/playbook/other.md"))]

        monkeypatch.setattr(templates, "walk_shipped_files", _no_guide)
        with pytest.raises(RuntimeError, match="missing the load-bearing box guide"):
            core_defaults.rom_default_categories()

    def test_fail_closed_when_walk_returns_empty(self, monkeypatch):
        """The over-broad-filter / empty-glob class: the guide is physically on
        disk but the walk yields ZERO files — must RAISE, never short-circuit to a
        silent guide-less launch (the empty enumeration must not be mistaken for a
        no-rom install)."""
        monkeypatch.setattr(templates, "walk_shipped_files", lambda root: [])
        with pytest.raises(RuntimeError, match="missing the load-bearing box guide"):
            core_defaults.rom_default_categories()

    def test_disjointness_raises_on_template_collision(self, monkeypatch):
        """A rom file whose ``~`` path collides with a template writable-seed file
        RAISES (an RO bind would silently shadow the writable seed)."""
        collide = "playbook/CONTENTS.md"

        def _colliding(root: Path) -> list[tuple[str, Path]]:
            if str(root).endswith("rom"):
                # rom root: keep the guide (fail-closed passes) + a colliding file.
                return [
                    (ROM_GUIDE_REL, Path("/pkg/rom") / ROM_GUIDE_REL),
                    (collide, Path("/pkg/rom") / collide),
                ]
            # template root: shares the colliding ~ path.
            return [(collide, Path("/pkg/template") / collide)]

        monkeypatch.setattr(templates, "walk_shipped_files", _colliding)
        with pytest.raises(RuntimeError, match="collide with template writable seeds"):
            core_defaults.rom_default_categories()


class TestBaseTemplateSeedsPlaybook:
    """(B) The base-template seed source moved to ``data/global/template``,
    which carries ``playbook/CONTENTS.md`` — so install → seed lands
    ``~/playbook/CONTENTS.md`` (create-if-absent), NO ``INSTRUCTIONS.md``."""

    def test_packaged_source_carries_playbook_contents(self):
        base = _packaged_base_template()
        assert base is not None
        assert (base / "playbook" / "CONTENTS.md").is_file()
        # The retired base template's only file must be gone.
        assert not (base / "INSTRUCTIONS.md").exists()

    def test_install_lands_playbook_in_base_template_dir(self, std):
        """Install copies the packaged tree into ``@system.base_template`` with the
        ``playbook/`` prefix intact (so the layer seeds it at box home ``~``)."""
        install_packaged_templates(std, ["claude"])
        assert (std.base_template / "playbook" / "CONTENTS.md").is_file()

    def test_seed_lands_playbook_contents_at_home(self, std, config, project_dir):
        """End-to-end: the base layer seeds ``~/playbook/CONTENTS.md`` at box
        create through the single keystore-routed seed (create-if-absent)."""
        from kanibako.commands.start import _apply_init_seeds

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        install_packaged_templates(std, ["claude"])
        _apply_init_seeds(
            std=std,
            proj=proj,
            agent_name="claude",
            target=_FakeTarget(),
            global_config_path=std.settings,
            agent_config_path=std.agents / "claude" / "settings.yaml",
            logger=logging.getLogger("test-playbook-seed"),
            deliver_creds=True,
        )
        assert (proj.shell_path / "playbook" / "CONTENTS.md").is_file()


class _FakeTarget:
    """Minimal resolved-agent stand-in for the seed seam (mirrors test_templates):
    only ``.name`` + empty ``default_seeds()`` are read for a non-descriptor target."""

    name = "claude"

    def default_seeds(self):
        return {}

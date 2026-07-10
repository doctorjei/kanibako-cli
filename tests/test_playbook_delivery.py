"""Instruction-delivery redesign — increment 2 (deliver the new files).

Two delivery mechanisms land the redesigned playbook tree into a box:

* the RO built-in bundle (``data/global/base/shared/playbook/kanibako``) is
  BIND-MOUNTED read-only, live from the installed package, at ``~/playbook/kanibako``
  (routed through the keystore as ``box.bindings.ro.playbook_kanibako`` — the new
  kani-category bind mirroring ``kani_pkg``); and
* the writable user tree (``data/global/base/template/playbook``) is SEEDED
  create-if-absent through the existing base-template layer, so
  ``~/playbook/CONTENTS.md`` (+ the scoped directive skeleton) lands at box create.

These tests pin (A) the bundle bind reconciles to a ``ro`` Mount at the right slot
and (B) the base-template seed source now deposits ``playbook/CONTENTS.md``.
"""

from __future__ import annotations

import logging

from kanibako import core_defaults
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


class TestPlaybookBundleBind:
    """(A) The RO built-in bundle rides the keystore as a new kani bind and
    reconciles to a read-only Mount at ``~/playbook/kanibako``."""

    def test_kani_category_emits_bundle_triple(self):
        """``kani_default_categories`` emits ``box.bindings.ro.playbook_kanibako``
        as (packaged shared bundle dir, ~/playbook/kanibako, ro) — mirroring the
        ``kani_pkg`` RO built-in bind, sourced from the installed package."""
        cats = core_defaults.kani_default_categories()
        key = "box.bindings.ro.playbook_kanibako"
        assert key in cats, "missing playbook_kanibako kani bind"
        host_src, box_dest, options = cats[key]
        assert box_dest == "~/playbook/kanibako"
        assert options == "ro"
        # Host source is the import-resolved packaged bundle dir (never a copy).
        assert host_src.endswith("global/base/shared/playbook/kanibako")

    def test_bundle_reconciles_to_ro_mount_at_slot(self):
        """The bind resolves through the launch cascade to a Mount whose box-side
        dest is the guest ``~/playbook/kanibako`` and whose options are ``ro``."""
        cats = dict(core_defaults.kani_default_categories())
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

        by_name = {m.name: m for m in rec.mounts}
        assert "playbook_kanibako" in by_name, "bundle bind not reconciled"
        m = by_name["playbook_kanibako"]
        assert m.scope == "box"
        assert m.category == "bindings.ro"
        # `~` resolved box-side to the guest home; read-only options preserved.
        assert m.box_dest == f"{GUEST_HOME}/playbook/kanibako"
        assert m.options == "ro"


class TestBaseTemplateSeedsPlaybook:
    """(B) The base-template seed source moved to ``data/global/base/template``,
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
            shares=True,
        )
        assert (proj.shell_path / "playbook" / "CONTENTS.md").is_file()


class _FakeTarget:
    """Minimal resolved-agent stand-in for the seed seam (mirrors test_templates):
    only ``.name`` + empty ``default_seeds()`` are read for a non-descriptor target."""

    name = "claude"

    def default_seeds(self):
        return {}

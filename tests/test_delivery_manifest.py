"""GOLDEN MANIFEST — a single holistic guard on box-delivery completeness.

WHY THIS TEST EXISTS
--------------------
Kanibako lands its shipped canon / template / instruction files into a box
through TWO delivery layers, and each is covered PIECEMEAL elsewhere
(``test_templates.py``, ``test_canon_delivery.py``, ``test_instructions_bind.py``).
Piecemeal coverage spot-checks ONE file per mechanism, so a delivery regression
that drops / misplaces / mis-sources a *different* file (e.g. a data-layout
rename that moves ``data/global/template`` or a plugin's ``data/KICKOFF.md``)
can slip through the standard gate and only surface in the podman-gated e2e —
or, worse, in a real box on a user's machine.

This module is the explicit, enumerated cross-check: "create a project, then
assert ALL delivered files are exactly where they should be." The manifest below
is a readable list of every expected (source / home-dest / box-dest); the asserts
iterate it so a failure names the offending file precisely.

WHAT THIS DOES NOT DO
---------------------
This is a HOST-side, NO-podman guard. It proves the SOURCE files exist and the
SOURCE→DEST mapping is correct through the real resolution seams
(``_apply_init_seeds`` for the seed layer; ``reconcile_categories`` /
``descriptor_mounts`` for the bind layer). It does NOT boot a container — the
physical materialization of the RO binds inside a live box is (and remains) the
job of the podman e2e ``tests/e2e/test_instructions_delivery.py``. This test
COMPLEMENTS that e2e; it does not replace it.

THE TWO DELIVERY LAYERS
-----------------------
1. SEEDED (materialized at ``kanibako create``, host-side, create-if-absent):
   the base template tree (``data/global/template`` → ``~/playbook/...``) plus the
   per-agent template tree (``plugins/<agent>/data/template`` → e.g. claude's
   ``~/.claude.json`` + ``~/.claude/settings.json``). Driven here through the SAME
   keystore-routed seed entrypoint the create command uses,
   ``kanibako.commands.start._apply_init_seeds``.

2. BIND-delivered (SOURCE+DEST resolvable host-side; physical bind needs podman):
   * the RO packaged CANON → FIVE SIBLING binds from
     ``core_defaults.rom_default_categories`` (spec §2c, J-7): ``canon_collection``
     and ``canon_bible_contents`` as FILE binds, plus one whole-directory bind per
     packaged bible chapter (``canon_bible_{general,workset,box}``), reconciled
     through ``reconcile_categories``; plus
   * the PLUGIN's bible chapter (``canon_bible_agent``) at ``~/canon/bible/agent``,
     emitted by core from the RESOLVED target and GATED on that plugin shipping
     ``data/rom/directives/ROM_AGENT.md`` — which, since C-CANON R2, all three
     first-party plugins DO, so this manifest requires it of each; and
   * the KICKOFF loader → ``~/.config/kanibako/kickoff.md``. ⚑ TWO SOURCES COEXIST
     this release: core's packaged ``data/global/KICKOFF.md``
     (``box.bindings.ro[~/.config/kanibako/kickoff.md]``, spec §2c / P-5) and each plugin's
     ``data/KICKOFF.md`` descriptor ``managed_pointer`` bind, whose deletion is
     deferred one release. Core YIELDS while a plugin supplies one, so exactly one
     file reaches the slot; the manifest checks BOTH sources ship and that the
     yield holds.

   NOTE on the box guide: the former per-agent ``@system.instructions`` →
   native-slot bind is RETIRED (see ``test_instructions_bind.py``), AND the per-file
   rom enumerator that replaced the old whole-dir ``playbook_kanibako`` bind is
   itself retired (C-CANON R1). The guide now reaches the box ONLY as a file INSIDE
   the ``canon_bible_general`` CHAPTER bind, at
   ``~/canon/bible/general/directives/ROM_GENERAL.md`` — so this manifest asserts
   the packaged guide under that chapter's SOURCE and the bind at its dest, NOT a
   bind of its own. (Under R1 the same guide rode a whole-dir ``canon_bible`` bind;
   J-7 replaced that book-level bind with per-chapter siblings.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from kanibako.settings import core_defaults
from kanibako.settings.core_defaults import (
    BIBLE_AGENT_CHAPTER,
    PLUGIN_CHAPTER_MARKER_REL,
    ROM_BIBLE_CHAPTERS,
    ROM_BIBLE_REL,
    ROM_COLLECTION_REL,
    ROM_CONTENTS_REL,
    ROM_GUIDE_REL,
)
from kanibako.settings.paths import resolve_project
from kanibako.settings.settings_categories import reconcile_categories
from kanibako.settings.settings_launch import build_launch_snapshot, snapshot_category_entries
from kanibako.settings.settings_resolve import GUEST_HOME, ResolveCtx
from kanibako.targets import resolve_target
from kanibako.targets.assembly import descriptor_mounts
from kanibako.targets.base import AgentInstall, PluginDescriptor
from kanibako.launch.templates import (
    _packaged_agent_store,
    _packaged_base_template,
    _packaged_shared_bundle,
    install_packaged_templates,
)


# ===========================================================================
# THE MANIFEST — the explicit, load-bearing declaration of what gets delivered.
# ===========================================================================


@dataclass(frozen=True)
class SeedFile:
    """One file ``box create`` must deposit in the box STORE.

    ⚑ "SEED" IS THE PHASE, NOT THE MECHANISM — two mechanisms fill this manifest and
    the distinction is Jei's 2026-08-07g ruling: the ``home/...`` rows arrive through
    the ``seeded`` CATEGORY (spec §2a layers 1-3), the ``canon/handbook/...`` row
    through the HOST-side template copy that replaced the retired handbook layers
    (``launch.templates.install_box_handbook_template``).  Both run at create, both
    are create-if-absent, and this manifest deliberately does not care which — it
    asserts what a created box HAS.  Which is also why the driver below runs the
    whole ``_seed_box_home`` rather than the seed step alone.

    ``rel`` is the path within the packaged SOURCE subtree that a layer copies from;
    ``dest`` is the box-store-relative path it must land at. ⚑ THE TWO ARE NO LONGER
    THE SAME. Before the canon restructure the layer roots were home-relative, so a
    source-relative path WAS the home-relative one; now a layer's box content lives
    under ``.../box/home`` and ``.../box/canon/handbook`` while the destinations are
    ``home/...`` and ``canon/handbook/...`` in the box store. Spelling them
    separately is the point: a relayout that moves one without the other fails here.

    ``layer`` names WHERE the shipped source lives, so a failure points at the exact
    source tree that dropped/renamed the file.

    ``packaged`` is False for a row whose SOURCE is PLANTED by the driver rather than
    shipped in the wheel — today exactly the workset layer (see the manifest note).
    Those rows are real deliveries and the presence check covers them; only the
    provenance check (*does the wheel ship this source*) skips them.
    """

    layer: str  # "base" | "agent:claude" | "workset"
    rel: str
    dest: str
    packaged: bool = True


# --- SEED layer: every file a claude PRIMARY box must have seeded at create. ---
#
# base layer  = data/global/template/box   (the packaged BOX mould)
# agent layer = plugins/claude/data/base   (the claude AGENT-STORE payload)
#
# ⚑ TWO DESTINATIONS, both HOST paths under the box store, filled by two different
# mechanisms (see ``SeedFile``):
#   home/...            → the ``seeded`` category (spec §2a). Delivered at ``~`` by
#                         the rw home bind (the box's own, agent-writable notebook +
#                         workbook).
#   canon/handbook/...  → ``@box.canon/handbook``, a SIBLING of home, filled by the
#                         host-side handbook template copy and bound RO back into the
#                         box at ``~/canon/handbook/box``. It is NOT under the home: a
#                         guest-spelled dest would land inside the home bind and the
#                         RO mount would silently shadow it.
#
# ⚑ There is deliberately no ``playbook/`` row any more: that tree became the canon
# HANDBOOK, which is BOUND from a host store and never seeded (M-10).
#
# 🛑 THE WORKSET LAYER USED TO BE ABSENT HERE, AND THAT WAS A HOLE IN THE DETECTOR.
# The note this replaces read "INTENTIONALLY absent: a primary box's default workset
# template dir ships no files, so its (skip-if-absent) layer contributes none." Every
# word of that is still true about the WHEEL — and it meant a delivery loss confined
# to the workset scope's seed row was INVISIBLE to the one test whose whole job is
# catching a dropped delivery. Two of the three §2a layers were guarded; the third
# was not, and the third is the one a scope-ordered fold is most likely to drop.
#
# So the driver PLANTS one file in the workset template dir (``packaged=False``) and
# the manifest carries its row. This is not a synthetic extra: it exercises the SAME
# ``workset.seeded`` key a user gets the moment they put a file in that dir.
#: Source path within the workset template dir, and the box-store dest it must reach.
WORKSET_PLANT_REL = "box/home/workset-layer-marker.md"
WORKSET_PLANT_DEST = "home/workset-layer-marker.md"

SEED_MANIFEST: tuple[SeedFile, ...] = (
    # ---- base: the box's own NOTEBOOK (agent-editable directives) ----
    SeedFile("base", "box/home/canon/notebook/MY_CONTENTS.md",
             "home/canon/notebook/MY_CONTENTS.md"),
    SeedFile("base", "box/home/canon/notebook/directives/CONVENTIONS.md",
             "home/canon/notebook/directives/CONVENTIONS.md"),
    # ---- base: the box's own WORKBOOK (process / progress / state) ----
    SeedFile("base", "box/home/canon/workbook/devnotes.md",
             "home/canon/workbook/devnotes.md"),
    SeedFile("base", "box/home/canon/workbook/tasks.md",
             "home/canon/workbook/tasks.md"),
    # ---- base: the box's HANDBOOK CHAPTER — lands OUTSIDE the home, and by the
    # ---- HOST-template copy rather than the ``seeded`` category (2026-08-07g) ----
    SeedFile("base", "box/canon/handbook/directives/SYS_BOX.md",
             "canon/handbook/directives/SYS_BOX.md"),
    # ---- claude agent store payload (harness config stubs) ----
    SeedFile("agent:claude", "template/box/home/.claude.json",
             "home/.claude.json"),
    SeedFile("agent:claude", "template/box/home/.claude/settings.json",
             "home/.claude/settings.json"),
    # ---- workset: layer 3, PLANTED (the wheel ships no workset template files) ----
    SeedFile("workset", WORKSET_PLANT_REL, WORKSET_PLANT_DEST, packaged=False),
)


# --- The HOST-STORE fills the install performs, beyond the box's own seed. ---
#
# ⚑ These are NOT box seeds: they are the (packaged subtree → host store) pairs of
# the ENUMERATED install (P-S2), and they are what the box's handbook binds READ.
# Listed here because a delivery manifest that stopped at the box home would miss
# the whole HANDBOOK book — bound, never seeded.
STORE_MANIFEST: tuple[tuple[str, str], ...] = (
    # packaged rel under data/global/template  ->  host path rel to the store root
    ("handbook/SYS_CONTENTS.md", "canon:handbook/SYS_CONTENTS.md"),
    ("handbook/general/directives/SYS_GENERAL.md",
     "canon:handbook/general/directives/SYS_GENERAL.md"),
    ("handbook/general/directives/rules/CANON.md",
     "canon:handbook/general/directives/rules/CANON.md"),
    ("handbook/general/directives/rules/DATAPOLICY.md",
     "canon:handbook/general/directives/rules/DATAPOLICY.md"),
    ("handbook/general/directives/rules/INTERACTION.md",
     "canon:handbook/general/directives/rules/INTERACTION.md"),
    ("agent_default/canon/handbook/directives/SYS_AGENT.md",
     "agents:default/canon/handbook/directives/SYS_AGENT.md"),
    ("box/home/canon/notebook/MY_CONTENTS.md",
     "template:box/home/canon/notebook/MY_CONTENTS.md"),
    ("workset/canon/handbook/directives/SYS_WORKSET.md",
     "template:workset/canon/handbook/directives/SYS_WORKSET.md"),
)


# --- The PLUGIN-sourced half of the agent store (not in STORE_MANIFEST above,
# which is packaged-CORE-sourced). ⚑ This chapter is the source of the
# ``canon_hb_agent`` bind, so a plugin that stopped shipping it would silently give
# every box of that agent an empty handbook/agent mountpoint.
PLUGIN_STORE_MANIFEST: tuple[tuple[str, str], ...] = (
    ("claude", "canon/handbook/directives/SYS_AGENT.md"),
    ("codex", "canon/handbook/directives/SYS_AGENT.md"),
    ("goose", "canon/handbook/directives/SYS_AGENT.md"),
)


def _seed_source_root(layer: str) -> Path | None:
    """Resolve the packaged SOURCE tree a seed layer copies from.

    ⚑ PACKAGED layers only — a ``packaged=False`` row's source is planted at run
    time and has no wheel subtree to point at, so it never reaches here.
    """
    if layer == "base":
        return _packaged_base_template()
    if layer.startswith("agent:"):
        found = _packaged_agent_store(layer.split(":", 1)[1])
        return None if found is None else found[0]
    raise AssertionError(f"unknown packaged seed layer: {layer!r}")


def _store_root(std, token: str) -> Path:
    """Resolve a ``STORE_MANIFEST`` root token to its host path."""
    return {
        "canon": std.canon,
        "agents": std.agents,
        "template": std.template,
    }[token]


# --- BIND layer: the per-agent KICKOFF-loader delivery slot (uniform dest). ---
_BIND_AGENTS = ("claude", "codex", "goose")
_KICKOFF_BOX_DEST = f"{GUEST_HOME}/.config/kanibako/kickoff.md"

# --- BIND layer: the RO packaged canon (five core siblings + the gated plugin one). ---
#
# ⚑ rom-root-relative source paths are NO LONGER their own ``~``-dests: the packaged
# tree is FLAT (``rom/{COLLECTION.md, bible/**}``, no ``canon/`` wrapper — J-7 /
# Jei's samples), while every guest dest lives under ``~/canon``. The two are spelled
# separately here on purpose, so a relayout that moves one without the other fails.
_COLLECTION_REL_IN_ROM = ROM_COLLECTION_REL
_CONTENTS_REL_IN_ROM = ROM_CONTENTS_REL
_BIBLE_REL_IN_ROM = ROM_BIBLE_REL
_GUIDE_REL_IN_ROM = ROM_GUIDE_REL  # a file INSIDE the general chapter, not its own bind

_CANON_BOX_ROOT = f"{GUEST_HOME}/canon"
_BIBLE_AGENT_BOX_DEST = f"{_CANON_BOX_ROOT}/{_BIBLE_REL_IN_ROM}/{BIBLE_AGENT_CHAPTER}"

# ⚑ DEST-KEYED (disk-store R-3/R-5/R-10). The canon producers return ONE terminal
# arm key whose value is ``{box_dest: (src, options)}``; the ``canon_*`` NAMES that
# used to spell these keys are no longer part of the keyspace, so the manifest is
# indexed by the guest DESTINATION — which R-11 also makes absolute, hence
# ``{GUEST_HOME}/...`` here rather than the authored ``~/...``.
_ARM = "box.bindings.ro"

# guest DEST -> (rom-relative SOURCE, source is a directory)
_CANON_BINDS: dict[str, tuple[str, bool]] = {
    f"{_CANON_BOX_ROOT}/{_COLLECTION_REL_IN_ROM}": (_COLLECTION_REL_IN_ROM, False),
    f"{_CANON_BOX_ROOT}/{_CONTENTS_REL_IN_ROM}": (_CONTENTS_REL_IN_ROM, False),
    **{
        f"{_CANON_BOX_ROOT}/{_BIBLE_REL_IN_ROM}/{chapter}": (
            f"{_BIBLE_REL_IN_ROM}/{chapter}", True,
        )
        for chapter in ROM_BIBLE_CHAPTERS
    },
}
_GENERAL_BOX_DEST = f"{_CANON_BOX_ROOT}/{_BIBLE_REL_IN_ROM}/general"

# The plugin chapter's gate marker, relative to a plugin's ``data/rom`` root.
_CHAPTER_MARKER = PLUGIN_CHAPTER_MARKER_REL


def _ctx() -> ResolveCtx:
    return ResolveCtx(
        agent_name="claude",
        workset_name=None,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


class _FakeTarget:
    """Minimal resolved-agent stand-in for the seed seam (mirrors the sibling
    seed tests): only ``.name`` + an empty ``default_seeds()`` are read for a
    non-descriptor target."""

    name = "claude"

    def default_seeds(self) -> dict[str, object]:
        return {}


# ===========================================================================
# LAYER 1 — SEEDED files land at the box home (create-time, host-side).
# ===========================================================================


class TestSeededManifest:
    """Drive the real create-time home seed and assert the FULL seeded set is
    present at the box home — enumerated from ``SEED_MANIFEST``, not spot-checked.

    Reuses the exact fixtures + entrypoint the sibling seed tests use
    (``std``/``config``/``project_dir`` conftest fixtures, ``resolve_project`` +
    ``install_packaged_templates`` + ``_apply_init_seeds``) — no bespoke harness.
    """

    def _seed_primary_claude_box(self, std, config, project_dir) -> Path:
        """Create + seed a primary claude box; return its box STORE root.

        ⚑ The STORE, not the home: create fills TWO destinations and only one of
        them is under the home.

        ⚑ Drives the WHOLE ``_seed_box_home`` — all three ordered steps, exactly as
        ``box create`` does — NOT ``_apply_init_seeds`` alone.  Since 2026-08-07g the
        handbook chapter is filled by step 3 (a host template copy) rather than by
        the ``seeded`` category, so a driver that stopped at step 2 would be
        asserting delivery for a route that no longer delivers it.

        ⚑ PLANTS the workset layer's one file before seeding — see the manifest note
        at ``WORKSET_PLANT_REL``. Without it the third §2a layer contributes nothing
        and this whole class is blind to a loss confined to the workset scope.
        """
        from types import SimpleNamespace

        from kanibako.commands.start import _seed_box_home

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        install_packaged_templates(std, ["claude"])
        plant = std.primary_workset / "template" / WORKSET_PLANT_REL
        plant.parent.mkdir(parents=True, exist_ok=True)
        plant.write_text("workset layer")
        _seed_box_home(
            std=std,
            proj=proj,
            target=_FakeTarget(),
            desc=None,
            agent_id="claude",
            agent_cfg_path=std.agents / "claude" / "settings.yaml",
            system_settings_path=std.settings,
            auth_src=SimpleNamespace(creds_shared=True),
            logger=logging.getLogger("test-delivery-manifest"),
        )
        return proj.shell_path.parent

    def test_every_manifest_source_exists(self):
        """Provenance guard: every PACKAGED seeded file has a real shipped SOURCE.

        Names exactly which (layer, rel) the shipped tree is missing — so a
        data-layout rename that moves the source tree fails HERE, loudly, naming
        the file, rather than silently seeding nothing.

        ⚑ A ``packaged=False`` row is skipped, and only here: its source is planted
        by the driver, so there is no wheel subtree to interrogate. Its DELIVERY is
        asserted by the sibling below exactly like every other row's.
        """
        missing: list[str] = []
        for entry in SEED_MANIFEST:
            if not entry.packaged:
                continue
            root = _seed_source_root(entry.layer)
            if root is None or not (root / entry.rel).is_file():
                missing.append(f"{entry.layer}:{entry.rel}")
        assert not missing, f"packaged seed SOURCE missing for: {missing}"

    def test_all_seeded_files_present_in_box_store(self, std, config, project_dir):
        """The FULL manifest lands in the box store after a create-time seed.

        A dropped / misplaced / mis-sourced seed file makes its manifest entry
        fail by name — the holistic delivery guard the piecemeal seed tests
        (which check ONE file each) do not provide.
        """
        store = self._seed_primary_claude_box(std, config, project_dir)
        missing = [
            f"{entry.layer}:{entry.rel} -> {entry.dest}"
            for entry in SEED_MANIFEST
            if not (store / entry.dest).is_file()
        ]
        assert not missing, (
            f"box store {store} is missing seeded files: {missing}"
        )

    def test_the_handbook_chapter_is_not_under_the_home(
        self, std, config, project_dir,
    ):
        """⚑⚑ THE HOST-DEST REGRESSION. ``@box.canon/handbook`` is a SIBLING of the
        home, so nothing from that layer may appear anywhere under the home — where
        the guest translator would have put it, silently, reporting success."""
        store = self._seed_primary_claude_box(std, config, project_dir)
        home = store / "home"
        assert (store / "canon" / "handbook" / "directives" / "SYS_BOX.md").is_file()
        assert not list(home.rglob("SYS_BOX.md")), sorted(home.rglob("SYS_BOX.md"))

    def test_every_store_manifest_file_installs(self, std):
        """The HOST-STORE half: the handbook book and the agent-default chapter are
        INSTALLED (never seeded), and they are what the box's handbook binds read."""
        install_packaged_templates(std, ["claude"])
        base = _packaged_base_template()
        missing: list[str] = []
        for src_rel, dest_spec in STORE_MANIFEST:
            token, dest_rel = dest_spec.split(":", 1)
            if base is None or not (base / src_rel).is_file():
                missing.append(f"SOURCE {src_rel}")
                continue
            if not (_store_root(std, token) / dest_rel).is_file():
                missing.append(f"DEST {dest_spec}")
        assert not missing, f"host-store install incomplete: {missing}"

    def test_every_plugin_ships_and_installs_its_handbook_chapter(self, std):
        """The PLUGIN-sourced store half: each agent's own handbook chapter.

        It is the SOURCE of that agent's ``canon_hb_agent`` bind, and the bind is
        SKIP-IF-ABSENT — so a plugin that stopped shipping the chapter would give
        every box of that agent an empty ``~/canon/handbook/agent`` with no warning
        at all. Asserted per plugin, by name.
        """
        agents = [name for name, _rel in PLUGIN_STORE_MANIFEST]
        install_packaged_templates(std, agents)
        missing: list[str] = []
        for name, rel in PLUGIN_STORE_MANIFEST:
            found = _packaged_agent_store(name)
            if found is None or not (found[0] / rel).is_file():
                missing.append(f"SOURCE {name}:{rel}")
                continue
            if not (std.agents / name / rel).is_file():
                missing.append(f"DEST agents/{name}/{rel}")
        assert not missing, f"plugin store install incomplete: {missing}"


# ===========================================================================
# LAYER 2 — BIND-delivered sources + slots resolve (host-side, no podman).
# ===========================================================================


class TestRomBindManifest:
    """The RO packaged CANON: the COLLECTION.md index, the bible's ROM_CONTENTS.md
    and one bind per packaged chapter — each declared with a stable key and
    reconciled to a read-only Mount.

    Modeled on ``test_canon_delivery.py::TestCanonBinds``: the binds ride the
    keystore as ``box.bindings.ro.canon_*`` and resolve through the launch cascade →
    ``reconcile_categories``.
    """

    def _reconcile_rom(self):
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
        return cats, reconcile_categories(entries)

    def test_every_canon_bind_source_exists_and_reconciles_ro(self):
        """The manifest of canon binds: exactly these keys, each with a real
        packaged host SOURCE, each reconciling RO at its declared guest dest."""
        cats, rec = self._reconcile_rom()
        assert set(cats) == {_ARM}, cats
        assert set(cats[_ARM]) == set(_CANON_BINDS), cats

        by_dest = {m.box_dest: m for m in rec.mounts}
        missing: list[str] = []
        for box_dest in _CANON_BINDS:
            host_src, options = cats[_ARM][box_dest]
            if not Path(host_src).exists():
                missing.append(f"{box_dest}: source {host_src}")
            assert options == "ro", box_dest
            assert box_dest in by_dest, f"not reconciled at {box_dest}"
            assert by_dest[box_dest].category == "bindings.ro", box_dest
            assert by_dest[box_dest].options == "ro", box_dest
        assert not missing, f"packaged canon SOURCE missing for: {missing}"

    def test_index_and_contents_are_file_binds_and_chapters_are_directory_binds(self):
        """The shapes are load-bearing: the two indexes mount FILE-onto-file over the
        skeleton's 0-byte mountpoints, while each chapter replaces a whole directory.
        Neither book ROOT is ever bound — ``~/canon`` holds the SEEDED
        notebook/workbook, and ``~/canon/bible`` is R1's retired whole-dir bind."""
        cats, _rec = self._reconcile_rom()
        for box_dest, (_rel, is_dir) in _CANON_BINDS.items():
            assert Path(cats[_ARM][box_dest][0]).is_dir() == is_dir, box_dest
        dests = set(_CANON_BINDS)
        assert f"{GUEST_HOME}/canon" not in dests
        assert f"{GUEST_HOME}/canon/{_BIBLE_REL_IN_ROM}" not in dests

    def test_box_guide_delivered_inside_the_general_chapter_bind(self):
        """The guide has NO bind of its own — it is a file inside the ``general``
        CHAPTER bind, at ``~/canon/bible/general/directives/ROM_GENERAL.md``.

        The former per-agent ``@system.instructions`` → native-slot bind is retired,
        and so is the per-file rom enumerator that used to give the guide its own
        mount. Under R1 it rode a whole-dir ``canon_bible`` bind; J-7 replaced that
        with per-chapter siblings, so it now rides the chapter it belongs to. This is
        the assertion that catches a guide that stops shipping.
        """
        cats, rec = self._reconcile_rom()
        general_src = Path(cats[_ARM][_GENERAL_BOX_DEST][0])
        rom_root = _packaged_shared_bundle()
        assert rom_root is not None
        assert general_src == rom_root / f"{_BIBLE_REL_IN_ROM}/general"

        guide = rom_root / _GUIDE_REL_IN_ROM
        assert guide.is_file(), f"box guide source missing: {guide}"
        assert guide.is_relative_to(general_src), "the guide must ride its chapter"

        by_dest = {m.box_dest: m for m in rec.mounts}
        general_dest = _GENERAL_BOX_DEST
        assert general_dest in by_dest
        assert general_dest == f"{GUEST_HOME}/canon/bible/general"
        # No separate mount for the guide: it arrives with its chapter.
        assert f"{GUEST_HOME}/canon/{_GUIDE_REL_IN_ROM}" not in by_dest

    def test_plugin_bible_chapter_ships_and_binds_for_every_harness(self):
        """The SIXTH canon bind (``canon_bible_agent``): R1 landed the emitter and the
        ``Target.rom_root`` interface; **R2 shipped the CONTENT** in all three plugin
        packages, so this is now a MANIFEST row like any other — every first-party
        harness MUST ship ``data/rom/directives/ROM_AGENT.md`` and MUST bind it at
        ``~/canon/bible/agent``.

        A missing chapter is what this catches: it is not a neutral no-op but a
        dangling ``@agent/directives/ROM_AGENT.md`` import in the bible's
        ``ROM_CONTENTS.md`` on every box that harness runs. (The emitter's GATE — no
        marker, no bind — stays covered by the temp-plugin tests in
        ``test_canon_delivery.py``, which is where a gate-false plugin can still be
        constructed.)
        """
        missing: list[str] = []
        for agent in _BIND_AGENTS:
            target = resolve_target(agent, None)
            root = target.rom_root()
            assert root is not None and root.is_dir(), f"{agent}: no rom_root"
            if not (root / _CHAPTER_MARKER).is_file():
                missing.append(f"{agent}: {root / _CHAPTER_MARKER}")
                continue

            cats = core_defaults.rom_agent_default_categories(target)
            assert set(cats) == {_ARM}, agent
            assert set(cats[_ARM]) == {_BIBLE_AGENT_BOX_DEST}, agent
            src, opts = cats[_ARM][_BIBLE_AGENT_BOX_DEST]
            assert Path(src) == root
            assert opts == "ro"
            assert _BIBLE_AGENT_BOX_DEST == f"{GUEST_HOME}/canon/bible/agent"
        assert not missing, f"packaged plugin bible chapter missing for: {missing}"


class TestKickoffLoaderManifest:
    """The KICKOFF loader reaches the uniform kickoff slot — from BOTH sources.

    Modeled on ``test_instructions_bind.py::test_kickoff_delivered_ro_at_kickoff_slot``:
    the plugin's ``managed_pointer`` (LITERAL-origin, best-effort) binding, driven
    through ``descriptor_mounts``, resolves its shipped ``data/KICKOFF.md`` source
    and mounts RO at ``~/.config/kanibako/kickoff.md``. Parametrized over every
    first-party harness (their KICKOFF.md sources + shared kickoff slot).

    ⚑ Plus the CORE row (P-5 / C-CANON R2): the base now ships the kickoff CONTENT
    at ``data/global/KICKOFF.md`` and emits its ``box.bindings.ro`` entry at that dest, YIELDING
    while a plugin still supplies one. Both sources are manifest rows for as long as
    both exist — a delivery manifest that tracked only the live one would go silent
    exactly when the follow-up release flips which one that is.
    """

    def test_core_kickoff_source_ships_and_declares_the_slot(self):
        """(a) the packaged core loader exists, (b) it declares the same slot."""
        cats = core_defaults.kickoff_default_categories(None)
        assert set(cats) == {_ARM}
        assert set(cats[_ARM]) == {_KICKOFF_BOX_DEST}
        src, opts = cats[_ARM][_KICKOFF_BOX_DEST]
        assert Path(src).is_file(), f"packaged core kickoff SOURCE missing: {src}"
        assert opts == "ro"

    @pytest.mark.parametrize("agent", _BIND_AGENTS)
    def test_core_yields_so_exactly_one_file_reaches_the_slot(self, agent: str):
        """Two deliveries at one dest is a §0 row-1 collision (a hard launch error),
        so while the plugins still ship theirs the core bind must not be emitted."""
        desc = resolve_target(agent, None).descriptor
        assert desc is not None
        assert core_defaults.kickoff_default_categories(desc) == {}

    @staticmethod
    def _kickoff_binding(agent: str):
        desc = resolve_target(agent, None).descriptor
        assert desc is not None
        ptrs = [b for b in desc.bindings if b.key == "managed_pointer"]
        assert len(ptrs) == 1, f"{agent}: expected exactly one managed_pointer binding"
        return ptrs[0]

    @pytest.mark.parametrize("agent", _BIND_AGENTS)
    def test_kickoff_source_exists_and_delivers_ro_at_slot(self, agent: str):
        """(a) host SOURCE (the plugin's KICKOFF.md) exists, (b) box_dest = kickoff slot."""
        b = self._kickoff_binding(agent)

        # (a) The shipped literal source resolves to a real file.
        assert b.literal_src is not None
        assert b.literal_src.is_file(), f"{agent}: KICKOFF-loader source missing"

        # (b) Through the plugin's own delivery path it mounts RO at the slot.
        # The AGENT_CRITICAL binary binds need a real install; isolate the kickoff
        # binding so this stays a pure source→dest check (LITERAL origin ignores
        # the install fields — a dummy nonexistent install is fine).
        p = Path("/nonexistent")
        install = AgentInstall(name=agent, binary=p, install_dir=p, launcher=p)
        d = PluginDescriptor(command=(agent,), bindings=(b,), mode={"start": ()})
        mounts = descriptor_mounts(d, install)
        assert len(mounts) == 1, f"{agent}: kickoff loader did not deliver exactly one mount"
        m = mounts[0]
        assert Path(m.source).is_file()
        assert m.destination == _KICKOFF_BOX_DEST
        assert m.options == "ro"

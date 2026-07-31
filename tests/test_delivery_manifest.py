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
   * the RO packaged CANON → TWO binds from ``core_defaults.rom_default_categories``
     (``canon_collection``, the ``~/canon/COLLECTION.md`` index as a FILE bind, and
     ``canon_bible``, the whole ``~/canon/bible`` book as a DIRECTORY bind),
     reconciled through ``reconcile_categories``; plus
   * the PLUGIN's bible chapter (``canon_bible_agent``) at ``~/canon/bible/agent``,
     emitted by core from the RESOLVED target and GATED on that plugin shipping
     ``data/rom/directives/ROM_AGENT.md``; and
   * the per-harness KICKOFF-loader SEED → ``~/.config/kanibako/kickoff.md``, a
     descriptor ``managed_pointer`` delivery bind resolved through
     ``descriptor_mounts``.

   NOTE on the box guide: the former per-agent ``@system.instructions`` →
   native-slot bind is RETIRED (see ``test_instructions_bind.py``), AND the per-file
   rom enumerator that replaced the old whole-dir ``playbook_kanibako`` bind is
   itself retired (C-CANON R1). The guide now reaches the box ONLY as a file INSIDE
   the whole-dir ``canon_bible`` bind, at
   ``~/canon/bible/general/directives/ROM_GENERAL.md`` — so this manifest asserts
   the packaged guide under the bible bind's SOURCE and the bind at its dest, NOT a
   bind of its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from kanibako import core_defaults
from kanibako.core_defaults import (
    PLUGIN_CHAPTER_MARKER_REL,
    ROM_AGENT_CHAPTER_REL,
    ROM_BIBLE_REL,
    ROM_COLLECTION_REL,
    ROM_GUIDE_REL,
)
from kanibako.paths import resolve_project
from kanibako.settings_categories import reconcile_categories
from kanibako.settings_launch import build_launch_snapshot, snapshot_category_entries
from kanibako.settings_resolve import GUEST_HOME, ResolveCtx
from kanibako.targets import resolve_target
from kanibako.targets.assembly import descriptor_mounts
from kanibako.targets.base import AgentInstall, PluginDescriptor
from kanibako.templates import (
    _packaged_agent_template,
    _packaged_base_template,
    _packaged_shared_bundle,
    install_packaged_templates,
)


# ===========================================================================
# THE MANIFEST — the explicit, load-bearing declaration of what gets delivered.
# ===========================================================================


@dataclass(frozen=True)
class SeedFile:
    """One file the create-time home seed must deposit at the box home.

    ``rel`` is the path BOTH within the packaged source tree AND (identically)
    within the box home — the seed copies the layer tree verbatim under ``~``, so
    for these layers the source-relative path equals the home-relative path.
    ``layer`` names WHERE the shipped source lives, so a failure points at the
    exact source tree that dropped/renamed the file.
    """

    layer: str  # "base" | "agent:claude"
    rel: str


# --- SEED layer: every file a claude PRIMARY box must have seeded at home. ---
#
# base layer  = data/global/template  (the writable user HANDBOOK tree)
# agent layer = plugins/claude/data/template  (the claude harness template)
#
# The base layer seeds all THREE handbook roots (playbook / notebook / workbook);
# see playbook/general/directives/rules/HANDBOOK.md for what each one holds.
# The agent layer ships harness CONFIG only — its directive stub was dropped when
# the box brief moved to the notebook, so no agent-layer playbook file is seeded.
#
# The workset layer is INTENTIONALLY absent: a primary box's default workset
# template dir ships no files, so its (skip-if-absent) layer contributes none.
SEED_MANIFEST: tuple[SeedFile, ...] = (
    # ---- base: playbook — global / agent / workset directives ----
    SeedFile("base", "playbook/CONTENTS.md"),
    SeedFile("base", "playbook/agents/default/directives/BRIEF_AGENTS.md"),
    SeedFile("base", "playbook/general/directives/BRIEF_GENERAL.md"),
    SeedFile("base", "playbook/general/directives/rules/DATAPOLICY.md"),
    SeedFile("base", "playbook/general/directives/rules/HANDBOOK.md"),
    SeedFile("base", "playbook/general/directives/rules/INTERACTION.md"),
    SeedFile("base", "playbook/workset/directives/BRIEF_WORKSET.md"),
    # ---- base: notebook — box-specific directives + history ----
    SeedFile("base", "notebook/directives/BRIEF_BOX.md"),
    SeedFile("base", "notebook/directives/CONVENTIONS.md"),
    # ---- base: workbook — process / progress / state ----
    SeedFile("base", "workbook/devnotes.md"),
    SeedFile("base", "workbook/tasks.md"),
    # ---- claude agent template tree (harness config stubs) ----
    SeedFile("agent:claude", ".claude.json"),
    SeedFile("agent:claude", ".claude/settings.json"),
)


def _seed_source_root(layer: str) -> Path | None:
    """Resolve the packaged SOURCE tree a seed layer copies from."""
    if layer == "base":
        return _packaged_base_template()
    if layer.startswith("agent:"):
        return _packaged_agent_template(layer.split(":", 1)[1])
    raise AssertionError(f"unknown seed layer: {layer!r}")


# --- BIND layer: the per-agent KICKOFF-loader delivery slot (uniform dest). ---
_BIND_AGENTS = ("claude", "codex", "goose")
_KICKOFF_BOX_DEST = f"{GUEST_HOME}/.config/kanibako/kickoff.md"

# --- BIND layer: the RO packaged canon (two core binds + the gated plugin one). ---
#
# rom-root-relative source paths; each is also its ``~/``-relative guest dest,
# because the packaged tree MIRRORS the guest layout.
_COLLECTION_REL_IN_ROM = ROM_COLLECTION_REL
_BIBLE_REL_IN_ROM = ROM_BIBLE_REL
_GUIDE_REL_IN_ROM = ROM_GUIDE_REL  # a file INSIDE the bible bind, not its own bind

_COLLECTION_BOX_DEST = f"{GUEST_HOME}/{_COLLECTION_REL_IN_ROM}"
_BIBLE_BOX_DEST = f"{GUEST_HOME}/{_BIBLE_REL_IN_ROM}"
_BIBLE_AGENT_BOX_DEST = f"{GUEST_HOME}/{ROM_AGENT_CHAPTER_REL}"

_CANON_BIND_KEYS = {
    "box.bindings.ro.canon_collection": _COLLECTION_BOX_DEST,
    "box.bindings.ro.canon_bible": _BIBLE_BOX_DEST,
}

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
        """Create + seed a primary claude box; return its box-home path."""
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
            logger=logging.getLogger("test-delivery-manifest"),
            deliver_creds=True,
        )
        return proj.shell_path

    def test_every_manifest_source_exists(self):
        """Provenance guard: every seeded file has a real packaged SOURCE.

        Names exactly which (layer, rel) the shipped tree is missing — so a
        data-layout rename that moves the source tree fails HERE, loudly, naming
        the file, rather than silently seeding nothing.
        """
        missing: list[str] = []
        for entry in SEED_MANIFEST:
            root = _seed_source_root(entry.layer)
            if root is None or not (root / entry.rel).is_file():
                missing.append(f"{entry.layer}:{entry.rel}")
        assert not missing, f"packaged seed SOURCE missing for: {missing}"

    def test_all_seeded_files_present_at_box_home(self, std, config, project_dir):
        """The FULL manifest lands at the box home after a create-time seed.

        A dropped / misplaced / mis-sourced seed file makes its manifest entry
        fail by name — the holistic delivery guard the piecemeal seed tests
        (which check ONE file each) do not provide.
        """
        home = self._seed_primary_claude_box(std, config, project_dir)
        missing = [
            f"{entry.layer}:{entry.rel}"
            for entry in SEED_MANIFEST
            if not (home / entry.rel).is_file()
        ]
        assert not missing, (
            f"box home {home} is missing seeded files: {missing}"
        )


# ===========================================================================
# LAYER 2 — BIND-delivered sources + slots resolve (host-side, no podman).
# ===========================================================================


class TestRomBindManifest:
    """The RO packaged CANON: the COLLECTION.md index and the whole bible book,
    each declared with a stable key and reconciled to a read-only Mount.

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
        assert set(cats) == set(_CANON_BIND_KEYS), cats

        by_dest = {m.box_dest: m for m in rec.mounts}
        missing: list[str] = []
        for key, box_dest in _CANON_BIND_KEYS.items():
            host_src, dest, options = cats[key]
            if not Path(host_src).exists():
                missing.append(f"{key}: source {host_src}")
            assert options == "ro", key
            # The declared dest is the ``~``-spelling of the guest path.
            assert dest == box_dest.replace(GUEST_HOME, "~", 1), key
            assert box_dest in by_dest, f"{key} not reconciled at {box_dest}"
            assert by_dest[box_dest].category == "bindings.ro", key
            assert by_dest[box_dest].options == "ro", key
        assert not missing, f"packaged canon SOURCE missing for: {missing}"

    def test_index_is_a_file_bind_and_bible_is_a_directory_bind(self):
        """The shapes are load-bearing: a whole-dir bind on ``~/canon`` itself would
        freeze the SEEDED notebook/workbook, which is why the index binds as a FILE."""
        cats, _rec = self._reconcile_rom()
        assert Path(cats["box.bindings.ro.canon_collection"][0]).is_file()
        assert Path(cats["box.bindings.ro.canon_bible"][0]).is_dir()
        assert f"{GUEST_HOME}/canon" not in {v for v in _CANON_BIND_KEYS.values()}

    def test_box_guide_delivered_inside_the_bible_bind(self):
        """The guide has NO bind of its own — it is a file inside the whole-dir
        bible bind, at ``~/canon/bible/general/directives/ROM_GENERAL.md``.

        The former per-agent ``@system.instructions`` → native-slot bind is retired,
        and so is the per-file rom enumerator that used to give the guide its own
        mount. This is the assertion that catches a guide that stops shipping.
        """
        cats, rec = self._reconcile_rom()
        bible_src = Path(cats["box.bindings.ro.canon_bible"][0])
        rom_root = _packaged_shared_bundle()
        assert rom_root is not None
        assert bible_src == rom_root / _BIBLE_REL_IN_ROM

        guide = rom_root / _GUIDE_REL_IN_ROM
        assert guide.is_file(), f"box guide source missing: {guide}"
        assert guide.is_relative_to(bible_src), "the guide must ride the bible bind"

        by_dest = {m.box_dest: m for m in rec.mounts}
        assert _BIBLE_BOX_DEST in by_dest
        assert _BIBLE_BOX_DEST == f"{GUEST_HOME}/canon/bible"
        # No separate mount for the guide: it arrives with its book.
        assert f"{GUEST_HOME}/{_GUIDE_REL_IN_ROM}" not in by_dest

    def test_plugin_bible_chapter_declared_and_gate_negative_today(self):
        """The SEVENTH canon bind (``canon_bible_agent``): R1 lands the emitter and
        the ``Target.rom_root`` interface; R2 fans the CONTENT out to the three
        plugin packages. Until a plugin ships its chapter marker the emitter yields
        NOTHING — correct, because an empty plugin rom would shadow core's
        placeholder chapter into a dangling ``@agent/directives/ROM_AGENT.md``.

        ⚑ WHEN R2 LANDS this test does not need editing: it asserts the CONTRACT on
        both sides of the gate, so a harness that starts shipping a chapter is
        checked for the right bind and one that does not is checked for none.
        """
        for agent in _BIND_AGENTS:
            target = resolve_target(agent, None)
            root = target.rom_root()
            assert root is not None and root.is_dir(), f"{agent}: no rom_root"

            cats = core_defaults.rom_agent_default_categories(target)
            if (root / _CHAPTER_MARKER).is_file():
                assert set(cats) == {"box.bindings.ro.canon_bible_agent"}, agent
                src, dest, opts = cats["box.bindings.ro.canon_bible_agent"]
                assert Path(src) == root and dest == f"~/{ROM_AGENT_CHAPTER_REL}"
                assert opts == "ro"
                assert _BIBLE_AGENT_BOX_DEST == f"{GUEST_HOME}/canon/bible/agent"
            else:
                assert cats == {}, f"{agent}: chapter emitted without a marker"


class TestKickoffLoaderManifest:
    """The per-harness KICKOFF-loader SEED delivers RO to the uniform kickoff slot.

    Modeled on ``test_instructions_bind.py::test_kickoff_delivered_ro_at_kickoff_slot``:
    the plugin's ``managed_pointer`` (LITERAL-origin, best-effort) binding, driven
    through ``descriptor_mounts``, resolves its shipped ``data/KICKOFF.md`` source
    and mounts RO at ``~/.config/kanibako/kickoff.md``. Parametrized over every
    first-party harness (their KICKOFF.md sources + shared kickoff slot).
    """

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

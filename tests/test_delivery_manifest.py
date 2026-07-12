"""GOLDEN MANIFEST — a single holistic guard on box-delivery completeness.

WHY THIS TEST EXISTS
--------------------
Kanibako lands its shipped playbook / directive / instruction files into a box
through TWO delivery layers, and each is covered PIECEMEAL elsewhere
(``test_templates.py``, ``test_playbook_delivery.py``, ``test_instructions_bind.py``).
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
   * the RO built-in rom tree → each shipped file bound ro at its mirrored ``~``
     path (carries the ``KANIBAKO.md`` guide at
     ``~/playbook/kanibako/directives/KANIBAKO.md``), enumerated PER FILE by
     ``core_defaults.rom_default_categories`` and reconciled through
     ``reconcile_categories``; and
   * the per-harness KICKOFF-loader SEED → ``~/.config/kanibako/kickoff.md``, a
     descriptor ``managed_pointer`` delivery bind resolved through
     ``descriptor_mounts``.

   NOTE on the KANIBAKO.md guide: the former per-agent ``@system.instructions`` →
   native-slot bind is RETIRED (see ``test_instructions_bind.py``), AND the former
   single whole-dir ``playbook_kanibako`` RO bind is GENERALIZED to a per-file rom
   enumerator. The guide now reaches the box ONLY as a per-file rom RO bind at
   ``~/playbook/kanibako/directives/KANIBAKO.md``, so this manifest asserts the
   guide there — NOT as a separate per-agent bind and NOT as a whole-dir bind.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from kanibako import core_defaults
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
# base layer  = data/global/template  (the writable user playbook tree)
# agent layer = plugins/claude/data/template  (the claude harness template)
#
# The workset layer is INTENTIONALLY absent: a primary box's default workset
# template dir ships no files, so its (skip-if-absent) layer contributes none.
SEED_MANIFEST: tuple[SeedFile, ...] = (
    # ---- base template tree (create-if-absent playbook skeleton) ----
    SeedFile("base", "playbook/CONTENTS.md"),
    SeedFile("base", "playbook/agents/directives/AGENTS.md"),
    SeedFile("base", "playbook/box/directives/BOX.md"),
    SeedFile("base", "playbook/general/directives/GENERAL.md"),
    SeedFile("base", "playbook/general/directives/rules/INTERACTION.md"),
    SeedFile("base", "playbook/general/directives/rules/WORKPOLICY.md"),
    SeedFile("base", "playbook/workset/directives/WORKSET.md"),
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

# The RO rom tree is enumerated PER FILE (``core_defaults.rom_default_categories``);
# the KANIBAKO.md guide is one such file, bound ro at its mirror path.
_GUIDE_REL_IN_ROM = "playbook/kanibako/directives/KANIBAKO.md"  # rom-root-relative
_GUIDE_BOX_DEST = f"{GUEST_HOME}/{_GUIDE_REL_IN_ROM}"


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
            shares=True,
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
    """The RO rom tree is enumerated per FILE; the KANIBAKO.md guide reconciles to
    a read-only Mount at its mirror ``~/playbook/kanibako/directives/KANIBAKO.md``.

    Modeled on ``test_playbook_delivery.py::TestRomBinds``: the binds ride the
    keystore as ``box.bindings.ro.rom_*`` and resolve through the launch cascade →
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

    def _guide_bind(self, cats):
        """Locate the single guide bind by its mirror dest (hash-key robust)."""
        matches = [
            (k, v) for k, v in cats.items()
            if v[1] == f"~/{_GUIDE_REL_IN_ROM}"
        ]
        assert len(matches) == 1, "exactly one guide bind expected"
        return matches[0]

    def test_guide_source_exists_and_reconciles_to_ro_slot(self):
        """(a) host SOURCE (the packaged guide FILE) exists, (b) box_dest is the
        guide's mirror slot, ro."""
        cats, rec = self._reconcile_rom()

        # (a) The declared host source is the packaged guide file and it EXISTS.
        _key, (host_src, _dest, _opts) = self._guide_bind(cats)
        host_path = Path(host_src)
        assert host_path.is_file(), f"guide source missing: {host_path}"
        bundle = _packaged_shared_bundle()
        assert bundle is not None
        assert host_path == bundle / "directives" / "KANIBAKO.md"

        # (b) It reconciles to a RO Mount at the correct box-side slot.
        by_dest = {m.box_dest: m for m in rec.mounts}
        assert _GUIDE_BOX_DEST in by_dest, "guide bind not reconciled"
        m = by_dest[_GUIDE_BOX_DEST]
        assert m.category == "bindings.ro"
        assert m.box_dest == _GUIDE_BOX_DEST
        assert m.options == "ro"

    def test_kanibako_guide_delivered_at_mirror(self):
        """The KANIBAKO.md guide is delivered ro at exactly its mirror path.

        The former per-agent ``@system.instructions`` → native-slot bind is
        retired; the guide now reaches the box only at
        ``~/playbook/kanibako/directives/KANIBAKO.md`` (a per-file rom RO bind).
        """
        cats, rec = self._reconcile_rom()
        _key, (host_src, dest, options) = self._guide_bind(cats)

        assert Path(host_src).is_file(), f"KANIBAKO.md guide source missing: {host_src}"
        assert dest == f"~/{_GUIDE_REL_IN_ROM}"
        assert options == "ro"

        by_dest = {m.box_dest: m for m in rec.mounts}
        assert _GUIDE_BOX_DEST in by_dest
        assert _GUIDE_BOX_DEST == (
            f"{GUEST_HOME}/playbook/kanibako/directives/KANIBAKO.md"
        )


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

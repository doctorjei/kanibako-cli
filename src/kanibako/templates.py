"""Shell template resolution and application."""

from __future__ import annotations

import importlib.resources
import shutil
import tempfile
from typing import TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from kanibako.paths import ProjectPaths, StandardPaths
    from kanibako.targets.base import Target


# ---------------------------------------------------------------------------
# Layered-template path resolution (Phase 7a).
#
# The 1.6.0 home-seed model layers three ordered template sources into the box
# home at creation (base -> agent -> workset; later overlays earlier).  These
# pure helpers DERIVE each layer's on-disk source root; ``stage_and_seed_templates``
# below stages them, in order, then seeds the merged tree into the box home
# (the key-route TEMP-STORE design).
#
#   layer 1  base    @system.base_template      = @system.global/base_template (FLAT)
#   layer 2  agent   @agent.<agent>.template    = @system.agents/<agent>/template
#   layer 3  workset @workset.template          = @meta.workset.path/template
#
# Layers 2/3 are runtime-dependent (agent name / workset mode), so they are
# free functions rather than ``StandardPaths`` properties — mirroring the
# channels helpers (``channels.workset_root`` / ``workset_channel_paths``).
# ---------------------------------------------------------------------------


def base_template_dir(std: StandardPaths) -> Path:
    """Return the layer-1 base-template source root ``@system.base_template``.

    FLAT: the base template is read directly from ``@system.base_template/*``
    (no ``general/`` or variant subdir — the 1.6.0 model drops those).
    """
    return std.base_template


def agent_template_dir(std: StandardPaths, agent_name: str) -> Path:
    """Return the layer-2 agent-template source root ``@agent.<agent>.template``.

    Derived as ``@system.agents/<agent_name>/template``.  Per-agent, so it
    depends on the runtime agent name (hence a derived helper, not a
    ``StandardPaths`` property).
    """
    return std.agents / agent_name / "template"


def workset_template_dir(proj: ProjectPaths, std: StandardPaths) -> Path | None:
    """Return the layer-3 workset-template source root ``@workset.template``.

    Derived as ``@meta.workset.path/template`` for PRIMARY/NAMED worksets,
    reusing :func:`kanibako.channels.workset_root`.  Returns ``None`` for
    STANDALONE boxes (no workset-local template layer).
    """
    from kanibako.channels import has_workset_channels, workset_root

    if not has_workset_channels(proj):
        return None
    return workset_root(proj, std) / "template"


def template_layer_specs(
    target: Target | None,
    proj: ProjectPaths,
    std: StandardPaths,
) -> list[Path]:
    """Return the ordered template-layer source roots, LOWEST -> HIGHEST.

    The 1.6.0 home-seed model layers ordered template sources into the box home
    at creation (base -> agent -> workset; later overlays earlier — §2a).  This
    pure helper RESOLVES each layer's on-disk source root, in authority order::

        1. base    @system.base_template       (always present)
        2. agent   @agent.<agent>.template      (only when an agent target binds)
        3. workset @workset.template            (None / absent for STANDALONE)

    Layers whose source is ``None`` (no agent target; standalone has no workset
    layer) or whose directory is absent on disk are SKIPPED, so the returned list
    contains only real, existing layer roots ready to stage.  Reuses the existing
    per-layer resolvers (:func:`base_template_dir`, :func:`agent_template_dir`,
    :func:`workset_template_dir`) — no path logic is reinvented here.
    """
    candidates: list[Path | None] = [base_template_dir(std)]
    if target is not None:
        candidates.append(agent_template_dir(std, target.name))
    candidates.append(workset_template_dir(proj, std))
    return [layer for layer in candidates if layer is not None and layer.is_dir()]


def stage_and_seed_templates(home: Path, layers: list[Path]) -> None:
    """Seed *home* once from the ordered template *layers* via a TEMP staging dir.

    Realises the key-route TEMP-STORE design (PLAN "R1/R4 RESOLVED ... TEMP-STORE
    STAGE"): the per-file LAST-WINS merge across layers is resolved entirely in a
    temporary staging dir (where overwrite is intended), and the merged tree is
    then copied into *home* with CREATE-IF-ABSENT (an existing home file is NEVER
    overwritten).  Two phases:

    1. **Stage.** Copy each layer's files into a temporary dir in order
       (LOWEST -> HIGHEST).  Overwrite WITHIN staging is intended, so a later
       layer's file at the same relative path wins (per-file last-wins).

    2. **Seed.** Copy the merged staged tree into *home* with
       :func:`_copy_resource_tree_if_absent` — a pre-existing home file (a
       user-edited file on a box detection mis-flags as un-seeded) survives
       untouched.  This is the load-bearing failsafe against re-seed DATA LOSS.

    SEED-ONCE: the caller invokes this only when the box still needs seeding (the
    launch gate detects an already-seeded box via the per-box registry flag OR the
    existing-inbox backstop).  No file is special-cased or merged — every file is a
    plain ordered copy (the CLAUDE.md merge special-case is gone, D-B5).
    """
    if not layers:
        return
    with tempfile.TemporaryDirectory(prefix="kanibako-seed-") as staging:
        staged = Path(staging)
        for layer in layers:
            for entry in sorted(layer.rglob("*")):
                if not entry.is_file():
                    continue
                rel = entry.relative_to(layer)
                dest = staged / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Overwrite within staging is intended (per-file last-wins).
                shutil.copy2(str(entry), str(dest))
        _copy_resource_tree_if_absent(staged, home)


# ---------------------------------------------------------------------------
# Packaged curated-template install (Phase 9c).
#
# The base + per-agent template content ships as STATIC files inside the
# installed packages (mirroring how ``image-baseline.yaml`` ships under
# ``kanibako.data``):
#
#   base   -> ``kanibako.data`` resource ``templates/base/``
#   agent  -> ``kanibako.plugins.<agent>`` resource ``template/``
#
# On first-run init (``cli._ensure_initialized`` / ``install.run``) these are
# COPIED into the runtime template dirs (``@system.base_template`` and
# ``@system.agents/<agent>/template``) where the layered seed-once apply above
# reads them at box creation.  The copy is CREATE-IF-ABSENT per file: it adds
# files the user does not yet have but never clobbers a user-edited template
# (an explicit "refresh from package" is out of scope for 1.6.0).
# ---------------------------------------------------------------------------


def _copy_resource_tree_if_absent(src: Path, dest: Path) -> None:
    """Copy every file under *src* into *dest*, skipping files that exist.

    Mirrors the relative tree of *src* into *dest*.  Existing destination files
    are left untouched (create-if-absent) so user edits to a seeded template
    survive a later kanibako upgrade.  Directories are created as needed.
    """
    if not src.is_dir():
        return
    for entry in src.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(src)
        target = dest / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(entry), str(target))


# Public alias so other modules (e.g. the seed-once apply in commands.start)
# can reuse the create-if-absent tree copy without reaching for a private name.
copy_resource_tree_if_absent = _copy_resource_tree_if_absent


def _packaged_base_template() -> Path | None:
    """Locate the packaged base-template content (``kanibako.data/templates/base``)."""
    try:
        ref = importlib.resources.files("kanibako.data").joinpath("templates", "base")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def _packaged_agent_template(agent_name: str) -> Path | None:
    """Locate a plugin's packaged template content (``kanibako.plugins.<agent>/template``).

    Returns ``None`` if the plugin is not installed or ships no ``template/``
    (e.g. ``no_agent`` / a third-party target without curated content).
    """
    try:
        ref = importlib.resources.files(
            f"kanibako.plugins.{agent_name}"
        ).joinpath("template")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def install_packaged_templates(std: StandardPaths, agent_names: list[str]) -> None:
    """Copy packaged curated-template content into the runtime template dirs.

    Populates ``@system.base_template`` from the packaged base content and each
    ``@system.agents/<agent>/template`` from the agent plugin's packaged
    ``template/``.  Create-if-absent (never clobbers user edits).  Called from
    first-run init; safe to re-run (idempotent for unchanged trees).
    """
    base_src = _packaged_base_template()
    if base_src is not None:
        _copy_resource_tree_if_absent(base_src, std.base_template)

    for agent_name in agent_names:
        agent_src = _packaged_agent_template(agent_name)
        if agent_src is None:
            continue
        dest = agent_template_dir(std, agent_name)
        _copy_resource_tree_if_absent(agent_src, dest)

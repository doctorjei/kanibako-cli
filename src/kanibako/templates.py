"""Shell template resolution and application."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from kanibako.paths import ProjectPaths, StandardPaths


# ---------------------------------------------------------------------------
# Layered-template path resolution (Phase 7a).
#
# The 1.6.0 home-seed model layers three ordered template sources into the box
# home at creation (base -> agent -> workset; later overlays earlier).  These
# pure helpers DERIVE each layer's on-disk source root; ``apply_template_layers``
# below copies them, in order, into the box home (Phase 7c).
#
#   layer 1  base    @system.base_template      = @system.global/base_template (FLAT)
#   layer 2  agent   @agent.<agent>.template    = @system.agents/<agent>/template
#   layer 3  workset @workset.template          = @workset.meta.root/template
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

    Derived as ``@workset.meta.root/template`` for PRIMARY/NAMED worksets,
    reusing :func:`kanibako.channels.workset_root`.  Returns ``None`` for
    STANDALONE boxes (no workset-local template layer).
    """
    from kanibako.channels import has_workset_channels, workset_root

    if not has_workset_channels(proj):
        return None
    return workset_root(proj, std) / "template"


def apply_template_layers(
    home: Path,
    layers: list[Path | None],
) -> None:
    """Seed *home* once by copying the ordered template *layers* in order.

    The 1.6.0 home-seed model layers ordered template sources into the box home
    at creation (base -> agent -> workset; later overlays earlier).  Each layer
    in *layers* is copied into *home* in sequence with ``dirs_exist_ok=True`` so
    a later layer's file overwrites an earlier layer's file at the same relative
    path (per-file LAST-WINS).  Layers that do not exist on disk are skipped (a
    ``<None>`` / absent layer contributes nothing — e.g. STANDALONE boxes have
    no workset layer).

    This is SEED-ONCE: callers invoke it only on box creation
    (``proj.is_new``).  It does NOT special-case or merge any file — every file
    is a plain ordered copy (the CLAUDE.md merge special-case is gone, D-B5).
    The caller is responsible for never re-running it on a subsequent launch so
    user edits made inside the box survive (D-B6).
    """
    for layer in layers:
        if layer is None:
            continue
        if not layer.is_dir():
            continue
        home.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(layer), str(home), dirs_exist_ok=True)

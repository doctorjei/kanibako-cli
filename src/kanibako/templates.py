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
# pure helpers DERIVE each layer's on-disk source root.  They do NOT apply
# anything — the legacy ``apply_shell_template`` below still drives the actual
# seed until Phase 7c swaps in the layered seed-once apply.
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


def resolve_template(
    templates_base: Path,
    agent_name: str,
    template_name: str = "standard",
) -> Path | None:
    """Return the path to the resolved template directory, or None for 'empty'.

    Resolution order:
      1. {templates_base}/{agent_name}/{template_name}/
      2. {templates_base}/general/{template_name}/
      3. None (empty template — no files applied)
    """
    if template_name == "empty":
        return None

    agent_dir = templates_base / agent_name / template_name
    if agent_dir.is_dir():
        return agent_dir

    general_dir = templates_base / "general" / template_name
    if general_dir.is_dir():
        return general_dir

    return None


def apply_shell_template(
    shell_path: Path,
    templates_base: Path,
    agent_name: str,
    template_name: str = "standard",
) -> None:
    """Apply base + resolved template to a shell directory.

    Layering:
      1. general/base/* is copied first (common skeleton)
      2. The resolved template overlays on top

    No-op if the resolved template is None (the ``"empty"`` sentinel or no
    template dirs exist on disk).
    """
    resolved = resolve_template(templates_base, agent_name, template_name)
    if resolved is None:
        return

    # Layer 1: general/base (if it exists)
    base_dir = templates_base / "general" / "base"
    if base_dir.is_dir():
        shutil.copytree(str(base_dir), str(shell_path), dirs_exist_ok=True)

    # Layer 2: resolved template
    shutil.copytree(str(resolved), str(shell_path), dirs_exist_ok=True)

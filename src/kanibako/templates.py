"""Shell template resolution and application."""

from __future__ import annotations

import shutil
from pathlib import Path


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

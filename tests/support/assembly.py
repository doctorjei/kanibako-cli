"""Assemble the cascade levels from settings file PATHS, as the NARROW read does."""

from __future__ import annotations

from pathlib import Path

from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_assemble import ReadPurpose, assemble_levels, cascade_files


def assemble_levels_at(
    *,
    agent_name: str,
    floor: dict[str, object] | None = None,
    system_path: Path | None = None,
    agent_path: Path | None = None,
    workset_path: Path | None = None,
    box_path: Path | None = None,
    base_path: Path | None = None,
) -> list[KeyStore]:
    """``assemble_levels`` over the files at these paths, read with ``ReadPurpose.NARROW``."""
    return assemble_levels(
        agent_name=agent_name,
        files=cascade_files(
            purpose=ReadPurpose.NARROW, system_path=system_path, agent_path=agent_path,
            workset_path=workset_path, box_path=box_path, base_path=base_path,
        ),
        floor=floor,
    )


def display_view(path: Path, level: str) -> object:
    """The file at *path* as a *level* file's reader views it (``ReadPurpose.DISPLAY``)."""
    from kanibako.settings.settings_assemble import read_settings_files

    (read,) = read_settings_files(((level, path),), purpose=ReadPurpose.DISPLAY)
    return read.view

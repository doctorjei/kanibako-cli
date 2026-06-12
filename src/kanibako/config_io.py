"""Centralized load/dump for kanibako config documents (YAML).

All kanibako-owned config files (kanibako.yaml, project.yaml, config.yaml,
workset.yaml, names.yaml, spawn.yaml, general.yaml, crab configs) are
serialized as YAML through these two helpers. There is no hand-rolled
serializer. (pyproject.toml is Python packaging and is NOT handled here.)
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_doc(path: Path | None) -> dict:
    """Load a config document → dict. Missing/empty/non-mapping → {}."""
    if path is None or not path.exists():
        return {}
    text = path.read_text()
    # Defensive: only parse real text. A non-str (e.g. a MagicMock from an
    # under-mocked test path) fed to yaml.safe_load can balloon memory
    # catastrophically — guard the host instead of trusting the input.
    if not isinstance(text, str):
        return {}
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def dump_doc(path: Path, data: dict) -> None:
    """Serialize *data* to *path* as YAML (creates parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            data, sort_keys=False, default_flow_style=False, allow_unicode=True,
        )
    )

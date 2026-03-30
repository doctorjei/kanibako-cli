"""Goose credential and config handling via YAML files."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from kanibako.utils import cp_if_newer

try:
    import yaml
except ImportError:
    raise ImportError(
        "PyYAML is required for the Goose plugin. Install it with: pip install pyyaml"
    )

# Keys from config.yaml that are safe to copy (non-secret settings).
_SAFE_CONFIG_KEYS = {"provider", "model", "extensions", "instructions"}


def read_yaml(path: Path) -> dict:
    """Read a YAML file, returning {} on any error."""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
        return data if isinstance(data, dict) else {}
    except (yaml.YAMLError, OSError):
        return {}


def write_yaml(path: Path, data: dict) -> None:
    """Write a dict as YAML, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, default_flow_style=False))


def filter_config(src: Path, dst: Path) -> None:
    """Copy host config.yaml to project, keeping only safe keys.

    Non-safe keys (credentials, tokens, etc.) are dropped.
    """
    data = read_yaml(src)
    if not data:
        return
    filtered = {k: v for k, v in data.items() if k in _SAFE_CONFIG_KEYS}
    write_yaml(dst, filtered)


def refresh_secrets(host_secrets: Path, project_secrets: Path) -> bool:
    """Copy host secrets.yaml to project if host is newer.

    If project secrets don't exist yet, copies the host file wholesale.
    Sets 0600 permissions on the project copy.
    Returns True if the project file was updated.
    """
    if not host_secrets.is_file():
        return False

    # If project secrets don't exist, copy wholesale
    if not project_secrets.is_file():
        project_secrets.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(host_secrets), str(project_secrets))
        project_secrets.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        return True

    # mtime check: only update if host is strictly newer
    if os.stat(host_secrets).st_mtime <= os.stat(project_secrets).st_mtime:
        return False

    shutil.copy2(str(host_secrets), str(project_secrets))
    project_secrets.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    return True


def writeback_secrets(project_secrets: Path) -> None:
    """Copy project secrets.yaml back to host if newer."""
    if not project_secrets.is_file():
        return
    host_secrets = Path.home() / ".config" / "goose" / "secrets.yaml"
    cp_if_newer(project_secrets, host_secrets)

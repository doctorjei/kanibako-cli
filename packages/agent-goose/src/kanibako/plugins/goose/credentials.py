"""Goose config handling via YAML files.

The host<->box sync of goose's secrets.yaml is NOT here: it is declared as a
``CredFileSpec`` in the goose ``PluginDescriptor`` (``goose-defaults.yaml``) and
realized by the agent-agnostic credsync engine (the §2d ``synced`` category
view).  The former bespoke ``refresh_secrets`` / ``writeback_secrets`` copies
(an unsanctioned second route) were folded into that one SYNC spec.
"""

from __future__ import annotations

from pathlib import Path

try:
    import yaml
except ImportError:
    raise ImportError(
        "PyYAML is required for the Goose plugin. Install it with: pip install pyyaml"
    )

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

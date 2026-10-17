"""Goose YAML parsing.

The host<->box sync of goose's secrets.yaml is NOT here: it is declared as a
``CredFileSpec`` in the goose ``PluginDescriptor`` (``goose-defaults.yaml``) and
realized by the agent-agnostic credsync engine (the §2d ``synced`` category
view).  The former bespoke ``refresh_secrets`` / ``writeback_secrets`` copies
(an unsanctioned second route) were folded into that one SYNC spec.
"""

from __future__ import annotations

try:
  import yaml
except ImportError:
  raise ImportError("PyYAML is required for the Goose plugin. Install it with: pip install pyyaml")


def parse_yaml(text: str | bytes) -> dict:
  """Parse YAML text as a mapping, returning {} for anything else or an error."""
  try:
    data = yaml.safe_load(text)
  except yaml.YAMLError:
    return {}
  return data if isinstance(data, dict) else {}

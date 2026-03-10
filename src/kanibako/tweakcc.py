"""tweakcc integration: config merging and patched binary caching.

tweakcc customises Claude Code by patching its cli.js bundle.  Kanibako
manages the patching lifecycle — config merging, binary caching on tmpfs,
flock-based reference counting — so that patched variants are transparent
to the user and shared across projects with identical configs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from kanibako.log import get_logger

logger = get_logger("tweakcc")


@dataclass
class TweakccConfig:
    """Resolved tweakcc configuration for a launch.

    Produced by merging agent-level defaults, an optional external config
    file, and per-project inline overrides.
    """

    enabled: bool = False
    config_path: str | None = None  # path to external tweakcc config.json
    overrides: dict = field(default_factory=dict)  # inline [tweakcc] overrides


def load_tweakcc_section(data: dict) -> dict:
    """Extract the ``[tweakcc]`` section from parsed TOML data.

    Returns a plain dict.  Missing section → empty dict.
    """
    return dict(data.get("tweakcc", {}))


def resolve_tweakcc_config(
    agent_tweakcc: dict,
    project_tweakcc: dict | None = None,
) -> TweakccConfig:
    """Merge agent and project tweakcc sections into a resolved config.

    Resolution order (highest wins):
      1. Project ``[tweakcc]`` overrides
      2. Agent ``[tweakcc]`` defaults

    The ``enabled`` and ``config`` keys are extracted; everything else is
    treated as inline overrides passed through to tweakcc.
    """
    merged = dict(agent_tweakcc)
    if project_tweakcc:
        merged.update(project_tweakcc)

    enabled = bool(merged.pop("enabled", False))
    config_path = merged.pop("config", None)
    if config_path is not None:
        config_path = str(config_path)

    return TweakccConfig(
        enabled=enabled,
        config_path=config_path,
        overrides=merged,
    )


def load_external_config(config_path: str | None) -> dict:
    """Load an external tweakcc config.json file.

    Returns empty dict if *config_path* is None or the file doesn't exist.
    """
    if not config_path:
        return {}

    path = Path(config_path).expanduser()
    if not path.is_file():
        logger.debug("External tweakcc config not found: %s", path)
        return {}

    try:
        with open(path) as f:
            data = json.load(f)
        logger.debug("Loaded external tweakcc config: %s", path)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load tweakcc config %s: %s", path, e)
        return {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def build_merged_config(
    tweakcc_cfg: TweakccConfig,
    kanibako_defaults: dict | None = None,
) -> dict:
    """Build the final tweakcc config dict for ``tweakcc --apply``.

    Merge order (highest wins):
      1. Inline overrides from ``tweakcc_cfg.overrides``
      2. External config file (``tweakcc_cfg.config_path``)
      3. Kanibako's own defaults (``kanibako_defaults``)

    Returns a dict ready to be serialized as config.json.
    """
    result: dict = {}

    # Layer 1: kanibako defaults (lowest priority)
    if kanibako_defaults:
        result = _deep_merge(result, kanibako_defaults)

    # Layer 2: external config file
    external = load_external_config(tweakcc_cfg.config_path)
    if external:
        result = _deep_merge(result, external)

    # Layer 3: inline overrides (highest priority)
    if tweakcc_cfg.overrides:
        result = _deep_merge(result, tweakcc_cfg.overrides)

    return result


def write_merged_config(config: dict, output_path: Path) -> None:
    """Write the merged tweakcc config to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)
    logger.debug("Wrote merged tweakcc config: %s", output_path)

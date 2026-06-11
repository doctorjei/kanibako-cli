"""Crab YAML configuration: load, write, and resolve per-crab settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kanibako.config_io import dump_doc, load_doc

# Keys that live directly in [crab] as crab identity (not crab state).
IDENTITY_KEYS = frozenset({"name", "shell", "run_args"})


@dataclass
class CrabConfig:
    """Per-crab configuration loaded from a crab YAML file.

    Sections:
      crab   — identity (name, shell, run_args) plus crab-state knobs
               (model, access, start_mode, autonomous, …)
      env    — raw env vars injected into container
      shared — crab-level shared cache paths
    """

    name: str = ""
    shell: str = "standard"
    run_args: list[str] = field(default_factory=list)
    state: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    shared_caches: dict[str, str] = field(default_factory=dict)
    tweakcc: dict = field(default_factory=dict)


def crabs_dir(data_path: Path, paths_crabs: str = "crabs") -> Path:
    """Return the crabs directory under *data_path*."""
    return data_path / (paths_crabs or "crabs")


def crab_toml_path(
    data_path: Path, crab_id: str, paths_crabs: str = "crabs",
) -> Path:
    """Return the path to a crab's config file."""
    return crabs_dir(data_path, paths_crabs) / f"{crab_id}.yaml"


def load_crab_config(path: Path) -> CrabConfig:
    """Read a crab config file and return a CrabConfig.

    Returns defaults if the file does not exist.
    """
    cfg = CrabConfig()
    if not path.exists():
        return cfg

    data = load_doc(path)

    crab_sec = data.get("crab", {})
    cfg.name = str(crab_sec.get("name", ""))
    cfg.shell = str(crab_sec.get("shell", "standard"))
    raw_args = crab_sec.get("run_args", [])
    cfg.run_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

    cfg.state = {
        k: str(v) for k, v in crab_sec.items() if k not in IDENTITY_KEYS
    }
    cfg.env = {k: str(v) for k, v in data.get("env", {}).items()}
    cfg.shared_caches = {k: str(v) for k, v in data.get("shared", {}).items()}
    cfg.tweakcc = dict(data.get("tweakcc", {}))

    return cfg


def write_crab_config(path: Path, cfg: CrabConfig) -> None:
    """Write a CrabConfig to a YAML file."""
    crab_sec: dict = {
        "name": cfg.name,
        "shell": cfg.shell,
        "run_args": list(cfg.run_args),
    }
    for k, v in cfg.state.items():
        crab_sec[k] = v

    data: dict = {
        "crab": crab_sec,
        "env": dict(cfg.env),
        "shared": dict(cfg.shared_caches),
        "tweakcc": dict(cfg.tweakcc),
    }
    dump_doc(path, data)

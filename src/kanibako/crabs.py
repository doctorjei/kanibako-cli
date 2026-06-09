"""Crab TOML configuration: load, write, and resolve per-crab settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomllib

# Keys that live directly in [crab] as crab identity (not crab state).
IDENTITY_KEYS = frozenset({"name", "shell", "run_args"})


@dataclass
class CrabConfig:
    """Per-crab configuration loaded from a crab TOML file.

    Sections:
      [crab]   — identity (name, shell, run_args) plus crab-state knobs
                 (model, access, start_mode, autonomous, …)
      [env]    — raw env vars injected into container
      [shared] — crab-level shared cache paths
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
    """Return the path to a crab's TOML file."""
    return crabs_dir(data_path, paths_crabs) / f"{crab_id}.toml"


def load_crab_config(path: Path) -> CrabConfig:
    """Read a crab TOML file and return a CrabConfig.

    Returns defaults if the file does not exist.
    """
    cfg = CrabConfig()
    if not path.exists():
        return cfg

    with open(path, "rb") as f:
        data = tomllib.load(f)

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
    """Write a CrabConfig to a TOML file.

    Uses a custom serializer since config._write_toml() cannot handle lists.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    # [crab] section — identity keys plus crab-state knobs
    lines.append("[crab]")
    lines.append(f'name = "{cfg.name}"')
    lines.append(f'shell = "{cfg.shell}"')
    args_str = ", ".join(f'"{a}"' for a in cfg.run_args)
    lines.append(f"run_args = [{args_str}]")
    # Emit model as a hint comment when not explicitly set.
    if "model" not in cfg.state:
        lines.append('# model = "opus"')
    for k, v in cfg.state.items():
        lines.append(f'{k} = "{v}"')
    lines.append("")

    # [env] section
    lines.append("[env]")
    for k, v in cfg.env.items():
        lines.append(f'{k} = "{v}"')
    lines.append("")

    # [shared] section
    lines.append("[shared]")
    for k, v in cfg.shared_caches.items():
        lines.append(f'{k} = "{v}"')
    lines.append("")

    # [tweakcc] section
    lines.append("[tweakcc]")
    if not cfg.tweakcc:
        lines.append("# enabled = false")
    else:
        for k, v in cfg.tweakcc.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {str(v).lower()}")
            else:
                lines.append(f'{k} = "{v}"')
    lines.append("")

    path.write_text("\n".join(lines))

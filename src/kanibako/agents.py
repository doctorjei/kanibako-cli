"""Agent TOML configuration: load, write, and resolve per-agent settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomllib


@dataclass
class AgentConfig:
    """Per-agent configuration loaded from an agent TOML file.

    Sections:
      [agent]  — identity and defaults (name, shell, default_args)
      [state]  — runtime behavior knobs (model, access, etc.)
      [env]    — raw env vars injected into container
      [shared] — agent-level shared cache paths
    """

    name: str = ""
    shell: str = "standard"
    default_args: list[str] = field(default_factory=list)
    state: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    shared_caches: dict[str, str] = field(default_factory=dict)
    tweakcc: dict = field(default_factory=dict)


def agents_dir(data_path: Path, paths_agents: str = "agents") -> Path:
    """Return the agents directory under *data_path*."""
    return data_path / (paths_agents or "agents")


def agent_toml_path(
    data_path: Path, agent_id: str, paths_agents: str = "agents",
) -> Path:
    """Return the path to an agent's TOML file."""
    return agents_dir(data_path, paths_agents) / f"{agent_id}.toml"


def load_agent_config(path: Path) -> AgentConfig:
    """Read an agent TOML file and return an AgentConfig.

    Returns defaults if the file does not exist.
    """
    cfg = AgentConfig()
    if not path.exists():
        return cfg

    with open(path, "rb") as f:
        data = tomllib.load(f)

    agent_sec = data.get("agent", {})
    cfg.name = str(agent_sec.get("name", ""))
    cfg.shell = str(agent_sec.get("shell", "standard"))
    raw_args = agent_sec.get("default_args", [])
    cfg.default_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

    cfg.state = {k: str(v) for k, v in data.get("state", {}).items()}
    cfg.env = {k: str(v) for k, v in data.get("env", {}).items()}
    cfg.shared_caches = {k: str(v) for k, v in data.get("shared", {}).items()}
    cfg.tweakcc = dict(data.get("tweakcc", {}))

    return cfg


def write_agent_config(path: Path, cfg: AgentConfig) -> None:
    """Write an AgentConfig to a TOML file.

    Uses a custom serializer since config._write_toml() cannot handle lists.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    # [agent] section
    lines.append("[agent]")
    lines.append(f'name = "{cfg.name}"')
    lines.append(f'shell = "{cfg.shell}"')
    args_str = ", ".join(f'"{a}"' for a in cfg.default_args)
    lines.append(f"default_args = [{args_str}]")
    lines.append("")

    # [state] section
    lines.append("[state]")
    if not cfg.state:
        lines.append("# model = \"opus\"")
    else:
        # Always emit model as comment if not present
        if "model" not in cfg.state:
            lines.append("# model = \"opus\"")
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

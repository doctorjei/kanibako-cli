"""Agent YAML configuration: load, write, and resolve per-agent settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kanibako.config_io import dump_doc, load_doc

# Keys that live directly in the [agent] section as agent identity (not state).
IDENTITY_KEYS = frozenset({"name", "run_args"})


@dataclass
class AgentConfig:
    """Per-agent configuration loaded from an agent YAML file.

    Sections:
      agent  — identity (name, run_args) plus agent-state knobs
               (model, access, start_mode, autonomous, …)
      env    — raw env vars injected into container
    """

    name: str = ""
    run_args: list[str] = field(default_factory=list)
    state: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    tweakcc: dict = field(default_factory=dict)


def agents_dir(data_path: Path, paths_agents: str = "agents") -> Path:
    """Return the agents directory under *data_path*."""
    return data_path / (paths_agents or "agents")


def agent_config_path(
    data_path: Path, agent_id: str, paths_agents: str = "agents",
) -> Path:
    """Return the path to an agent's config file."""
    return agents_dir(data_path, paths_agents) / f"{agent_id}.yaml"


def load_agent_config(path: Path) -> AgentConfig:
    """Read an agent config file and return an AgentConfig.

    Returns defaults if the file does not exist.
    """
    cfg = AgentConfig()
    if not path.exists():
        return cfg

    data = load_doc(path)

    agent_sec = data.get("agent", {})
    cfg.name = str(agent_sec.get("name", ""))
    raw_args = agent_sec.get("run_args", [])
    cfg.run_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

    cfg.state = {
        k: str(v) for k, v in agent_sec.items() if k not in IDENTITY_KEYS
    }
    cfg.env = {k: str(v) for k, v in data.get("env", {}).items()}
    cfg.tweakcc = dict(data.get("tweakcc", {}))

    return cfg


def write_agent_config(path: Path, cfg: AgentConfig) -> None:
    """Write an AgentConfig to a YAML file."""
    agent_sec: dict = {
        "name": cfg.name,
        "run_args": list(cfg.run_args),
    }
    for k, v in cfg.state.items():
        agent_sec[k] = v

    data: dict = {
        "agent": agent_sec,
        "env": dict(cfg.env),
        "tweakcc": dict(cfg.tweakcc),
    }
    dump_doc(path, data)

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
      agent     — identity (name, run_args) plus agent-state knobs
                  (model, auto_approve, allow_helpers, endpoint, …)
      env       — raw env vars injected into container (VAR -> value)
      env_file  — env-from-file pointers (VAR -> host PATH): at launch the
                  file's contents become the env var's VALUE (secret stays in
                  the host file, only the path is stored — spec §2d).
    """

    name: str = ""
    run_args: list[str] = field(default_factory=list)
    state: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    env_file: dict[str, str] = field(default_factory=dict)
    tweakcc: dict = field(default_factory=dict)


def agents_dir(data_path: Path, paths_agents: str = "agents") -> Path:
    """Return the agents directory under *data_path*."""
    return data_path / (paths_agents or "agents")


def agent_settings_path(agents_root: Path, agent_id: str) -> Path:
    """Return ``@meta.agent.<agent>.settings`` for *agent_id*.

    The per-agent SETTINGS cascade file lives INSIDE the per-agent store dir
    (``@meta.agent.<agent>.path`` = ``agents/<agent>/``) as ``settings.yaml``
    — NOT the old sibling ``agents/<agent>.yaml`` file (D-2026-06-22).  This
    parallels the per-agent template dir ``agents/<agent>/template`` and the
    Part-3 ``agents/<agent>/{plugins,cache}`` stores.
    """
    return agents_root / agent_id / "settings.yaml"


def agent_config_path(
    data_path: Path, agent_id: str, paths_agents: str = "agents",
) -> Path:
    """Return the path to an agent's config (settings) file.

    Convenience wrapper for callers that hold a *data_path* rather than the
    resolved agents root; delegates to :func:`agent_settings_path`.
    """
    return agent_settings_path(agents_dir(data_path, paths_agents), agent_id)


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
    # env_file: VAR -> host PATH pointer (the token file). Stored as a plain
    # string path; the file's CONTENTS (the secret) are never persisted here —
    # they are read into the container env only at launch (spec §2d).
    cfg.env_file = {k: str(v) for k, v in data.get("env_file", {}).items()}
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
        "env_file": dict(cfg.env_file),
        "tweakcc": dict(cfg.tweakcc),
    }
    # The settings file lives inside the per-agent store dir
    # (agents/<agent>/settings.yaml); ensure that dir exists.
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(path, data)

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
      agent        — identity (name, run_args) plus agent-state knobs
                     (model, auto_approve, allow_helpers, endpoint, …). The
                     per-node DISCRIMINATED sub-table ``agent.<node>.secret_path``
                     also lives here (see *secret_path* below).
      env          — raw env vars injected into container (VAR -> value)
      secret_path  — the SECRET category (spec §2a, 2026-07-06; RENAMED from the
                     rc0-rc2 ``env_file``): VAR -> host PATH pointer to secret
                     material (e.g. a 0600 bearer-token file). Stored DISCRIMINATED
                     under ``agent.<node>.secret_path.<VAR>`` (the SAME first-class
                     category shape ``config set agent.<node>.secret_path.<VAR>``
                     writes and ``_agent_partial`` reads into the launch cascade),
                     so it resolves through ``system → workset → box → agent``
                     precedence. The value is a PATH only — at launch it is ro-bind-
                     mounted arm's-length + exported IN-BOX; kanibako NEVER reads the
                     secret VALUE (never in the snapshot/keystore/logs/argv).
    """

    name: str = ""
    run_args: list[str] = field(default_factory=list)
    state: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    secret_path: dict[str, str] = field(default_factory=dict)
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

    # The node is the per-agent store dir name (agents/<node>/settings.yaml), which
    # IS the cascade discriminator ``_agent_partial`` reads (agent_name == node). The
    # DISCRIMINATED ``agent.<node>.secret_path`` sub-table (first-class category)
    # lives under the same ``agent:`` table as the flat state — read it by node.
    node = path.parent.name

    agent_sec = data.get("agent", {})
    if not isinstance(agent_sec, dict):
        agent_sec = {}
    cfg.name = str(agent_sec.get("name", ""))
    raw_args = agent_sec.get("run_args", [])
    cfg.run_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

    # Flat state = the SCALAR agent-state knobs. Exclude identity keys AND any
    # dict-valued entry: the discriminated ``<node>:`` sub-table (secret_path, node
    # binds) is a dict and is NOT flat state — it rides ``_agent_partial``, not the
    # ``_agent_state_partial`` state channel.
    cfg.state = {
        k: str(v)
        for k, v in agent_sec.items()
        if k not in IDENTITY_KEYS and not isinstance(v, dict)
    }
    cfg.env = {k: str(v) for k, v in data.get("env", {}).items()}
    # secret_path: VAR -> host PATH pointer, read from the DISCRIMINATED
    # ``agent.<node>.secret_path`` sub-table (spec §2a SECRET category). Stored as a
    # plain string path; the file's CONTENTS (the secret) are never persisted here
    # nor read — they are ro-mounted + exported IN-BOX only at launch.
    node_sub = agent_sec.get(node, {})
    secret_sub = node_sub.get("secret_path", {}) if isinstance(node_sub, dict) else {}
    cfg.secret_path = {
        k: str(v) for k, v in secret_sub.items()
    } if isinstance(secret_sub, dict) else {}
    cfg.tweakcc = dict(data.get("tweakcc", {}))

    return cfg


def write_agent_config(path: Path, cfg: AgentConfig) -> None:
    """Write an AgentConfig to a YAML file."""
    node = path.parent.name
    agent_sec: dict = {
        "name": cfg.name,
        "run_args": list(cfg.run_args),
    }
    for k, v in cfg.state.items():
        agent_sec[k] = v
    # secret_path (spec §2a SECRET category) is stored DISCRIMINATED under the
    # ``agent.<node>.secret_path`` sub-table — the SAME first-class category location
    # ``config set agent.<node>.secret_path.<VAR>`` writes and ``_agent_partial``
    # reads into the launch cascade — so the persona-adopted token pointer resolves
    # through the cascade like any other agent-tier category. Only materialized when
    # non-empty (sparse). NO ``env_file`` section (RENAMED, clean break — rc0-rc2 only).
    if cfg.secret_path:
        agent_sec[node] = {"secret_path": dict(cfg.secret_path)}

    data: dict = {
        "agent": agent_sec,
        "env": dict(cfg.env),
        "tweakcc": dict(cfg.tweakcc),
    }
    # The settings file lives inside the per-agent store dir
    # (agents/<agent>/settings.yaml); ensure that dir exists.
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(path, data)

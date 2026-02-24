"""kanibako shared: manage shared cache directories."""

from __future__ import annotations

import argparse
import sys

from kanibako.agents import agents_dir, load_agent_config
from kanibako.config import config_file_path, load_config
from kanibako.paths import xdg, load_std_paths


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "shared",
        help="Manage shared cache directories (init, list)",
        description="Create and inspect shared cache directories.",
    )
    ss = p.add_subparsers(dest="shared_command", metavar="COMMAND")

    # kanibako shared init <name>
    init_p = ss.add_parser(
        "init",
        help="Create a shared cache directory",
        description="Create a shared cache directory so it will be mounted on next launch.",
    )
    init_p.add_argument("name", help="Cache name (e.g. pip, npm, cargo-reg)")
    init_p.add_argument(
        "--agent", default=None, metavar="ID",
        help="Create an agent-level cache instead of a global cache",
    )
    init_p.set_defaults(func=run_init)

    # kanibako shared list (default)
    list_p = ss.add_parser(
        "list",
        help="List configured shared caches and their status (default)",
        description="Show all configured shared caches and whether their directories exist.",
    )
    list_p.set_defaults(func=run_list)

    p.set_defaults(func=run_list)


def run_init(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    name = args.name
    agent_id = args.agent
    shared_base = std.data_path / config.paths_shared

    if agent_id:
        cache_dir = shared_base / agent_id / name
    else:
        cache_dir = shared_base / "global" / name

    if cache_dir.is_dir():
        print(f"Already exists: {cache_dir}")
        return 0

    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"Created: {cache_dir}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    std = load_std_paths(config)

    shared_base = std.data_path / config.paths_shared
    found_any = False

    # Global caches from [shared] in kanibako.toml.
    if config.shared_caches:
        found_any = True
        print(f"{'NAME':<18} {'STATUS':<10} {'CONTAINER PATH'}")
        for cache_name, container_rel in sorted(config.shared_caches.items()):
            host_dir = shared_base / "global" / cache_name
            status = "ready" if host_dir.is_dir() else "missing"
            print(f"{cache_name:<18} {status:<10} {container_rel}")

    # Agent-level caches from agent TOMLs.
    a_dir = agents_dir(std.data_path, config.paths_agents)
    if a_dir.is_dir():
        for toml_file in sorted(a_dir.iterdir()):
            if not toml_file.suffix == ".toml":
                continue
            agent_id = toml_file.stem
            agent_cfg = load_agent_config(toml_file)
            if not agent_cfg.shared_caches:
                continue

            found_any = True
            print(f"\nAgent: {agent_id}")
            print(f"  {'NAME':<16} {'STATUS':<10} {'CONTAINER PATH'}")
            for cache_name, container_rel in sorted(agent_cfg.shared_caches.items()):
                host_dir = shared_base / agent_id / cache_name
                status = "ready" if host_dir.is_dir() else "missing"
                print(f"  {cache_name:<16} {status:<10} {container_rel}")

    if not found_any:
        print("No shared caches configured.")
        print("Add entries to [shared] in kanibako.toml or agent TOML files.")
        return 0

    # Print hint about missing caches.
    print()
    print("Caches marked 'missing' won't be mounted. Use 'kanibako shared init <name>' to create.")
    return 0

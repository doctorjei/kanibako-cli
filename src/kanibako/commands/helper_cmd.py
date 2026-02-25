"""kanibako helper: spawn and manage child kanibako instances."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from kanibako.config import config_file_path, load_config
from kanibako.helpers import (
    SpawnBudget,
    check_spawn_allowed,
    child_budget,
    create_broadcast_dirs,
    create_helper_dirs,
    create_peer_channels,
    link_broadcast,
    read_spawn_config,
    resolve_init_script,
    resolve_spawn_budget,
    write_spawn_config,
)
from kanibako.paths import xdg, load_std_paths


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "helper",
        help="Spawn and manage child kanibako instances",
        description="Spawn, list, stop, cleanup, and respawn helper instances.",
    )
    ss = p.add_subparsers(dest="helper_command", metavar="COMMAND")

    # kanibako helper spawn
    spawn_p = ss.add_parser(
        "spawn",
        help="Spawn a new helper instance",
        description="Create and launch a new child kanibako instance.",
    )
    spawn_p.add_argument(
        "--depth", type=int, default=None,
        help="Spawn depth limit for the child (only if no config override)",
    )
    spawn_p.add_argument(
        "--breadth", type=int, default=None,
        help="Spawn breadth limit for the child (only if no config override)",
    )
    spawn_p.add_argument(
        "--model", default=None, metavar="VARIANT",
        help="Override model variant for the child (e.g. sonnet)",
    )
    spawn_p.set_defaults(func=run_spawn)

    # kanibako helper list
    list_p = ss.add_parser(
        "list",
        help="List active helpers",
        description="Show all helper instances and their status.",
    )
    list_p.set_defaults(func=run_list)

    # kanibako helper stop <N>
    stop_p = ss.add_parser(
        "stop",
        help="Stop a helper instance",
        description="Stop a running helper container.",
    )
    stop_p.add_argument("number", type=int, help="Helper number to stop")
    stop_p.set_defaults(func=run_stop)

    # kanibako helper cleanup <N>
    cleanup_p = ss.add_parser(
        "cleanup",
        help="Stop and remove a helper",
        description="Stop a helper and remove its directory structure and peer channels.",
    )
    cleanup_p.add_argument("number", type=int, help="Helper number to clean up")
    cleanup_p.set_defaults(func=run_cleanup)

    # kanibako helper respawn <N>
    respawn_p = ss.add_parser(
        "respawn",
        help="Relaunch a stopped helper",
        description="Relaunch a previously stopped helper (same number, same directories).",
    )
    respawn_p.add_argument("number", type=int, help="Helper number to respawn")
    respawn_p.set_defaults(func=run_respawn)

    p.set_defaults(func=run_list)


def _helpers_dir() -> Path:
    """Return the helpers directory for the current session."""
    return Path.home() / "helpers"


def _ro_spawn_config_path(helpers_dir: Path, helper_num: int) -> Path:
    """Return the path to a helper's RO spawn config."""
    return helpers_dir / str(helper_num) / "spawn.toml"


def _state_path(helpers_dir: Path, helper_num: int) -> Path:
    """Return the path to a helper's state file."""
    return helpers_dir / str(helper_num) / "state.json"


def _read_state(helpers_dir: Path, helper_num: int) -> dict:
    """Read a helper's state file.  Returns empty dict if absent."""
    path = _state_path(helpers_dir, helper_num)
    if not path.is_file():
        return {}
    with open(path) as f:
        return json.load(f)


def _write_state(helpers_dir: Path, helper_num: int, state: dict) -> None:
    """Write a helper's state file."""
    path = _state_path(helpers_dir, helper_num)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def _get_existing_helpers(helpers_dir: Path) -> list[int]:
    """Scan helpers/ for existing helper directories (numeric names)."""
    if not helpers_dir.is_dir():
        return []
    result = []
    for child in helpers_dir.iterdir():
        if child.is_dir() and child.name.isdigit():
            result.append(int(child.name))
    return sorted(result)


def _next_helper_number(existing: list[int], budget: SpawnBudget) -> int:
    """Determine the next helper number (first unused slot)."""
    used = set(existing)
    # Sequentially find the next unused number starting from 1
    # (0 is reserved for the director)
    n = 1
    while n in used:
        n += 1
    return n


def run_spawn(args: argparse.Namespace) -> int:
    """Spawn a new helper instance."""
    helpers_dir = _helpers_dir()

    # Resolve own spawn budget
    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    host_budget = None
    ro_budget = None

    # Check for RO spawn config (set by parent, if we are a helper)
    own_ro_config = Path.home() / "spawn.toml"
    if own_ro_config.is_file():
        ro_budget = read_spawn_config(own_ro_config)

    # Check host config
    if config_file.is_file():
        host_budget = read_spawn_config(config_file)

    budget = resolve_spawn_budget(
        ro_budget, host_budget, args.depth, args.breadth,
    )

    # Check if spawning is allowed
    existing = _get_existing_helpers(helpers_dir)
    error = check_spawn_allowed(budget, len(existing))
    if error:
        print(f"Cannot spawn: {error}", file=sys.stderr)
        return 1

    # Determine helper number
    helper_num = _next_helper_number(existing, budget)

    # Create directory structure
    create_helper_dirs(helpers_dir, helper_num)
    create_broadcast_dirs(helpers_dir)
    create_peer_channels(helpers_dir, helper_num, existing)
    link_broadcast(helpers_dir, helper_num)

    # Write RO spawn config for the child
    child_cfg = child_budget(budget)
    write_spawn_config(
        _ro_spawn_config_path(helpers_dir, helper_num),
        child_cfg,
    )

    # Copy init script into helper's scripts/
    init_script = resolve_init_script(
        Path.home() / "playbook" / "scripts",
    )
    dest_scripts = helpers_dir / str(helper_num) / "playbook" / "scripts"
    dest_init = dest_scripts / "helper-init.sh"
    if not dest_init.exists():
        shutil.copy2(init_script, dest_init)

    # Write helper state
    state = {
        "status": "spawned",
        "model": args.model,
        "depth": child_cfg.depth,
        "breadth": child_cfg.breadth,
        "peers": existing,
    }
    _write_state(helpers_dir, helper_num, state)

    print(f"Spawned helper {helper_num}")
    if args.model:
        print(f"  model: {args.model}")
    print(f"  depth: {child_cfg.depth}, breadth: {child_cfg.breadth}")
    print(f"  peers: {existing}")
    # Container launch will be wired in a future phase
    return 0


def run_list(args: argparse.Namespace) -> int:
    """List active helpers."""
    helpers_dir = _helpers_dir()
    existing = _get_existing_helpers(helpers_dir)

    if not existing:
        print("No helpers.")
        return 0

    print(f"{'NUM':<6} {'STATUS':<10} {'MODEL':<10} {'DEPTH':<6} {'PEERS'}")
    for num in existing:
        state = _read_state(helpers_dir, num)
        status = state.get("status", "unknown")
        model = state.get("model") or "-"
        depth = state.get("depth", "?")
        # Count peer symlinks
        peers_dir = helpers_dir / str(num) / "peers"
        peer_count = 0
        if peers_dir.is_dir():
            peer_count = sum(1 for p in peers_dir.iterdir() if p.is_symlink())
        print(f"{num:<6} {status:<10} {model:<10} {depth!s:<6} {peer_count} ch")
    return 0


def run_stop(args: argparse.Namespace) -> int:
    """Stop a helper instance."""
    # TODO (phase 4A): container stop
    print(f"Stopping helper {args.number}... (not yet implemented)")
    return 0


def run_cleanup(args: argparse.Namespace) -> int:
    """Stop and remove a helper."""
    # TODO (phase 4A): full implementation
    print(f"Cleaning up helper {args.number}... (not yet implemented)")
    return 0


def run_respawn(args: argparse.Namespace) -> int:
    """Relaunch a stopped helper."""
    # TODO (phase 4B): full implementation
    print(f"Respawning helper {args.number}... (not yet implemented)")
    return 0

"""kanibako helper: spawn and manage child kanibako instances."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from kanibako.config import config_file_path
from kanibako.helpers import (
    SpawnBudget,
    check_spawn_allowed,
    child_budget,
    create_broadcast_dirs,
    create_helper_dirs,
    create_peer_channels,
    link_broadcast,
    read_spawn_config,
    remove_helper_dirs,
    resolve_init_script,
    resolve_spawn_budget,
    write_spawn_config,
)
from kanibako.paths import xdg


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
    cleanup_p.add_argument(
        "--cascade", action="store_true",
        help="Also remove all descendant helpers recursively",
    )
    cleanup_p.set_defaults(func=run_cleanup)

    # kanibako helper respawn <N>
    respawn_p = ss.add_parser(
        "respawn",
        help="Relaunch a stopped helper",
        description="Relaunch a previously stopped helper (same number, same directories).",
    )
    respawn_p.add_argument("number", type=int, help="Helper number to respawn")
    respawn_p.set_defaults(func=run_respawn)

    # kanibako helper send <N> <message>
    send_p = ss.add_parser(
        "send",
        help="Send a message to a helper",
        description="Send a message to a specific helper by number.",
    )
    send_p.add_argument("number", type=int, help="Helper number to send to")
    send_p.add_argument("message", help="Message text to send")
    send_p.set_defaults(func=run_send)

    # kanibako helper broadcast <message>
    bcast_p = ss.add_parser(
        "broadcast",
        help="Broadcast a message to all helpers",
        description="Send a message to all connected helpers.",
    )
    bcast_p.add_argument("message", help="Message text to broadcast")
    bcast_p.set_defaults(func=run_broadcast)

    # kanibako helper log
    log_p = ss.add_parser(
        "log",
        help="View inter-agent message log",
        description="Display the JSONL message log in human-readable format.",
    )
    log_p.add_argument(
        "--follow", "-f", action="store_true",
        help="Follow log output (like tail -f)",
    )
    log_p.add_argument(
        "--from", type=int, default=None, dest="from_helper",
        help="Filter messages from a specific helper number",
    )
    log_p.add_argument(
        "--last", type=int, default=None,
        help="Show only the last N entries",
    )
    log_p.set_defaults(func=run_log)

    p.set_defaults(func=run_list)


def _helpers_dir() -> Path:
    """Return the helpers directory for the current session."""
    return Path.home() / "helpers"


def _socket_path() -> Path:
    """Return the path to the helper hub socket."""
    return Path.home() / ".kanibako" / "helper.sock"


def _check_helpers_enabled() -> bool:
    """Check if the helper socket exists (helpers are enabled)."""
    return _socket_path().exists()


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

    # Launch container via socket if helpers are enabled
    container_name = None
    if _check_helpers_enabled():
        from kanibako.helper_client import send_request
        try:
            resp = send_request(_socket_path(), {
                "action": "spawn",
                "helper_num": helper_num,
                "model": args.model,
                "helpers_dir": str(helpers_dir),
            })
            if resp.get("status") == "ok":
                container_name = resp.get("container_name")
                state["status"] = "running"
                state["container_name"] = container_name
            else:
                state["status"] = "failed"
                state["error"] = resp.get("message", "unknown error")
                print(
                    f"Warning: container launch failed: {resp.get('message')}",
                    file=sys.stderr,
                )
        except Exception as e:
            state["status"] = "failed"
            state["error"] = str(e)
            print(f"Warning: container launch failed: {e}", file=sys.stderr)
    else:
        state["status"] = "spawned"

    _write_state(helpers_dir, helper_num, state)

    print(f"Spawned helper {helper_num}")
    if args.model:
        print(f"  model: {args.model}")
    print(f"  depth: {child_cfg.depth}, breadth: {child_cfg.breadth}")
    print(f"  peers: {existing}")
    if container_name:
        print(f"  container: {container_name}")
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
    helpers_dir = _helpers_dir()
    helper_num = args.number
    helper_root = helpers_dir / str(helper_num)

    if not helper_root.is_dir():
        print(f"Helper {helper_num} does not exist.", file=sys.stderr)
        return 1

    state = _read_state(helpers_dir, helper_num)
    if state.get("status") == "stopped":
        print(f"Helper {helper_num} is already stopped.")
        return 0

    # Stop container via socket if running
    container_name = state.get("container_name")
    if container_name and _check_helpers_enabled():
        from kanibako.helper_client import send_request
        try:
            send_request(_socket_path(), {
                "action": "stop",
                "container_name": container_name,
            })
        except Exception:
            pass  # Best-effort stop

    state["status"] = "stopped"
    _write_state(helpers_dir, helper_num, state)
    print(f"Stopped helper {helper_num}.")
    return 0


def run_cleanup(args: argparse.Namespace) -> int:
    """Stop and remove a helper."""
    helpers_dir = _helpers_dir()
    helper_num = args.number
    helper_root = helpers_dir / str(helper_num)

    if not helper_root.is_dir():
        print(f"Helper {helper_num} does not exist.", file=sys.stderr)
        return 1

    # Stop container if running
    state = _read_state(helpers_dir, helper_num)
    container_name = state.get("container_name")
    if container_name and _check_helpers_enabled():
        from kanibako.helper_client import send_request
        try:
            send_request(_socket_path(), {
                "action": "stop",
                "container_name": container_name,
            })
        except Exception:
            pass

    cascade = getattr(args, "cascade", False)
    if cascade:
        removed = _cascade_cleanup(helpers_dir, helper_num)
        print(f"Cleaned up helper {helper_num} and {len(removed) - 1} descendant(s).")
    else:
        existing = _get_existing_helpers(helpers_dir)
        siblings = [n for n in existing if n != helper_num]
        remove_helper_dirs(helpers_dir, helper_num, siblings)
        print(f"Cleaned up helper {helper_num}.")
    return 0


def _cascade_cleanup(helpers_dir: Path, helper_num: int) -> list[int]:
    """Recursively clean up a helper and all its descendants.

    Returns the list of all helper numbers that were removed.
    """
    removed = []
    # Check if this helper has its own helpers/ subtree (children)
    child_helpers_dir = helpers_dir / str(helper_num) / "helpers"
    if child_helpers_dir.is_dir():
        children = _get_existing_helpers(child_helpers_dir)
        for child in children:
            removed.extend(_cascade_cleanup(child_helpers_dir, child))

    # Now clean up this helper itself
    existing = _get_existing_helpers(helpers_dir)
    siblings = [n for n in existing if n != helper_num]
    remove_helper_dirs(helpers_dir, helper_num, siblings)
    removed.append(helper_num)
    return removed


def run_respawn(args: argparse.Namespace) -> int:
    """Relaunch a stopped helper."""
    helpers_dir = _helpers_dir()
    helper_num = args.number
    helper_root = helpers_dir / str(helper_num)

    if not helper_root.is_dir():
        print(f"Helper {helper_num} does not exist.", file=sys.stderr)
        return 1

    state = _read_state(helpers_dir, helper_num)
    if state.get("status") != "stopped":
        status = state.get("status", "unknown")
        print(
            f"Helper {helper_num} is {status}, not stopped. "
            f"Only stopped helpers can be respawned.",
            file=sys.stderr,
        )
        return 1

    # Relaunch container via socket if helpers are enabled
    if _check_helpers_enabled():
        from kanibako.helper_client import send_request
        try:
            resp = send_request(_socket_path(), {
                "action": "spawn",
                "helper_num": helper_num,
                "model": state.get("model"),
                "helpers_dir": str(helpers_dir),
            })
            if resp.get("status") == "ok":
                state["status"] = "running"
                state["container_name"] = resp.get("container_name")
            else:
                state["status"] = "failed"
                print(
                    f"Warning: container relaunch failed: {resp.get('message')}",
                    file=sys.stderr,
                )
        except Exception as e:
            state["status"] = "failed"
            print(f"Warning: container relaunch failed: {e}", file=sys.stderr)
    else:
        state["status"] = "respawned"

    _write_state(helpers_dir, helper_num, state)
    print(f"Respawned helper {helper_num}.")
    return 0


def run_send(args: argparse.Namespace) -> int:
    """Send a message to a specific helper."""
    if not _check_helpers_enabled():
        print("Helpers not enabled (no socket found).", file=sys.stderr)
        return 1

    from kanibako.helper_client import send_request
    try:
        resp = send_request(_socket_path(), {
            "action": "send",
            "to": args.number,
            "payload": {"text": args.message},
        })
        if resp.get("status") != "ok":
            print(f"Send failed: {resp.get('message')}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Send failed: {e}", file=sys.stderr)
        return 1

    print(f"Message sent to helper {args.number}.")
    return 0


def run_broadcast(args: argparse.Namespace) -> int:
    """Broadcast a message to all helpers."""
    if not _check_helpers_enabled():
        print("Helpers not enabled (no socket found).", file=sys.stderr)
        return 1

    from kanibako.helper_client import send_request
    try:
        resp = send_request(_socket_path(), {
            "action": "broadcast",
            "payload": {"text": args.message},
        })
        if resp.get("status") != "ok":
            print(f"Broadcast failed: {resp.get('message')}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Broadcast failed: {e}", file=sys.stderr)
        return 1

    print("Message broadcast to all helpers.")
    return 0


def _log_path() -> Path:
    """Return the path to the helper message log file."""
    return Path.home() / ".kanibako" / "helper-messages.jsonl"


def run_log(args: argparse.Namespace) -> int:
    """Display the inter-agent message log."""
    log_file = _log_path()

    if not log_file.is_file():
        print("No helper message log found.", file=sys.stderr)
        return 1

    follow = getattr(args, "follow", False)
    from_helper = getattr(args, "from_helper", None)
    last_n = getattr(args, "last", None)

    if follow:
        return _follow_log(log_file, from_helper)

    entries = _read_log_entries(log_file)

    # Filter by helper
    if from_helper is not None:
        entries = [
            e for e in entries
            if e.get("from") == from_helper or e.get("helper") == from_helper
        ]

    # Last N entries
    if last_n is not None and last_n > 0:
        entries = entries[-last_n:]

    if not entries:
        print("No log entries.")
        return 0

    for entry in entries:
        print(_format_log_entry(entry))
    return 0


def _read_log_entries(log_file: Path) -> list[dict]:
    """Read all JSONL entries from the log file."""
    entries = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def _format_log_entry(entry: dict) -> str:
    """Format a single log entry for display."""
    ts = entry.get("ts", "")
    # Extract time portion (HH:MM:SS)
    if "T" in ts:
        time_part = ts.split("T")[1].split(".")[0].split("+")[0]
    else:
        time_part = ts[:8] if len(ts) >= 8 else ts

    entry_type = entry.get("type", "")

    if entry_type == "message":
        sender = entry.get("from", "?")
        recipient = entry.get("to", "?")
        to_str = "*" if recipient == "all" else str(recipient)
        text = entry.get("payload", {}).get("text", "")
        return f"{time_part}  [{sender} → {to_str}]  {text}"
    elif entry_type == "control":
        event = entry.get("event", "?")
        helper = entry.get("helper", "")
        extra = ""
        if "model" in entry and entry["model"]:
            extra = f" (model={entry['model']})"
        return f"{time_part}  [{event}] helper {helper}{extra}"
    else:
        return f"{time_part}  {json.dumps(entry)}"


def _follow_log(log_file: Path, from_helper: int | None) -> int:
    """Follow the log file, printing new entries as they appear."""
    import time

    # Print existing entries first
    entries = _read_log_entries(log_file)
    if from_helper is not None:
        entries = [
            e for e in entries
            if e.get("from") == from_helper or e.get("helper") == from_helper
        ]
    for entry in entries:
        print(_format_log_entry(entry))

    # Then tail the file
    with open(log_file) as f:
        f.seek(0, 2)  # seek to end
        try:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if from_helper is not None:
                    if entry.get("from") != from_helper and entry.get("helper") != from_helper:
                        continue
                print(_format_log_entry(entry))
        except KeyboardInterrupt:
            pass
    return 0

"""kanibako helper: spawn and manage child kanibako instances."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
from pathlib import Path

from kanibako.channels.helpers import (
    HELPER_SCRIPTS_RELPATH,
    INIT_SCRIPT_NAME,
    PARENT_SCRIPTS_RELPATH,
    SPAWN_CONFIG_FILENAME,
    SpawnBudget,
    check_spawn_allowed,
    child_budget,
    create_broadcast_dirs,
    create_helper_dirs,
    create_peer_channels,
    effective_breadth,
    link_broadcast,
    read_helper_number,
    read_spawn_budget,
    remove_helper_dirs,
    resolve_init_script,
    resolve_spawn_budget,
    write_spawn_budget,
)
from kanibako.settings.config import system_settings_path
from kanibako.settings.settings_resolve import BOX_PINNED_STATE_RELPATH


def add_helper_subparsers(p: argparse.ArgumentParser) -> None:
    """Register helper subcommands on the given parser.

    Called by box/_parser.py to nest helpers under ``kanibako box helper``.
    """
    ss = p.add_subparsers(dest="helper_command", metavar="COMMAND")

    # helper spawn
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

    # helper list
    list_p = ss.add_parser(
        "list",
        aliases=["ls"],
        help="List active helpers",
        description="Show all helper instances and their status.",
    )
    list_p.add_argument(
        "-q", "--quiet", action="store_true",
        help="Print only helper numbers, one per line",
    )
    list_p.set_defaults(func=run_list)

    # helper stop <N>
    stop_p = ss.add_parser(
        "stop",
        help="Stop a helper instance",
        description="Stop a running helper container.",
    )
    stop_p.add_argument("number", type=int, help="Helper number to stop")
    stop_p.set_defaults(func=run_stop)

    # helper cleanup <N>
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

    # helper respawn <N>
    respawn_p = ss.add_parser(
        "respawn",
        help="Relaunch a stopped helper",
        description="Relaunch a previously stopped helper (same number, same directories).",
    )
    respawn_p.add_argument("number", type=int, help="Helper number to respawn")
    respawn_p.set_defaults(func=run_respawn)

    # helper send <N> <message>
    send_p = ss.add_parser(
        "send",
        help="Send a message to a helper",
        description="Send a message to a specific helper by number.",
    )
    send_p.add_argument("number", type=int, help="Helper number to send to")
    send_p.add_argument("message", help="Message text to send")
    send_p.set_defaults(func=run_send)

    # helper broadcast <message>
    bcast_p = ss.add_parser(
        "broadcast",
        help="Broadcast a message to all helpers",
        description="Send a message to all connected helpers.",
    )
    bcast_p.add_argument("message", help="Message text to broadcast")
    bcast_p.set_defaults(func=run_broadcast)

    # helper log
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

    # helper register <N>  (used by helper-init.sh, hidden from help)
    register_p = ss.add_parser(
        "register",
        help=argparse.SUPPRESS,
        description="Register this helper with the hub (used by helper-init.sh).",
    )
    register_p.add_argument("number", type=int, help="Helper number to register")
    register_p.set_defaults(func=run_register)

    p.set_defaults(func=run_list)


def _helpers_dir() -> Path:
    """Return the helpers directory for the current session."""
    return Path.home() / "helpers"


def _socket_path() -> Path:
    """Return the path to the helper hub socket.

    Under the FIXED pinned root
    (:data:`~kanibako.settings.settings_resolve.BOX_PINNED_STATE_RELPATH`), NOT the
    box's ``$XDG_STATE_HOME``: that is where the mount actually lands, because a
    mount dest is fixed host-side before the box is live.  Read IN-BOX, so
    ``Path.home()`` is the box's own home.  (``$XDG_STATE_HOME/kanibako`` is a
    symlink onto this dir after boot, so reading through either works — this reads
    the real thing, which is correct even if the projection was skipped.)
    """
    return Path.home() / BOX_PINNED_STATE_RELPATH / "helper.sock"


#: How long the liveness probe waits for the hub to take a connection.
_PROBE_TIMEOUT = 2.0


def _check_helpers_enabled() -> bool:
    """True only when a hub LISTENS on the helper socket: a connect is taken.

    The path existing is not enough.  An ephemeral start pre-creates the socket's
    mount stub (a plain file) in the persistent home, where it outlives the start,
    and a hub that died leaves its socket file behind; with either, nothing could
    launch a helper.
    """
    path = _socket_path()
    if not path.is_socket():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(_PROBE_TIMEOUT)
        probe.connect(str(path))
    except OSError:
        return False
    finally:
        probe.close()
    return True


#: Why a spawn or respawn refuses in a box with no hub: nothing could launch the helper.
#: A hub runs only on a start that is not persistent, with helpers allowed
#: (``commands.start``: a persistent start forces ``no_helpers``).
_NO_HUB = (
    "Cannot %s: this box runs no helper hub. "
    "A hub runs only on an ephemeral start (`kanibako start --ephemeral`) with helpers allowed."
)

#: Why a spawn fails when the hub could not start the helper's container.
_SPAWN_FAILED = (
    "Cannot spawn: helper %d did not launch: %s. Its directory is kept, marked failed; "
    "`kanibako box helper cleanup %d` removes it."
)

#: Why a respawn fails.  The helper ran before, so its directory holds its work: it
#: stays stopped and respawnable, and ``cleanup`` (which deletes it) is not the cure.
_RESPAWN_FAILED = (
    "Cannot respawn: helper %d did not launch: %s. It stays stopped, the reason in its "
    "state.json; fix the cause and retry `kanibako box helper respawn %d`."
)


def _own_helper_number() -> int | None:
    """This box's own helper number, or ``None`` in the director.

    Read from the box's bound spawn document (``meta.box.helper_num``, which the hub
    writes at each helper launch), so a ``podman exec`` shell and a user's own init
    script get the same answer.  Raises :class:`KanibakoError` (the CLI's ``Error:``
    line, rc 1) when the document is unreadable or the value is malformed."""
    return read_helper_number(Path.home() / SPAWN_CONFIG_FILENAME)


def _launch(request: dict) -> tuple[str | None, str | None]:
    """Ask the hub to start a helper's container: ``(container_name, None)`` when it
    started, ``(None, reason)`` when it did not.  The request names this box as the
    ``requester`` (``None`` in the director), so the hub launches the helper under
    this box's home and not the director's."""
    from kanibako.channels.helper_client import send_request
    request = {**request, "requester": _own_helper_number()}
    try:
        resp = send_request(_socket_path(), request)
    except Exception as e:
        return None, str(e)
    if resp.get("status") == "ok":
        return resp.get("container_name"), None
    return None, str(resp.get("message", "unknown error"))


def _ro_spawn_config_path(helpers_dir: Path, helper_num: int) -> Path:
    """The settings document carrying the budget this parent hands helper *helper_num*.

    Written here, mounted RO at the spawn-budget bind's dest by the hub, and read back
    there through the same ``system.helpers.*`` keys any settings file carries.
    """
    return helpers_dir / str(helper_num) / SPAWN_CONFIG_FILENAME


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


def _next_helper_number(existing: list[int], budget: SpawnBudget, own: int = 0) -> int:
    """The first unused number in this box's block: ``own*B+1 .. own*B+B``.

    keyspec ``meta.box.helper_num``: "unique across the director's helper tree".  Box
    *own* (0 is the director) numbers its children in its own block of *B* (the
    breadth, ``effective_breadth`` for unlimited), so no two boxes in the tree hand
    out the same number.  The breadth is the tree's: each child is handed its
    parent's (``child_budget``).
    """
    b = effective_breadth(budget.breadth)
    used = set(existing)
    first = own * b + 1
    for n in range(first, first + b):
        if n not in used:
            return n
    raise ValueError(f"no free helper number in {first}..{first + b - 1}")


def run_spawn(args: argparse.Namespace) -> int:
    """Spawn a new helper instance."""
    helpers_dir = _helpers_dir()

    # Resolve own spawn budget from the two DECLARED ``system.helpers.*`` tiers
    # (spec §2g).  Both are ordinary settings documents, so one reader answers both.
    #
    # Tier 1 — the DELIVERED budget (the spawn-budget bind): the host's resolved budget
    # in a director, the parent's decremented copy in a helper.  It outranks tier 2.
    handed_down = read_spawn_budget(Path.home() / SPAWN_CONFIG_FILENAME)
    # Tier 2 — this box's own system settings file (``@config.settings``).
    own_settings = read_spawn_budget(system_settings_path())

    budget = resolve_spawn_budget(
        handed_down, own_settings, args.depth, args.breadth,
    )

    # Check if spawning is allowed
    existing = _get_existing_helpers(helpers_dir)
    error = check_spawn_allowed(budget, len(existing))
    if error:
        print(f"Cannot spawn: {error}", file=sys.stderr)
        return 1
    # ⚑ BEFORE ANY WRITE: with no hub the helper's dirs and budget copy would describe
    # a helper that never launches, under a "Spawned" line and rc 0.
    if not _check_helpers_enabled():
        print(_NO_HUB % "spawn", file=sys.stderr)
        return 1

    # Determine helper number: a block of its own per box, so unique across the tree
    helper_num = _next_helper_number(existing, budget, _own_helper_number() or 0)

    # Create directory structure
    create_helper_dirs(helpers_dir, helper_num)
    create_broadcast_dirs(helpers_dir)
    create_peer_channels(helpers_dir, helper_num, existing)
    link_broadcast(helpers_dir, helper_num)

    # Hand the child its own (decremented) budget, as the declared keys
    child_cfg = child_budget(budget)
    write_spawn_budget(
        _ro_spawn_config_path(helpers_dir, helper_num),
        child_cfg,
    )

    # Copy init script into helper's scripts/
    init_script = resolve_init_script(
        Path.home() / PARENT_SCRIPTS_RELPATH,
    )
    dest_scripts = helpers_dir / str(helper_num) / HELPER_SCRIPTS_RELPATH
    dest_init = dest_scripts / INIT_SCRIPT_NAME
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

    # Launch the container through the hub (its socket was checked above).  A helper
    # that did not launch is NOT spawned: no success line, rc 1.
    container_name, failure = _launch({
        "action": "spawn",
        "helper_num": helper_num,
        "model": args.model,
        "helpers_dir": str(helpers_dir),
    })
    if failure is not None:
        state["status"] = "failed"
        state["error"] = failure
        _write_state(helpers_dir, helper_num, state)
        print(_SPAWN_FAILED % (helper_num, failure, helper_num), file=sys.stderr)
        return 1
    state["status"] = "running"
    state["container_name"] = container_name
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
        from kanibako.channels.helper_client import send_request
        try:
            send_request(_socket_path(), {
                "action": "stop",
                "container_name": container_name,
                "helper_num": helper_num,
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
        from kanibako.channels.helper_client import send_request
        try:
            send_request(_socket_path(), {
                "action": "stop",
                "container_name": container_name,
                "helper_num": helper_num,
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

    if not _check_helpers_enabled():
        print(_NO_HUB % "respawn", file=sys.stderr)
        return 1

    # Relaunch the container through the hub; a relaunch that fails is not "Respawned"
    container_name, failure = _launch({
        "action": "spawn",
        "helper_num": helper_num,
        "model": state.get("model"),
        "helpers_dir": str(helpers_dir),
    })
    if failure is not None:
        # Still ``stopped``, so a retry is a respawn: a ``failed`` helper cannot be
        # respawned, which would leave only ``cleanup``.
        state["error"] = failure
        _write_state(helpers_dir, helper_num, state)
        print(_RESPAWN_FAILED % (helper_num, failure, helper_num), file=sys.stderr)
        return 1
    state["status"] = "running"
    state["container_name"] = container_name
    state.pop("error", None)
    _write_state(helpers_dir, helper_num, state)
    print(f"Respawned helper {helper_num}.")
    return 0


def run_send(args: argparse.Namespace) -> int:
    """Send a message to a specific helper."""
    if not _check_helpers_enabled():
        print(_NO_HUB % "send", file=sys.stderr)
        return 1

    from kanibako.channels.helper_client import send_request
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
        print(_NO_HUB % "broadcast", file=sys.stderr)
        return 1

    from kanibako.channels.helper_client import send_request
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


def run_register(args: argparse.Namespace) -> int:
    """Register this helper with the hub (one-shot)."""
    if not _check_helpers_enabled():
        return 1

    from kanibako.channels.helper_client import send_request
    try:
        resp = send_request(_socket_path(), {
            "action": "register",
            "helper_num": args.number,
        })
        if resp.get("status") != "ok":
            return 1
    except Exception:
        return 1
    return 0


def _log_path() -> Path:
    """Return the path to the helper message log file.

    Under the FIXED pinned root — the ``helpers.jsonl`` dest ``core-defaults.yaml``
    declares — NOT the box's ``$XDG_STATE_HOME``; see :func:`_socket_path`.
    """
    return Path.home() / BOX_PINNED_STATE_RELPATH / "helpers.jsonl"


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

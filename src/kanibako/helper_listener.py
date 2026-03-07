"""Host-side helper hub: Unix socket server for spawn/stop and message routing."""

from __future__ import annotations

import json
import shutil
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kanibako.container import ContainerRuntime
from kanibako.log import get_logger
from kanibako.targets.base import Mount

logger = get_logger("helper_listener")


@dataclass
class HelperContext:
    """Everything needed to launch helper containers from the host."""

    runtime: ContainerRuntime
    image: str
    container_name_prefix: str  # e.g. "kanibako-myapp" (project container name)
    shell_path: Path      # director's shell_path (parent of helpers/)
    helpers_dir: Path     # absolute host path to helpers/ inside shell_path
    socket_path: Path     # host path to helper.sock
    binary_mounts: list[Mount] = field(default_factory=list)
    env: dict[str, str] | None = None
    entrypoint: str | None = None
    default_entrypoint: str | None = None  # from target.default_entrypoint
    project_path: Path | None = None   # host-side workspace directory
    data_path: Path | None = None      # kanibako data root (~/.local/share/kanibako/)


class HelperHub:
    """Central message router and container orchestrator.

    Runs a Unix domain socket server in a background thread.  Helpers
    connect and send JSON-line requests; the hub dispatches spawn/stop
    commands and routes messages between helpers.
    """

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._ctx: HelperContext | None = None
        self._log: MessageLog | None = None

        # Connection table: helper_num -> socket connection
        self._connections: dict[int, socket.socket] = {}
        self._conn_lock = threading.Lock()

        # Track launched container names for cleanup
        self._containers: list[str] = []
        self._containers_lock = threading.Lock()

    def start(self, socket_path: Path, context: HelperContext,
              log: MessageLog | None = None) -> None:
        """Bind the Unix socket and start the accept loop."""
        self._ctx = context
        self._log = log

        # Ensure parent dir exists, remove stale socket
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            socket_path.unlink()

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(socket_path))
        self._sock.listen(16)
        self._sock.settimeout(1.0)  # so accept loop checks shutdown flag

        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="helper-hub",
        )
        self._accept_thread.start()
        logger.debug("HelperHub listening on %s", socket_path)

    def stop(self) -> None:
        """Shut down: stop all helper containers, close socket."""
        self._shutdown.set()

        # Stop all tracked helper containers
        if self._ctx:
            with self._containers_lock:
                for name in self._containers:
                    try:
                        self._ctx.runtime.stop(name)
                        self._ctx.runtime.rm(name)
                    except Exception:
                        pass
                self._containers.clear()

        # Close all client connections
        with self._conn_lock:
            for conn in self._connections.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()

        # Close server socket
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        if self._accept_thread:
            self._accept_thread.join(timeout=5.0)
            self._accept_thread = None

        if self._log:
            self._log.close()

    def _accept_loop(self) -> None:
        """Accept incoming connections until shutdown."""
        while not self._shutdown.is_set():
            try:
                conn, _ = self._sock.accept()  # type: ignore[union-attr]
                t = threading.Thread(
                    target=self._client_reader,
                    args=(conn,),
                    daemon=True,
                )
                t.start()
            except socket.timeout:
                continue
            except OSError:
                if not self._shutdown.is_set():
                    logger.debug("Accept loop OSError (shutting down?)")
                break

    def _client_reader(self, conn: socket.socket) -> None:
        """Read newline-delimited JSON from a client connection."""
        helper_num: int | None = None
        buf = b""
        try:
            while not self._shutdown.is_set():
                try:
                    data = conn.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        request = json.loads(line)
                    except json.JSONDecodeError:
                        _send_json(conn, {"status": "error", "message": "invalid JSON"})
                        continue
                    response, helper_num = self._dispatch(
                        conn, request, helper_num,
                    )
                    if response is not None:
                        _send_json(conn, response)
        finally:
            if helper_num is not None:
                self._unregister(helper_num)
                if self._log:
                    self._log.log_control("disconnect", helper_num)
            try:
                conn.close()
            except Exception:
                pass

    def _dispatch(
        self, conn: socket.socket, request: dict,
        current_helper: int | None,
    ) -> tuple[dict | None, int | None]:
        """Route a request to the appropriate handler.

        Returns (response_dict, updated_helper_num).
        """
        action = request.get("action", "")

        if action == "register":
            helper_num = int(request.get("helper_num", -1))
            if helper_num < 0:
                return {"status": "error", "message": "invalid helper_num"}, current_helper
            self._register(helper_num, conn)
            if self._log:
                self._log.log_control("register", helper_num)
            return {"status": "ok"}, helper_num

        if action == "spawn":
            resp = self._handle_spawn(request)
            return resp, current_helper

        if action == "stop":
            resp = self._handle_stop(request)
            return resp, current_helper

        if action == "fork":
            resp = self._handle_fork(request)
            return resp, current_helper

        if action == "send":
            to = request.get("to")
            payload = request.get("payload", {})
            sender = current_helper if current_helper is not None else 0
            if to is None:
                return {"status": "error", "message": "missing 'to'"}, current_helper
            self._route_message(sender, int(to), payload)
            return {"status": "ok"}, current_helper

        if action == "broadcast":
            payload = request.get("payload", {})
            sender = current_helper if current_helper is not None else 0
            self._broadcast_message(sender, payload)
            return {"status": "ok"}, current_helper

        return {"status": "error", "message": f"unknown action: {action}"}, current_helper

    def _register(self, helper_num: int, conn: socket.socket) -> None:
        with self._conn_lock:
            self._connections[helper_num] = conn

    def _unregister(self, helper_num: int) -> None:
        with self._conn_lock:
            self._connections.pop(helper_num, None)

    def _route_message(self, sender: int, recipient: int,
                       payload: dict) -> None:
        """Send a message to a specific helper."""
        if self._log:
            self._log.log_message(sender, recipient, payload)
        with self._conn_lock:
            conn = self._connections.get(recipient)
        if conn:
            msg = {"event": "message", "from": sender, "payload": payload}
            try:
                _send_json(conn, msg)
            except OSError:
                logger.debug("Failed to deliver to helper %d", recipient)

    def _broadcast_message(self, sender: int, payload: dict) -> None:
        """Send a message to all connected helpers."""
        if self._log:
            self._log.log_message(sender, "all", payload)
        with self._conn_lock:
            targets = list(self._connections.items())
        msg = {"event": "message", "from": sender, "payload": payload}
        for num, conn in targets:
            if num == sender:
                continue
            try:
                _send_json(conn, msg)
            except OSError:
                logger.debug("Failed to broadcast to helper %d", num)

    def _handle_spawn(self, request: dict) -> dict:
        """Launch a helper container."""
        ctx = self._ctx
        if ctx is None:
            return {"status": "error", "message": "no context"}

        helper_num = int(request.get("helper_num", -1))
        if helper_num < 0:
            return {"status": "error", "message": "invalid helper_num"}

        helpers_dir = request.get("helpers_dir")
        if helpers_dir:
            # Container-side path; map to host path via ctx
            helpers_dir_host = ctx.helpers_dir
        else:
            helpers_dir_host = ctx.helpers_dir

        container_name = f"{ctx.container_name_prefix}-helper-{helper_num}"

        mounts = _build_helper_mounts(ctx, helper_num, helpers_dir_host)

        # Use helper-init.sh as entrypoint wrapper — it registers with the
        # hub, sources broadcast scripts, then execs the agent command.
        init_script = "/home/agent/playbook/scripts/helper-init.sh"
        agent_cmd = ctx.entrypoint or ctx.default_entrypoint or "/bin/bash"
        cli_args = [str(helper_num), agent_cmd]
        model = request.get("model")
        if model:
            cli_args.extend(["--model", model])

        try:
            rc = ctx.runtime.run(
                ctx.image,
                shell_path=helpers_dir_host / str(helper_num),
                project_path=helpers_dir_host / str(helper_num) / "workspace",
                vault_ro_path=helpers_dir_host / str(helper_num) / "vault" / "share-ro",
                vault_rw_path=helpers_dir_host / str(helper_num) / "vault" / "share-rw",
                extra_mounts=mounts or None,
                vault_enabled=True,
                env=ctx.env,
                name=container_name,
                entrypoint=init_script,
                cli_args=cli_args,
                detach=True,
            )
        except Exception as e:
            return {"status": "error", "message": str(e)}

        if rc != 0:
            return {"status": "error", "message": f"container exited with {rc}"}

        with self._containers_lock:
            self._containers.append(container_name)

        if self._log:
            self._log.log_control(
                "spawn", helper_num,
                model=request.get("model"),
            )

        return {"status": "ok", "container_name": container_name}

    def _handle_stop(self, request: dict) -> dict:
        """Stop and remove a helper container."""
        ctx = self._ctx
        if ctx is None:
            return {"status": "error", "message": "no context"}

        container_name = request.get("container_name", "")
        if not container_name:
            return {"status": "error", "message": "missing container_name"}

        ctx.runtime.stop(container_name)
        ctx.runtime.rm(container_name)

        with self._containers_lock:
            if container_name in self._containers:
                self._containers.remove(container_name)

        # Extract helper_num from container name if possible
        helper_num = _parse_helper_num(container_name)
        if self._log and helper_num is not None:
            self._log.log_control("stop", helper_num)

        return {"status": "ok"}

    def _handle_fork(self, request: dict) -> dict:
        """Fork the current project into a sibling directory."""
        ctx = self._ctx
        if ctx is None:
            return {"status": "error", "message": "no context"}
        if ctx.project_path is None or ctx.data_path is None:
            return {"status": "error", "message": "fork requires project_path and data_path"}

        name = request.get("name", "")
        if not name:
            return {"status": "error", "message": "missing fork name"}

        # Validate name: no path separators, no dots, not empty after strip
        name = name.strip()
        if not name or "/" in name or "\\" in name or "." in name:
            return {"status": "error", "message": "invalid fork name (no slashes or dots)"}

        # Compute destination
        new_path = ctx.project_path.parent / f"{ctx.project_path.name}.{name}"
        if new_path.exists():
            return {"status": "error", "message": f"destination already exists: {new_path}"}

        # Copy workspace
        try:
            shutil.copytree(ctx.project_path, new_path)
        except Exception as e:
            return {"status": "error", "message": f"workspace copy failed: {e}"}

        # Resolve source metadata dir via names.toml reverse lookup
        from kanibako.names import assign_name, read_names

        source_meta_dir: Path | None = None
        names = read_names(ctx.data_path)
        for rname, rpath in names["projects"].items():
            if rpath == str(ctx.project_path):
                candidate = ctx.data_path / "boxes" / rname
                if candidate.is_dir():
                    source_meta_dir = candidate
                break

        # Fallback: derive from shell_path (shell_path is typically boxes/{name}/shell/)
        if source_meta_dir is None:
            candidate = ctx.shell_path.parent
            if candidate.is_dir() and candidate.parent.name == "boxes":
                source_meta_dir = candidate

        # Assign a new name for the fork
        try:
            new_name = assign_name(ctx.data_path, str(new_path))
        except Exception as e:
            return {"status": "error", "message": f"name assignment failed: {e}"}

        # Copy metadata if we found the source
        if source_meta_dir is not None:
            new_meta_dir = ctx.data_path / "boxes" / new_name
            try:
                if not new_meta_dir.exists():
                    shutil.copytree(
                        source_meta_dir, new_meta_dir,
                        ignore=shutil.ignore_patterns(
                            ".kanibako.lock", "helpers",
                        ),
                    )
            except Exception as e:
                logger.warning("metadata copy failed: %s", e)

        if self._log:
            self._log.log_control("fork", name=name, path=str(new_path),
                                  new_name=new_name)

        return {"status": "ok", "path": str(new_path), "name": new_name}


_LOG_MAX_BYTES = 1_048_576  # 1 MiB


class MessageLog:
    """Append-only JSONL log with size-based rotation."""

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(log_path, "a")
        self._lock = threading.Lock()

    def log_message(self, sender: int, recipient: int | str,
                    payload: dict) -> None:
        """Record a message event."""
        self._write({
            "type": "message",
            "from": sender,
            "to": recipient,
            "payload": payload,
        })

    def log_control(self, event: str, helper: int | None = None,
                    **extra: Any) -> None:
        """Record a control event (spawn, stop, register, disconnect)."""
        entry: dict[str, Any] = {"type": "control", "event": event}
        if helper is not None:
            entry["helper"] = helper
        entry.update(extra)
        self._write(entry)

    def _write(self, entry: dict) -> None:
        from datetime import datetime, timezone
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._file.write(json.dumps(entry) + "\n")
            self._file.flush()
            self._rotate_if_needed()

    def _rotate_if_needed(self) -> None:
        """Rotate log file when it exceeds the size threshold.

        Caller must hold ``self._lock``.
        """
        try:
            pos = self._file.tell()
        except OSError:
            return
        if pos < _LOG_MAX_BYTES:
            return
        self._file.close()
        backup = self._path.with_suffix(self._path.suffix + ".1")
        self._path.rename(backup)
        self._file = open(self._path, "a")

    def close(self) -> None:
        with self._lock:
            self._file.close()


def _send_json(conn: socket.socket, data: dict) -> None:
    """Send a JSON object followed by newline."""
    conn.sendall(json.dumps(data).encode() + b"\n")


def _build_helper_mounts(ctx: HelperContext, helper_num: int,
                         helpers_dir: Path) -> list[Mount]:
    """Build bind mounts for a helper container."""
    helper_root = helpers_dir / str(helper_num)
    mounts: list[Mount] = []

    # Peers directory
    peers_dir = helper_root / "peers"
    if peers_dir.is_dir():
        mounts.append(Mount(peers_dir, "/home/agent/peers", "Z,U"))

    # Broadcast directory
    all_link = helper_root / "all"
    if all_link.exists():
        mounts.append(Mount(all_link, "/home/agent/all", "Z,U"))

    # Spawn config (read-only)
    spawn_toml = helper_root / "spawn.toml"
    if spawn_toml.is_file():
        mounts.append(Mount(spawn_toml, "/home/agent/spawn.toml", "ro"))

    # Helper socket — mount the hub socket into the helper
    if ctx.socket_path.exists():
        kanibako_dir = helper_root / ".kanibako"
        kanibako_dir.mkdir(parents=True, exist_ok=True)
        mounts.append(Mount(ctx.socket_path, "/home/agent/.kanibako/helper.sock", ""))

    # Target binary mounts (same agent binary as the director)
    mounts.extend(ctx.binary_mounts)

    return mounts


def _parse_helper_num(container_name: str) -> int | None:
    """Extract helper number from a container name.

    Handles both formats:
    - New: ``kanibako-{name}-helper-{N}``
    - Legacy: ``kanibako-helper-{N}-{hash}``
    """
    parts = container_name.split("-")
    # Walk backwards looking for "helper" followed by a numeric part.
    for i in range(len(parts) - 1, 0, -1):
        if parts[i - 1] == "helper":
            try:
                return int(parts[i])
            except ValueError:
                pass
    return None

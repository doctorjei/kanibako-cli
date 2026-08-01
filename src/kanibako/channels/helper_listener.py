"""Host-side helper hub: Unix socket server for spawn/stop and message routing."""

from __future__ import annotations

import json
import shutil
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kanibako.runtime.container import ContainerRuntime
from kanibako.log import get_logger
from kanibako.settings.settings_resolve import GUEST_HOME
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
    box_shell: str | None = None       # resolved box.shell (no-agent fallback)
    project_path: Path | None = None   # host-side workspace directory
    data_path: Path | None = None      # kanibako data root (~/.local/share/kanibako/)
    boxes: Path | None = None          # resolved system.path.boxes (std.boxes)
    registry: Path | None = None       # resolved config.registry file (std.registry)
    primary_workset: Path | None = None  # resolved config.primary_workset (std.primary_workset)


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
        # Fall back to the resolved box.shell (box.shell -> $KANIBAKO_SHELL ->
        # image's recorded login shell -> sh) rather than a hardcoded /bin/bash,
        # so a no-agent helper honors the same shell-resolution chain as the
        # main launch path.  A real-agent helper keeps winning on entrypoint /
        # default_entrypoint; box_shell only covers the no-agent case.  The
        # final "sh" is a last-ditch floor (box_shell itself already falls back
        # to sh, so it is rarely reached).
        agent_cmd = (
            ctx.entrypoint or ctx.default_entrypoint or ctx.box_shell or "sh"
        )
        cli_args = [str(helper_num), agent_cmd]
        model = request.get("model")
        if model:
            cli_args.extend(["--model", model])

        # ⚑ Canon re-protect, same as the primary launch seam.  A helper home has no
        # canon skeleton TODAY, so this is a no-op — but ``:U`` re-chowns whatever
        # IS in the bind source at every container creation, so the day a helper home
        # gains one, the seam that forgot to pass this would silently be the one
        # unprotected launch path.  Passing it now costs nothing and removes that.
        helper_home = helpers_dir_host / str(helper_num)

        def _helper_reprotect() -> None:
            from kanibako.settings.core_defaults import materialize_canon_skeleton_if_present

            materialize_canon_skeleton_if_present(helper_home)

        try:
            rc = ctx.runtime.run(
                ctx.image,
                post_start=_helper_reprotect,
                shell_path=helpers_dir_host / str(helper_num),
                project_path=helpers_dir_host / str(helper_num) / "workspace",
                vault_ro_path=helpers_dir_host / str(helper_num) / "vault" / "ro",
                vault_rw_path=helpers_dir_host / str(helper_num) / "vault" / "rw",
                extra_mounts=mounts or None,
                enable_vault=True,
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

        # helper_num is carried structurally in the request (the (name,
        # helper_num) pair is the helper identity; the container name is a
        # one-way rendering of it, never a source to parse back from).
        helper_num = request.get("helper_num")
        if self._log and helper_num is not None:
            self._log.log_control("stop", helper_num)

        return {"status": "ok"}

    def _handle_fork(self, request: dict) -> dict:
        """Fork the current project into a sibling directory."""
        ctx = self._ctx
        if ctx is None:
            return {"status": "error", "message": "no context"}
        if (
            ctx.project_path is None
            or ctx.data_path is None
            or ctx.boxes is None
            or ctx.registry is None
            or ctx.primary_workset is None
        ):
            return {
                "status": "error",
                "message": (
                    "fork requires project_path, data_path, boxes, registry "
                    "and primary_workset"
                ),
            }

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

        # Resolve source metadata dir via the PRIMARY per-workset ``boxes:``
        # membership reverse lookup (the sole store since the global ``projects:``
        # section retired).
        from kanibako.settings.paths import (
            assign_primary_box_name,
            primary_box_name_for_workspace,
        )

        boxes_base = ctx.boxes
        source_meta_dir: Path | None = None
        source_name = primary_box_name_for_workspace(
            ctx.primary_workset, str(ctx.project_path),
        )
        if source_name is not None:
            candidate = boxes_base / source_name
            if candidate.is_dir():
                source_meta_dir = candidate

        # Fallback: derive from shell_path (shell_path is typically boxes/{name}/home/)
        if source_meta_dir is None:
            candidate = ctx.shell_path.parent
            if candidate.is_dir() and candidate.parent.name == "boxes":
                source_meta_dir = candidate

        # Assign + register a new name for the fork in the PRIMARY membership
        # (was global-only before — a fork now joins the membership like any
        # other primary box).
        try:
            new_name = assign_primary_box_name(
                ctx.primary_workset, ctx.registry, str(new_path),
            )
        except Exception as e:
            return {"status": "error", "message": f"name assignment failed: {e}"}

        # Copy metadata if we found the source
        if source_meta_dir is not None:
            new_meta_dir = boxes_base / new_name
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
    """Build bind mounts for a helper container.

    DOCUMENTED BLOCKER (core-mounts-bindings Phase C — bespoke route kept by
    design, per the plan's "lower priority / possible follow-up").  The core box
    launch (Phases A/B) routes its mounts through the category keyspace via
    ``commands.start._resolve_launch_categories`` →
    ``_category_resolution_inputs`` → ``reconcile_categories``.  The invariant
    permits a bespoke route HERE because reaching that seam from the IN-BOX
    ``crab helper`` spawn path is disproportionate, for three concrete reasons:

    1. CONTEXT DATA IS ABSENT.  ``reconcile_categories`` is driven by
       ``_category_resolution_inputs``, which requires ``std`` (StandardPaths:
       ``.agents``/``.data``/``.data_home``/``.channels``/…), ``proj``
       (ProjectPaths, incl. ``.group`` for the workset scope roots),
       ``agent_name``, AND the FOUR per-level config-file paths
       (``global_config_path``/``project_toml``/``workset_config_path``/
       ``agent_config_path``).  ``HelperContext`` — what the hub carries host-side
       across the box's lifetime — deliberately holds only a flat subset
       (runtime/image/shell_path/helpers_dir/socket_path/binary_mounts/env/…).
       None of std/proj/agent_name/the config paths are present, so the seam is
       not reachable without threading the entire launch context into the hub.

    2. WRONG TOPOLOGY EVEN IF THREADED.  A helper has NO ``ProjectPaths`` of its
       own: its home/workspace/vault are a DERIVED sub-tree under
       ``helpers/<N>/`` (``helper_root`` below), not ``proj.shell_path`` /
       ``proj.project_path`` / ``proj.vault_*_path``.  ``_category_resolution_inputs``
       hardwires the director box's std/proj paths and the ``proj.group`` scope
       roots, so feeding it the box's std/proj would resolve the WRONG sources;
       a helper-specific inputs builder would be a large bespoke bridge — exactly
       what this sub-step is told not to force.

    3. CIRCULAR DEPENDENCY.  The seam (``_resolve_launch_categories`` /
       ``_emit_reconciled_mounts`` / ``_category_resolution_inputs``) lives in
       ``commands.start``, which already imports this module
       (``from kanibako.channels.helper_listener import HelperContext, HelperHub``).  This
       module is a lean socket server depending only on container/log/
       settings_resolve/targets.base; importing the seam back the other way would
       introduce a ``commands.start`` ↔ ``helper_listener`` import cycle.

    The socket bind below intentionally mirrors Phase A/B's empty mount options
    for a LIVE unix socket (``""`` — a ``Z``/``U`` relabel/chown would break the
    shared socket topology), so the bespoke list stays consistent with the keyed
    route even though it does not flow through it.
    """
    helper_root = helpers_dir / str(helper_num)
    mounts: list[Mount] = []

    # Peers directory
    peers_dir = helper_root / "peers"
    if peers_dir.is_dir():
        mounts.append(Mount(peers_dir, f"{GUEST_HOME}/peers", "Z,U"))

    # Broadcast directory
    all_link = helper_root / "all"
    if all_link.exists():
        mounts.append(Mount(all_link, f"{GUEST_HOME}/all", "Z,U"))

    # Spawn config (read-only)
    spawn_toml = helper_root / "spawn.yaml"
    if spawn_toml.is_file():
        mounts.append(Mount(spawn_toml, f"{GUEST_HOME}/spawn.yaml", "ro"))

    # Helper socket — mount the hub socket into the helper.  The box-side dest
    # is XDG_STATE_HOME-aware: derived from the helper's container env (the same
    # single derivation start.py + helper-init.sh use) so all sides agree.
    if ctx.socket_path.exists():
        from kanibako.settings.paths import box_state_home

        box_socket = box_state_home(ctx.env) / "kanibako" / "helper.sock"
        kanibako_dir = helper_root / ".local" / "state" / "kanibako"
        kanibako_dir.mkdir(parents=True, exist_ok=True)
        mounts.append(Mount(ctx.socket_path, str(box_socket), ""))

    # Target binary mounts (same agent binary as the director)
    mounts.extend(ctx.binary_mounts)

    return mounts

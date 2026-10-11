"""Host-side helper hub: Unix socket server for spawn/stop and message routing."""

from __future__ import annotations

import json
import shlex
import shutil
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kanibako.channels.helpers import (
    HELPER_CORE_LEAVES,
    HELPER_SCRIPTS_RELPATH,
    INIT_SCRIPT_NAME,
    SPAWN_CONFIG_FILENAME,
    mark_helper_stopped,
    write_helper_number,
)
from kanibako.errors import ContainerError
from kanibako.runtime.container import ContainerRuntime
from kanibako.log import get_logger
from kanibako.settings.core_defaults import core_bind_slots, helper_budget_dest
from kanibako.settings.settings_resolve import (
    BOX_PINNED_STATE_RELPATH,
    GUEST_HOME,
    normalize_bind_dest,
)
from kanibako.settings.store_collapse import HOME_DEST, HOME_OPTIONS
from kanibako.targets.base import Mount
from kanibako.utils import render_container_name

if TYPE_CHECKING:
    from kanibako.settings.workset_dirkeys import EarlyScope

logger = get_logger("helper_listener")


@dataclass
class HelperContext:
    """Everything needed to launch helper containers from the host."""

    runtime: ContainerRuntime
    image: str
    #: The DIRECTOR box's ``(<W>, <B>)`` pair; a helper's name renders from it.
    container_name_segments: tuple[str, str]
    shell_path: Path      # director's shell_path (parent of helpers/)
    helpers_dir: Path     # absolute host path to helpers/ inside shell_path
    socket_path: Path     # host path to helper.sock
    binary_mounts: list[Mount] = field(default_factory=list)
    # A SNAPSHOT of the launch environment as it stood when the hub started, never
    # the launch's live dict: the hub outlives that assembly and spawns from this map
    # for the rest of the session, so an alias would let a post-hub write reach
    # helpers the launch never described. The caller copies out (P8).
    env: dict[str, str] | None = None
    entrypoint: str | None = None
    default_entrypoint: str | None = None  # from target.default_entrypoint
    box_shell: str | None = None       # resolved box.shell (no-agent fallback)
    project_path: Path | None = None   # host-side workspace directory
    data_path: Path | None = None      # kanibako data root (~/.local/share/kanibako/)
    boxes: Path | None = None          # resolved PRIMARY workset.boxes (std.boxes)
    primary_workset: Path | None = None  # resolved config.primary_workset (std.primary_workset)
    early: EarlyScope = field(kw_only=True)  # the PRIMARY early scope (paths._early_scope)


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

        # THE TREE: helper_num -> the host home it was launched into.  One hub serves
        # the director's whole helper tree, and keyspec ``meta.box.helper_num`` makes the
        # number unique across it, so this map both places a helper's children (under
        # their requester's home) and refuses a number already held elsewhere.
        # Guarded by ``_containers_lock``.
        self._homes: dict[int, Path] = {}

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

        # Stop all tracked helper containers, and mark each one ``stopped`` in its state
        # document, which ``respawn`` and ``list`` read.  ⚑ The mark follows what the
        # runtime REPORTS, since ``stop``/``rm`` return a bool and do not raise: a stop
        # that exits 0 means the container is not running (an already-exited one
        # included), and an ``rm`` that exits 0 means it is gone.  When the runtime
        # refuses both, the helper may still run, so its state is left as it was.
        if self._ctx:
            with self._containers_lock:
                launched = {
                    render_container_name(
                        *self._ctx.container_name_segments, helper_num=num,
                    ): (num, home)
                    for num, home in self._homes.items()
                }
                for name in self._containers:
                    try:
                        stopped = self._ctx.runtime.stop(name)
                        removed = self._ctx.runtime.rm(name)
                    except Exception:
                        continue
                    if not (stopped or removed) or name not in launched:
                        continue
                    num, home = launched[name]
                    try:
                        mark_helper_stopped(home.parent, num)
                    except (OSError, ValueError) as exc:
                        logger.debug("helper %d state not marked stopped: %s", num, exc)
                self._containers.clear()

        # Close all client connections.  ``shutdown()`` first: a bare ``close()``
        # neither wakes this side's blocked reader nor sends the peer an EOF, so a
        # helper holding its registration open would never learn the hub is gone.
        with self._conn_lock:
            for conn in self._connections.values():
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
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
                self._unregister(helper_num, conn)
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

    def _unregister(self, helper_num: int, conn: socket.socket) -> None:
        """Drop *helper_num* only while *conn* still holds it: a respawned helper
        that registered on a new connection before the old one closed keeps it."""
        with self._conn_lock:
            if self._connections.get(helper_num) is conn:
                del self._connections[helper_num]

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

        # WHERE: under the REQUESTER's home.  The request's ``helpers_dir`` is an in-box
        # path (every box's ``~/helpers``) and says nothing about which box sent it; the
        # structured ``requester`` does (``None``: the director).
        requester = request.get("requester")
        if requester is None:
            helpers_dir_host = ctx.helpers_dir
        else:
            with self._containers_lock:
                requester_home = self._homes.get(int(requester))
            if requester_home is None:
                return {"status": "error",
                        "message": f"requesting helper {requester} was not launched by this hub"}
            helpers_dir_host = requester_home / "helpers"
        helper_home = helpers_dir_host / str(helper_num)

        container_name = render_container_name(
            *ctx.container_name_segments, helper_num=helper_num,
        )
        if container_name is None:
            raise ContainerError(
                f"director box {ctx.container_name_segments[1]!r} renders no helper "
                f"container name; start refuses such a box."
            )

        # RESERVE the number for this home before launching: a number held by another
        # home is refused, and the same home (a respawn) keeps it.
        with self._containers_lock:
            held = self._homes.get(helper_num)
            if held is not None and held != helper_home:
                return {"status": "error",
                        "message": f"helper number {helper_num} is already taken in this "
                                   f"tree (by {held})"}
            self._homes[helper_num] = helper_home

        # Use helper-init.sh as entrypoint wrapper — it registers with the
        # hub, sources broadcast scripts, then execs the agent command.
        init_script = f"{GUEST_HOME}/{HELPER_SCRIPTS_RELPATH}/{INIT_SCRIPT_NAME}"
        # Fall back to the resolved box.shell (box.shell -> image's recorded
        # login shell -> sh) rather than a hardcoded /bin/bash,
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
        def _helper_reprotect() -> None:
            from kanibako.settings.core_defaults import materialize_canon_skeleton_if_present

            materialize_canon_skeleton_if_present(helper_home)

        try:
            # Inside the try: a document or mount that cannot be written is a failed
            # launch with a reply, not a dropped connection.
            # The helper's NUMBER goes in its own spawn document (keyspec
            # ``meta.box.helper_num``, set at spawn), written here at every launch and
            # bound RO at ``~/spawn.yaml`` below, never into its env: system-design
            # § Delivery at launch lets nothing write a variable after the channel.
            write_helper_number(helper_home / SPAWN_CONFIG_FILENAME, helper_num)
            mounts = _build_helper_mounts(ctx, helper_num, helpers_dir_host)
            rc = ctx.runtime.run(
                ctx.image,
                post_start=_helper_reprotect,
                shell_path=helper_home,
                project_path=helper_home / HELPER_CORE_LEAVES["project_path"],
                # ⚑ NOT the workset vault, and NOT to be resolved.  ``helpers_dir`` is
                # ``proj.shell_path/"helpers"`` (``commands/start.py``) — the DIRECTOR
                # box's own HOME.  A helper is not a workset member and owns no
                # ``workset.vault_*``; this private leaf is created at the same literal by
                # ``channels/helpers.py::create_helper_dirs``, and pointing it at a
                # repointed workset arm would give every helper the DIRECTOR's vault.
                vault_ro_path=helper_home / HELPER_CORE_LEAVES["vault_ro_path"],
                vault_rw_path=helper_home / HELPER_CORE_LEAVES["vault_rw_path"],
                extra_mounts=mounts or None,
                enable_vault=True,
                env=ctx.env,
                name=container_name,
                entrypoint=init_script,
                cli_args=cli_args,
                detach=True,
            )
        except Exception as e:
            self._release(helper_num, held, helper_home)
            return {"status": "error", "message": str(e)}

        if rc != 0:
            self._release(helper_num, held, helper_home)
            return {"status": "error", "message": f"container exited with {rc}"}

        with self._containers_lock:
            self._containers.append(container_name)

        if self._log:
            self._log.log_control(
                "spawn", helper_num,
                model=request.get("model"),
            )

        return {"status": "ok", "container_name": container_name}

    def _release(self, helper_num: int, held: Path | None, helper_home: Path) -> None:
        """Undo a reservation a failed launch made (a number held before stays held)."""
        if held is None:
            with self._containers_lock:
                if self._homes.get(helper_num) == helper_home:
                    del self._homes[helper_num]

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
            or ctx.primary_workset is None
        ):
            return {
                "status": "error",
                "message": (
                    "fork requires project_path, data_path, boxes "
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
        # ⚑ The fork is named for its directory: the box-name rule, before the copy.
        from kanibako.errors import DerivedBoxNameError
        from kanibako.launch.box_identity import is_valid_box_name
        from kanibako.launch.romanize import to_ascii
        from kanibako.settings.messages import (CURE_DERIVED_FORK_BOX, CURE_DERIVED_FORK_NAME,
                                                CURE_DERIVED_FORK_NAME_ASCII)
        from kanibako.settings.paths import refuse_derived_box_name

        try:
            refuse_derived_box_name(new_path.name)
        except DerivedBoxNameError as e:
            if is_valid_box_name(f"x.{name}"):
                cure = CURE_DERIVED_FORK_BOX % (f"kanibako box move {shlex.quote(str(ctx.project_path))} "
                                                "<new-path> --name <new-name>")
            else:
                spelled = to_ascii(name)
                cure = (CURE_DERIVED_FORK_NAME_ASCII % spelled
                        if spelled is not None and is_valid_box_name(f"x.{spelled}")
                        else CURE_DERIVED_FORK_NAME)
            return {"status": "error", "message": e.with_cure(cure)}

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
            ctx.primary_workset, str(ctx.project_path), early=ctx.early,
        )
        if source_name is not None:
            candidate = boxes_base / source_name
            if candidate.is_dir():
                source_meta_dir = candidate

        # Fallback: derive from shell_path (a box's shell is @meta.box.path/home, so its
        # parent is the metadata dir).  ⚑ The predicate compares that dir's parent to the
        # RESOLVED box store, never to a literal leaf name: ``workset.boxes`` is
        # repointable, so a name test silently missed every workset that repointed it and
        # the fork lost its source metadata.  Direct child, not containment — the store
        # holds one metadata dir per box name, which is the same shape ``new_meta_dir``
        # below composes.
        if source_meta_dir is None:
            candidate = ctx.shell_path.parent
            if candidate.is_dir() and candidate.parent.resolve() == boxes_base.resolve():
                source_meta_dir = candidate

        # Assign + register a new name for the fork in the PRIMARY membership.
        try:
            new_name = assign_primary_box_name(
                ctx.primary_workset, str(new_path), early=ctx.early,
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
    design, per the plan's "lower priority / possible follow-up") for the
    BROADCAST and SOCKET mounts.  The home, workspace, vault and spawn-budget mounts
    are not exempt: home takes the director's foundation dest and options
    (``store_collapse.HOME_DEST``/``HOME_OPTIONS``), and the others the declared
    ``core:`` rows (:func:`~kanibako.settings.core_defaults.core_bind_slots`) and the
    ``helper_budget`` row (:func:`~kanibako.settings.core_defaults.helper_budget_dest`).  A box launch
    routes its mounts through the category keyspace: ``commands.start``'s ONE
    resolve (``_resolve_launch_snapshot``) adapts the launch snapshot to
    ``CategoryEntry`` rows, the ASSEMBLY COLLAPSE folds them into
    ``meta.assembly.bindings`` on that snapshot, and ``_emit_category_mounts``
    turns that one dest-keyed map into ``Mount``\\ s.  ⚑ THIS FUNCTION IS OUTSIDE
    THAT ROUTE and builds its list by hand.  The single-route invariant permits it
    HERE because reaching the seam from the in-box ``kanibako helper`` spawn path
    is disproportionate, for three concrete reasons:

    1. CONTEXT DATA IS ABSENT.  ``_resolve_launch_snapshot`` requires ``std``
       (StandardPaths: ``.agents``/``.data``/``.data_home``/``.channels``/…),
       ``proj`` (ProjectPaths, incl. the workset scope roots), ``agent_name`` and
       the per-level settings-file paths (``system_settings_path`` /
       ``agent_cfg_path``, plus the workset/box cascade paths it derives from
       ``proj``).  ``HelperContext`` — what the hub carries host-side across the
       box's lifetime — deliberately holds only a flat subset (runtime / image /
       shell_path / helpers_dir / socket_path / binary_mounts / env / …).  None of
       std / proj / agent_name / the settings paths are present, so the seam is not
       reachable without threading the entire launch context into the hub.

    2. WRONG TOPOLOGY EVEN IF THREADED.  A helper has NO ``ProjectPaths`` of its
       own: its home/workspace/vault are a DERIVED sub-tree under
       ``helpers/<N>/`` (``helper_root`` below), not ``proj.shell_path`` /
       ``proj.project_path`` / ``proj.vault_*_path``.  The resolve is anchored on
       the DIRECTOR box's std/proj and its workset scope roots, so feeding it the
       box's own would resolve the WRONG sources; a helper-specific inputs builder
       would be a large bespoke bridge — exactly what this sub-step is told not to
       force.

    3. CIRCULAR DEPENDENCY.  The seam lives in ``commands.start``, which already
       imports this module (``from kanibako.channels.helper_listener import
       HelperContext, HelperHub, MessageLog``).  This module is a lean socket
       server depending only on runtime.container / log / settings_resolve /
       targets.base; importing the seam back the other way would introduce a
       ``commands.start`` ↔ ``helper_listener`` import cycle.

    The socket bind below intentionally mirrors the keyed route's empty mount
    options for a LIVE unix socket (``""`` — a ``Z``/``U`` relabel/chown would
    break the shared socket topology), so the bespoke list stays consistent with
    the keyed route even though it does not flow through it.
    """
    helper_root = helpers_dir / str(helper_num)

    # THE HOME FOUNDATION, FIRST, so every deeper bind lands on top of it.  The helper
    # root is the helper's home: the parent copied the entrypoint wrapper into its
    # ``scripts/`` and its budget copy beside it, and both are addressed under ``~``.
    # Dest and options are the director's foundation's (``store_collapse``), never
    # re-spelled here.
    mounts: list[Mount] = [Mount(helper_root, HOME_DEST, HOME_OPTIONS)]

    # Workspace and vault: the helper's own leaves at the dests and options of the
    # director's declared ``core:`` rows — one row per slot for both, so these are NOT
    # under the exemption above.  The ``ro`` vault is why the binds are needed at all
    # when the leaves already sit under the home: it keeps the helper from writing it.
    slots = core_bind_slots()
    for source, leaf in HELPER_CORE_LEAVES.items():
        leaf_dir = helper_root / leaf
        if leaf_dir.is_dir():
            dest, options = slots[source]
            mounts.append(Mount(leaf_dir, normalize_bind_dest(dest), options))

    # Broadcast directory: the REAL ``helpers/all`` the parent shares with all its
    # helpers, never a link under the helper root.  A link there is written in-box and
    # does not resolve on the host, and the launch's stub step replaces whatever sits at
    # a mount's destination with an empty dir, so a source at that path binds nothing.
    all_dir = helpers_dir / "all"
    if all_dir.is_dir():
        mounts.append(Mount(all_dir, f"{GUEST_HOME}/all", "Z,U"))

    # The SPAWN DOCUMENT: the budget the PARENT wrote into this helper's home
    # (``commands.helper_cmd``) and the number the hub wrote beside it, mounted at the dest and options of the director's own
    # declared row, spec §2c ``box.bindings.ro[~/spawn.yaml]`` — one row for both, so
    # this mount is NOT under the exemption above.  For a helper the dest is the
    # source's own path, so the ``ro`` bind covers the only in-box path to the file: a
    # helper must not be able to raise the budget it was handed or change its number.
    budget_doc = helper_root / SPAWN_CONFIG_FILENAME
    if budget_doc.is_file():
        budget_dest, budget_options = helper_budget_dest()
        mounts.append(Mount(budget_doc, normalize_bind_dest(budget_dest), budget_options))

    # Helper socket — mount the hub socket into the helper.  The box-side dest is
    # the FIXED pinned root, the same path ``core-defaults.yaml`` declares for the
    # director's box, that ``helper-init.sh`` registers against and that the in-box
    # CLI reads.  Nothing is derived from the helper's env: a mount dest is written
    # into the runtime's arguments before the helper is live, and the box's real XDG
    # location is served after boot by ``box_supervisor.project_pinned_xdg``.
    if ctx.socket_path.exists():
        kanibako_dir = helper_root / BOX_PINNED_STATE_RELPATH
        kanibako_dir.mkdir(parents=True, exist_ok=True)
        box_socket = f"{GUEST_HOME}/{BOX_PINNED_STATE_RELPATH}/helper.sock"
        mounts.append(Mount(ctx.socket_path, box_socket, ""))

    # Target binary mounts (same agent binary as the director)
    mounts.extend(ctx.binary_mounts)

    return mounts

"""FF-1 remote-VS-Code plumbing for ``kanibako code --remote`` (A' topology).

LOCAL VS Code → REMOTE rootless podman socket over SSH.  The kanibako CLI stays
HOST-side (box lifecycle over plain SSH); kanibako-CLI-over-remote-podman is a
NON-GOAL.  This module owns the host-side pieces that make a LOCAL VS Code Dev
Containers "attach to running container" reach a REMOTE box:

* the KANIBAKO-OWNED ssh ControlMaster mux options (:func:`mux_ssh_options`) and
  the lifecycle ssh leg (:func:`ssh_command` / :func:`remote_run_kanibako` /
  :func:`probe_remote`);
* the unix-socket TUNNEL engine (FF-1b): ONE kanibako-owned OpenSSH master per
  dest carries a unix-socket forward (:func:`tunnel_socket_path` /
  :func:`ensure_tunnel`), and podman dials ``unix://<local.sock>`` — the
  upstream-recommended pattern (podman's built-in ``ssh:`` client hardcodes a
  golang ssh that cannot mux and ignores ``~/.ssh/config``/ProxyJump).  The real
  ssh binary makes the tunnel, so alias/port/ProxyJump/key-files all work
  natively with NO ssh-agent requirement.  Persistence is LEASED from OpenSSH
  (``ControlPersist``) — kanibako runs no daemon;
* engine pre-flight (:func:`preflight_engine`) + docker-context naming
  (:func:`context_slug` / :func:`remote_context_name`);
* a flat, sh-greppable connection store (:func:`write_context_entry` /
  :func:`read_context_entry`) the generated wrapper reads (grep only, NEVER
  sourced — no code execution from data);
* :class:`RemoteEngine`, a duck-typed subset of
  :class:`~kanibako.container.ContainerRuntime` the attach seed path needs,
  built on ``podman --remote --url unix://<local.sock>`` (it does NOT modify
  ContainerRuntime);
* :func:`ensure_docker_context_meta`, writing the docker-CLI-convention
  ``meta.json`` so the ext can resolve the context itself;
* :func:`ensure_dispatch_wrapper`, the generated POSIX ``sh`` dispatch wrapper
  (wired as ``dev.containers.dockerPath``) — it re-establishes the tunnel on
  demand before dialing the local unix socket.

Routing = CONTEXT-TOKEN DISPATCH (R1, 2026-07-09): the wrapper's default branch
execs local podman verbatim (zero local delta); a stored ``kanibako-remote-*``
context token — detected via argv ``--context`` / ``$DOCKER_CONTEXT`` / a
matching ``$DOCKER_HOST`` — ensures the ssh tunnel and routes to
``podman --remote --url unix://<local.sock>``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
from pathlib import Path

from kanibako.errors import KanibakoError
from kanibako.log import get_logger
from kanibako.paths import xdg

logger = get_logger("vscode_remote")

# Context-name prefix for a kanibako-managed remote docker context.  ONLY names
# with this prefix are ever routed remote by the wrapper or written as docker
# context meta — a user's own contexts pass straight through to local podman.
_CONTEXT_PREFIX = "kanibako-remote-"

# Bump when the generated wrapper body changes so an install refresh rewrites
# the on-disk script (the header records this version).
_SCRIPT_VERSION = 2


# ---------------------------------------------------------------------------
# ssh mux + lifecycle leg
# ---------------------------------------------------------------------------

def _runtime_dir() -> str:
    """Resolve the runtime dir for the ssh ControlPath (design fallback chain).

    Per the ratified design: ``$XDG_RUNTIME_DIR`` (honored iff set AND absolute),
    else ``$TMPDIR`` (iff absolute), else ``/tmp``.  Deliberately NOT the
    :func:`kanibako.paths.resolve_xdg` runtime fallback (which appends
    ``/kanibako`` and warns) — the ControlPath just needs a short, writable,
    per-user dir the mux socket can live under.
    """
    val = os.environ.get("XDG_RUNTIME_DIR", "")
    if val and os.path.isabs(val):
        return val
    val = os.environ.get("TMPDIR", "")
    if val and os.path.isabs(val):
        return val
    return "/tmp"


def _control_path() -> str:
    """The ssh ``ControlPath`` template (``%C`` is expanded by ssh itself)."""
    return f"{_runtime_dir()}/kanibako-remote-%C"


def mux_ssh_options() -> list[str]:
    """The KANIBAKO-OWNED ssh ControlMaster mux options (BINDING).

    Every ssh leg (the lifecycle legs + the unix-socket tunnel master) uses
    these: a kanibako-owned ControlMaster (auto), a kanibako-owned ControlPath
    under the runtime dir, and a 60s ControlPersist.  NEVER the user's
    ControlMaster (session-kill footgun); NEVER mux-free (the ext shells out
    dozens of times per attach).  The tunnel master overrides ControlPersist to
    600 (see :func:`ensure_tunnel`); command legs keep 60.
    """
    return [
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={_control_path()}",
        "-o", "ControlPersist=60",
    ]


def ssh_command(dest: str, remote_argv: list[str]) -> list[str]:
    """Build the local ``ssh`` argv running *remote_argv* on *dest*.

    *dest* is an OPAQUE ssh destination (resolved by the user's ``~/.ssh/config``;
    ``user@`` and ``:port`` pass through verbatim).  ``--`` terminates LOCAL
    ssh option parsing BEFORE *dest*, so a destination beginning with ``-``
    cannot be consumed as an ssh option (option-injection hardening).  Each
    remote arg is :func:`shlex.quote`-d so the remote shell re-parses the
    command exactly as given — ssh concatenates the trailing args with spaces
    before handing them to the remote shell.
    """
    return [
        "ssh", *mux_ssh_options(), "--", dest,
        *[shlex.quote(a) for a in remote_argv],
    ]


# A CONSTANT, user-data-free preamble prepended to the remote kanibako command
# so a per-user pipx/uv install (``~/.local/bin``) is found: a non-interactive
# ssh command does NOT source ``~/.profile``, so ``$HOME/.local/bin`` is often
# absent from PATH and a per-user install is invisible (rc=127 "kanibako:
# command not found").  It rides INSIDE ``sh -c`` (never through
# :func:`shlex.quote`), so ``$HOME``/``$PATH`` stay live and are expanded by the
# remote shell — a naively quoted preamble would make ``$HOME`` a literal.
_REMOTE_PATH_PREAMBLE = 'PATH="$HOME/.local/bin:$PATH" '


def remote_run_kanibako(
    dest: str, args: list[str],
) -> subprocess.CompletedProcess[str]:
    """Run ``kanibako <args...>`` on *dest* over the mux ssh leg (captured).

    The remote command is prefixed with a CONSTANT PATH preamble
    (:data:`_REMOTE_PATH_PREAMBLE`) that puts ``$HOME/.local/bin`` — where
    pipx/uv install kanibako per-user — on PATH, since a non-interactive ssh
    command does not source ``~/.profile``.  The kanibako argv is
    individually :func:`shlex.quote`-d and joined, then the constant preamble is
    prepended; the whole string runs via ``sh -c`` (same channel as
    :func:`probe_remote`) so the remote shell expands the preamble while every
    user-supplied arg stays quoted (a hostile box name cannot break out).
    """
    remote_cmd = _REMOTE_PATH_PREAMBLE + shlex.join(["kanibako", *args])
    return subprocess.run(
        ssh_command(dest, ["sh", "-c", remote_cmd]),
        capture_output=True, text=True,
    )


# One ssh round-trip: print the remote uid and whether the rootless podman user
# socket exists.  Markers are grepped from stdout so unrelated login banners are
# tolerated.
_PROBE_SCRIPT = (
    'uid=$(id -u); '
    'printf "KANIBAKO_UID=%s\\n" "$uid"; '
    'if [ -S "/run/user/$uid/podman/podman.sock" ]; then '
    'echo KANIBAKO_SOCK=ok; else echo KANIBAKO_SOCK=missing; fi'
)

_SOCKET_REMEDIATION = (
    "The rootless podman API socket is not running on the remote host.\n"
    "  On the remote host, enable it for your user:\n"
    "      systemctl --user enable --now podman.socket\n"
    "      loginctl enable-linger \"$USER\"\n"
    "  (linger keeps the user socket up without an active login session.)"
)


def probe_remote(dest: str) -> int:
    """One ssh round-trip: return the remote uid; verify the podman socket.

    Runs ``id -u`` + a ``test -S`` on ``/run/user/<uid>/podman/podman.sock``.
    A missing socket raises :class:`~kanibako.errors.KanibakoError` carrying the
    ``systemctl --user enable --now podman.socket`` + ``loginctl enable-linger``
    remediation; an ssh failure or unreadable uid also raises.
    """
    result = subprocess.run(
        ssh_command(dest, ["sh", "-c", _PROBE_SCRIPT]),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise KanibakoError(
            f"Could not reach '{dest}' over ssh (exit {result.returncode}).\n"
            f"  {stderr}" if stderr else
            f"Could not reach '{dest}' over ssh (exit {result.returncode})."
        )
    uid: str | None = None
    sock: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("KANIBAKO_UID="):
            uid = line[len("KANIBAKO_UID="):].strip()
        elif line.startswith("KANIBAKO_SOCK="):
            sock = line[len("KANIBAKO_SOCK="):].strip()
    if uid is None or not uid.isdigit():
        raise KanibakoError(
            f"Could not read the remote uid from '{dest}' "
            "(unexpected ssh output)."
        )
    if sock != "ok":
        raise KanibakoError(_SOCKET_REMEDIATION)
    return int(uid)


def remote_socket_path(remote_uid: int) -> str:
    """The rootless podman API socket path on the remote host for *remote_uid*."""
    return f"/run/user/{remote_uid}/podman/podman.sock"


def tunnel_socket_path(context_name: str) -> Path:
    """The LOCAL unix-socket path the ssh tunnel forwards to for *context_name*.

    Lives under the runtime dir (same fallback chain as the ssh ControlPath);
    *context_name* is already fs-safe (:func:`remote_context_name`).
    """
    return Path(_runtime_dir()) / f"{context_name}.sock"


def _socket_accepts(path: Path) -> bool:
    """Whether a unix socket at *path* accepts a connection (cheap liveness).

    A plain ``connect`` — NOT a podman round-trip — so a live tunnel master's
    forward answers instantly and a stale socket file (master gone) is treated
    as down so the tunnel is re-established.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def ensure_tunnel(dest: str, remote_uid: int, local_sock: Path) -> None:
    """Ensure a kanibako-owned ssh tunnel forwards *local_sock* to the remote
    podman socket (idempotent).

    Fast path: if *local_sock* already accepts a connection (a live master's
    forward), return immediately.  Otherwise invoke the REAL ssh binary to
    background a ControlPersist master carrying the unix-socket forward::

        ssh -o ControlPersist=600 <mux opts> -o ExitOnForwardFailure=yes
            -o StreamLocalBindUnlink=yes -f -N
            -L <local_sock>:/run/user/<uid>/podman/podman.sock -- <dest>

    ``ControlPersist=600`` (the tunnel lease) is placed BEFORE the base
    :func:`mux_ssh_options` — OpenSSH takes the FIRST obtained value for a
    repeated option (ssh_config semantics, ``ssh -G``-confirmed), so the 600
    must precede the base options' 60 to win.  ``StreamLocalBindUnlink``
    clears any stale socket file; the tunnel made by REAL ssh reads the user's
    ``~/.ssh/config`` (alias/port/ProxyJump/key-files all work, no ssh-agent
    requirement).  A nonzero rc raises
    :class:`~kanibako.errors.KanibakoError` carrying ssh's stderr verbatim.
    """
    if local_sock.exists() and _socket_accepts(local_sock):
        return
    forward = f"{local_sock}:{remote_socket_path(remote_uid)}"
    argv = [
        "ssh",
        "-o", "ControlPersist=600",
        *mux_ssh_options(),
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StreamLocalBindUnlink=yes",
        "-f", "-N",
        "-L", forward,
        "--", dest,
    ]
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise KanibakoError(
            f"Could not establish the ssh tunnel to the remote podman socket "
            f"on '{dest}' (exit {result.returncode})."
            + (f"\n  {stderr}" if stderr else "")
        )


def engine_url(local_sock: Path) -> str:
    """The podman remote engine URL for a LOCAL forwarded unix socket.

    Always ``unix://<local_sock>`` — podman dials the local end of the ssh
    tunnel (:func:`ensure_tunnel`), never a golang ``ssh:`` connection.
    """
    return f"unix://{local_sock}"


def context_slug(dest: str) -> str:
    """A filesystem-safe slug for *dest* (used to name the docker context).

    The readable part is lossy (``me@host`` and ``me/host`` both normalise to
    ``me-host``), so a short digest of the VERBATIM dest is appended to keep
    distinct destinations from sharing a context name / store entry.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", dest).strip("-").lower()
    digest = hashlib.sha256(dest.encode()).hexdigest()[:6]
    return f"{slug or 'default'}-{digest}"


def remote_context_name(dest: str) -> str:
    """The kanibako-managed docker context NAME for *dest*."""
    return f"{_CONTEXT_PREFIX}{context_slug(dest)}"


# ---------------------------------------------------------------------------
# Connection store (flat, sh-greppable; NEVER sourced)
# ---------------------------------------------------------------------------

def _vscode_remote_state_dir() -> Path:
    return xdg("XDG_STATE_HOME", ".local/state") / "kanibako" / "vscode-remote"


def contexts_dir() -> Path:
    """Directory holding one flat ``KEY=VALUE`` file per stored context."""
    return _vscode_remote_state_dir() / "contexts"


def write_context_entry(
    name: str, *, url: str, dest: str, uid: int, sock: str, remote_sock: str,
) -> Path:
    """Write the flat ``KEY=VALUE`` store file for context *name*.

    One line each: ``URL=`` (``unix://<local_sock>``), ``DEST=``, ``UID=``,
    ``SOCK=`` (the local forwarded socket) and ``REMOTE_SOCK=`` (the remote
    podman socket) — sh-greppable (the wrapper extracts values with ``sed`` to
    re-establish the tunnel + dial the local socket), NEVER sourced.  Returns
    the file path.
    """
    d = contexts_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(
        f"URL={url}\nDEST={dest}\nUID={uid}\n"
        f"SOCK={sock}\nREMOTE_SOCK={remote_sock}\n"
    )
    return path


def read_context_entry(name: str) -> dict[str, str]:
    """Read the flat store file for context *name* (``{}`` if absent/unreadable)."""
    path = contexts_dir() / name
    result: dict[str, str] = {}
    try:
        raw = path.read_text()
    except OSError:
        return result
    for line in raw.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# RemoteEngine — duck-typed ContainerRuntime subset for the attach seed path
# ---------------------------------------------------------------------------

class RemoteEngine:
    """The subset of :class:`~kanibako.container.ContainerRuntime` the attach
    seed path needs, driven by ``podman --remote --url unix://<local.sock>``.

    Built on the podman remote argv prefix dialing the LOCAL end of the ssh
    tunnel (:func:`ensure_tunnel`) — podman never execs ssh here (the golang
    ``ssh:`` client is bypassed entirely).  Does NOT touch
    :class:`ContainerRuntime`.
    """

    def __init__(self, url: str, *, podman: str | None = None) -> None:
        self.url = url
        self.podman = podman or shutil.which("podman") or "podman"
        self.argv_prefix = [self.podman, "--remote", "--url", url]

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self.argv_prefix, *args],
            capture_output=True, text=True,
        )

    def version(self) -> subprocess.CompletedProcess[str]:
        """Pre-flight the engine leg: ``podman --remote … version``.

        Runs against the stored ``unix://`` URL, so a dead tunnel (local socket
        not answering) is caught before the lifecycle legs run.  Returns the raw
        CompletedProcess so the caller can surface podman's own stderr (see
        :func:`preflight_engine`).
        """
        return self._run(["version"])

    def running_with_stderr(self, name: str) -> tuple[bool, str]:
        """Whether remote container *name* is running, plus podman's stderr.

        The stderr is the honest surface for the "did not come up" path — an
        ``inspect`` that fails (no such container, engine unreachable) names the
        underlying problem, which a bare bool would swallow.
        """
        result = self._run(
            ["inspect", "--format", "{{.State.Running}}", name],
        )
        running = result.returncode == 0 and result.stdout.strip() == "true"
        return running, (result.stderr or "").strip()

    def is_running(self, name: str) -> bool:
        """Whether the remote container *name* is currently running."""
        running, _ = self.running_with_stderr(name)
        return running

    def inspect_env(self, name: str, key: str) -> str | None:
        """The value of env var *key* on remote container *name*, or None."""
        result = self._run(
            ["inspect", "--format", "{{json .Config.Env}}", name],
        )
        if result.returncode != 0:
            return None
        try:
            env_list = json.loads(result.stdout.strip() or "null")
        except (ValueError, TypeError):
            return None
        if not isinstance(env_list, list):
            return None
        prefix = f"{key}="
        for item in env_list:
            if isinstance(item, str) and item.startswith(prefix):
                return item[len(prefix):]
        return None

    def container_image(self, name: str) -> str | None:
        """The image reference remote container *name* was created from, or None."""
        result = self._run(["inspect", "--format", "{{.ImageName}}", name])
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None


_PREFLIGHT_TUNNEL_HINT = (
    "Hint: the local end of the ssh tunnel to the remote podman socket is not "
    "answering. Check that the ssh tunnel came up (kanibako owns a "
    "ControlPersist master; see the dispatch log) and that the remote "
    "podman.socket is running."
)


def preflight_engine(engine: RemoteEngine) -> None:
    """Pre-flight the remote podman engine leg; raise on a dead connection.

    Runs ``podman --remote --url unix://<local.sock> version`` (via
    :meth:`RemoteEngine.version`) BEFORE the lifecycle legs, so a dead tunnel
    (the local forwarded socket not answering) is caught with podman's OWN
    stderr plus a generic tunnel remediation rather than a downstream
    "did not come up".  Raises :class:`~kanibako.errors.KanibakoError` carrying
    podman's stderr verbatim; returns None on success.
    """
    result = engine.version()
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip()
    body = stderr or "(podman produced no error output)"
    raise KanibakoError(
        f"Could not reach the remote podman engine over the ssh tunnel.\n"
        f"  {body}\n  {_PREFLIGHT_TUNNEL_HINT}"
    )


# ---------------------------------------------------------------------------
# Docker context meta.json (so the ext can resolve/validate the context)
# ---------------------------------------------------------------------------

def _docker_config_dir() -> Path:
    val = os.environ.get("DOCKER_CONFIG")
    if val and os.path.isabs(val):
        return Path(val)
    return Path.home() / ".docker"


def ensure_docker_context_meta(name: str, url: str) -> Path:
    """Write the docker-CLI-convention ``meta.json`` for context *name*.

    Docker keys a context by ``sha256(name)``: the file lives at
    ``<docker config>/contexts/meta/<sha256(name)>/meta.json`` (the docker CLI
    itself is NOT required to write it).  Idempotent (rewritten only on change);
    refuses any non-``kanibako-remote-*`` name so it never touches a user's own
    contexts.  Returns the meta.json path.
    """
    if not name.startswith(_CONTEXT_PREFIX):
        raise ValueError(
            f"refusing to write docker context meta for non-kanibako "
            f"context {name!r}"
        )
    digest = hashlib.sha256(name.encode()).hexdigest()
    meta_dir = _docker_config_dir() / "contexts" / "meta" / digest
    meta_file = meta_dir / "meta.json"
    payload = {
        "Name": name,
        "Metadata": {},
        "Endpoints": {"docker": {"Host": url, "SkipTLSVerify": False}},
    }
    content = json.dumps(payload, separators=(",", ":"))
    try:
        if meta_file.read_text() == content:
            return meta_file
    except OSError:
        pass
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(content)
    return meta_file


# ---------------------------------------------------------------------------
# Generated dispatch wrapper + ssh mux shim
# ---------------------------------------------------------------------------

def vscode_remote_bin_dir() -> Path:
    """Directory holding the generated ``podman-dispatch`` wrapper + ``ssh`` shim."""
    return (
        xdg("XDG_DATA_HOME", ".local/share")
        / "kanibako" / "vscode-remote" / "bin"
    )


def dispatch_wrapper_path() -> Path:
    """Path VS Code's ``dev.containers.dockerPath`` points at."""
    return vscode_remote_bin_dir() / "podman-dispatch"


def dispatch_log_path() -> Path:
    """Path of the wrapper's one-line-per-invocation audit log."""
    return _vscode_remote_state_dir() / "dispatch.log"


# The wrapper is built from a template with @@TOKEN@@ placeholders substituted
# via str.replace (NOT f-strings / %-format) so the sh body's own
# ``$``/``%``/``{}`` are left untouched.

_WRAPPER_TEMPLATE = """#!/bin/sh
# kanibako vscode-remote dispatch wrapper (generated) v@@VERSION@@
# DO NOT EDIT -- regenerated by `kanibako code --remote`.
# Routes VS Code Dev Containers' podman calls: a stored kanibako-remote context
# token (argv --context / $DOCKER_CONTEXT / a matching $DOCKER_HOST) ensures a
# kanibako-owned ssh tunnel to the remote podman socket, then execs
# `podman --remote --url unix://<local.sock>`; everything else execs local
# podman verbatim (zero local delta). Store files are grepped, NEVER sourced.
set -u

STORE_DIR='@@STORE_DIR@@'
LOG_FILE='@@LOG_FILE@@'
CONTROL_PATH='@@CONTROL_PATH@@'
SENTINEL='__kanibako_dispatch_end__'

# --- strip --context / --context=VAL from argv, capturing the value ---
ctx_argv=''
set -- "$@" "$SENTINEL"
while [ "${1:-}" != "$SENTINEL" ]; do
    arg=$1
    shift
    case "$arg" in
        --context)
            if [ "${1:-}" != "$SENTINEL" ]; then
                ctx_argv=$1
                shift
            fi
            ;;
        --context=*)
            ctx_argv=${arg#--context=}
            ;;
        *)
            set -- "$@" "$arg"
            ;;
    esac
done
shift  # drop the sentinel

# --- detect the routing context + which channel fired ---
context=''
channel='none'
if [ -n "$ctx_argv" ]; then
    context=$ctx_argv
    channel='argv'
elif [ -n "${DOCKER_CONTEXT:-}" ]; then
    context=$DOCKER_CONTEXT
    channel='env:DOCKER_CONTEXT'
elif [ -n "${DOCKER_HOST:-}" ]; then
    for _f in "$STORE_DIR"/kanibako-remote-*; do
        [ -f "$_f" ] || continue
        _u=$(sed -n 's/^URL=//p' "$_f" 2>/dev/null)
        if [ "$_u" = "$DOCKER_HOST" ]; then
            context=$(basename "$_f")
            channel='env:DOCKER_HOST'
            break
        fi
    done
fi

# --- resolve route (remote ONLY for a stored kanibako-remote context) ---
route='local'
url=''
if [ -n "$context" ]; then
    case "$context" in
        kanibako-remote-*)
            if [ -f "$STORE_DIR/$context" ]; then
                url=$(sed -n 's/^URL=//p' "$STORE_DIR/$context" 2>/dev/null)
                [ -n "$url" ] && route='remote'
            fi
            ;;
    esac
fi

# --- append one audit line (self-truncating at ~1MB) ---
_log_dir=${LOG_FILE%/*}
mkdir -p "$_log_dir" 2>/dev/null || true
if [ -f "$LOG_FILE" ]; then
    _sz=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$_sz" -gt 1048576 ] 2>/dev/null; then
        tail -n 200 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null \\
            && mv "$LOG_FILE.tmp" "$LOG_FILE" 2>/dev/null || true
    fi
fi
_ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo '?')
printf '%s channel=%s context=%s route=%s argv=%s %s %s\\n' \\
    "$_ts" "$channel" "${context:-none}" "$route" \\
    "${1:-}" "${2:-}" "${3:-}" >> "$LOG_FILE" 2>/dev/null || true

# --- exec ---
if [ "$route" = 'remote' ]; then
    # Re-establish the kanibako-owned ssh tunnel to the remote podman socket if
    # the local forwarded socket is gone (the sh -S check is weaker than the
    # sugar's connect-probe, but the ControlPersist master keeps it warm; a
    # stale socket file is unlinked by StreamLocalBindUnlink on re-establish).
    _sock=$(sed -n 's/^SOCK=//p' "$STORE_DIR/$context" 2>/dev/null)
    _rsock=$(sed -n 's/^REMOTE_SOCK=//p' "$STORE_DIR/$context" 2>/dev/null)
    _dest=$(sed -n 's/^DEST=//p' "$STORE_DIR/$context" 2>/dev/null)
    if [ -n "$_sock" ] && [ ! -S "$_sock" ]; then
        _ssh=$(command -v ssh 2>/dev/null)
        if [ -n "$_ssh" ] && [ -n "$_rsock" ] && [ -n "$_dest" ]; then
            # BatchMode: this runs HEADLESS under the editor's extension host --
            # never let ssh attempt interactive auth (fail fast instead; the
            # sugar's interactive establish is the strong path).  Output is
            # kept off stdout so the ext never parses ssh noise as podman's.
            "$_ssh" -o BatchMode=yes \\
                -o ControlPersist=600 \\
                -o ControlMaster=auto -o "ControlPath=$CONTROL_PATH" \\
                -o ExitOnForwardFailure=yes \\
                -o StreamLocalBindUnlink=yes -f -N \\
                -L "$_sock:$_rsock" -- "$_dest" \\
                >/dev/null 2>>"$LOG_FILE" \\
                || printf '%s tunnel-establish-failed context=%s\\n' \\
                    "$_ts" "$context" >> "$LOG_FILE" 2>/dev/null || true
        fi
    fi
    _podman=$(command -v podman 2>/dev/null) || {
        echo 'kanibako dispatch: podman not found on PATH' >&2
        exit 127
    }
    exec "$_podman" --remote --url "unix://$_sock" "$@"
fi
_podman=$(command -v podman 2>/dev/null) || {
    echo 'kanibako dispatch: podman not found on PATH' >&2
    exit 127
}
exec "$_podman" "$@"
"""


def _wrapper_content() -> str:
    return (
        _WRAPPER_TEMPLATE
        .replace("@@VERSION@@", str(_SCRIPT_VERSION))
        .replace("@@STORE_DIR@@", str(contexts_dir()))
        .replace("@@LOG_FILE@@", str(dispatch_log_path()))
        .replace("@@CONTROL_PATH@@", _control_path())
    )


def _write_script(path: Path, content: str) -> bool:
    """Write *content* to *path* as a 0755 script iff it changed (idempotent).

    Returns True iff the file content was (re)written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text() == content:
            path.chmod(0o755)
            return False
    except OSError:
        pass
    path.write_text(content)
    path.chmod(0o755)
    return True


def ensure_dispatch_wrapper() -> Path:
    """Install/refresh the ``podman-dispatch`` wrapper (idempotent). Returns path."""
    path = dispatch_wrapper_path()
    _write_script(path, _wrapper_content())
    return path

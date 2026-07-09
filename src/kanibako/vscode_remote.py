"""FF-1 remote-VS-Code plumbing for ``kanibako code --remote`` (A' topology).

LOCAL VS Code → REMOTE rootless podman socket over SSH.  The kanibako CLI stays
HOST-side (box lifecycle over plain SSH); kanibako-CLI-over-remote-podman is a
NON-GOAL.  This module owns the host-side pieces that make a LOCAL VS Code Dev
Containers "attach to running container" reach a REMOTE box:

* the KANIBAKO-OWNED ssh ControlMaster mux options (:func:`mux_ssh_options`) and
  the lifecycle ssh leg (:func:`ssh_command` / :func:`remote_run_kanibako` /
  :func:`probe_remote`);
* the remote podman engine URL + docker-context naming (:func:`engine_url` /
  :func:`context_slug` / :func:`remote_context_name`);
* a flat, sh-greppable connection store (:func:`write_context_entry` /
  :func:`read_context_entry`) the generated wrapper reads (grep only, NEVER
  sourced — no code execution from data);
* :class:`RemoteEngine`, a duck-typed subset of
  :class:`~kanibako.container.ContainerRuntime` the attach seed path needs,
  built on ``podman --remote --ssh native --url <url>`` with a mux-shim-prefixed
  PATH (it does NOT modify ContainerRuntime);
* :func:`ensure_docker_context_meta`, writing the docker-CLI-convention
  ``meta.json`` so the ext can resolve the context itself;
* :func:`ensure_dispatch_wrapper` / :func:`ensure_ssh_shim`, the generated POSIX
  ``sh`` dispatch wrapper (wired as ``dev.containers.dockerPath``) + ssh mux
  shim.

Routing = CONTEXT-TOKEN DISPATCH (R1, 2026-07-09): the wrapper's default branch
execs local podman verbatim (zero local delta); a stored ``kanibako-remote-*``
context token — detected via argv ``--context`` / ``$DOCKER_CONTEXT`` / a
matching ``$DOCKER_HOST`` — routes to ``podman --remote`` against the stored URL.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
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

# Bump when the generated wrapper/shim body changes so an install refresh
# rewrites the on-disk scripts (the header records this version).
_SCRIPT_VERSION = 1


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

    Every ssh leg (lifecycle + the podman ``--ssh native`` shim) uses these:
    a kanibako-owned ControlMaster (auto), a kanibako-owned ControlPath under
    the runtime dir, and a 60s ControlPersist.  NEVER the user's ControlMaster
    (session-kill footgun); NEVER mux-free (the ext shells out dozens of times
    per attach).
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


def remote_run_kanibako(
    dest: str, args: list[str],
) -> subprocess.CompletedProcess[str]:
    """Run ``kanibako <args...>`` on *dest* over the mux ssh leg (captured)."""
    return subprocess.run(
        ssh_command(dest, ["kanibako", *args]),
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


def engine_url(dest: str, uid: int) -> str:
    """The podman remote engine URL for *dest*'s rootless socket.

    *dest* is embedded VERBATIM (opaque; ``user@`` and ``:port`` pass through).
    """
    return f"ssh://{dest}/run/user/{uid}/podman/podman.sock"


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
    name: str, *, url: str, dest: str, uid: int,
) -> Path:
    """Write the flat ``KEY=VALUE`` store file for context *name*.

    One line each: ``URL=``, ``DEST=``, ``UID=`` — sh-greppable (the wrapper
    extracts ``URL`` with ``sed``), NEVER sourced.  Returns the file path.
    """
    d = contexts_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(f"URL={url}\nDEST={dest}\nUID={uid}\n")
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
    seed path needs, driven by ``podman --remote --ssh native --url <url>``.

    Built on the podman remote argv prefix with a PATH prefixed by the ssh mux
    shim dir (so podman's ``--ssh native`` shells out to the KANIBAKO-OWNED mux
    ssh, not the user's plain ssh).  Does NOT touch :class:`ContainerRuntime`.
    """

    def __init__(
        self, url: str, *, podman: str | None = None, shim_dir: Path | None = None,
    ) -> None:
        self.url = url
        self.podman = podman or shutil.which("podman") or "podman"
        self.shim_dir = str(shim_dir) if shim_dir is not None else str(
            vscode_remote_bin_dir()
        )
        self.argv_prefix = [
            self.podman, "--remote", "--ssh", "native", "--url", url,
        ]
        env = dict(os.environ)
        env["PATH"] = f"{self.shim_dir}{os.pathsep}{env.get('PATH', '')}"
        self._env = env

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self.argv_prefix, *args],
            capture_output=True, text=True, env=self._env,
        )

    def is_running(self, name: str) -> bool:
        """Whether the remote container *name* is currently running."""
        result = self._run(
            ["inspect", "--format", "{{.State.Running}}", name],
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

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


def ssh_shim_path() -> Path:
    """Path to the generated ssh mux shim (first on PATH for the remote leg)."""
    return vscode_remote_bin_dir() / "ssh"


def dispatch_log_path() -> Path:
    """Path of the wrapper's one-line-per-invocation audit log."""
    return _vscode_remote_state_dir() / "dispatch.log"


# The wrapper + shim are built from templates with @@TOKEN@@ placeholders
# substituted via str.replace (NOT f-strings / %-format) so the sh body's own
# ``$``/``%``/``{}`` are left untouched.

_WRAPPER_TEMPLATE = """#!/bin/sh
# kanibako vscode-remote dispatch wrapper (generated) v@@VERSION@@
# DO NOT EDIT -- regenerated by `kanibako code --remote`.
# Routes VS Code Dev Containers' podman calls: a stored kanibako-remote context
# token (argv --context / $DOCKER_CONTEXT / a matching $DOCKER_HOST) execs
# `podman --remote` at the stored engine URL; everything else execs local podman
# verbatim (zero local delta). Store files are grepped, NEVER sourced.
set -u

STORE_DIR='@@STORE_DIR@@'
LOG_FILE='@@LOG_FILE@@'
SHIM_DIR='@@SHIM_DIR@@'
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
    PATH="$SHIM_DIR:$PATH"
    export PATH
    _podman=$(command -v podman 2>/dev/null) || {
        echo 'kanibako dispatch: podman not found on PATH' >&2
        exit 127
    }
    exec "$_podman" --remote --ssh native --url "$url" "$@"
fi
_podman=$(command -v podman 2>/dev/null) || {
    echo 'kanibako dispatch: podman not found on PATH' >&2
    exit 127
}
exec "$_podman" "$@"
"""


_SHIM_TEMPLATE = """#!/bin/sh
# kanibako vscode-remote ssh mux shim (generated) v@@VERSION@@
# DO NOT EDIT -- regenerated by `kanibako code --remote`.
# podman `--ssh native` shells out to `ssh`; this shim (first on PATH for the
# remote leg) injects the KANIBAKO-OWNED ControlMaster mux, then execs real ssh.
set -u
SHIM_DIR='@@SHIM_DIR@@'
CONTROL_PATH='@@CONTROL_PATH@@'

# Drop our own dir from PATH so we resolve the REAL ssh (not this shim).
_np=''
_oifs=$IFS
IFS=':'
for _d in $PATH; do
    [ "$_d" = "$SHIM_DIR" ] && continue
    if [ -z "$_np" ]; then _np=$_d; else _np="$_np:$_d"; fi
done
IFS=$_oifs
PATH=$_np
export PATH

_ssh=$(command -v ssh 2>/dev/null) || {
    echo 'kanibako ssh shim: ssh not found on PATH' >&2
    exit 127
}
exec "$_ssh" -o ControlMaster=auto -o "ControlPath=$CONTROL_PATH" \\
    -o ControlPersist=60 "$@"
"""


def _wrapper_content() -> str:
    return (
        _WRAPPER_TEMPLATE
        .replace("@@VERSION@@", str(_SCRIPT_VERSION))
        .replace("@@STORE_DIR@@", str(contexts_dir()))
        .replace("@@LOG_FILE@@", str(dispatch_log_path()))
        .replace("@@SHIM_DIR@@", str(vscode_remote_bin_dir()))
    )


def _shim_content() -> str:
    return (
        _SHIM_TEMPLATE
        .replace("@@VERSION@@", str(_SCRIPT_VERSION))
        .replace("@@SHIM_DIR@@", str(vscode_remote_bin_dir()))
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


def ensure_ssh_shim() -> Path:
    """Install/refresh the ssh mux shim (idempotent). Returns path."""
    path = ssh_shim_path()
    _write_script(path, _shim_content())
    return path
